"""Working style and conversation history rules."""
from datetime import timedelta

STYLE_KEYS = ("level", "control", "formula", "lang")
STYLE_TTL = timedelta(days=180)
SESSION_TTL = timedelta(days=30)
HISTORY = 12


def _default() -> dict:
    return {"level": "unknown", "control": "normal", "formula": None, "lang": "en", "history": [], "frustrated": 0,
            "urgent": False, "goal": None, "turns": 0}


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


def add_pattern(p: dict, label: str | None, goal: str | None = None) -> None:
    """goal: what this kind of question says the person is trying to finish (from the agent's pack)."""
    if not label:
        return
    hist = p.setdefault("history", [])
    hist.append(label)
    del hist[:-HISTORY]
    if goal:
        p["goal"] = goal


def restore(session, style):
    if session is not None:
        return {**_default(), **session}
    p = _default()
    if style is not None:
        p.update({k: style[k] for k in STYLE_KEYS if k in style})
        p["returning"] = True
    return p


def merge_style(old, p):
    for k in STYLE_KEYS:
        v = p.get(k)
        if v not in (None, "unknown", "normal") or k == "lang":
            old[k] = v
    return old
