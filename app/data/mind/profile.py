"""Working style and conversation persistence."""
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from app.domain.models import MindSession, VisitorStyle, dumps, loads, utcnow


def load_session(db, goal_id):
    row = db.get(MindSession, goal_id) if goal_id is not None else None
    return (loads(row.data, {}) or {}) if row is not None else None


def load_style(db, visitor_id):
    row = db.get(VisitorStyle, visitor_id)
    return (loads(row.data, {}) or {}) if row is not None else None


def save(db: Session, goal_id: int | None, visitor_id: str, p: dict, merge_style) -> None:
    now = utcnow()
    if goal_id is not None:
        row = db.get(MindSession, goal_id)
        if row is None:
            row = MindSession(goal_id=goal_id, visitor_id=visitor_id)
            db.add(row)
        row.data, row.updated_at = dumps(p), now
    style = db.get(VisitorStyle, visitor_id)
    if style is None:
        style = VisitorStyle(visitor_id=visitor_id)
        db.add(style)
    old = loads(style.data, {}) or {}
    old = merge_style(old, p)
    style.data, style.updated_at = dumps(old), now


def delete_visitor(db: Session, visitor_id: str) -> int:
    n = db.execute(delete(MindSession).where(MindSession.visitor_id == visitor_id)).rowcount or 0
    n += db.execute(delete(VisitorStyle).where(VisitorStyle.visitor_id == visitor_id)).rowcount or 0
    return n


def maintain(db: Session, now, *, session_ttl, style_ttl) -> int:
    n = db.execute(delete(MindSession).where(MindSession.updated_at < now - session_ttl)).rowcount or 0
    n += db.execute(delete(VisitorStyle).where(VisitorStyle.updated_at < now - style_ttl)).rowcount or 0
    return n


def style_of(db: Session, visitor_id: str) -> dict:
    row = db.scalars(select(VisitorStyle).where(VisitorStyle.visitor_id == visitor_id)).first()
    return loads(row.data, {}) if row else {}
