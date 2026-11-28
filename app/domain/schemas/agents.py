"""Validation schemas for agents."""

from pydantic import BaseModel


class StartIn(BaseModel):
    inputs: dict[str, str] = {}


class OwnerIn(BaseModel):
    email: str = ""
    business: str = ""
    code: str = ""
