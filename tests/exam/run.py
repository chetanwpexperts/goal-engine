"""DBaba's exam: the 5 practice papers (150 questions), asked the way a user would, graded without anyone's opinion.

  query / debugging -> the SQL in DBaba's answer is run on real MySQL, MariaDB and PostgreSQL with the practice
                       data and must return what the key's query returns
  change (UPDATE...) -> run inside a transaction, the table compared with the key's result, then rolled back
  theory / traps    -> DBaba's answer must contain each must-have fact

The key (papers/paperN.json) is itself proved first: --verify-key runs every key query on every engine.
DBaba never sees the key. Run:
    python -m tests.exam.run --verify-key            prove the key on the engines
    python -m tests.exam.run --papers 1               the exam (report in exam_report.json)
Engines: EXAM_MYSQL / EXAM_MARIADB / EXAM_POSTGRES = host:port:user:password (database 'exam' is (re)built).
Defaults are the knowledge lab ports (3307 MySQL, 3308 MariaDB, 5433 PostgreSQL; tools/knowledge/docker-compose.yml).
"""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULTS = {"mysql": "127.0.0.1:3307:root:ge", "mariadb": "127.0.0.1:3308:root:ge", "postgres": "127.0.0.1:5433:postgres:ge"}


# ------------------------------------------------------------------ engines
def connect(engine: str):
    spec = os.getenv(f"EXAM_{engine.upper()}", DEFAULTS[engine])
    if spec in ("", "off"):
        return None
    host, port, user, pw = (spec.split(":", 3) + [""] * 4)[:4]
    try:
        if engine == "postgres":
            import psycopg

            c = psycopg.connect(host=host, port=int(port), user=user, password=pw, dbname="postgres", autocommit=True, connect_timeout=5)
            cur = c.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname = 'exam'")
            if not cur.fetchone():
                cur.execute("CREATE DATABASE exam")
            c.close()
            return psycopg.connect(host=host, port=int(port), user=user, password=pw, dbname="exam", autocommit=True)
        import pymysql

        c = pymysql.connect(host=host, port=int(port), user=user, password=pw, autocommit=True, connect_timeout=5)
        c.cursor().execute("CREATE DATABASE IF NOT EXISTS exam")
        c.select_db("exam")
        return c
    except Exception as exc:  # noqa: BLE001
        print(f"  ({engine} not reachable: {str(exc)[:80]})")
        return None


def load(engine: str, c):
    setup = (HERE / ("setup_postgres.sql" if engine == "postgres" else "setup_mysql.sql")).read_text()
    setup += "\n" + (HERE / "extra_setup.sql").read_text()
    if engine == "postgres":
        setup = setup.replace("DROP TABLE IF EXISTS projects;", "DROP TABLE IF EXISTS projects CASCADE;")
    cur = c.cursor()
    for stmt in _statements(re.sub(r"--[^\n]*", "", setup)):
        cur.execute(stmt)


def _statements(text: str) -> list[str]:
    out, cur, q = [], [], None
    for ch in text:
        if q:
            q = None if ch == q else q
        elif ch in "'\"":
            q = ch
        elif ch == ";":
            s = "".join(cur).strip()
            if s:
                out.append(s)
            cur = []
            continue
        cur.append(ch)
    s = "".join(cur).strip()
    return out + ([s] if s else [])


def _norm(v):
    if isinstance(v, decimal.Decimal) or isinstance(v, float):
        return round(float(v), 2)
    if isinstance(v, (dt.date, dt.datetime)):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return v.decode(errors="replace")
    return v


def run(c, sql: str):
    """(column names, rows) or raises."""
    cur = c.cursor()
    cur.execute(sql.strip().rstrip(";"))
    names = [d[0].lower() for d in (cur.description or [])]
    rows = [tuple(_norm(v) for v in r) for r in (cur.fetchall() if cur.description else [])]
    return names, rows


def changed_state(engine: str, c, stmt: str, state: str):
    """Run an UPDATE/DELETE inside a transaction, read the table, roll back."""
    if engine == "postgres":
        c.autocommit = False
    else:
        c.autocommit(False)
    try:
        cur = c.cursor()
        cur.execute(stmt.strip().rstrip(";"))
        return run(c, state)[1]
    finally:
        c.rollback()
        if engine == "postgres":
            c.autocommit = True
        else:
            c.autocommit(True)


# ------------------------------------------------------------------ comparing results
def project(names, rows, cols):
    idx = [names.index(x) for x in cols if x in names]
    if len(idx) == len(cols):
        return [tuple(r[i] for i in idx) for r in rows]
    return None


def same(q: dict, ref, got) -> tuple[bool, str]:
    (rn, rr), (gn, gr) = ref, got
    if "rowcount" in q:
        return len(gr) == q["rowcount"], f"{len(gr)} rows, expected {q['rowcount']}"
    if "positional" in q:
        k = q["positional"]
        if gn and len(gn) < k:
            return False, f"{len(gn)} columns"
        a, b = [r[:k] for r in rr], [r[:k] for r in gr]
        return Counter(a) == Counter(b), "rows differ"
    cols = q.get("cols") or rn
    a, b = project(rn, rr, cols), project(gn, gr, cols)
    if b is None:
        if len(gn) == len(rn):
            b = gr
            a = rr
        else:
            return False, f"columns {gn} lack {cols}"
    if q.get("ordered"):
        return a == b, "rows or their order differ"
    ok = Counter(a) == Counter(b)
    if ok and q.get("ordered_cols"):
        oa, ob = project(rn, rr, q["ordered_cols"]), project(gn, gr, q["ordered_cols"])
        ok = ob is not None and oa == ob
        return ok, "order differs"
    return ok, "rows differ"


# ------------------------------------------------------------------ the paper
def questions(paper: int) -> dict[int, str]:
    text = (HERE / "questions.txt").read_text()
    parts = re.split(r"(?m)^Sample Paper (\d+):", text)
    body = dict(zip(parts[1::2], parts[2::2])).get(str(paper), "")
    body = re.sub(r"(?m)^MySQL • PostgreSQL • MariaDB.*Page \d+ of \d+\s*$", "", body)
    body = re.sub(r"(?m)^Section [A-C]:.*$", "", body)
    out = {}
    for m in re.finditer(r"(?ms)^Q(\d+)\.\s+[A-Z ]+?\s{2}(.+?)(?=^Q\d+\.|\Z)", body):
        out[int(m.group(1))] = re.sub(r"[ \t]+", " ", m.group(2)).strip()
    return out


SQL_RE = re.compile(r"(?is)\b((?:with|select|update|delete|insert)\b.+?)(?:;|```|\n\s*\n|$)")


def sql_in(text: str) -> list[str]:
    blocks = re.findall(r"(?s)```(?:sql)?\s*(.+?)```", text)
    found = [b.strip() for b in blocks]
    for m in SQL_RE.finditer(text):
        s = m.group(1).strip()
        if re.search(r"(?i)\b(from|set|where)\b", s) and len(s) < 4000:
            found.append(s)
    seen, out = set(), []
    for s in found:
        for part in _statements(re.sub(r"--[^\n]*", "", s)):
            if re.match(r"(?i)\s*(with|select|update|delete|insert)\b", part) and part not in seen:
                seen.add(part)
                out.append(part)
    return out


def texts(o) -> list[str]:
    if isinstance(o, str):
        return [o]
    if isinstance(o, dict):
        return [t for v in o.values() for t in texts(v)]
    if isinstance(o, list):
        return [t for v in o for t in texts(v)]
    return []


def ask_dbaba(client, question: str) -> str:
    client.post("/api/new-chat")
    r = client.post("/api/message", json={"text": question}).json()
    goal = (r.get("goal") or {}).get("id")
    if "which database" in (r.get("reply") or "").lower():              # a user would answer this once
        r2 = client.post("/api/message", json={"text": "MySQL", **({"goal_id": goal} if goal else {})}).json()
        r = {"first": r, "second": r2}
    try:
        state = client.get("/api/state").json()
        r["_cards"] = [g for g in state.get("goals", []) if g.get("id") == goal]
    except Exception:  # noqa: BLE001
        pass
    reply = (r.get("second") or r.get("first") or r).get("reply", "") if isinstance(r, dict) else ""
    return "REPLY: " + str(reply) + "\n" + "\n".join(texts(r))


def _reply_only(ans: str) -> str:
    return ans


def has(text: str, word: str) -> bool:
    w = re.escape(word.lower())
    return re.search(rf"(?<![\w.]){w}(?![\w])" if re.match(r"^[\w ]+$", word) else w, text.lower()) is not None


def grade(q: dict, answer: str, conns: dict, refs: dict) -> tuple[bool, str]:
    if q["type"] == "theory":
        miss = [g[0] for g in q["must"] if not any(has(answer, w) for w in g)]
        return not miss, ("missing: " + ", ".join(miss)) if miss else "all facts present"
    cands = sql_in(answer)
    if q.get("forbid"):
        cands = [s for s in cands if not re.search(q["forbid"], s, re.I)]
    if q.get("require"):
        cands = [s for s in cands if re.search(q["require"], s, re.I)]
    if not cands:
        return False, "no SQL in the answer"
    why = "no candidate matched"
    for s in cands:
        for eng, c in conns.items():
            if eng not in refs:
                continue
            try:
                if q["type"] == "change":
                    if not re.match(r"(?i)\s*(update|delete)\b", s):
                        continue
                    ok = changed_state(eng, c, s, q["state"]) == refs[eng]
                    why = "table after it differs"
                else:
                    if not re.match(r"(?i)\s*(with|select)\b", s):
                        continue
                    ok, why = same(q, refs[eng], run(c, s))
            except Exception as exc:  # noqa: BLE001
                ok, why = False, f"{eng}: {str(exc).splitlines()[0][:90]}"
            if ok:
                return True, f"matched on {eng}"
    return False, why


def key_results(q: dict, conns: dict) -> dict:
    out = {}
    for eng, c in conns.items():
        if q.get("engines") and eng not in q["engines"]:
            continue
        ref = q.get(f"ref_{'postgres' if eng == 'postgres' else 'mysql'}") or q.get("ref")
        if not ref:
            continue
        if q["type"] == "change":
            out[eng] = changed_state(eng, c, ref, q["state"])
        else:
            out[eng] = run(c, ref)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", default="1")
    ap.add_argument("--verify-key", action="store_true")
    args = ap.parse_args()
    conns = {e: c for e in DEFAULTS if (c := connect(e)) is not None}
    if not conns:
        sys.exit("no database engine reachable")
    for e, c in conns.items():
        load(e, c)
    print("engines:", ", ".join(conns))
    papers = [int(p) for p in args.papers.split(",")]
    report, total, passed = {}, 0, 0
    client = None
    if not args.verify_key:
        os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(HERE / ".exam.db"))
        os.environ.update(ENGINE_MODE="ml", SITE_MODE="open", AGENT_SCHEDULER="off")
        sys.path.insert(0, str(HERE.parents[1]))
        import logging

        logging.disable(logging.WARNING)
        from fastapi.testclient import TestClient

        from app import limits
        from app.main import app

        limits.BURST.update(visitor=10**6, ip=10**6, all=10**6)
        client = TestClient(app).__enter__()
    for p in papers:
        key = json.loads((HERE / "papers" / f"paper{p}.json").read_text())
        qtext = questions(p)
        for q in key["questions"]:
            n = q["n"]
            try:
                refs = key_results(q, conns) if q["type"] != "theory" else {}
            except Exception as exc:  # noqa: BLE001
                print(f"P{p} Q{n}: KEY FAILS: {exc}")
                continue
            if args.verify_key:
                if q.get("proof"):
                    print(f"P{p} Q{n} proof:", {e: run(c, q["proof"])[1] for e, c in conns.items()})
                for e, r in refs.items():
                    print(f"P{p} Q{n} {e}: {r if q['type'] == 'change' else r[1]}"[:200])
                continue
            ans = ask_dbaba(client, qtext.get(n, ""))
            ok, why = grade(q, ans, conns, refs)
            total += 1
            passed += ok
            report[f"P{p}Q{n}"] = {"pass": ok, "why": why, "type": q["type"], "question": qtext.get(n, "")[:200],
                                   "answer": _reply_only(ans)[:1500]}
            print(f"P{p} Q{n:>2} {'PASS' if ok else 'fail'}  [{q['type']}] {why}")
    if not args.verify_key:
        print(f"\nscore: {passed}/{total}")
        (HERE / "exam_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
