"""Persistence for learning records; callers supply cleaned text and decisions."""
from datetime import timedelta
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from app.models import LearnedPhrase, LearnRequest, LearnVote, LearnMiss, utcnow

REQUEST_TTL = timedelta(days=180)
VOTE_TTL = timedelta(days=90)


def learned_phrases(db: Session, brain: str) -> list[tuple[str, str]]:
    return [(p.text, p.label) for p in db.scalars(select(LearnedPhrase).where(
        LearnedPhrase.brain == brain, LearnedPhrase.status.in_(("auto", "approved"))).order_by(LearnedPhrase.id))]


def load_examples(brain: str) -> list[tuple[str, str]]:
    from app.db import _SessionLocal, get_engine

    get_engine()
    with _SessionLocal() as db:
        return learned_phrases(db, brain)


def votes_today(db, voter, since):
    return db.scalar(select(func.count()).select_from(LearnVote).where(LearnVote.voter == voter, LearnVote.created_at >= since))


def find_vote(db, fp, voter):
    return db.scalar(select(LearnVote).where(LearnVote.phrase_fp == fp, LearnVote.voter == voter))


def upsert_vote(db, brain, fp, text, label, voter):
    row = find_vote(db, fp, voter)
    if row:
        row.label, row.created_at = label, utcnow()
    else:
        db.add(LearnVote(brain=brain, phrase_fp=fp, text=text, label=label, voter=voter))
    db.flush()


def vote_labels(db, fp):
    return list(db.scalars(select(LearnVote.label).where(LearnVote.phrase_fp == fp)))


def find_phrase(db, brain, fp):
    row = db.scalar(select(LearnedPhrase).where(LearnedPhrase.brain == brain, LearnedPhrase.phrase_fp == fp))
    return {"status": row.status, "label": row.label} if row else None


def save_phrase(db, brain, fp, text, label):
    row = db.scalar(select(LearnedPhrase).where(LearnedPhrase.brain == brain, LearnedPhrase.phrase_fp == fp))
    if row:
        row.label = label
    else:
        db.add(LearnedPhrase(brain=brain, phrase_fp=fp, text=text, label=label))


def commit(db):
    db.commit()


def record_request(db: Session, brain: str, fp: str, text: str, guess: str, confidence: float):
    row = db.scalar(select(LearnRequest).where(LearnRequest.fingerprint == fp))
    if row:
        row.count += 1
        row.last_seen = utcnow()
        if row.status == "learned":
            row.status = "new"           # learned, but people still don't get the right answer
    else:
        db.add(LearnRequest(brain=brain, fingerprint=fp, text=text, guess=(guess or "")[:80], confidence=float(confidence or 0)))
    db.commit()


def record_miss(db: Session, brain: str, fp: str, said: str, detail: str, answer: str):
    row = db.scalar(select(LearnMiss).where(LearnMiss.fingerprint == fp))
    if row:
        row.count += 1
        row.last_seen = utcnow()
        row.said = said or row.said
        if row.status == "learned":
            row.status = "new"
    else:
        db.add(LearnMiss(brain=brain, fingerprint=fp, said=said, detail=detail, answer=answer))
    db.commit()


def overview(db: Session, status: str = "new", limit: int = 100) -> dict:
    reqs = db.scalars(select(LearnRequest).where(LearnRequest.status == status if status else True)
                      .order_by(LearnRequest.count.desc(), LearnRequest.last_seen.desc()).limit(limit))
    phrases = db.scalars(select(LearnedPhrase).order_by(LearnedPhrase.id.desc()).limit(limit))
    return {
        "requests": [{"id": r.id, "brain": r.brain, "text": r.text, "guess": r.guess, "confidence": r.confidence, "count": r.count,
                      "status": r.status, "last_seen": r.last_seen.isoformat()} for r in reqs],
        "learned": [{"id": p.id, "brain": p.brain, "text": p.text, "label": p.label, "status": p.status,
                     "created_at": p.created_at.isoformat()} for p in phrases],
    }


def set_status(db: Session, kind: str, item_id: int, status: str) -> bool:
    model, allowed = {"request": (LearnRequest, {"new", "learned", "ignored"}),
                      "phrase": (LearnedPhrase, {"auto", "approved", "rejected"})}.get(kind, (None, set()))
    if model is None or status not in allowed:
        return False
    row = db.get(model, item_id)
    if not row:
        return False
    row.status = status
    db.commit()
    return True


def feed(db: Session, limit: int = 50) -> list[dict]:
    """For the nightly learning: the most-asked problems no brain understood yet, then answers people said were wrong."""
    from app.models import LearnMiss

    rows = db.scalars(select(LearnRequest).where(LearnRequest.status == "new")
                      .order_by(LearnRequest.count.desc(), LearnRequest.last_seen.desc()).limit(limit))
    out = [{"id": r.id, "brain": r.brain, "text": r.text, "asked": r.count, "best_guess": r.guess} for r in rows]
    misses = db.scalars(select(LearnMiss).where(LearnMiss.status == "new")
                        .order_by(LearnMiss.count.desc(), LearnMiss.last_seen.desc()).limit(limit))
    out += [{"id": f"miss-{m.id}", "brain": m.brain, "text": m.said, "detail": m.detail, "answer": m.answer,
             "asked": m.count, "best_guess": "", "kind": "said_wrong"} for m in misses]
    return out


def maintain(db: Session, now=None) -> dict:
    now = now or utcnow()
    done = {
        "learn_votes": db.execute(delete(LearnVote).where(LearnVote.created_at < now - VOTE_TTL)).rowcount,
        "learn_requests": db.execute(delete(LearnRequest).where(LearnRequest.last_seen < now - REQUEST_TTL)).rowcount,
    }
    from app.models import LearnMiss

    done["learn_misses"] = db.execute(delete(LearnMiss).where(LearnMiss.last_seen < now - REQUEST_TTL)).rowcount
    return done


def delete_voter(db: Session, voter: str) -> int:
    return db.execute(delete(LearnVote).where(LearnVote.voter == voter)).rowcount
