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
    for ask in ("can you optimize this query", "I am asking about optimize", "isko fast kaise karu"):
        r = say(client, ask)
        assert "your work, city" not in r["reply"] and "steps are above" not in r["reply"], (ask, r["reply"])
        assert "s.created_at >= '2026-10-01'" in r["reply"], (ask, r["reply"])
        assert "DATE()" in r["reply"] or "range" in r["reply"].lower(), r["reply"]
        assert "information_schema.STATISTICS" in r["reply"] and "ADD INDEX" in r["reply"]
