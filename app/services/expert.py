"""Expert hand-off use cases."""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import expert
from app.domain.schemas.expert import ExpertIn, ExpertUpdate
from app.security.admin import _is_admin
from app.security.audit import record
from app.services.urls import _public_base


def ask_expert(body: ExpertIn, request: Request, db: Session, visitor: str):
    try:
        req = expert.create(db, visitor, body.model_dump())
    except expert.ExpertError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    expert.notify(req, request.app.state.mailer(), _public_base(request))
    return {"ok": True, "request": expert.as_dict(req), "requests": expert.mine(db, visitor)}


def my_expert_requests(db: Session, visitor: str):
    return {"requests": expert.mine(db, visitor)}


def admin_requests(request: Request, status: str, db: Session):
    if not _is_admin(request):
        raise HTTPException(404, "Not Found")
    result = {"requests": expert.all_requests(db, status)}
    record(db, "admin", "admin_action", "expert_requests", request.client.host if request.client else "")
    db.commit()
    return result


def admin_update(req_id: int, body: ExpertUpdate, request: Request, db: Session):
    if not _is_admin(request):
        raise HTTPException(404, "Not Found")
    try:
        result = expert.as_dict(expert.update(db, req_id, body.status, body.note), admin=True)
        record(db, "admin", "admin_action", f"expert_requests:{req_id}", request.client.host if request.client else "")
        db.commit()
        return result
    except expert.ExpertError:
        raise HTTPException(404, "Not Found")
