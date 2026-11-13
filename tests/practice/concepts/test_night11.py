"""Night 11: tonight's concept questions (join cardinality, whether a column is unique or can be NULL, a column's data type, the
inferred schema, proven / likely risk / needs schema / needs execution, checking a query without the database, why the table
structure and EXPLAIN are needed, join keys, the grain, measures and dimensions, a query's hidden assumptions, whether EXPLAIN
runs the statement, unique in the data vs a UNIQUE constraint, estimated vs actual rows) through the real chat endpoint, in
English, Hinglish and Hindi. Then the query-only answer in the chat: the card carries each finding's class, the inferred
schema and what can't be proven, and 'what can't you prove' / 'kitna pakka hai' asked after a check gets the report, while a
pasted query, an error or a complaint still go where they went before."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say, say_sql

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT11 = ["join_cardinality", "is_column_unique", "is_column_nullable", "column_data_type", "inferred_schema", "confidence_classes",
           "query_without_database", "why_schema_needed", "why_explain_needed", "join_key", "grain", "measure_dimension",
           "query_assumptions", "explain_without_running", "unique_in_data_vs_constraint", "estimated_vs_actual_rows"]
OFFICIAL = ("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT11) <= ids and len(NIGHT11) >= 15 and "proof_classes" in funds
    for cid in NIGHT11:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"}, cid
        assert c["examples"] and all(e.get("result") is not None or e.get("changed") is not None for e in c["examples"]), cid
        assert c["source"].startswith(OFFICIAL), cid
        assert set(c["engines"]) >= {"mysql", "mariadb", "postgresql"}, cid
        assert set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid


@pytest.mark.parametrize("cid", NIGHT11)
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
    ("what is an index", "index"),                                 # the learning feed: a real user's question, still answered
    ("what is cardinality of an index", "index_selectivity"),      # the other meaning of the word keeps its concept
    ("how to find duplicate emails", "find_duplicates"),           # earlier nights keep their words
    ("what is a one to many relationship", "relationships"),
    ("explain vs explain analyze", "explain_analyze"),
    ("what is the grain of a report", "grain"),
    ("what does likely risk mean", "confidence_classes"),
    ("how to know if a column accepts null values", "is_column_nullable"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


@pytest.mark.parametrize("text", [
    "SELECT o.id, SUM(o.amount) FROM orders o JOIN order_items i ON i.order_id = o.id GROUP BY o.id",
    "ERROR 1111 (HY000): Invalid use of group function",
    "my query is slow on mysql",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


# ---------------------------------------------------------------- the query-only answer in the chat
def _result(r):
    return r["agents"][-1]["runs"][-1]["result"]


QUERY = ("SELECT c.city, SUM(o.amount) AS total FROM customers c JOIN orders o ON o.customer_id = c.id "
         "WHERE o.status = 'paid' AND o.order_date BETWEEN '2024-01-01' AND '2024-01-31' GROUP BY c.city")


def test_the_card_carries_classes_the_inferred_schema_and_what_cant_be_proven(client, ml_mode):
    fresh(client)
    r = say_sql(client, QUERY, "PostgreSQL")
    res = _result(r)
    fs = res["correct"]["findings"]
    assert fs and all(f["confidence"]["class"] in ("proven", "likely_risk", "needs_schema", "needs_execution") for f in fs), fs
    assert any(f["topic"] == "date_range" and f["confidence"]["class"] == "needs_schema" for f in fs), fs
    qo = res["query_only"]
    tables = {t["name"]: t for t in qo["inferred_schema"]["tables"]}
    assert set(tables) == {"customers", "orders"} and not any(t["verified"] for t in tables.values())
    assert "measure" in tables["orders"]["columns"]["amount"]["roles"] and tables["orders"]["columns"]["order_date"]["type"] == "date"
    assert all(not c["verified"] for t in tables.values() for c in t["columns"].values())
    assert any("orders.order_date" in x["en"] for x in qo["cant_prove"]), qo["cant_prove"]
    assert any(c["sql"].startswith("EXPLAIN (ANALYZE") for x in qo["cant_prove"] for c in x["check"]), qo["cant_prove"]
    assert "Inferred schema" in qo["en"] and "verified=false" in qo["en"] and "worth checking" in qo["en"].lower()
    assert "the index is missing" not in qo["en"].lower()


@pytest.mark.parametrize("ask,hi", [("what can't you prove from this query", False), ("how sure are you", False),
                                    ("kitna pakka hai", True), ("assumptions kya hain", True)])
def test_asking_what_cant_be_proven_gets_the_report(client, ml_mode, ask, hi):
    fresh(client)
    r = say_sql(client, QUERY, "MariaDB")
    goal = r["goal"]["id"]
    r = say(client, ask, goal)
    rep = r["reply"]
    if hi:
        assert "verified=false" in rep and ("pakka nahi" in rep or "Kitna pakka" in rep), rep
    else:
        assert "can't be proven from the query alone" in rep and "verified=false" in rep, rep
    assert "ANALYZE SELECT" in rep and "EXPLAIN ANALYZE" not in rep, rep           # MariaDB's own words (rule A, B)
    assert not WRONG_PLACES.search(rep), rep


def test_tables_given_with_the_query_verify_the_inferred_schema(client, ml_mode):
    fresh(client)
    tables = ("CREATE TABLE customers (id INT PRIMARY KEY, city VARCHAR(40));\n"
              "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT NOT NULL, status VARCHAR(20) NOT NULL, amount DECIMAL(10,2) NOT NULL,"
              " order_date DATE NOT NULL);\n")
    r = say_sql(client, tables + QUERY + ";", "PostgreSQL")
    res = _result(r)
    inf = res["query_only"]["inferred_schema"]
    assert all(t["verified"] for t in inf["tables"]), inf
    assert any(k.get("verified") and k["shape"] == "many orders -> one customers" for k in inf["links"]), inf["links"]
    assert not any(f["topic"] == "date_range" for f in res["correct"]["findings"]), res["correct"]["findings"]   # a DATE: ruled out
