"""E1 (SQLite), night 2: SQLite's types-and-tables questions (INT vs INTEGER PRIMARY KEY, AUTOINCREMENT, NULL in a primary key,
VARCHAR(n) not enforced, how affinity is picked, booleans, money, keeping dates in one shape, text compared with numbers, ADD
COLUMN and DROP COLUMN limits, rebuilding a table, SET NOT NULL, WITHOUT ROWID, foreign keys and indexes, a created_at default,
ENUM) through the real chat endpoint, in English, Hinglish and Hindi. Every example was run on SQLite (tests/practice/concepts/
lab.py keeps the result). Real work still goes where it went: a pasted SQLite error to the diagnoser, a table script for SQLite
to the checker."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.practice.concepts.test_e1_sqlite1 import WRONG_PLACES, fresh
from tests.test_ml import say, say_sql

E1N2 = ["sqlite_int_primary_key", "sqlite_autoincrement", "sqlite_null_primary_key", "sqlite_varchar_length", "sqlite_affinity_rules",
        "sqlite_boolean", "sqlite_money", "sqlite_date_check", "sqlite_text_number_compare", "sqlite_add_column", "sqlite_drop_column",
        "sqlite_rebuild_table", "sqlite_set_not_null", "sqlite_without_rowid", "sqlite_fk_index", "sqlite_default_timestamp", "sqlite_enum"]


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/products/dbaba/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(E1N2) <= ids and len(E1N2) >= 15
    for cid in E1N2:
        c = K.by_id(cid)
        assert c["engine"] == "sqlite" and "sqlite" in c["proven"], cid
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert c["examples"] and all(set(e["sql"]) == {"sqlite"} and e.get("result") is not None for e in c["examples"]), cid
        assert c["source"].startswith("https://www.sqlite.org/"), cid
        assert set(c["engines"]) >= {"sqlite", "mysql", "mariadb", "postgresql"}, cid
        assert c["engines"]["sqlite"].startswith("Lab (SQLite"), cid
        assert all(c["engines"][k].startswith("Notes only") for k in ("sqlserver", "oracle")), cid   # rule A
        assert set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid


def test_the_examples_give_their_stored_results_on_sqlite():
    from tests.practice.concepts import lab

    assert lab.check_sqlite(json.loads(K.PACK.read_text(encoding="utf-8")), write=False) == []


@pytest.mark.parametrize("cid", E1N2)
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
    ("what is rowid in sqlite", "sqlite_rowid"),                     # last night's answers keep their words
    ("how to make an auto increment id in sqlite", "sqlite_rowid"),
    ("what is type affinity in sqlite", "sqlite_type_affinity"),
    ("what is a strict table in sqlite", "sqlite_strict"),
    ("how to change a column type in sqlite", "sqlite_alter_table"),
    ("how to enable foreign keys in sqlite", "sqlite_foreign_keys"),
    ("what is a primary key", "primary_key"),
    ("what is a check constraint", "check_constraint"),
    ("what is an index", "index"),                                   # a real user's question from the learning feed
    ("is varchar 255 a limit in sqlite", "sqlite_varchar_length"),    # tonight's, other words
    ("sqlite mein boolean hota hai kya", "sqlite_boolean"),
    ("how to store price in sqlite", "sqlite_money"),
    ("how does sqlite store a created_at default", "sqlite_default_timestamp"),
    ("sqlite mein enum hota hai kya", "sqlite_enum"),
    ("how to rebuild a sqlite table to change a column", "sqlite_rebuild_table"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


def test_a_general_how_does_question_is_a_lesson():
    """'how does X decide / store / pick ...' is a question shape (rule D), never a fixed sentence."""
    for text in ["how does sqlite decide a column's affinity", "how is a date stored in sqlite", "how does mysql pick an index"]:
        assert K.shape(text) == "define", text


@pytest.mark.parametrize("text", [
    'sqlite3.OperationalError: unknown datatype for items.code: "VARCHAR(20)"',
    "Error: in prepare, AUTOINCREMENT is only allowed on an INTEGER PRIMARY KEY",
    "sqlite3.OperationalError: cannot drop UNIQUE column: \"email\"",
    "my sqlite id column is always null after insert",
])
def test_real_errors_still_go_to_the_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


def test_a_strict_type_error_gets_sqlites_fix_in_the_chat(client, ml_mode):
    fresh(client)
    r = say(client, 'sqlite3.OperationalError: unknown datatype for items.code: "VARCHAR(20)"')
    assert "Fix (SQLite)" in r["reply"] and "STRICT" in r["reply"], r["reply"][:700]


def test_a_table_script_for_sqlite_goes_to_the_checker(client, ml_mode):
    fresh(client)
    r = say_sql(client, "CREATE TABLE users (id INT PRIMARY KEY, name TEXT, joined DATE)", "SQLite")
    res = r["agents"][-1]["runs"][-1]["result"]
    topics = {f["topic"] for f in res["correct"]["findings"]}
    assert "sqlite_rowid_pk" in topics and "id INTEGER PRIMARY KEY" in res["correct"]["corrected"], (topics, res["correct"].get("corrected"))
    assert not WRONG_PLACES.search(r["reply"]), r["reply"][:300]
