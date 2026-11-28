"""Database report scheduling route."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.security.visitor import visitor_id
from app.domain.schemas.db_jobs import DbReportIn
from app.services import db_reports as service

from app.security.permissions import require

router = APIRouter()


@router.post("/api/agents/{agent_id}/db-report", dependencies=[Depends(require("visitor"))])
def schedule_db_report(agent_id: int, body: DbReportIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.schedule_db_report(agent_id, body, db, visitor)
