"""Declarative route permissions, using the application's existing authenticators.

Product-specific checks are supplied by each application at construction time,
so this module never imports a product or a service. Missing checks deny access.
Ownership and request-specific safeguards remain the responsibility of the route.
"""
from collections.abc import Callable
import hmac
import os

from fastapi import HTTPException, Request

from app.security.admin import _is_admin
from app.security.visitor import visitor_id


PERMISSIONS = frozenset({
    "public", "visitor", "account", "admin", "api_key", "local_screen", "local_setup",
})


def require(perm: str, hidden: bool = False) -> Callable:
    """Return a FastAPI dependency; reject misspelled permissions immediately.

    hidden=True answers a refused caller 404 instead of 401, for areas whose existence is not shown to guests."""
    if perm not in PERMISSIONS:
        raise ValueError(f"Unknown permission: {perm}")

    def check(request: Request) -> None:
        if perm == "public":
            return
        if perm == "visitor":
            allowed = bool(visitor_id(request)) if hasattr(request.state, "visitor_id") else False
        elif perm == "account":
            # accounts.resolve has already validated the session and populated state.
            allowed = getattr(request.state, "user", None) is not None
        elif perm == "admin":
            allowed = _is_admin(request)
        elif perm == "api_key" and request.url.path == "/api/learning/feed":
            want = os.getenv("LEARN_KEY", "")
            allowed = len(want) >= 16 and hmac.compare_digest(request.query_params.get("key", ""), want)
        else:
            route_check = getattr(request.app.state, "route_permission", None)
            result = route_check(request, perm) if route_check is not None else None
            if result is None:
                checks = getattr(request.app.state, "permission_checks", {})
                validator = checks.get(perm)
                allowed = validator is not None and validator(request)
            else:
                allowed = result
        if not allowed:
            if hidden or perm == "admin" or (perm == "api_key" and request.url.path == "/api/learning/feed"):
                raise HTTPException(status_code=404, detail="Not Found")
            reject = getattr(request.app.state, "permission_denied", None)
            if reject is not None:
                reject(request, perm)
            raise HTTPException(status_code=401, detail="Not authorized")

    return check
