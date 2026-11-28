"""The transactions lab (E1 night 4): a script run statement by statement the way a database's command line runs it
(autocommit on, an error reported and the next statement run, like `mysql --force` / psql / the sqlite3 shell), then what is
SAVED read from a second connection while the first is still open (an open transaction's changes aren't saved).

Every script works on scratch tables: lab_tx (id, v) with the row (1, 10), lab_log (id, note), empty, and lab_tx2 for the
DDL cases; all are dropped afterwards. A result is {"v": the saved v of row 1 (or None when the row is gone), "log": the
saved rows of lab_log, "errors": [each error's message, without the server's code prefix], "warnings": [PostgreSQL's
warnings], "open": the first connection is still in a transaction at the end}. A stored lab line is compared on the keys it
has.

Used by tests/practice/test_e1_sqlite4.py (each dialect file's txn_rules lab lines are run again) and to write those lines.
"""

import os
import re
import sqlite3
import tempfile

from app.products.dbaba.sqlroutine import split_script

SETUP = ["CREATE TABLE lab_tx (id INT PRIMARY KEY, v INT)", "INSERT INTO lab_tx VALUES (1, 10)",
         "CREATE TABLE lab_log (id INT PRIMARY KEY, note VARCHAR(40))"]


def _clean(msg: str) -> str:
    msg = str(msg).strip().splitlines()[0]
    msg = re.sub(r"^\(\d+, ['\"](.*)['\"]\)$", r"\1", msg)          # pymysql: (1305, 'SAVEPOINT x does not exist')
    return msg.strip()


def _saved(read) -> int | None:
    rows = read("SELECT v FROM lab_tx WHERE id = 1")
    return int(rows[0][0]) if rows else None


def _log(read) -> int:
    return int(read("SELECT COUNT(*) FROM lab_log")[0][0])


def run_sqlite(script: str) -> dict:
    d = tempfile.mkdtemp()
    p = os.path.join(d, "lab.db")
    a = sqlite3.connect(p, isolation_level=None, timeout=0)
    try:
        for s in SETUP:
            a.execute(s)
        errors = []
        for stmt in split_script(script):
            try:
                a.execute(stmt).fetchall()
            except sqlite3.Error as exc:
                errors.append(_clean(exc))
        is_open = a.in_transaction
        b = sqlite3.connect(p, isolation_level=None, timeout=0)
        try:
            v, log = _saved(lambda q: b.execute(q).fetchall()), _log(lambda q: b.execute(q).fetchall())
        except sqlite3.OperationalError:
            v, log = "locked", "locked"                             # rollback-journal mode: an open writer blocks this read
        b.close()
        return {"v": v, "errors": errors, "warnings": [], "open": is_open, "log": log}
    finally:
        a.close()
        for f in os.listdir(d):
            os.remove(os.path.join(d, f))
        os.rmdir(d)


def _engine(kind: str):
    from tests.fuzz.db import MYSQL, PG, Engine

    return Engine(MYSQL if kind == "mysql" else PG).open()


def run_server(kind: str, script: str) -> dict:
    """kind: 'mysql' (MariaDB here) or 'postgres'."""
    a, b = _engine(kind), _engine(kind)
    warnings = []
    if kind == "postgres":
        a.conn.add_notice_handler(lambda n: warnings.append(n.message_primary) if n.severity == "WARNING" else None)
    try:
        for t in ("lab_tx2", "lab_log", "lab_tx"):
            a.run(f"DROP TABLE IF EXISTS {t}")
        for s in SETUP:
            a.run(s)
        errors = []
        for stmt in split_script(script):
            try:
                a.run(stmt)
            except Exception as exc:  # noqa: BLE001 - the error is the lab's result
                errors.append(_clean(exc.diag.message_primary if hasattr(exc, "diag") and exc.diag.message_primary else
                                     (exc.args[1] if len(exc.args) > 1 else exc)))
        if kind == "mysql":
            is_open = bool(a.run("SELECT @@in_transaction")[0][0])
        else:
            from psycopg.pq import TransactionStatus

            is_open = a.conn.info.transaction_status != TransactionStatus.IDLE
        v = _saved(b.run)
        return {"v": v, "errors": errors, "warnings": list(warnings), "open": is_open, "log": _log(b.run)}
    finally:
        a.rollback()
        for t in ("lab_tx2", "lab_log", "lab_tx"):
            try:
                b.run(f"DROP TABLE IF EXISTS {t}")
            except Exception:  # noqa: BLE001
                pass
        a.conn.close()
        b.conn.close()


def run(engine: str, script: str) -> dict:
    """engine: 'sqlite', 'mariadb' or 'postgresql'."""
    if engine == "sqlite":
        return run_sqlite(script)
    return run_server("mysql" if engine == "mariadb" else "postgres", script)
