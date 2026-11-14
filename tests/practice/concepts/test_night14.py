"""Night 14: tonight's concept questions (when two queries are the same, checking a rewrite, IN <-> EXISTS, NOT IN <-> NOT EXISTS
without changing the rows, IN as a JOIN, correlated totals as one grouped join, a calculation written once, RAND() twice,
deterministic functions, a derived table as a CTE, keyset paging with mixed directions and with NULLs, where NULLs sort, row
comparison, a COUNT that turns NULL after LEFT JOIN, IN as a value in SELECT, an alias in ORDER BY) through the real chat
endpoint, in English, Hinglish and Hindi. Then the rewrites in the chat: a pasted query with words asking for a form gets that
form with its proof, while a pasted query, an error or a complaint still go where they went before."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say, say_sql

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT14 = ["query_equivalence", "rewrite_check", "in_to_exists_rewrite", "not_in_rewrite", "in_to_join_rewrite", "correlated_to_join",
           "compute_once", "rand_twice", "deterministic_function", "derived_to_cte", "keyset_mixed_order", "keyset_nulls",
           "nulls_sort_order", "row_value_comparison", "left_join_count_null", "in_select_null", "order_by_alias"]
OFFICIAL = ("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT14) <= ids and len(NIGHT14) >= 15 and "rewrite_equivalence" in funds
    for cid in NIGHT14:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"}, cid
        assert c["examples"] and all(e.get("result") is not None or e.get("changed") is not None for e in c["examples"]), cid
        assert c["source"].startswith(OFFICIAL), cid
        assert set(c["engines"]) >= {"mysql", "mariadb", "postgresql"}, cid
        assert all(c["engines"][k].startswith("Notes only") for k in ("sqlserver", "oracle", "sqlite")), cid   # rule A
        assert set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid


@pytest.mark.parametrize("cid", NIGHT14)
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
    ("in vs exists", "in_vs_exists"),                               # earlier nights keep their words
    ("not in vs not exists", "not_in_vs_not_exists"),
    ("join vs subquery", "join_vs_subquery"),
    ("what is keyset pagination", "keyset_pagination"),
    ("cte vs subquery", "cte_vs_subquery"),
    ("why can't I use a column alias in where", "alias"),
    ("how to select random rows in sql", "random_rows"),
    ("how to sum before joining", "pre_aggregate"),
    ("are these two queries equivalent when they return the same result", "query_equivalence"),   # tonight's, other words
    ("can i rewrite in as exists", "in_to_exists_rewrite"),
    ("nulls first or last in postgres", "nulls_sort_order"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


@pytest.mark.parametrize("text", [
    "SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders)",
    "SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders);\n\nrewrite this with EXISTS",
    "ERROR 1054 (42S22): Unknown column 'net' in 'where clause'",
    "ERROR:  column \"net\" does not exist",
    "my query with not in is slow on postgres",
    "which column should i index for this query",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


# ---------------------------------------------------------------- the rewrites in the chat
def _result(r):
    return r["agents"][-1]["runs"][-1]["result"]


@pytest.mark.parametrize("sql,engine,topic,family", [
    ("SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders WHERE status = 'paid');\n\nrewrite this with EXISTS",
     "MariaDB", "rw_exists", "in_to_exists"),
    ("SELECT id FROM categories WHERE id NOT IN (SELECT category_id FROM products);\n\nNOT IN ki jagah NOT EXISTS likho",
     "PostgreSQL", "rw_not_exists", "not_in_to_not_exists"),
    ("SELECT c.id, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) AS n, (SELECT MAX(o.amount) FROM orders o "
     "WHERE o.customer_id = c.id) AS top FROM customers c", "PostgreSQL", "preagg", "preaggregate"),
    ("SELECT c.id FROM customers c WHERE c.id IN (SELECT customer_id FROM orders);\n\nJOIN से लिखो", "MariaDB", "rw_join", "in_to_join"),
])
def test_a_pasted_query_gets_the_rewrite_with_its_proof(client, ml_mode, sql, engine, topic, family):
    fresh(client)
    r = say_sql(client, sql, engine)
    res = _result(r)
    hit = [f for f in res["correct"]["findings"] if f["topic"] == topic]
    assert hit and hit[0]["proof"]["family"] == family and hit[0]["proof"]["verdict"] == "same", [(f["topic"], f.get("proof")) for f in res["correct"]["findings"]]
    assert res.get("suggested_kind") == "speed" and res.get("proof"), res.get("suggested_kind")
    assert "Duplicates" in r["reply"] or "Duplicate" in r["reply"], r["reply"][:400]
    assert not WRONG_PLACES.search(r["reply"]), r["reply"][:300]
