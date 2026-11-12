"""The admin panel (app/admin.py) and the enquiry form (app/enquiry.py).

What must hold:
* every admin endpoint is 404 for anyone without the admin cookie; the pages have strict rules (no framing, no other code)
* an enquiry is saved and emailed; bots (hidden field, too fast) are dropped silently; bad input is refused; 3 an hour
* enquiries can be moved through statuses, noted and deleted; "Delete all my data" removes them; old ones expire
* usage counts hold numbers only: what was asked in the workspace is never stored
* the overview, people and DBaba knowledge views add up; the DBaba test box answers like the workspace
"""

from datetime import timedelta

import pytest
from sqlalchemy import inspect, text

KEY = "k" * 24
GOOD = {"name": "Asha Rao", "email": "asha@example.com", "phone": "98765 43210", "company": "Rao Traders", "role": "developer",
        "interest": ["dbaba", "workspace"], "team": "2-10", "message": "We hit deadlocks every evening.", "consent": True,
        "source": "home", "elapsed_ms": 9000}


class Mail:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, *, reply_to=None, from_name="", attachments=None):
        self.sent.append({"to": to, "subject": subject, "body": body, "reply_to": reply_to})
        return "<x>"


@pytest.fixture()
def mail(monkeypatch):
    from app import accounts, enquiry

    m = Mail()
    monkeypatch.setattr(accounts, "mailer", lambda: m)
    monkeypatch.setenv("ENQUIRY_EMAIL", "owner@example.com")
    enquiry._recent.clear()
    return m


@pytest.fixture()
def admin(client, monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", KEY)
    client.get(f"/admin?key={KEY}", follow_redirects=False)
    return client


ADMIN_GETS = ["/api/admin/overview", "/api/admin/users", "/api/admin/enquiries", "/api/admin/knowledge"]


def test_admin_is_invisible_without_the_key(client, monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", KEY)
    for path in ADMIN_GETS:
        assert client.get(path).status_code == 404, path
    assert client.post("/api/admin/knowledge/try", json={"text": "what is a join"}).status_code == 404
    assert client.post("/api/admin/enquiries/1", json={"status": "spam"}).status_code == 404
    assert client.delete("/api/admin/enquiries/1").status_code == 404
    assert client.post("/api/admin/knowledge/miss/1", json={"status": "learned"}).status_code == 404
    client.get("/admin?key=wrong-wrong-wrong-wrong", follow_redirects=False)
    assert client.get("/api/admin/overview").status_code == 404
    monkeypatch.setenv("ADMIN_KEY", "short")                      # a weak key never opens it
    client.get("/admin?key=short", follow_redirects=False)
    assert client.get("/api/admin/overview").status_code == 404


def test_admin_page_and_enquiry_page_rules(admin):
    page = admin.get("/admin")
    assert page.status_code == 200 and "admin.js" in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"] and page.headers["x-frame-options"] == "DENY"
    assert "<script>" not in page.text and "<style>" not in page.text   # nothing inline: the strict rules allow none
    form = admin.get("/enquiry")
    assert form.status_code == 200 and 'name="website"' in form.text and "enquiry.js" in form.text
    assert "frame-ancestors 'none'" in form.headers["content-security-policy"]
    for path in ADMIN_GETS:
        assert admin.get(path).status_code == 200, path


def test_enquiry_reaches_the_owner_and_the_panel(admin, mail):
    r = admin.post("/api/enquiry", json=GOOD)
    assert r.status_code == 200 and r.json() == {"ok": True}
    m = mail.sent[-1]
    assert m["to"] == "owner@example.com" and m["reply_to"] == "asha@example.com"
    assert "Asha Rao" in m["body"] and "deadlocks" in m["body"] and "/admin#enquiries" in m["body"]
    got = admin.get("/api/admin/enquiries?status=new").json()
    e = got["enquiries"][0]
    assert e["name"] == "Asha Rao" and e["company"] == "Rao Traders" and e["role"] == "Developer"
    assert any("DBaba" in i for i in e["interest"]) and got["counts"] == {"new": 1}
    up = admin.post(f"/api/admin/enquiries/{e['id']}", json={"status": "talking", "note": "call on Monday"}).json()
    assert up["status"] == "talking" and up["note"] == "call on Monday"
    assert admin.post(f"/api/admin/enquiries/{e['id']}", json={"status": "nonsense"}).status_code == 404
    assert admin.get("/api/admin/enquiries?status=new").json()["enquiries"] == []
    assert admin.delete(f"/api/admin/enquiries/{e['id']}").json() == {"ok": True}
    assert admin.get("/api/admin/enquiries").json()["enquiries"] == []


def test_enquiry_from_a_logged_in_person_is_linked_to_the_account(client, mail):
    from tests.test_accounts import new_user

    client.post("/api/account/signup", json=new_user(n=71))
    assert client.post("/api/enquiry", json=GOOD).status_code == 200
    from app.db import get_db
    from app.models import Enquiry
    assert next(get_db()).query(Enquiry).one().user_id is not None


def test_bots_are_dropped_quietly(client, mail):
    from app.db import get_db
    from app.models import Enquiry

    assert client.post("/api/enquiry", json={**GOOD, "website": "http://spam.example"}).json() == {"ok": True}
    assert client.post("/api/enquiry", json={**GOOD, "elapsed_ms": 800}).json() == {"ok": True}
    assert next(get_db()).query(Enquiry).count() == 0 and mail.sent == []


@pytest.mark.parametrize("change, says", [
    ({"name": "A"}, "name"), ({"email": "not-an-email"}, "email"), ({"consent": False}, "tick"),
    ({"message": "", "interest": []}, "what you need"),
])
def test_enquiry_validation(client, mail, change, says):
    r = client.post("/api/enquiry", json={**GOOD, **change})
    assert r.status_code == 400 and says in r.json()["error"]


def test_enquiry_cleans_what_it_keeps(admin, mail):
    admin.post("/api/enquiry", json={**GOOD, "role": "hacker", "team": "999", "interest": ["dbaba", "<script>"],
                                     "phone": "+91 98765<b>43210", "name": "Asha\x00 Rao"})
    e = admin.get("/api/admin/enquiries").json()["enquiries"][0]
    assert e["role"] == "" and e["team"] == "" and len(e["interest"]) == 1 and "<" not in e["phone"] and "\x00" not in e["name"]


def test_three_an_hour_from_one_address(client, mail):
    for _ in range(3):
        assert client.post("/api/enquiry", json=GOOD).status_code == 200
    assert client.post("/api/enquiry", json=GOOD).status_code == 400


def test_delete_all_my_data_and_old_ones_expire(client, mail):
    from app import enquiry
    from app.db import get_db
    from app.models import Enquiry, utcnow

    client.post("/api/enquiry", json=GOOD)
    client.post("/api/privacy/delete-all")
    db = next(get_db())
    assert db.query(Enquiry).count() == 0
    client.post("/api/enquiry", json=GOOD)
    row = db.query(Enquiry).one()
    row.created_at = utcnow() - timedelta(days=400)
    db.commit()
    assert enquiry.maintain(db) == 1 and db.query(Enquiry).count() == 0


def test_usage_counts_are_numbers_only(admin, mail):
    """A workspace question is counted per day; its words are stored nowhere."""
    from app import usage
    from app.db import get_db, get_engine

    db = next(get_db())
    usage.count(db, "workspace_ask")
    usage.count(db, "workspace_ask", 2)
    assert usage.series(db, "workspace_ask")[-1] == 3 and len(usage.series(db, "workspace_ask")) == 30
    marker = "zebra_marker_7731"
    r = admin.post("/api/admin/knowledge/try", json={"text": f"SELECT * FROM {marker} WHERE x = NULL"})
    assert r.status_code == 200 and r.json()["blocks"]
    eng = get_engine()
    with eng.connect() as conn:
        for t in inspect(eng).get_table_names():
            for row in conn.execute(text(f'SELECT * FROM "{t}"')):
                assert marker not in " ".join(map(str, row)), t
    ov = admin.get("/api/admin/overview").json()
    assert ov["workspace"]["workspace_ask"][-1] == 3 and len(ov["days"]) == 30


def test_overview_and_people_add_up(admin, mail, ml_mode):
    from tests.test_accounts import new_user
    from tests.test_ml import say

    say(admin, "meri website bahut slow hai example.com")
    admin.post("/api/enquiry", json=GOOD)
    admin.post("/api/account/signup", json=new_user(n=72))
    ov = admin.get("/api/admin/overview").json()
    assert ov["people"][-1] >= 1 and ov["messages"][-1] >= 1 and ov["people_week"] >= 1
    areas = {a["label"]: a for a in ov["areas"]}
    assert areas["Website checker"]["week"] >= 1
    assert ov["enquiries"]["new"] == 1 and ov["enquiries"]["per_day"][-1] == 1
    assert ov["accounts"]["total"] == 1 and ov["accounts"]["signups"][-1] == 1
    users = admin.get("/api/admin/users").json()["users"]
    assert users[0]["email"] == "asha72@example.com" and users[0]["verified"] is False


def test_dbaba_knowledge_view(admin):
    from app import learning
    from app.db import get_db

    k = admin.get("/api/admin/knowledge").json()
    assert k["dbaba"]["concepts"] > 50 and k["dbaba"]["phrasings"] > k["dbaba"]["concepts"]
    assert {e["name"] for e in k["dbaba"]["engines"]} >= {"MySQL", "PostgreSQL"}
    cur = k["curriculum"]
    assert cur["total"] == 15 and 1 <= cur["done"] <= 15 and cur["nights"][0]["done"] and cur["nights"][0]["date"]
    assert "sqlaggs" in k["dbaba"]["checkers"]
    db = next(get_db())
    learning.report_miss(db, "database", "still wrong", "SELECT * FROM t WHERE a = '<v>'", "looked fine")
    miss = admin.get("/api/admin/knowledge").json()["said_wrong"][0]
    assert miss["said"] == "still wrong" and miss["count"] == 1
    assert admin.post(f"/api/admin/knowledge/miss/{miss['id']}", json={"status": "learned"}).json() == {"ok": True}
    assert admin.get("/api/admin/knowledge").json()["said_wrong"] == []
    assert admin.post(f"/api/admin/knowledge/miss/{miss['id']}", json={"status": "deleted"}).status_code == 404


def test_test_box_answers_like_the_workspace(admin):
    from app import console

    q = "left join aur inner join mein kya farak hai"
    r = admin.post("/api/admin/knowledge/try", json={"text": q}).json()
    assert r["blocks"] == console.answer(q)[0]
    bad = admin.post("/api/admin/knowledge/try", json={"text": "SELECT name, COUNT(*) FROM users"}).json()
    assert any(b["type"] == "findings" for b in bad["blocks"])
    assert admin.post("/api/admin/knowledge/try", json={"text": "  "}).status_code == 400


def test_enquiry_open_before_launch(client, monkeypatch):
    monkeypatch.setenv("SITE_MODE", "closed")
    assert "Talk to us" in client.get("/enquiry").text
    assert client.get("/static/enquiry.js").status_code == 200
    assert client.post("/api/enquiry", json={**GOOD, "website": "x"}).status_code == 200
    assert "Coming soon" in client.get("/console").text                 # the rest of the site stays closed
