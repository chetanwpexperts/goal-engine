"""Is the query right, before is it fast: a query with many kinds of mistakes, random broken queries (tests/fuzz/sqlbugs.py)
and valid ones (tests/fuzz/db.py) that must get no false 'error' or 'wrong'."""

import random
from pathlib import Path

import pytest

from app.agents import sqlcorrect
from app.agents.sql import analyze_sql
from tests.fuzz import db as dbfuzz
from tests.fuzz import sqlbugs

BROKEN = (Path(__file__).parent / "data" / "broken_joins.sql").read_text()


def test_every_kind_of_mistake_in_one_query_is_found():
    r = sqlcorrect.check(BROKEN)
    titles = " | ".join(f["title_en"] for f in r["findings"])
    for must in ["'products.id'", "'city.id'", "FULL OUTER JOIN", "ORDER BY uses customer", "aren't grouped", "Self join",
                 "departments is joined twice", "cities is joined twice", "'!= NULL'", "COUNT(o.id)", "HAVING", "RIGHT JOIN",
                 "o.customer is probably o.customer_id", "cat.category_id is probably cat.id", "oi.price and oi.unit_price",
                 "quantity has no table"]:
        assert must in titles, must
    fixed = r["corrected"]
    assert "FULL OUTER" not in fixed and "dep2" not in fixed and "ci2" not in fixed and "GROUP BY" not in fixed
    assert "m.id = e.manager_id" in fixed and "IS NOT NULL" in fixed and "-- check: customer_id" in fixed
    assert fixed.rstrip().endswith("(you wrote customer)") and "customer_name ASC;" in fixed
    assert [f for f in sqlcorrect.check(fixed)["findings"] if f["kind"] in ("error", "wrong")] == []


def test_a_wrong_query_gets_no_index_advice_until_its_right():
    res = analyze_sql(BROKEN)
    assert res["indexes"] == [] and res["check_query"] is None and res["speed_later"]["indexes"]
    assert res["rewritten"] == res["correct"]["corrected"]


@pytest.mark.parametrize("seed", range(1, 60))
def test_broken_queries_are_found_and_corrected(seed):
    item = sqlbugs.make(seed)
    if item is None:
        pytest.skip("no parent/child tables in this schema")
    assert sqlbugs.check(item, None) == []


def test_valid_queries_get_no_false_mistakes():
    for seed in range(1, 120):
        schema, rnd = dbfuzz.make_schema(seed), random.Random(seed)
        for _ in range(3):
            sql, _q = dbfuzz.make_query(schema, rnd)
            bad = [f["title_en"] for f in sqlcorrect.check(sql)["findings"] if f["kind"] in ("error", "wrong")]
            assert bad == [], (sql, bad)


def test_the_conversation(client, ml_mode):
    from tests.test_ml import say

    r = say(client, "I have query make joins correct in this")
    assert [a["recipe"] for a in r["agents"]] == ["sql_review"]
    goal = r["goal"]["id"]
    r = client.post("/api/message", json={"text": BROKEN, "goal_id": goal}).json()
    assert "in the card" in r["reply"] and "ALTER TABLE" not in r["reply"]
    assert len(r["reply"].splitlines()) <= 5 and "Fixed" in r["reply"]
    assert r["options"] == ["Optimize it"] and r["next"] == []                                  # no quiz buttons
    r = client.post("/api/message", json={"text": "In this query join galat tha join sahi krna tha", "goal_id": goal}).json()
    assert "'products.id'" in r["reply"] and "Self join" in r["reply"] and "'!= NULL'" not in r["reply"]
    r = client.post("/api/message", json={"text": "big query I need small query", "goal_id": goal}).json()
    assert "lines down to" in r["reply"] and "dep2" not in r["reply"].split("Removed:")[0]
    r = client.post("/api/message", json={"text": "query slow hai", "goal_id": goal}).json()
    assert "stop it running" not in r["reply"] and "ALTER TABLE" in r["reply"]      # speed advice, for the corrected query


def test_second_broken_query_school():
    r = sqlcorrect.check((Path(__file__).parent / "data" / "broken_school.sql").read_text())
    titles = " | ".join(f["title_en"] for f in r["findings"])
    for must in ["'subjects.id'", "WHERE uses percentage", "ORDER BY uses student", "aren't grouped", "INNER JOIN sub",
                 "Self join on teachers", "fees is joined twice", "'= NULL'", "SUM(f.amount) is multiplied by the marks rows",
                 "HAVING marks", "s.class is probably s.class_id", "m.max_mark is probably m.max_marks", "exam_year has no table"]:
        assert must in titles, must
    assert "m.marks and m.max_marks: two columns, or one?" in titles          # asked, never merged
    assert "m.marks," in r["corrected"] and "AND f.status IS NULL" in r["corrected"] and "LEFT JOIN fees f" in r["corrected"]
    assert {x["title_en"] for x in r["left"]} >= {"SUM(f.amount) is multiplied by the marks rows"}


def test_a_bare_column_goes_to_the_table_the_rows_belong_to():
    q = ("SELECT u.id, u.name, o.id AS order_id, o.total_amount FROM users u LEFT JOIN orders o ON u.id = o.customer_id "
         "WHERE o.status = 'paid' AND u.email != NULL ORDER BY created_at DESC;")
    fixed = sqlcorrect.check(q)["corrected"]
    assert "INNER JOIN orders o" in fixed and "u.email IS NOT NULL" in fixed and "ORDER BY o.created_at DESC;" in fixed
