"""What the person will most likely ask next, as a question pattern (not exact words).

Two sources, blended:
- starting chains written from general knowledge of how people work with files, databases and websites (works for the
  very first visitor);
- learned counts from real sessions (anonymous pattern labels only), using the last two patterns, then the last one,
  then the starting chains when there's little data. Old counts fade every night, so recent behaviour weighs more.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PatternStep, utcnow

ANY = "*"
ALPHA = 3.0          # how many real observations it takes before the learned counts outweigh the starting chains
DECAY = 0.97         # nightly fade
DROP_BELOW = 0.05

# ---------------------------------------------------------------- starting chains: P(next pattern | last pattern)
START = {
    "office": {
        "upload": {"review": .22, "rows": .14, "sum_g": .14, "dups": .12, "fix": .1, "describe": .08, "count_g": .1, "month": .1},
        "rows": {"count_g": .24, "sum_g": .16, "list_f": .14, "dups": .12, "sum": .12, "review": .12, "top": .1},
        "count": {"count_g": .3, "list_f": .2, "sum": .15, "rows": .1, "top": .1, "review": .15},
        "count_f": {"count_g": .3, "list_f": .3, "sum_f": .2, "highlight": .1, "formula": .1},
        "count_g": {"list_f": .24, "rank": .2, "sum_g": .2, "pivot": .1, "month": .1, "formula": .08, "highlight": .08},
        "sum": {"sum_g": .34, "month": .2, "top": .15, "avg": .1, "formula": .1, "count": .11},
        "sum_f": {"sum_g": .3, "list_f": .25, "month": .15, "top": .1, "formula": .1, "count_f": .1},
        "sum_g": {"rank": .24, "month": .2, "list_f": .16, "pivot": .14, "top": .1, "formula": .1, "growth": .06},
        "avg": {"sum_g": .3, "top": .2, "rank": .2, "list_f": .15, "formula": .15},
        "month": {"growth": .3, "sum_g": .2, "rank": .15, "top": .15, "formula": .1, "list_f": .1},
        "top": {"list_f": .2, "sum_g": .2, "rank": .2, "highlight": .2, "formula": .1, "month": .1},
        "rank": {"list_f": .3, "top": .2, "month": .15, "sum_g": .15, "highlight": .1, "formula": .1},
        "list_f": {"count_g": .2, "sum_f": .25, "highlight": .25, "dups": .1, "sort": .1, "formula": .1},
        "review": {"fix": .45, "dups": .2, "list_f": .1, "rows": .1, "sum_g": .15},
        "dups": {"fix": .5, "review": .2, "rows": .15, "count": .15},
        "fix": {"review": .3, "rows": .15, "sum_g": .2, "dups": .1, "month": .1, "count_g": .15},
        "lookup": {"review": .2, "missing": .2, "sum_g": .3, "list_f": .3},
        "combine": {"sum_g": .3, "dups": .3, "rows": .2, "count_g": .2},
        "highlight": {"list_f": .3, "count": .3, "sort": .2, "sum_f": .2},
        "describe": {"rows": .2, "sum_g": .2, "review": .2, "dups": .15, "count_g": .15, "month": .1},
        "aging": {"list_f": .3, "sum_g": .3, "highlight": .2, "count_g": .2},
        "pivot": {"rank": .3, "list_f": .2, "month": .2, "sum_g": .3},
        "growth": {"rank": .3, "month": .3, "list_f": .2, "sum_g": .2},
        "formula": {"sum_g": .3, "list_f": .2, "month": .2, "rank": .3},
        "other": {"review": .2, "sum_g": .2, "rows": .15, "dups": .15, "month": .1, "top": .1, "count_g": .1},
    },
    "database": {
        "db_start": {"db_connect": .5, "db_health": .2, "db_explore": .15, "db_slow": .15},
        "db_connect": {"db_health": .35, "db_explore": .3, "db_slow": .2, "db_export": .15},
        "db_health": {"db_slow": .35, "db_fix": .3, "db_report": .2, "db_explore": .15},
        "db_slow": {"db_fix": .45, "db_health": .2, "db_report": .2, "db_explore": .15},
        "db_error": {"db_fix": .5, "db_health": .25, "db_connect": .25},
        "db_explore": {"db_export": .35, "db_health": .25, "db_report": .2, "db_slow": .2},
        "db_export": {"db_report": .4, "db_explore": .3, "db_health": .3},
        "db_report": {"db_health": .4, "db_explore": .3, "db_export": .3},
        "db_fix": {"db_health": .4, "db_report": .3, "db_slow": .3},
        "other": {"db_connect": .4, "db_health": .3, "db_explore": .3},
    },
    "website": {
        "web_problem": {"web_check": .6, "web_slow": .2, "web_watch": .2},
        "web_check": {"web_watch": .4, "web_slow": .25, "web_another": .2, "web_fix": .15},
        "web_slow": {"web_fix": .5, "web_watch": .3, "web_another": .2},
        "web_fix": {"web_check": .5, "web_watch": .3, "web_another": .2},
        "web_watch": {"web_another": .5, "web_check": .3, "web_slow": .2},
        "web_another": {"web_watch": .5, "web_slow": .3, "web_check": .2},
        "other": {"web_check": .5, "web_watch": .3, "web_slow": .2},
    },
}


def prior(brain: str, last: str | None) -> dict[str, float]:
    chains = START.get(brain, {})
    return chains.get(last or "other") or chains.get("other", {})


def record(db: Session, brain: str, history: list[str], new: str) -> None:
    """Count one step: the patterns before -> the pattern asked now. Labels only."""
    if not new or not history:
        return
    p1 = history[-1]
    p2 = history[-2] if len(history) >= 2 else ANY
    now = utcnow()
    for a, b in ((p2, p1), (ANY, p1)):
        row = db.scalars(select(PatternStep).where(PatternStep.brain == brain, PatternStep.prev2 == a,
                                                   PatternStep.prev1 == b, PatternStep.nxt == new)).first()
        if row is None:
            row = PatternStep(brain=brain, prev2=a, prev1=b, nxt=new, weight=0.0)
            db.add(row)
        row.weight = (row.weight or 0.0) + 1.0
        row.updated_at = now
        if p2 == ANY:
            break                              # one row is enough when there is only one pattern before


def _counts(db: Session, brain: str, p2: str, p1: str) -> dict[str, float]:
    rows = db.scalars(select(PatternStep).where(PatternStep.brain == brain, PatternStep.prev2 == p2, PatternStep.prev1 == p1))
    return {r.nxt: r.weight for r in rows}


def scores(db: Session, brain: str, history: list[str]) -> dict[str, float]:
    """P(next pattern), with backoff: last two -> last one -> starting chains."""
    p1 = history[-1] if history else None
    base = prior(brain, p1)
    if not p1:
        return dict(base)
    c1 = _counts(db, brain, ANY, p1)
    n1 = sum(c1.values())
    keys = set(base) | set(c1)
    one = {k: (c1.get(k, 0.0) + ALPHA * base.get(k, 0.0)) / (n1 + ALPHA) for k in keys}
    if len(history) < 2:
        return one
    c2 = _counts(db, brain, history[-2], p1)
    n2 = sum(c2.values())
    keys |= set(c2)
    return {k: (c2.get(k, 0.0) + ALPHA * one.get(k, 0.0)) / (n2 + ALPHA) for k in keys}


def decay(db: Session, now=None) -> int:
    """Once a day: old habits fade so new ones show up. (The clean-up runs often; this only acts every ~24h.)"""
    from datetime import timedelta

    now = now or utcnow()
    meta = db.scalars(select(PatternStep).where(PatternStep.brain == "_meta", PatternStep.prev2 == "decay")).first()
    if meta is not None and meta.updated_at and meta.updated_at > now - timedelta(hours=23):
        return 0
    if meta is None:
        meta = PatternStep(brain="_meta", prev2="decay", prev1=ANY, nxt=ANY, weight=1.0)
        db.add(meta)
    meta.updated_at = now
    for r in db.scalars(select(PatternStep).where(PatternStep.brain != "_meta")):
        r.weight = (r.weight or 0.0) * DECAY
    db.flush()
    return db.execute(delete(PatternStep).where(PatternStep.brain != "_meta", PatternStep.weight < DROP_BELOW)).rowcount or 0


def top_paths(db: Session, brain: str, limit: int = 20) -> list[dict]:
    rows = db.scalars(select(PatternStep).where(PatternStep.brain == brain, PatternStep.prev2 == ANY)
                      .order_by(PatternStep.weight.desc()).limit(limit))
    return [{"after": r.prev1, "next": r.nxt, "weight": round(r.weight, 2)} for r in rows]
