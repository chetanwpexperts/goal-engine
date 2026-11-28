"""Chat and goal use cases."""
import logging
import hmac
import re
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import select
from app import accounts, privacy
from app.core.config import get_settings
from app.services.goals import InvalidMessage, all_goals, handle_message
from app.ai.router import AIUnavailable
from app.limits import LimitReached, usage_today
from app.agents.core import agent_dict, goal_agents
from app.domain.models import Message
from app.domain.schemas.chat import MessageIn, StatusIn
from app.security.visitor import COOKIE
from app.services.agents import _goal_hi
from app.services.goals import own_goal
from app.services.output import public
from app.services.visitor_history import PREV_COOKIE, _earlier_ids


def _messages(db: Session, visitor: str, goal_id: int | None = None, limit: int = 100) -> list[dict]:
    query = select(Message).where(Message.visitor_id == visitor)
    if goal_id is not None:
        query = query.where(Message.goal_id == goal_id)
    rows = list(db.scalars(query.order_by(Message.id.desc()).limit(limit)))
    return [{"role": m.role, "text": m.text, "relation": m.relation, "goal_id": m.goal_id}
            for m in reversed(rows)]


def state(db: Session, visitor: str) -> dict:
    goals = all_goals(db, visitor)
    settings = get_settings()
    used = usage_today(db, visitor)
    return public({
        "goals": [g.summary() for g in goals],
        "current": goals[0].as_dict() if goals else None,
        "agents": [agent_dict(a, db, _goal_hi(goals[0])) for a in goal_agents(db, goals[0].id)] if goals else [],
        "messages": _messages(db, visitor, limit=40),
        # the chat shows the latest messages across topics: earlier topics' worked examples, so their answers split too
        "cards": [c for g in reversed(goals[1:5]) for c in g.as_dict()["cards"]],
        "remaining_today": max(0, settings.visitor_daily_cap - used["visitor"]),
    })


def post_message(body: MessageIn, request: Request, db: Session, visitor: str):
    try:
        result = handle_message(db, visitor, body.text, request.app.state.router, get_settings(),
                                brain=request.app.state.brain, goal_id=body.goal_id, client_ip=accounts.client_ip(request))
    except InvalidMessage as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except LimitReached as exc:
        return JSONResponse({"error": str(exc)}, status_code=429)
    except AIUnavailable:
        logging.getLogger(__name__).exception("AI unavailable")
        return JSONResponse({"error": "I couldn't think about that right now. Please try again in a minute."},
                            status_code=503)
    used = usage_today(db, visitor)
    result["remaining_today"] = max(0, get_settings().visitor_daily_cap - used["visitor"])
    return public(result)


def new_chat(request: Request) -> JSONResponse:
    """Start fresh: the browser gets a new anonymous id, and remembers the old ones so 'Delete all my data' still covers them."""
    response = JSONResponse({"ok": True})
    ids = ",".join(([request.state.visitor_id] + _earlier_ids(request))[:30])
    response.set_cookie(PREV_COOKIE, f"{ids}.{privacy.fingerprint('prev:' + ids)[2:]}", max_age=60 * 60 * 24 * 180,
                        httponly=True, samesite="lax", secure=get_settings().cookie_secure)
    response.delete_cookie(COOKIE)
    return response


def get_goal(goal_id: int, db: Session, visitor: str) -> dict:
    goal = own_goal(db, goal_id, visitor)
    return public({"goal": goal.as_dict(), "messages": _messages(db, visitor, goal_id=goal.id),
                   "agents": [agent_dict(a, db, _goal_hi(goal)) for a in goal_agents(db, goal.id)]})


def set_status(goal_id: int, body: StatusIn, db: Session,
               visitor: str) -> dict:
    goal = own_goal(db, goal_id, visitor)
    goal.status = body.status
    db.commit()
    return public({"goal": goal.as_dict(), "goals": [g.summary() for g in all_goals(db, visitor)]})
