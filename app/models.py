"""The persistent goal state lives here, separate from the chat transcript."""

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # ML engine state
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True, default="")
    slots: Mapped[str | None] = mapped_column(Text, nullable=True)            # JSON dict
    pending: Mapped[str | None] = mapped_column(String(40), nullable=True)    # question waiting for an answer
    questions_asked: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
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
            "intent": self.intent or "",
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
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("day", "scope", name="uq_usage_day_scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[str] = mapped_column(String(10))
    scope: Mapped[str] = mapped_column(String(80))  # "all" or "visitor:<id>"
    count: Mapped[int] = mapped_column(Integer, default=0)


class Agent(Base):
    """An agent assembled for a goal from a recipe: what it does, its inputs, its schedule."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id"), index=True)
    recipe: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed / needs_input / running / active / done / failed / paused
    inputs: Mapped[str | None] = mapped_column(Text, nullable=True)       # JSON
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")    # running / done / failed
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)       # JSON
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OwnerContact(Base):
    """The business owner's verified email. Nothing is sent to customers until it's verified."""

    __tablename__ = "owner_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visitor_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320))
    business_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    code_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    code_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    codes_today: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SentEmail(Base):
    """Every email an agent sends to someone, for caps, the owner's log, and stopping requests."""

    __tablename__ = "sent_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    to_email: Mapped[str] = mapped_column(String(320), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))   # sent / failed
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Connector(Base):
    """A small program on the user's own server that talks to their database. Its token is shown once."""

    __tablename__ = "connectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_hint: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(20), default="waiting")   # waiting / online / disconnected
    db_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    db_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    can_write: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    host_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(10), nullable=True, default="agent")   # agent (runs on their server) / direct
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)   # direct only: encrypted connection details
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ConnectorJob(Base):
    """One approved piece of work for a connector: look at tables, run a read-only query, export, health."""

    __tablename__ = "connector_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id"), index=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20))        # schema / query / export / explain / health
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)   # queued / claimed / done / failed / expired
    result: Mapped[str | None] = mapped_column(Text(4_000_000), nullable=True)   # MEDIUMTEXT on MySQL
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
