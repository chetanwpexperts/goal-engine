"""Validation schemas for console."""

from pydantic import BaseModel


class ConnectIn(BaseModel):
    type: str = "mysql"
    host: str = ""
    port: str = ""
    database: str = ""
    user: str = ""
    password: str = ""


class SqlIn(BaseModel):
    sql: str = ""


class AskIn(BaseModel):
    text: str = ""
