"""Audit persistence; callers supply only validated metadata."""
import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.domain.models import AuditEvent


def append(db, who: str, action: str, target: str, ip_hash: str) -> None:
    db.add(AuditEvent(who=who, action=action, target=target, ip_hash=ip_hash))
    db.flush()


@contextmanager
def local_session(path: Path):
    """An isolated audit database, never the customer's monitored database."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    if os.name != "nt":
        os.chmod(path, 0o600)
    engine = create_engine(URL.create("sqlite", database=str(path)))
    try:
        AuditEvent.__table__.create(engine, checkfirst=True)
        with Session(engine) as db, db.begin():
            yield db
    finally:
        engine.dispose()
