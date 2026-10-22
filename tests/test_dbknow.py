"""The database knowledge files stay valid, and their query templates really run on MySQL and PostgreSQL."""

import re
import socket

import pytest

from app import dbknow
from tests.test_database import gc


def test_knowledge_files_are_valid():
    assert dbknow.validate() == []


def test_error_and_slow_lookup():
    assert dbknow.find_error("mysql", "(1054, \"Unknown column 'ordr_date' in 'field list'\")")["fix"]["type"] == "closest_column"
    assert dbknow.find_error("postgres", 'column "Name" does not exist')["id"] == "pg-undefined-column"
    ids = {r["id"] for r in dbknow.slow_findings("mysql", "SELECT * FROM orders WHERE YEAR(created) = 2026 AND name LIKE '%ab%'")}
    assert {"function-on-indexed-column", "leading-wildcard-like"} <= ids


def _up(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


SAMPLE = {"table": "orders", "date_col": "created", "months": 6, "column": "amount", "pk": "id", "child": "orders",
          "fk": "id", "days": 60}


@pytest.mark.parametrize("engine,port,cfg", [
    ("mysql", 3306, {"type": "mysql", "host": "127.0.0.1", "port": "3306", "database": "shop_db", "user": "shopapp", "password": "app pass"}),
    ("postgres", 5432, {"type": "postgres", "host": "127.0.0.1", "port": "5432", "database": "shop_pg", "user": "shopapp", "password": "app pass"}),
])
def test_templates_run_read_only(engine, port, cfg):
    """Every question template must be a valid read-only query on a real database (scripts/setup_test_dbs.sh)."""
    if not _up(port):
        pytest.skip(f"needs local {engine}")
    db = gc.Database(cfg).connect()
    if engine == "mysql":
        db.rows("SELECT 1")
        try:
            db.rows("SELECT created FROM orders LIMIT 1")
        except Exception:  # noqa: BLE001
            w = gc.Database({**cfg, "writer": True}).connect()
            cur = w.cursor(read_only=False)
            w.start_write(cur)
            cur.execute("ALTER TABLE orders ADD COLUMN created DATETIME DEFAULT CURRENT_TIMESTAMP")
            w.conn.commit()
    for q in dbknow.load("questions"):
        tpl = q["templates"].get(engine)
        if not tpl:
            continue
        sql = tpl.format(**{k: SAMPLE.get(k, "id") for k in re.findall(r"{(\w+)}", tpl)})
        db.query(sql, 50)          # raises if the template is broken or not read-only


@pytest.mark.parametrize("engine,port,cfg", [
    ("mysql", 3306, {"type": "mysql", "host": "127.0.0.1", "port": "3306", "database": "shop_db", "user": "shopapp", "password": "app pass"}),
    ("postgres", 5432, {"type": "postgres", "host": "127.0.0.1", "port": "5432", "database": "shop_pg", "user": "shopapp", "password": "app pass"}),
])
def test_playbook_checks_run_read_only(engine, port, cfg):
    """Every automatic check in an issue playbook must run, read-only, on a real database."""
    if not _up(port):
        pytest.skip(f"needs local {engine}")
    db = gc.Database(cfg).connect()
    for issue in dbknow.load("issues"):
        for d in issue["diagnose"]:
            if d.get("engine") == engine and d.get("sql"):
                db.rows(gc.safe_sql(d["sql"]))
