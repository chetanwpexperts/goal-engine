"""Agent and owner verification HTTP routes."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.schemas.agents import StartIn, OwnerIn
from app.security.visitor import visitor_id
from app.services import agents as service

from app.security.permissions import require

router = APIRouter()


@router.post("/api/agents/{agent_id}/start", dependencies=[Depends(require("visitor"))])
def start_agent(agent_id: int, body: StartIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.start_agent(agent_id, body, db, visitor)


@router.post("/api/agents/{agent_id}/preview", dependencies=[Depends(require("visitor"))])
def preview_agent(agent_id: int, body: StartIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.preview_agent(agent_id, body, db, visitor)


@router.post("/api/agents/{agent_id}/activate", dependencies=[Depends(require("visitor"))])
def activate_agent(agent_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.activate_agent(agent_id, db, visitor)


@router.post("/api/owner/code", dependencies=[Depends(require("visitor"))])
def owner_code(body: OwnerIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.owner_code(body, db, visitor, request.app.state.mailer)


@router.post("/api/owner/verify", dependencies=[Depends(require("visitor"))])
def owner_verify(body: OwnerIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.owner_verify(body, db, visitor)


@router.get("/api/agents", dependencies=[Depends(require("visitor"))])
def my_agents(db: Session = Depends(get_db), visitor: str = Depends(visitor_id)) -> dict:
    return service.my_agents(db, visitor)


@router.post("/api/agents/{agent_id}/connect/{connector_id}", dependencies=[Depends(require("visitor"))])
def link_connector(agent_id: int, connector_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.link_connector(agent_id, connector_id, db, visitor)


@router.post("/api/agents/{agent_id}/pause", dependencies=[Depends(require("visitor"))])
def pause_agent(agent_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.pause_agent(agent_id, db, visitor)
