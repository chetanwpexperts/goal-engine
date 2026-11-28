"""Learning use cases."""
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from datetime import timedelta
from app.security.admin import _is_admin
from app.security.audit import record
from app.core.redact import scrub
from app.core.text import normalize, fingerprint
from app.engine import learning as rules
from app.data import learning as storage
from app.products.shared.learn import ask_to_learn, report_miss
from app.domain.models import utcnow
from app.privacy import fingerprint as voter_fingerprint
from app.data.learning import overview, feed, maintain
from app.domain.schemas.learning import LearnVoteIn, LearnAskIn, LearnStatusIn


def learn_choice(body: LearnVoteIn, db: Session, visitor: str):
    try:
        return vote(db, body.brain, body.text, body.label, visitor)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def learn_ask(body: LearnAskIn, db: Session):
    try:
        ask_to_learn(db, body.brain, body.text, body.guess, body.confidence)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


def admin_learning(request: Request, status: str, db: Session):
    if not _is_admin(request):
        raise HTTPException(404, "Not Found")
    result = overview(db, status)
    record(db, "admin", "admin_action", "learning", request.client.host if request.client else "")
    db.commit()
    return result


def admin_learning_set(kind: str, item_id: int, body: LearnStatusIn, request: Request, db: Session):
    if not _is_admin(request) or not set_status(db, kind, item_id, body.status):
        raise HTTPException(404, "Not Found")
    record(db, "admin", "admin_action", f"learning:{item_id}", request.client.host if request.client else "")
    db.commit()
    return {"ok": True}


def vote(db, brain, text, label, visitor_id):
    if brain not in rules.BRAINS or label not in rules.labels(brain):
        raise ValueError("unknown meaning")
    clean = scrub(text)
    if len(normalize(clean)) < 3:
        raise ValueError("too short")
    voter = voter_fingerprint(f"learn:{visitor_id}")
    if storage.votes_today(db, voter, utcnow() - timedelta(days=1)) >= rules.VOTES_PER_DAY:
        return {"learned": False, "counted": False}
    fp = fingerprint(brain, clean)
    storage.upsert_vote(db, brain, fp, clean, label, voter)
    top = rules.decide(storage.vote_labels(db, fp))
    learned = False
    if top is not None:
        phrase = storage.find_phrase(db, brain, fp)
        if not phrase or (phrase["status"] != "rejected" and phrase["label"] != top):
            storage.save_phrase(db, brain, fp, clean, top)
            rules._version_cache.clear()
            learned = True
    storage.commit(db)
    return {"learned": learned, "counted": True}


def set_status(db, kind, item_id, status):
    ok = storage.set_status(db, kind, item_id, status)
    if ok:
        rules._version_cache.clear()
    return ok


def delete_voter(db, visitor_id):
    return storage.delete_voter(db, voter_fingerprint(f"learn:{visitor_id}"))
