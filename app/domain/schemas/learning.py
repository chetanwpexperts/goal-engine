"""Validation schemas for learning."""

from pydantic import BaseModel, Field


class LearnVoteIn(BaseModel):
    brain: str
    text: str = Field(max_length=2000)
    label: str = Field(max_length=80)


class LearnAskIn(BaseModel):
    brain: str
    text: str = Field(max_length=2000)
    guess: str = ""
    confidence: float = 0.0


class LearnStatusIn(BaseModel):
    status: str
