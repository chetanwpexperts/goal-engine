"""Night 8: tonight's concept questions (composite indexes and the leftmost prefix, covering indexes, EXPLAIN and its access
types, EXPLAIN ANALYZE, Seq Scan vs Index Scan, sargable conditions, leading wildcards, keyset paging, OR vs UNION ALL, clustered
indexes, selectivity, the cost of indexes, expression and partial indexes, implicit conversion, indexes for ORDER BY, statistics,
finding slow queries, index types, full-text, why an index isn't used) through the real chat endpoint, in English, Hinglish and
Hindi. Each phrasing gets its concept, never the query checker, the error diagnoser, the connect step or 'no specific answer';
'slow' in a general question is a lesson, while the person's own slow query or database still goes to the checker / diagnoser;
and a pasted EXPLAIN plan is read step by step."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT8 = ["composite_index", "leftmost_prefix", "covering_index", "explain", "explain_analyze", "explain_access_types", "seq_scan_vs_index_scan",
          "sargable", "leading_wildcard", "keyset_pagination", "or_vs_union", "clustered_index", "index_selectivity", "too_many_indexes",
          "expression_index", "partial_index", "implicit_conversion", "index_order_by", "analyze_statistics", "slow_query_log", "btree_vs_hash",
          "fulltext_index", "index_not_used"]


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT8) <= ids and len(NIGHT8) >= 15
    for cid in NIGHT8:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"}, cid
        assert c["examples"] and all(e.get("result") is not None or e.get("changed") is not None for e in c["examples"]), cid
        assert c["source"].startswith(("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")), cid
        assert set(c["engines"]) >= {"mysql", "mariadb", "postgresql"}, cid
        assert "index_use" in c["fundamentals"] and set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid


@pytest.mark.parametrize("cid", NIGHT8)
def test_every_phrasing_of_tonights_concepts(client, ml_mode, cid):
    c = K.by_id(cid)
    titles = set(c["title"].values())
    for lang, phr in c["phrasings"].items():
        for text in phr:
            fresh(client)
            r = say(client, text)
            assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in titles and not WRONG_PLACES.search(r["reply"]), \
                (lang, text, r["relation"], r["reply"][:200])


@pytest.mark.parametrize("text,cid", [                 # said other ways than the stored phrasings: the shape and the words decide
    ("why is offset pagination slow on big tables", "keyset_pagination"),
    ("what is an index on multiple columns", "composite_index"),
    ("can i use like '%term' with an index in postgres", "leading_wildcard"),
    ("what is a query plan", "explain"),
    ("what does eq_ref mean", "explain_access_types"),
    ("explain analyze kya karta hai", "explain_analyze"),
    ("how to index lower(email) in postgres", "expression_index"),
    ("do too many indexes slow down writes", "too_many_indexes"),
    ("why doesn't year() use the index", "sargable"),
    ("what is a secondary index in mysql", "clustered_index"),
    ("what is pg_stat_statements", "slow_query_log"),
    ("what is an index", "index"),                                 # earlier nights' concepts keep their words
    ("does a foreign key create an index", "fk_index"),
    ("what is limit and offset", "limit"),
    ("what is like in sql", "like"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


def test_a_named_database_gets_its_own_notes():
    pg, _ = K.answer(K.match("what is a partial index in postgres"), "what is a partial index in postgres")
    assert "PostgreSQL" in pg and "CREATE INDEX" in pg
    md, _ = K.answer(K.match("how to index lower(email) in mariadb"), "how to index lower(email) in mariadb")
    assert "virtual column" in md.lower() or "generated" in md.lower()


@pytest.mark.parametrize("text", [
    "SELECT id FROM orders WHERE YEAR(order_date) = 2024",
    "SELECT id FROM products ORDER BY id LIMIT 20 OFFSET 200000",
    "ERROR 1176 (42000): Key 'ix_x' doesn't exist in table 'orders'",
    "my query is slow",
    "why is my query slow",
    "database bahut slow hai",
    "the report page is slow",
    "mera database dheema chal raha hai",
    "यह क्वेरी धीमी है",
    "index lagane ke baad bhi meri query slow hai",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] != "concept", (text, r["reply"][:200])


PG_PLAN = """                                                            QUERY PLAN
----------------------------------------------------------------------------------------------------------------------------
 Seq Scan on orders o  (cost=0.00..571.00 rows=100 width=29) (actual time=0.011..11.143 rows=7362 loops=1)
   Filter: (EXTRACT(year FROM order_date) = '2023'::numeric)
   Rows Removed by Filter: 12638
 Planning Time: 0.832 ms
 Execution Time: 15.456 ms
(5 rows)"""

MY_PLAN = """+------+-------------+-------+------+---------------+------+---------+------+-------+-------------------------------------------------+
| id   | select_type | table | type | possible_keys | key  | key_len | ref  | rows  | Extra                                           |
+------+-------------+-------+------+---------------+------+---------+------+-------+-------------------------------------------------+
|    1 | SIMPLE      | c     | ALL  | NULL          | NULL | NULL    | NULL | 2000  | Using temporary; Using filesort                 |
|    1 | SIMPLE      | o     | ALL  | NULL          | NULL | NULL    | NULL | 19569 | Using where; Using join buffer (flat, BNL join) |
+------+-------------+-------+------+---------------+------+---------+------+-------+-------------------------------------------------+
2 rows in set (0.001 sec)"""


@pytest.mark.parametrize("plan,engine,says", [(PG_PLAN, "PostgreSQL", "the filter wraps the column"),
                                              (MY_PLAN, "MySQL/MariaDB", "Join without an index on o")])
def test_a_pasted_plan_is_read_step_by_step(client, ml_mode, plan, engine, says):
    fresh(client)
    r = say(client, plan)
    assert r["relation"] != "concept" and f"This is a {engine} plan" in r["reply"] and says in r["reply"], r["reply"][:400]
    assert "Steps, in the order they run" in r["reply"]


def test_a_pasted_plan_with_its_query_checks_both(client, ml_mode):
    fresh(client)
    r = say(client, "EXPLAIN ANALYZE SELECT id FROM orders o WHERE EXTRACT(YEAR FROM order_date) = 2023\n" + PG_PLAN)
    assert r["relation"] != "concept", r["reply"][:300]
    assert "order_date >= '2023-01-01'" in r["reply"] or "EXTRACT" in r["reply"], r["reply"][:600]
