"""Validation schemas for accounts."""

from pydantic import BaseModel


class SignupIn(BaseModel):
    full_name: str = ""
    phone: str = ""
    email: str = ""
    location: str = ""
    username: str = ""
    password: str = ""
    why: str = ""
    website: str = ""          # honeypot: people never see it


class LoginIn(BaseModel):
    login: str = ""            # email or username
    password: str = ""


class CodeIn(BaseModel):
    code: str = ""


class ForgotIn(BaseModel):
    email: str = ""


class ResetIn(BaseModel):
    email: str = ""
    code: str = ""
    password: str = ""
