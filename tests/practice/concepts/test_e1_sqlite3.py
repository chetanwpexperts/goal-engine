"""E1 (SQLite), night 3: SQLite's query questions (reading EXPLAIN QUERY PLAN, SCAN vs SEARCH, automatic indexes, TEMP B-TREE,
columns not in GROUP BY, 7 / 2 = 3, joining text, where NULLs sort, UPDATE / DELETE with another table or a LIMIT, double
quotes, ANY / ALL, recursive queries, dividing by zero, a SELECT name in WHERE) through the real chat endpoint, in English,
Hinglish and Hindi. Every example was run on SQLite (tests/practice/concepts/lab.py keeps the result; a plan keeps what its
lines say). Real work still goes where it went: a pasted query to the checker, a pasted plan to the plan reader."""

import json

import pytest

from app.dbknow import concepts as K
from tests.practice.concepts.test_e1_sqlite1 import WRONG_PLACES, fresh
from tests.test_ml import say, say_sql

E1N3 = ["sqlite_explain_query_plan", "sqlite_scan_search", "sqlite_automatic_index", "sqlite_temp_btree", "sqlite_bare_columns",
        "sqlite_integer_division", "sqlite_concat", "sqlite_null_sort", "sqlite_update_join", "sqlite_delete_join", "sqlite_update_limit",
        "sqlite_double_quotes", "sqlite_any_all", "sqlite_recursive_cte", "sqlite_divide_by_zero", "sqlite_alias_in_where"]


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/products/dbaba/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(E1N3) <= ids and len(E1N3) >= 15
    for cid in E1N3:
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


@pytest.mark.parametrize("cid", E1N3)
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
    ("what is rowid in sqlite", "sqlite_rowid"),                     # earlier nights' answers keep their words
    ("is like case sensitive in sqlite", "sqlite_case_compare"),
    ("how to do an upsert in sqlite", "sqlite_upsert"),
    ("what is a recursive cte", "recursive_cte"),                    # not naming SQLite: the general lesson
    ("what is explain", "explain"),
    ("how to read a query plan in sqlite", "sqlite_explain_query_plan"),   # tonight's, other words
    ("sqlite explain mein SEARCH ka matlab kya hai", "sqlite_scan_search"),
    ("how does sqlite sort nulls in order by", "sqlite_null_sort"),
    ("how to join first and last name in sqlite", "sqlite_concat"),
    ("sqlite mein delete with limit kaise kare", "sqlite_update_limit"),
    ("why is 7/2 equal to 3 in sqlite", "sqlite_integer_division"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


def test_how_does_a_database_behave_is_a_lesson():
    """'how does X sort / compare / round / divide ...' is a question shape (rule D), never a fixed sentence."""
    for text in ["how does sqlite sort nulls", "how does postgres compare text with numbers", "how does mysql round 2.5"]:
        assert K.shape(text) == "define", text


@pytest.mark.parametrize("text", [
    "SELECT customer_id, name, SUM(amount) FROM orders GROUP BY customer_id",
    "sqlite3.OperationalError: near \"SELECT\": syntax error",
    "my sqlite query is slow",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


def test_a_query_for_sqlite_gets_sqlites_answer(client, ml_mode):
    fresh(client)
    r = say_sql(client, "SELECT id, first_name + ' ' + last_name AS full_name FROM customers", "SQLite")
    res = r["agents"][-1]["runs"][-1]["result"]
    topics = {f["topic"] for f in res["correct"]["findings"]}
    assert "concat" in topics and "||" in (res["correct"]["corrected"] or ""), (topics, res["correct"].get("corrected"))
    assert "error 1" not in r["reply"] and not WRONG_PLACES.search(r["reply"]), r["reply"][:500]


def test_a_pasted_sqlite_plan_is_read_as_sqlites(client, ml_mode):
    fresh(client)
    r = say(client, "QUERY PLAN\n|--SCAN o\n`--SEARCH c USING INTEGER PRIMARY KEY (rowid=?)")
    assert "SQLite" in r["reply"] and "SCAN o" in r["reply"] and "full table scan" in r["reply"], r["reply"][:600]
