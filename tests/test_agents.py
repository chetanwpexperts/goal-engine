"""Agents: assigned automatically, run on approval, results back in the goal. No internet in tests."""

import dataclasses
from datetime import datetime, timedelta

import pytest

from app.agents import core
from app.agents.safety import Page, UnsafeURL, normalize_url
from app.agents.sql import analyze_sql
from app.agents.website import check_website
from tests.test_ml import say

GOOD_HTML = ("<html><head><title>Bakery</title><meta name='viewport' content='width=device-width'>"
             "<meta name='description' content='Fresh cakes'></head><body>" + "x" * 400 +
             "<a href='tel:+911234'>Call</a><footer>© 2026</footer></body></html>")
BAD_HTML = "<html><head></head><body>" + "x" * 400 + "<footer>Copyright 2019</footer></body></html>"


def fake_fetcher(pages):
    def fetch(url, verify=True, **_):
        return pages.get(url) or Page(url=url, error="connect", error_detail="refused")
    return fetch


def test_safe_url_rules():
    assert normalize_url("mybakery.com") == "https://mybakery.com/"
    for bad in ("ftp://x.com", "http://localhost", "http://intranet", "http://x.com:8080", "javascript:alert(1)", ""):
        with pytest.raises(UnsafeURL):
            normalize_url(bad)


def test_private_addresses_are_never_fetched():
    result = check_website("http://127.0.0.1.nip.io")  # resolves to a private address
    assert result["ok"] is False or result["findings"][0]["code"] == "no_dns"


def test_website_checker_finds_real_problems():
    pages = {"https://bad.com/": Page(url="https://bad.com/", final_url="https://bad.com/", status=200, text=BAD_HTML, seconds=3.4)}
    result = check_website("bad.com", fetcher=fake_fetcher(pages), ssl_days=lambda h: 5, now=datetime(2026, 10, 21))
    codes = [f["code"] for f in result["findings"]]
    for code in ("not_mobile", "no_contact_button", "slow", "old_copyright", "no_title", "ssl_expiring"):
        assert code in codes
    assert result["score"] < 60


def test_website_checker_clean_site_and_down_site():
    pages = {"https://good.com/": Page(url="https://good.com/", final_url="https://good.com/", status=200, text=GOOD_HTML, seconds=0.4)}
    good = check_website("good.com", fetcher=fake_fetcher(pages), ssl_days=lambda h: 80, now=datetime(2026, 10, 21))
    assert good["findings"] == [] and good["score"] == 100
    down = check_website("down.com", fetcher=fake_fetcher({}), ssl_days=lambda h: None)
    assert down["findings"][0]["code"] == "down"


def test_sql_checker_finds_bugs_and_writes_fixes():
    q = ("SELECT * FROM salaries s JOIN employees e ON e.id = s.employee_id "
         "WHERE DATE(s.created_at) = '2026-10-01' AND s.status = 'paid' OR e.name LIKE '%kumar%' ORDER BY s.amount DESC")
    r = analyze_sql(q, "1 | SIMPLE | s | ALL | 120000 | Using where; Using filesort")
    titles = " ".join(f["title_en"] for f in r["findings"])
    assert "AND and OR mixed" in titles and "DATE()" in titles and "full table scan" in titles
    assert "ALTER TABLE salaries ADD INDEX idx_salaries_status_created_at (status, created_at);" in r["indexes"]
    assert "s.created_at >= '2026-10-01'" in r["rewritten"]
    assert analyze_sql("DROP TABLE users")["ok"] is False


@pytest.fixture()
def fake_web(monkeypatch):
    pages = {"https://mybakery.com/": Page(url="https://mybakery.com/", final_url="https://mybakery.com/", status=200,
                                           text=BAD_HTML, seconds=3.0)}

    def run(inputs):
        return check_website(inputs["url"], fetcher=fake_fetcher(pages), ssl_days=lambda h: 60)

    for key in ("website_check", "website_watch"):
        monkeypatch.setitem(core.RECIPES, key, dataclasses.replace(core.RECIPES[key], run=run))


def test_system_does_the_work_instead_of_giving_steps(client, ml_mode, fake_web):
    first = say(client, "meri website bahut slow hai mybakery.com")
    assert first["goal"]["intent"] == "website_problem"
    assert first["reply"].startswith("Main ye karunga")
    assert "Ye kijiye" not in first["reply"]
    agent = first["agents"][0]
    assert agent["recipe"] == "website_check" and agent["ready"] and agent["access"] == "none"
    done = say(client, "haan")
    assert done["relation"] == "agent_run"
    assert "problem mili" in done["reply"]
    run = done["agents"][0]["runs"][0]
    assert run["status"] == "done" and run["result"]["findings"]


def test_agent_asks_only_for_its_input_then_runs(client, ml_mode, fake_web):
    first = say(client, "website pe not secure likha aata hai")
    assert "Website ka address" in first["reply"]
    assert not first["agents"][0]["ready"]
    second = say(client, "mybakery.com")
    assert second["relation"] == "agent_run"
    assert second["agents"][0]["runs"]


def test_daily_watch_and_scheduler(client, ml_mode, fake_web):
    say(client, "meri website bahut slow hai mybakery.com")
    done = say(client, "haan")
    watch = say(client, done["options"][0])
    agents = {a["recipe"]: a for a in watch["agents"]}
    assert agents["website_watch"]["status"] == "active" and agents["website_watch"]["next_run_at"]

    from app.db import get_db
    from app.models import Agent
    db = next(get_db())
    agent = db.query(Agent).filter_by(recipe="website_watch").one()
    agent.next_run_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    first_claim = core.claim_due(db, datetime.utcnow())
    second_claim = core.claim_due(db, datetime.utcnow())
    assert [a.id for a in first_claim] == [agent.id] and second_claim == []


def test_sql_agent_flow_with_pasted_query(client, ml_mode):
    first = say(client, "mere database ki query bahut slow hai")
    assert first["agents"][0]["recipe"] == "sql_review"
    assert "query" in first["reply"].lower()
    second = say(client, "SELECT * FROM orders WHERE YEAR(created_at) = 2026 AND status = 'paid' OR customer_id = 5")
    assert second["relation"] == "agent_run"
    result = second["agents"][0]["runs"][0]["result"]
    assert result["indexes"] and result["rewritten"]


def test_start_endpoint_with_inputs(client, ml_mode):
    first = say(client, "query optimize karni hai database ki")
    agent_id = first["agents"][0]["id"]
    res = client.post(f"/api/agents/{agent_id}/start", json={"inputs": {"query": "select * from users where lower(email) = 'a@b.com'"}})
    assert res.status_code == 200
    assert res.json()["agents"][0]["runs"][0]["status"] == "done"
    client.cookies.clear()
    assert client.post(f"/api/agents/{agent_id}/start", json={}).status_code == 404   # someone else's agent


def test_database_request_gets_working_agent_and_honest_later_levels(client, ml_mode):
    res = say(client, "mujhe apne database mein automation chahiye roz backup aur report email pe")
    agent = res["agents"][0]
    assert agent["recipe"] == "database" and agent["mode"] == "report" and agent["available"] is True
    assert "agle level" in res["reply"] and "Backup" in res["reply"]




def test_sql_checker_does_not_claim_missing_indexes_it_cannot_see():
    q = ("SELECT s.*, c.course_name, f.amount, b.batch_name FROM Students s JOIN courses c ON c.course_id = s.course_id "
         "JOIN fee_structure f ON f.course_id = s.course_id AND f.category_type = s.category AND f.scheme_id = s.scheme_id "
         "JOIN Batches b ON b.batch_id = s.batch_id WHERE s.status = 'active' ORDER BY s.student_name")
    r = analyze_sql(q)
    assert not r["indexes_verified"] and "information_schema.STATISTICS" in r["check_query"]
    assert any("can't see your current indexes" in f["title_en"] for f in r["findings"])
    assert r["indexes"] == ["ALTER TABLE Students ADD INDEX idx_Students_status_student_name (status, student_name);",
                            "ALTER TABLE fee_structure ADD INDEX idx_fee_structure_course_id_category_type_scheme_id "
                            "(course_id, category_type, scheme_id);"]           # not 11: own primary keys and outward joins skipped
    assert set(r["assumed_primary"]) == {"courses.course_id", "Batches.batch_id"}
    r = analyze_sql(q, "CREATE TABLE `fee_structure` (`id` int, `course_id` int, PRIMARY KEY (`id`), "
                       "KEY `c` (`course_id`,`category_type`)) ENGINE=InnoDB;")
    assert r["indexes_verified"] and r["check_query"] is None and len(r["indexes"]) == 1
