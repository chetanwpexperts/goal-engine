"""What the person will most likely ask next, as a question pattern (not exact words).

Three sources, most personal first:
- the person's own habits (returning visitors; labels only, under a one-way fingerprint of their browser id);
- everyone's steps (anonymous), using the last two patterns, then the last one;
- the agent's starting chains from its pack (so the very first visitor already gets good guesses).
Each layer only takes over as it has enough observations (ALPHA). Counts fade once a day, and one-off paths that never
repeat are dropped, so the counts follow real, current habits.
"""

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.mind import packs
from app.models import PatternStep, PersonalStep, utcnow

ANY = "*"
ALPHA = 3.0          # observations needed before the learned counts outweigh the layer below
PERSONAL_ALPHA = 2.0
DECAY = 0.97         # daily fade
DROP_BELOW = 0.05
ONE_OFF_AGE = timedelta(days=14)        # seen once and never again in two weeks: noise
PERSONAL_TTL = timedelta(days=180)


def prior(brain: str, last: str | None) -> dict[str, float]:
    chains = packs.chains(brain)
    return chains.get(last or "other") or chains.get("other", {})


def _who(visitor: str | None) -> str | None:
    if not visitor:
        return None
    from app.privacy import fingerprint

    return fingerprint(f"mind:{visitor}")


def record(db: Session, brain: str, history: list[str], new: str, visitor: str | None = None) -> None:
    """Count one step: the patterns before -> the pattern asked now. Labels only."""
    if not new or not history:
        return
    p1 = history[-1]
    p2 = history[-2] if len(history) >= 2 else ANY
    now = utcnow()
    for a in ([p2, ANY] if p2 != ANY else [ANY]):
        row = db.scalars(select(PatternStep).where(PatternStep.brain == brain, PatternStep.prev2 == a,
                                                   PatternStep.prev1 == p1, PatternStep.nxt == new)).first()
        if row is None:
            row = PatternStep(brain=brain, prev2=a, prev1=p1, nxt=new, weight=0.0)
            db.add(row)
        row.weight = (row.weight or 0.0) + 1.0
        row.updated_at = now
    who = _who(visitor)
    if who:
        row = db.scalars(select(PersonalStep).where(PersonalStep.who == who, PersonalStep.brain == brain,
                                                    PersonalStep.prev1 == p1, PersonalStep.nxt == new)).first()
        if row is None:
            row = PersonalStep(who=who, brain=brain, prev1=p1, nxt=new, weight=0.0)
            db.add(row)
        row.weight = (row.weight or 0.0) + 1.0
        row.updated_at = now
    db.flush()


def _counts(db: Session, brain: str, p2: str, p1: str) -> dict[str, float]:
    rows = db.scalars(select(PatternStep).where(PatternStep.brain == brain, PatternStep.prev2 == p2, PatternStep.prev1 == p1))
    return {r.nxt: r.weight for r in rows}


def _blend(counts: dict[str, float], below: dict[str, float], alpha: float) -> dict[str, float]:
    n = sum(counts.values())
    keys = set(below) | set(counts)
    return {k: (counts.get(k, 0.0) + alpha * below.get(k, 0.0)) / (n + alpha) for k in keys}


def scores(db: Session, brain: str, history: list[str], visitor: str | None = None) -> dict[str, float]:
    """P(next pattern): the person's own habits -> everyone's last two -> everyone's last one -> starting chains."""
    p1 = history[-1] if history else None
    out = dict(prior(brain, p1))
    if not p1:
        return out
    out = _blend(_counts(db, brain, ANY, p1), out, ALPHA)
    if len(history) >= 2:
        out = _blend(_counts(db, brain, history[-2], p1), out, ALPHA)
    who = _who(visitor)
    if who:
        mine = {r.nxt: r.weight for r in db.scalars(select(PersonalStep).where(PersonalStep.who == who, PersonalStep.brain == brain,
                                                                                 PersonalStep.prev1 == p1))}
        if mine:
            out = _blend(mine, out, PERSONAL_ALPHA)
    return out


def decay(db: Session, now=None) -> int:
    """Once a day: old habits fade so new ones show up, and one-off paths are dropped.
    (The clean-up runs often; this only acts every ~24h.)"""
    now = now or utcnow()
    meta = db.scalars(select(PatternStep).where(PatternStep.brain == "_meta", PatternStep.prev2 == "decay")).first()
    if meta is not None and meta.updated_at and meta.updated_at > now - timedelta(hours=23):
        return 0
    if meta is None:
        meta = PatternStep(brain="_meta", prev2="decay", prev1=ANY, nxt=ANY, weight=1.0)
        db.add(meta)
    meta.updated_at = now
    for model in (PatternStep, PersonalStep):
        for r in db.scalars(select(model).where(model.brain != "_meta")):
            r.weight = (r.weight or 0.0) * DECAY
    db.flush()
    n = 0
    for model in (PatternStep, PersonalStep):
        n += db.execute(delete(model).where(model.brain != "_meta", model.weight < DROP_BELOW)).rowcount or 0
        n += db.execute(delete(model).where(model.brain != "_meta", model.weight <= 1.0, model.updated_at < now - ONE_OFF_AGE)).rowcount or 0
    n += db.execute(delete(PersonalStep).where(PersonalStep.updated_at < now - PERSONAL_TTL)).rowcount or 0
    return n


def delete_visitor(db: Session, visitor: str) -> int:
    who = _who(visitor)
    if not who:
        return 0
    return db.execute(delete(PersonalStep).where(PersonalStep.who == who)).rowcount or 0


def top_paths(db: Session, brain: str, limit: int = 20) -> list[dict]:
    rows = db.scalars(select(PatternStep).where(PatternStep.brain == brain, PatternStep.prev2 == ANY)
                      .order_by(PatternStep.weight.desc()).limit(limit))
    return [{"after": r.prev1, "next": r.nxt, "weight": round(r.weight, 2)} for r in rows]
