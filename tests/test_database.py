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
    import tempfile
    for job in jobs:
        try:
            if job["kind"] == "export":            # like the real connector: gzip file, streamed upload
                tmp = tempfile.NamedTemporaryFile(suffix=".csv.gz", delete=False).name
                data = gc.handle(db, job, export_path=tmp)
                with open(tmp, "rb") as fh:
                    up = client.post(f"/api/connector/upload/{job['id']}", headers=auth, content=fh.read())
                assert up.status_code == 200, up.text
                body = {"ok": True, "result": data, "uploaded": True}
            else:
                body = {"ok": True, "result": gc.handle(db, job)}
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
    assert "database kahan hai" in res["reply"].lower()
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
    assert token.startswith("gec_") and token in made["command"] and "install.sh | bash" in made["command"]
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


def test_finds_database_settings_in_projects(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / ".env").write_text('APP_NAME=Shop\nDB_CONNECTION=mysql\nDB_HOST=127.0.0.1\nDB_PORT=3306\nDB_DATABASE=shop_db\nDB_USERNAME=shop\nDB_PASSWORD="p@ss word"\n')
    (tmp_path / "blog").mkdir()
    (tmp_path / "blog" / "wp-config.php").write_text("<?php\ndefine( 'DB_NAME', 'blog' );\ndefine( 'DB_USER', 'wp' );\ndefine( 'DB_PASSWORD', 's3cr3t' );\ndefine( 'DB_HOST', 'localhost:3307' );\n")
    ci = tmp_path / "portal" / "application" / "config"
    ci.mkdir(parents=True)
    (ci / "database.php").write_text("<?php\n$db['default']['hostname'] = 'localhost';\n$db['default']['username'] = 'ci';\n$db['default']['password'] = 'x';\n$db['default']['database'] = 'portal';\n$db['default']['dbdriver'] = 'mysqli';\n")
    (tmp_path / "node_app").mkdir()
    (tmp_path / "node_app" / ".env").write_text("DATABASE_URL=postgres://app:pw%40x@db.local:5433/appdb?sslmode=disable\n")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / ".env").write_text("APP_KEY=x\n")   # no database: ignored
    found = {p["database"]: p for p in gc.find_projects([str(tmp_path)])}
    assert set(found) == {"shop_db", "blog", "portal", "appdb"}
    assert found["shop_db"]["password"] == "p@ss word" and found["shop_db"]["app"] == "Laravel"
    assert found["blog"]["port"] == "3307" and found["blog"]["host"] == "localhost" and found["blog"]["app"] == "WordPress"
    assert found["portal"]["path"].endswith("portal") and found["portal"]["app"] == "CodeIgniter 3"
    assert found["appdb"]["type"] == "postgres" and found["appdb"]["password"] == "pw@x" and found["appdb"]["port"] == "5433"


def test_installer_is_served_with_server_address(client):
    res = client.get("/connector/install.sh")
    assert res.status_code == 200 and "http://testserver" in res.text and "__SERVER__" not in res.text
    assert "</dev/tty" in res.text


# ---------------------------------------------------------------- direct connection (typed on Goal Engine)
from app.agents import direct  # noqa: E402


def test_direct_refuses_private_addresses(client, monkeypatch):
    monkeypatch.delenv("DIRECT_DB_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv("APP_SECRET", "x" * 32)
    for host in ("127.0.0.1", "localhost", "10.0.0.5", "192.168.1.10", "169.254.169.254", "[::1]"):
        res = client.post("/api/connectors/direct", json={"host": host, "database": "d", "user": "u", "password": "p"})
        err = res.json()
        assert res.status_code == 400 and (err["use_command"] or "Couldn't find" in err["error"]), host
    assert direct._clean_form({"host": "https://db.example.com:3307/x", "database": "d", "user": "u"})["port"] == "3307"


def test_direct_rate_limit(client, monkeypatch):
    monkeypatch.setenv("APP_SECRET", "x" * 32)
    direct._attempts.clear()
    codes = [client.post("/api/connectors/direct", json={"host": "10.1.1.1", "database": "d", "user": "u"}).json()["error"] for _ in range(10)]
    assert "Too many tries" in codes[-1]
    direct._attempts.clear()


def test_secret_round_trip(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "y" * 32)
    token = direct.encrypt({"password": "p@ss"})
    assert "p@ss" not in token and direct.decrypt(token)["password"] == "p@ss"
    monkeypatch.setenv("APP_SECRET", "z" * 32)
    with pytest.raises(direct.DirectError):
        direct.decrypt(token)


def _local_mysql():
    try:
        socket.create_connection(("127.0.0.1", 3306), timeout=1).close()
        return True
    except OSError:
        return False


import socket  # noqa: E402


@pytest.mark.skipif(not _local_mysql(), reason="needs a local MySQL/MariaDB with the shop_db test user")
def test_direct_end_to_end_with_real_mysql(client, ml_mode, monkeypatch):
    monkeypatch.setenv("APP_SECRET", "s" * 32)
    monkeypatch.setenv("DIRECT_DB_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("DIRECT_DB_SYNC", "1")
    direct._attempts.clear()
    agent = say(client, "database se data Excel mein chahiye")["agents"][0]
    bad = client.post("/api/connectors/direct", json={"host": "127.0.0.1", "database": "shop_db", "user": "shopapp", "password": "wrong"})
    assert bad.status_code == 400 and "said no" in bad.json()["error"]
    res = client.post("/api/connectors/direct", json={"host": "127.0.0.1", "database": "shop_db", "user": "shopapp",
                                                      "password": "app pass", "agent_id": agent["id"]})
    assert res.status_code == 200, res.text
    card = res.json()["agents"][0]
    c = card["connector"]
    assert c["kind"] == "direct" and c["online"] and c["can_write"] is True and "orders" in [t["name"] for t in c["tables"]]

    from app.db import get_db
    from app.models import Connector
    s = next(get_db())
    saved = s.get(Connector, c["id"]).secret
    assert "app pass" not in saved and "shopapp" not in saved

    job = client.post(f"/api/connectors/{c['id']}/jobs", json={"kind": "query", "table": "orders"}).json()
    assert client.get(f"/api/connector-jobs/{job['id']}").json()["result"]["row_count"] >= 2
    exp = client.post(f"/api/connectors/{c['id']}/jobs", json={"kind": "export", "table": "orders"}).json()
    assert "amount" in client.get(f"/api/connector-jobs/{exp['id']}/download").text
    assert client.post(f"/api/connectors/{c['id']}/jobs", json={"kind": "query", "sql": "DELETE FROM orders"}).status_code == 400
    health = client.post(f"/api/connectors/{c['id']}/jobs", json={"kind": "health"}).json()
    assert client.get(f"/api/connector-jobs/{health['id']}").json()["status"] == "done"
    # a token can't be used to impersonate a direct connector, and disconnect forgets the password
    client.post(f"/api/connectors/{c['id']}/disconnect")
    s.expire_all()
    assert s.get(Connector, c["id"]).secret is None


def test_direct_refuses_our_own_server(client, monkeypatch):
    monkeypatch.delenv("DIRECT_DB_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv("APP_SECRET", "x" * 32)
    monkeypatch.setenv("SERVER_IP", "93.184.215.14")
    monkeypatch.setattr(direct.socket, "getaddrinfo",
                        lambda host, *a, **k: [(2, 1, 6, "", ("93.184.215.14", 3306))])
    direct._attempts.clear()
    res = client.post("/api/connectors/direct", json={"host": "mysite.example", "database": "d", "user": "u"})
    assert res.status_code == 400 and "own server" in res.json()["error"]
    direct._attempts.clear()


def test_long_poll_waits_then_returns(client, ml_mode):
    import time as _t
    token = client.post("/api/connectors", json={}).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    client.post("/api/connector/hello", headers=auth, json={"db_type": "sqlite"})
    start = _t.monotonic()
    assert client.post("/api/connector/poll?wait=0", headers=auth).json()["jobs"]   # schema job waiting: instant
    assert _t.monotonic() - start < 1
    start = _t.monotonic()
    assert client.post("/api/connector/poll?wait=1", headers=auth).json()["jobs"] == []
    assert 0.9 < _t.monotonic() - start < 3


def _local_pg():
    try:
        socket.create_connection(("127.0.0.1", 5432), timeout=1).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _local_pg(), reason="needs a local PostgreSQL with the shop_pg test database")
def test_postgres_driver_for_real(monkeypatch):
    db = gc.Database({"type": "postgres", "host": "127.0.0.1", "port": "5432", "database": "shop_pg",
                      "user": "shopapp", "password": "app pass"}).connect()
    assert db.version() and db.can_write() is True
    assert [t["name"] for t in db.schema()["tables"]] == ["orders"]
    q = db.query("SELECT id, amount, created FROM orders ORDER BY id", 50)
    assert q["row_count"] == 2 and q["rows"][1][1] == "250.50"
    assert db.export("SELECT * FROM orders")["row_count"] == 2
    assert db.explain("SELECT * FROM orders WHERE id = 1")["rows"]
    assert db.health()["checks"]
    # even if the safety check were bypassed, the transaction itself refuses writes
    cur = db.cursor()
    with pytest.raises(Exception, match="read-only"):
        cur.execute("DELETE FROM orders")
    db.done()
    assert db.query("SELECT count(*) FROM orders", 5)["rows"][0][0] == 2


@pytest.mark.skipif(not _local_mysql(), reason="needs a local MySQL/MariaDB")
def test_mysql_transaction_refuses_writes_even_without_the_check():
    db = gc.Database({"type": "mysql", "host": "127.0.0.1", "port": "3306", "database": "shop_db",
                      "user": "shopapp", "password": "app pass"}).connect()
    cur = db.cursor()
    with pytest.raises(Exception, match="(?i)read.only"):
        cur.execute("DELETE FROM orders")
    db.done()
    assert db.query("SELECT count(*) FROM orders", 5)["rows"][0][0] >= 2


def test_secret_columns_are_hidden():
    cols = ["ID", "user_login", "user_pass", "user_email", "apiToken", "api_key", "passenger_name", "compass", "password_hash",
            "remember_token", "session_id", "card_number", "hashtag_count"]
    hidden = {cols[i] for i in gc.secret_columns(cols)}
    assert hidden == {"user_pass", "apiToken", "api_key", "password_hash", "remember_token", "session_id", "card_number"}


def test_preview_and_export_never_contain_passwords(tmp_path):
    import sqlite3 as _sq
    path = tmp_path / "wp.sqlite"
    con = _sq.connect(path)
    con.executescript("CREATE TABLE wp_users (ID INTEGER PRIMARY KEY, user_login TEXT, user_pass TEXT, user_email TEXT);"
                      "INSERT INTO wp_users VALUES (1, 'admin', '$P$Bsecrethash', 'a@b.com');")
    con.commit(); con.close()
    db = gc.Database({"type": "sqlite", "database": str(path)}).connect()
    q = db.query("SELECT * FROM wp_users", 10)
    assert "$P$Bsecrethash" not in str(q) and q["hidden_columns"] == ["user_pass"] and q["rows"][0][1] == "admin"
    assert "$P$Bsecrethash" not in db.export("SELECT * FROM wp_users")["csv"]


def test_live_view_hides_values_and_is_one_at_a_time(client, ml_mode, shop_db):
    assert gc.shape("SELECT * FROM users WHERE email = 'a@b.com' AND id = 42") == "SELECT * FROM users WHERE email = ? AND id = ?"
    db = gc.Database({"type": "sqlite", "database": shop_db}).connect()
    live = db.live()
    assert live["running"]["available"] is False and live["running"]["reason"]
    agent = say(client, "mere mysql database ki tables dikhao")["agents"][0]
    token = client.post("/api/connectors", json={"agent_id": agent["id"]}).json()["token"]
    client.post("/api/connector/hello", headers={"Authorization": f"Bearer {token}"}, json={"db_type": "sqlite"})
    pump(client, token, db)
    cid = client.get("/api/connectors").json()["connectors"][0]["id"]
    a = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "live"}).json()
    b = client.post(f"/api/connectors/{cid}/jobs", json={"kind": "live"}).json()
    assert a["id"] == b["id"]                       # the screen asking twice doesn't make two jobs
    pump(client, token, db)
    assert client.get(f"/api/connector-jobs/{a['id']}").json()["status"] == "done"
    assert all(i["kind"] != "live" for i in client.get(f"/api/connectors/{cid}/activity").json()["items"])
