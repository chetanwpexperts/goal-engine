"""The enquiry form: people interested in Goal Engine (DBaba, Sheetu, the website checker, automation agents) tell us who
they are and what they need. Each enquiry lands in the admin panel and emails the owner.

Spam: our own picture captcha (app/captcha.py, no outside service), a hidden field bots fill, a minimum time on the
form, and a few enquiries per address per hour (counted in auth_attempts, so every app process sees the same number).
Privacy: only what the person typed in the form, plus the account it came from when logged in.
"""

import logging
import os
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Enquiry, dumps, loads, utcnow

log = logging.getLogger(__name__)
router = APIRouter()

ROLES = {"developer": "Developer", "dba": "Database admin (DBA)", "business": "Business owner / manager", "analyst": "Analyst / Excel user",
         "student": "Student", "other": "Other"}
INTERESTS = {"dbaba": "DBaba: databases and SQL", "workspace": "DBaba workspace for developers", "sheetu": "Sheetu: Excel and Word files",
             "website": "Website checker", "automation": "Automation agents", "other": "Something else"}
TEAMS = ("just me", "2-10", "11-50", "51+")
STATUSES = ("new", "talking", "customer", "closed", "spam")
PER_HOUR_PER_ADDRESS = 3
KEEP = timedelta(days=365)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


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


class EnquiryError(ValueError):
    def __init__(self, message: str, captcha: bool = False):
        super().__init__(message)
        self.captcha = captcha      # True: the browser shows a new picture


def _clean(text: str, n: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", (text or "").strip())[:n]


def create(db: Session, body: EnquiryIn, visitor_id: str, user_id: int | None, ip: str) -> Enquiry | None:
    """The saved enquiry, or None for a bot (answered as if it worked, so it learns nothing)."""
    if body.website or (0 < body.elapsed_ms < 2500):
        log.info("enquiry: dropped a bot")
        return None
    name, email = _clean(body.name, 80), _clean(body.email, 190).lower()
    if len(name) < 2:
        raise EnquiryError("Please tell us your name.")
    if not EMAIL_RE.match(email):
        raise EnquiryError("Please give an email address we can reply to.")
    if not body.consent:
        raise EnquiryError("Please tick the box so we may contact you about your enquiry.")
    message = _clean(body.message, 4000)
    if len(message) < 5 and not body.interest:
        raise EnquiryError("Tell us a little about what you need, or pick what you're interested in.")
    from app import captcha
    from app.models import AuthAttempt

    try:
        captcha.check(db, body.captcha_token, body.captcha)
    except captcha.CaptchaError as exc:
        raise EnquiryError(str(exc), captcha=True)
    key = f"enquiry-ip:{ip}"[:120]
    recent = db.scalar(select(func.count()).select_from(AuthAttempt)
                       .where(AuthAttempt.key == key, AuthAttempt.at > utcnow() - timedelta(hours=1)))
    if recent >= PER_HOUR_PER_ADDRESS:
        raise EnquiryError("We've received your enquiries. We'll be in touch soon.")
    db.add(AuthAttempt(key=key, at=utcnow()))
    phone = re.sub(r"[^\d+ ()-]", "", body.phone or "")[:30]
    row = Enquiry(name=name, email=email, phone=phone, company=_clean(body.company, 120),
                  role=body.role if body.role in ROLES else "", team=body.team if body.team in TEAMS else "",
                  interest=dumps([i for i in body.interest if i in INTERESTS][:6]), message=message,
                  source=_clean(body.source, 60), visitor_id=visitor_id or "", user_id=user_id)
    db.add(row)
    db.commit()
    from app import usage

    usage.count(db, "enquiry")
    return row


def as_dict(e: Enquiry) -> dict:
    return {"id": e.id, "name": e.name, "email": e.email, "phone": e.phone, "company": e.company,
            "role": ROLES.get(e.role, e.role or ""), "team": e.team, "interest": [INTERESTS.get(i, i) for i in loads(e.interest, [])],
            "message": e.message, "source": e.source, "user_id": e.user_id, "status": e.status, "note": e.note or "",
            "created_at": e.created_at.isoformat(), "updated_at": e.updated_at.isoformat() if e.updated_at else None}


def notify(e: Enquiry, mailer, base_url: str) -> bool:
    to = os.getenv("ENQUIRY_EMAIL") or os.getenv("EXPERT_EMAIL") or os.getenv("SMTP_USER", "")
    if not to:
        return False
    d = as_dict(e)
    lines = [f"New enquiry #{e.id}", "", f"Name: {e.name}", f"Email: {e.email}", f"Phone: {e.phone or '-'}",
             f"Company: {e.company or '-'}", f"Role: {d['role'] or '-'}", f"Team size: {e.team or '-'}",
             f"Interested in: {', '.join(d['interest']) or '-'}", f"From page: {e.source or '-'}",
             f"Has an account: {'yes' if e.user_id else 'no'}", "", "Their message:", e.message or "-", "",
             f"All enquiries: {base_url}/admin#enquiries"]
    try:
        mailer.send(to, f"Goal Engine enquiry · {e.name}{' · ' + e.company if e.company else ''}", "\n".join(lines),
                    reply_to=e.email, from_name="Goal Engine")
        return True
    except Exception:  # noqa: BLE001 - the enquiry is saved either way
        log.exception("Couldn't send the enquiry email")
        return False


def maintain(db: Session) -> int:
    n = db.execute(delete(Enquiry).where(Enquiry.created_at < utcnow() - KEEP)).rowcount
    db.commit()
    return n


def counts(db: Session) -> dict:
    return dict(db.execute(select(Enquiry.status, func.count()).group_by(Enquiry.status)).all())


@router.post("/api/enquiry")
def send_enquiry(body: EnquiryIn, request: Request, db: Session = Depends(get_db)):
    from app import accounts

    me = accounts.current_user(request)
    try:
        row = create(db, body, request.state.visitor_id, me.id if me else None, accounts.client_ip(request))
    except EnquiryError as exc:
        return JSONResponse({"error": str(exc), "captcha": exc.captcha}, status_code=400)
    if row is not None:
        base = str(request.base_url).rstrip("/")
        notify(row, accounts.mailer(), base)
    return {"ok": True}


@router.get("/api/enquiry/captcha")
def new_captcha():
    from app import captcha

    return JSONResponse(captcha.new(), headers={"Cache-Control": "no-store"})
