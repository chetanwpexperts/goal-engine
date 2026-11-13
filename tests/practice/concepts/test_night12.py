"""Night 12: tonight's concept questions (the blocks of a query, its dependency tree, the order of CTEs, several CTEs, a CTE
used twice, whether a CTE is run once, totalling before a join, fan-out, the chasm trap, whether a join changes the grain,
UNION vs UNION ALL, debugging a long query, counting rows at each step, a derived table's alias, CTE vs temporary table, a
WITH inside a CTE) through the real chat endpoint, in English, Hinglish and Hindi. Then the blocks in the chat: a long query's
card and reading carry the dependency tree and each block's grain, 'show the dependency tree' / 'har block ka grain' after a
check gets it, while a pasted query, an error or a complaint still go where they went before."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say, say_sql

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT12 = ["query_blocks", "dependency_tree", "cte_order_matters", "multiple_ctes", "cte_referenced_twice", "cte_materialized",
           "pre_aggregate", "fan_out", "chasm_trap", "grain_change_join", "union_vs_union_all", "debug_long_query", "rows_per_stage",
           "derived_table_alias", "cte_vs_temp_table", "nested_with"]
OFFICIAL = ("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT12) <= ids and len(NIGHT12) >= 15 and "grain" in funds
    for cid in NIGHT12:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"}, cid
        assert c["examples"] and all(e.get("result") is not None or e.get("changed") is not None for e in c["examples"]), cid
        assert c["source"].startswith(OFFICIAL), cid
        assert set(c["engines"]) >= {"mysql", "mariadb", "postgresql"}, cid
        assert set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid


@pytest.mark.parametrize("cid", NIGHT12)
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
    ("or vs union all which is faster", "or_vs_union"),             # earlier nights keep their words
    ("what does the with clause do", "cte"),
    ("why does a subquery in from need an alias", "derived_table"),
    ("what is a cte", "cte"),
    ("what is a recursive cte", "recursive_cte"),
    ("union all kya hota hai", "union_vs_union_all"),
    ("how to aggregate before joining two tables", "pre_aggregate"),
    ("what is fanout", "fan_out"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


@pytest.mark.parametrize("text", [
    "WITH t AS (SELECT o.customer_id, SUM(o.amount) AS s FROM orders o JOIN order_items i ON i.order_id = o.id GROUP BY o.customer_id) SELECT * FROM t",
    "ERROR 1146 (42S02): Table 'shop.b' doesn't exist",
    "my cte query is slow on postgres",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


# ---------------------------------------------------------------- the blocks in the chat
def _result(r):
    return r["agents"][-1]["runs"][-1]["result"]


LONG = """WITH paid AS (
  SELECT id, customer_id, amount
  FROM orders
  WHERE status = 'paid'
),
spend AS (
  SELECT customer_id, SUM(amount) AS total, COUNT(*) AS n
  FROM paid
  GROUP BY customer_id
),
items AS (
  SELECT o.customer_id, SUM(o.amount) AS amount_seen, SUM(i.quantity) AS qty
  FROM orders o
  JOIN order_items i ON i.order_id = o.id
  GROUP BY o.customer_id
)
SELECT
  c.id,
  c.city,
  s.total,
  s.n,
  it.qty,
  it.amount_seen
FROM customers c
JOIN spend s
  ON s.customer_id = c.id
LEFT JOIN items it
  ON it.customer_id = c.id
WHERE c.city IS NOT NULL
  AND s.total > 0
ORDER BY
  s.total DESC,
  c.id
"""


def test_a_long_query_shows_its_tree_and_grain(client, ml_mode):
    fresh(client)
    assert len(LONG.strip().splitlines()) >= 30
    r = say_sql(client, LONG, "PostgreSQL")
    res = _result(r)
    tree = res["blocks"]["en"]
    for line in ("CTE spend: input one row per paid (CTE) row -> output one row per customer_id",
                 "CTE items: input one row per order_items row -> output one row per o.customer_id",
                 "CTE paid: input one row per orders row -> output one row per orders row"):
        assert line in tree, tree
    assert "! join order_items" in tree and "! join spend" not in tree, tree          # spend is one row per customer_id: proven
    assert "Blocks, how they depend on each other" in res["reading"]["en"]
    fs = res["correct"]["findings"]
    assert any(f["topic"] == "join_fanout" and f.get("block") == "items" and f["confidence"]["class"] == "likely_risk" for f in fs), fs
    assert not any(f["kind"] in ("error", "wrong") for f in fs), fs


@pytest.mark.parametrize("ask,hi", [("show the dependency tree", False), ("what is the grain of each block", False),
                                    ("har block ka grain batao", True)])
def test_asking_for_the_tree_gets_it(client, ml_mode, ask, hi):
    fresh(client)
    r = say_sql(client, LONG, "MariaDB")
    r = say(client, ask, r["goal"]["id"])
    assert "CTE spend" in r["reply"] and "CTE items" in r["reply"] and "->" in r["reply"], r["reply"]
    assert ("andar" in r["reply"]) == hi, r["reply"]
    assert not WRONG_PLACES.search(r["reply"]), r["reply"]
