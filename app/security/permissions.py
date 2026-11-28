"""Declarative route permissions, using the application's existing authenticators.

Product-specific checks are supplied by each application at construction time,
so this module never imports a product or a service. Missing checks deny access.
Ownership and request-specific safeguards remain the responsibility of the route.
"""
from collections.abc import Callable

from fastapi import HTTPException, Request

from app.security.admin import _is_admin
from app.security.visitor import visitor_id


PERMISSIONS = frozenset({
    "public", "visitor", "account", "admin", "api_key", "local_screen", "local_setup",
})


def require(perm: str) -> Callable:
    """Return a FastAPI dependency; reject misspelled permissions immediately."""
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
        else:
            checks = getattr(request.app.state, "permission_checks", {})
            validator = checks.get(perm)
            allowed = validator is not None and validator(request)
        if not allowed:
            raise HTTPException(status_code=403 if perm == "admin" else 401,
                                detail="Not authorized")

    return check
