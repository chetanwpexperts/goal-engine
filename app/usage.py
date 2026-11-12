"""How much each part of Goal Engine is used, per day: numbers only, never what anyone typed or saw.

Stored in usage_counters as scope 'use:<name>' and day 'YYYY-MM-DD' (the flood counters there use 'burst:').
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UsageCounter, utcnow

NAMES = {"workspace_ask": "DBaba workspace: questions asked", "workspace_run": "DBaba workspace: queries run",
         "workspace_connect": "DBaba workspace: databases connected", "enquiry": "Enquiries sent"}


def count(db: Session, name: str, n: int = 1) -> None:
    from app.limits import _counter

    try:
        row = _counter(db, utcnow().strftime("%Y-%m-%d"), f"use:{name}"[:80])
        row.count += n
        db.commit()
    except Exception:  # noqa: BLE001 - a count must never break the work it counts
        db.rollback()


def series(db: Session, name: str, days: int = 30) -> list[int]:
    """Counts for the last `days` days, oldest first (today last)."""
    today = utcnow().date()
    want = [(today - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(days - 1, -1, -1)]
    got = dict(db.execute(select(UsageCounter.day, UsageCounter.count).where(UsageCounter.scope == f"use:{name}",
                                                                              UsageCounter.day.in_(want))).all())
    return [int(got.get(d, 0)) for d in want]
