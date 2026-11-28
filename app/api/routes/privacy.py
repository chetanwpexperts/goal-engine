"""Privacy routes."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.security.visitor import visitor_id
from app.services import privacy as service

router = APIRouter()


@router.post("/api/privacy/delete-all")
def delete_all(request: Request, response: Response, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.delete_all(request, response, db, visitor)
