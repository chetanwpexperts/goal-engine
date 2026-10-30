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


# No-AI mode costs nothing per message, so there are no daily caps ("free, no limits"); these only stop floods.
BURST = {"visitor": 20, "ip": 60, "all": 600}         # messages per minute


def _minute(offset: int = 0) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(minutes=offset)).strftime("%m%d%H%M")


def reserve_burst(db: Session, visitor_id: str, client_ip: str = "") -> None:
    minute = _minute()
    scopes = [("all", BURST["all"]), (f"v:{visitor_id}", BURST["visitor"])]
    if client_ip:
        scopes.append((f"ip:{client_ip}", BURST["ip"]))
    rows = [(_counter(db, minute, f"burst:{s}"[:80]), most) for s, most in scopes]
    for row, most in rows:
        if row.count >= most:
            raise LimitReached("You're sending messages very fast. Please wait a minute and try again."
                               if not row.scope.startswith("burst:all") else
                               "Goal Engine is very busy right now. Please try again in a minute.")
    for row, _ in rows:
        row.count += 1
    db.commit()


def prune_bursts(db: Session) -> int:
    from sqlalchemy import delete

    n = db.execute(delete(UsageCounter).where(UsageCounter.scope.like("burst:%"),
                                              UsageCounter.day.not_in((_minute(), _minute(1))))).rowcount
    db.commit()
    return n


def reserve_ai_call(db: Session, visitor_id: str, settings: Settings, client_ip: str = "") -> None:
    if settings.engine_mode == "ml":
        reserve_burst(db, visitor_id, client_ip)
        return
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
