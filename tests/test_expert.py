"""Expert hand-off: offered when no agent can do it, contact goes to Chetan by email with a WhatsApp link,
only Chetan's private link sees the list."""

import pytest

import app.main as main
from app import expert
from tests.test_ml import say


class Mail:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, *, reply_to=None, from_name="", attachments=None):
        self.sent.append({"to": to, "subject": subject, "body": body})
        return "<x>"


@pytest.fixture()
def mail(monkeypatch):
    m = Mail()
    monkeypatch.setattr(main, "mailer", lambda: m)
    monkeypatch.setenv("EXPERT_EMAIL", "chetan@example.com")
    expert._recent.clear()
    return m


def test_offered_only_when_no_agent(client, ml_mode):
    gst = say(client, "mujhe GST registration karwana hai, kaise hoga")
    assert gst["offer_expert"] is True and not gst["agents"]
    client.post("/api/new-chat")
    client.cookies.delete("ge_vid")
    site = say(client, "meri website bahut slow hai example.com")
    assert site["offer_expert"] is False and site["agents"]


def test_request_emails_chetan_with_whatsapp_link(client, ml_mode, mail):
    goal = say(client, "mujhe GST registration karwana hai, kaise hoga")["goal"]
    r = client.post("/api/expert", json={"goal_id": goal["id"], "need": "GST registration karwana hai, kapde ki dukaan",
                                         "name": "Ravi Kumar", "phone": "98765 43210", "contact": "whatsapp", "consent": True})
    assert r.status_code == 200, r.text
    m = mail.sent[-1]
    assert m["to"] == "chetan@example.com" and "Ravi Kumar" in m["body"] and "+919876543210" in m["body"]
    assert "https://wa.me/919876543210?text=" in m["body"]
    assert client.get("/api/expert").json()["requests"][0]["status"] == "new"


def test_validation_consent_bots_and_limits(client, ml_mode, mail):
    base = {"need": "Need help with my website", "name": "A", "phone": "9876543210", "contact": "whatsapp", "consent": True}
    assert client.post("/api/expert", json={**base, "consent": False}).status_code == 400
    assert client.post("/api/expert", json={**base, "phone": "123"}).status_code == 400
    assert client.post("/api/expert", json={**base, "website": "http://spam"}).status_code == 400
    assert client.post("/api/expert", json={**base, "contact": "email", "email": "bad"}).status_code == 400
    for _ in range(3):
        assert client.post("/api/expert", json=base).status_code == 200
    assert client.post("/api/expert", json=base).status_code == 400          # 3 a day


def test_admin_page_needs_private_key(client, ml_mode, mail, monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", "a" * 24)
    client.post("/api/expert", json={"need": "Website banwani hai", "name": "Sita", "phone": "+44 7700 900123",
                                     "contact": "call", "consent": True})
    assert client.get("/admin").status_code == 404
    assert client.get("/api/admin/requests").status_code == 404
    client.get("/admin?key=wrong", follow_redirects=False)
    assert client.get("/api/admin/requests").status_code == 404
    client.get(f"/admin?key={'a' * 24}", follow_redirects=False)
    reqs = client.get("/api/admin/requests?status=new").json()["requests"]
    assert reqs[0]["name"] == "Sita" and reqs[0]["phone"] == "+447700900123"
    up = client.post(f"/api/admin/requests/{reqs[0]['id']}", json={"status": "talking", "note": "called, wants quote"})
    assert up.json()["status"] == "talking"


def test_delete_all_removes_expert_requests(client, ml_mode, mail):
    client.post("/api/expert", json={"need": "Website banwani hai", "name": "Sita", "phone": "9876543210",
                                     "contact": "whatsapp", "consent": True})
    client.post("/api/privacy/delete-all")
    from app.db import get_db
    from app.models import ExpertRequest
    assert next(get_db()).query(ExpertRequest).count() == 0


def test_phone_cleaning():
    assert expert.clean_phone("098765 43210") == "+919876543210"
    assert expert.clean_phone("+1 (415) 555-0100") == "+14155550100"
    assert expert.clean_phone("0044 7700 900123") == "+447700900123"
    assert expert.clean_phone("12") == ""


def test_lost_card_and_fatty_liver_get_real_answers_not_sales(client, ml_mode):
    card = say(client, "mera atm card kho gya hai kaise milega")
    assert card["goal"]["intent"] == "lost_card" and "block" in card["reply"].lower() and card["offer_expert"] is False
    assert "seekh" not in card["reply"]
    client.post("/api/new-chat"); client.cookies.delete("ge_vid")
    liver = say(client, "mera fatty lever hai kaise thik kru")
    assert liver["goal"]["intent"] == "liver_health" and "doctor" in liver["reply"].lower() and liver["offer_expert"] is False
