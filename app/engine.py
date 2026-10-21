"""Receive -> understand -> structure. Every message updates goal state, not just the chat."""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import build_user_prompt
from app.ai.router import AIRouter
from app.ai.schema import Interpretation
from app.config import Settings
from app.limits import release_ai_call, reserve_ai_call
from app.models import Goal, Message, dumps


MAX_CONTEXT_GOALS = 6
RECENT_MESSAGES = 6

# Requests that application data can answer, with no AI call (cheapest reliable path).
LIST_GOALS = re.compile(
    r"^\s*(show|list|see|view|dikhao|dikha|batao)?\s*(my|meri|mere|all)?\s*(active\s+)?"
    r"(goals?|tasks?|problems?)\s*(dikhao|dikha|batao|list|show)?\s*[?.!]*\s*$",
    re.I,
)


class InvalidMessage(ValueError):
    pass


def active_goals(db: Session, visitor_id: str, limit: int | None = None) -> list[Goal]:
    query = (select(Goal).where(Goal.visitor_id == visitor_id, Goal.status == "active")
             .order_by(Goal.updated_at.desc(), Goal.id.desc()))
    if limit:
        query = query.limit(limit)
    return list(db.scalars(query))


def all_goals(db: Session, visitor_id: str) -> list[Goal]:
    return list(db.scalars(select(Goal).where(Goal.visitor_id == visitor_id)
                           .order_by(Goal.updated_at.desc(), Goal.id.desc())))


def recent_messages(db: Session, visitor_id: str) -> list[dict[str, str]]:
    rows = list(db.scalars(select(Message).where(Message.visitor_id == visitor_id)
                           .order_by(Message.id.desc()).limit(RECENT_MESSAGES)))
    return [{"role": m.role, "text": m.text[:500]} for m in reversed(rows)]


def _apply(goal: Goal, data: Interpretation) -> None:
    goal.title = (data.title or goal.title or "Untitled goal")[:200]
    goal.detected_language = data.detected_language or goal.detected_language
    goal.interpreted_problem = data.interpreted_problem or goal.interpreted_problem
    goal.desired_goal = data.desired_goal or goal.desired_goal
    goal.known_facts = dumps(data.known_facts)
    goal.missing_info = dumps([m.model_dump() for m in data.missing_info])
    goal.constraints = dumps(data.constraints)
    goal.sub_goals = dumps(data.sub_goals)
    goal.confidence = data.confidence
    goal.next_action_type = data.next_action.type
    goal.next_action = data.next_action.description


def handle_message(db: Session, visitor_id: str, text: str, router: AIRouter, settings: Settings) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise InvalidMessage("Please write something first.")
    if len(text) > settings.max_message_chars:
        raise InvalidMessage(f"Please keep it under {settings.max_message_chars} characters.")

    if LIST_GOALS.match(text):
        goals = active_goals(db, visitor_id)
        reply = ("Here are your active goals." if goals else "You don't have any goals yet. Tell me what's wrong.")
        db.add(Message(visitor_id=visitor_id, role="user", text=text, relation="list"))
        db.add(Message(visitor_id=visitor_id, role="assistant", text=reply, relation="list", provider="app"))
        db.commit()
        return {"reply": reply, "relation": "list", "goal": None,
                "goals": [g.summary() for g in all_goals(db, visitor_id)], "provider": "app"}

    reserve_ai_call(db, visitor_id, settings)
    context = active_goals(db, visitor_id, MAX_CONTEXT_GOALS)
    prompt = build_user_prompt(text, [g.as_dict() for g in context], recent_messages(db, visitor_id))
    try:
        data, provider = router.interpret(prompt)
    except Exception:
        release_ai_call(db, visitor_id)
        raise

    relation = data.relation
    goal: Goal | None = None
    if relation in ("update_goal", "supporting_task"):
        goal = next((g for g in context if g.id == data.goal_ref), None)
        if goal is None:  # the AI pointed at a goal that isn't this visitor's: start a new one
            relation = "new_goal"
    if relation == "new_goal":
        goal = Goal(visitor_id=visitor_id, title=data.title or "Untitled goal", original_request=text)
        db.add(goal)
    if goal is not None:
        _apply(goal, data)
        goal.updates = (goal.updates or 0) + 1

    db.flush()
    goal_id = goal.id if goal else None
    db.add(Message(visitor_id=visitor_id, goal_id=goal_id, role="user", text=text, relation=relation))
    db.add(Message(visitor_id=visitor_id, goal_id=goal_id, role="assistant", text=data.reply,
                   relation=relation, provider=provider))
    db.commit()

    return {
        "reply": data.reply,
        "relation": relation,
        "goal": goal.as_dict() if goal else None,
        "goals": [g.summary() for g in all_goals(db, visitor_id)],
        "provider": provider,
    }
