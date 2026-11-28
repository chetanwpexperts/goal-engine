"""Chat and goal routes."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from app.db import get_db
from app.security.visitor import visitor_id
from app.domain.schemas.chat import MessageIn, StatusIn
from app.services import chat as service

router = APIRouter()


@router.get("/api/state")
def state(db: Session = Depends(get_db), visitor: str = Depends(visitor_id)) -> dict:
    return service.state(db, visitor)


@router.post("/api/message")
def post_message(body: MessageIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.post_message(body, request, db, visitor)


@router.post("/api/new-chat")
def new_chat(request: Request) -> JSONResponse:
    return service.new_chat(request)


@router.get("/api/goals/{goal_id}")
def get_goal(goal_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)) -> dict:
    return service.get_goal(goal_id, db, visitor)


@router.post("/api/goals/{goal_id}/status")
def set_status(goal_id: int, body: StatusIn, db: Session = Depends(get_db),
               visitor: str = Depends(visitor_id)) -> dict:
    return service.set_status(goal_id, body, db, visitor)
