"""Planner: one sentence -> an agent spec, built from blocks.

The rule planner understands the automations small businesses ask for most, in Hinglish,
Hindi and English, including when they should run. An optional AI planner (PLANNER_AI=on)
handles anything else; whatever it writes still has to pass the same validator.
"""

import json
import re

from app.agents.spec import clean, validate
from app.ml.normalize import normalize


NUM_WORDS = {"ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "saat": 7, "das": 10,
             "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7, "ten": 10, "pandrah": 15, "bees": 20}
WEEKDAY_WORDS = {
    0: ("monday", "somvar", "mon"), 1: ("tuesday", "mangalvar", "mangal", "tue"), 2: ("wednesday", "budhvar", "budh", "wed"),
    3: ("thursday", "guruvar", "veervar", "thu"), 4: ("friday", "shukravar", "shukra", "fri"),
    5: ("saturday", "shanivar", "shani", "sat"), 6: ("sunday", "ravivar", "itvar", "sun"),
}

AUTOMATE = (r"\b(automatic\w*|automate\w*|khud se|khud hi|apne aap|apne ap|har din|roz|rozana|daily|weekly|monthly|har hafte|"
            r"har mahine|har (monday|tuesday|wednesday|thursday|friday|saturday|sunday|somvar|mangalvar|budhvar|guruvar|"
            r"shukravar|shanivar|ravivar)|schedule\w*|every|reminders?|remind\w*|yaad dila\w*|yaad|bhej\w*|send|alerts?|notify)\b")
# Things that need a live connection we don't have as a block yet (database, server): not a sheet automation.
CONNECTOR_ONLY = r"\b(database|db|mysql|postgres\w*|backup|server|cron)\b"


def _num(text: str, unit: str) -> int | None:
    m = re.search(rf"\b(\d{{1,3}}|{'|'.join(NUM_WORDS)})\s*({unit})", text)
    if not m:
        return None
    raw = m.group(1)
    return int(raw) if raw.isdigit() else NUM_WORDS[raw]


def parse_schedule(text: str, default: dict) -> dict:
    sched = dict(default)
    if re.search(r"\b(har ghante|hourly|every hour)\b", text):
        return {"every": "hour", "at": "09:00"}
    for wd, words in WEEKDAY_WORDS.items():
        if re.search(rf"\b(har|every|each|harek)\s+({'|'.join(words)})\b", text) or re.search(rf"\b({'|'.join(words[:2])})\s+ko\b", text):
            sched = {"every": "week", "weekday": wd, "at": sched.get("at", "10:00")}
            break
    else:
        if re.search(r"\b(roz|daily|har din|every day|rozana|pratidin)\b", text):
            sched = {"every": "day", "at": sched.get("at", "09:00")}
        elif re.search(r"\b(har hafte|weekly|every week|hafte mein ek)\b", text):
            sched = {"every": "week", "weekday": 0, "at": sched.get("at", "10:00")}
        elif re.search(r"\b(har mahine|monthly|every month|mahine ki)\b", text):
            day = _num(text, r"tarikh|tareekh|date") or 1
            sched = {"every": "month", "day": max(1, min(day, 28)), "at": sched.get("at", "10:00")}
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|bje|o clock)\b", text)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "pm" and hh < 12:
            hh += 12
        if m.group(3) in ("baje", "bje") and re.search(r"\b(shaam|sham|raat|evening|dopahar)\b", text) and hh < 12:
            hh += 12
        if 0 <= hh <= 23:
            sched["at"] = f"{hh:02d}:{mm:02d}"
    elif re.search(r"\b(shaam|sham|evening)\b", text):
        sched["at"] = "18:00"
    elif re.search(r"\b(subah|morning)\b", text):
        sched["at"] = "09:00"
    return sched


TEMPLATES = {
    "payment": dict(
        title="Payment reminders", kind="reminder", rule="due_soon", days=3,
        fields=["name", "email", "amount", "due_date", "status"],
        schedule={"every": "week", "weekday": 0, "at": "10:00"},
        subject="Payment reminder",
        message_en="Hi {name},\n\nA friendly reminder that ₹{amount} is due on {due_date}. If you've already paid, please ignore this message.\n\nThank you!",
        message_hi="Namaste {name},\n\nYaad dilana tha ki ₹{amount} ka payment {due_date} ko due hai. Agar aap pay kar chuke hain to is message ko ignore karein.\n\nDhanyavaad!"),
    "fees": dict(
        title="Fee reminders", kind="reminder", rule="due_soon", days=3,
        fields=["name", "email", "amount", "due_date", "status"],
        schedule={"every": "month", "day": 1, "at": "10:00"},
        subject="Fee reminder",
        message_en="Dear {name},\n\nThis is a reminder that the fee of ₹{amount} is due on {due_date}. Please ignore this if already paid.\n\nThank you!",
        message_hi="Namaste {name},\n\nYaad dilana tha ki ₹{amount} ki fees {due_date} tak deni hai. Agar jama kar di hai to is message ko ignore karein.\n\nDhanyavaad!"),
    "appointment": dict(
        title="Appointment reminders", kind="reminder", rule="tomorrow", days=1,
        fields=["name", "email", "appointment"],
        schedule={"every": "day", "at": "18:00"},
        subject="Reminder: your appointment tomorrow",
        message_en="Hi {name},\n\nThis is a reminder of your appointment tomorrow ({appointment}). Reply to this email if you need to reschedule.\n\nSee you soon!",
        message_hi="Namaste {name},\n\nYaad dila dein ki kal ({appointment}) aapka appointment hai. Time badalna ho to is email ka reply karein.\n\nMilte hain!"),
    "inactive": dict(
        title="Win back inactive customers", kind="reminder", rule="inactive", days=30,
        fields=["name", "email", "last_visit"],
        schedule={"every": "day", "at": "11:00"},
        subject="We miss you",
        message_en="Hi {name},\n\nIt's been a while since your last visit. We'd love to see you again; reply to this email and we'll help you right away.\n\nThank you!",
        message_hi="Namaste {name},\n\nKaafi din ho gaye aapko aaye hue. Aapka phir se swagat hai; is email ka reply karein, hum turant madad karenge.\n\nDhanyavaad!"),
    "review": dict(
        title="Review requests", kind="reminder", rule="yesterday", days=1,
        fields=["name", "email", "visit_date"],
        schedule={"every": "day", "at": "11:00"},
        subject="How was your experience?",
        message_en="Hi {name},\n\nThank you for visiting us yesterday! Could you spare a minute to leave us a review? It really helps.\n\nThank you!",
        message_hi="Namaste {name},\n\nKal aane ke liye shukriya! Kya aap 1 minute nikaal ke hamein review de sakte hain? Isse bahut madad milti hai.\n\nDhanyavaad!"),
    "birthday": dict(
        title="Birthday wishes", kind="reminder", rule="birthday_today", days=0,
        fields=["name", "email", "birthday"],
        schedule={"every": "day", "at": "09:00"},
        subject="Happy birthday, {name}!",
        message_en="Hi {name},\n\nWishing you a very happy birthday! Have a wonderful year ahead.\n\nWarm wishes!",
        message_hi="Namaste {name},\n\nJanamdin ki dher saari shubhkamnayein! Aapka saal bahut accha rahe.\n\nShubhkamnayein!"),
    "report": dict(
        title="Daily report", kind="report", rule="all", days=0,
        fields=["name", "amount", "due_date", "appointment", "status"],
        schedule={"every": "day", "at": "09:00"},
        subject="Your report", message_en="", message_hi="", send_to="owner"),
    "dues_report": dict(
        title="Pending payments report", kind="report", rule="amount_due", days=0,
        fields=["name", "amount", "due_date", "status"],
        schedule={"every": "day", "at": "09:00"},
        subject="Pending payments", message_en="", message_hi="", send_to="owner"),
}

PATTERNS: list[tuple[str, str]] = [
    ("birthday", r"(birthday|janamdin|janmdin|janam din|\bdob\b)"),
    ("review", r"(review|rating|feedback)"),
    ("appointment", r"(appointment|booking|slot|session|visit\b.*\b(kal|tomorrow|pehle|before))"),
    ("inactive", r"(nahi aaye|nahi aaya|nahi aayi|order nahi|nahi kharid|inactive|wapas nahi|purane customer|old customers|haven t (come|ordered|visited)|not (come|ordered|visited))"),
    ("dues_report", r"(report|list|summary|hisaab).*(baaki|pending|due|udhaar|outstanding)|(baaki|pending|due|udhaar|outstanding).*(report|list|summary|hisaab)"),
    ("report", r"\b(report|summary|hisaab|total|collection)\b"),
    ("fees", r"\b(fees|fee)\b"),
    ("payment", r"(payment|paise|paisa|baaki|due\b|udhaar|udhar|balance|bill|emi|installment|kist|invoice|dues)"),
]


def plan_rules(text: str) -> dict | None:
    norm = normalize(text)
    if not re.search(AUTOMATE, norm) or re.search(CONNECTOR_ONLY, norm):
        return None
    for key, pattern in PATTERNS:
        if re.search(pattern, norm):
            spec = clean(dict(TEMPLATES[key]))
            spec["template"] = key
            spec["schedule"] = parse_schedule(norm, spec["schedule"])
            if key == "inactive":
                spec["days"] = _num(norm, r"din|days|day") or (30 if re.search(r"mahin|month", norm) else spec["days"])
                if re.search(r"\b(ek|1)\s*(mahine|month)", norm):
                    spec["days"] = 30
            elif key in ("payment", "fees"):
                before = _num(norm, r"din pehle|days before|day before")
                if before is not None:
                    spec["days"] = before
            return spec if not validate(spec) else None
    return None


AI_PROMPT = """You turn a business owner's request into an automation plan using ONLY these blocks.
Reply with ONE JSON object and nothing else:
{"title": short title,
 "kind": "reminder" (messages to people in the owner's sheet) or "report" (a summary emailed to the owner),
 "rule": one of all | amount_due | due_soon | overdue | tomorrow | today | inactive | birthday_today | yesterday,
 "days": integer 0-365 used by due_soon / inactive,
 "fields": columns needed from: name, email, phone, amount, due_date, appointment, last_visit, birthday, visit_date, status,
 "schedule": {"every": "hour"|"day"|"week"|"month", "at": "HH:MM", "weekday": 0-6 (week only, 0=Monday), "day": 1-28 (month only)},
 "subject": email subject,
 "message_en": message in English using {name} and other {field} placeholders,
 "message_hi": the same message in Hinglish,
 "send_to": "row_email" for reminders, "owner" for reports}
If the request can't be done with these blocks, reply {"unsupported": "<short reason>"}."""


def plan_ai(text: str, router) -> dict | None:
    if router is None or not getattr(router, "providers", None):
        return None
    for provider in router.providers:
        try:
            raw = provider.complete_json(AI_PROMPT, text)
            data = json.loads(raw.strip().strip("`").removeprefix("json").strip())
        except Exception:  # noqa: BLE001 - try the next provider
            continue
        if not isinstance(data, dict) or "unsupported" in data:
            return None
        spec = clean(data)
        spec["template"] = "ai"
        return spec if not validate(spec) else None
    return None


def plan(text: str, router=None, use_ai: bool = False) -> dict | None:
    return plan_rules(text) or (plan_ai(text, router) if use_ai else None)
