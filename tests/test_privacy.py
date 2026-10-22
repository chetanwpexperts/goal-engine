"""What we promise on /privacy, checked: nothing from users' databases is kept, secrets are encrypted,
customer details expire, and 'Delete all my data' really deletes everything."""

import re
from datetime import timedelta

import pytest
from sqlalchemy import func, select

import app.main as main
from app import privacy
from app.db import get_db
from app.models import (Agent, AgentRun, Connector, ConnectorJob, Goal, Message, OwnerContact, SentEmail, dumps, loads,
                        utcnow)
from tests.test_automation import FakeMailer, sheet
from tests.test_database import gc, pump, shop_db  # noqa: F401 - fixture
from tests.test_ml import say


@pytest.fixture()
def mail(monkeypatch):
    m = FakeMailer()
    m.send = lambda to, subject, body, **kw: (m.sent.append({"to": to, "subject": subject, "body": body}), "<x>")[1]
    monkeypatch.setattr(main, "mailer", lambda: m)
    return m


def session():
    return next(get_db())


def test_pasted_customer_rows_are_encrypted(client, ml_mode, mail):
    agent = say(client, "jinka payment baaki hai unko har Monday reminder chala jaye")["agents"][0]
    client.post(f"/api/agents/{agent['id']}/preview", json={"inputs": {"csv_text": sheet()}})
    raw = session().get(Agent, agent["id"]).inputs
    assert "ravi@example.com" not in raw and "Ravi Kumar" not in raw and "csv_enc" in raw


def test_sent_log_keeps_no_addresses_and_run_details_expire(client, ml_mode, mail):
    agent = say(client, "jinka payment baaki hai unko har Monday reminder chala jaye")["agents"][0]
    client.post(f"/api/agents/{agent['id']}/preview", json={"inputs": {"csv_text": sheet()}})
    client.post("/api/owner/code", json={"email": "owner@shop.com", "business": "Shop"})
    client.post("/api/owner/verify", json={"code": re.search(r"\d{6}", mail.sent[-1]["body"]).group(0)})
    client.post(f"/api/agents/{agent['id']}/activate")
    from app.agents import automation
    s = session()
    a = s.get(Agent, agent["id"])
    automation.run(s, a, True, preview=False, mailer=mail)
    logged = list(s.scalars(select(SentEmail.to_email)))
    assert logged and all(e.startswith("h:") and "@" not in e for e in logged)
    # same person isn't emailed twice inside the gap, even though we only keep a fingerprint
    before = len(mail.sent)
    automation.run(s, a, True, preview=False, mailer=mail)
    assert len([m for m in mail.sent[before:] if m["to"] != "owner@shop.com"]) == 0

    runs = list(s.scalars(select(AgentRun).where(AgentRun.agent_id == a.id)))
    assert any("ravi@example.com" in (r.result or "") for r in runs)
    privacy.maintain(s, utcnow() + timedelta(hours=25))
    s.expire_all()
    for r in s.scalars(select(AgentRun).where(AgentRun.agent_id == a.id)):
        assert "ravi@example.com" not in (r.result or "") and "Ravi" not in (r.result or "")
    privacy.maintain(s, utcnow() + timedelta(days=31))
    assert s.scalar(select(func.count()).select_from(SentEmail)) == 0


def test_database_results_are_wiped(client, ml_mode, shop_db):  # noqa: F811
    agent = say(client, "mere mysql database ki tables dikhao")["agents"][0]
    token = client.post("/api/connectors", json={"agent_id": agent["id"]}).json()["token"]
    db = gc.Database({"type": "sqlite", "database": shop_db}).connect()
    client.post("/api/connector/hello", headers={"Authorization": f"Bearer {token}"}, json={"db_type": "sqlite"})
    pump(client, token, db)
    cid = client.get("/api/connectors").json()["connectors"][0]["id"]
    q = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "query", "table": "customers"}).json()
    e = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "export", "table": "customers"}).json()
    pump(client, token, db)
    s = session()
    assert "Customer 5" in s.get(ConnectorJob, q["id"]).result
    path = s.get(ConnectorJob, e["id"]).file_path

    privacy.maintain(s, utcnow() + timedelta(minutes=16))
    s.expire_all()
    assert "Customer" not in s.get(ConnectorJob, q["id"]).result
    assert s.get(ConnectorJob, e["id"]).file_path == path          # export still downloadable for the hour
    privacy.maintain(s, utcnow() + timedelta(minutes=61))
    s.expire_all()
    assert s.get(ConnectorJob, e["id"]).file_path is None
    import os
    assert not os.path.exists(path)
    # table names and counts stay, so the card keeps working
    assert client.get("/api/connectors").json()["connectors"][0]["tables"]


def test_delete_all_my_data_covers_earlier_sessions(client, ml_mode, mail):
    say(client, "meri website slow hai example.com")
    client.post("/api/owner/code", json={"email": "owner@shop.com", "business": "Shop"})
    client.post("/api/connectors", json={"name": "x"})
    first = client.cookies.get("ge_vid")
    client.post("/api/new-chat")
    client.cookies.delete("ge_vid")
    say(client, "jinka payment baaki hai unko har Monday reminder chala jaye")
    second = client.cookies.get("ge_vid")
    assert first and second and first != second

    res = client.post("/api/privacy/delete-all")
    assert res.status_code == 200
    s = session()
    for model, col in ((Goal, Goal.visitor_id), (Message, Message.visitor_id), (Agent, Agent.visitor_id),
                       (OwnerContact, OwnerContact.visitor_id), (Connector, Connector.visitor_id)):
        assert s.scalar(select(func.count()).select_from(model).where(col.in_([first, second]))) == 0, model


def test_forged_earlier_ids_are_ignored(client, ml_mode):
    say(client, "meri website slow hai example.com")
    victim = client.cookies.get("ge_vid")
    client.cookies.clear()
    client.cookies.set("ge_prev", f"{victim}.deadbeef")
    say(client, "hello")
    client.post("/api/privacy/delete-all")
    s = session()
    assert s.scalar(select(func.count()).select_from(Goal).where(Goal.visitor_id == victim)) > 0


def test_inactive_visitors_and_unused_passwords_expire(client, ml_mode):
    say(client, "meri website slow hai example.com")
    vid = client.cookies.get("ge_vid")
    s = session()
    c = Connector(visitor_id=vid, name="x", token_hash="t" * 64, token_hint="direct", kind="direct", secret="enc", status="online")
    s.add(c)
    s.commit()
    privacy.maintain(s, utcnow() + timedelta(days=31))
    s.expire_all()
    assert s.get(Connector, c.id).secret is None
    privacy.maintain(s, utcnow() + timedelta(days=91))
    assert s.scalar(select(func.count()).select_from(Goal).where(Goal.visitor_id == vid)) == 0


def test_privacy_page(client):
    res = client.get("/privacy")
    assert res.status_code == 200 and "Delete all my data" in res.text


def test_activity_log_has_no_values_and_live_data_expires(client, ml_mode, shop_db):  # noqa: F811
    agent = say(client, "mere mysql database ki tables dikhao")["agents"][0]
    token = client.post("/api/connectors", json={"agent_id": agent["id"]}).json()["token"]
    db = gc.Database({"type": "sqlite", "database": shop_db}).connect()
    client.post("/api/connector/hello", headers={"Authorization": f"Bearer {token}"}, json={"db_type": "sqlite"})
    pump(client, token, db)
    cid = client.get("/api/connectors").json()["connectors"][0]["id"]
    client.post(f"/api/connectors/{cid}/jobs", json={"kind": "query", "sql": "SELECT * FROM customers WHERE name = 'Customer 7' AND id > 3"})
    client.post(f"/api/connectors/{cid}/jobs", json={"kind": "export", "table": "orders"})
    pump(client, token, db)
    items = client.get(f"/api/connectors/{cid}/activity").json()["items"]
    text = str(items)
    assert "Customer 7" not in text and "name = ?" in text and "id > ?" in text and "Excel · orders" in text
    assert all(i["status"] == "done" and i["ms"] is not None for i in items)
    s = session()
    s.add(ConnectorJob(connector_id=cid, kind="live", status="done", result=dumps({"running": [["SELECT secret"]]}),
                       finished_at=utcnow()))
    s.commit()
    privacy.maintain(s, utcnow() + timedelta(minutes=3))
    assert s.scalar(select(func.count()).select_from(ConnectorJob).where(ConnectorJob.kind == "live")) == 0
