"""Database job routes."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.security.visitor import visitor_id
from app.domain.schemas.db_jobs import DbJobIn, DiagnoseIn, OptimizeIn, RecognizeIn, PlanIn, ApplyIn, UndoIn, ExcelIn
from app.services import db_jobs as service

router = APIRouter()


@router.post("/api/dbbrain/recognize")
def brain_recognize(body: RecognizeIn):
    return service.brain_recognize(body)


@router.get("/api/dbbrain/playbook/{issue_id}")
def brain_playbook(issue_id: str):
    return service.brain_playbook(issue_id)


@router.post("/api/connectors/{connector_id}/diagnose")
def brain_diagnose(connector_id: int, body: DiagnoseIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.brain_diagnose(connector_id, body, db, visitor)


@router.post("/api/connectors/{connector_id}/optimize")
def brain_optimize(connector_id: int, body: OptimizeIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.brain_optimize(connector_id, body, db, visitor)


@router.post("/api/connectors/{connector_id}/jobs")
def create_db_job(connector_id: int, body: DbJobIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.create_db_job(connector_id, body, db, visitor)


@router.post("/api/connectors/{connector_id}/plan")
def plan_change(connector_id: int, body: PlanIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.plan_change(connector_id, body, db, visitor)


@router.post("/api/connectors/{connector_id}/apply")
def apply_change(connector_id: int, body: ApplyIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.apply_change(connector_id, body, db, visitor)


@router.post("/api/connectors/{connector_id}/undo")
def undo_change(connector_id: int, body: UndoIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.undo_change(connector_id, body, db, visitor)


@router.get("/api/connector-jobs/{job_id}")
def get_db_job(job_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.get_db_job(job_id, db, visitor)


@router.get("/api/connector-jobs/{job_id}/download")
def download_db_job(job_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.download_db_job(job_id, db, visitor)


@router.post("/api/connector-jobs/{job_id}/excel")
def db_job_excel(job_id: int, body: ExcelIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.db_job_excel(job_id, body, db, visitor)


@router.get("/api/connector-jobs/{job_id}/excel")
def db_job_excel_file(job_id: int, tag: str = "", db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return service.db_job_excel_file(job_id, tag, db, visitor)
