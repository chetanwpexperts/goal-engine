"""Validation schemas for office."""

from pydantic import BaseModel


class OfficeStartIn(BaseModel):
    name: str
    size: int
    agent_id: int | None = None


class OfficeFixIn(BaseModel):
    ops: list[str]


class OfficeCompareIn(BaseModel):
    other: str
    key: str = ""
