"""Database job and diagnosis use cases."""
import os
import re
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from app.agents import database as dbagent
from app.domain.schemas.db_jobs import DbJobIn, DiagnoseIn, OptimizeIn, RecognizeIn, PlanIn, ApplyIn, UndoIn, ExcelIn


def _own_conn(db: Session, connector_id: int, visitor: str):
    try:
        return dbagent.own_connector(db, connector_id, visitor)
    except dbagent.DatabaseError:
        raise HTTPException(404, "Not found")


def _own_job(db: Session, job_id: int, visitor: str):
    from app.models import Connector, ConnectorJob

    job = db.get(ConnectorJob, job_id)
    connector = db.get(Connector, job.connector_id) if job else None
    if job is None or connector is None or connector.visitor_id != visitor:
        raise HTTPException(404, "Job not found")
    return job


def brain_recognize(body: RecognizeIn):
    from app.dbknow import brain

    rec = brain.recognize(body.text)
    hi = body.lang != "en"
    alts = [{**a, "text": brain.describe(a, hi)} for a in rec.get("alternatives", [])]
    return {**rec, "alternatives": [a for a in alts if a["text"]], "text": brain.describe(rec, hi)}


def brain_playbook(issue_id: str):
    from app.dbknow import service

    issue = service.playbook(issue_id) or service.error_entry(issue_id)
    if issue is None:
        raise HTTPException(404, "Not found")
    return issue


def brain_diagnose(connector_id: int, body: DiagnoseIn, db: Session, visitor: str):
    """Run a playbook's own read-only checks on this database."""
    from app.dbknow import service

    try:
        job, issue, checks = service.start_checks(db, _own_conn(db, connector_id, visitor), body.issue_id)
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"job": dbagent.job_dict(job), "playbook": issue, "checks": checks}


def brain_optimize(connector_id: int, body: OptimizeIn, db: Session, visitor: str):
    """The solving loop: try the fixes on the real database and prove which one is the same answer and faster."""
    from app.dbknow import service

    try:
        job, analysis = service.start_optimize(db, _own_conn(db, connector_id, visitor), body.sql)
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"job": dbagent.job_dict(job), "analysis": analysis}


def create_db_job(connector_id: int, body: DbJobIn, db: Session, visitor: str):
    try:
        connector = dbagent.own_connector(db, connector_id, visitor)
        sql, summary = body.sql, None
        if body.table and body.kind == "query":
            sql, summary = dbagent.table_preview_sql(body.table), f"Preview · {body.table}"
        elif body.table and body.kind == "export":
            sql, summary = dbagent.table_export_sql(body.table), f"Excel · {body.table}"
        job = dbagent.queue(db, connector, body.kind, {"sql": sql} if sql else {}, summary=summary)
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return dbagent.job_dict(job)


def plan_change(connector_id: int, body: PlanIn, db: Session, visitor: str):
    """Step 1: show exactly which rows a change would touch. Nothing is changed."""
    try:
        return dbagent.job_dict(dbagent.queue(db, _own_conn(db, connector_id, visitor), "plan", {"sql": body.sql}))
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


def apply_change(connector_id: int, body: ApplyIn, db: Session, visitor: str):
    """Step 2: run exactly what was previewed (once, within 10 minutes), saving a copy for Undo first."""
    try:
        return dbagent.job_dict(dbagent.approve(db, _own_conn(db, connector_id, visitor), body.plan_id))
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


def undo_change(connector_id: int, body: UndoIn, db: Session, visitor: str):
    try:
        return dbagent.job_dict(dbagent.request_undo(db, _own_conn(db, connector_id, visitor), body.change_id, body.force))
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


def get_db_job(job_id: int, db: Session, visitor: str):
    return dbagent.job_dict(_own_job(db, job_id, visitor))


def download_db_job(job_id: int, db: Session, visitor: str):
    job = _own_job(db, job_id, visitor)
    if not job.file_path or not os.path.exists(job.file_path):
        raise HTTPException(404, "No file for this job")
    if job.file_path.endswith(".gz"):
        # sent compressed; the browser unpacks it, so the user simply gets a .csv
        return FileResponse(job.file_path, media_type="text/csv", filename=f"export_{job.id}.csv",
                            headers={"Content-Encoding": "gzip", "Cache-Control": "no-store"})
    return FileResponse(job.file_path, media_type="text/csv", filename=f"export_{job.id}.csv")


def db_job_excel(job_id: int, body: ExcelIn, db: Session, visitor: str):
    """S3: the export as a workbook: Data, a Pivot with live COUNTIFS / SUMIFS and a chart, About. Nothing is sent anywhere."""
    from app.agents import sqlexcel
    from app.models import Connector

    job = _own_job(db, job_id, visitor)
    try:
        info = sqlexcel.for_job(job, db.get(Connector, job.connector_id), body.by or None, body.measure or None, body.hi, body.words)
    except sqlexcel.ExcelError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    p = info["pivot"]
    return {"say": info["say"], "notes": info["notes"], "chart": info["chart"], "rows": info["rows"], "sheets": info["sheets"],
            "columns": info["columns"],
            "pivot": {k: p[k] for k in ("by", "measure", "mode", "groups", "formulas")} if p else None,
            "download": f"/api/connector-jobs/{job.id}/excel?tag={info['tag']}"}


def db_job_excel_file(job_id: int, tag: str, db: Session, visitor: str):
    job = _own_job(db, job_id, visitor)
    path = f"{job.file_path}.{tag}.xlsx" if job.file_path and re.fullmatch(r"[0-9a-f]{10}", tag or "") else ""
    if not path or not os.path.exists(path):
        raise HTTPException(404, "No workbook for this job")
    return FileResponse(path, filename=f"export_{job.id}.xlsx", headers={"Cache-Control": "no-store"},
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
