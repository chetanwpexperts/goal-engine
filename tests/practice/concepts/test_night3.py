"""Night 3: tonight's concept questions (aggregation: COUNT / SUM / AVG, conditional totals, totals of totals, shares,
ROLLUP, ONLY_FULL_GROUP_BY ...) through the real chat endpoint, in English, Hinglish and Hindi. Each phrasing gets its concept,
never the query checker, the error diagnoser, the connect step or 'no specific answer'; and real work (a pasted query, a pasted
error, a complaint about their own query) still goes where it went before."""

import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT3 = ["count_distinct", "sum_function", "avg_nulls", "min_max", "conditional_count", "count_case_else", "sum_null_zero",
          "group_by_multiple", "count_per_group", "count_including_zero", "nested_aggregate", "percent_of_total", "avg_of_averages",
          "join_sum_doubled", "string_agg", "rollup", "only_full_group_by", "having_alias", "group_by_month", "sum_distinct",
          "group_by_vs_window"]


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    assert set(NIGHT3) <= ids and len(NIGHT3) >= 15
    for cid in NIGHT3:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert set(c["proven"]) >= {"mariadb", "postgresql"} and all(e.get("result") is not None for e in c["examples"]), cid
        assert c["source"].startswith(("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")), cid
        assert set(c["engines"]) >= {"mysql", "mariadb", "postgresql"}, cid


@pytest.mark.parametrize("cid", NIGHT3)
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
    ("explain count distinct in postgres", "count_distinct"),
    ("difference between count(*) and count(distinct col)", "count_distinct"),
    ("percentage nikalna hai total ka, kaise kare", "percent_of_total"),
    ("how can i get a grand total row", "rollup"),
    ("orders ko month wise group kaise kare", "group_by_month"),
    ("why is my total too high after joining payments", "join_sum_doubled"),
    ("group_concat kya hai", "string_agg"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


def test_a_named_database_gets_its_own_syntax(client, ml_mode):
    my, _ = K.answer(K.match("how to get subtotals and a grand total in mariadb"), "how to get subtotals and a grand total in mariadb")
    ex = lambda text: text.split("Example", 1)[1].split("Common mistake")[0]   # noqa: E731 - the example shown
    assert "WITH ROLLUP" in ex(my) and "ROLLUP (status)" not in ex(my) and "- PostgreSQL" not in my
    pg, _ = K.answer(K.match("how to get subtotals and a grand total in postgres"), "how to get subtotals and a grand total in postgres")
    assert "ROLLUP (status)" in ex(pg) and "WITH ROLLUP" not in ex(pg)
    sa, _ = K.answer(K.match("group_concat vs string_agg in postgresql"), "group_concat vs string_agg in postgresql")
    assert "STRING_AGG(id::text" in sa and "SEPARATOR" not in sa.split("Example")[1]


@pytest.mark.parametrize("text", [
    "SELECT COUNT(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) FROM orders",
    "ERROR 1111 (HY000): Invalid use of group function",
    "ERROR 1055 (42000): Expression #2 of SELECT list is not in GROUP BY clause and contains nonaggregated column 'shop.o.amount'",
    "my sum query is slow",
    "why is this query giving wrong totals: SELECT c.id, SUM(o.amount) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN payments p ON p.order_id = o.id GROUP BY c.id",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] != "concept", r["reply"][:200]
