"""Audit records retain only fixed metadata and a keyed IP fingerprint."""
import secrets
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from app.core.db import get_db
from app.domain.models import AuditEvent
from app.security.audit import record


def test_audit_metadata(fresh_db, monkeypatch):
    monkeypatch.setenv("APP_SECRET", secrets.token_urlsafe(32))
    with contextmanager(get_db)() as db:
        record(db, "account:1", "login", "account", "192.0.2.1")
        record(db, "account:1", "logout", "account", "192.0.2.1")
        record(db, "admin", "admin_action", "enquiries:2", "192.0.2.2")
        db.commit()
        events = list(db.scalars(select(AuditEvent).order_by(AuditEvent.id)))
        assert [e.action for e in events] == ["login", "logout", "admin_action"]
        assert events[0].who == "account:1"
        assert events[0].target == "account"
        assert events[0].at is not None
        assert len(events[0].ip_hash) == 64
        assert events[0].ip_hash == events[1].ip_hash
        assert events[0].ip_hash != events[2].ip_hash
        assert "192.0.2.1" not in events[0].ip_hash


def test_audit_rejects_unstructured_metadata(fresh_db):
    with contextmanager(get_db)() as db:
        for who, action, target in [
            ("raw token", "login", "account"),
            ("admin", "message text", "account"),
            ("admin", "admin_action", "raw password"),
        ]:
            with pytest.raises(ValueError):
                record(db, who, action, target, "192.0.2.1")
        assert not list(db.scalars(select(AuditEvent)))


def test_local_audit_isolated(tmp_path):
    import sqlite3
    from app.security.audit import record_local

    record_local(tmp_path, "local_screen", "login", "screen", "192.0.2.1")
    record_local(tmp_path, "local_screen", "api_key_create", "api_key", "192.0.2.1")
    with sqlite3.connect(tmp_path / "audit.sqlite3") as db:
        rows = db.execute("SELECT who, action, target, ip_hash FROM audit_events ORDER BY id").fetchall()
    assert [row[1] for row in rows] == ["login", "api_key_create"]
    assert rows[0][3] == rows[1][3]
    assert len(rows[0][3]) == 64
    assert "192.0.2.1" not in str(rows)


def test_local_key_audit_routes(tmp_path):
    import sqlite3
    import time
    from fastapi.testclient import TestClient
    from app.products.dbaba.local.server import COOKIE, SESSION_IDLE, make_app

    app = make_app(str(tmp_path / "dbaba.json"), announce=lambda _: None)
    session = secrets.token_urlsafe(32)
    app.state.sessions[session] = time.time() + SESSION_IDLE
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)) as client:
        assert client.post("/api/keys", json={"name": "Test"}).status_code == 401
        client.cookies.set(COOKIE, session)
        response = client.post("/api/keys", json={"name": "Test"})
        assert response.status_code == 200
        key = response.json()["api_key"]
        kid = response.json()["keys"][0]["id"]
        assert client.post("/api/keys/" + kid + "/revoke", json={}).status_code == 200
        assert client.post("/api/logout", json={}).status_code == 200
    with sqlite3.connect(app.state.cfg.data_dir / "audit.sqlite3") as db:
        rows = db.execute("SELECT who, action, target, ip_hash FROM audit_events ORDER BY id").fetchall()
    assert [row[1] for row in rows] == ["api_key_create", "api_key_revoke", "logout"]
    assert key not in str(rows)
    assert session not in str(rows)
