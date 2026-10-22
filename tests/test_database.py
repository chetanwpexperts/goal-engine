"""Database agent: sentence -> agent -> connector (real script, SQLite) -> tables, preview, export, health, report."""

import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest

import app.main as main
from app.agents import database as dbagent
from tests.test_ml import say

_spec = importlib.util.spec_from_file_location("goal_connector", Path(__file__).resolve().parents[1] / "connector" / "goal_connector.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)


class Mail:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, *, reply_to=None, from_name="", attachments=None):
        self.sent.append({"to": to, "subject": subject, "body": body, "attachments": attachments or []})
        return "<x@test>"


@pytest.fixture()
def shop_db(tmp_path):
    path = tmp_path / "shop.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, city TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL, created TEXT);
        CREATE TABLE logs (msg TEXT);
    """)
    con.executemany("INSERT INTO customers (name, city) VALUES (?, ?)", [(f"Customer {i}", "Panchkula") for i in range(120)])
    con.executemany("INSERT INTO orders (customer_id, amount, created) VALUES (?, ?, '2026-10-01')", [(i, i * 10.5) for i in range(1, 80)])
    con.commit()
    con.close()
    return str(path)


def pump(client, token, db):
    """One connector cycle against the real API, as the script on the user's server would do."""
    auth = {"Authorization": f"Bearer {token}"}
    jobs = client.post("/api/connector/poll", headers=auth).json()["jobs"]
    for job in jobs:
        try:
            data = gc.handle(db, job)
            csv_text = data.pop("csv", None)
            body = {"ok": True, "result": data, **({"csv": csv_text} if csv_text is not None else {})}
        except Exception as exc:  # noqa: BLE001
            body = {"ok": False, "error": str(exc)}
        assert client.post(f"/api/connector/result/{job['id']}", headers=auth, json=body).status_code == 200
    return jobs


def test_read_only_checks_on_both_sides():
    for bad in ("DELETE FROM orders", "select 1; drop table x", "UPDATE t SET a=1", "SELECT * INTO OUTFILE '/tmp/x' FROM t",
                "select sleep(100)", "CALL proc()", "with x as (delete from t returning *) select * from x"):
        with pytest.raises(dbagent.DatabaseError):
            dbagent.read_only_sql(bad)
        with pytest.raises(ValueError):
            gc.safe_sql(bad)
    ok = "SELECT name, 'drop table; update' AS note FROM customers -- delete later"
    assert dbagent.read_only_sql(ok) and gc.safe_sql(ok)
    with pytest.raises(dbagent.DatabaseError):
        dbagent.table_preview_sql("users; drop")


def test_sentence_builds_database_agent_not_sheet_automation(client, ml_mode):
    res = say(client, "muje mera data excel sheet me dalna hai database se")
    assert res["relation"] == "agent_plan"
    agent = res["agents"][0]
    assert agent["recipe"] == "database" and agent["mode"] == "export" and agent["available"]
    assert "connector" in res["reply"].lower()
    # Pasted SQL still goes to the query checker, not the connector
    client.post("/api/new-chat")
    res2 = say(client, "ye query slow hai SELECT * FROM orders WHERE YEAR(created) = 2024")
    assert all(a["recipe"] != "database" for a in res2["agents"])


def test_connector_end_to_end(client, ml_mode, shop_db, monkeypatch):
    mail = Mail()
    monkeypatch.setattr(main, "mailer", lambda: mail)
    agent = say(client, "mere mysql database ki tables dikhao")["agents"][0]
    made = client.post("/api/connectors", json={"name": "Shop", "agent_id": agent["id"]}).json()
    token = made["token"]
    assert token.startswith("gec_") and token in made["command"] and "goal_connector.py setup" in made["command"]
    assert made["connector"]["status"] == "waiting"
    assert client.post("/api/connector/poll", headers={"Authorization": "Bearer gec_wrong"}).status_code == 401
    assert token not in str(client.get("/api/connectors").json())   # never shown again

    db = gc.Database({"type": "sqlite", "database": shop_db}).connect()
    hello = client.post("/api/connector/hello", headers={"Authorization": f"Bearer {token}"},
                        json={"db_type": "sqlite", "db_name": "shop", "version": gc.VERSION, "can_write": False})
    assert hello.status_code == 200
    assert pump(client, token, db)[0]["kind"] == "schema"

    conn = client.get("/api/connectors").json()["connectors"][0]
    assert conn["online"] and {t["name"]: t["rows"] for t in conn["tables"]} == {"customers": 120, "logs": 0, "orders": 79}

    cid = conn["id"]
    preview = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "query", "table": "customers"}).json()
    pump(client, token, db)
    done = client.get(f"/api/connector-jobs/{preview['id']}").json()
    assert done["status"] == "done" and done["result"]["row_count"] == 50 and done["result"]["columns"] == ["id", "name", "city"]

    export = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "export", "table": "customers"}).json()
    pump(client, token, db)
    file = client.get(f"/api/connector-jobs/{export['id']}/download")
    assert file.status_code == 200 and file.text.count("\n") == 121 and "Customer 119" in file.text

    bad = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "query", "sql": "DELETE FROM customers"})
    assert bad.status_code == 400
    custom = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "query", "sql": "SELECT city, COUNT(*) n FROM customers GROUP BY city"}).json()
    health = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "health"}).json()
    pump(client, token, db)
    assert client.get(f"/api/connector-jobs/{custom['id']}").json()["result"]["rows"] == [["Panchkula", 120]]
    checks = client.get(f"/api/connector-jobs/{health['id']}").json()["result"]["checks"]
    assert any("primary key" in c["text"] and "logs" in c["text"] for c in checks)

    # Another visitor can't see or use this connector
    client.cookies.clear()
    assert client.post(f"/api/connectors/{cid}/jobs", json={"kind": "health"}).status_code == 400
    assert client.get(f"/api/connector-jobs/{export['id']}/download").status_code == 404


def test_scheduled_report_emails_csv(client, ml_mode, shop_db, monkeypatch):
    mail = Mail()
    monkeypatch.setattr(main, "mailer", lambda: mail)
    agent = say(client, "roz subah database se orders ki report email karo")["agents"][0]
    assert agent["mode"] == "report"
    token = client.post("/api/connectors", json={"agent_id": agent["id"]}).json()["token"]
    db = gc.Database({"type": "sqlite", "database": shop_db}).connect()
    client.post("/api/connector/hello", headers={"Authorization": f"Bearer {token}"}, json={"db_type": "sqlite"})
    pump(client, token, db)

    assert client.post(f"/api/agents/{agent['id']}/db-report", json={"table": "orders"}).status_code == 400  # no verified email
    client.post("/api/owner/code", json={"email": "owner@shop.com", "business": "Shop"})
    client.post("/api/owner/verify", json={"code": re.search(r"\d{6}", mail.sent[-1]["body"]).group(0)})
    res = client.post(f"/api/agents/{agent['id']}/db-report", json={"table": "orders", "every": "day", "at": "09:00"})
    assert res.status_code == 200 and "09:00" in res.json()["reply"]

    from app.db import get_db
    from app.models import Agent

    gen = main.app.dependency_overrides.get(get_db, get_db)()
    s = next(gen)
    report = s.query(Agent).filter_by(recipe="db_report").one()
    main._run_db_report(s, report)
    pump(client, token, db)
    sent = mail.sent[-1]
    assert sent["to"] == "owner@shop.com" and sent["attachments"][0][0].endswith(".csv")
    assert sent["attachments"][0][1].decode("utf-8-sig").count("\n") == 80
