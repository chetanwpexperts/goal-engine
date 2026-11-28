"""Audit persistence; callers supply only validated metadata."""
from app.domain.models import AuditEvent


def append(db, who: str, action: str, target: str, ip_hash: str) -> None:
    db.add(AuditEvent(who=who, action=action, target=target, ip_hash=ip_hash))
    db.flush()
