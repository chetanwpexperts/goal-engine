"""Public URL resolution for outbound links."""

import os
from fastapi import Request


def _public_base(request: Request) -> str:
    if os.getenv("PUBLIC_URL"):
        return os.getenv("PUBLIC_URL", "").rstrip("/")
    base = str(request.base_url).rstrip("/")
    if request.headers.get("x-forwarded-proto") == "https" and base.startswith("http://"):
        base = "https://" + base[7:]
    return base
