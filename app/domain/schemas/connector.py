"""Validation schemas for connector."""

from pydantic import BaseModel


class ConnectorIn(BaseModel):
    name: str = ""
    agent_id: int | None = None


class DirectIn(BaseModel):
    type: str = "mysql"
    host: str = ""
    port: str = ""
    database: str = ""
    user: str = ""
    password: str = ""
    name: str = ""
    agent_id: int | None = None
    allow_changes: bool = False
