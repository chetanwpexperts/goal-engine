"""Validation schemas for expert."""

from pydantic import BaseModel


class ExpertIn(BaseModel):
    goal_id: int | None = None
    need: str = ""
    name: str = ""
    phone: str = ""
    email: str = ""
    contact: str = "whatsapp"
    consent: bool = False
    website: str = ""          # honeypot


class ExpertUpdate(BaseModel):
    status: str | None = None
    note: str | None = None
