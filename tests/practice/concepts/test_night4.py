"""Night 4: tonight's concept questions (subqueries: correlated / scalar / derived tables, EXISTS and NOT EXISTS, IN vs EXISTS,
NOT IN and NULLs, ANY / ALL, latest and top N per group, second highest, CTE vs subquery, LATERAL, LIMIT in a subquery) through the
real chat endpoint, in English, Hinglish and Hindi. Each phrasing gets its concept, never the query checker, the error diagnoser,
the connect step or 'no specific answer'; and real work (a pasted query, a pasted error, a complaint about their own query) still
goes where it went before."""

import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT4 = ["subquery", "correlated_subquery", "scalar_subquery", "derived_table", "exists", "not_exists", "in_vs_exists",
          "not_in_vs_not_exists", "not_in_null", "any_all", "latest_per_group", "top_n_per_group", "nth_highest", "cte_vs_subquery",
          "lateral_join", "exists_select_1", "exists_vs_count", "limit_in_subquery"]


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    assert set(NIGHT4) <= ids and len(NIGHT4) >= 15
    for cid in NIGHT4:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert set(c["proven"]) >= {"mariadb", "postgresql"} and all(e.get("result") is not None for e in c["examples"]), cid
        assert c["source"].startswith(("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")), cid
        assert set(c["engines"]) >= {"mysql", "mariadb", "postgresql"}, cid
        assert all(set(f) <= set(K.pack() and __import__("json").load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"])
                   for f in [c["fundamentals"]]), cid


@pytest.mark.parametrize("cid", NIGHT4)
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
    ("explain correlated subqueries with an example", "correlated_subquery"),
    ("how do i get each customer's most recent order", "latest_per_group"),
    ("har department ki sabse latest hiring kaise nikale", "latest_per_group"),
    ("what is the difference between exists and in", "in_vs_exists"),
    ("why is my not in query returning nothing when the column has nulls", "not_in_null"),
    ("how to find 3rd highest salary in postgres", "nth_highest"),
    ("subquery kya hai mysql mein", "subquery"),
    ("what does exists do", "exists"),
    ("with clause vs subquery which is better", "cte_vs_subquery"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


def test_a_named_database_gets_its_own_syntax():
    my, _ = K.answer(K.match("what is a lateral join in mariadb"), "what is a lateral join in mariadb")
    ex = lambda text: text.split("Example", 1)[1].split("Common mistake")[0]   # noqa: E731 - the example shown
    assert "LATERAL" not in ex(my) and "No LATERAL" in my
    pg, _ = K.answer(K.match("what is a lateral join in postgres"), "what is a lateral join in postgres")
    assert "JOIN LATERAL" in ex(pg)


@pytest.mark.parametrize("text", [
    "SELECT id FROM customers c WHERE EXISTS (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id)",
    "ERROR 1242 (21000): Subquery returns more than 1 row",
    "ERROR 1235 (42000): This version of MariaDB doesn't yet support 'LIMIT & IN/ALL/ANY/SOME subquery'",
    "my subquery is slow",
    "why does this return no rows: SELECT id FROM customers WHERE city NOT IN ('Pune', NULL)",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] != "concept", r["reply"][:200]
