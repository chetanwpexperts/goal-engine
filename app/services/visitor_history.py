"""Signed earlier visitor identities."""
import hmac
import re
from fastapi import Request
from app import privacy

PREV_COOKIE = "ge_prev"
VISITOR_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


def _earlier_ids(request: Request) -> list[str]:
    """Earlier anonymous ids of this browser (from 'start fresh'), signed so nobody can add someone else's."""
    raw = request.cookies.get(PREV_COOKIE, "")
    ids, _, sig = raw.rpartition(".")
    if not ids or not hmac.compare_digest(sig, privacy.fingerprint("prev:" + ids)[2:]):
        return []
    return [i for i in ids.split(",") if VISITOR_RE.match(i)]
