"""The SQL console (app/console.py): inside the locked database area, read-only, rows straight back, nothing kept.

What must hold:
* guests get 404 on every console endpoint; the page has its own strict rules (no framing, no other site's code, no cache)
* a query's text and its rows are never written to Goal Engine's database or logs (marker values are searched for)
* only one read-only statement runs; the database's own error comes back; password-like columns are masked
* 10 minutes without typing or a query: disconnected and the details wiped (by the next request, and by the scheduler)
* typing ('alive') keeps it connected; Disconnect, locking the area and logging out wipe the details
"""

import logging
import socket
from datetime import timedelta

import pytest
from sqlalchemy import inspect, text

from tests.test_accounts import FakeMailer, mail, new_user  # noqa: F401 - fixture

DB = {"type": "mysql", "host": "127.0.0.1", "database": "shop_db", "user": "shopapp", "password": "app pass"}
PG = {"type": "postgres", "host": "127.0.0.1", "port": "5432", "database": "shop_pg", "user": "shopapp", "password": "app pass"}


def _up(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


@pytest.fixture
def locked(monkeypatch, mail):
    monkeypatch.setenv("DB_AREA_LOCK", "on")
    monkeypatch.setenv("DIRECT_DB_ALLOW_PRIVATE", "1")
    from app import console
    from app.agents import direct

    direct._attempts.clear()
    console._runs.clear()
    return mail


def _open_area(client, mail, n):
    client.post("/api/account/signup", json=new_user(n=n))
    client.post("/api/account/verify", json={"code": mail.last_code(f"asha{n}@example.com")})
    client.post("/api/db-area/code")
    r = client.post("/api/db-area/open", json={"code": mail.last_code(f"asha{n}@example.com")})
    assert r.json()["state"] == "open", r.json()


def _everything_stored() -> str:
    """Every value in every table of Goal Engine's own database, as one text."""
    from app.db import get_engine

    eng = get_engine()
    out = []
    with eng.connect() as conn:
        for t in inspect(eng).get_table_names():
            for row in conn.execute(text(f'SELECT * FROM "{t}"')):
                out.append(" ".join(str(v) for v in row))
    return "\n".join(out)


def _age(connector_id: int, minutes: int):
    from app.db import _SessionLocal
    from app.models import Connector, utcnow

    with _SessionLocal() as db:
        c = db.get(Connector, connector_id)
        c.last_seen_at = utcnow() - timedelta(minutes=minutes)
        db.commit()


def _console_row():
    from app.db import _SessionLocal
    from app.models import Connector

    with _SessionLocal() as db:
        return db.query(Connector).filter(Connector.token_hint == "console").order_by(Connector.id.desc()).first()


# ---------------------------------------------------------------- who can reach it
def test_guests_get_404_and_the_page_has_strict_rules(client, locked):
    for method, path in [("get", "/api/console/state"), ("post", "/api/console/connect"), ("post", "/api/console/run"),
                         ("post", "/api/console/check"), ("get", "/api/console/schema"), ("post", "/api/console/alive"),
                         ("post", "/api/console/disconnect")]:
        r = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
        assert r.status_code == 404, (path, r.status_code)
    page = client.get("/console")
    assert page.status_code == 200 and "DBaba workspace" in page.text
    csp = page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "script-src 'self'" in csp and "connect-src 'self'" in csp
    assert page.headers["x-frame-options"] == "DENY" and page.headers["cache-control"] == "no-store"
    assert "<script>" not in page.text and " style=" not in page.text          # no inline code: the strict rules allow none


def test_a_locked_area_is_asked_to_open_first(client, locked):
    client.post("/api/account/signup", json=new_user(n=41))
    client.post("/api/account/verify", json={"code": locked.last_code("asha41@example.com")})
    r = client.post("/api/console/connect", json=DB)
    assert r.status_code == 403 and r.json()["db_area"] == "locked"


def test_not_connected_says_so(client, locked):
    _open_area(client, locked, 42)
    assert client.get("/api/console/state").json() == {"connected": False, "reason": "none", "idle_seconds": 600}
    r = client.post("/api/console/run", json={"sql": "SELECT 1"})
    assert r.status_code == 409 and r.json()["reason"] == "none"


# ---------------------------------------------------------------- the console on a real database
@pytest.mark.skipif(not _up(3306), reason="needs local MySQL")
def test_connect_run_and_nothing_is_kept(client, locked, caplog):
    caplog.set_level(logging.DEBUG)
    _open_area(client, locked, 43)
    r = client.post("/api/console/connect", json=DB)
    assert r.status_code == 200, r.json()
    d = r.json()
    assert d["connected"] and d["database"] == "shop_db" and d["user"] == "shopapp" and d["seconds_left"] > 590
    assert "orders" in [t["name"] for t in d["tables"]]
    assert r.headers["cache-control"] == "no-store"
    before_jobs = _everything_stored().count("connector_jobs")

    marker = "zq-console-marker-7731"
    run = client.post("/api/console/run", json={"sql": f"SELECT '{marker}' AS m, 42 AS answer, NULL AS nothing"})
    assert run.status_code == 200, run.json()
    out = run.json()
    assert out["columns"] == ["m", "answer", "nothing"] and out["rows"] == [[marker, 42, None]] and out["row_count"] == 1
    assert isinstance(out["ms"], int) and run.headers["cache-control"] == "no-store"
    # the query and its rows exist on the developer's screen only
    stored = _everything_stored()
    assert marker not in stored and "app pass" not in stored and "shopapp" not in stored
    assert stored.count("connector_jobs") == before_jobs
    assert marker not in caplog.text
    from app.db import _SessionLocal
    from app.models import ConnectorJob

    with _SessionLocal() as db:
        assert db.query(ConnectorJob).count() == 0                     # no job rows at all: nothing queued, nothing saved

    # real rows, the row cap, and EXPLAIN / SHOW
    rows = client.post("/api/console/run", json={"sql": "SELECT id, amount FROM orders ORDER BY id"}).json()
    assert rows["columns"] == ["id", "amount"] and rows["row_count"] >= 2 and rows["limited"] is False
    big = client.post("/api/console/run", json={"sql": "SELECT a.id FROM orders a, orders b, orders c, orders d, orders e, "
                                                       "orders f, orders g, orders h, orders i, orders j, orders k"}).json()
    assert big["row_count"] == 1000 and big["limited"] is True
    assert client.post("/api/console/run", json={"sql": "EXPLAIN SELECT * FROM orders WHERE id = 1"}).status_code == 200
    assert client.post("/api/console/run", json={"sql": "SHOW TABLES"}).status_code == 200
    # password-like columns are masked, as everywhere
    masked = client.post("/api/console/run", json={"sql": "SELECT user_login, user_pass FROM wp_users LIMIT 1"}).json()
    assert masked["hidden_columns"] == ["user_pass"]


@pytest.mark.skipif(not _up(3306), reason="needs local MySQL")
def test_only_one_read_only_statement_and_the_databases_own_error(client, locked):
    _open_area(client, locked, 44)
    assert client.post("/api/console/connect", json=DB).status_code == 200
    for bad in ["UPDATE orders SET amount = 0", "DELETE FROM orders", "DROP TABLE orders", "SELECT 1; DROP TABLE orders",
                "INSERT INTO orders (amount) VALUES (1)", "SELECT * FROM orders INTO OUTFILE '/tmp/x'", "CALL anything()",
                "SET GLOBAL max_connections = 1", "SELECT SLEEP(5)"]:
        r = client.post("/api/console/run", json={"sql": bad})
        assert r.status_code == 400 and r.json()["reason"] == "not_read_only", (bad, r.json())
    import pymysql

    conn = pymysql.connect(host="127.0.0.1", user="shopapp", password="app pass", database="shop_db")
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM orders")
        assert cur.fetchone()[0] >= 2                                    # nothing was changed
    conn.close()
    err = client.post("/api/console/run", json={"sql": "SELECT no_such_column FROM orders"})
    assert err.status_code == 400 and err.json()["reason"] == "db_error"
    assert err.json()["error"].startswith("ERROR 1054") and "no_such_column" in err.json()["error"]


@pytest.mark.skipif(not _up(3306), reason="needs local MySQL")
def test_ten_idle_minutes_disconnect_and_typing_keeps_it(client, locked):
    from app import console
    from app.db import _SessionLocal

    _open_area(client, locked, 45)
    assert client.post("/api/console/connect", json=DB).status_code == 200
    c = _console_row()
    _age(c.id, 9)                                                         # 9 idle minutes: still there
    assert client.post("/api/console/alive").json()["seconds_left"] > 590    # typing starts the clock again
    _age(c.id, 11)                                                        # 11 idle minutes
    r = client.post("/api/console/run", json={"sql": "SELECT 1"})
    assert r.status_code == 410 and r.json()["reason"] == "timed_out"
    row = _console_row()
    assert row.secret is None and row.status == "disconnected"
    assert client.get("/api/console/state").json()["connected"] is False
    # the scheduler wipes it even if nobody comes back
    assert client.post("/api/console/connect", json=DB).status_code == 200
    _age(_console_row().id, 11)
    with _SessionLocal() as db:
        assert console.maintain(db) == 1
    assert _console_row().secret is None


@pytest.mark.skipif(not _up(3306), reason="needs local MySQL")
def test_disconnect_lock_and_logout_wipe_the_details(client, locked):
    _open_area(client, locked, 46)
    assert client.post("/api/console/connect", json=DB).status_code == 200
    first = _console_row().id
    assert client.post("/api/console/connect", json=DB).status_code == 200       # one console connection at a time
    from app.db import _SessionLocal
    from app.models import Connector

    with _SessionLocal() as db:
        assert db.get(Connector, first).secret is None
    assert client.post("/api/console/disconnect").json()["connected"] is False
    assert _console_row().secret is None
    assert client.post("/api/console/connect", json=DB).status_code == 200
    client.post("/api/db-area/lock")
    assert _console_row().secret is None
    _open_area_again = client.post("/api/db-area/code")
    assert _open_area_again.status_code == 200
    client.post("/api/db-area/open", json={"code": locked.last_code("asha46@example.com")})
    assert client.post("/api/console/connect", json=DB).status_code == 200
    client.post("/api/account/logout")
    assert _console_row().secret is None


@pytest.mark.skipif(not _up(3306), reason="needs local MySQL")
def test_console_connections_stay_apart_from_the_agents(client, locked):
    _open_area(client, locked, 47)
    assert client.post("/api/console/connect", json=DB).status_code == 200
    assert client.get("/api/connectors").json()["connectors"] == []


@pytest.mark.skipif(not _up(3306), reason="needs local MySQL")
def test_dbaba_checks_the_query_for_the_connected_database(client, locked):
    _open_area(client, locked, 48)
    assert client.post("/api/console/connect", json=DB).status_code == 200
    r = client.post("/api/console/check", json={"sql": "SELECT id FROM orders WHERE amount = NULL"}).json()
    assert any("NULL" in f["title_en"] for f in r["findings"]) and "IS NULL" in (r["corrected"] or "")
    assert r["engine"] == "mysql"
    assert "amount = NULL" not in _everything_stored()


@pytest.mark.skipif(not _up(5432), reason="needs local PostgreSQL")
def test_postgresql_too(client, locked):
    _open_area(client, locked, 49)
    r = client.post("/api/console/connect", json=PG)
    assert r.status_code == 200, r.json()
    out = client.post("/api/console/run", json={"sql": "SELECT id, amount FROM orders ORDER BY id"}).json()
    assert out["columns"] == ["id", "amount"] and out["row_count"] >= 2
    err = client.post("/api/console/run", json={"sql": "SELECT nope FROM orders"}).json()
    assert err["reason"] == "db_error" and "42703" in err["error"]
    assert client.post("/api/console/run", json={"sql": "DELETE FROM orders"}).json()["reason"] == "not_read_only"


# ---------------------------------------------------------------- Ask DBaba: a developer's issue in any form
CODE = {
    "php": ('$sql = "SELECT * FROM orders o JOIN customers c ON o.id = c.id WHERE c.email = \'$email\'";\n'
            '$res = mysqli_query($conn, $sql);'),
    "java": ('String sql = "SELECT name FROM users " +\n             "WHERE email = NULL";\n'
             'PreparedStatement ps = conn.prepareStatement(sql);'),
    "node": "const rows = await pool.query(`SELECT id, COUNT(CASE WHEN status='paid' THEN 1 ELSE 0 END) FROM orders WHERE id = ${id} GROUP BY id`)",
    "dotnet": 'var cmd = new SqlCommand("SELECT id FROM Orders WHERE Status = @s AND Total = NULL", conn);',
    "python": 'cursor.execute(f"SELECT * FROM users WHERE id = {uid} AND deleted_at = NULL")',
}


def _titles(blocks):
    return [i["title"] for b in blocks if b["type"] == "findings" for i in b["items"]]


def test_sql_is_found_inside_code_of_any_language():
    from app.console import injection_finding, sql_in_code

    assert sql_in_code(CODE["java"]) == "SELECT name FROM users WHERE email = NULL"
    assert sql_in_code(CODE["dotnet"]) == "SELECT id FROM Orders WHERE Status = @s AND Total = NULL"
    assert sql_in_code("hello 'world' and \"nothing\" here") is None
    glued = [CODE["php"], CODE["node"], CODE["python"], 'q = "SELECT * FROM t WHERE id = " + id;',
             '$q = "SELECT * FROM t WHERE id = " . $id;', 'cursor.execute("SELECT * FROM t WHERE a = \'%s\'" % a)']
    safe = ['cursor.execute("SELECT * FROM t WHERE id = %s", (uid,))', 'await client.query("SELECT * FROM t WHERE id = $1", [id])',
            '$st = $pdo->prepare("SELECT * FROM t WHERE email = ?"); $st->execute([$email]);', CODE["dotnet"], CODE["java"]]
    for t in glued:
        assert injection_finding(t, sql_in_code(t) or t, False), t
    for t in safe:
        assert injection_finding(t, sql_in_code(t) or t, False) is None, t


@pytest.mark.parametrize("lang", sorted(CODE))
def test_ask_reads_code_in_any_language(client, locked, lang):
    _open_area(client, locked, 60 + sorted(CODE).index(lang))
    r = client.post("/api/console/ask", json={"text": CODE[lang]})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.json()
    titles = _titles(r.json()["blocks"])
    assert titles, (lang, r.json())
    if lang in ("php", "node", "python"):
        assert "SQL injection" in titles[0], (lang, titles)                # glued values come first, with the safe way
    if lang in ("java", "dotnet", "python"):
        assert any("NULL" in t for t in titles), (lang, titles)
    if lang == "node":
        assert any("counts every row" in t for t in titles), titles


def test_ask_errors_problems_and_questions(client, locked):
    _open_area(client, locked, 70)
    err = client.post("/api/console/ask", json={"text": "SQLSTATE[HY000] [1040] Too many connections in /var/www/app/db.php"}).json()
    assert "connections" in err["blocks"][0]["title"].lower() and "Fix" in err["blocks"][0]["body"]
    slow = client.post("/api/console/ask", json={"text": "Deadlock found when trying to get lock; try restarting transaction"}).json()
    assert "deadlock" in (slow["blocks"][0]["title"] + slow["blocks"][0]["body"]).lower()
    q = client.post("/api/console/ask", json={"text": "left join aur inner join mein kya farak hai"}).json()
    assert q["blocks"][0]["type"] == "text" and "LEFT JOIN" in q["blocks"][0]["body"]
    vague = client.post("/api/console/ask", json={"text": "hmm something is off"}).json()
    assert vague["blocks"][-1]["type"] == "text"
    assert client.post("/api/console/ask", json={"text": ""}).status_code == 400


def test_asking_keeps_nothing(client, locked, caplog):
    caplog.set_level(logging.DEBUG)
    _open_area(client, locked, 71)
    marker = "zq-ask-marker-5512"
    r = client.post("/api/console/ask", json={"text": f"SELECT id FROM orders WHERE note = '{marker}' AND amount = NULL"})
    assert r.status_code == 200
    from app.db import _SessionLocal
    from app.models import Goal, LearnRequest, Message

    stored = _everything_stored()
    assert marker not in stored and marker not in caplog.text
    with _SessionLocal() as db:
        assert db.query(Message).count() == 0 and db.query(Goal).count() == 0 and db.query(LearnRequest).count() == 0


@pytest.mark.skipif(not _up(3306), reason="needs local MySQL")
def test_ask_checks_on_the_connected_database(client, locked):
    _open_area(client, locked, 72)
    assert client.post("/api/console/connect", json=DB).status_code == 200
    r = client.post("/api/console/ask", json={"text": "my app shows too many connections error at peak time"}).json()
    assert r["connected"] is True and r["engine"] == "mysql"
    assert r["blocks"] and "connections" in r["blocks"][0]["title"].lower()
    pb = client.post("/api/console/ask", json={"text": "too many connections"}).json()   # a known problem: its own checks, run here
    tables = [b for b in pb["blocks"] if b["type"] == "table"]
    assert tables and all(b.get("columns") or b.get("error") for b in tables), pb
    q = client.post("/api/console/ask", json={"text": "SELECT * FROM orders WHERE YEAR(created) = 2024"}).json()
    sqls = [b for b in q["blocks"] if b["type"] == "sql"]
    assert any(b["run"] for b in sqls)                                                    # a read-only query it suggests can be run here
    assert client.get("/api/console/state").json()["seconds_left"] > 590                  # asking counts as activity
