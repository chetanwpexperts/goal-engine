"""S3's generator: a query's result to Excel, proved on the real engines.

For random schemas (tests/fuzz/db.py) and a planted table full of traps (capitals twins, wildcards, digits, '=' at the
start, codes with leading zeros, 16+ digit numbers, NULL and empty text, trailing spaces), each case:

1. runs a random SELECT on MariaDB, PostgreSQL and SQLite and exports it through the connector's own export code
   (connector/goal_connector.py: export_file, the gzip CSV a real export makes);
2. builds the workbook (app/agents/sqlexcel.py) from that CSV, grouped by a column the generator picks or leaves to DBaba;
3. has LibreOffice open it and work out every formula (nothing is cached in the file: the numbers come from the live
   COUNTIFS / SUMIFS / SUMPRODUCT formulas over the Data sheet);
4. compares the recalculated Pivot sheet with the builder's own numbers AND with the engine's own
   SELECT g, COUNT(*), SUM(m), AVG(m) ... GROUP BY g, grouped the way that engine groups text. Groups the engine merges
   but Excel can't (MariaDB: trailing spaces, accents) must be named in the workbook's notes.
5. checks the Data sheet: every cell equals the database's value (long numbers and leading-zero codes kept digit for
   digit, '=...' text not turned into a formula).

    python -m tests.fuzz.dbexcel 40 7        # 40 cases, seed 7: prints "problems: 0" when all hold
"""

import csv
import datetime as dt
import glob
import gzip
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from tests.fuzz import db as fz

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("goal_connector", ROOT / "connector" / "goal_connector.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)

TRAP_LABELS = ["Red", "red", "RED", "Green", "a*", "a?b", "axb", "10", "010", ">5", "=x", "", None, "Jan 2026", "TRUE",
               "Blue", "Blue ", "Café", "Cafe", "x~y"]


def soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def recalc(paths: list[str], outdir: str) -> dict:
    """LibreOffice opens each workbook, works out every formula, and writes each sheet as CSV."""
    exe = soffice()
    if not exe or not paths:
        return {}
    prof = tempfile.mkdtemp(prefix="lo_prof_")
    for k in range(0, len(paths), 40):            # in batches: one LibreOffice start for 40 workbooks, a time limit per batch
        subprocess.run([exe, f"-env:UserInstallation=file://{prof}", "--headless", "--convert-to",
                        "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,false,false,false,-1", "--outdir", outdir,
                        *paths[k:k + 40]], capture_output=True, timeout=600)
    shutil.rmtree(prof, ignore_errors=True)
    out = {}
    for p in paths:
        stem = Path(p).stem
        sheets = {}
        for f in glob.glob(os.path.join(outdir, f"{stem}-*.csv")):
            name = Path(f).stem[len(stem) + 1:]
            with open(f, encoding="utf-8", newline="") as fh:
                sheets[name] = list(csv.reader(fh))
        out[p] = sheets
    return out


def _num(s):
    if s in ("", None):
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return s


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(a)), abs(float(b)))


# ---------------------------------------------------------------- engines and their exports
class Target:
    def __init__(self, kind):
        self.kind = kind
        self.tmp = None
        if kind == "sqlite":
            self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name
            import sqlite3

            self.raw = sqlite3.connect(self.tmp, isolation_level=None)
            self.cfg = {"type": "sqlite", "host": "", "port": "", "user": "", "password": "", "database": self.tmp}
        else:
            base = fz.MYSQL if kind == "mysql" else fz.PG
            self.eng = fz.Engine(base).open()
            self.cfg = {**base, "port": base["port"]}
        self.engine_id = {"mysql": "mariadb", "postgres": "postgresql", "sqlite": "sqlite"}[kind]

    def run(self, sql, params=None, many=None):
        if self.kind == "sqlite":
            cur = self.raw.cursor()
            if many is not None:
                cur.executemany(sql.replace("%s", "?"), [tuple(fz._sqlite_value(v) for v in r) for r in many])
                return None
            cur.execute(sql, params or ())
            return cur.fetchall() if cur.description else None
        return self.eng.run(sql, params, many)

    def create(self, schema):
        if self.kind == "sqlite":
            for n in reversed(schema["order"]):
                self.run(f"DROP TABLE IF EXISTS {n}")
            for n in schema["order"]:
                t = schema["tables"][n]
                for stmt in fz._ddl(t, "postgres"):
                    self.run(stmt)
                self.run(f"INSERT INTO {n} VALUES ({', '.join(['%s'] * len(t['cols']))})", many=[tuple(r) for r in t["rows"]])
        else:
            self.eng.create(schema)

    def drop(self, names):
        if self.kind == "sqlite":
            for n in reversed(names):
                self.run(f"DROP TABLE IF EXISTS {n}")
        else:
            self.eng.drop(names)

    def export(self, sql, path):
        conn = gc.Database(self.cfg).connect()
        try:
            return conn.export_file(sql, path)
        finally:
            try:
                conn.conn.close()
            except Exception:  # noqa: BLE001
                pass


def targets() -> list[Target]:
    out = []
    for k in ("mysql", "postgres", "sqlite"):
        try:
            out.append(Target(k))
        except Exception:  # noqa: BLE001 - server down: skipped
            pass
    return out


def trap_table(seed, rnd):
    """A table built to catch every way a pivot can lie."""
    name = f"fx{seed}_traps"
    rows = []
    n = rnd.randint(30, 80)
    for k in range(n):
        lab = rnd.choice(TRAP_LABELS)
        amt = None if rnd.random() < 0.12 else Decimal(rnd.randint(-5000, 900000)) / 100
        code = f"{rnd.randint(0, 999):05d}"
        big = rnd.choice([None, 10 ** 17 + rnd.randint(0, 10 ** 6), rnd.randint(1, 99)])
        when = dt.datetime(2025, rnd.randint(1, 12), rnd.randint(1, 28), rnd.randint(0, 23), rnd.randint(0, 59))
        note = rnd.choice(["=SUM(A1)", "+91 98", "-x", "@me", "ok", None])
        rows.append([k + 1, lab, amt, code, big, when, note])
    if not any(r[2] is None for r in rows):
        rows[0][2] = None
    cols = [{"name": "id", "kind": "pk", "params": None}, {"name": "label", "kind": "cat", "params": None},
            {"name": "amount", "kind": "money", "params": None}, {"name": "code", "kind": "cat", "params": None},
            {"name": "big_no", "kind": "big", "params": None}, {"name": "made_at", "kind": "datetime", "params": None},
            {"name": "note", "kind": "cat", "params": None}]
    return {"name": name, "base": "traps", "pk": "id", "cols": cols, "fks": [], "indexes": [], "rows": rows}


def _ddl_traps(t, kind):
    ty = {"mysql": {"pk": "INT NOT NULL", "money": "DECIMAL(12,2)", "big": "BIGINT", "datetime": "DATETIME", "cat": "VARCHAR(40)"},
          "postgres": {"pk": "INTEGER NOT NULL", "money": "NUMERIC(12,2)", "big": "BIGINT", "datetime": "TIMESTAMP", "cat": "VARCHAR(40)"},
          "sqlite": {"pk": "INTEGER NOT NULL", "money": "NUMERIC(12,2)", "big": "INTEGER", "datetime": "TIMESTAMP", "cat": "VARCHAR(40)"}}[kind]
    cols = ", ".join(f"{c['name']} {ty[c['kind']]}" for c in t["cols"])
    return f"CREATE TABLE {t['name']} ({cols}, PRIMARY KEY (id))"


# ---------------------------------------------------------------- truth: the engine's own GROUP BY
def engine_key(v, engine_id):
    if v is None or v == "":
        return ""
    if isinstance(v, str):
        if engine_id in ("mariadb", "mysql"):
            from app.products.dbaba.sqlexcel import _fold

            return _fold(v)
        return v
    if isinstance(v, Decimal):
        return float(v)
    return v


def truth(tg: Target, sql, gcol, mcol, month):
    g = f"x.{gcol}"
    if month:
        if tg.kind == "sqlite":
            gexpr = f"strftime('%Y-%m', {g})"
        else:
            gexpr = f"EXTRACT(YEAR FROM {g}) * 100 + EXTRACT(MONTH FROM {g})"
    else:
        gexpr = g
    m = f"x.{mcol}" if mcol else "1"
    rows = tg.run(f"SELECT {gexpr}, COUNT(*), SUM({m}), AVG({m}), COUNT({m}) FROM ({sql}) x GROUP BY {gexpr}")
    out = {}
    for k, c, s, a, cm in rows:
        if month and k is not None:
            k = str(k).replace("-", "")[:6] if tg.kind == "sqlite" else str(int(k))
            key = k
        else:
            key = engine_key(k, tg.engine_id)
        e = out.setdefault(key, [0, 0.0, 0])
        e[0] += c
        e[1] += float(s or 0)
        e[2] += cm
    return out


# ---------------------------------------------------------------- one case
def make_cases(seed, rnd, schema, traps):
    cases = []
    tabs = [t for t in schema["tables"].values()]
    t = rnd.choice(tabs)
    cases.append({"sql": f"SELECT * FROM {t['name']}", "by": None, "measure": None})
    # a named group and measure on a domain table
    cats = [c["name"] for c in t["cols"] if c["kind"] == "cat"]
    nums = [c["name"] for c in t["cols"] if c["kind"] in ("money", "int")]
    if cats:
        cases.append({"sql": f"SELECT * FROM {t['name']}", "by": rnd.choice(cats), "measure": rnd.choice(nums) if nums else None})
    # a join: child rows with a parent column
    kids = [(tt, c) for tt in tabs for c in tt["cols"] if c["kind"] == "fk"]
    if kids:
        tt, c = rnd.choice(kids)
        parent = schema["tables"][c["ref"]]
        pcats = [x["name"] for x in parent["cols"] if x["kind"] == "cat"]
        knums = [x["name"] for x in tt["cols"] if x["kind"] in ("money", "int")]
        if pcats:
            pc = rnd.choice(pcats)
            sel = ", ".join([f"k.{x['name']}" for x in tt["cols"] if x["kind"] != "fk"][:5] + [f"p.{pc} AS parent_{pc}"])
            cases.append({"sql": f"SELECT {sel} FROM {tt['name']} k LEFT JOIN {parent['name']} p ON p.{parent['pk']} = k.{c['name']}",
                          "by": f"parent_{pc}", "measure": rnd.choice(knums) if knums else None})
    # by month
    dts = [(tt, c["name"]) for tt in tabs for c in tt["cols"] if c["kind"] == "datetime"]
    if dts:
        tt, dc = rnd.choice(dts)
        nums2 = [c["name"] for c in tt["cols"] if c["kind"] in ("money", "int")]
        cases.append({"sql": f"SELECT * FROM {tt['name']}", "by": dc, "measure": rnd.choice(nums2) if nums2 else None, "month": True})
    # the traps
    cases.append({"sql": f"SELECT * FROM {traps['name']}", "by": "label", "measure": "amount", "traps": True})
    cases.append({"sql": f"SELECT label, amount, code, big_no, note FROM {traps['name']} WHERE amount IS NOT NULL OR label IS NULL",
                  "by": "label", "measure": None, "traps": True})
    return cases


def run(n_cases=40, seed=1, show=False) -> dict:
    from app.products.dbaba import sqlexcel

    if not soffice():
        return {"skipped": "LibreOffice is not installed", "problems": 0, "cases": 0}
    tgs = targets()
    work = tempfile.mkdtemp(prefix="dbexcel_")
    rnd = random.Random(seed)
    built, problems, done = [], [], 0
    s = seed * 1000
    while done < n_cases:
        s += 1
        schema = fz.make_schema(s)
        traps = trap_table(s, rnd)
        names = schema["order"] + [traps["name"]]
        for tg in tgs:
            tg.drop(names)
            tg.create(schema)
            tg.run(_ddl_traps(traps, tg.kind))
            tg.run(f"INSERT INTO {traps['name']} VALUES ({', '.join(['%s'] * 7)})", many=[tuple(r) for r in traps["rows"]])
            if tg.kind != "sqlite":
                tg.eng.commit()
        for case in make_cases(s, rnd, schema, traps):
            for tg in tgs:
                if done >= n_cases:
                    break
                done += 1
                gz = os.path.join(work, f"c{done}.csv.gz")
                try:
                    info_x = tg.export(case["sql"], gz)
                except Exception as exc:  # noqa: BLE001
                    problems.append({"case": done, "engine": tg.kind, "sql": case["sql"], "problem": f"export failed: {exc}"})
                    continue
                cols, rows, notes = sqlexcel.read_csv(gz)
                xl = os.path.join(work, f"c{done}.xlsx")
                info = sqlexcel.build(cols, rows, xl, engine=tg.engine_id, sql=case["sql"], by=case["by"], measure=case["measure"],
                                      notes=notes, typed=False, limited=info_x.get("limited"))
                raw = tg.run(case["sql"])
                p = info["pivot"]
                eng = truth(tg, case["sql"], p["by"], p["measure"], p["mode"] == "month") if p else None
                built.append((xl, tg, case, info, raw, cols, eng))
        for tg in tgs:
            tg.drop(names)
    results = recalc([b[0] for b in built], work)
    for xl, tg, case, info, raw, cols, eng in built:
        sheets = results.get(xl) or {}
        where = {"engine": tg.kind, "sql": case["sql"], "by": case["by"], "measure": case["measure"]}
        problems += [{**where, "problem": p} for p in check(sheets, info, tg, case, raw, cols, eng)]
    shutil.rmtree(work, ignore_errors=True)
    for tg in tgs:
        if tg.tmp:
            os.unlink(tg.tmp)
    out = {"cases": len(built), "problems": len(problems), "fails": problems[:20],
           "pivots": sum(1 for b in built if b[3]["pivot"]),
           "formula_kinds": sorted({k for b in built if b[3]["pivot"] for k in b[3]["pivot"]["formulas"]})}
    return out


def check(sheets, info, tg, case, raw, cols, eng) -> list[str]:
    from app.products.dbaba import sqlexcel

    out = []
    data = sheets.get("Data")
    if not data:
        return ["LibreOffice could not open the workbook"]
    if data[0] != [str(c) for c in cols]:
        out.append(f"Data header {data[0]} != {cols}")
    if len(data) - 1 != len(raw):
        out.append(f"Data has {len(data) - 1} rows, the database returned {len(raw)}")
    # every cell kept: long numbers and codes digit for digit, '=' text as text
    for r_db, r_x in zip(raw[:400], data[1:401]):
        for v, x in zip(r_db, r_x):
            if v is None:
                if x not in ("",):
                    out.append(f"NULL became {x!r}")
            elif isinstance(v, str):
                if x != sqlexcel._text(v, {}) and not (v.strip() == "" and x == ""):
                    out.append(f"text {v!r} became {x!r}")
            elif isinstance(v, bool):
                pass
            elif isinstance(v, int):
                if str(v) != x.replace(",", "") and not _close(v, _num(x)):
                    out.append(f"number {v} became {x!r}")
            elif isinstance(v, (Decimal, float)):
                if not _close(v, _num(x)):
                    out.append(f"number {v} became {x!r}")
        if len(out) > 5:
            return out
    p = info["pivot"]
    if case.get("by") and not p:
        return out + [f"no pivot for a named column ({info.get('no_pivot')})"]
    if not p:
        return out
    piv = sheets.get("Pivot")
    if not piv:
        return out + ["no Pivot sheet after recalculation"]
    body = piv[1:1 + p["groups"]]
    total = piv[1 + p["groups"]] if len(piv) > 1 + p["groups"] else None
    has_m = p["measure"] is not None
    for v, row in zip(p["values"], body):
        if _num(row[1]) != v["count"]:
            out.append(f"group {v['label']!r}: formula counted {row[1]}, rows {v['count']}")
        if has_m:
            if not _close(_num(row[2]) or 0.0, v["sum"]):
                out.append(f"group {v['label']!r}: formula sum {row[2]}, rows {v['sum']}")
            if not _close(_num(row[3]) if row[3] != "" else None, v["avg"]):
                out.append(f"group {v['label']!r}: formula average {row[3]}, rows {v['avg']}")
        for cell in row:
            if str(cell).startswith(("#", "Err:")):
                out.append(f"group {v['label']!r}: Excel error {cell}")
    if total is None or _num(total[1]) != len(raw):
        out.append(f"total row {total} != {len(raw)} rows")
    # against the engine's own GROUP BY
    gcol, mcol = p["by"], p["measure"]
    month = p["mode"] == "month"
    mine = {}
    for v in p["values"]:
        lab = v["label"]
        if month:
            key = "" if lab == sqlexcel.EMPTY_LABEL else dt.datetime.strptime(lab, "%b %Y").strftime("%Y%m")
        elif lab == sqlexcel.EMPTY_LABEL:
            key = ""
        else:
            bi = cols.index(gcol)
            orig = next((r[bi] for r in raw if r[bi] is not None and _label_match(r[bi], lab)), lab)
            key = engine_key(orig, tg.engine_id)
        e = mine.setdefault(key, [0, 0.0, 0, []])
        e[0] += v["count"]
        e[1] += v["sum"] or 0
        e[2] += v["values"]
        e[3].append(lab)
    if set(mine) != set(eng):
        out.append(f"groups differ from the engine: only here {sorted(map(str, set(mine) - set(eng)))[:5]}, "
                   f"only in the engine {sorted(map(str, set(eng) - set(mine)))[:5]}")
    merged_named = " ".join(info["notes"])
    for k, e in mine.items():
        t = eng.get(k)
        if t is None:
            continue
        if e[0] != t[0]:
            out.append(f"group {k!r}: {e[0]} rows here, the engine's GROUP BY says {t[0]}")
        if has_m and not _close(e[1], t[1]):
            out.append(f"group {k!r}: sum {e[1]} here, the engine says {t[1]}")
        if len(e[3]) > 1 and not all(repr(x) in merged_named for x in e[3][:4]):
            out.append(f"the engine merges {e[3]} but the notes don't say so")
    if has_m and any(v["values"] == 0 for v in p["values"]) and "NULL" not in merged_named:
        out.append("a group with no values but no SUM-is-NULL note")
    return out


def _label_match(v, lab):
    if isinstance(v, str):
        return v == lab
    if isinstance(v, (dt.datetime, dt.date)):
        return str(v) == lab or v.isoformat() == lab
    return _close(v, _num(lab)) if isinstance(_num(lab), float) else str(v) == str(lab)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    n = int(argv[0]) if argv else 40
    seed = int(argv[1]) if len(argv) > 1 else 1
    res = run(n, seed)
    print(json.dumps(res, indent=1, default=str, ensure_ascii=False))
    print(f"problems: {res['problems']}")
    return 0 if res["problems"] == 0 else 1


if __name__ == "__main__":
    fz._test_env()
    sys.exit(main())
