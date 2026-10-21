"""The persistent goal state lives here, separate from the chat transcript."""

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# List fields are stored as JSON in TEXT columns, so MySQL and SQLite behave the same.
def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active / done / dropped
    title: Mapped[str] = mapped_column(String(200))
    original_request: Mapped[str] = mapped_column(Text)
    detected_language: Mapped[str] = mapped_column(String(40), default="")
    interpreted_problem: Mapped[str] = mapped_column(Text, default="")
    desired_goal: Mapped[str] = mapped_column(Text, default="")
    known_facts: Mapped[str] = mapped_column(Text, default="[]")
    missing_info: Mapped[str] = mapped_column(Text, default="[]")
    constraints: Mapped[str] = mapped_column(Text, default="[]")
    sub_goals: Mapped[str] = mapped_column(Text, default="[]")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    next_action_type: Mapped[str] = mapped_column(String(20), default="ask")
    next_action: Mapped[str] = mapped_column(Text, default="")
    updates: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "title": self.title,
            "original_request": self.original_request,
            "detected_language": self.detected_language,
            "interpreted_problem": self.interpreted_problem,
            "desired_goal": self.desired_goal,
            "known_facts": loads(self.known_facts, []),
            "missing_info": loads(self.missing_info, []),
            "constraints": loads(self.constraints, []),
            "sub_goals": loads(self.sub_goals, []),
            "confidence": round(self.confidence or 0, 2),
            "next_action": {"type": self.next_action_type, "description": self.next_action},
            "updates": self.updates,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "status": self.status,
                "updated_at": self.updated_at.isoformat() if self.updated_at else None}


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id"), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(12))  # user / assistant
    text: Mapped[str] = mapped_column(Text)
    relation: Mapped[str] = mapped_column(String(24), default="")  # how it touched the goal state
    provider: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("day", "scope", name="uq_usage_day_scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[str] = mapped_column(String(10))
    scope: Mapped[str] = mapped_column(String(80))  # "all" or "visitor:<id>"
    count: Mapped[int] = mapped_column(Integer, default=0)
