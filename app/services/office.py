"""Office-file use cases."""

import os
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app import accounts
from app.domain.models import Agent
from app.domain.schemas.office import OfficeStartIn, OfficeFixIn, OfficeCompareIn
from app.services.goals import own_goal


def _office_call(fn):
    from app.agents.office_agent import OfficeAgentError

    try:
        return fn()
    except OfficeAgentError as exc:
        raise HTTPException(400, str(exc))


def office_start(body: OfficeStartIn, request: Request, db: Session, visitor: str):
    from app.agents import office_agent as oa

    try:
        accounts._limit(db, "upload-ip", accounts.client_ip(request))
    except accounts.AccountError as exc:
        return JSONResponse({"error": "Too many uploads from your network. Please try again in an hour."}, status_code=exc.status)

    if body.agent_id is not None:
        agent = db.get(Agent, body.agent_id)
        if not agent or agent.visitor_id != visitor or agent.recipe != "office":
            raise HTTPException(404, "Not found")
    return _office_call(lambda: oa.file_dict(oa.start_upload(db, visitor, body.agent_id, body.name, body.size)))


async def office_chunk(token: str, offset: int, request: Request, db: Session, visitor: str):
    from app.agents import office_agent as oa

    data = await request.body()
    return _office_call(lambda: oa.file_dict(oa.add_chunk(db, oa.own_file(db, token, visitor), offset, data)))


def office_done(token: str, db: Session, visitor: str):
    from app.agents import office_agent as oa

    return _office_call(lambda: oa.file_dict(oa.finish_upload(db, oa.own_file(db, token, visitor))))


def office_answer(goal_id: int, name: str, db: Session, visitor: str):
    from app.agents import office_agent as oa

    goal = own_goal(db, goal_id, visitor)
    return _office_call(lambda: oa.answer_sheet(db, goal, name[:40]))


def office_get(token: str, db: Session, visitor: str):
    from app.agents import office_agent as oa

    return _office_call(lambda: oa.file_dict(oa.own_file(db, token, visitor)))


def office_fix(token: str, body: OfficeFixIn, db: Session, visitor: str):
    from app.agents import office_agent as oa

    return _office_call(lambda: oa.file_dict(oa.start_fix(db, oa.own_file(db, token, visitor), body.ops)))


def office_compare(token: str, body: OfficeCompareIn, db: Session, visitor: str):
    from app.agents import office_agent as oa

    return _office_call(lambda: oa.file_dict(oa.start_compare(db, oa.own_file(db, token, visitor), oa.own_file(db, body.other, visitor), body.key)))


def office_download(token: str, what: str, db: Session, visitor: str):
    """The latest version of the file (every fix so far), or the comparison report."""
    from app.agents import office_agent as oa

    f = _office_call(lambda: oa.own_file(db, token, visitor))
    if what == "compare":
        path, name = f.result_path, f.result_name or "comparison.xlsx"
    else:
        path, name = (f.path if len(oa.steps(f)) > 1 else None), oa._named(f.name, "fixed")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Not ready")
    return FileResponse(path, filename=name, headers={"Cache-Control": "no-store"})


def office_undo(token: str, db: Session, visitor: str):
    from app.agents import office_agent as oa

    return _office_call(lambda: oa.file_dict(oa.start_undo(db, oa.own_file(db, token, visitor))))


def office_delete(token: str, db: Session, visitor: str):
    from app.agents import office_agent as oa

    f = _office_call(lambda: oa.own_file(db, token, visitor))
    oa.remove_file(f)
    db.commit()
    return {"ok": True}
