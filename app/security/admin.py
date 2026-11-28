"""Administrator cookie validation."""

import hmac
import os

from fastapi import Request


ADMIN_COOKIE = "ge_admin"


def _admin_token() -> str:
    key = os.getenv("ADMIN_KEY", "")
    return hmac.new(key.encode(), b"goal-engine-admin", "sha256").hexdigest() if len(key) >= 16 else ""


def _is_admin(request: Request) -> bool:
    token = _admin_token()
    return bool(token) and hmac.compare_digest(request.cookies.get(ADMIN_COOKIE, ""), token)
