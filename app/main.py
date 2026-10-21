import logging
from contextlib import asynccontextmanager
import re
import secrets
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.router import AIRouter, AIUnavailable
from app.ml.brain import Brain
from app.config import get_settings
from app.db import get_db, init_db
from app.engine import InvalidMessage, all_goals, handle_message
from app.limits import LimitReached, usage_today
from app.models import Goal, Message


logging.basicConfig(level=logging.INFO)
STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "ge_vid"
VISITOR_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")

@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    if not hasattr(application.state, "router"):
        application.state.router = AIRouter.from_settings(get_settings())
    if not hasattr(application.state, "brain"):
        application.state.brain = Brain()
    application.state.brain.model.load()  # train or load the ML model before the first visitor
    yield


app = FastAPI(title="Goal Engine", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def visitor_cookie(request: Request, call_next):
    """Anonymous visitor id, so each person only ever sees their own goals."""
    visitor = request.cookies.get(COOKIE, "")
    fresh = not VISITOR_RE.match(visitor)
    if fresh:
        visitor = secrets.token_urlsafe(24)
    request.state.visitor_id = visitor
    response = await call_next(request)
    if fresh:
        response.set_cookie(COOKIE, visitor, max_age=60 * 60 * 24 * 180, httponly=True,
                            samesite="lax", secure=get_settings().cookie_secure)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def visitor_id(request: Request) -> str:
    return request.state.visitor_id


def own_goal(db: Session, goal_id: int, visitor: str) -> Goal:
    goal = db.get(Goal, goal_id)
    if not goal or goal.visitor_id != visitor:
        raise HTTPException(404, "Goal not found")
    return goal


class MessageIn(BaseModel):
    text: str
    goal_id: int | None = None   # the goal the person is looking at


class StatusIn(BaseModel):
    status: Literal["active", "done", "dropped"]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict:
    router: AIRouter = app.state.router
    return {"ok": True, "engine": get_settings().engine_mode, "ai_providers": [p.name for p in router.providers]}


@app.get("/api/state")
def state(db: Session = Depends(get_db), visitor: str = Depends(visitor_id)) -> dict:
    goals = all_goals(db, visitor)
    settings = get_settings()
    used = usage_today(db, visitor)
    return {
        "goals": [g.summary() for g in goals],
        "current": goals[0].as_dict() if goals else None,
        "messages": _messages(db, visitor, limit=40),
        "remaining_today": max(0, settings.visitor_daily_cap - used["visitor"]),
    }


@app.post("/api/message")
def post_message(body: MessageIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        result = handle_message(db, visitor, body.text, app.state.router, get_settings(),
                                brain=app.state.brain, goal_id=body.goal_id)
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
    return result


@app.post("/api/new-chat")
def new_chat() -> JSONResponse:
    """Start fresh: the browser gets a new anonymous id. Old messages stay stored for learning."""
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response


@app.get("/api/goals/{goal_id}")
def get_goal(goal_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)) -> dict:
    goal = own_goal(db, goal_id, visitor)
    return {"goal": goal.as_dict(), "messages": _messages(db, visitor, goal_id=goal.id)}


@app.post("/api/goals/{goal_id}/status")
def set_status(goal_id: int, body: StatusIn, db: Session = Depends(get_db),
               visitor: str = Depends(visitor_id)) -> dict:
    goal = own_goal(db, goal_id, visitor)
    goal.status = body.status
    db.commit()
    return {"goal": goal.as_dict(), "goals": [g.summary() for g in all_goals(db, visitor)]}


def _messages(db: Session, visitor: str, goal_id: int | None = None, limit: int = 100) -> list[dict]:
    query = select(Message).where(Message.visitor_id == visitor)
    if goal_id is not None:
        query = query.where(Message.goal_id == goal_id)
    rows = list(db.scalars(query.order_by(Message.id.desc()).limit(limit)))
    return [{"role": m.role, "text": m.text, "relation": m.relation, "goal_id": m.goal_id}
            for m in reversed(rows)]
