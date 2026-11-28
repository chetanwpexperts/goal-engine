"""Minified public JavaScript route (registered before static mount)."""
from fastapi import APIRouter, Depends
from app.core.paths import STATIC
from app.security.permissions import require

router = APIRouter()
_MIN_JS: dict = {}


@router.get("/static/app.js", include_in_schema=False, dependencies=[Depends(require("public"))])
def app_js():
    """The page's script, minified: no comments or notes about how things work reach the browser."""
    from fastapi.responses import Response

    path = STATIC / "app.js"
    key = path.stat().st_mtime
    if _MIN_JS.get("key") != key:
        import rjsmin

        _MIN_JS.update(key=key, body=rjsmin.jsmin(path.read_text(encoding="utf-8"), keep_bang_comments=False))
    return Response(_MIN_JS["body"], media_type="application/javascript", headers={"Cache-Control": "no-cache"})
