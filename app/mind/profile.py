"""The person's working style, built from the first message and refined by every next one.

First-time visitor: starting guesses from what they typed. Returning visitor: starts from how they worked last time
(VisitorStyle). Only the style is kept, never what's in their files or messages."""

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import MindSession, VisitorStyle, dumps, loads, utcnow

STYLE_KEYS = ("level", "control", "formula", "lang")
STYLE_TTL = timedelta(days=180)
SESSION_TTL = timedelta(days=30)
HISTORY = 12

# what the questions so far say the person is trying to finish
GOAL_OF = {
    "review": "check", "dups": "clean", "fix": "clean", "describe": "explore", "rows": "check", "upload": "explore",
    "sum_g": "report", "count_g": "report", "month": "report", "pivot": "report", "top": "analyse", "rank": "analyse",
    "lookup": "combine", "combine": "combine", "reconcile": "reconcile", "compare": "reconcile",
    "db_health": "fix", "db_slow": "fix", "db_error": "fix", "db_explore": "explore", "db_export": "report", "db_report": "report",
    "web_check": "check", "web_problem": "fix", "web_watch": "monitor",
}


def _default() -> dict:
    return {"level": "unknown", "control": "normal", "formula": None, "lang": "en", "history": [], "frustrated": 0,
            "urgent": False, "goal": None, "turns": 0}


def load(db: Session, goal_id: int | None, visitor_id: str) -> dict:
    if goal_id is not None:
        row = db.get(MindSession, goal_id)
        if row is not None:
            return {**_default(), **(loads(row.data, {}) or {})}
    p = _default()
    style = db.get(VisitorStyle, visitor_id)
    if style is not None:                        # returning visitor: start from how they worked before
        old = loads(style.data, {}) or {}
        p.update({k: old[k] for k in STYLE_KEYS if k in old})
        p["returning"] = True
    return p


def update(p: dict, signals: dict, lang: str | None = None) -> dict:
    p["turns"] = p.get("turns", 0) + 1
    if signals.get("expert"):
        p["level"] = "expert"
    elif signals.get("beginner") and p.get("level") != "expert":
        p["level"] = "beginner"
    if signals.get("ask_first"):
        p["control"] = "ask_first"
    elif signals.get("just_do"):
        p["control"] = "just_do"
    if signals.get("no_formula"):
        p["formula"] = False
    elif signals.get("always_formula"):
        p["formula"] = True                    # only a standing ask; one "give me the formula" is for that answer
    p["urgent"] = bool(signals.get("urgent"))
    if signals.get("frustrated") or signals.get("repeat"):
        p["frustrated"] = p.get("frustrated", 0) + 1
    if lang:
        p["lang"] = lang
    return p


def add_pattern(p: dict, label: str | None) -> None:
    if not label:
        return
    hist = p.setdefault("history", [])
    hist.append(label)
    del hist[:-HISTORY]
    goal = GOAL_OF.get(label)
    if goal:
        p["goal"] = goal


def save(db: Session, goal_id: int | None, visitor_id: str, p: dict) -> None:
    now = utcnow()
    if goal_id is not None:
        row = db.get(MindSession, goal_id)
        if row is None:
            row = MindSession(goal_id=goal_id, visitor_id=visitor_id)
            db.add(row)
        row.data, row.updated_at = dumps(p), now
    style = db.get(VisitorStyle, visitor_id)
    if style is None:
        style = VisitorStyle(visitor_id=visitor_id)
        db.add(style)
    old = loads(style.data, {}) or {}
    for k in STYLE_KEYS:
        v = p.get(k)
        if v not in (None, "unknown", "normal") or k == "lang":
            old[k] = v
    style.data, style.updated_at = dumps(old), now


def delete_visitor(db: Session, visitor_id: str) -> int:
    n = db.execute(delete(MindSession).where(MindSession.visitor_id == visitor_id)).rowcount or 0
    n += db.execute(delete(VisitorStyle).where(VisitorStyle.visitor_id == visitor_id)).rowcount or 0
    return n


def maintain(db: Session, now=None) -> int:
    now = now or utcnow()
    n = db.execute(delete(MindSession).where(MindSession.updated_at < now - SESSION_TTL)).rowcount or 0
    n += db.execute(delete(VisitorStyle).where(VisitorStyle.updated_at < now - STYLE_TTL)).rowcount or 0
    return n


def style_of(db: Session, visitor_id: str) -> dict:
    row = db.scalars(select(VisitorStyle).where(VisitorStyle.visitor_id == visitor_id)).first()
    return loads(row.data, {}) if row else {}
