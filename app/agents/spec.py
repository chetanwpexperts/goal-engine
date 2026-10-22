"""An agent spec: the plan the planner writes and the validator checks before anything runs.

{
  "title": "Payment reminders",
  "kind": "reminder" | "report",
  "schedule": {"every": "day" | "week" | "month" | "hour", "at": "10:00", "weekday": 0-6, "day": 1-28},
  "rule": "due_soon", "days": 3,
  "fields": ["name", "email", "amount", "due_date"],
  "subject": "...", "message_en": "...", "message_hi": "...",
  "send_to": "row_email" | "owner"
}
"""

import re
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.agents.blocks import RULE_FIELDS, RULES, RULES_HI


EVERY = ("hour", "day", "week", "month")
KINDS = ("reminder", "report")
TZ = ZoneInfo("Asia/Kolkata")
WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAYS_HI = ("Somvar", "Mangalvar", "Budhvar", "Guruvar", "Shukravar", "Shanivar", "Ravivar")
MAX_MESSAGE = 1200


def validate(spec: dict) -> list[str]:
    """Problems that make a spec unsafe or impossible. Empty list = OK to show the owner."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["not a plan"]
    if spec.get("kind") not in KINDS:
        errors.append("unknown agent kind")
    if spec.get("rule") not in RULES:
        errors.append("unknown rule for picking rows")
    days = spec.get("days", 3)
    if not isinstance(days, int) or not 0 <= days <= 365:
        errors.append("days must be 0-365")
    sched = spec.get("schedule") or {}
    if sched.get("every") not in EVERY:
        errors.append("schedule must be hourly, daily, weekly or monthly")
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", sched.get("at", "")):
        errors.append("time must be HH:MM")
    if sched.get("every") == "week" and sched.get("weekday") not in range(7):
        errors.append("weekly schedule needs a weekday")
    if sched.get("every") == "month" and sched.get("day") not in range(1, 29):
        errors.append("monthly schedule needs a day 1-28")
    fields = spec.get("fields") or []
    if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
        errors.append("fields must be a list")
    for needed in RULE_FIELDS.get(spec.get("rule"), []):
        if needed not in fields:
            errors.append(f"rule needs the {needed} column")
    if spec.get("send_to") not in ("row_email", "owner"):
        errors.append("send_to must be row_email or owner")
    if spec.get("send_to") == "row_email" and "email" not in fields:
        errors.append("sending to customers needs an email column")
    if spec.get("kind") == "reminder" and spec.get("send_to") != "row_email":
        errors.append("reminders go to the people in the sheet")
    for key in ("message_en", "message_hi"):
        if spec.get("kind") == "reminder" and not (spec.get(key) or "").strip():
            errors.append(f"{key} is empty")
        if len(spec.get(key) or "") > MAX_MESSAGE:
            errors.append(f"{key} is too long")
    if len(spec.get("subject") or "") > 150:
        errors.append("subject is too long")
    return errors


def schedule_text(sched: dict, hi: bool) -> str:
    at = sched.get("at", "09:00")
    every = sched.get("every")
    if every == "hour":
        return "Har ghante" if hi else "Every hour"
    if every == "day":
        return f"Roz {at} baje" if hi else f"Every day at {at}"
    if every == "week":
        wd = sched.get("weekday", 0)
        return f"Har {WEEKDAYS_HI[wd]} {at} baje" if hi else f"Every {WEEKDAYS_EN[wd]} at {at}"
    return f"Har mahine {sched.get('day', 1)} tarikh {at} baje" if hi else f"Every month on day {sched.get('day', 1)} at {at}"


def next_run(sched: dict, after: datetime) -> datetime:
    """Next run time (naive UTC) strictly after `after` (naive UTC). Times are India time."""
    local = after.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
    hh, mm = (int(x) for x in sched.get("at", "09:00").split(":"))
    every = sched.get("every")
    if every == "hour":
        nxt = local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        nxt = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if every == "day":
            if nxt <= local:
                nxt += timedelta(days=1)
        elif every == "week":
            nxt += timedelta(days=(sched.get("weekday", 0) - nxt.weekday()) % 7)
            if nxt <= local:
                nxt += timedelta(days=7)
        else:  # month
            day = sched.get("day", 1)
            nxt = nxt.replace(day=day)
            if nxt <= local:
                month = nxt.month % 12 + 1
                nxt = nxt.replace(year=nxt.year + (nxt.month == 12), month=month)
    return nxt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def describe(spec: dict, hi: bool) -> list[dict]:
    """The plan as plain-language blocks for the card."""
    days = spec.get("days", 3)
    who = (RULES_HI if hi else RULES)[spec["rule"]].format(days=days)
    sched = schedule_text(spec["schedule"], hi)
    steps = [{"kind": "when", "text": sched},
             {"kind": "read", "text": "Aapki sheet padhunga (Google Sheet link ya CSV)" if hi else "Read your sheet (Google Sheet link or CSV)"}]
    if spec["kind"] == "reminder":
        steps += [
            {"kind": "think", "text": f"Unko dhoondhunga {who}" if hi else f"Find the people {who}"},
            {"kind": "do", "text": "Har ek ko unke naam ke saath message email karunga" if hi else "Email each one a personal message with their details"},
            {"kind": "check", "text": "Aapko summary bhejunga: kisko gaya, kya fail hua" if hi else "Send you a summary: who got it, anything that failed"},
        ]
    else:
        steps += [
            {"kind": "think", "text": f"Report banaunga: {who}" if hi else f"Build a report of the rows {who}"},
            {"kind": "do", "text": "Report aapko email karunga" if hi else "Email the report to you"},
        ]
    return steps


def clean(spec: dict) -> dict:
    """A defensive copy with defaults filled in."""
    out = deepcopy(spec)
    out.setdefault("days", 3)
    out.setdefault("subject", "")
    out.setdefault("message_en", "")
    out.setdefault("message_hi", "")
    out.setdefault("send_to", "row_email" if out.get("kind") == "reminder" else "owner")
    return out
