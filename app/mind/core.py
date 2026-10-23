"""One mind for all three agents.

before(): read the person in this message (level, urgency, frustration, control, formulas or not), on top of what the
conversation and their earlier visits already showed. The agents read it while they work (now()).
after():  note the question's pattern, learn the step (anonymous), and work out the likely next questions.
"""

import contextvars
import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mind import predict, profile, signals
from app.models import Message, loads

_current: contextvars.ContextVar[dict | None] = contextvars.ContextVar("mind", default=None)
PACK = Path(__file__).resolve().parent / "pack"


def now() -> dict:
    """The mind of the turn being handled ({} outside a turn)."""
    return _current.get() or {}


def set_pattern(label: str, plan: dict | None = None, brain: str = "office") -> None:
    """An agent says what kind of question this was (and the plan it made)."""
    cur = _current.get()
    if cur is not None:
        cur["label"], cur["plan"], cur["brain"] = label, plan, brain


def _previous_texts(db: Session, visitor_id: str, goal_id: int | None, limit: int = 3) -> list[str]:
    if goal_id is None:
        return []
    rows = db.scalars(select(Message.text).where(Message.visitor_id == visitor_id, Message.goal_id == goal_id, Message.role == "user")
                      .order_by(Message.id.desc()).limit(limit))
    return list(reversed(list(rows)))


def _previous_reply(db: Session, visitor_id: str, goal_id: int | None) -> str:
    if goal_id is None:
        return ""
    row = db.scalars(select(Message.text).where(Message.visitor_id == visitor_id, Message.goal_id == goal_id, Message.role == "assistant")
                     .order_by(Message.id.desc()).limit(1)).first()
    return row or ""


def before(db: Session, visitor_id: str, goal_id: int | None, text: str) -> dict:
    prev = _previous_texts(db, visitor_id, goal_id)
    sig = signals.read(text, prev)
    p = profile.load(db, goal_id, visitor_id)
    profile.update(p, sig)
    cur = {"signals": sig, "profile": p, "goal_id": goal_id, "label": None, "plan": None, "brain": None,
           "last_reply": _previous_reply(db, visitor_id, goal_id)}
    _current.set(cur)
    return cur


# ---------------------------------------------------------------- database and website: patterns from the words
@lru_cache(maxsize=4)
def _pack(brain: str):
    from sklearn.feature_extraction.text import TfidfVectorizer

    data = json.loads((PACK / f"{brain}.json").read_text(encoding="utf-8"))
    labels, phrases = [], []
    for label, items in data["patterns"].items():
        labels += [label] * len(items)
        phrases += [p.lower() for p in items]
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
    return vec, vec.fit_transform(phrases), labels


def label_from_words(brain: str, text: str, min_score: float = 0.25) -> str:
    vec, mat, labels = _pack(brain)
    sims = (mat @ vec.transform([(text or "").lower()]).T).toarray().ravel()
    i = int(sims.argmax())
    return labels[i] if sims[i] >= min_score else "other"


DB_NEXT = {
    "db_connect": ("How do I connect my database?", "Database kaise jodun?"),
    "db_health": ("Check my database health", "Mere database ki health check karo"),
    "db_explore": ("Show me the tables in my database", "Database ki tables dikhao"),
    "db_export": ("Export a database table to Excel", "Database se data Excel mein export karo"),
    "db_report": ("Email me a daily database report", "Roz subah database report email karo"),
}


def _web_next(label: str, agent_inputs: dict, hi: bool) -> str | None:
    url = agent_inputs.get("website_url") or agent_inputs.get("url")
    if label == "web_watch":
        return "Haan, roz check karo" if hi else "Yes, check it daily"
    if label == "web_another":
        return "Koi aur website check karo" if hi else "Check another website"
    if label in ("web_check", "web_fix") and url:
        return f"{url} dobara check karo" if hi else f"Check {url} again"
    return None


# ---------------------------------------------------------------- after the agent answered
def _brain_of(agents) -> tuple[str | None, object | None]:
    for a in agents:
        if a.recipe == "office":
            return "office", a
    for a in agents:
        if a.recipe in ("database", "sql_review"):
            return "database", a
    for a in agents:
        if a.recipe in ("website_check", "website_watch"):
            return "website", a
    return None, None


def after(db: Session, visitor_id: str, goal, text: str, reply: str, agents: list, hi: bool) -> dict:
    """Returns {'next': [...], 'reply': reply (maybe adjusted)}."""
    cur = _current.get() or before(db, visitor_id, goal.id if goal else None, text)
    p = cur["profile"]
    sig = cur["signals"]
    p["lang"] = "hi" if hi else "en"
    out = {"next": [], "reply": reply}
    brain, agent = _brain_of(agents)
    label = cur.get("label")
    if brain and not label:
        if brain == "office":
            label = None                                # the Excel agent names its own patterns; small talk has none
        elif brain == "database":
            label = label_from_words("database", text)
        elif brain == "website":
            label = label_from_words("website", text)
    history = list(p.get("history") or [])
    if brain == "office" and label and not any(h.startswith("office:") for h in history):
        history.append("office:upload")                 # the file itself was the first step
        profile.add_pattern(p, "office:upload")
    if brain and label and label != "other":
        try:
            predict.record(db, brain, [h.split(":", 1)[1] for h in history if h.startswith(brain + ":")], label)
        except Exception:  # noqa: BLE001 - learning never breaks a reply
            pass
        profile.add_pattern(p, f"{brain}:{label}")

    # the same answer to the same question again: say so instead of repeating it silently
    if (sig.get("repeat") or sig.get("frustrated")) and cur.get("last_reply") and reply.strip() == cur["last_reply"].strip():
        out["reply"] = ("Ye wahi jawab hai jo pehle diya tha, shayad main aapki baat galat samjha. Thoda alag shabdon mein batao, ya neeche se chuno.\n\n"
                        if hi else "That's the same answer as before, so I may have misread you. Say it another way, or pick below.\n\n") + reply

    # likely next questions: not when they're in a hurry or annoyed (then: just do exactly what was asked)
    if brain and not sig.get("urgent") and not sig.get("frustrated") and not sig.get("repeat"):
        own = [h.split(":", 1)[1] for h in (p.get("history") or []) if h.startswith(brain + ":")]
        try:
            out["next"] = _next(db, brain, agent, own, cur, hi, text)
        except Exception:  # noqa: BLE001
            out["next"] = []
    if goal is not None and goal.id is not None:
        profile.save(db, goal.id, visitor_id, p)
    db.commit()
    return out


def _next(db: Session, brain: str, agent, own: list[str], cur: dict, hi: bool, text: str) -> list[str]:
    if not own:
        if brain == "office" and agent is not None:
            from app.agents import office_agent

            f = office_agent.open_file(db, agent)
            if f is None or f.status != "ready":
                return []
            own = ["upload"]
        elif brain == "database":
            own = ["db_start"]
        else:
            own = ["web_problem"]
    scores = predict.scores(db, brain, own)
    if brain == "office":
        if cur["profile"].get("level") == "expert" and cur.get("plan"):
            scores["formula"] = max(scores.get("formula", 0), 0.2)
        if cur["profile"].get("formula") is False:
            scores.pop("formula", None)
        return _office_next(db, agent, scores, cur, hi, text)
    if brain == "database":
        from app.agents import database as dbagent

        has = bool(dbagent.visitor_connectors(db, agent.visitor_id)) if agent is not None else False
        if not has:
            # nothing else can happen until the database is connected: that is the one next step
            return [] if own[-1] == "db_connect" else [DB_NEXT["db_connect"][1 if hi else 0]]
        out = []
        for label, pr in sorted(scores.items(), key=lambda x: -x[1]):
            if label in DB_NEXT and pr >= 0.1 and not (label == "db_connect" and has) and label != own[-1]:
                out.append(DB_NEXT[label][1 if hi else 0])
            if len(out) >= 3:
                break
        return out
    inputs = (loads(agent.inputs, {}) or {}) if agent is not None else {}
    from app.models import AgentRun

    run = db.scalars(select(AgentRun).where(AgentRun.agent_id == agent.id).order_by(AgentRun.id.desc()).limit(1)).first() \
        if agent is not None else None
    if run is None or run.status != "done":
        return []                                   # the next step is the address / the check itself
    out = []
    for label, pr in sorted(scores.items(), key=lambda x: -x[1]):
        s = _web_next(label, inputs, hi) if pr >= 0.1 else None
        if s and s not in out:
            out.append(s)
        if len(out) >= 3:
            break
    return out


SUGGEST_MAX_BYTES = 10 * 1024 * 1024       # bigger files: no reading the whole file again just to suggest


def _office_next(db: Session, agent, scores: dict, cur: dict, hi: bool, text: str) -> list[str]:
    from app.agents import office_agent
    from app.mind import office as mo
    from app.office.sheetdata import load_tables

    f = office_agent.open_file(db, agent)
    if f is None or f.status != "ready":
        return []
    report = loads(f.report, {}) or {}
    if f.size > SUGGEST_MAX_BYTES:
        return []
    tables = cur.get("tables") or load_tables(f.path)
    plan = cur.get("plan") or (loads(agent.inputs, {}) or {}).get("mind_plan")
    return mo.suggestions(scores, tables, plan, report, hi, asked=text)
