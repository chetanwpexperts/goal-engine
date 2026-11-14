"""Night 15: tonight's concept questions (a CTE nothing reads, naming a CTE's columns, EXCEPT / INTERSECT, a CTE in EXPLAIN, NULL
as a UNION placeholder, ORDER BY 1, OVER () on every row, the WINDOW clause, LAG / LEAD with a default, writing a long query so
it can be read, HAVING without GROUP BY, a CTE's scope, WITH before UPDATE / DELETE / INSERT, CREATE TABLE AS SELECT, temporary
tables, GROUP BY an expression, PERCENT_RANK / CUME_DIST) through the real chat endpoint, in English, Hinglish and Hindi. Then a
long pasted query, an error or a complaint still go where they went before, and a big pasted query gets the tree and the
located mistake in the chat."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.fuzz import bigq
from tests.test_ml import say, say_sql

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT15 = ["unused_cte", "cte_column_list", "except_intersect", "cte_in_explain", "union_null_placeholder", "order_by_position",
           "over_empty", "named_window", "lag_default", "sql_formatting", "having_without_group_by", "cte_scope", "cte_with_update",
           "create_table_as", "temporary_table", "group_by_expression", "percent_rank"]
OFFICIAL = ("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT15) <= ids and len(NIGHT15) >= 15
    for cid in NIGHT15:
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


@pytest.mark.parametrize("cid", NIGHT15)
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
    ("what is a cte", "cte"),                                       # earlier nights keep their words
    ("cte vs temp table", "cte_vs_temp_table"),
    ("what is the lag function", "lag_lead"),
    ("what is a window function", "window_function"),
    ("how to debug a long query", "debug_long_query"),
    ("what is order by", "order_by"),
    ("what is having", "having"),
    ("union vs union all", "union_vs_union_all"),
    ("lag default value in mariadb", "lag_default"),                # tonight's, other words
    ("how to show total on every row", "over_empty"),
    ("is intersect supported in mariadb", "except_intersect"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


def test_the_engine_named_gets_its_own_note(client, ml_mode):
    fresh(client)
    r = say(client, "how to give lag a default value in mariadb")
    assert r["relation"] == "concept" and "1064" in r["reply"], r["reply"][:600]


@pytest.mark.parametrize("text", [
    "SELECT id, LAG(amount, 1, 0) OVER (ORDER BY id) AS prev FROM orders",
    "WITH g AS (SELECT id FROM orders WHERE status = 'cancelled') DELETE FROM orders WHERE id IN (SELECT id FROM g)",
    "ERROR 1064 (42000): You have an error in your SQL syntax; check the manual near ' 0) OVER (ORDER BY id)'",
    "my long report query with ctes is slow",
    "which cte in my query is slow",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


def _result(r):
    return r["agents"][-1]["runs"][-1]["result"]


def test_a_big_pasted_query_gets_the_tree_and_the_located_mistake(client, ml_mode):
    case = bigq.make(1500, "null_eq")
    fresh(client)
    r = say_sql(client, case["planted"], "PostgreSQL")
    res = _result(r)
    hit = [f for f in res["correct"]["findings"] if f["topic"] == "null"]
    assert hit and hit[0]["block"] == case["block"], [(f["topic"], f.get("block")) for f in res["correct"]["findings"]]
    assert res.get("blocks") and "Blocks, how they depend" in res["reading"]["en"]
    assert not WRONG_PLACES.search(r["reply"]), r["reply"][:300]


def test_mariadb_gets_the_with_moved_inside(client, ml_mode):
    fresh(client)
    r = say_sql(client, "WITH g AS (SELECT id FROM orders WHERE status = 'cancelled') DELETE FROM orders WHERE id IN (SELECT id FROM g)",
                "MariaDB")
    res = _result(r)
    hit = [f for f in res["correct"]["findings"] if f["topic"] == "dialect"]
    assert hit and "WITH" in hit[0]["title_en"], [(f["topic"], f["title_en"]) for f in res["correct"]["findings"]]
    assert res["correct"]["corrected"].startswith("DELETE FROM orders WHERE id IN (WITH g AS")
