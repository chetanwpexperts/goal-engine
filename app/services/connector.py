"""Connector protocol and connection use cases."""
import asyncio
import logging
import time
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from app import accounts, dbarea
from app.products.dbaba import database as dbagent
from app.products.shared import direct as dbagent_direct
from app.domain.models import dumps, loads
from app.domain.schemas.connector import ConnectorIn, DirectIn
from app.services.agents import own_agent, _agent_payload
from app.services.urls import _public_base
from app.services.db_reports import _after_db_job


def _connector_from(request: Request, db: Session):
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    connector = dbagent.authenticate(db, token)
    if connector is None:
        raise HTTPException(401, "Unknown or disconnected connector")
    return connector


async def connector_hello(request: Request, db: Session):
    connector = _connector_from(request, db)
    info = await request.json()
    dbagent.hello(db, connector, info if isinstance(info, dict) else {})
    dbagent.queue(db, connector, "schema")   # fresh table list every time it starts
    if connector.can_write:
        logging.getLogger(__name__).info("Connector %s reports a database user that can write", connector.id)
    return {"ok": True, "poll_seconds": 4, "long_poll": 20}


async def connector_poll(request: Request, db: Session):
    """Long poll: the connector's request waits here (up to ?wait= seconds, max 25) until a job arrives,
    so a job starts the moment it's queued instead of on the next 4-second check."""
    connector = await run_in_threadpool(_connector_from, request, db)
    try:
        wait = max(0.0, min(25.0, float(request.query_params.get("wait", "0"))))
    except ValueError:
        wait = 0.0
    deadline = time.monotonic() + wait
    while True:
        jobs = await run_in_threadpool(dbagent.claim, db, connector)
        if jobs or time.monotonic() >= deadline or await request.is_disconnected():
            break
        await asyncio.sleep(0.4)
    return {"jobs": [{"id": j.id, "kind": j.kind, "payload": loads(j.payload, {})} for j in jobs]}


async def connector_upload(job_id: int, request: Request, db: Session):
    """The connector streams an export here (gzip). Written to disk piece by piece, never held in memory."""
    from app.domain.models import ConnectorJob

    connector = await run_in_threadpool(_connector_from, request, db)
    job = await run_in_threadpool(db.get, ConnectorJob, job_id)
    if job is None or job.connector_id != connector.id or job.kind != "export" or job.status != "claimed":
        return JSONResponse({"error": "Unknown job"}, status_code=400)
    path = dbagent.save_upload_path(job)
    size = 0
    try:
        with open(path, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > dbagent.MAX_UPLOAD_BYTES:
                    raise ValueError("too big")
                fh.write(chunk)
    except ValueError:
        path.unlink(missing_ok=True)
        return JSONResponse({"error": "The export is too big. Add a WHERE condition or fewer columns"}, status_code=413)
    job.file_path = str(path)
    await run_in_threadpool(db.commit)
    return {"ok": True}


async def connector_result(job_id: int, request: Request, db: Session):
    connector = _connector_from(request, db)
    data = await request.json()
    try:
        job = dbagent.complete(db, connector, job_id, data if isinstance(data, dict) else {})
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _after_db_job(db, job)
    return {"ok": True}


def create_connector(body: ConnectorIn, request: Request, db: Session, visitor: str):
    try:
        connector, token = dbagent.create_connector(db, visitor, body.name)
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if body.agent_id:
        agent = own_agent(db, body.agent_id, visitor)
        inputs = loads(agent.inputs, {}) or {}
        inputs["connector_id"] = connector.id
        agent.inputs = dumps(inputs)
        db.commit()
    base = _public_base(request)
    command = f"curl -fsSL {base}/connector/install.sh | bash -s -- {token}"
    return {"connector": dbagent.connector_dict(connector, db), "token": token, "command": command}


def connect_direct(body: DirectIn, request: Request, db: Session, visitor: str):
    # the address nginx saw (the right-most forwarded one); the left-most part can be faked by the client
    fwd = [x.strip() for x in (request.headers.get("x-forwarded-for") or "").split(",") if x.strip()]
    client_ip = fwd[-1] if fwd else (request.client.host if request.client else "")
    user = db.get(accounts.User, accounts.current_user(request).id) if accounts.current_user(request) else None
    form = body.model_dump()
    try:
        if user is not None and dbarea.enabled():
            dbarea.before_connect(db, user, (form.get("host") or "").strip().lower()[:200], client_ip)
        connector = dbagent_direct.connect(db, visitor, form, body.name, allow_changes=body.allow_changes,
                                           client_ip=client_ip)
    except (dbagent_direct.DirectError, dbagent.DatabaseError) as exc:
        if user is not None:
            dbarea.record(db, user, form, client_ip, False, str(exc)[:200])
        return JSONResponse({"error": str(exc), "use_command": bool(getattr(exc, "use_command", False))}, status_code=400)
    if user is not None:
        dbarea.record(db, user, form, client_ip, True)
        dbarea.tell_owner(user, connector.host_label or "", connector.db_name or "", client_ip)
    if body.agent_id:
        agent = own_agent(db, body.agent_id, visitor)
        inputs = loads(agent.inputs, {}) or {}
        inputs["connector_id"] = connector.id
        agent.inputs = dumps(inputs)
        db.commit()
        return _agent_payload(db, agent, visitor)
    return {"connector": dbagent.connector_dict(connector, db)}


def connector_info():
    return {"server_ip": dbagent_direct.server_ip()}


def list_connectors(db: Session, visitor: str):
    return {"connectors": [dbagent.connector_dict(c, db) for c in dbagent.visitor_connectors(db, visitor)]}


def disconnect_connector(connector_id: int, db: Session, visitor: str):
    try:
        connector = dbagent.own_connector(db, connector_id, visitor)
    except dbagent.DatabaseError:
        raise HTTPException(404, "Connector not found")
    connector.status = "disconnected"
    connector.secret = None          # forget the saved password
    db.commit()
    return {"ok": True}


def connector_activity(connector_id: int, after: int, db: Session, visitor: str):
    try:
        connector = dbagent.own_connector(db, connector_id, visitor)
    except dbagent.DatabaseError:
        raise HTTPException(404, "Not found")
    return {"items": dbagent.activity(db, connector, after), "online": dbagent.is_online(connector), "status": connector.status}
