"""Daily caps: protect the free AI quota and stop abuse of the public demo."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import UsageCounter


class LimitReached(RuntimeError):
    pass


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _counter(db: Session, day: str, scope: str) -> UsageCounter:
    row = db.scalar(select(UsageCounter).where(UsageCounter.day == day, UsageCounter.scope == scope))
    if row:
        return row
    row = UsageCounter(day=day, scope=scope, count=0)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:  # created by a parallel request
        db.rollback()
        row = db.scalar(select(UsageCounter).where(UsageCounter.day == day, UsageCounter.scope == scope))
    return row


def reserve_ai_call(db: Session, visitor_id: str, settings: Settings) -> None:
    day = _today()
    everyone = _counter(db, day, "all")
    visitor = _counter(db, day, f"visitor:{visitor_id}")
    if everyone.count >= settings.ai_daily_cap:
        raise LimitReached("The demo has reached today's limit. Please try again tomorrow.")
    if visitor.count >= settings.visitor_daily_cap:
        raise LimitReached("You've reached today's limit for this demo. Please come back tomorrow.")
    everyone.count += 1
    visitor.count += 1
    db.commit()


def release_ai_call(db: Session, visitor_id: str) -> None:
    """Give the message back when the AI failed, so errors don't eat the daily limit."""
    day = _today()
    for scope in ("all", f"visitor:{visitor_id}"):
        row = db.scalar(select(UsageCounter).where(UsageCounter.day == day, UsageCounter.scope == scope))
        if row and row.count > 0:
            row.count -= 1
    db.commit()


def usage_today(db: Session, visitor_id: str) -> dict[str, int]:
    day = _today()
    rows = {r.scope: r.count for r in db.scalars(select(UsageCounter).where(UsageCounter.day == day))}
    return {"all": rows.get("all", 0), "visitor": rows.get(f"visitor:{visitor_id}", 0)}
