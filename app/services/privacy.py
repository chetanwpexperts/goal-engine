"""Delete all data for the current browser and account."""
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from app import accounts, privacy
from app.security.visitor import COOKIE
from app.services.visitor_history import PREV_COOKIE, _earlier_ids


def delete_all(request: Request, response: Response, db: Session, visitor: str):
    counts = {}
    for vid in [visitor] + _earlier_ids(request):
        for k, v in privacy.delete_visitor(db, vid).items():
            counts[k] = counts.get(k, 0) + (v or 0)
    user = accounts.current_user(request)
    if user is not None:
        counts["account"] = accounts.delete_account(db, user.id)
        response.delete_cookie(accounts.SESSION_COOKIE)
    response.delete_cookie(COOKIE)
    response.delete_cookie(PREV_COOKIE)
    return {"ok": True, "deleted": counts}
