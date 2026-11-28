"""Remaining website pages and public status routes."""
import hmac
from pathlib import Path
from fastapi import APIRouter, Request, Depends
from fastapi.responses import FileResponse, JSONResponse, Response
from app.core.config import get_settings
from app.core.paths import STATIC
from app.security.admin import ADMIN_COOKIE, _admin_token, _is_admin
from app.security.headers import CONSOLE_CSP
from app.services.urls import _public_base
ADMIN_CSP = CONSOLE_CSP
from app.security.permissions import require

router = APIRouter()


@router.get("/admin", dependencies=[Depends(require("public"))])
def admin_page(request: Request, key: str = "") -> Response:
    from fastapi.responses import RedirectResponse

    token = _admin_token()
    if key:
        response = RedirectResponse("/admin", status_code=303)
        if token and hmac.compare_digest(hmac.new(key.encode(), b"goal-engine-admin", "sha256").hexdigest(), token):
            response.set_cookie(ADMIN_COOKIE, token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="strict",
                                secure=get_settings().cookie_secure)
        return response
    if not _is_admin(request):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return FileResponse(STATIC / "admin.html", headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store",
                                                        "Content-Security-Policy": ADMIN_CSP, "X-Frame-Options": "DENY",
                                                        "Referrer-Policy": "no-referrer"})


@router.get("/connector/install.sh", dependencies=[Depends(require("public"))])
def connector_installer(request: Request) -> Response:
    text = (STATIC.parent.parent / "connector" / "install.sh").read_text()
    return Response(text.replace("__SERVER__", _public_base(request)), media_type="text/x-shellscript")


@router.get("/connector/goal_connector.py", dependencies=[Depends(require("public"))])
def connector_script() -> FileResponse:
    return FileResponse(STATIC.parent.parent / "connector" / "goal_connector.py", media_type="text/x-python")


@router.get("/console", include_in_schema=False, dependencies=[Depends(require("public"))])
def console_page() -> FileResponse:
    """The SQL console: its own page, its own strict rules (no other site's code, no framing, nothing cached)."""
    return FileResponse(STATIC / "console.html", headers={"Content-Security-Policy": CONSOLE_CSP, "X-Frame-Options": "DENY",
                                                          "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
                                                          "X-Robots-Tag": "noindex, nofollow"})


@router.get("/api/known", dependencies=[Depends(require("public"))])
def known() -> dict:
    """How many problems, errors, questions and fixes the agents know (grows every night)."""
    import glob
    import json as _json

    n = 0
    for f in glob.glob(str(STATIC.parent / "dbknow" / "*.json")) + \
            glob.glob(str(STATIC.parent / "officeknow" / "*.json")):
        try:
            data = _json.loads(Path(f).read_text(encoding="utf-8"))
            if isinstance(data, list):              # lists of problems/errors/formulas; dict files are settings, not fixes
                n += len(data)
        except (OSError, ValueError):
            continue
    return {"fixes": n}


@router.get("/api/status", dependencies=[Depends(require("public"))])
def live_status():
    """Home screen: one quiet line that the agents are up."""
    return {"online": True}
