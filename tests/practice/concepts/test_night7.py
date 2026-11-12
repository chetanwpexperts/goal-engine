"""Night 7: tonight's concept questions (CASE WHEN, the order of WHENs, CASE without ELSE, simple vs searched CASE, custom sort,
pivot, CTE, recursive CTE, number and date series, hierarchies, window functions, PARTITION BY, ROW_NUMBER / RANK / DENSE_RANK,
running totals, LAG / LEAD, window frames, LAST_VALUE, NTILE, gaps and islands, moving averages, why a window can't go in WHERE,
old versions) through the real chat endpoint, in English, Hinglish and Hindi. Each phrasing gets its concept, never the query
checker, the error diagnoser, the connect step or 'no specific answer'; and real work (a pasted query, a pasted error, a complaint
about their own query) still goes where it went before."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT7 = ["case_when", "case_when_order", "case_no_else", "simple_vs_searched_case", "custom_sort", "pivot_case", "cte", "recursive_cte",
          "generate_series", "hierarchy_query", "window_function", "partition_by", "rank_functions", "row_number", "running_total",
          "lag_lead", "window_frame", "last_value_frame", "ntile", "gaps_and_islands", "moving_average", "window_in_where",
          "window_old_versions"]


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT7) <= ids and len(NIGHT7) >= 15
    for cid in NIGHT7:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"}, cid
        assert c["examples"] and all(e.get("result") is not None for e in c["examples"]), cid
        assert c["source"].startswith(("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")), cid
        assert set(c["engines"]) >= {"mysql", "mariadb", "postgresql"}, cid
        assert c["fundamentals"] and set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid


@pytest.mark.parametrize("cid", NIGHT7)
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
    ("explain case when with an example", "case_when"),
    ("how do i calculate cumulative total in postgres", "running_total"),
    ("what is the difference between rank and dense rank", "rank_functions"),
    ("what is a common table expression", "cte"),
    ("how to find employees under a manager recursively", "hierarchy_query"),
    ("what is lead function in sql", "lag_lead"),
    ("dense_rank kya hai", "rank_functions"),
    ("how to convert rows to columns in mysql", "pivot_case"),
    ("what does over do in sql", "window_function"),
    ("how to find consecutive login days", "gaps_and_islands"),
    ("what is with recursive in mariadb", "recursive_cte"),
    ("what is a window frame", "window_frame"),
    ("how to get the second highest salary", "nth_highest"),       # earlier nights' concepts keep their words
    ("cte vs subquery", "cte_vs_subquery"),
    ("group by vs partition by", "group_by_vs_window"),
    ("how to get the latest order for each customer", "latest_per_group"),
    ("what is a primary key", "primary_key"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


def test_a_named_database_gets_its_own_syntax():
    ex = lambda text: text.split("Example", 1)[1].split("Common mistake")[0]   # noqa: E731 - the example shown
    pg, _ = K.answer(K.match("how to generate a series of dates in postgres"), "how to generate a series of dates in postgres")
    assert "generate_series" in ex(pg) and "WITH RECURSIVE" not in ex(pg)
    my, _ = K.answer(K.match("how to generate a series of dates in mysql"), "how to generate a series of dates in mysql")
    assert "WITH RECURSIVE" in ex(my) and "generate_series" not in ex(my)
    md, _ = K.answer(K.match("what is lag and lead in mariadb"), "what is lag and lead in mariadb")
    assert "4018" in md.split("Common mistake", 1)[1]


@pytest.mark.parametrize("text", [
    "SELECT id FROM employees WHERE ROW_NUMBER() OVER (ORDER BY salary DESC) = 1",
    "WITH RECURSIVE n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n) SELECT x FROM n",
    "ERROR 4015 (HY000): Window function is allowed only in SELECT list and ORDER BY clause",
    "ERROR: window functions are not allowed in WHERE",
    "ERROR 1146 (42S02): Table 'shop.n' doesn't exist",
    "my running total query is slow",
    "why does this case return null: SELECT CASE WHEN amount > 100 THEN 'big' END FROM orders",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] != "concept", r["reply"][:200]
