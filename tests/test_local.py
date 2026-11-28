"""S4: DBaba's own screen and local API (app/local), against the sandbox MariaDB and PostgreSQL.

What is proved here:
* the contract (docs/dbaba-local-contract.md): every endpoint, its shape, its status codes;
* who may call what: API key, session after the screen password, setup token only from 127.0.0.1 and only before
  setup, 409 after it, reset; wrong passwords lock the login; Host header guard; JSON-only cookie changes;
* the settings file the installer and Docker already write is read as it is (scrypt screen password, plain database
  block), and only an API key's SHA-256 is ever stored;
* setup on both engines: detect finds them, test explains failures in plain words (lab: MariaDB answers 1044 for a
  database that doesn't exist), save stores the database and starts the first scan, the scan finds a planted table
  without a primary key on each engine;
* jobs and alerts: listing, changing, due times, raise once / clear / acknowledge;
* the real process: `python -m app.products.dbaba.local serve` prints the setup token, listens only on 127.0.0.1, and goes through
  setup over HTTP.
"""

import datetime as dt
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
from starlette.testclient import TestClient

from app.products.dbaba.local import dbconn, report
from app.products.dbaba.local.config import Config
from app.products.dbaba.local.jobs import Jobs
from app.products.dbaba.local.server import make_app

MARIA = {"engine": "mariadb", "host": "127.0.0.1", "port": 3306, "database": "shop_db", "user": "shopapp", "password": "app pass"}
PG = {"engine": "postgresql", "host": "127.0.0.1", "port": 5432, "database": "shop_pg", "user": "shopapp", "password": "app pass"}


def _up(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


needs_maria = pytest.mark.skipif(not _up(3306), reason="MariaDB test database not running")
needs_pg = pytest.mark.skipif(not _up(5432), reason="PostgreSQL test database not running")


def installer_file(path, password="screen pass 1", database=None):
    """Exactly what deploy/onprem/install.sh writes."""
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
    data = {"version": 1, "port": 8765,
            "screen_password": {"algorithm": "scrypt", "salt": salt.hex(), "hash": digest.hex(), "n": 16384, "r": 8, "p": 1},
            "database": database, "api_keys": [], "updates": {"enabled": True}, "alerts": {"email": None}}
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)
    return path


@pytest.fixture()
def fresh(tmp_path):
    """A desktop 'Try it now' start: no file yet, a setup token."""
    app = make_app(str(tmp_path / "dbaba.json"), 8765, "TOKEN-1", announce=lambda line: None)
    c = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
    return app, c, tmp_path / "dbaba.json"


def _db(d):
    return {"type": d["engine"], "host": d["host"], "port": d["port"], "name": d["database"], "user": d["user"], "password": d["password"]}


# ---------------------------------------------------------------- who may call what
def test_health_is_open_and_everything_else_needs_a_key(fresh):
    app, c, _ = fresh
    assert c.get("/api/health").json() == {"ok": True, "version": c.get("/api/health").json()["version"]}
    for method, path in (("get", "/api/state"), ("get", "/api/report"), ("get", "/api/report.html"), ("post", "/api/report/scan"),
                         ("get", "/api/jobs"), ("post", "/api/jobs/scan"), ("post", "/api/alerts/x/ack"), ("post", "/api/message"),
                         ("post", "/api/new-chat"), ("post", "/api/setup/detect"), ("post", "/api/setup/reset")):
        assert getattr(c, method)(path).status_code == 401, path
    assert c.get("/api/state", headers={"Authorization": "Bearer dbk_wrong"}).status_code == 401


def test_setup_token_only_from_loopback_and_only_before_setup(fresh):
    app, c, _ = fresh
    H = {"X-DBaba-Setup": "TOKEN-1"}
    assert c.get("/api/state", headers=H).json()["setup_done"] is False
    assert c.get("/api/state", headers={"X-DBaba-Setup": "TOKEN-2"}).status_code == 401
    remote = TestClient(app, base_url="http://127.0.0.1:8765", client=("10.0.0.9", 40000))
    assert remote.post("/api/setup/detect", headers=H).status_code == 401          # the token is no use from another machine
    assert c.post("/api/setup/detect", headers=H).status_code == 200


def test_host_header_guard_and_json_only_cookie_changes(tmp_path):
    cfg = installer_file(tmp_path / "dbaba.json")
    app = make_app(str(cfg), 8765, None, announce=lambda line: None)
    evil = TestClient(app, base_url="http://attacker.example:8765")
    assert evil.get("/api/health").status_code == 403                              # DNS rebinding can't reach it
    c = TestClient(app, base_url="http://localhost:8765")
    assert c.get("/api/health").status_code == 200
    assert c.post("/api/login", json={"password": "screen pass 1"}).status_code == 200
    assert c.post("/api/report/scan", data={"x": "1"}).status_code == 415          # a cross-site form post
    assert c.post("/api/report/scan", json={}).status_code == 200
    r = c.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"


def test_login_reads_the_installers_password_and_locks_after_five_misses(tmp_path):
    cfg = installer_file(tmp_path / "dbaba.json")
    app = make_app(str(cfg), 8765, None, announce=lambda line: None)
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    for _ in range(5):
        assert c.post("/api/login", json={"password": "nope nope"}).status_code == 401
    assert c.post("/api/login", json={"password": "screen pass 1"}).status_code == 429   # locked, even with the right one
    app.state.fails.clear()
    r = c.post("/api/login", json={"password": "screen pass 1"})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert c.get("/api/state").status_code == 200
    c.post("/api/logout", json={})
    assert c.get("/api/state").status_code == 401


def test_a_fresh_file_has_no_password_so_no_login(fresh):
    app, c, path = fresh
    assert c.post("/api/login", json={"password": "anything1"}).status_code == 409
    assert oct(path.stat().st_mode & 0o777) == "0o600"


# ---------------------------------------------------------------- setup on both engines
@pytest.mark.parametrize("d", [pytest.param(MARIA, marks=needs_maria), pytest.param(PG, marks=needs_pg)], ids=["mariadb", "postgresql"])
def test_full_setup_then_report_jobs_alerts(fresh, d):
    app, c, path = fresh
    H = {"X-DBaba-Setup": "TOKEN-1"}
    found = c.post("/api/setup/detect", headers=H).json()["found"]
    assert any(f["engine"] == d["engine"] and f["port"] == d["port"] for f in found), found
    bad = c.post("/api/setup/test", headers=H, json={**d, "password": "wrong"}).json()
    assert bad["ok"] is False and "user and password" in bad["reason"]
    ok = c.post("/api/setup/test", headers=H, json=d).json()
    assert ok["ok"] is True and ok["version"]
    assert c.post("/api/setup/save", headers=H, json=d).json() == {"ok": False, "reason": "Choose a screen password (at least 8 characters)."}
    assert c.post("/api/setup/save", headers=H, json={**d, "screen_password": "short"}).json()["ok"] is False
    saved = c.post("/api/setup/save", headers=H, json={**d, "screen_password": "screen pass 2"}).json()
    key = saved["api_key"]
    assert saved["ok"] is True and key.startswith("dbk_")
    on_disk = json.loads(path.read_text())
    assert key not in path.read_text() and on_disk["api_keys"][0]["hash"] == hashlib.sha256(key.encode()).hexdigest()
    assert on_disk["setup_done"] is True and on_disk["database"]["name"] == d["database"]
    assert c.post("/api/setup/detect", headers=H).status_code == 409                # setup is closed now
    K = {"Authorization": f"Bearer {key}"}
    for _ in range(60):
        r = c.get("/api/report", headers=K).json()
        if r["at"]:
            break
        time.sleep(0.5)
    assert r["engine"] == d["engine"] and 0 <= r["score"] <= 100 and r["sections"]
    st = c.get("/api/state", headers=K).json()
    assert st["setup_done"] and st["database"]["engine"] == d["engine"] and st["last_scan"]["score"] == r["score"]
    assert "password" not in json.dumps(st["database"])
    jobs = c.get("/api/jobs", headers=K).json()["jobs"]
    assert {j["id"] for j in jobs} == {"health", "scan"}
    j = c.post("/api/jobs/scan", headers=K, json={"enabled": False, "at": "03:15"}).json()
    assert j["enabled"] is False and j["at"] == "03:15"
    assert c.post("/api/jobs/scan", headers=K, json={"enabled": True, "at": "25:00"}).status_code == 400
    assert c.post("/api/jobs/nope", headers=K, json={"enabled": True, "at": None}).status_code == 404
    a = app.state.jobs.raise_alert("test", "warning", "A test alert", "text")
    assert app.state.jobs.raise_alert("test", "warning", "A test alert", "text") is None   # raised once until seen
    assert c.post(f"/api/alerts/{a['id']}/ack", headers=K).json() == {"ok": True}
    assert c.post("/api/alerts/missing/ack", headers=K).status_code == 404
    html = c.get("/api/report.html", headers=K)
    assert html.headers["content-disposition"].startswith("attachment") and "<script" not in html.text
    assert c.post("/api/setup/reset", headers=K).json() == {"ok": True}
    assert c.get("/api/state", headers=K).json()["setup_done"] is False and app.state.token


@needs_maria
def test_mariadb_says_1044_for_a_missing_database():
    """Lab (MariaDB 10.11): a database that doesn't exist gives this user 'access denied to database' (1044), so the
    reason must say both possibilities, not 'wrong password'."""
    r = dbconn.test(_db({**MARIA, "database": "no_such_db_s4"}))
    assert r["ok"] is False and "doesn't exist" in r["reason"] and "no rights" in r["reason"]


@needs_pg
def test_postgres_missing_database():
    r = dbconn.test(_db({**PG, "database": "no_such_db_s4"}))
    assert r["ok"] is False and "no database named 'no_such_db_s4'" in r["reason"]


def test_nothing_listening():
    r = dbconn.test(_db({**MARIA, "port": 1}))
    assert r["ok"] is False and "Nothing answered at 127.0.0.1:1" in r["reason"]


@pytest.mark.parametrize("d,ddl,drop", [
    pytest.param(MARIA, "CREATE TABLE s4_lab_nokey (a INT)", "DROP TABLE IF EXISTS s4_lab_nokey", marks=needs_maria),
    pytest.param(PG, "CREATE TABLE s4_lab_nokey (a INT)", "DROP TABLE IF EXISTS s4_lab_nokey", marks=needs_pg)], ids=["mariadb", "postgresql"])
def test_scan_finds_a_table_without_primary_key_and_its_fix_runs(d, ddl, drop):
    """The planted table is found on each engine, and the suggested ALTER really adds the key (run on a copy, then
    dropped): the fix is lab-proven, not just text."""
    db = _db(d)
    from tests.fuzz.db import Engine, MYSQL, PG as PGCFG

    eng = Engine(MYSQL if d["engine"] == "mariadb" else PGCFG).open()
    eng.run(drop)
    eng.run(ddl)
    eng.run("INSERT INTO s4_lab_nokey VALUES (1), (2)")
    try:
        rep = report.scan(db)
        items = [i for s in rep["sections"] for i in s["items"] if i["id"] == "nopk_s4_lab_nokey"]
        assert items and items[0]["severity"] == "warning" and items[0]["fix"]["needs_approval"] is True
        eng.run(items[0]["fix"]["sql"].rstrip(";"))
        assert sorted(r[0] for r in eng.run("SELECT id FROM s4_lab_nokey")) == [1, 2]    # the key was added and filled
        again = report.scan(db)
        assert not any(i["id"] == "nopk_s4_lab_nokey" for s in again["sections"] for i in s["items"])
    finally:
        eng.run(drop)


def test_unreachable_database_is_one_critical_item():
    rep = report.scan(_db({**MARIA, "port": 1}))
    assert rep["sections"][0]["items"][0]["severity"] == "critical" and rep["score"] == 75


# ---------------------------------------------------------------- jobs
def test_jobs_due_times(tmp_path):
    cfg = Config(tmp_path / "dbaba.json")
    j = Jobs(cfg)
    s = j.settings()
    now = dt.datetime(2026, 11, 27, 10, 0, tzinfo=dt.timezone.utc)
    assert j.due("health", s["health"], now, {}) is True
    assert j.due("health", s["health"], now, {"health": {"at": (now - dt.timedelta(minutes=2)).isoformat()}}) is False
    assert j.due("health", s["health"], now, {"health": {"at": (now - dt.timedelta(minutes=6)).isoformat()}}) is True
    off = {**s["scan"], "enabled": False}
    assert j.due("scan", off, now, {}) is False


def test_alert_clears_when_the_problem_goes(tmp_path):
    j = Jobs(Config(tmp_path / "dbaba.json"))
    j.raise_alert("unreachable", "critical", "down", "x")
    j.clear("unreachable", "The database is reachable again", "ok")
    al = j.public_alerts()
    assert al[0]["level"] == "info" and al[1]["acknowledged"] is True and set(al[0]) == {"id", "level", "title", "text", "at", "acknowledged"}


@needs_maria
def test_health_job_raises_and_clears(tmp_path):
    cfg = Config(tmp_path / "dbaba.json")
    cfg.update(lambda d: d.update(database={**_db(MARIA), "port": 1}, setup_done=True))
    j = Jobs(cfg)
    j.health()
    assert j.connected is False and j.alerts()[0]["key"] == "unreachable"
    cfg.update(lambda d: d.update(database=_db(MARIA)))
    j.health()
    assert j.connected is True and any(a["key"] == "unreachable:ok" for a in j.alerts())
    assert j.listing()[0]["last_run"]["ok"] is True


# ---------------------------------------------------------------- the real process
@needs_maria
def test_the_real_process(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "PYTHONPATH": os.getcwd(), "GEMINI_API_KEY": "sk-should-be-cleared", "ENGINE_MODE": "ai",
           "VISITOR_DAILY_CAP": "1"}                                             # the environment can't switch AI on
    env.pop("DATABASE_URL", None)
    p = subprocess.Popen([sys.executable, "-m", "app.products.dbaba.local", "serve", "--port", str(port), "--config", str(tmp_path / "dbaba.json")],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        line = p.stdout.readline().strip()
        assert line.startswith("DBABA-SETUP-TOKEN ") and len(line) > 30
        token = line.split(" ", 1)[1]
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                if json.load(urllib.request.urlopen(base + "/api/health", timeout=1))["ok"]:
                    break
            except OSError:
                time.sleep(0.2)
        listening = subprocess.run(["ss", "-Hltn", f"sport = :{port}"], capture_output=True, text=True).stdout
        local = [line.split()[3] for line in listening.splitlines() if line.strip()]
        assert local == [f"127.0.0.1:{port}"], listening                         # only loopback, nothing on 0.0.0.0

        def call(path, body=None, headers=None):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json", **(headers or {})},
                                         method="POST" if body is not None else "GET")
            return json.load(urllib.request.urlopen(req, timeout=30))
        saved = call("/api/setup/save", {**MARIA, "screen_password": "screen pass 3"}, {"X-DBaba-Setup": token})
        assert saved["ok"]
        st = call("/api/state", headers={"Authorization": "Bearer " + saved["api_key"]})
        assert st["setup_done"] and st["database"]["engine"] == "mariadb"
        reply = call("/api/message", {"text": "what is a primary key"}, {"Authorization": "Bearer " + saved["api_key"]})
        assert "primary key" in reply["reply"].lower()
        assert (tmp_path / "data" / "dbaba.db").exists()                          # its own working database, next to the file
    finally:
        p.terminate()
        p.wait(timeout=10)
    assert "sk-should-be-cleared" not in (tmp_path / "dbaba.json").read_text()


def test_reset_command(tmp_path):
    cfg = installer_file(tmp_path / "dbaba.json", database=_db(MARIA))
    assert Config(cfg).setup_done is True                                        # an installer file with a database is set up
    r = subprocess.run([sys.executable, "-m", "app.products.dbaba.local", "reset", "--config", str(cfg)], capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": os.getcwd()})
    assert r.returncode == 0 and Config(cfg).setup_done is False


def test_settings_write_without_fchmod(tmp_path, monkeypatch):
    """Windows has no os.fchmod: the settings file must still be written (found reviewing the Windows runner, PR #58)."""
    monkeypatch.delattr(os, "fchmod")
    cfg = Config(tmp_path / "dbaba.json")
    cfg.update(lambda d: d.__setitem__("port", 9999))
    assert Config(tmp_path / "dbaba.json").data["port"] == 9999


@needs_maria
def test_connected_is_known_right_after_a_restart(tmp_path):
    cfg = Config(tmp_path / "dbaba.json")
    cfg.update(lambda d: d.update(database=_db(MARIA), setup_done=True))
    j = Jobs(cfg)
    assert j.connected is None
    j.start(every=3600)
    for _ in range(50):
        if j.connected is not None:
            break
        time.sleep(0.1)
    j.stop()
    assert j.connected is True


# ---------------------------------------------------------------- after setup: a new database address, API keys
@needs_maria
def test_change_database_address_after_setup(fresh):
    """An SSH tunnel's new local port (or a moved server): tested first, saved only if it works, password kept."""
    app, c, path = fresh
    H = {"X-DBaba-Setup": "TOKEN-1"}
    assert c.post("/api/database", headers=H, json={"port": 3306}).status_code == 401     # the setup token can't do it
    key = c.post("/api/setup/save", headers=H, json={**MARIA, "screen_password": "screen pass 7"}).json()["api_key"]
    K = {"Authorization": "Bearer " + key}
    bad = c.post("/api/database", headers=K, json={"host": "127.0.0.1", "port": 1}).json()
    assert bad["ok"] is False and "Nothing answered at 127.0.0.1:1" in bad["reason"]
    assert json.loads(path.read_text())["database"]["port"] == 3306                        # nothing changed
    good = c.post("/api/database", headers=K, json={"engine": "mariadb", "host": "localhost", "port": 3306,
                                                   "database": "shop_db", "user": "shopapp"}).json()   # no password: kept
    assert good["ok"] is True and good["version"]
    saved = json.loads(path.read_text())["database"]
    assert saved["host"] == "localhost" and saved["password"] == "app pass"
    assert c.post("/api/database", json={"port": 3306}).status_code == 401


def test_change_database_needs_setup_first(fresh):
    app, c, path = fresh
    key = Config(path).new_api_key("x")
    app.state.cfg.data = Config(path).data
    assert c.post("/api/database", headers={"Authorization": "Bearer " + key}, json={"port": 3306}).status_code == 409


def test_api_keys_made_and_revoked_only_from_the_screen(tmp_path):
    cfg = installer_file(tmp_path / "dbaba.json")
    app = make_app(str(cfg), 8765, None, announce=lambda line: None)
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    assert c.post("/api/keys", json={"name": "cli"}).status_code == 401
    c.post("/api/login", json={"password": "screen pass 1"})
    assert c.post("/api/keys", json={"name": "<script>"}).status_code == 400
    made = c.post("/api/keys", json={"name": "laptop cli"}).json()
    key = made["api_key"]
    assert key.startswith("dbk_") and key not in cfg.read_text() and [k["name"] for k in made["keys"]] == ["laptop cli"]
    out = TestClient(app, base_url="http://127.0.0.1:8765")                                # no cookie: the key alone
    K = {"Authorization": "Bearer " + key}
    assert out.get("/api/state", headers=K).status_code == 200
    assert out.get("/api/keys", headers=K).json()["keys"][0]["name"] == "laptop cli"
    assert out.post("/api/keys", headers=K, json={"name": "more"}).status_code == 401      # a key can't make keys
    kid = made["keys"][0]["id"]
    assert out.post(f"/api/keys/{kid}/revoke", headers=K).status_code == 401              # ... nor revoke them
    assert c.post("/api/keys/zzz/revoke", json={}).status_code == 404
    assert c.post(f"/api/keys/{kid}/revoke", json={}).json()["keys"] == []
    assert out.get("/api/state", headers=K).status_code == 401                             # revoked: refused at once
