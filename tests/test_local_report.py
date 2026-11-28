"""S5: the full database report (app/local/checks.py, app/local/report.py).

Two kinds of proof:
* the judging, on gathered facts written out here (runs anywhere): every threshold, every "can't see" case, no fix
  where nothing is proven, the engines' published end-of-support dates;
* the lab, on the sandbox MariaDB and PostgreSQL with tests/s5lab.py's planted problems: duplicate and covering indexes,
  a foreign key without an index, dead rows, slow queries read by the query checker, a real lock wait and a real
  deadlock. Statistics that are switched off on a server (performance_schema, pg_stat_statements) give the honest
  "DBaba can't see" item, and the parts that need them are checked only where they are on.
"""

import datetime as dt
import threading
import time

import pytest

from app.products.dbaba.local import checks, report
from tests.test_local import needs_maria, needs_pg

MY_DB = {"type": "mariadb", "host": "127.0.0.1", "port": 3306, "name": "shop_db", "user": "shopapp", "password": "app pass"}
PG_DB = {"type": "postgresql", "host": "127.0.0.1", "port": 5432, "name": "shop_pg", "user": "shopapp", "password": "app pass"}
GB = 1024 ** 3


def ids(items, sec=None):
    return [i["id"] for s, its in items.items() if sec in (None, s) for i in its]


def by_id(items, iid):
    return next(i for its in items.values() for i in its if i["id"] == iid)


def my_g(**kw):
    g = {"vars": {"innodb_buffer_pool_size": 128 * 1024 ** 2, "max_connections": 151, "performance_schema": 1, "slow_query_log": 1,
                  "log_bin": 1, "bind_address": "127.0.0.1", "version": "10.11.14-MariaDB"},
         "status": {"Uptime": str(30 * 86400), "Max_used_connections": "10", "Threads_connected": "2", "Innodb_deadlocks": "0"},
         "indexes": [], "tables": [], "fks": [], "slow": [], "index_use": [], "lock_waits": [], "accounts": []}
    g.update(kw)
    return g


def pg_g(**kw):
    g = {"vars": {"shared_buffers": ("16384", "8kB"), "max_connections": ("100", None), "autovacuum": ("on", None),
                  "listen_addresses": ("localhost", None), "ssl": ("on", None), "password_encryption": ("scram-sha-256", None),
                  "archive_mode": ("on", None)},
         "started": (dt.timedelta(days=30), 0), "indexes": [], "tables": [], "fks": [], "slow": [], "index_use": [],
         "lock_waits": [], "accounts": [["app", False]]}
    g.update(kw)
    return g


# ---------------------------------------------------------------- indexes
def test_covered_and_duplicate_indexes_but_never_a_unique_one():
    idx = [["t", "ix_a", 1, "a", 1], ["t", "ix_a2", 1, "a", 1], ["t", "ix_ab", 1, "a", 1], ["t", "ix_ab", 2, "b", 1],
           ["t", "uq_a", 1, "a", 0], ["t", "PRIMARY", 1, "id", 0], ["t", "ix_b", 1, "b", 1]]
    out = checks.judge(my_g(indexes=idx), "mariadb", MY_DB)
    found = ids(out, "indexes")
    assert "dup_t_ix_a" in found and "dup_t_ix_a2" in found               # both are the first part of ix_ab
    assert not any(x.endswith(("uq_a", "PRIMARY", "ix_ab", "ix_b")) for x in found)   # unique keeps its rule; b isn't a prefix
    assert by_id(out, "dup_t_ix_a")["fix"]["sql"] == "ALTER TABLE t DROP INDEX ix_a;"


def test_unused_indexes_wait_seven_days_and_say_so_in_one_line():
    idx = [["t", "ix_a", 1, "a", 1], ["t", "ix_b", 1, "b", 1], ["t", "uq", 1, "c", 0]]
    use = [["t", "ix_a", 0], ["t", "ix_b", 5], ["t", "uq", 0], ["t", "PRIMARY", 0]]
    young = checks.judge(my_g(indexes=idx, index_use=use, status={"Uptime": "7200"}), "mariadb", MY_DB)
    assert ids(young, "indexes") == ["unused_young"] and "2 hours" in by_id(young, "unused_young")["what"]
    old = checks.judge(my_g(indexes=idx, index_use=use), "mariadb", MY_DB)
    assert ids(old, "indexes") == ["unused_t_ix_a"] and by_id(old, "unused_t_ix_a")["severity"] == "warning"


def test_postgres_foreign_key_without_index():
    idx = [["orders", "orders_pkey", 1, "id", 0], ["orders", "ix_cs", 1, "customer_id", 1], ["orders", "ix_cs", 2, "status", 1]]
    fks = [["orders", "fk_customer", ["customer_id"]], ["orders", "fk_shop", ["shop_id"]]]
    out = checks.judge(pg_g(indexes=idx, fks=fks), "postgresql", PG_DB)
    assert ids(out, "indexes") == ["fkidx_orders_fk_shop"]                 # customer_id leads ix_cs, so it is covered
    assert by_id(out, "fkidx_orders_fk_shop")["fix"]["sql"].startswith("CREATE INDEX CONCURRENTLY")


# ---------------------------------------------------------------- tables
def test_wasted_space_only_when_big_and_a_real_share():
    tabs = [["fresh", 20000, 1_589_248, 0, 4_194_304],                     # lab: a fresh table shows 4 MB DATA_FREE
            ["churned", 1_000_000, 400 * 1024 ** 2, 0, 300 * 1024 ** 2]]
    out = checks.judge(my_g(tables=tabs), "mariadb", MY_DB)
    assert "free_churned" in ids(out, "tables") and "free_fresh" not in ids(out, "tables")


def test_postgres_dead_rows_and_stale_statistics():
    tabs = [["scans", 40000, 10, 0, 10000, 30000, None, None], ["calm", 1_000_000, 10, 0, 5000, 9000, None, None],
            ["stale", 100_000, 10, 0, 0, 50_000, None, None]]
    out = checks.judge(pg_g(tables=tabs), "postgresql", PG_DB)
    t = ids(out, "tables")
    assert "dead_scans" in t and "dead_calm" not in t and "stale_stale" in t and "stale_scans" not in t


# ---------------------------------------------------------------- settings, backups, security
def test_memory_advice_only_for_a_database_on_this_machine():
    g = my_g()
    assert "buffer_pool" in ids(checks.judge(g, "mariadb", MY_DB, ram=8 * GB))
    assert "buffer_pool" not in ids(checks.judge(g, "mariadb", {**MY_DB, "host": "db.internal"}, ram=8 * GB))
    assert "buffer_pool" not in ids(checks.judge(g, "mariadb", MY_DB, ram=None))
    pg = checks.judge(pg_g(), "postgresql", PG_DB, ram=8 * GB)
    assert "shared_buffers" in ids(pg) and "restart" in by_id(pg, "shared_buffers")["fix"]["sql"]


def test_settings_backups_and_network():
    my = checks.judge(my_g(vars={**my_g()["vars"], "slow_query_log": 0, "log_bin": 0, "bind_address": "0.0.0.0"},
                           status={**my_g()["status"], "Max_used_connections": "140"}), "mariadb", MY_DB)
    assert {"slow_log_off", "no_binlog", "bind_all", "max_conn", "backup_unknown"} <= set(ids(my))
    pg = checks.judge(pg_g(vars={**pg_g()["vars"], "autovacuum": ("off", None), "listen_addresses": ("*", None),
                                 "archive_mode": ("off", None), "password_encryption": ("md5", None)}), "postgresql", PG_DB)
    assert by_id(pg, "autovacuum_off")["severity"] == "critical"
    assert {"listen_all", "no_archive", "md5"} <= set(ids(pg))


def test_accounts():
    acc = [["root", "localhost", 0], ["root", "%", 0], ["", "localhost", 1], ["app", "10.0.0.%", 1]]
    out = checks.judge(my_g(accounts=acc), "mariadb", MY_DB)
    sec = ids(out, "security")
    assert {"remote_root_%", "anon_localhost", "nopass_app_10.0.0.%"} <= set(sec)
    assert all(by_id(out, i)["severity"] == "critical" for i in ("remote_root_%", "anon_localhost", "nopass_app_10.0.0.%"))
    hidden = checks.judge(my_g(accounts={"_error": "OperationalError: (1142, 'SELECT command denied')"}), "mariadb", MY_DB)
    assert "accounts_unreadable" in ids(hidden) and by_id(hidden, "accounts_unreadable")["severity"] == "info"
    pg = checks.judge(pg_g(accounts=[["postgres", True], ["admin", True], ["app", False]]), "postgresql", PG_DB)
    assert "supers" in ids(pg)


def test_end_of_support_versions():
    assert report._support("mariadb", "10.5.21-MariaDB")["severity"] == "warning"
    assert report._support("mariadb", "10.11.14-MariaDB") is None
    assert report._support("postgresql", "13.9") and report._support("postgresql", "16.15") is None
    assert report._support("mysql", "8.0.36") and report._support("mysql", "8.4.2") is None


# ---------------------------------------------------------------- slow queries: honest about what is hidden
SLOW = [["SELECT * FROM `t` WHERE YEAR(`d`) = ?", 40, 9000.0, 225.0, 4_000_000, 40, 40],
        ["SELECT `id` FROM `t` WHERE `code` LIKE ?", 10, 3000.0, 300.0, 1_000_000, 10, 10],
        ["SELECT COUNT(*) FROM `mysql`.`slow_log`", 50, 8000.0, 160.0, 0, 50, 0],
        ["INSERT INTO `t` VALUES (...)", 900, 90000.0, 100.0, 0, 0, 0],
        ["SELECT `id` FROM `t` WHERE `id` = ?", 99999, 500.0, 0.005, 99999, 99999, 0]]


def test_slow_queries_judged_by_reasons_not_by_scans():
    checks_ = {SLOW[0][0]: {"findings": [{"severity": 3, "topic": "sargable", "title_en": "YEAR() on a column in WHERE blocks the index",
                                          "fix_en": "Compare the raw column with a range."}], "plan": None,
                            "index_sql": "ALTER TABLE t ADD INDEX ix (d);"},
               SLOW[1][0]: {"findings": [], "plan": None, "index_sql": "ALTER TABLE t ADD INDEX ix (code);"}}
    out = checks.judge(my_g(slow=SLOW), "mariadb", MY_DB, slow_checks=checks_)
    s = out["slow"]
    assert [i["id"] for i in s] == ["slow_0", "slow_1"]          # system catalog, INSERT and the fast one are left out
    assert s[0]["severity"] == "warning" and "blocks the index" in s[0]["why"] and s[0]["fix"] is None
    assert "40 of the runs used no index" in s[0]["what"] and "worth testing" in s[0]["what"]
    assert s[1]["severity"] == "info" and s[1]["fix"] is None and "no index can help" in s[1]["what"]   # LIKE ? may be '%x'


def test_slow_queries_blind_unreadable_or_none():
    blind = checks.judge(my_g(slow=None), "mariadb", MY_DB)
    assert by_id(blind, "slow_blind")["what"] == "performance_schema is off on this server."
    pgb = checks.judge(pg_g(slow=None), "postgresql", PG_DB)
    assert "pg_stat_statements" in by_id(pgb, "slow_blind")["why"]
    denied = checks.judge(my_g(slow={"_error": "denied"}), "mariadb", MY_DB)
    assert "slow_unreadable" in ids(denied)
    quiet = checks.judge(my_g(slow=[SLOW[4]]), "mariadb", MY_DB)
    assert ids(quiet, "slow") == ["slow_none"]


def test_tidy_mariadb_statement_text():
    t = "SELECT `h` . `name` , COUNT ( * ) FROM `p` JOIN `h` ON `h` . `id` = `p` . `hub_id` WHERE `id` IN ( SELECT `x` FROM `u` )"
    assert checks.tidy(t) == "SELECT `h`.`name`, COUNT(*) FROM `p` JOIN `h` ON `h`.`id` = `p`.`hub_id` WHERE `id` IN (SELECT `x` FROM `u`)"


def test_postgres_date_functions_block_the_index_whatever_the_value():
    """Found by S5 on pg_stat_statements text: EXTRACT / date_trunc / date_part around a column were missed when compared
    with a placeholder ($2), which is how every PostgreSQL driver sends values."""
    from app.agents.sql import analyze_sql

    for q in ("SELECT * FROM t WHERE EXTRACT(YEAR FROM d) = $2", "SELECT * FROM t WHERE EXTRACT(YEAR FROM d) = 2025",
              "SELECT * FROM t WHERE date_trunc('month', d) = $1", "SELECT * FROM t WHERE date_part('year', d) = ?"):
        titles = [f["title_en"] for f in analyze_sql(q, engine="postgresql")["findings"]]
        assert sum("on a column in WHERE blocks the index" in t for t in titles) == 1, (q, titles)
    titles = [f["title_en"] for f in analyze_sql("SELECT EXTRACT(YEAR FROM d) FROM t WHERE id = 3", engine="postgresql")["findings"]]
    assert not any("blocks the index" in t for t in titles)          # in SELECT it costs nothing


def test_compare_reports():
    a = {"at": "1", "score": 80, "sections": [{"items": [{"id": "x", "title": "X", "severity": "warning"},
                                                        {"id": "i", "title": "I", "severity": "info"}]}]}
    b = {"at": "2", "score": 90, "sections": [{"items": [{"id": "y", "title": "Y", "severity": "critical"}]}]}
    assert report.compare(b, None) == {"first": True}
    c = report.compare(b, a)
    assert c["new"] == ["Y"] and c["gone"] == ["X"] and c["score_before"] == 80


# ---------------------------------------------------------------- the lab
@pytest.fixture(scope="module")
def lab():
    from tests import s5lab
    from tests.fuzz.db import MYSQL, PG, Engine

    engines = {}
    for cfg in (MYSQL, PG):
        try:
            e = Engine(cfg).open()
        except Exception:  # noqa: BLE001
            continue
        s5lab.build(e)
        engines[cfg["type"]] = e
    yield engines
    for e in engines.values():
        s5lab.drop(e)


def _scan(db):
    return report.scan(db, {"slow_ms": 0.5, "slow_total_ms": 1e12, "slow_top": 40})   # the lab's statistics keep other tests' queries


def _all(rep):
    return {i["id"]: i for s in rep["sections"] for i in s["items"]}


@needs_maria
def test_lab_mariadb(lab):
    rep = _scan(MY_DB)
    items = _all(rep)
    assert "dup_s5_parcels_ix_hub" in items and "dup_s5_parcels_ix_hub_copy" in items
    assert not any(k.startswith("fkidx_") for k in items)              # InnoDB indexes its foreign keys itself
    pss = lab["mysql"].run("SELECT @@performance_schema")[0][0]
    if pss:
        slow = [i for i in items.values() if i["id"].startswith("slow_") and "s5_parcels" in i["title"]]
        year = [i for i in slow if "YEAR" in i["title"]]
        assert year and year[0]["severity"] == "warning" and "blocks the index" in year[0]["why"]
        assert not any("doesn't mention" in i["why"] for i in slow)    # the spaced statistics text gave a false alarm before tidy()
        assert not any("mysql" in i["title"].lower() for i in slow)
    else:
        assert "slow_blind" in items


@needs_pg
def test_lab_postgres(lab):
    rep = _scan(PG_DB)
    items = _all(rep)
    assert "dup_s5_parcels_ix_hub" in items and "fkidx_s5_parcels_fk_s5_hub" in items and "dead_s5_scans" in items, sorted(items)
    has = lab["postgres"].run("SELECT count(*) FROM pg_extension WHERE extname = 'pg_stat_statements'")[0][0]
    if has:
        ext = [i for i in items.values() if i["id"].startswith("slow_") and "EXTRACT" in i["title"]]
        assert ext and ext[0]["severity"] == "warning" and "EXTRACT() on a column" in ext[0]["why"]
        assert not any("pg_class" in i["title"] for i in items.values() if i["id"].startswith("slow_"))
    else:
        assert "slow_blind" in items


def _hold_lock(engine_cfg, sql_hold, sql_wait, settle=1.5):
    """A: take a row lock and keep it; B: wait for it. Returns (A, B, thread) still running."""
    from tests.fuzz.db import Engine

    a, b = Engine(engine_cfg).open(), Engine(engine_cfg).open()
    a.begin()
    a.run(sql_hold)

    def wait():
        try:
            b.begin()
            b.run(sql_wait)
            b.rollback()
        except Exception:  # noqa: BLE001
            b.rollback()
    th = threading.Thread(target=wait, daemon=True)
    th.start()
    time.sleep(settle)
    return a, b, th


@pytest.mark.parametrize("db,kind", [pytest.param(MY_DB, "mysql", marks=needs_maria), pytest.param(PG_DB, "postgres", marks=needs_pg)],
                         ids=["mariadb", "postgresql"])
def test_lab_lock_wait_names_who_blocks_whom(lab, db, kind):
    from tests.fuzz.db import MYSQL, PG

    a, b, th = _hold_lock(MYSQL if kind == "mysql" else PG, "UPDATE s5_hubs SET name = 'held' WHERE id = 1",
                          "UPDATE s5_hubs SET name = 'waiting' WHERE id = 1")
    try:
        from app.products.dbaba.local import dbconn

        conn = dbconn.open_db(db)
        try:
            g = checks.gather(conn, db["type"])
        finally:
            dbconn.close(conn)
        waits = [i for i in checks.judge(g, db["type"], db)["now"] if i["id"].startswith("lockwait_")]
        assert waits, g.get("lock_waits")
        w = waits[0]
        # data values never appear in a report (name = ?): the statements and both connections do
        # MariaDB shows no statement for a holder whose statement has finished (lab: trx_query is NULL); PostgreSQL shows its last
        holder = "(an open transaction, idle)" if kind == "mysql" else "Holding the lock: UPDATE s5_hubs SET name = ? WHERE id = ?"
        assert "Waiting: UPDATE s5_hubs SET name = ? WHERE id = ?" in w["what"] and holder in w["what"] and "'held'" not in w["what"]
        assert w["title"].startswith("Connection ") and "lock held by" in w["title"]
        assert waits[0]["fix"]["needs_approval"] is True
    finally:
        a.rollback()
        th.join(timeout=30)


@pytest.mark.parametrize("db,kind", [pytest.param(MY_DB, "mysql", marks=needs_maria), pytest.param(PG_DB, "postgres", marks=needs_pg)],
                         ids=["mariadb", "postgresql"])
def test_lab_deadlock_is_counted(lab, db, kind):
    from tests.fuzz.db import MYSQL, PG, Engine

    cfg = MYSQL if kind == "mysql" else PG
    a, b = Engine(cfg).open(), Engine(cfg).open()
    a.begin()
    b.begin()
    a.run("UPDATE s5_hubs SET name = 'a' WHERE id = 1")
    b.run("UPDATE s5_hubs SET name = 'b' WHERE id = 2")
    errors = []

    def second(e, sql):
        try:
            e.run(sql)
        except Exception as exc:  # noqa: BLE001 - one of the two is chosen as the victim
            errors.append(str(exc))
        finally:
            e.rollback()
    t1 = threading.Thread(target=second, args=(a, "UPDATE s5_hubs SET name = 'a' WHERE id = 2"))
    t2 = threading.Thread(target=second, args=(b, "UPDATE s5_hubs SET name = 'b' WHERE id = 1"))
    t1.start()
    time.sleep(0.5)
    t2.start()
    t1.join(30)
    t2.join(30)
    assert any("deadlock" in e.lower() for e in errors), errors
    items = _all(_scan(db))
    assert "deadlocks" in items and items["deadlocks"]["severity"] == "warning"


@needs_maria
def test_report_through_the_api_has_every_section_and_compares(tmp_path, lab):
    from starlette.testclient import TestClient

    from app.products.dbaba.local.server import make_app

    app = make_app(str(tmp_path / "dbaba.json"), 8765, "T", announce=lambda line: None)
    c = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1))
    key = c.post("/api/setup/save", headers={"X-DBaba-Setup": "T"},
                 json={"engine": "mariadb", "host": "127.0.0.1", "port": 3306, "database": "shop_db", "user": "shopapp",
                       "password": "app pass", "screen_password": "screen pass 5"}).json()["api_key"]
    K = {"Authorization": "Bearer " + key}
    for _ in range(120):
        r = c.get("/api/report", headers=K).json()
        if r["at"] and not r["pending"]:
            break
        time.sleep(0.5)
    secs = {s["id"] for s in r["sections"]}
    assert {"slow", "indexes", "backups", "security", "server"} <= secs and r["changes"] == {"first": True}
    app.state.jobs.scan()
    r2 = c.get("/api/report", headers=K).json()
    assert r2["changes"]["first"] is False and r2["changes"]["score_before"] == r["score"]
