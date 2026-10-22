"""One sentence -> planned agent -> preview -> verified owner -> runs on schedule. Nothing really sends."""

import re
from datetime import date, datetime, timedelta

import pytest

import app.main as main
from app.agents import automation
from app.agents.blocks import map_fields, parse_csv, select_rows, sheet_csv_url
from app.agents.planner import plan
from app.agents.spec import next_run, validate
from tests.test_ml import say


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, *, reply_to=None, from_name=""):
        self.sent.append({"to": to, "subject": subject, "body": body, "reply_to": reply_to, "from": from_name})
        return f"<{len(self.sent)}@test>"


def sheet(today=None):
    today = today or date.today()
    d = lambda n: (today + timedelta(days=n)).strftime("%d/%m/%Y")  # noqa: E731
    return ("Customer Name,Email Address,Amount Due,Due Date,Status\n"
            f"Ravi Kumar,ravi@example.com,2500,{d(2)},pending\n"
            f"Sita Devi,sita@example.com,1200,{d(-1)},pending\n"
            f"Aman Gupta,aman@example.com,800,{d(1)},paid\n"
            f"No Mail,,500,{d(1)},pending\n"
            f"Later Person,later@example.com,900,{d(20)},pending\n")


def test_planner_understands_common_requests():
    cases = {
        "jinka payment baaki hai unko har Monday reminder chala jaye": ("payment", {"every": "week", "weekday": 0, "at": "10:00"}),
        "fees ki yaad parents ko har mahine 5 tarikh ko bhejo": ("fees", {"every": "month", "day": 5, "at": "10:00"}),
        "patients ko appointment se ek din pehle shaam 7 baje reminder bhejo": ("appointment", {"every": "day", "at": "19:00"}),
        "send payment reminders every friday at 4 pm": ("payment", {"every": "week", "weekday": 4, "at": "16:00"}),
        "roz subah 9 baje mujhe pending payments ki report chahiye": ("dues_report", {"every": "day", "at": "09:00"}),
    }
    for text, (template, schedule) in cases.items():
        spec = plan(text)
        assert spec and spec["template"] == template and spec["schedule"] == schedule, text
    assert plan("jo customer 15 din se nahi aaye unko automatic message jaye")["days"] == 15
    for not_automation in ("meri website slow hai", "teen credit card hai emi nahi bhar pa raha",
                           "database ka roz backup automatic ho"):
        assert plan(not_automation) is None, not_automation


def test_validator_blocks_unsafe_plans():
    good = plan("jinka payment baaki hai unko har Monday reminder chala jaye")
    assert validate(good) == []
    bad = dict(good, send_to="owner")
    assert validate(bad)
    assert validate(dict(good, rule="delete_everything"))
    assert validate(dict(good, schedule={"every": "minute", "at": "10:00"}))
    assert validate(dict(good, fields=["name"]))  # no email column for customer messages


def test_columns_are_matched_by_meaning_and_rows_selected():
    table = parse_csv(sheet(date(2026, 10, 22)))
    fields = map_fields(table.columns, ["name", "email", "amount", "due_date", "status"])
    assert fields == {"name": "Customer Name", "email": "Email Address", "amount": "Amount Due",
                      "due_date": "Due Date", "status": "Status"}
    rows = select_rows(table, fields, "due_soon", 3, today=date(2026, 10, 22))
    assert [r["Customer Name"] for r in rows] == ["Ravi Kumar", "Sita Devi", "No Mail"]  # paid and far-off skipped


def test_google_sheet_links_become_csv_links():
    assert sheet_csv_url("https://docs.google.com/spreadsheets/d/abc123/edit#gid=42") == \
        "https://docs.google.com/spreadsheets/d/abc123/export?format=csv&gid=42"
    assert sheet_csv_url("https://docs.google.com/spreadsheets/d/e/2PACX-x/pubhtml") == \
        "https://docs.google.com/spreadsheets/d/e/2PACX-x/pub?output=csv"


def test_schedule_times_are_india_time():
    monday_10_ist = next_run({"every": "week", "weekday": 0, "at": "10:00"}, datetime(2026, 10, 22, 6, 0))  # Thu 11:30 IST
    assert monday_10_ist == datetime(2026, 10, 26, 4, 30)  # Mon 10:00 IST in UTC
    assert next_run({"every": "day", "at": "09:00"}, datetime(2026, 10, 22, 2, 0)) == datetime(2026, 10, 22, 3, 30)


@pytest.fixture()
def fake_mail(monkeypatch):
    m = FakeMailer()
    monkeypatch.setattr(main, "mailer", lambda: m)
    return m


def test_full_flow_preview_verify_activate_and_run(client, ml_mode, fake_mail):
    first = say(client, "jinka payment baaki hai unko har Monday reminder chala jaye")
    assert first["relation"] == "agent_plan"
    agent = first["agents"][0]
    assert agent["automation"] and "Payment reminders" in first["reply"] and "Har Somvar 10:00" in first["reply"]

    preview = client.post(f"/api/agents/{agent['id']}/preview", json={"inputs": {"csv_text": sheet()}}).json()
    result = preview["agents"][0]["runs"][0]["result"]
    assert result["preview"] and [m["to"] for m in result["messages"]] == ["ravi@example.com", "sita@example.com"]
    assert "2,500" in result["messages"][0]["body"] and result["skipped"][0]["reason"] == "no valid email"
    assert fake_mail.sent == []  # a preview never sends

    assert client.post(f"/api/agents/{agent['id']}/activate").status_code == 400  # owner not verified yet

    assert client.post("/api/owner/code", json={"email": "owner@shop.com", "business": "Kumar Traders"}).status_code == 200
    code = re.search(r"\d{6}", fake_mail.sent[-1]["body"]).group(0)
    assert client.post("/api/owner/verify", json={"code": "000000" if code != "000000" else "111111"}).status_code == 400
    assert client.post("/api/owner/verify", json={"code": code}).status_code == 200

    on = client.post(f"/api/agents/{agent['id']}/activate").json()
    assert on["agents"][0]["status"] == "active" and on["agents"][0]["next_run_at"]

    from app.db import get_db
    from app.models import Agent
    db = next(get_db())
    row = db.get(Agent, agent["id"])
    before = len(fake_mail.sent)
    run = automation.run(db, row, True, preview=False, mailer=fake_mail, scheduled=True)
    customer_mail = [m for m in fake_mail.sent[before:] if m["to"] != "owner@shop.com"]
    assert run.status == "done" and [m["to"] for m in customer_mail] == ["ravi@example.com", "sita@example.com"]
    assert all(m["reply_to"] == "owner@shop.com" and m["from"] == "Kumar Traders" for m in customer_mail)
    assert any(m["to"] == "owner@shop.com" for m in fake_mail.sent[before:])  # summary to the owner

    again = len(fake_mail.sent)
    automation.run(db, row, True, preview=False, mailer=fake_mail)
    assert [m for m in fake_mail.sent[again:] if m["to"] != "owner@shop.com"] == []  # never twice within 20 hours


def test_missing_columns_are_explained(client, ml_mode, fake_mail):
    first = say(client, "jinka payment baaki hai unko har Monday reminder chala jaye")
    res = client.post(f"/api/agents/{first['agents'][0]['id']}/preview", json={"inputs": {"csv_text": "Naam,Phone\nRavi,98765\n"}}).json()
    run = res["agents"][0]["runs"][0]
    assert run["status"] == "failed" and "Email" in run["summary"] and "Naam, Phone" in run["summary"]


def test_report_preview(client, ml_mode, fake_mail):
    first = say(client, "roz subah 9 baje mujhe pending payments ki report chahiye")
    res = client.post(f"/api/agents/{first['agents'][0]['id']}/preview", json={"inputs": {"csv_text": sheet()}}).json()
    report = res["agents"][0]["runs"][0]["result"]["report"]
    assert "Ravi Kumar" in report["body"] and fake_mail.sent == []


def test_sheet_link_in_chat_triggers_preview(client, ml_mode, fake_mail, monkeypatch):
    from app.agents import blocks
    from app.agents.safety import Page

    monkeypatch.setattr(blocks, "fetch", lambda url, **_: Page(url=url, final_url=url, status=200, text=sheet()))
    say(client, "jinka payment baaki hai unko har Monday reminder chala jaye")
    res = say(client, "ye lo https://docs.google.com/spreadsheets/d/abc123/edit")
    assert res["relation"] == "agent_run" and "Preview" in res["reply"]


def test_daily_cap(client, ml_mode, fake_mail, monkeypatch):
    monkeypatch.setattr(automation, "MAX_PER_DAY", 1)
    first = say(client, "jinka payment baaki hai unko har Monday reminder chala jaye")
    agent_id = first["agents"][0]["id"]
    client.post(f"/api/agents/{agent_id}/preview", json={"inputs": {"csv_text": sheet()}})
    client.post("/api/owner/code", json={"email": "owner@shop.com"})
    client.post("/api/owner/verify", json={"code": re.search(r"\d{6}", fake_mail.sent[-1]["body"]).group(0)})
    from app.db import get_db
    from app.models import Agent
    db = next(get_db())
    run = automation.run(db, db.get(Agent, agent_id), True, preview=False, mailer=fake_mail)
    result = __import__("json").loads(run.result)
    assert result["sent"] == 1 and result["held_for_cap"] == 1
