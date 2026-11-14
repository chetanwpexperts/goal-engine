"""Night 13: tonight's concept questions (what an ON condition must connect, JOIN without ON, a subquery that gives the same
value on every row, the link condition of a correlated subquery, the same subquery written twice, the columns of a UNION, when
UNION ALL gives the same rows as UNION, UNION and upper/lower case, sorting a UNION, a HAVING condition on a GROUP BY column,
filtering early, SELECT * with a JOIN, two columns with one name in a subquery, how to review a query's structure, a speed risk
vs a wrong result, whether a subquery in SELECT is slow) through the real chat endpoint, in English, Hinglish and Hindi. Then
the structural checks in the chat: a pasted query gets them on its card, with the right class, while a pasted query, an error
or a complaint still go where they went before."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say, say_sql

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT13 = ["on_condition_tables", "join_without_on", "same_value_every_row", "correlation_condition", "repeated_subquery",
           "union_columns", "union_all_safe", "union_case_insensitive", "union_order_by", "having_on_group_column", "filter_early",
           "select_star_join", "duplicate_column_name", "query_review_checklist", "speed_risk", "subquery_in_select_speed"]
OFFICIAL = ("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT13) <= ids and len(NIGHT13) >= 15 and {"set_operations", "repeated_work"} <= set(funds)
    for cid in NIGHT13:
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


@pytest.mark.parametrize("cid", NIGHT13)
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
    ("what is a cartesian product", "cross_join"),                  # earlier nights keep their words
    ("why can't I use a column alias in where", "alias"),
    ("union vs union all", "union_vs_union_all"),
    ("what is a correlated subquery", "correlated_subquery"),
    ("what is a scalar subquery", "scalar_subquery"),
    ("what is row explosion in a join", "join_duplicates"),
    ("is mysql case sensitive", "collation"),
    ("when can i use union all", "union_all_safe"),                 # tonight's shapes: when can / where does / which / is it ok
    ("where does order by go in a union query", "union_order_by"),
    ("is it ok to write having on a group by column", "having_on_group_column"),
    ("is union case insensitive in mysql", "union_case_insensitive"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


@pytest.mark.parametrize("text", [
    "SELECT c.name, (SELECT SUM(o.amount) FROM orders o) AS total FROM customers c",
    "SELECT id, 'c' AS k FROM customers UNION SELECT id, 's' FROM suppliers",
    "ERROR 1060 (42S21): Duplicate column name 'id'",
    "ERROR:  syntax error at or near \"WHERE\"",
    "my union query is slow on postgres",
    "which column should i index for this query",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


# ---------------------------------------------------------------- the structural checks in the chat
def _result(r):
    return r["agents"][-1]["runs"][-1]["result"]


@pytest.mark.parametrize("sql,engine,topic,cls", [
    ("SELECT c.id, (SELECT SUM(o.amount) FROM orders o) AS total FROM customers c", "MariaDB", "scalar_uncorr", "likely_risk"),
    ("SELECT c.id, (SELECT MAX(o.amount) FROM orders o WHERE o.customer_id = o.id) AS top FROM customers c", "PostgreSQL",
     "corr_inner", "likely_risk"),
    ("SELECT id, 'c' AS k FROM customers UNION SELECT id, 's' FROM suppliers", "PostgreSQL", "union_all", "needs_schema"),
    ("SELECT customer_id, SUM(amount) FROM orders GROUP BY customer_id HAVING customer_id = 3", "MariaDB", "having_key",
     "needs_execution"),
    ("SELECT COUNT(*) FROM (SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.id > 2) t", "MariaDB",
     "star_dup", "proven"),
])
def test_a_pasted_query_gets_tonights_checks_with_their_class(client, ml_mode, sql, engine, topic, cls):
    fresh(client)
    r = say_sql(client, sql, engine)
    fs = _result(r)["correct"]["findings"]
    hit = [f for f in fs if f["topic"] == topic]
    assert hit and hit[0]["confidence"]["class"] == cls, [(f["topic"], f.get("confidence")) for f in fs]
    assert not WRONG_PLACES.search(r["reply"]), r["reply"][:300]
