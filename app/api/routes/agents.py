"""Agent and owner verification HTTP routes."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.schemas.agents import StartIn, OwnerIn
from app.security.visitor import visitor_id
from app.services import agents as service

router = APIRouter()


@router.post("/api/agents/{agent_id}/start")
def start_agent(agent_id: int, body: StartIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.start_agent(agent_id, body, db, visitor)


@router.post("/api/agents/{agent_id}/preview")
def preview_agent(agent_id: int, body: StartIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.preview_agent(agent_id, body, db, visitor)


@router.post("/api/agents/{agent_id}/activate")
def activate_agent(agent_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.activate_agent(agent_id, db, visitor)


@router.post("/api/owner/code")
def owner_code(body: OwnerIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.owner_code(body, db, visitor, request.app.state.mailer)


@router.post("/api/owner/verify")
def owner_verify(body: OwnerIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.owner_verify(body, db, visitor)


@router.get("/api/agents")
def my_agents(db: Session = Depends(get_db), visitor: str = Depends(visitor_id)) -> dict:
    return service.my_agents(db, visitor)


@router.post("/api/agents/{agent_id}/connect/{connector_id}")
def link_connector(agent_id: int, connector_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.link_connector(agent_id, connector_id, db, visitor)


@router.post("/api/agents/{agent_id}/pause")
def pause_agent(agent_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.pause_agent(agent_id, db, visitor)
