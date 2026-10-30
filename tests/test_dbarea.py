"""The locked database area: 404 for guests, email code to open, 15-minute idle lock wipes typed passwords."""

import socket
from datetime import timedelta

import pytest

from tests.test_accounts import FakeMailer, mail, new_user  # noqa: F401 - fixture

DB = {"type": "mysql", "host": "127.0.0.1", "database": "shop_db", "user": "shopapp", "password": "app pass"}


def _mysql_up():
    try:
        socket.create_connection(("127.0.0.1", 3306), timeout=1).close()
        return True
    except OSError:
        return False


@pytest.fixture
def locked(monkeypatch, mail):
    monkeypatch.setenv("DB_AREA_LOCK", "on")
    monkeypatch.setenv("DIRECT_DB_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("DIRECT_DB_SYNC", "1")
    from app.agents import direct
    direct._attempts.clear()
    return mail


def _verified(client, mail, n):
    client.post("/api/account/signup", json=new_user(n=n))
    client.post("/api/account/verify", json={"code": mail.last_code(f"asha{n}@example.com")})


def _open(client, mail, n):
    client.post("/api/db-area/code")
    r = client.post("/api/db-area/open", json={"code": mail.last_code(f"asha{n}@example.com")})
    assert r.status_code == 200 and r.json()["state"] == "open", r.json()


def test_guests_get_404_everywhere(client, locked):
    for method, path in [("post", "/api/connectors/direct"), ("get", "/api/connectors"), ("post", "/api/connectors"),
                         ("get", "/api/connector-jobs/1"), ("post", "/api/agents/1/php-connector"),
                         ("post", "/api/agents/1/connect/1"), ("get", "/api/db-area"), ("post", "/api/db-area/code")]:
        r = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
        assert r.status_code == 404, (path, r.status_code)
    assert client.get("/api/connector-info").status_code == 200         # our server's IP for instructions stays public


def test_unverified_then_locked_then_open(client, locked):
    client.post("/api/account/signup", json=new_user(n=21))
    r = client.post("/api/connectors/direct", json=DB)
    assert r.status_code == 403 and r.json()["db_area"] == "verify"
    client.post("/api/account/verify", json={"code": locked.last_code("asha21@example.com")})
    r = client.post("/api/connectors/direct", json=DB)
    assert r.status_code == 403 and r.json()["db_area"] == "locked"
    wrong = client.post("/api/db-area/open", json={"code": "000000"})
    assert wrong.status_code == 400
    _open(client, locked, 21)
    assert client.get("/api/db-area").json()["state"] == "open"
    assert client.get("/api/connectors").status_code == 200


@pytest.mark.skipif(not _mysql_up(), reason="needs local MySQL")
def test_connect_emails_the_owner_logs_it_and_idle_wipes_the_password(client, locked):
    from app.db import _SessionLocal
    from app.models import Connector, DbConnectLog, UserSession, utcnow
    from app import dbarea

    _verified(client, locked, 22)
    _open(client, locked, 22)
    r = client.post("/api/connectors/direct", json=DB)
    assert r.status_code == 200, r.json()
    assert any("database was connected" in subj for _, subj, _ in locked.sent)
    log = client.get("/api/db-area/log").json()["log"]
    assert log[0]["ok"] is True and log[0]["database"] == "shop_db" and "pass" not in str(log)
    with _SessionLocal() as db:
        c = db.get(Connector, r.json()["connector"]["id"])
        assert c.secret                                          # encrypted, only while open
        for s in db.query(UserSession).all():
            s.db_seen_at = utcnow() - timedelta(minutes=16)      # 16 idle minutes
        db.commit()
    assert client.post("/api/connectors/direct", json=DB).json()["db_area"] == "locked"
    with _SessionLocal() as db:
        c = db.get(Connector, r.json()["connector"]["id"])
        assert c.secret is None and c.status == "disconnected"
        assert db.query(DbConnectLog).count() >= 1
        assert dbarea.maintain(db)["db_areas_locked"] == 0       # already locked by the request


@pytest.mark.skipif(not _mysql_up(), reason="needs local MySQL")
def test_logout_wipes_typed_passwords(client, locked):
    from app.db import _SessionLocal
    from app.models import Connector

    _verified(client, locked, 23)
    _open(client, locked, 23)
    cid = client.post("/api/connectors/direct", json=DB).json()["connector"]["id"]
    client.post("/api/account/logout")
    with _SessionLocal() as db:
        assert db.get(Connector, cid).secret is None


def test_no_probing_many_servers(client, locked, monkeypatch):
    from app.agents import direct

    monkeypatch.setattr(direct, "public_ip", lambda host, port: (_ for _ in ()).throw(direct.DirectError("Couldn't find the server")))
    _verified(client, locked, 24)
    _open(client, locked, 24)
    for i in range(5):
        client.post("/api/connectors/direct", json={**DB, "host": f"db{i}.example.com"})
        direct._attempts.clear()
    r = client.post("/api/connectors/direct", json={**DB, "host": "db9.example.com"})
    assert r.status_code == 400 and "different database servers" in r.json()["error"]
    assert client.post("/api/connectors/direct", json={**DB, "host": "db1.example.com"}).json()["error"] != r.json()["error"]


def test_lock_now(client, locked):
    _verified(client, locked, 25)
    _open(client, locked, 25)
    assert client.post("/api/db-area/lock").json()["state"] == "locked"
    assert client.get("/api/connectors").json()["db_area"] == "locked"


@pytest.mark.skipif(not _mysql_up(), reason="needs local MySQL")
def test_account_holder_gets_an_answer_checked_on_their_database(client, locked, ml_mode):
    import pymysql
    from tests.test_ml import say

    conn = pymysql.connect(host="127.0.0.1", user="shopapp", password="app pass", database="shop_db", autocommit=True)
    with conn.cursor() as cur:
        cur.execute("INSERT IGNORE INTO wp_options (option_name, option_value) VALUES ('livecheck', 'x')")
        try:
            cur.execute("INSERT INTO wp_options (option_name, option_value) VALUES ('livecheck', 'y')")
        except pymysql.err.IntegrityError as exc:
            err = f"ERROR {exc.args[0]} (23000): {exc.args[1]}"
    conn.close()
    guest = say(client, err)["reply"]
    assert "not checked on your database" in guest and "Create a free account" in guest

    _verified(client, locked, 26)
    _open(client, locked, 26)
    assert client.post("/api/connectors/direct", json=DB).status_code == 200
    client.post("/api/new-chat")
    proved = say(client, err)["reply"]
    assert "Checked on your database" in proved and "livecheck" in proved and "wp_options" in proved, proved

    client.post("/api/db-area/lock")                 # locked: back to "based on what you pasted", without the signup line
    client.post("/api/new-chat")
    again = say(client, err)["reply"]
    assert "not checked on your database" in again and "Create a free account" not in again and "Open your database area" in again


@pytest.mark.skipif(not _mysql_up(), reason="needs local MySQL")
def test_password_reset_wipes_typed_passwords_at_once(client, locked):
    from app.db import _SessionLocal
    from app.models import Connector

    _verified(client, locked, 27)
    _open(client, locked, 27)
    cid = client.post("/api/connectors/direct", json=DB).json()["connector"]["id"]
    client.post("/api/account/forgot", json={"email": "asha27@example.com"})
    client.post("/api/account/reset", json={"email": "asha27@example.com", "code": locked.last_code("asha27@example.com"),
                                            "password": "another-good-pass-1"})
    with _SessionLocal() as db:
        assert db.get(Connector, cid).secret is None


def test_launch_switch_keeps_connecting_closed_for_everyone(client, locked, monkeypatch, ml_mode):
    from tests.test_ml import say

    monkeypatch.setenv("DB_CONNECT", "off")
    assert client.get("/api/account/me").json()["db_connect"] is False
    reply = say(client, "ERROR 1040 (HY000): Too many connections")["reply"]
    assert "opens in a few days" in reply and "Create a free account" not in reply
    _verified(client, locked, 28)
    r = client.post("/api/connectors/direct", json=DB)
    assert r.status_code == 403 and r.json()["db_area"] == "soon"
    assert client.post("/api/db-area/code").json()["db_area"] == "soon"
    assert client.get("/api/db-area").json()["state"] == "soon"


def test_connect_question_doesnt_promise_a_closed_connection(client, monkeypatch, ml_mode):
    from tests.test_ml import say

    monkeypatch.setenv("DB_CONNECT", "off")
    reply = say(client, "How do I connect my database?")["reply"]
    assert "opens in a few days" in reply and "step or two" not in reply
