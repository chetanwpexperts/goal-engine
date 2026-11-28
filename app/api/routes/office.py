"""Office file HTTP routes."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.schemas.office import OfficeStartIn, OfficeFixIn, OfficeCompareIn
from app.security.visitor import visitor_id
from app.services import office as service

from app.security.permissions import require

router = APIRouter()


@router.post("/api/office/files", dependencies=[Depends(require("visitor"))])
def office_start(body: OfficeStartIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.office_start(body, request, db, visitor)


@router.put("/api/office/files/{token}/chunk", dependencies=[Depends(require("visitor"))])
async def office_chunk(token: str, offset: int, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return await service.office_chunk(token, offset, request, db, visitor)


@router.post("/api/office/files/{token}/done", dependencies=[Depends(require("visitor"))])
def office_done(token: str, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.office_done(token, db, visitor)


@router.get("/api/office/answer", dependencies=[Depends(require("visitor"))])
def office_answer(goal_id: int, name: str, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.office_answer(goal_id, name, db, visitor)


@router.get("/api/office/files/{token}", dependencies=[Depends(require("visitor"))])
def office_get(token: str, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.office_get(token, db, visitor)


@router.post("/api/office/files/{token}/fix", dependencies=[Depends(require("visitor"))])
def office_fix(token: str, body: OfficeFixIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.office_fix(token, body, db, visitor)


@router.post("/api/office/files/{token}/compare", dependencies=[Depends(require("visitor"))])
def office_compare(token: str, body: OfficeCompareIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.office_compare(token, body, db, visitor)


@router.get("/api/office/files/{token}/download", dependencies=[Depends(require("visitor"))])
def office_download(token: str, what: str = "file", db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.office_download(token, what, db, visitor)


@router.post("/api/office/files/{token}/undo", dependencies=[Depends(require("visitor"))])
def office_undo(token: str, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.office_undo(token, db, visitor)


@router.delete("/api/office/files/{token}", dependencies=[Depends(require("visitor"))])
def office_delete(token: str, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.office_delete(token, db, visitor)
