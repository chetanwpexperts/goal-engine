"""Pattern count persistence."""
from datetime import timedelta
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from app.domain.models import PatternStep, PersonalStep, utcnow


def record(db: Session, brain: str, history: list[str], new: str, who: str | None = None, *, any_pattern: str) -> None:
    """Count one step: the patterns before -> the pattern asked now. Labels only."""
    if not new or not history:
        return
    p1 = history[-1]
    p2 = history[-2] if len(history) >= 2 else any_pattern
    now = utcnow()
    for a in ([p2, any_pattern] if p2 != any_pattern else [any_pattern]):
        row = db.scalars(select(PatternStep).where(PatternStep.brain == brain, PatternStep.prev2 == a,
                                                   PatternStep.prev1 == p1, PatternStep.nxt == new)).first()
        if row is None:
            row = PatternStep(brain=brain, prev2=a, prev1=p1, nxt=new, weight=0.0)
            db.add(row)
        row.weight = (row.weight or 0.0) + 1.0
        row.updated_at = now
    if who:
        row = db.scalars(select(PersonalStep).where(PersonalStep.who == who, PersonalStep.brain == brain,
                                                    PersonalStep.prev1 == p1, PersonalStep.nxt == new)).first()
        if row is None:
            row = PersonalStep(who=who, brain=brain, prev1=p1, nxt=new, weight=0.0)
            db.add(row)
        row.weight = (row.weight or 0.0) + 1.0
        row.updated_at = now
    db.flush()


def counts(db: Session, brain: str, p2: str, p1: str) -> dict[str, float]:
    rows = db.scalars(select(PatternStep).where(PatternStep.brain == brain, PatternStep.prev2 == p2, PatternStep.prev1 == p1))
    return {r.nxt: r.weight for r in rows}


def personal_counts(db, who, brain, p1):
    return {r.nxt: r.weight for r in db.scalars(select(PersonalStep).where(PersonalStep.who == who, PersonalStep.brain == brain,
                                                                         PersonalStep.prev1 == p1))}


def decay(db: Session, now, *, factor, drop_below, one_off_age, personal_ttl, any_pattern) -> int:
    """Once a day: old habits fade so new ones show up, and one-off paths are dropped.
    (The clean-up runs often; this only acts every ~24h.)"""
    meta = db.scalars(select(PatternStep).where(PatternStep.brain == "_meta", PatternStep.prev2 == "decay")).first()
    if meta is not None and meta.updated_at and meta.updated_at > now - timedelta(hours=23):
        return 0
    if meta is None:
        meta = PatternStep(brain="_meta", prev2="decay", prev1=any_pattern, nxt=any_pattern, weight=1.0)
        db.add(meta)
    meta.updated_at = now
    for model in (PatternStep, PersonalStep):
        for r in db.scalars(select(model).where(model.brain != "_meta")):
            r.weight = (r.weight or 0.0) * factor
    db.flush()
    n = 0
    for model in (PatternStep, PersonalStep):
        n += db.execute(delete(model).where(model.brain != "_meta", model.weight < drop_below)).rowcount or 0
        n += db.execute(delete(model).where(model.brain != "_meta", model.weight <= 1.0, model.updated_at < now - one_off_age)).rowcount or 0
    n += db.execute(delete(PersonalStep).where(PersonalStep.updated_at < now - personal_ttl)).rowcount or 0
    return n


def delete_visitor(db, who):
    if not who:
        return 0
    return db.execute(delete(PersonalStep).where(PersonalStep.who == who)).rowcount or 0


def top_paths(db: Session, brain: str, limit: int = 20, *, any_pattern: str) -> list[dict]:
    rows = db.scalars(select(PatternStep).where(PatternStep.brain == brain, PatternStep.prev2 == any_pattern)
                      .order_by(PatternStep.weight.desc()).limit(limit))
    return [{"after": r.prev1, "next": r.nxt, "weight": round(r.weight, 2)} for r in rows]
