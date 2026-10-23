"""One mind for every agent. It knows people and question patterns, not any technology: each agent's field knowledge
comes from its pack (app/knowledge/agents/*.json, see packs.py).

before(): read the person in this message (level, urgency, annoyance, control, formulas or not), on top of what the
conversation and their earlier visits already showed. Agents read it while they work (now()).
after():  note the question's pattern, learn the step (anonymous for everyone; fingerprinted for the person's own
habits), and work out the likely next questions.
"""

import contextvars
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mind import packs, predict, profile, signals
from app.models import Message, utcnow

_current: contextvars.ContextVar[dict | None] = contextvars.ContextVar("mind", default=None)
PAUSE = timedelta(minutes=30)       # a longer gap in the same conversation starts a fresh chain of questions
BREAK = "|"


def now() -> dict:
    """The mind of the turn being handled ({} outside a turn)."""
    return _current.get() or {}


def set_pattern(label: str, plan: dict | None = None, brain: str | None = None) -> None:
    """An agent says what kind of question this was (and the plan it made)."""
    cur = _current.get()
    if cur is not None:
        cur["label"], cur["plan"] = label, plan
        if brain:
            cur["brain"] = brain


def asked_back() -> None:
    """An agent asked the person something (which one? which meaning?): the answer comes first, no suggestions."""
    cur = _current.get()
    if cur is not None:
        cur["asked_back"] = True


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
    last_at = p.get("last_at")
    if last_at and p.get("history") and p["history"][-1] != BREAK:
        try:
            if utcnow() - datetime.fromisoformat(last_at) > PAUSE:
                p["history"].append(BREAK)            # back after a long pause: don't link this to what came before
        except ValueError:
            pass
    cur = {"signals": sig, "profile": p, "goal_id": goal_id, "label": None, "plan": None, "brain": None,
           "last_reply": _previous_reply(db, visitor_id, goal_id), "visitor_id": visitor_id}
    _current.set(cur)
    return cur


def _chain(history: list[str], brain: str) -> list[str]:
    """This agent's patterns since the last pause."""
    out = []
    for h in history:
        if h == BREAK:
            out = []
        elif h.startswith(brain + ":"):
            out.append(h.split(":", 1)[1])
    return out


def _brain_of(agents) -> tuple[str | None, object | None]:
    found = []
    for a in agents:
        name = packs.brain_for_recipe(a.recipe)
        if name:
            found.append((packs.get(name).get("priority", 99), a.id or 0, name, a))
    if not found:
        return None, None
    _, _, name, agent = min(found, key=lambda x: (x[0], -x[1]))
    return name, agent


def after(db: Session, visitor_id: str, goal, text: str, reply: str, agents: list, hi: bool) -> dict:
    """Returns {'next': [...], 'reply': reply (maybe adjusted)}."""
    cur = _current.get() or before(db, visitor_id, goal.id if goal else None, text)
    p = cur["profile"]
    sig = cur["signals"]
    p["lang"] = "hi" if hi else "en"
    out = {"next": [], "reply": reply}
    brain, agent = _brain_of(agents)
    pack = packs.get(brain) if brain else {}
    label = cur.get("label")
    if brain and not label and not pack.get("hooks", {}).get("suggest"):
        label = packs.label_from_words(brain, text)    # agents that don't name their own patterns: from the pack's phrasings
    chain = _chain(p.get("history") or [], brain) if brain else []
    if brain and label and label != "other":
        if not chain and pack.get("first") and label != pack["first"]:
            profile.add_pattern(p, f"{brain}:{pack['first']}")     # where this agent's work starts (e.g. the file arrived)
            chain = [pack["first"]]
        try:
            predict.record(db, brain, chain, label, visitor=visitor_id)
        except Exception:  # noqa: BLE001 - learning never breaks a reply
            pass
        profile.add_pattern(p, f"{brain}:{label}", pack.get("goals", {}).get(label))
        chain.append(label)

    # the same answer to the same question again: say so instead of repeating it silently
    if (sig.get("repeat") or sig.get("frustrated")) and cur.get("last_reply") and reply.strip() == cur["last_reply"].strip():
        out["reply"] = ("Ye wahi jawab hai jo pehle diya tha, shayad main aapki baat galat samjha. Thoda alag shabdon mein batao, ya neeche se chuno.\n\n"
                        if hi else "That's the same answer as before, so I may have misread you. Say it another way, or pick below.\n\n") + reply

    # likely next questions: not when they're in a hurry or annoyed (then: just do exactly what was asked)
    if brain and not cur.get("asked_back") and not sig.get("urgent") and not sig.get("frustrated") and not sig.get("repeat"):
        try:
            out["next"] = _next(db, brain, agent, chain, cur, hi, text, visitor_id)
        except Exception:  # noqa: BLE001
            out["next"] = []
    p["last_at"] = utcnow().isoformat()
    if goal is not None and goal.id is not None:
        profile.save(db, goal.id, visitor_id, p)
    db.commit()
    return out


def _next(db: Session, brain: str, agent, chain: list[str], cur: dict, hi: bool, text: str, visitor_id: str) -> list[str]:
    pack = packs.get(brain)
    if not chain:
        ready = packs.hook(brain, "ready")
        if ready is not None and not ready(db, agent):
            return []
        chain = [pack.get("first", "other")]
    scores = predict.scores(db, brain, chain, visitor=visitor_id)
    suggest = packs.hook(brain, "suggest")
    if suggest is not None:
        return suggest(db, agent, scores, cur, hi, text)      # the agent writes them with the person's own names
    state_fn = packs.hook(brain, "state")
    state = state_fn(db, agent) if (state_fn is not None and agent is not None) else {}
    steps = pack.get("next", {})
    for flag, labels in pack.get("when", {}).items():
        if state.get(flag):
            # the agent is at a known point (e.g. it just finished a piece of work): those are the next steps
            just = {lab for lab, sc in packs.nearest(brain, text, k=1, min_score=0.45)}     # what they just asked
            return [steps[x]["hi" if hi else "en"] for x in labels if x in steps and x not in just][:3]
    for need, only in pack.get("until", {}).items():
        if not state.get(need):
            # nothing else can happen until this is done: that is the one next step
            return [] if chain[-1] == only or only not in steps else [steps[only]["hi" if hi else "en"]]
    out = []
    values = {k: v for k, v in state.items() if isinstance(v, str)}
    for label, pr in sorted(scores.items(), key=lambda x: -x[1]):
        step = steps.get(label)
        if not step or pr < 0.1:
            continue
        if not all(state.get(n) for n in step.get("needs", [])):
            continue
        try:
            s = step["hi" if hi else "en"].format(**values)
        except KeyError:
            continue
        if s not in out:
            out.append(s)
        if len(out) >= 3:
            break
    return out
