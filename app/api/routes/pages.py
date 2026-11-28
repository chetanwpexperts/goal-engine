"""Public website pages and preview entry."""

import hmac

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from app.core.config import get_settings
from app.core.paths import STATIC
from app.security.headers import ENQUIRY_CSP
from app.security.site import PREVIEW_COOKIE, _preview_token, _site_open

router = APIRouter()


@router.get("/robots.txt")
def robots() -> Response:
    return Response("User-agent: *\nDisallow: /\n" if not _site_open() else "User-agent: *\nAllow: /\n", media_type="text/plain")


@router.get("/preview")
def preview(key: str = "") -> Response:
    """Private entry while the site is closed: /preview?key=<PREVIEW_KEY>. /preview?off=1 leaves preview."""
    from fastapi.responses import RedirectResponse

    response = RedirectResponse("/", status_code=303)
    token = _preview_token()
    if token and key and hmac.compare_digest(hmac.new(key.encode(), b"goal-engine-preview", "sha256").hexdigest(), token):
        response.set_cookie(PREVIEW_COOKIE, token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax",
                            secure=get_settings().cookie_secure)
    else:
        response.delete_cookie(PREVIEW_COOKIE)
    return response


@router.get("/privacy")
def privacy_page() -> FileResponse:
    return FileResponse(STATIC / "privacy.html")


@router.get("/product")
def product_page() -> FileResponse:
    return FileResponse(STATIC / "product.html")


@router.get("/how-it-works")
def how_page() -> FileResponse:
    return FileResponse(STATIC / "how-it-works.html")


@router.get("/self-hosted")
def self_hosted_page() -> FileResponse:
    return FileResponse(STATIC / "self-hosted.html")


@router.get("/enquiry", include_in_schema=False)
def enquiry_page() -> FileResponse:
    """Talk to us: the enquiry form (app/enquiry.py)."""
    return FileResponse(STATIC / "enquiry.html", headers={"Content-Security-Policy": ENQUIRY_CSP, "X-Frame-Options": "DENY"})


@router.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
