"""Anonymous visitor identity."""

from fastapi import Request


COOKIE = "ge_vid"


def visitor_id(request: Request) -> str:
    return request.state.visitor_id
