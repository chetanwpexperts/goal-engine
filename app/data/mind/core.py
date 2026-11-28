"""Conversation history and transaction helpers."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.domain.models import Message


def previous_texts(db: Session, visitor_id: str, goal_id: int | None, limit: int = 3) -> list[str]:
    if goal_id is None:
        return []
    rows = db.scalars(select(Message.text).where(Message.visitor_id == visitor_id, Message.goal_id == goal_id, Message.role == "user")
                      .order_by(Message.id.desc()).limit(limit))
    return list(reversed(list(rows)))


def previous_reply(db: Session, visitor_id: str, goal_id: int | None) -> str:
    if goal_id is None:
        return ""
    row = db.scalars(select(Message.text).where(Message.visitor_id == visitor_id, Message.goal_id == goal_id, Message.role == "assistant")
                     .order_by(Message.id.desc()).limit(1)).first()
    return row or ""


def commit(db):
    db.commit()


def record_apart(record, brain, chain, label, visitor_id):
    """Record in a short separate transaction, retrying a conflicting write once."""
    from sqlalchemy.exc import IntegrityError, OperationalError
    from app.core.db import get_engine

    for _ in range(2):
        with Session(get_engine()) as own:
            try:
                record(own, brain, chain, label, visitor=visitor_id)
                own.commit()
                return
            except (IntegrityError, OperationalError):
                own.rollback()


def record_step(db, record, brain, chain, label, visitor_id):
    if db.get_bind().dialect.name == "sqlite":
        record(db, brain, chain, label, visitor=visitor_id)
    else:
        record_apart(record, brain, chain, label, visitor_id)
