"""The database agent reasons from the 5 layers, the same way for every database: real errors from the real test
databases, thousands of generated ones in every format, checks that prove the cause on a connected database, and the
chat end to end."""

import re
import socket

import pytest

from app.dbknow import reasoning as R
from tests.fuzz import db_errors as gen
from tests.test_ml import say


def _up(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


both_dbs = pytest.mark.skipif(not (_up(3306) and _up(5432)), reason="needs the local MariaDB and PostgreSQL test databases")


@pytest.mark.parametrize("seed", [3, 19])
def test_every_layer_every_database_in_every_format(seed):
    ok, errs = gen.run(1500, seed)
    assert not errs, "\n".join(errs[:10])


@both_dbs
def test_real_errors_from_real_databases():
    from tests.dbreal.real_errors import collect

    for msg, want, engine in collect():
        res = R.diagnose(msg)
        assert res["found"] and res["found"][0]["cause"] == want, (msg, [x["cause"] for x in res["found"]])
        assert res["engine"] in ({engine, "mariadb"} if engine == "mysql" else {engine}), (msg, res["engine"])


@both_dbs
def test_connected_checks_prove_the_cause():
    from tests.dbreal import real_errors as RE

    def runner(conn):
        def run(sql, params):
            with conn.cursor() as c:
                c.execute(sql, params or None)
                return c.fetchall()
        return run

    my, pg = RE.mariadb(), RE.postgres()
    RE.setup_pg(pg)
    RE.my_error(my, "INSERT INTO wp_options (option_name) VALUES ('provecheck')")
    err = RE.my_error(my, "INSERT INTO wp_options (option_name) VALUES ('provecheck')")
    res = R.diagnose(err)
    live = R.run_live(R.live_plan(res), runner(my))
    assert live["duplicate_key"]["seen"] and "wp_options" in live["duplicate_key"]["live_proof"]
    rep = R.report(R.diagnose(err, live), False)
    assert "Checked on your database" in rep and "already in wp_options" in rep

    err = RE.pg_error(pg, "INSERT INTO t_child VALUES (2,99)")
    live = R.run_live(R.live_plan(R.diagnose(err)), runner(pg))
    assert live["fk_missing_parent"]["seen"] and "t_parent has no row with id = 99" in live["fk_missing_parent"]["live_proof"]

    err = RE.pg_error(pg, "SELECT * FROM orderz")
    live = R.run_live(R.live_plan(R.diagnose(err)), runner(pg))
    assert "closest real names" in live["unknown_table"]["live_proof"]


def test_live_checks_never_run_pasted_text_as_sql():
    with pytest.raises(ValueError):
        R._sql("SELECT * FROM `{table}`", {"table": "x`; DROP TABLE users; --"})
    sql, params = R._sql("SELECT 1 FROM `{table}` WHERE `{column}` = :value", {"table": "users", "column": "email", "value": "a'; drop"})
    assert params == {"value": "a'; drop"} and "drop" not in sql.lower()


def test_without_an_error_it_never_claims_a_cause():
    res = R.diagnose("database se connect nahi ho raha app ka")
    assert not res["found"]
    rep = R.report(res, False)
    assert "Wrong user or password" in rep and "paste the full error" in rep


def test_unknown_database_gets_both_fixes():
    rep = R.report(R.diagnose("kabhi kabhi too many connections aata hai"), False)
    assert "Fix (MySQL/MariaDB)" in rep and "Fix (PostgreSQL)" in rep


def test_hinglish_reply_and_labels(client, ml_mode):
    r = say(client, "order save karte waqt ye aa raha hai: ERROR 1062 (23000): Duplicate entry 'ravi@example.in' for key 'users.users_email_unique'")
    assert "pehle se ek unique field" in r["reply"] and "paste kiye text pe based" in r["reply"]
    r = say(client, "ERROR:  deadlock detected\nDETAIL:  Process 1234 waits for ShareLock on transaction 5678; blocked by process 4321.")
    assert "deadlock" in r["reply"].lower() and "Fix (PostgreSQL)" in r["reply"] and "Prevent" in r["reply"]


def test_a_new_database_is_one_dialect_file():
    src = open(R.__file__, encoding="utf-8").read()
    for word in ("mysql", "postgres", "mariadb"):
        assert not re.search(rf"\b{word}\b.*(?:regex|re\.compile)", src, re.I)
    assert set(R.engines()) >= {"mysql", "mariadb", "postgresql"}


def test_lowercase_code_and_title_follows_the_new_error(client, ml_mode):
    """'error 1062' typed by hand is a real code, and a second, different error in the same chat retitles the goal."""
    from tests.test_ml import say
    first = say(client, "I get error 1062 duplicate entry when saving")
    assert "unique" in first["reply"].lower() and "that value that value" not in first["reply"].lower()
    assert "1062" not in first["goal"]["title"]
    second = say(client, "ERROR 1040 (HY000): Too many connections")
    assert "connections" in second["goal"]["title"].lower() and "duplicate" not in second["goal"]["title"].lower()
    assert "The value that value" not in second["reply"] + first["reply"]


# Replication: messages captured on lab replicas (a MariaDB 10.11 source + replica, a PostgreSQL 16 primary with a hot standby and
# a logical subscriber), each produced by doing the real thing: writing on the replica, skipping an error, purging logs the replica
# still needed, VACUUM / TRUNCATE on the primary under a long standby query, inserting on the standby.
REPLICATION_REAL = [
    ("             Slave_SQL_Running: No\n         Seconds_Behind_Master: NULL\n                Last_SQL_Errno: 1062\n"
     "                Last_SQL_Error: Error 'Duplicate entry '2' for key 'PRIMARY'' on query. Default database: ''. "
     "Query: 'INSERT INTO app.users VALUES (2,'c@d.com')'", "replication_stopped", "mysql"),
    ("Last_SQL_Error: Could not execute Update_rows_v1 event on table app.users; Can't find record in 'users', Error_code: 1032; "
     "handler error HA_ERR_KEY_NOT_FOUND; the event's master log bin.000003, end_log_pos 1124", "replication_stopped", "mysql"),
    ("Last_IO_Errno: 1236\nLast_IO_Error: Got fatal error 1236 from master when reading data from binary log: "
     "'Could not find first log file name in binary log index file'", "source_log_purged", "mysql"),
    ("ERROR 1290: The MariaDB server is running with the --read-only option so it cannot execute this statement", "write_on_replica", "mariadb"),
    ("FATAL:  could not receive data from WAL stream: ERROR:  requested WAL segment 00000001000000000000000F has already been removed",
     "source_log_purged", "postgresql"),
    ("ERROR:  canceling statement due to conflict with recovery\nDETAIL:  User query might have needed to see row versions that must be removed.",
     "standby_query_conflict", "postgresql"),
    ("ERROR:  canceling statement due to conflict with recovery\nDETAIL:  User was holding a relation lock for too long.\nSQLSTATE: 40001",
     "standby_query_conflict", "postgresql"),
    ("ERROR:  duplicate key value violates unique constraint \"users_pkey\"\nDETAIL:  Key (id)=(2) already exists.\n"
     "CONTEXT:  processing remote data for replication origin \"pg_16393\" during message type \"INSERT\" for replication target relation "
     "\"public.users\" in transaction 731, finished at 0/302B020", "replication_stopped", "postgresql"),
    ("ERROR:  cannot execute INSERT in a read-only transaction", "write_on_replica", "postgresql"),
]


@pytest.mark.parametrize("msg,want,engine", REPLICATION_REAL)
def test_replication_errors_from_lab_replicas(msg, want, engine):
    res = R.diagnose(msg)
    assert [x["cause"] for x in res["found"]][:1] == [want], (msg, [x["cause"] for x in res["found"]])
    assert res["engine"] == engine
    assert len(res["found"]) == 1, "the replica's own duplicate / the shared SQLSTATE 40001 must not be claimed as a second cause"


def test_replication_fixes_name_the_popular_wrong_fix():
    rep = R.report(R.diagnose(REPLICATION_REAL[0][0]), False)
    assert "Common mistake" in rep and "sql_replica_skip_counter" in rep and "drift" in rep
    rep = R.report(R.diagnose(REPLICATION_REAL[2][0]), False)
    assert "fresh" in rep and "backup" in rep and "silently missing" in rep
    rep = R.report(R.diagnose(REPLICATION_REAL[5][0]), True)
    assert "hot_standby_feedback" in rep and "max_standby_streaming_delay" in rep and "Kyun" in rep


@pytest.mark.parametrize("text,likely", [
    ("hamara replica 2 ghante peeche chal raha hai, data purana dikhta hai", "replica_lag"),
    ("read replica shows old data right after saving", "replica_lag"),
    ("slave pe replication ruk gayi", "replication_stopped"),
    ("standby pe report query beech mein cancel ho jaati hai", "standby_query_conflict"),
])
def test_replication_described_without_an_error_is_never_claimed(text, likely):
    res = R.diagnose(text)
    assert not res["found"]
    assert likely in [x["cause"] for x in res["open"][:4]], [x["cause"] for x in res["open"][:6]]
    assert "paste the full error" in R.report(res, False)


def test_other_read_only_options_are_not_a_replica():
    """1290 is the code for any option that blocks a statement; only the read-only ones mean a replica."""
    res = R.diagnose("ERROR 1290 (HY000): The MySQL server is running with the --secure-file-priv option so it cannot execute this statement")
    assert "write_on_replica" not in [x["cause"] for x in res["found"]]


@both_dbs
def test_connected_check_proves_a_read_only_server():
    from tests.dbreal import real_errors as RE

    def runner(conn):
        def run(sql, params):
            with conn.cursor() as c:
                c.execute(sql, params or None)
                return c.fetchall()
        return run

    pg = RE.postgres()
    RE.setup_pg(pg)
    err = RE.pg_error(pg, "BEGIN READ ONLY; INSERT INTO t_parent VALUES (7,'r@o.com')")
    plan = R.live_plan(R.diagnose(err))
    assert not R.run_live(plan, runner(pg))["write_on_replica"]["seen"]      # this server accepts writes: ruled out
    pg.execute("SET default_transaction_read_only = on")
    try:
        live = R.run_live(plan, runner(pg))
    finally:
        pg.execute("RESET default_transaction_read_only")
    assert live["write_on_replica"]["seen"] and "only accepts reads" in live["write_on_replica"]["live_proof"]
    my = RE.mariadb()
    my_plan = R.live_plan(R.diagnose(REPLICATION_REAL[3][0]))
    assert not R.run_live(my_plan, runner(my))["write_on_replica"]["seen"]


# WAL / binary log / disk: messages captured on a lab PostgreSQL 16 (archive_command failing, max_wal_size 32MB) and checks
# proven on lab servers (an inactive replication slot holding WAL, a MariaDB with binary logging on and no expiry).
WAL_REAL = [
    ("2026-11-02 04:06:31.016 UTC [225] LOG:  archive command failed with exit code 1\n"
     "2026-11-02 04:06:31.016 UTC [225] DETAIL:  The failed archive command was: false", "wal_archive_failing"),
    ('2026-11-02 04:07:35.086 UTC [225] WARNING:  archiving write-ahead log file "00000001000000000000001B" failed too many times, '
     "will try again later", "wal_archive_failing"),
    ("2026-11-01 20:09:57.752 UTC [3077] LOG:  checkpoints are occurring too frequently (0 seconds apart)\n"
     '2026-11-01 20:09:57.752 UTC [3077] HINT:  Consider increasing the configuration parameter "max_wal_size".', "checkpoints_too_frequent"),
]


@pytest.mark.parametrize("msg,want", WAL_REAL)
def test_wal_messages_from_a_lab_server(msg, want):
    res = R.diagnose(msg)
    assert [x["cause"] for x in res["found"]] == [want] and res["engine"] == "postgresql"


def test_wal_and_binlog_fixes_name_the_popular_wrong_fix():
    rep = R.report(R.diagnose('ERROR:  could not extend file "base/16384/2619": No space left on device'), False)
    assert "pg_resetwal" in rep and "Common mistake" in rep
    rep = R.report(R.diagnose(WAL_REAL[0][0]), False)
    assert "/bin/true" in rep and "pg_stat_archiver" in rep
    rep = R.report(R.diagnose(WAL_REAL[2][0]), True)
    assert "lowering max_wal_size" in rep and "Kyun" in rep


@pytest.mark.parametrize("text,likely,engine", [
    ("pg_wal folder bahut bada ho gaya hai, disk bhar gayi", "wal_retained_by_slot", "postgresql"),
    ("mysql binlog files bahut jagah le rahi hain", "binlog_not_expiring", "mysql"),
])
def test_growing_logs_described_without_an_error(text, likely, engine):
    res = R.diagnose(text)
    assert not res["found"] and res["engine"] == engine
    assert likely in [x["cause"] for x in res["open"][:4]], [x["cause"] for x in res["open"][:6]]


def test_a_cause_of_one_database_is_never_likely_on_another():
    for text, wrong in (("mysql binlog files bahut jagah le rahi hain", "wal_retained_by_slot"),
                        ("pg_wal folder bahut bada ho gaya hai", "binlog_not_expiring")):
        assert wrong not in [x["cause"] for x in R.diagnose(text)["open"]]


@both_dbs
def test_connected_check_finds_why_the_disk_is_full():
    """A full disk: the check looks deeper (slots, archiving, binary logs) and proves the slot that holds the WAL."""
    from tests.dbreal import real_errors as RE

    def runner(conn):
        def run(sql, params):
            with conn.cursor() as c:
                c.execute(sql, params or None)
                return c.fetchall()
        return run

    pg = RE.postgres()
    err = 'ERROR:  could not extend file "base/16384/2619": No space left on device'
    plan = R.live_plan(R.diagnose(err))
    assert {p["cause"] for p in plan} >= {"wal_retained_by_slot", "wal_archive_failing"}
    live = R.run_live(plan, runner(pg))
    assert not live["wal_retained_by_slot"]["seen"] and not live["wal_archive_failing"]["seen"]
    pg.execute("SELECT pg_create_physical_replication_slot('dbaba_test_slot', true)")
    try:
        live = R.run_live(plan, runner(pg))
    finally:
        pg.execute("SELECT pg_drop_replication_slot('dbaba_test_slot')")
    assert live["wal_retained_by_slot"]["seen"] and "dbaba_test_slot" in live["wal_retained_by_slot"]["live_proof"]
    res = R.diagnose(err, live)
    assert {x["cause"] for x in res["found"]} == {"disk_full", "wal_retained_by_slot"}
    assert "dbaba_test_slot" in R.report(res, False)
    my = RE.mariadb()                                     # the test MariaDB runs without binary logging: ruled out, not guessed
    my_plan = R.live_plan(R.diagnose('ERROR 1021 (HY000): Disk full (/tmp/#sql_1f2_0.MAI) MariaDB (errno: 28 "No space left on device")'))
    assert [p["cause"] for p in my_plan] == ["binlog_not_expiring"]
    assert R.run_live(my_plan, runner(my))["binlog_not_expiring"]["seen"] is False
