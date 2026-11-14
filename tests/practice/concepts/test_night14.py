"""Night 14: tonight's concept questions (when a rewrite is the same query, checking that two queries return the same rows,
IN -> EXISTS, EXISTS -> IN, IN -> JOIN without duplicates, NOT IN -> NOT EXISTS, a correlated subquery replaced by a join,
COUNT 0 vs NULL after a LEFT JOIN, ORDER BY a SELECT name, a calculation written once, functions that change on every call,
keyset paging with ties, row value comparison, IN list vs OR, BETWEEN's ends, checking that a rewrite is faster) through the
real chat endpoint, in English, Hinglish and Hindi. Then the rewrites in the chat: a pasted query gets its same-rows rewrite
(and the one the person asks for), while a pasted query, an error or a complaint still go where they went before."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say, say_sql

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT14 = ["equivalent_rewrite", "compare_query_results", "in_to_exists", "exists_to_in", "in_to_join", "not_in_to_not_exists",
           "correlated_to_join", "correlated_count_zero", "order_by_alias", "repeat_calculation", "deterministic_functions",
           "keyset_tie_breaker", "row_value_comparison", "or_vs_in_list", "between_inclusive", "rewrite_speed_check"]
OFFICIAL = ("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT14) <= ids and len(NIGHT14) >= 15 and "equivalence" in funds
    for cid in NIGHT14:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"}, cid
        assert c["examples"] and all(e.get("result") is not None or e.get("changed") is not None for e in c["examples"]), cid
        assert c["source"].startswith(OFFICIAL), cid
        assert set(c["engines"]) >= {"mysql", "mariadb", "postgresql"}, cid
        assert all(c["engines"][k].startswith("Notes only") for k in ("sqlserver", "oracle", "sqlite")), cid   # rule A
        assert set(c["fundamentals"]) <= set(funds) and "equivalence" in c["fundamentals"], cid
        assert all(K.by_id(r) for r in c["related"]), cid


def test_the_lab_results_hold_what_the_answers_say():
    ex = {cid: K.by_id(cid)["examples"][0]["result"] for cid in NIGHT14}
    assert ex["equivalent_rewrite"] == [["5", "8", "5"]]                      # IN, a plain JOIN, a JOIN to DISTINCT
    assert ex["compare_query_results"] == [["3", "0"]]                        # EXCEPT ALL sees the repeats, EXCEPT doesn't
    assert ex["not_in_to_not_exists"] == [["4"]]
    assert ex["correlated_count_zero"][1] == ["4", "0", "NULL", "0"]          # the correlated COUNT, the LEFT JOIN, COALESCE


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
    ("what is keyset pagination", "keyset_pagination"),
    ("how to sum before joining", "pre_aggregate"),
    ("what is an alias in sql", "alias"),
    ("what is the in operator in sql", "in_operator"),
    ("join vs subquery", "join_vs_subquery"),
    ("how to replace null with 0", "coalesce"),
    ("can i use an alias in order by in postgres", "order_by_alias"),   # tonight's, in other words
    ("is between inclusive in mysql", "between_inclusive"),
    ("how to turn IN into EXISTS", "in_to_exists"),
    ("EXISTS ko IN se kaise likhe", "exists_to_in"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


@pytest.mark.parametrize("text", [
    "SELECT c.id, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) AS n FROM customers c",
    "SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders)",
    "ERROR 1054 (42S22): Unknown column 'net' in 'where clause'",
    "ERROR:  column \"net\" does not exist",
    "my exists query is slow on mysql",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


# ---------------------------------------------------------------- the rewrites in the chat
def _result(r):
    return r["agents"][-1]["runs"][-1]["result"]


@pytest.mark.parametrize("sql,engine,topic", [
    ("SELECT c.id, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) AS n FROM customers c", "MariaDB", "preagg_subquery"),
    ("SELECT id, amount - COALESCE(discount, 0) AS net FROM orders ORDER BY amount - COALESCE(discount, 0) DESC", "PostgreSQL",
     "expr_once"),
    ("SELECT c.id FROM customers c WHERE c.id IN (SELECT o.customer_id FROM orders o);\n\nrewrite this using EXISTS", "PostgreSQL",
     "to_exists"),
])
def test_a_pasted_query_gets_its_same_rows_rewrite(client, ml_mode, sql, engine, topic):
    fresh(client)
    r = say_sql(client, sql, engine)
    fs = _result(r)["correct"]["findings"]
    hit = [f for f in fs if f["topic"] == topic]
    assert hit and hit[0]["confidence"]["class"] == "needs_execution", [(f["topic"], f.get("confidence")) for f in fs]
    assert hit[0]["same"]["proof"] == "query" and hit[0].get("rewrite")
    assert not WRONG_PLACES.search(r["reply"]), r["reply"][:300]
