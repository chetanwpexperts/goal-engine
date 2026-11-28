"""Validation schemas for main."""

from typing import Literal

from pydantic import BaseModel, Field
from app.domain.schemas.agents import StartIn, OwnerIn  # compatibility re-exports
from app.domain.schemas.office import OfficeStartIn, OfficeFixIn, OfficeCompareIn  # compatibility re-exports


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


class MessageIn(BaseModel):
    text: str
    goal_id: int | None = None   # the goal the person is looking at


class StatusIn(BaseModel):
    status: Literal["active", "done", "dropped"]


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


class DbJobIn(BaseModel):
    kind: str
    sql: str = ""
    table: str = ""


class DbReportIn(BaseModel):
    sql: str = ""
    table: str = ""
    every: str = "day"
    at: str = "09:00"
    weekday: int = 0
    title: str = ""


class DiagnoseIn(BaseModel):
    issue_id: str


class OptimizeIn(BaseModel):
    sql: str


class RecognizeIn(BaseModel):
    text: str
    lang: str = "en"


class PlanIn(BaseModel):
    sql: str


class ApplyIn(BaseModel):
    plan_id: int


class UndoIn(BaseModel):
    change_id: int
    force: bool = False


class ExcelIn(BaseModel):
    by: str = Field("", max_length=80)          # the column to group by (empty: DBaba picks one, or none)
    measure: str = Field("", max_length=80)     # the number column to add up
    words: str = Field("", max_length=400)      # or the person's own words: "status wise with total amount"
    hi: bool = False
