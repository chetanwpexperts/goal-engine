"""Free accounts: signup with email code, login limits, sessions on every device, reset, delete."""

import re

import pytest

from app import accounts


class FakeMailer:
    configured = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, **_):
        self.sent.append((to, subject, body))
        return "id"

    def last_code(self, to):
        for t, _, body in reversed(self.sent):
            if t == to:
                return re.search(r"\b(\d{6})\b", body).group(1)
        raise AssertionError("no email to " + to)


@pytest.fixture
def mail(monkeypatch):
    m = FakeMailer()
    monkeypatch.setattr(accounts, "_mailer", m)
    monkeypatch.setenv("APP_SECRET", "t" * 32)
    from app.db import get_engine
    from app.models import AuthAttempt
    from sqlalchemy import delete
    with get_engine().begin() as c:          # each test starts with fresh limits
        c.execute(delete(AuthAttempt))
    return m


def new_user(n=1, **over):
    d = {"full_name": f"Asha Verma {n}", "phone": "+91 98765 43210", "email": f"asha{n}@example.com", "location": "Chandigarh",
         "username": f"asha{n}", "password": "pink-river-42x", "why": "I run a small shop and keep all stock in Excel and MySQL."}
    d.update(over)
    return d


def signup(client, **over):
    return client.post("/api/account/signup", json=new_user(**over))


def test_signup_verify_and_me(client, mail):
    r = signup(client, n=1)
    assert r.status_code == 200 and r.json()["code_sent"] is True
    assert r.json()["user"]["email_verified"] is False
    assert "password" not in str(r.json()["user"]) and "hash" not in str(r.json())
    me = client.get("/api/account/me").json()["user"]
    assert me["username"] == "asha1" and me["why"].startswith("I run")
    bad = client.post("/api/account/verify", json={"code": "000000" if mail.last_code("asha1@example.com") != "000000" else "111111"})
    assert bad.status_code == 400 and "tries left" in bad.json()["error"]
    ok = client.post("/api/account/verify", json={"code": mail.last_code("asha1@example.com")})
    assert ok.status_code == 200 and ok.json()["user"]["email_verified"] is True


def test_signup_checks_every_field(client, mail):
    for over, field in [({"full_name": "A"}, "full_name"), ({"phone": "12"}, "phone"), ({"email": "nope"}, "email"),
                        ({"location": ""}, "location"), ({"username": "a"}, "username"), ({"why": "shop"}, "why"),
                        ({"password": "short"}, "password"), ({"password": "aaaaaaaaaaaa"}, "password")]:
        r = signup(client, n=2, **over)
        assert r.status_code == 400 and r.json()["field"] == field, (over, r.json())


def test_duplicate_email_and_username(client, mail):
    assert signup(client, n=3).status_code == 200
    client.post("/api/account/logout")
    assert signup(client, n=3).json()["field"] == "email"
    assert signup(client, n=4, username="asha3").json()["field"] == "username"


def test_code_dies_after_five_wrong_tries(client, mail):
    signup(client, n=5)
    right = mail.last_code("asha5@example.com")
    wrong = "123456" if right != "123456" else "654321"
    for _ in range(5):
        client.post("/api/account/verify", json={"code": wrong})
    r = client.post("/api/account/verify", json={"code": right})
    assert r.status_code == 400 and "expired" in r.json()["error"]


def test_login_logout_and_wrong_password_lock(client, mail):
    signup(client, n=6)
    client.post("/api/account/logout")
    assert client.get("/api/account/me").json()["user"] is None
    unknown = client.post("/api/account/login", json={"login": "nobody@example.com", "password": "pink-river-42x"})
    wrong = client.post("/api/account/login", json={"login": "asha6", "password": "wrong-password-1"})
    assert unknown.json()["error"] == wrong.json()["error"]          # never says which accounts exist
    for _ in range(4):
        client.post("/api/account/login", json={"login": "asha6", "password": "wrong-password-1"})
    locked = client.post("/api/account/login", json={"login": "ASHA6@example.com", "password": "pink-river-42x"})
    assert locked.status_code == 429 and "locked" in locked.json()["error"]


def test_too_many_logins_from_one_address(client, mail):
    for i in range(20):
        client.post("/api/account/login", json={"login": f"x{i}", "password": "whatever-123"})
    r = client.post("/api/account/login", json={"login": "x", "password": "whatever-123"})
    assert r.status_code == 429


def test_account_keeps_guest_chats_and_shows_them_on_another_device(client, mail, ml_mode):
    from tests.test_ml import say
    say(client, "My Excel total is wrong")
    before = [g["id"] for g in client.get("/api/state").json()["goals"]]
    signup(client, n=7)
    assert [g["id"] for g in client.get("/api/state").json()["goals"]] == before                   # the guest's chats came with the account
    client.post("/api/account/logout")
    assert before and [g["id"] for g in client.get("/api/state").json()["goals"]] == []   # logged out: a new guest, the chats aren't here
    client.cookies.clear()
    client.post("/api/account/login", json={"login": "asha7", "password": "pink-river-42x"})
    assert [g["id"] for g in client.get("/api/state").json()["goals"]] == before                   # "another device": same chats


def test_forgot_and_reset_password(client, mail):
    signup(client, n=8)
    client.post("/api/account/logout")
    same = client.post("/api/account/forgot", json={"email": "missing@example.com"}).json()
    assert client.post("/api/account/forgot", json={"email": "asha8@example.com"}).json() == same
    code = mail.last_code("asha8@example.com")
    r = client.post("/api/account/reset", json={"email": "asha8@example.com", "code": code, "password": "new-blue-sky-77"})
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is True
    client.post("/api/account/logout")
    assert client.post("/api/account/login", json={"login": "asha8", "password": "pink-river-42x"}).status_code == 401
    assert client.post("/api/account/login", json={"login": "asha8", "password": "new-blue-sky-77"}).status_code == 200


def test_delete_all_removes_the_account(client, mail):
    signup(client, n=9)
    r = client.post("/api/privacy/delete-all").json()
    assert r["deleted"]["account"] == 1
    client.cookies.clear()
    assert client.post("/api/account/login", json={"login": "asha9", "password": "pink-river-42x"}).status_code == 401


def test_session_cookie_is_safe(client, mail):
    r = signup(client, n=10)
    c = r.headers.get("set-cookie", "")
    assert "ge_sess=" in c and "httponly" in c.lower() and "samesite=lax" in c.lower()
    client.cookies.clear()
    client.cookies.set("ge_sess", "made-up")
    assert client.get("/api/account/me").json()["user"] is None


def test_no_daily_cap_in_ml_mode_but_floods_stop(client, ml_mode):
    from app import limits
    from tests.test_ml import say
    old = limits.BURST["visitor"]
    limits.BURST["visitor"] = 26                          # (the address and total limits stay high for tests)
    try:
        for _ in range(25):                               # more than the old 25-a-day cap
            say(client, "My Excel total is wrong")
        r = client.post("/api/message", json={"text": "My Excel total is wrong"})
        r = client.post("/api/message", json={"text": "My Excel total is wrong"})
        assert r.status_code == 429 and "very fast" in r.json()["error"]
    finally:
        limits.BURST["visitor"] = old


def test_admin_sees_new_users_with_dates(client, ml_mode, mail, monkeypatch):
    """The private admin page lists signups newest first, with when they joined and whether they verified."""
    monkeypatch.setenv("ADMIN_KEY", "k" * 24)
    assert client.get("/api/admin/users").status_code == 404                  # no admin cookie: hidden
    assert signup(client, n=71).status_code == 200
    client.post("/api/account/verify", json={"code": mail.last_code("asha71@example.com")})
    client.post("/api/account/logout")
    client.cookies.clear()
    assert signup(client, n=72).status_code == 200                           # never verified
    client.cookies.clear()
    client.get(f"/admin?key={'k' * 24}", follow_redirects=False)
    out = client.get("/api/admin/users").json()
    mine = [u for u in out["users"] if u["email"] in ("asha71@example.com", "asha72@example.com")]
    assert [u["email"] for u in mine] == ["asha72@example.com", "asha71@example.com"]        # newest first
    assert mine[0]["verified"] is False and mine[1]["verified"] is True
    assert mine[1]["created_at"] and mine[1]["location"] == "Chandigarh" and "Excel" in mine[1]["why"]
    assert mine[1]["whatsapp"].startswith("https://wa.me/91") and "password" not in str(out) and "pink-river" not in str(out)
    assert out["counts"]["today"] >= 2 and out["counts"]["all"] >= 2 and out["counts"]["unverified"] >= 1
    today = client.get("/api/admin/users?view=today").json()["users"]
    assert {"asha71@example.com", "asha72@example.com"} <= {u["email"] for u in today}
    unv = client.get("/api/admin/users?view=unverified").json()["users"]
    assert all(not u["verified"] for u in unv) and "asha72@example.com" in {u["email"] for u in unv}


def test_admin_learning_shows_dates_and_wrong_answers(client, ml_mode, monkeypatch):
    from app import learning
    from app.db import get_db

    monkeypatch.setenv("ADMIN_KEY", "k" * 24)
    db = next(get_db())
    learning.report_miss(db, "database", "still wrong", "SELECT name FROM t WHERE city = ?", "No problems found.")
    client.get(f"/admin?key={'k' * 24}", follow_redirects=False)
    out = client.get("/api/admin/learning").json()
    miss = [m for m in out["misses"] if m["said"] == "still wrong"][0]
    assert miss["first_seen"] and miss["last_seen"] and miss["detail"].startswith("SELECT")
    assert all(r["first_seen"] and r["last_seen"] for r in out["requests"])
    assert client.post(f"/api/admin/learning/miss/{miss['id']}", json={"status": "learned"}).json() == {"ok": True}
    assert not [m for m in client.get("/api/admin/learning").json()["misses"] if m["id"] == miss["id"]]
