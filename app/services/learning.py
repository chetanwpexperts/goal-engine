"""Learning use cases."""
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from app.security.admin import _is_admin
from app.domain.schemas.learning import LearnVoteIn, LearnAskIn, LearnStatusIn


def learn_choice(body: LearnVoteIn, db: Session, visitor: str):
    from app import learning

    try:
        return learning.vote(db, body.brain, body.text, body.label, visitor)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def learn_ask(body: LearnAskIn, db: Session):
    from app import learning

    try:
        learning.ask_to_learn(db, body.brain, body.text, body.guess, body.confidence)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


def admin_learning(request: Request, status: str, db: Session):
    from app import learning

    if not _is_admin(request):
        raise HTTPException(404, "Not Found")
    return learning.overview(db, status)


def admin_learning_set(kind: str, item_id: int, body: LearnStatusIn, request: Request, db: Session):
    from app import learning

    if not _is_admin(request) or not learning.set_status(db, kind, item_id, body.status):
        raise HTTPException(404, "Not Found")
    return {"ok": True}
