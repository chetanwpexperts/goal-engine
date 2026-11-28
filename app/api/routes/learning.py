"""Learning routes."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from app.db import get_db
from app.security.visitor import visitor_id
from app.domain.schemas.learning import LearnVoteIn, LearnAskIn, LearnStatusIn
from app.services import learning as service

router = APIRouter()


@router.post("/api/learn/choice")
def learn_choice(body: LearnVoteIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.learn_choice(body, db, visitor)


@router.post("/api/learn/ask")
def learn_ask(body: LearnAskIn, db: Session = Depends(get_db)):
    return service.learn_ask(body, db)


@router.get("/api/admin/learning")
def admin_learning(request: Request, status: str = "new", db: Session = Depends(get_db)):
    return service.admin_learning(request, status, db)


@router.post("/api/admin/learning/{kind}/{item_id}")
def admin_learning_set(kind: str, item_id: int, body: LearnStatusIn, request: Request, db: Session = Depends(get_db)):
    return service.admin_learning_set(kind, item_id, body, request, db)
