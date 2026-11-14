"""Run the lab cases against SQL Server, Oracle and MongoDB on the night lab (docs/lab-server.md) and print what each engine
really did, one JSON line per case, between LAB-RESULTS-BEGIN and LAB-RESULTS-END in the job log. The nightly DBaba run
writes the cases, pushes, and reads these lines back from the job log: only what the lab shows becomes a rule.

Cases (made-up data only):
- lab/cases/sqlserver/<name>.sql and lab/cases/oracle/<name>.sql: statements separated by a line holding only `GO` (or `/`
  for Oracle). Every statement runs in a fresh scratch schema; the result of each statement is kept: the rows (first 50),
  the row count of a change, or the error code and message.
- lab/cases/mongo/<name>.json: {"setup": [command, ...], "run": [command, ...]}: database commands for db.command() on a
  fresh scratch database (insert, find, aggregate, createIndexes, explain ...). The reply of each "run" command is kept.
Each case is cleaned up afterwards (the scratch schema / database is dropped)."""

import json
import os
import re
import sys
from pathlib import Path

PW = os.environ.get("LAB_PW", "")
HERE = Path(__file__).resolve().parent / "cases"


def _val(v):
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def _split(text: str, sep: str) -> list[str]:
    """Statements separated by a line holding only the separator; parts that are only comments are skipped."""
    out = []
    for p in re.split(rf"^\s*{re.escape(sep)}\s*$", text, flags=re.M):
        body = "\n".join(ln for ln in p.strip().splitlines() if not ln.strip().startswith("--")).strip()
        if body:
            out.append(body.rstrip(";") if sep == "/" else body)
    return out


def _sql_case(cur, stmts, code_of):
    out = []
    for s in stmts:
        try:
            cur.execute(s)
            if cur.description:
                rows = cur.fetchmany(50)
                out.append({"sql": s[:300], "columns": [d[0] for d in cur.description], "rows": [[_val(x) for x in r] for r in rows]})
            else:
                out.append({"sql": s[:300], "changed": cur.rowcount})
        except Exception as exc:  # noqa: BLE001 - the error is the result
            out.append({"sql": s[:300], "error": code_of(exc), "message": str(exc).splitlines()[0][:300]})
    return out


def sqlserver(files):
    import pymssql

    for f in files:
        conn = pymssql.connect(server="127.0.0.1", port=14330, user="sa", password=PW, autocommit=True, login_timeout=10)
        cur = conn.cursor()
        cur.execute("IF DB_ID('lab_case') IS NOT NULL BEGIN ALTER DATABASE lab_case SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE lab_case; END")
        cur.execute("CREATE DATABASE lab_case")
        cur.execute("USE lab_case")
        res = _sql_case(cur, _split(f.read_text(encoding="utf-8"), "GO"), lambda e: str(e.args[0]) if e.args else "err")
        cur.execute("USE master")
        cur.execute("ALTER DATABASE lab_case SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE lab_case")
        conn.close()
        yield f.stem, res


def oracle(files):
    import oracledb

    for f in files:
        conn = oracledb.connect(user="lab", password=PW, dsn="127.0.0.1:15210/FREEPDB1")
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM user_tables")
        for (t,) in cur.fetchall():
            cur.execute(f'DROP TABLE "{t}" CASCADE CONSTRAINTS PURGE')
        res = _sql_case(cur, _split(f.read_text(encoding="utf-8"), "/"),
                        lambda e: f"ORA-{e.args[0].code:05d}" if e.args and hasattr(e.args[0], "code") else "err")
        cur.execute("SELECT table_name FROM user_tables")
        for (t,) in cur.fetchall():
            cur.execute(f'DROP TABLE "{t}" CASCADE CONSTRAINTS PURGE')
        conn.close()
        yield f.stem, res


def mongo(files):
    from bson import json_util
    from pymongo import MongoClient

    c = MongoClient("mongodb://127.0.0.1:27170", serverSelectionTimeoutMS=10000)
    for f in files:
        spec = json_util.loads(f.read_text(encoding="utf-8"))
        db = c["lab_case"]
        c.drop_database("lab_case")
        res = []
        for cmd in spec.get("setup", []):
            db.command(cmd)
        for cmd in spec.get("run", []):
            try:
                r = db.command(cmd)
                r.pop("$clusterTime", None)
                r.pop("operationTime", None)
                res.append({"command": json.loads(json_util.dumps(cmd)), "reply": json.loads(json_util.dumps(r))})
            except Exception as exc:  # noqa: BLE001 - the error is the result
                res.append({"command": json.loads(json_util.dumps(cmd)), "error": getattr(exc, "code", "err"), "message": str(exc)[:300]})
        c.drop_database("lab_case")
        yield f.stem, res


def main() -> int:
    print("LAB-RESULTS-BEGIN")
    bad = 0
    for engine, fn, pattern in (("sqlserver", sqlserver, "*.sql"), ("oracle", oracle, "*.sql"), ("mongo", mongo, "*.json")):
        files = sorted((HERE / engine).glob(pattern))
        try:
            for name, res in fn(files):
                print(json.dumps({"engine": engine, "case": name, "results": res}, ensure_ascii=False, default=str))
        except Exception as exc:  # noqa: BLE001 - one engine down doesn't hide the others
            bad += 1
            print(json.dumps({"engine": engine, "case": None, "lab_error": str(exc).splitlines()[0][:300]}))
    print("LAB-RESULTS-END")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
