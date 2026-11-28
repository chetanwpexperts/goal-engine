"""Expert requests and administrative request management."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.schemas.expert import ExpertIn, ExpertUpdate
from app.security.visitor import visitor_id
from app.services import expert

router = APIRouter()


@router.post("/api/expert")
def ask_expert(body: ExpertIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return expert.ask_expert(body, request, db, visitor)


@router.get("/api/expert")
def my_expert_requests(db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return expert.my_expert_requests(db, visitor)


@router.get("/api/admin/requests")
def admin_requests(request: Request, status: str = "", db: Session = Depends(get_db)):
    return expert.admin_requests(request, status, db)


@router.post("/api/admin/requests/{req_id}")
def admin_update(req_id: int, body: ExpertUpdate, request: Request, db: Session = Depends(get_db)):
    return expert.admin_update(req_id, body, request, db)
