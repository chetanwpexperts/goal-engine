"""Query fundamentals (app/dbknow/core/query_fundamentals.json) and reading a query stage by stage (app/agents/sqlread.py):
every finding names the fundamental it breaks, the card shows 'How DBaba read this query', and the chat answers 'what does
this query do / ye query kya karti hai' with the reading and the fundamentals behind what's wrong."""

import re
from pathlib import Path

from app.agents import sqlread
from app.agents.sql import analyze_sql
from tests.test_ml import say, say_sql

ROOT = Path(__file__).resolve().parents[1] / "app"


def test_every_checker_topic_has_a_fundamental():
    topics = set()
    for f in ("agents/sqlcorrect.py", "agents/sqlbasics.py"):
        text = (ROOT / f).read_text(encoding="utf-8")
        topics |= set(re.findall(r'(?:Finding|F|add)\((?:"(?:error|wrong|check)"|kind)(?: if [^,]+ else "\w+")?, "([a-z_]+)"', text))
    topics -= {"learned"}                       # rules learned from the manuals carry their own reason
    assert len(topics) > 20
    funds = sqlread.fundamentals()["fundamentals"]
    mapped = {t for f in funds.values() for t in f["topics"]}
    assert topics <= mapped, sorted(topics - mapped)
    for fid, f in funds.items():
        assert f["title_en"] and f["title_hi"] and f["rule_en"] and f["rule_hi"] and f["source"].startswith("https://"), fid


def test_reading_a_join_with_totals():
    r = sqlread.read_query("SELECT c.name, COUNT(o.id) AS orders, SUM(o.amount) FROM customers c "
                           "LEFT JOIN orders o ON o.customer_id = c.id WHERE c.city = 'Pune' GROUP BY c.name HAVING COUNT(o.id) > 1 "
                           "ORDER BY orders DESC LIMIT 5")
    assert [t["name"] for t in r["tables"]] == ["customers", "orders"]
    link = r["links"][0]
    assert link["shape"] == "one-to-many" and link["many_side"] == "orders" and link["one_side"] == "customers"
    assert r["row_filters"] == ["c.city = 'Pune'"]
    assert r["grain"] == "one row per c.name" and len(r["totals"]) >= 2
    assert r["group_filters"].startswith("COUNT(o.id) > 1") and "LIMIT 5" in r["final_cut"]
    en, hi = sqlread.render(r), sqlread.render(r, True)
    assert "one customers row, many orders rows" in en and "Grain: one row per c.name" in en
    assert "kai orders rows" in hi


def test_reading_changes_and_stages():
    r = sqlread.read_query("UPDATE products SET price = price * 1.1, stock = 0 WHERE category_id = 3")
    assert r["kind"] == "update" and r["output"] == ["price = price * 1.1", "stock = 0"] and r["row_filters"] == ["category_id = 3"]
    assert "No WHERE: EVERY row!" in sqlread.render(sqlread.read_query("DELETE FROM payments"))
    r = sqlread.read_query("WITH t AS (SELECT customer_id, SUM(amount) s FROM orders GROUP BY customer_id) SELECT * FROM t WHERE s > "
                           "(SELECT AVG(s) FROM t)")
    assert "CTE t" in r["stages"] and any("subquer" in x for x in r["stages"])


def test_findings_name_their_fundamental_and_the_card_has_the_reading():
    res = analyze_sql("SELECT name FROM customers WHERE phone = NULL OR city = 'Pune' AND id > 3")
    found = res["correct"]["findings"]
    assert {f["basis"]["id"] for f in found} >= {"null_logic", "precedence"}
    assert res["reading"]["en"].startswith("- Shows: name") and "Grain:" in res["reading"]["en"] and res["reading"]["hi"]
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "How DBaba read this query" in js and "f.basis" in js


def test_chat_reads_the_query_in_english_and_hinglish(client, ml_mode):
    client.post("/api/new-chat")
    client.cookies.clear()
    r = say_sql(client, "SELECT c.name, COUNT(*) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.name")
    assert r["relation"] == "agent_run"
    r = say(client, "what does this query do?")
    assert r["relation"] == "agent_reply" and "stage by stage" in r["reply"] and "many orders rows" in r["reply"]
    assert "Outer joins keep unmatched rows" in r["reply"]          # the fundamental behind the COUNT(*) finding
    r = say(client, "ye query kya karti hai")
    assert r["relation"] == "agent_reply" and "Query aise padhi" in r["reply"] and "Buniyaad" not in r["reply"][:5]


def test_explain_after_a_slow_query_still_says_why(client, ml_mode):
    client.post("/api/new-chat")
    client.cookies.clear()
    say(client, "my database is slow")
    r = say_sql(client, "SELECT * FROM orders WHERE YEAR(created) = 2024 ORDER BY created")
    assert r["relation"] == "agent_run"
    r = say(client, "explain this query")
    assert "stage by stage" in r["reply"] and ("Why this query can be slow" in r["reply"] or "already written well" in r["reply"]
                                               or "Your query, optimised" in r["reply"])


def test_saying_it_was_wrong_is_still_kept_to_learn(client, ml_mode):
    """learn-from-misses (PR #14) keeps working after the merge with the stage-by-stage reading."""
    from app.db import _SessionLocal
    from app.models import LearnMiss

    client.post("/api/new-chat")
    client.cookies.clear()
    say_sql(client, "SELECT name FROM customers WHERE city = 'Pune'")
    r = say(client, "this answer is wrong, not what i wanted")
    assert "values hidden" in r["reply"]
    with _SessionLocal() as db:
        rows = db.query(LearnMiss).all()
    assert rows and "Pune" not in rows[-1].detail and "?" in rows[-1].detail
