"""What the AI must return. Anything that doesn't fit is rejected, never guessed."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


Relation = Literal["new_goal", "update_goal", "supporting_task", "chit_chat"]
ActionType = Literal["ask", "investigate", "retrieve", "execute", "wait"]


class MissingInfo(BaseModel):
    question: str = Field(max_length=300)
    why: str = Field(default="", max_length=300)


class NextAction(BaseModel):
    type: ActionType
    description: str = Field(max_length=600)


class Interpretation(BaseModel):
    relation: Relation
    goal_ref: int | None = None          # existing goal id for update_goal / supporting_task
    detected_language: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=200)
    interpreted_problem: str = Field(default="", max_length=1200)
    desired_goal: str = Field(default="", max_length=800)
    known_facts: list[str] = Field(default_factory=list)
    missing_info: list[MissingInfo] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    sub_goals: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    next_action: NextAction = NextAction(type="ask", description="")
    reply: str = Field(max_length=1500)

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(1.0, value))

    @field_validator("known_facts", "constraints", "sub_goals")
    @classmethod
    def _clean_list(cls, items: list[str]) -> list[str]:
        cleaned = [str(i).strip()[:300] for i in items if str(i).strip()]
        return cleaned[:15]

    @field_validator("missing_info")
    @classmethod
    def _max_three(cls, items: list[MissingInfo]) -> list[MissingInfo]:
        return items[:3]  # only real blockers, never a questionnaire
