"""E1 (SQLite), night 4: SQLite's transaction and locking questions (BEGIN / COMMIT / ROLLBACK, BEGIN IMMEDIATE, busy_timeout,
'database is locked' despite a timeout, one writer at a time, savepoints, WAL checkpoints, journal modes, BEGIN EXCLUSIVE, its
isolation, fast bulk inserts, commit in Python, 'database table is locked', nested transactions, a statement going wrong
inside a transaction, lost updates, a job queue) plus the general SAVEPOINT lesson, through the real chat endpoint, in
English, Hinglish and Hindi. Every example was run on SQLite (tests/practice/concepts/lab.py keeps the result; the general one
on MariaDB and PostgreSQL). Real work still goes where it went: a pasted transaction script to the checker, a pasted
transaction error to the diagnoser."""

import json

import pytest

from app.dbknow import concepts as K
from tests.practice.concepts.test_e1_sqlite1 import WRONG_PLACES, fresh
from tests.test_ml import say, say_sql

E1N4 = ["sqlite_transaction", "sqlite_begin_immediate", "sqlite_busy_timeout", "sqlite_busy_snapshot", "sqlite_one_writer",
        "sqlite_savepoint", "sqlite_wal_checkpoint", "sqlite_journal_modes", "sqlite_exclusive", "sqlite_isolation", "sqlite_bulk_insert",
        "sqlite_python_commit", "sqlite_table_locked", "sqlite_nested_transactions", "sqlite_statement_in_transaction",
        "sqlite_lost_update", "sqlite_job_queue"]
GENERAL = ["savepoint"]


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/products/dbaba/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(E1N4 + GENERAL) <= ids and len(E1N4) + len(GENERAL) >= 15
    for cid in E1N4 + GENERAL:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert c["examples"] and all(e.get("result") is not None for e in c["examples"]), cid
        assert set(c["engines"]) >= {"sqlite", "mysql", "mariadb", "postgresql", "sqlserver", "oracle"}, cid
        assert c["engines"]["sqlite"].startswith("Lab (SQLite"), cid
        assert all(c["engines"][k].startswith("Notes only") for k in ("sqlserver", "oracle")), cid   # rule A
        assert set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid
        assert {"sqlite", "mariadb", "postgresql"} <= set(c["proven"]), cid
    for cid in E1N4:
        c = K.by_id(cid)
        assert c["engine"] == "sqlite" and all(set(e["sql"]) == {"sqlite"} for e in c["examples"]), cid
        assert c["source"].startswith("https://www.sqlite.org/"), cid
    assert set(K.by_id("savepoint")["examples"][0]["sql"]) == {"mysql", "postgresql"}
    assert "transactions" in funds and set(funds["transactions"]["proven"]) == {"mariadb", "postgresql", "sqlite"}


def test_the_examples_give_their_stored_results_on_sqlite():
    from tests.practice.concepts import lab

    assert lab.check_sqlite(json.loads(K.PACK.read_text(encoding="utf-8")), write=False) == []


@pytest.mark.parametrize("cid", E1N4 + GENERAL)
def test_every_phrasing_of_tonights_concepts(client, ml_mode, cid):
    c = K.by_id(cid)
    titles = set(c["title"].values())
    for lang, phr in c["phrasings"].items():
        for text in phr:
            fresh(client)
            r = say(client, text)
            assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in titles and not WRONG_PLACES.search(r["reply"]), \
                (lang, text, r["relation"], r["reply"][:200])


@pytest.mark.parametrize("text,cid", [
    ("what is a transaction", "transaction"),                          # not naming SQLite: the general lesson
    ("what is autocommit", "autocommit"),
    ("what are isolation levels", "isolation_levels"),
    ("what is wal mode in sqlite", "sqlite_wal"),                      # earlier nights keep their answers
    ("what does database is locked mean in sqlite", "sqlite_database_locked"),
    ("what is skip locked", "skip_locked_nowait"),
    ("how to do begin immediate in sqlite", "sqlite_begin_immediate"),   # tonight's, other words
    ("sqlite mein savepoint kaise use kare", "sqlite_savepoint"),
    ("how to insert lakhs of rows fast in sqlite", "sqlite_bulk_insert"),
    ("what is the isolation level of sqlite", "sqlite_isolation"),
    ("does sqlite support select for update", "sqlite_job_queue"),
    ("what is rollback to savepoint", "savepoint"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


def test_a_sqlite_concept_answers_without_a_notes_only_label(client, ml_mode):
    """SQLite is lab-proven from tonight: its concepts carry their lab note, never 'notes only'."""
    fresh(client)
    r = say(client, "what is begin immediate in sqlite")
    assert "Lab (SQLite 3.53)" in r["reply"] and "not lab-tested on SQLite" not in r["reply"], r["reply"][-500:]


def test_real_work_still_goes_to_the_checker_and_the_diagnoser(client, ml_mode):
    """A pasted transaction script is checked; a pasted transaction error is diagnosed; neither becomes a lesson."""
    fresh(client)
    r = say_sql(client, "this is for sqlite:\nBEGIN;\nSELECT stock FROM products WHERE id = 3;\nUPDATE products SET stock = stock - 1 WHERE id = 3;\nCOMMIT;")
    assert r["relation"] != "concept" and "BEGIN IMMEDIATE" in json.dumps(r), r["reply"][:400]
    fresh(client)
    r = say(client, "sqlite3.OperationalError: cannot start a transaction within a transaction")
    assert r["relation"] != "concept" and "SAVEPOINT" in r["reply"] and "Fix (SQLite)" in r["reply"], r["reply"][:400]
    fresh(client)
    r = say(client, "ERROR:  current transaction is aborted, commands ignored until end of transaction block")
    assert r["relation"] != "concept" and "ROLLBACK" in r["reply"], r["reply"][:400]
    fresh(client)
    r = say(client, "my sqlite app is slow when many users save at once")
    assert r["relation"] != "concept", r["reply"][:300]
