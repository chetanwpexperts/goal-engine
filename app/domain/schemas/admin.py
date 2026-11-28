"""Validation schemas for admin."""

from pydantic import BaseModel


class EnquiryUpdate(BaseModel):
    status: str | None = None
    note: str | None = None


class MissUpdate(BaseModel):
    status: str


class TryIn(BaseModel):
    text: str = ""
