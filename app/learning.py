"""How every brain upgrades itself from real users, without AI and without keeping anyone's details.

Two loops, the same for every brain (each agent pack, plus the general one):

1. "I meant this one": when a brain isn't sure it shows its best guesses. The person picks the right one.
   When 3 different people pick the same meaning for the same sentence, that sentence becomes a training
   example (LearnedPhrase) and the brain retrains itself within minutes. One person can't teach it alone,
   so nobody can poison it; Chetan can reject anything from /admin.
2. "None of these": the sentence goes to the learning list (LearnRequest). The nightly learning reads the
   most-asked ones first and writes real knowledge for them (playbooks, answers), tested before it's kept.

Only a scrubbed sentence is stored: no emails, links, IPs, phone numbers, long numbers, keys or passwords,
and no visitor id (votes keep a keyed one-way fingerprint so the same person counts once).
"""

import hashlib
import re
import time
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import LearnedPhrase, LearnRequest, LearnVote, utcnow
from app.ml.normalize import normalize

def _brains() -> tuple[str, ...]:
    from app.mind import packs

    return tuple(packs.all_packs()) + ("general",)


class _Brains(tuple):
    """Every agent pack's brain plus 'general'; a new pack joins learning by itself."""

    def __contains__(self, item):
        return item in _brains()

    def __iter__(self):
        return iter(_brains())


BRAINS = _Brains()
VOTES_TO_LEARN = 3          # different people agreeing
AGREEMENT = 0.75            # share of the votes for that sentence
VOTES_PER_DAY = 30          # per person
REQUEST_TTL = timedelta(days=180)
VOTE_TTL = timedelta(days=90)

_SCRUB = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"\b(?:https?://|www\.)\S+", re.I), "<link>"),
    (re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|in|net|org|io|co|dev|app|info|biz|xyz|shop|site|online)\b\S*", re.I), "<link>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"(?i)\b(password|passwd|pass|pwd|token|secret|api[_ -]?key|key)\b\s*[:=]?\s*\S+"), r"\1 <hidden>"),
    (re.compile(r"\+?\d[\d\s-]{8,}\d"), "<number>"),
    (re.compile(r"\b\d{6,}\b"), "<number>"),          # error codes (4-5 digits) stay: they're the useful part
    (re.compile(r"\b[A-Za-z0-9_\-]{24,}\b"), "<hidden>"),
    (re.compile(r"'[^']{1,80}'|\"[^\"]{1,80}\""), "'…'"),   # quoted values in error messages
]


def scrub(text: str) -> str:
    t = " ".join((text or "").split())[:600]
    for rx, rep in _SCRUB:
        t = rx.sub(rep, t)
    return t[:300]


def _fp(brain: str, text: str) -> str:
    return hashlib.sha256(f"{brain}\n{normalize(text)}".encode()).hexdigest()


# ------------------------------------------------------------------ what each brain can mean
def labels(brain: str) -> set[str]:
    if brain == "general":
        from app.ml.intent import training_examples

        return set(training_examples(include_learned=False)[1])
    from app.mind import packs

    fn = packs.hook(brain, "labels")          # the agent's pack names where its meanings come from
    return set(fn()) if fn is not None else set()


# ------------------------------------------------------------------ loop 1: "I meant this one"
def vote(db: Session, brain: str, text: str, label: str, visitor_id: str) -> dict:
    from app.privacy import fingerprint

    if brain not in BRAINS or label not in labels(brain):
        raise ValueError("unknown meaning")
    clean = scrub(text)
    if len(normalize(clean)) < 3:
        raise ValueError("too short")
    voter = fingerprint(f"learn:{visitor_id}")
    since = utcnow() - timedelta(days=1)
    if db.scalar(select(func.count()).select_from(LearnVote).where(LearnVote.voter == voter, LearnVote.created_at >= since)) >= VOTES_PER_DAY:
        return {"learned": False, "counted": False}
    fp = _fp(brain, clean)
    row = db.scalar(select(LearnVote).where(LearnVote.phrase_fp == fp, LearnVote.voter == voter))
    if row:
        row.label, row.created_at = label, utcnow()     # changed their mind: one person, one vote
    else:
        db.add(LearnVote(brain=brain, phrase_fp=fp, text=clean, label=label, voter=voter))
    db.flush()
    learned = _maybe_learn(db, brain, fp, clean)
    db.commit()
    return {"learned": learned, "counted": True}


def _maybe_learn(db: Session, brain: str, fp: str, text: str) -> bool:
    votes = list(db.scalars(select(LearnVote.label).where(LearnVote.phrase_fp == fp)))
    if len(votes) < VOTES_TO_LEARN:
        return False
    top = max(set(votes), key=votes.count)
    if votes.count(top) < VOTES_TO_LEARN or votes.count(top) / len(votes) < AGREEMENT:
        return False
    phrase = db.scalar(select(LearnedPhrase).where(LearnedPhrase.brain == brain, LearnedPhrase.phrase_fp == fp))
    if phrase:
        if phrase.status == "rejected" or phrase.label == top:
            return False
        phrase.label = top
    else:
        db.add(LearnedPhrase(brain=brain, phrase_fp=fp, text=text, label=top))
    _version_cache.clear()
    return True


# ------------------------------------------------------------------ loop 2: "None of these"
def ask_to_learn(db: Session, brain: str, text: str, guess: str = "", confidence: float = 0.0) -> None:
    if brain not in BRAINS:
        raise ValueError("unknown brain")
    clean = scrub(text)
    if len(normalize(clean)) < 3:
        raise ValueError("too short")
    fp = _fp(brain, clean)
    row = db.scalar(select(LearnRequest).where(LearnRequest.fingerprint == fp))
    if row:
        row.count += 1
        row.last_seen = utcnow()
        if row.status == "learned":
            row.status = "new"           # learned, but people still don't get the right answer
    else:
        db.add(LearnRequest(brain=brain, fingerprint=fp, text=clean, guess=(guess or "")[:80], confidence=float(confidence or 0)))
    db.commit()


# ------------------------------------------------------------------ what the brains train on
_version_cache: dict = {}


def examples(brain: str) -> list[tuple[str, str]]:
    """Learned sentences for one brain (cached a few minutes so every worker picks them up soon)."""
    hit = _version_cache.get(brain)
    if hit and time.monotonic() - hit[0] < 120:
        return hit[1]
    rows: list[tuple[str, str]] = []
    try:
        from app.db import _SessionLocal, get_engine

        get_engine()
        with _SessionLocal() as db:
            rows = [(p.text, p.label) for p in db.scalars(select(LearnedPhrase).where(
                LearnedPhrase.brain == brain, LearnedPhrase.status.in_(("auto", "approved"))).order_by(LearnedPhrase.id))]
    except Exception:  # noqa: BLE001 - learning is a bonus; never break understanding
        rows = hit[1] if hit else []
    _version_cache[brain] = (time.monotonic(), rows)
    return rows


# ------------------------------------------------------------------ Chetan's view + the nightly feed
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
    _version_cache.clear()
    return True


def feed(db: Session, limit: int = 50) -> list[dict]:
    """For the nightly learning: the most-asked problems no brain understood yet."""
    rows = db.scalars(select(LearnRequest).where(LearnRequest.status == "new")
                      .order_by(LearnRequest.count.desc(), LearnRequest.last_seen.desc()).limit(limit))
    return [{"id": r.id, "brain": r.brain, "text": r.text, "asked": r.count, "best_guess": r.guess} for r in rows]


def maintain(db: Session, now=None) -> dict:
    now = now or utcnow()
    done = {
        "learn_votes": db.execute(delete(LearnVote).where(LearnVote.created_at < now - VOTE_TTL)).rowcount,
        "learn_requests": db.execute(delete(LearnRequest).where(LearnRequest.last_seen < now - REQUEST_TTL)).rowcount,
    }
    return done


def delete_voter(db: Session, visitor_id: str) -> int:
    from app.privacy import fingerprint

    return db.execute(delete(LearnVote).where(LearnVote.voter == fingerprint(f"learn:{visitor_id}"))).rowcount
