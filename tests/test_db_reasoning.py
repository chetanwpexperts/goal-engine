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
