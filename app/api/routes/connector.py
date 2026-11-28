"""Connector routes."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from app.db import get_db
from app.security.visitor import visitor_id
from app.domain.schemas.connector import ConnectorIn, DirectIn
from app.services import connector as service

router = APIRouter()


@router.post("/api/connector/hello")
async def connector_hello(request: Request, db: Session = Depends(get_db)):
    return await service.connector_hello(request, db)


@router.post("/api/connector/poll")
async def connector_poll(request: Request, db: Session = Depends(get_db)):
    return await service.connector_poll(request, db)


@router.post("/api/connector/upload/{job_id}")
async def connector_upload(job_id: int, request: Request, db: Session = Depends(get_db)):
    return await service.connector_upload(job_id, request, db)


@router.post("/api/connector/result/{job_id}")
async def connector_result(job_id: int, request: Request, db: Session = Depends(get_db)):
    return await service.connector_result(job_id, request, db)


@router.post("/api/connectors")
def create_connector(body: ConnectorIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.create_connector(body, request, db, visitor)


@router.post("/api/connectors/direct")
def connect_direct(body: DirectIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.connect_direct(body, request, db, visitor)


@router.get("/api/connector-info")
def connector_info():
    return service.connector_info()


@router.get("/api/connectors")
def list_connectors(db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.list_connectors(db, visitor)


@router.post("/api/connectors/{connector_id}/disconnect")
def disconnect_connector(connector_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.disconnect_connector(connector_id, db, visitor)


@router.get("/api/connectors/{connector_id}/activity")
def connector_activity(connector_id: int, after: int = 0, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.connector_activity(connector_id, after, db, visitor)
