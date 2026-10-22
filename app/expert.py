"""Human expert hand-off: when an agent can't do the job (or the user just wants a person),
Goal Engine asks "Expert se baat karni hai?", collects a contact, and emails Chetan at once
with a one-tap WhatsApp reply link."""

import os
import re
import time
import urllib.parse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpertRequest, Goal, dumps, loads, utcnow

STATUSES = ("new", "talking", "done", "spam")
PER_VISITOR_PER_DAY = 3
_recent: dict[str, list[float]] = {}


class ExpertError(ValueError):
    pass


def clean_phone(raw: str) -> str:
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if re.fullmatch(r"[6-9]\d{9}", digits):          # Indian mobile without country code
        digits = "+91" + digits
    if re.fullmatch(r"0[6-9]\d{9}", digits):
        digits = "+91" + digits[1:]
    if digits and not digits.startswith("+"):
        digits = "+" + digits
    return digits if re.fullmatch(r"\+\d{8,15}", digits) else ""


def valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}", email or ""))


def create(db: Session, visitor_id: str, form: dict) -> ExpertRequest:
    if form.get("website"):                           # hidden field only bots fill in
        raise ExpertError("Request not accepted")
    if not form.get("consent"):
        raise ExpertError("Please tick the box so Chetan can contact you")
    name = (form.get("name") or "").strip()[:80]
    need = (form.get("need") or "").strip()[:2000]
    phone = clean_phone(form.get("phone", ""))
    email = (form.get("email") or "").strip().lower()[:320]
    contact = form.get("contact") if form.get("contact") in ("whatsapp", "call", "email") else "whatsapp"
    if not name:
        raise ExpertError("Please write your name")
    if len(need) < 10:
        raise ExpertError("Please write a line about what you need")
    if contact in ("whatsapp", "call") and not phone:
        raise ExpertError("Please write a mobile number (with country code if outside India)")
    if contact == "email" and not valid_email(email):
        raise ExpertError("Please write a valid email")
    if email and not valid_email(email):
        email = ""
    now = time.time()
    recent = [t for t in _recent.get(visitor_id, []) if now - t < 86400]
    if len(recent) >= PER_VISITOR_PER_DAY:
        raise ExpertError("You've already sent a few requests today. Chetan will get back to you")
    _recent[visitor_id] = recent + [now]
    goal = None
    if form.get("goal_id"):
        goal = db.get(Goal, int(form["goal_id"]))
        if goal is not None and goal.visitor_id != visitor_id:
            goal = None
    req = ExpertRequest(visitor_id=visitor_id, goal_id=goal.id if goal else None,
                        topic=(goal.title if goal else "General help")[:200], intent=goal.intent if goal else None,
                        need=need, name=name, phone=phone or None, email=email or None, contact=contact,
                        lang="en" if (goal and goal.detected_language == "English") else "hi")
    db.add(req)
    db.commit()
    return req


def whatsapp_link(req: ExpertRequest) -> str:
    if not req.phone:
        return ""
    first = req.name.split()[0] if req.name else ""
    text = (f"Namaste {first}, main Chetan, Goal Engine se. Aapne \"{req.topic}\" ke baare mein expert help maangi thi. Kab baat kar sakte hain?"
            if req.lang != "en" else
            f"Hi {first}, this is Chetan from Goal Engine. You asked for expert help with \"{req.topic}\". When can we talk?")
    return f"https://wa.me/{req.phone.lstrip('+')}?text={urllib.parse.quote(text)}"


def notify(req: ExpertRequest, mailer, base_url: str) -> bool:
    to = os.getenv("EXPERT_EMAIL") or os.getenv("SMTP_USER", "")
    if not to:
        return False
    how = {"whatsapp": "WhatsApp", "call": "Phone call", "email": "Email"}[req.contact]
    lines = [
        f"New expert request #{req.id}",
        "",
        f"Topic (what Goal Engine understood): {req.topic}",
        f"Problem type: {req.intent or '-'}",
        "",
        "In their words:",
        req.need,
        "",
        f"Name: {req.name}",
        f"Wants: {how}",
        f"Phone: {req.phone or '-'}",
        f"Email: {req.email or '-'}",
    ]
    if req.phone:
        lines += ["", f"Reply on WhatsApp (one tap): {whatsapp_link(req)}", f"Call: tel:{req.phone}"]
    lines += ["", f"All requests: {base_url}/admin"]
    try:
        mailer.send(to, f"Goal Engine: expert request · {req.topic[:60]}", "\n".join(lines), reply_to=req.email or None,
                    from_name="Goal Engine")
        return True
    except Exception:  # noqa: BLE001 - the request is saved; it shows on the admin page anyway
        return False


def as_dict(req: ExpertRequest, admin: bool = False) -> dict:
    d = {"id": req.id, "topic": req.topic, "status": req.status, "contact": req.contact,
         "created_at": req.created_at.isoformat()}
    if admin:
        d.update({"need": req.need, "name": req.name, "phone": req.phone, "email": req.email, "intent": req.intent,
                  "note": req.note, "whatsapp": whatsapp_link(req), "lang": req.lang})
    return d


def mine(db: Session, visitor_id: str) -> list[dict]:
    return [as_dict(r) for r in db.scalars(select(ExpertRequest).where(ExpertRequest.visitor_id == visitor_id)
                                           .order_by(ExpertRequest.id.desc()).limit(10))]


def all_requests(db: Session, status: str = "") -> list[dict]:
    q = select(ExpertRequest)
    if status in STATUSES:
        q = q.where(ExpertRequest.status == status)
    return [as_dict(r, admin=True) for r in db.scalars(q.order_by(ExpertRequest.id.desc()).limit(200))]


def update(db: Session, req_id: int, status: str | None, note: str | None) -> ExpertRequest:
    req = db.get(ExpertRequest, req_id)
    if req is None:
        raise ExpertError("Not found")
    if status in STATUSES:
        req.status = status
    if note is not None:
        req.note = note[:2000]
    db.commit()
    return req
