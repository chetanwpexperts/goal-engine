"""Validation schemas for dbarea."""

from pydantic import BaseModel


class CodeIn(BaseModel):
    code: str = ""
