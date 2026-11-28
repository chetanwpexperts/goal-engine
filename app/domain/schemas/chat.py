"""Validation schemas for chat."""

from typing import Literal

from pydantic import BaseModel


class MessageIn(BaseModel):
    text: str
    goal_id: int | None = None   # the goal the person is looking at


class StatusIn(BaseModel):
    status: Literal["active", "done", "dropped"]
