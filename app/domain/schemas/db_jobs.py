"""Validation schemas for db_jobs."""

from pydantic import BaseModel, Field


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
