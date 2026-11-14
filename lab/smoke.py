"""Can each lab database be reached and does it answer? Prints one line per engine; exit 0 only when all answer.
Run inside the lab job (LAB_PW set). --quiet prints nothing (used while waiting for start-up)."""

import os
import sys

PW = os.environ.get("LAB_PW", "")
QUIET = "--quiet" in sys.argv


def sqlserver():
    import pymssql

    with pymssql.connect(server="127.0.0.1", port=14330, user="sa", password=PW, login_timeout=5) as c:
        cur = c.cursor()
        cur.execute("SELECT @@VERSION")
        return cur.fetchone()[0].splitlines()[0]


def oracle():
    import oracledb

    with oracledb.connect(user="lab", password=PW, dsn="127.0.0.1:15210/FREEPDB1", tcp_connect_timeout=5) as c:
        cur = c.cursor()
        cur.execute("SELECT banner FROM v$version WHERE ROWNUM = 1")
        return cur.fetchone()[0]


def mongo():
    from pymongo import MongoClient

    c = MongoClient("mongodb://127.0.0.1:27170", serverSelectionTimeoutMS=5000)
    return "MongoDB " + c.server_info()["version"]


ok = True
for name, fn in (("sqlserver", sqlserver), ("oracle", oracle), ("mongo", mongo)):
    try:
        v = fn()
        if not QUIET:
            print(f"{name}: ok ({v})")
    except Exception as exc:  # noqa: BLE001
        ok = False
        if not QUIET:
            print(f"{name}: not answering ({str(exc).splitlines()[0][:120]})")
sys.exit(0 if ok else 1)
