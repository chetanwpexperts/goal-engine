"""Evidence, not guesses, chooses a database cause."""

from app.dbknow.diagnosis import evaluate
from app.dbknow.service import playbook


def _result(columns, rows):
    return {"columns": columns, "rows": rows}


def test_idle_connections_choose_leak_and_quote_evidence():
    issue = playbook("too-many-connections")
    checks = [d for d in issue["diagnose"] if d["engine"] == "mysql"]
    raw = [
        _result(["Variable_name", "Value"], [["Threads_connected", "142"]]),
        _result(["Variable_name", "Value"], [["max_connections", "151"]]),
        _result(["USER", "n", "sleeping"], [["shopapp", 120, 120]]),
    ]
    got = evaluate(issue, checks, raw)
    assert {c["id"] for c in got["likely_causes"]} == {"leak", "traffic"}
    assert any("120 connections" in text for text in got["found"])
    assert any("94" in text and "%" in text for text in got["found"])
    assert got["likely_causes"][0]["fix"] and got["verify"]


def test_healthy_evidence_does_not_guess():
    issue = playbook("too-many-connections")
    checks = [d for d in issue["diagnose"] if d["engine"] == "mysql"]
    raw = [
        _result(["Variable_name", "Value"], [["Threads_connected", "3"]]),
        _result(["Variable_name", "Value"], [["max_connections", "151"]]),
        _result(["USER", "n", "sleeping"], [["shopapp", 2, 1]]),
    ]
    got = evaluate(issue, checks, raw)
    assert got["likely_causes"] == []
    assert got["no_problem_found"] is True
    assert got["conclusion"] == "No problem found by these checks"


def test_failed_check_is_not_called_healthy():
    issue = playbook("too-many-connections")
    checks = [d for d in issue["diagnose"] if d["engine"] == "mysql"]
    got = evaluate(issue, checks, [{"error": "permission denied"}] * 3)
    assert got["likely_causes"] == [] and got["no_problem_found"] is False
