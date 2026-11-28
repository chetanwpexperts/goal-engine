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
