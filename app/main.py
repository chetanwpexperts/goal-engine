import logging
import os
import threading
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
from app.agents import automation
from app.agents.core import AgentError, agent_dict, claim_due, goal_agents, run_agent
from app.agents.mailer import SmtpMailer
from app.engine import _after_run
from app.models import Agent, dumps, loads, utcnow
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
    stop = threading.Event()
    if os.getenv("AGENT_SCHEDULER", "on") != "off":
        threading.Thread(target=_scheduler, args=(stop,), daemon=True, name="agent-scheduler").start()
    yield
    stop.set()


app = FastAPI(title="Goal Engine", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _scheduler(stop: threading.Event) -> None:
    """Runs daily agents when they're due. Safe with several workers (each run is claimed once)."""
    log = logging.getLogger("agent-scheduler")
    from app.db import get_db as _get_db

    while not stop.wait(60):
        try:
            db = next(_get_db())
            try:
                for agent in claim_due(db, utcnow()):
                    goal = db.get(Goal, agent.goal_id)
                    hi = bool(goal and goal.detected_language in ("Hinglish", "Hindi"))
                    try:
                        if agent.recipe == "automation":
                            automation.run(db, agent, hi, preview=False, scheduled=True)
                        else:
                            run_agent(db, agent, hi, scheduled=True)
                    except AgentError as exc:
                        log.warning("Scheduled agent %s: %s", agent.id, exc)
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - the scheduler must keep going
            log.exception("Scheduler tick failed")


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
    from app.ml.slots import SPECS

    return {"ok": True, "engine": get_settings().engine_mode, "problem_types": len(SPECS) - 1,
            "ai_providers": [p.name for p in router.providers]}


@app.get("/api/state")
def state(db: Session = Depends(get_db), visitor: str = Depends(visitor_id)) -> dict:
    goals = all_goals(db, visitor)
    settings = get_settings()
    used = usage_today(db, visitor)
    return {
        "goals": [g.summary() for g in goals],
        "current": goals[0].as_dict() if goals else None,
        "agents": [agent_dict(a, db, _goal_hi(goals[0])) for a in goal_agents(db, goals[0].id)] if goals else [],
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


class StartIn(BaseModel):
    inputs: dict[str, str] = {}


def own_agent(db: Session, agent_id: int, visitor: str) -> Agent:
    agent = db.get(Agent, agent_id)
    if not agent or agent.visitor_id != visitor:
        raise HTTPException(404, "Agent not found")
    return agent


def _goal_hi(goal: Goal | None) -> bool:
    return bool(goal is None or goal.detected_language in ("Hinglish", "Hindi"))


@app.post("/api/agents/{agent_id}/start")
def start_agent(agent_id: int, body: StartIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    agent = own_agent(db, agent_id, visitor)
    goal = db.get(Goal, agent.goal_id)
    hi = _goal_hi(goal)
    inputs = loads(agent.inputs, {}) or {}
    for key, value in body.inputs.items():
        if key in ("url", "query", "explain") and isinstance(value, str) and value.strip():
            inputs[key] = value.strip()[:20000]
    agent.inputs = dumps(inputs)
    db.commit()
    try:
        run = run_agent(db, agent, hi)
    except AgentError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    reply, options = _after_run(agent, run, hi)
    db.add(Message(visitor_id=visitor, goal_id=agent.goal_id, role="assistant", text=reply, relation="agent_run", provider="agent"))
    db.commit()
    return {"reply": reply, "options": options, "relation": "agent_run",
            "goal": goal.as_dict() if goal else None,
            "goals": [g.summary() for g in all_goals(db, visitor)],
            "agents": [agent_dict(a, db, hi) for a in goal_agents(db, agent.goal_id)]}


class OwnerIn(BaseModel):
    email: str = ""
    business: str = ""
    code: str = ""


def _agent_payload(db: Session, agent: Agent, visitor: str, reply: str = "") -> dict:
    goal = db.get(Goal, agent.goal_id)
    hi = _goal_hi(goal)
    return {"reply": reply, "relation": "agent_run", "goal": goal.as_dict() if goal else None,
            "goals": [g.summary() for g in all_goals(db, visitor)],
            "agents": [agent_dict(a, db, hi) for a in goal_agents(db, agent.goal_id)]}


def mailer() -> SmtpMailer:
    return SmtpMailer()


@app.post("/api/agents/{agent_id}/preview")
def preview_agent(agent_id: int, body: StartIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    agent = own_agent(db, agent_id, visitor)
    if agent.recipe != "automation":
        raise HTTPException(400, "Preview is for automation agents")
    inputs = loads(agent.inputs, {}) or {}
    for key in ("sheet_url", "csv_text"):
        if key in body.inputs:
            inputs[key] = (body.inputs[key] or "").strip()[:200000]
    agent.inputs = dumps(inputs)
    db.commit()
    hi = _goal_hi(db.get(Goal, agent.goal_id))
    run = automation.run(db, agent, hi, preview=True)
    reply = run.summary or ""
    db.add(Message(visitor_id=visitor, goal_id=agent.goal_id, role="assistant", text=reply, relation="agent_run", provider="agent"))
    db.commit()
    return _agent_payload(db, agent, visitor, reply)


@app.post("/api/agents/{agent_id}/activate")
def activate_agent(agent_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    agent = own_agent(db, agent_id, visitor)
    if agent.recipe != "automation":
        raise HTTPException(400, "Only automation agents run on a schedule")
    try:
        automation.activate(db, agent)
    except automation.AutomationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    goal = db.get(Goal, agent.goal_id)
    hi = _goal_hi(goal)
    spec = automation.spec_of(agent)
    when = automation.schedule_text(spec["schedule"], hi)
    reply = (f"Agent chalu ho gaya. {when} apne aap chalega. Har run ke baad aapko summary email aayegi." if hi
             else f"Agent is on. It runs {when.lower()} by itself, and emails you a summary after each run.")
    db.add(Message(visitor_id=visitor, goal_id=agent.goal_id, role="assistant", text=reply, relation="agent_run", provider="agent"))
    db.commit()
    return _agent_payload(db, agent, visitor, reply)


@app.post("/api/owner/code")
def owner_code(body: OwnerIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        automation.request_code(db, visitor, body.email, body.business, mailer(), hi=True)
    except automation.AutomationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    return {"ok": True}


@app.post("/api/owner/verify")
def owner_verify(body: OwnerIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        owner = automation.verify_code(db, visitor, body.code)
    except automation.AutomationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True, "email": owner.email}


@app.get("/api/agents")
def my_agents(db: Session = Depends(get_db), visitor: str = Depends(visitor_id)) -> dict:
    """My Agents: every agent this visitor has, newest first."""
    rows = list(db.scalars(select(Agent).where(Agent.visitor_id == visitor).order_by(Agent.id.desc()).limit(50)))
    out = []
    for agent in rows:
        goal = db.get(Goal, agent.goal_id)
        item = agent_dict(agent, db, _goal_hi(goal))
        item["goal_id"], item["goal_title"] = agent.goal_id, goal.title if goal else ""
        out.append(item)
    return {"agents": out}


@app.post("/api/agents/{agent_id}/pause")
def pause_agent(agent_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    agent = own_agent(db, agent_id, visitor)
    agent.status, agent.next_run_at = "paused", None
    db.commit()
    goal = db.get(Goal, agent.goal_id)
    return {"agents": [agent_dict(a, db, _goal_hi(goal)) for a in goal_agents(db, agent.goal_id)]}


@app.post("/api/new-chat")
def new_chat() -> JSONResponse:
    """Start fresh: the browser gets a new anonymous id. Old messages stay stored for learning."""
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response


@app.get("/api/goals/{goal_id}")
def get_goal(goal_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)) -> dict:
    goal = own_goal(db, goal_id, visitor)
    return {"goal": goal.as_dict(), "messages": _messages(db, visitor, goal_id=goal.id),
            "agents": [agent_dict(a, db, _goal_hi(goal)) for a in goal_agents(db, goal.id)]}


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
