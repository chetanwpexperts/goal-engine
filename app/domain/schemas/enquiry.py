"""Validation schemas for enquiry."""

from pydantic import BaseModel


class EnquiryIn(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    company: str = ""
    role: str = ""
    interest: list[str] = []
    team: str = ""
    message: str = ""
    consent: bool = False
    source: str = ""
    website: str = ""          # honeypot: people never see it, bots fill it
    elapsed_ms: int = 0        # time spent on the form
    captcha_token: str = ""
    captcha: str = ""          # the characters typed from the picture
