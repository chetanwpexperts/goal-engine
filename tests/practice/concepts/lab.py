"""The lab for the concepts pack: every example query in app/dbknow/core/concepts.json is run on MariaDB/MySQL and on
PostgreSQL against the practice schema (tests/practice/schema.py). The result kept with the example is what both engines
returned; an example whose engines disagree (when it is meant to be the same) is a failure.

Read-only examples run on the temporary practice tables. Examples that change data run, then the tables are rebuilt.
Examples that define things (CREATE TABLE / INDEX / VIEW) run on real scratch tables named lab_<table>, dropped afterwards
(MySQL can't build a view on a temporary table).

Run: python -m tests.practice.concepts.lab          (check)
     python -m tests.practice.concepts.lab --write  (store the results in the pack)
"""

import json
import re
import sys

from app.dbknow import concepts as K
from tests.practice import schema as S

DDL = re.compile(r"^\s*create\b", re.I)
ROUTINE = re.compile(r"\bcreate\s+(?:or\s+replace\s+)?(procedure|function|trigger)\s+(\w+)", re.I)


def _family(kind: str) -> str:
    return "mysql" if kind == "mysql" else "postgresql"


def _sql(ex: dict, kind: str, key: str = "sql") -> str | None:
    d = ex.get(key) or {}
    return d.get("all") or d.get(_family(kind))


def _exec(eng, sql: str):
    """(columns, rows) for a query, or (None, rowcount) for a change; several statements are run in order, the way the
    database's client runs a script (DELIMITER and $$ bodies understood). The last rows read are the result (a COMMIT after
    the SELECT doesn't hide them)."""
    from app.agents.sqlroutine import split_script

    out = (None, 0)
    for stmt in split_script(sql):
        cur = eng.conn.cursor()
        try:
            cur.execute(stmt)
            if cur.description:
                out = ([d[0] for d in cur.description], [[S.norm(v) for v in r] for r in cur.fetchall()])
            elif out[0] is None:
                out = (None, cur.rowcount)
        finally:
            cur.close()
    return out


def _ddl(eng, sql: str, fill: bool = False):
    """Run a CREATE example on real lab_ tables (with the practice rows when fill: a procedure or trigger works on them);
    drop everything afterwards, routines included. The result is the rows the example reads last, else 'ok'."""
    names = list(S.TABLES) + ["demo_parent"]
    made, routines = [], [(k.lower(), n) for k, n in ROUTINE.findall(sql)]
    try:
        for n in S.ORDER:
            cols, rows = S.TABLES[n]
            eng.run(f"DROP TABLE IF EXISTS lab_{n}" + (" CASCADE" if eng.kind == "postgres" else ""))
            eng.run(f"CREATE TABLE lab_{n} ({', '.join(cols)}, PRIMARY KEY (id))")
            made.append(f"lab_{n}")
            if fill:
                eng.run(f"INSERT INTO lab_{n} VALUES ({', '.join(['%s'] * len(cols))})", many=rows)
        eng.run("DROP TABLE IF EXISTS lab_demo_child")
        eng.run("DROP TABLE IF EXISTS lab_demo_pk")
        eng.run("DROP TABLE IF EXISTS lab_demo_parent")
        eng.run("CREATE TABLE lab_demo_parent (id INT PRIMARY KEY)")
        made.append("lab_demo_parent")
        text = sql
        for n in names + ["demo_child", "demo_pk", "paid_orders"]:
            text = re.sub(rf"\b{n}\b", f"lab_{n}", text)
        text = re.sub(r"\bix_orders_customer\b", "ix_lab_orders_customer", text)
        for obj in re.findall(r"\bcreate\s+(?:table|view)\s+(\w+)", text, re.I):
            made.append(obj)
        res = _exec(eng, text)
        return res if res[0] else (None, "ok")
    finally:
        for kind, name in routines:
            if kind == "trigger" and eng.kind == "postgres":
                continue                                   # goes with its table
            try:
                eng.run(f"DROP {kind.upper()} IF EXISTS {name}" + (" CASCADE" if eng.kind == "postgres" else ""))
            except Exception:  # noqa: BLE001
                eng.rollback()
        for obj in reversed(made):
            for what in ("VIEW", "TABLE"):
                try:
                    eng.run(f"DROP {what} IF EXISTS {obj}" + (" CASCADE" if eng.kind == "postgres" else ""))
                except Exception:  # noqa: BLE001
                    eng.rollback()


def run_example(eng, ex: dict) -> dict:
    out = {}
    sql = _sql(ex, eng.kind)
    if sql is None:
        return {"skipped": True}
    if ROUTINE.search(sql):                                 # a procedure / function / trigger: on filled real lab_ tables
        out["result"] = _ddl(eng, sql, fill=True)
        return out
    if DDL.match(sql):
        out["result"] = _ddl(eng, sql)
        return out
    S.build(eng)
    pv = _sql(ex, eng.kind, "preview")
    if pv:
        out["preview"] = _exec(eng, pv)
    out["result"] = _exec(eng, sql)
    S.build(eng)
    return out


def check(write: bool = False) -> list[str]:
    engines = S.engines()
    if len(engines) < 2:
        return ["needs both MariaDB/MySQL and PostgreSQL running"]
    data = json.loads(K.PACK.read_text(encoding="utf-8"))
    bad = []
    for c in data["concepts"]:
        for k, ex in enumerate(c["examples"]):
            got = {}
            for kind, eng in engines.items():
                try:
                    got[kind] = run_example(eng, ex)
                except Exception as exc:  # noqa: BLE001
                    eng.rollback()
                    S.build(eng)
                    got[kind] = {"error": str(exc).splitlines()[0][:160]}
            errs = [f"{kind}: {g['error']}" for kind, g in got.items() if "error" in g]
            if errs:
                bad.append(f"{c['id']} example {k}: fails: {errs}")
                continue
            m, p = got["mysql"], got["postgres"]
            if m["result"][1] != p["result"][1] or m.get("preview", (0, 0))[1] != p.get("preview", (0, 0))[1]:
                bad.append(f"{c['id']} example {k}: MariaDB and PostgreSQL disagree:\n  {m}\n  {p}")
                continue
            cols, res = m["result"]
            want = {"columns": cols, "result": res} if cols else {"changed": res}
            if "preview" in m:
                want["preview_columns"], want["preview_result"] = m["preview"]
            stored = {key: ex.get(key) for key in want}
            if stored != want:
                if write:
                    ex.update(want)
                else:
                    bad.append(f"{c['id']} example {k}: stored result differs from the lab: {stored} vs {want}")
    if write:
        K.PACK.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        K.pack.cache_clear()
    return bad


if __name__ == "__main__":
    problems = check("--write" in sys.argv)
    for p in problems:
        print(p)
    print(f"{len(problems)} problems")
    sys.exit(1 if problems else 0)
