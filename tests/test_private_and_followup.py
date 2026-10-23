"""The browser never learns how the brain read a message; and after a query check, 'optimize it' gets the optimised query."""

from app.config import get_settings  # noqa: F401
from tests.test_ml import say

QUERY = ("SELECT s.*, c.course_name, f.amount FROM Students s JOIN courses c ON c.course_id = s.course_id "
         "JOIN fee_structure f ON f.course_id = s.course_id AND f.scheme_id = s.scheme_id "
         "WHERE DATE(s.created_at) = '2026-10-01' AND s.status = 'active' ORDER BY s.student_name")


def test_live_site_sends_no_brain_internals(client, ml_mode, monkeypatch):
    monkeypatch.setenv("EXPOSE_INTERNALS", "")
    r = client.post("/api/message", json={"text": "My database is very slow, please check it and fix it"}).json()
    assert "provider" not in r and "intent" not in r["goal"] and "confidence" not in r["goal"]
    assert "interpreted_problem" not in r["goal"] and r["goal"]["title"]
    for a in r["agents"]:
        assert "confidence" not in (a.get("brain") or {})
    assert client.get("/api/health").json() == {"ok": True}
    js = client.get("/static/app.js").text
    assert "// " not in js[:5000] and len(js) < len(open("app/static/app.js", encoding="utf-8").read())
    assert client.get("/openapi.json").status_code == 404


def test_optimize_after_a_query_check(client, ml_mode):
    say(client, "My database is very slow, please check it and fix it")
    r = say(client, QUERY)
    assert r["relation"] == "agent_run"
    r = say(client, "can you optimize this query")
    assert "s.created_at >= '2026-10-01'" in r["reply"] and "ADD INDEX" in r["reply"], r["reply"]
    assert "information_schema.STATISTICS" in r["reply"]
    seen = {r["reply"]}
    for ask in ("I am asking about optimize", "isko fast kaise karu"):
        r = say(client, ask)
        assert "your work, city" not in r["reply"] and "steps are above" not in r["reply"], (ask, r["reply"])
        assert "ADD INDEX" in r["reply"] or "INDEX" in r["reply"], (ask, r["reply"])
        assert r["reply"] not in seen, "a different answer when they ask again"
        seen.add(r["reply"])


def test_real_conversation_optimize_then_what_changed(client, ml_mode):
    """A real one: a report query pasted from PHP code, then 'optimize it', then 'what did you change?'."""
    q = open("tests/data/student_fee_query.txt", encoding="utf-8").read()
    say(client, "My database is very slow, please check it and fix it")
    r = say(client, q)
    assert r["relation"] == "agent_run"
    res = r["agents"][0]["runs"][0]["result"]
    assert len(res["indexes"]) == 2 and "centers.id" in res["assumed_primary"]          # not 11
    assert "What did you change in my query?" in r["next"], r["next"]                 # the likely next question
    r1 = say(client, "can you optimize the query?")
    assert "already written well" in r1["reply"] and "LIMIT" in r1["reply"] and '";' not in r1["reply"], r1["reply"]
    assert r1["relation"] == "agent_reply"
    r2 = say(client, "what you change? what is the diffrence in my orignal query and your optimized query?")
    assert "didn't change your query's text" in r2["reply"] and r2["reply"] != r1["reply"], r2["reply"]
    r3 = say(client, "why is it slow")
    assert "Why this query can be slow" in r3["reply"] and "temporary table" in r3["reply"]
    r4 = say(client, "why is it slow")
    assert r4["reply"] != r3["reply"]                                                   # never the same answer twice
