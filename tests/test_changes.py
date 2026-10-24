"""Approved changes with Undo, on real MySQL, PostgreSQL and SQLite: the exact rows come back, nothing else is touched."""

import socket
import sqlite3

import pytest

from tests.test_database import gc


def _up(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


def _sqlite(tmp_path):
    path = tmp_path / "s.sqlite"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, price REAL, note TEXT, created TEXT, blob_col BLOB);"
                      "INSERT INTO items (name, price, note, created, blob_col) VALUES ('pen', 10.5, 'a where b', '2026-01-02 03:04:05', x'0102'),"
                      "('book', 200, NULL, '2026-02-02 10:00:00', NULL), ('bag', 900, 'x', NULL, NULL);"
                      "CREATE TABLE nokey (a INT);")
    con.commit()
    con.close()
    return {"type": "sqlite", "database": str(path), "writer": True}


DBS = {
    "mysql": (13306, {"type": "mysql", "host": "127.0.0.1", "port": "13306", "database": "shop_db", "user": "shopapp", "password": "app pass"}),
    "postgres": (15432, {"type": "postgres", "host": "127.0.0.1", "port": "15432", "database": "shop_pg", "user": "shopapp", "password": "app pass"}),
}


@pytest.fixture(params=["sqlite", "mysql", "postgres"])
def db(request, tmp_path):
    if request.param == "sqlite":
        return gc.Database(_sqlite(tmp_path)).connect()
    port, cfg = DBS[request.param]
    if not _up(port):
        pytest.skip(f"needs local {request.param}")
    d = gc.Database(cfg).connect()
    # fresh rows each test
    cur = d.cursor(read_only=False)
    d.start_write(cur)
    cur.execute("DELETE FROM items")
    for row in (("pen", "10.50", "a where b", "2026-01-02 03:04:05", b"\x01\x02"), ("book", "200", None, "2026-02-02 10:00:00", None),
                ("bag", "900", "x", None, None)):
        cur.execute(f"INSERT INTO items (name, price, note, created, blob_col) VALUES ({d.ph}, {d.ph}, {d.ph}, {d.ph}, {d.ph})", row)
    d.conn.commit()
    return d


def snapshot(d):
    return d.rows("SELECT name, price, note, created, blob_col FROM items ORDER BY name")[1]


def test_parse_change_rules():
    ok = gc.parse_change("UPDATE `orders` SET note = 'see where it goes' WHERE id = 3;")
    assert ok["op"] == "update" and ok["table"] == "orders" and ok["where"].strip() == "id = 3"
    assert gc.parse_change("delete from items where price > 100")["where"] == "price > 100"
    assert gc.parse_change('INSERT INTO "items" (name) VALUES (\'x\'), (\'y\')')["table"] == "items"
    for bad in ("UPDATE items SET price = 0", "DELETE FROM items", "DROP TABLE items", "UPDATE a, b SET x = 1 WHERE 1=1",
                "UPDATE a JOIN b ON a.id = b.id SET x = 1 WHERE 1 = 1", "INSERT INTO a SELECT * FROM b",
                "INSERT INTO a (x) VALUES (1) ON DUPLICATE KEY UPDATE x = 2", "DELETE FROM a WHERE 1=1; DROP TABLE a",
                "TRUNCATE items", "SELECT 1"):
        with pytest.raises(ValueError):
            gc.parse_change(bad)


def test_update_then_undo_restores_exactly(db):
    start = snapshot(db)
    plan = db.plan_change("UPDATE items SET price = price * 2, note = 'changed' WHERE price < 500")
    assert plan["count"] == 2 and not plan.get("blocked") and plan["pk"] == ["id"]
    record, info = db.apply_change("UPDATE items SET price = price * 2, note = 'changed' WHERE price < 500", plan["count"])
    assert info["rows"] == 2 and snapshot(db) != start
    db.undo_change(record)
    assert snapshot(db) == start


def test_delete_then_undo_brings_rows_back(db):
    start = snapshot(db)
    record, info = db.apply_change("DELETE FROM items WHERE name IN ('pen', 'bag')", 2)
    assert info["rows"] == 2 and len(snapshot(db)) == 1
    db.undo_change(record)
    assert snapshot(db) == start


def test_insert_then_undo_removes_only_new_rows(db):
    start = snapshot(db)
    record, info = db.apply_change("INSERT INTO items (name, price) VALUES ('cup', 5), ('mug', 7)", None)
    assert info["rows"] == 2 and len(snapshot(db)) == 5
    db.undo_change(record)
    assert snapshot(db) == start


def test_preview_mismatch_changes_nothing(db):
    start = snapshot(db)
    with pytest.raises(ValueError, match="changed since the preview"):
        db.apply_change("DELETE FROM items WHERE price > 100", 5)
    assert snapshot(db) == start


def test_undo_refuses_to_overwrite_newer_edits_unless_forced(db):
    record, _ = db.apply_change("UPDATE items SET note = 'v2' WHERE name = 'book'", 1)
    cur = db.cursor(read_only=False)
    db.start_write(cur)
    cur.execute("UPDATE items SET note = 'v3 by someone else' WHERE name = 'book'")
    db.conn.commit()
    with pytest.raises(gc.ChangeConflict):
        db.undo_change(record)
    assert [r[2] for r in snapshot(db) if r[0] == "book"] == ["v3 by someone else"]
    db.undo_change(record, force=True)
    assert [r[2] for r in snapshot(db) if r[0] == "book"] == [None]


def test_tables_without_primary_key_are_refused(db):
    assert db.plan_change("DELETE FROM nokey WHERE a = 1")["blocked"]
    with pytest.raises(ValueError, match="primary key"):
        db.apply_change("DELETE FROM nokey WHERE a = 1", 0)


# ---------------------------------------------------------------- through Goal Engine's API
from datetime import timedelta  # noqa: E402

from app.agents import direct  # noqa: E402
from app.db import get_db  # noqa: E402
from app.models import ConnectorJob, utcnow  # noqa: E402
from tests.test_ml import say  # noqa: E402


def pump(client, token, reader, writer):
    auth = {"Authorization": f"Bearer {token}"}
    for job in client.post("/api/connector/poll", headers=auth).json()["jobs"]:
        try:
            body = {"ok": True, "result": gc.handle(reader, job, writer=writer)}
        except Exception as exc:  # noqa: BLE001
            body = {"ok": False, "error": ("CONFLICT: " if isinstance(exc, gc.ChangeConflict) else "") + str(exc)}
        client.post(f"/api/connector/result/{job['id']}", headers=auth, json=body)


def job(client, jid):
    return client.get(f"/api/connector-jobs/{jid}").json()


@pytest.fixture()
def agent_conn(client, ml_mode, tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "UNDO_DIR", str(tmp_path / "undo"))
    cfg = _sqlite(tmp_path)
    reader = gc.Database({**cfg, "writer": False}).connect()
    writer = gc.Database(cfg).connect()
    agent = say(client, "database ki tables dikhao")["agents"][0]
    token = client.post("/api/connectors", json={"agent_id": agent["id"]}).json()["token"]
    client.post("/api/connector/hello", headers={"Authorization": f"Bearer {token}"}, json={"db_type": "sqlite", "allow_changes": True})
    pump(client, token, reader, writer)
    cid = client.get("/api/connectors").json()["connectors"][0]["id"]
    return cid, (lambda: pump(client, token, reader, writer)), reader, tmp_path


def test_preview_apply_undo_through_the_api(client, agent_conn):
    cid, run, reader, tmp = agent_conn
    before = snapshot(reader)
    plan = client.post(f"/api/connectors/{cid}/plan", json={"sql": "UPDATE items SET price = 1 WHERE price > 100"}).json()
    run()
    p = job(client, plan["id"])
    assert p["status"] == "done" and p["result"]["count"] == 2 and snapshot(reader) == before      # preview changes nothing

    change = client.post(f"/api/connectors/{cid}/apply", json={"plan_id": plan["id"]}).json()
    run()
    cj = job(client, change["id"])
    assert cj["status"] == "done" and snapshot(reader) != before, cj
    assert list((tmp / "undo").iterdir())                    # the undo copy is on THEIR side
    assert client.post(f"/api/connectors/{cid}/apply", json={"plan_id": plan["id"]}).status_code == 400   # only once

    act = client.get(f"/api/connectors/{cid}/activity").json()["items"]
    ch = next(i for i in act if i["kind"] == "change")
    assert ch["undo"]["available"] and ch["rows"] == 2 and "price = ?" in ch["summary"]

    undo = client.post(f"/api/connectors/{cid}/undo", json={"change_id": change["id"]}).json()
    run()
    assert job(client, undo["id"])["status"] == "done" and snapshot(reader) == before
    assert not list((tmp / "undo").iterdir())
    act = client.get(f"/api/connectors/{cid}/activity").json()["items"]
    assert next(i for i in act if i["kind"] == "change")["undo"] == {"available": False, "undone": True, "where": "your server"}
    assert client.post(f"/api/connectors/{cid}/undo", json={"change_id": change["id"]}).status_code == 400


def test_stale_preview_and_conflicting_undo(client, agent_conn):
    cid, run, reader, tmp = agent_conn
    plan = client.post(f"/api/connectors/{cid}/plan", json={"sql": "DELETE FROM items WHERE name = 'pen'"}).json()
    run()
    s = next(get_db())
    j = s.get(ConnectorJob, plan["id"])
    j.finished_at = utcnow() - timedelta(minutes=11)
    s.commit()
    r = client.post(f"/api/connectors/{cid}/apply", json={"plan_id": plan["id"]})
    assert r.status_code == 400 and "10 minutes" in r.json()["error"]

    plan = client.post(f"/api/connectors/{cid}/plan", json={"sql": "UPDATE items SET note = 'v2' WHERE name = 'book'"}).json()
    run()
    change = client.post(f"/api/connectors/{cid}/apply", json={"plan_id": plan["id"]}).json()
    run()
    w = gc.Database({"type": "sqlite", "database": str(tmp / "s.sqlite"), "writer": True}).connect()
    cur = w.cursor(read_only=False)
    w.start_write(cur)
    cur.execute("UPDATE items SET note = 'edited by the app' WHERE name = 'book'")
    w.conn.commit()
    undo = client.post(f"/api/connectors/{cid}/undo", json={"change_id": change["id"]}).json()
    run()
    u = job(client, undo["id"])
    assert u["status"] == "failed" and u["error"].startswith("CONFLICT")
    assert [r[2] for r in snapshot(reader) if r[0] == "book"] == ["edited by the app"]
    forced = client.post(f"/api/connectors/{cid}/undo", json={"change_id": change["id"], "force": True}).json()
    run()
    assert job(client, forced["id"])["status"] == "done" and [r[2] for r in snapshot(reader) if r[0] == "book"] == [None]


def test_changes_need_opt_in_and_bad_sql_is_refused(client, ml_mode, tmp_path):
    agent = say(client, "database ki tables dikhao")["agents"][0]
    token = client.post("/api/connectors", json={"agent_id": agent["id"]}).json()["token"]
    client.post("/api/connector/hello", headers={"Authorization": f"Bearer {token}"}, json={"db_type": "sqlite"})
    cid = client.get("/api/connectors").json()["connectors"][0]["id"]
    r = client.post(f"/api/connectors/{cid}/plan", json={"sql": "DELETE FROM items WHERE id = 1"})
    assert r.status_code == 400 and "switched off" in r.json()["error"]
    # a connector that never allowed changes refuses them itself too
    reader = gc.Database({**_sqlite(tmp_path), "writer": False}).connect()
    with pytest.raises(ValueError, match="switched off"):
        gc.handle(reader, {"id": 1, "kind": "change", "payload": {"sql": "DELETE FROM items WHERE id = 1"}})


def test_plan_rejects_unsafe_statements(client, agent_conn):
    cid, *_ = agent_conn
    for bad in ("DELETE FROM items", "DROP TABLE items", "UPDATE items SET x = 1 WHERE 1=1; DELETE FROM items WHERE 1=1"):
        assert client.post(f"/api/connectors/{cid}/plan", json={"sql": bad}).status_code == 400


@pytest.mark.skipif(not _up(13306), reason="needs local MySQL")
def test_direct_connection_change_and_undo(client, ml_mode, monkeypatch):
    monkeypatch.setenv("APP_SECRET", "s" * 32)
    monkeypatch.setenv("DIRECT_DB_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("DIRECT_DB_SYNC", "1")
    direct._attempts.clear()
    d = gc.Database(DBS["mysql"][1]).connect()
    cur = d.cursor(read_only=False)
    d.start_write(cur)
    cur.execute("DELETE FROM items")
    cur.execute("INSERT INTO items (name, price, note) VALUES ('pen', 10, 'secret note'), ('book', 200, NULL)")
    d.conn.commit()
    before = snapshot(d)
    agent = say(client, "database ki tables dikhao")["agents"][0]
    res = client.post("/api/connectors/direct", json={"host": "127.0.0.1", "port": "13306", "database": "shop_db", "user": "shopapp",
                                                      "password": "app pass", "agent_id": agent["id"], "allow_changes": True})
    cid = res.json()["agents"][0]["connector"]["id"]
    assert res.json()["agents"][0]["connector"]["allow_changes"] is True
    plan = client.post(f"/api/connectors/{cid}/plan", json={"sql": "DELETE FROM items WHERE name = 'pen'"}).json()
    assert job(client, plan["id"])["result"]["count"] == 1
    change = client.post(f"/api/connectors/{cid}/apply", json={"plan_id": plan["id"]}).json()
    assert job(client, change["id"])["status"] == "done" and len(snapshot(d)) == 1
    s = next(get_db())
    blob = s.get(ConnectorJob, change["id"]).undo_blob
    assert blob and "secret note" not in blob                # the undo copy is encrypted
    undo = client.post(f"/api/connectors/{cid}/undo", json={"change_id": change["id"]}).json()
    assert job(client, undo["id"])["status"] == "done" and snapshot(d) == before
    s.expire_all()
    assert s.get(ConnectorJob, change["id"]).undo_blob is None


@pytest.mark.skipif(not _up(13306), reason="needs local MySQL with the parents/order_lines tables")
def test_cascade_deletes_are_blocked():
    d = gc.Database(DBS["mysql"][1]).connect()
    plan = d.plan_change("DELETE FROM parents WHERE id = 1")
    assert plan["blocked"] and "order_lines" in plan["blocked"]
    with pytest.raises(ValueError, match="cascade"):
        d.apply_change("DELETE FROM parents WHERE id = 1", 1)
    assert d.query("SELECT COUNT(*) FROM order_lines", 5)["rows"][0][0] == 2
    assert not d.plan_change("UPDATE items SET note = 'x' WHERE id = 1").get("blocked")


def test_add_index_then_undo(db):
    plan = db.plan_change("ALTER TABLE items ADD INDEX idx_items_price (price)")
    assert plan["op"] == "add_index" and not plan.get("blocked"), plan
    record, info = db.apply_change("CREATE INDEX idx_items_price ON items (price)", None)
    assert any(i["name"] == "idx_items_price" for i in db.index_list("items"))
    again = db.plan_change("CREATE INDEX idx_items_price2 ON items (price)")
    assert "already exists" in again["blocked"]
    db.undo_change(record)
    assert not any(i["name"] == "idx_items_price" for i in db.index_list("items"))
    with pytest.raises(ValueError):
        gc.parse_change("CREATE UNIQUE INDEX u ON items (name)")
    with pytest.raises(ValueError):
        gc.parse_change("ALTER TABLE items ADD INDEX x (price), DROP COLUMN note")
