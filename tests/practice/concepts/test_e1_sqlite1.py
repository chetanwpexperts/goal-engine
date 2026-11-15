"""E1 (SQLite), night 1: SQLite's own concept questions (type affinity, STRICT tables, how dates are stored, date modifiers,
strftime, julianday, the current date, rowid, foreign keys being off, 'database is locked', WAL, ALTER TABLE's limits,
listing tables, upserts, PRAGMA, case in comparisons) through the real chat endpoint, in English, Hinglish and Hindi. Every
example was run on SQLite (tests/practice/concepts/lab.py keeps the result). Then real work still goes where it went: a
pasted SQLite error to the diagnoser, a query for SQLite to the checker."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say, say_sql

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

E1N1 = ["sqlite_type_affinity", "sqlite_strict", "sqlite_dates", "sqlite_date_modifiers", "sqlite_strftime", "sqlite_julianday",
        "sqlite_now", "sqlite_rowid", "sqlite_foreign_keys", "sqlite_database_locked", "sqlite_wal", "sqlite_alter_table",
        "sqlite_list_tables", "sqlite_upsert", "sqlite_pragma", "sqlite_case_compare"]


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(E1N1) <= ids and len(E1N1) >= 15
    for cid in E1N1:
        c = K.by_id(cid)
        assert c["engine"] == "sqlite" and "sqlite" in c["proven"], cid
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert c["examples"] and all(set(e["sql"]) == {"sqlite"} and (e.get("result") is not None or e.get("changed") is not None)
                                     for e in c["examples"]), cid
        assert c["source"].startswith("https://www.sqlite.org/"), cid
        assert set(c["engines"]) >= {"sqlite", "mysql", "mariadb", "postgresql"}, cid
        assert c["engines"]["sqlite"].startswith("Lab (SQLite"), cid
        assert all(c["engines"][k].startswith("Notes only") for k in ("sqlserver", "oracle")), cid   # rule A
        assert set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid


def test_the_examples_give_their_stored_results_on_sqlite():
    from tests.practice.concepts import lab

    assert lab.check_sqlite(json.loads(K.PACK.read_text(encoding="utf-8")), write=False) == []


@pytest.mark.parametrize("cid", E1N1)
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
    ("what is sqlite", "sqlite_intro"),                              # earlier answers keep their words
    ("sqlite vs mysql", "sqlite_intro"),
    ("how to add days to a date in mysql", "add_to_date"),
    ("what is a foreign key", "foreign_key"),
    ("upsert kya hai", "upsert"),
    ("how to list all tables in a mysql database", "list_tables"),
    ("how to add a month to a date in sqlite", "sqlite_date_modifiers"),   # tonight's, other words
    ("does sqlite check foreign keys", "sqlite_foreign_keys"),
    ("what is busy_timeout", "sqlite_database_locked"),
    ("sqlite mein columns kaise dekhe", "sqlite_list_tables"),
    ("what is typeof in sqlite", "sqlite_type_affinity"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


def test_a_sqlite_concept_shows_its_lab_note_not_a_notes_only_label(client, ml_mode):
    fresh(client)
    r = say(client, "what is rowid in sqlite")
    assert "Lab (SQLite 3.53)" in r["reply"] and "not lab-tested on SQLite" not in r["reply"], r["reply"][-500:]
    assert "run on SQLite" in r["reply"], r["reply"][:600]


@pytest.mark.parametrize("text", [
    "sqlite3.OperationalError: database is locked",
    "sqlite3.IntegrityError: UNIQUE constraint failed: users.email",
    "Error: in prepare, no such column: emial",
    "my sqlite app says database is locked when two users save",
])
def test_real_errors_still_go_to_the_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


def test_a_sqlite_error_gets_sqlites_fix_in_the_chat(client, ml_mode):
    fresh(client)
    r = say(client, "sqlite3.OperationalError: database is locked")
    assert "Fix (SQLite)" in r["reply"] and "busy_timeout" in r["reply"], r["reply"][:600]


def test_a_query_for_sqlite_goes_to_the_checker_with_sqlites_words(client, ml_mode):
    fresh(client)
    r = say_sql(client, "SELECT id, DATE_ADD(order_date, INTERVAL 7 DAY) AS due FROM orders WHERE order_date < NOW()", "SQLite")
    res = r["agents"][-1]["runs"][-1]["result"]
    hit = [f for f in res["correct"]["findings"] if f["topic"] == "dialect"]
    assert hit and "date(order_date, '+7 days')" in res["correct"]["corrected"] and "datetime('now')" in res["correct"]["corrected"], \
        (hit, res["correct"].get("corrected"))
    assert not WRONG_PLACES.search(r["reply"]), r["reply"][:300]
