"""Site availability and preview-cookie checks."""

import hmac
import os

from fastapi import Request


PREVIEW_COOKIE = "ge_preview"


def _site_open() -> bool:
    return os.getenv("SITE_MODE", "closed").strip().lower() == "open"


def _preview_token() -> str:
    key = os.getenv("PREVIEW_KEY", "")
    return hmac.new(key.encode(), b"goal-engine-preview", "sha256").hexdigest() if len(key) >= 16 else ""


def _has_preview(request: Request) -> bool:
    token = _preview_token()
    return bool(token) and hmac.compare_digest(request.cookies.get(PREVIEW_COOKIE, ""), token)
