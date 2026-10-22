import logging
import asyncio
import hmac
import os
import time
import threading
from contextlib import asynccontextmanager
import re
import secrets
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.router import AIRouter, AIUnavailable
from app.ml.brain import Brain
from app.agents import automation
from app import expert, privacy
from app.agents import database as dbagent
from app.agents import direct as dbagent_direct
from app.agents import phpconn
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
    from app.agents import office_agent

    try:
        office_agent.resume_interrupted(utcnow())   # file work cut off by a restart carries on by itself
    except Exception:  # noqa: BLE001
        logging.getLogger("goal-engine").warning("Could not resume file work", exc_info=True)
    stop = threading.Event()
    if os.getenv("AGENT_SCHEDULER", "on") != "off":
        threading.Thread(target=_scheduler, args=(stop,), daemon=True, name="agent-scheduler").start()
    yield
    stop.set()


app = FastAPI(title="Goal Engine", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _run_db_report(db: Session, agent: Agent) -> None:
    from app.agents.spec import next_run
    from app.models import Connector

    inputs = loads(agent.inputs, {}) or {}
    connector = db.get(Connector, inputs.get("connector_id") or 0)
    agent.next_run_at = next_run(inputs.get("schedule") or {"every": "day", "at": "09:00"}, utcnow())
    agent.last_run_at = utcnow()
    db.commit()
    if connector is not None and connector.status != "disconnected":
        dbagent.queue(db, connector, "export", {"sql": inputs.get("sql", "")}, agent_id=agent.id)


_last_maintenance = 0.0
_last_file_check = 0.0


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
                        elif agent.recipe == "db_report":
                            _run_db_report(db, agent)
                        else:
                            run_agent(db, agent, hi, scheduled=True)
                    except AgentError as exc:
                        log.warning("Scheduled agent %s: %s", agent.id, exc)
                global _last_maintenance
                global _last_file_check
                if time.monotonic() - _last_file_check > 6 * 3600:
                    _last_file_check = time.monotonic()
                    log.info("Website files changed state: %s", phpconn.check_all(db))
                if time.monotonic() - _last_maintenance > 600:
                    _last_maintenance = time.monotonic()
                    log.info("Privacy clean-up: %s", privacy.maintain(db))
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - the scheduler must keep going
            log.exception("Scheduler tick failed")


# ---------------------------------------------------------------- coming soon (closed until SITE_MODE=open)
PREVIEW_COOKIE = "ge_preview"
OPEN_PATHS = ("/api/health", "/api/connector/", "/connector/", "/robots.txt", "/preview", "/static/coming-soon.html",
              "/admin", "/api/admin/", "/api/learning/feed")      # admin and the feed have their own keys


def _site_open() -> bool:
    return os.getenv("SITE_MODE", "closed").strip().lower() == "open"


def _preview_token() -> str:
    key = os.getenv("PREVIEW_KEY", "")
    return hmac.new(key.encode(), b"goal-engine-preview", "sha256").hexdigest() if len(key) >= 16 else ""


def _has_preview(request: Request) -> bool:
    token = _preview_token()
    return bool(token) and hmac.compare_digest(request.cookies.get(PREVIEW_COOKIE, ""), token)


@app.middleware("http")
async def coming_soon(request: Request, call_next):
    """Until launch only people with the private preview link get in. Connectors keep working (they use their own token)."""
    path = request.url.path
    if _site_open() or _has_preview(request) or path.startswith(OPEN_PATHS):
        response = await call_next(request)
    elif path.startswith("/api/"):
        response = JSONResponse({"error": "Goal Engine is coming soon"}, status_code=503)
    else:
        response = FileResponse(STATIC / "coming-soon.html", status_code=200)
    if not _site_open():
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@app.get("/robots.txt")
def robots() -> Response:
    return Response("User-agent: *\nDisallow: /\n" if not _site_open() else "User-agent: *\nAllow: /\n", media_type="text/plain")


@app.get("/preview")
def preview(key: str = "") -> Response:
    """Private entry while the site is closed: /preview?key=<PREVIEW_KEY>. /preview?off=1 leaves preview."""
    from fastapi.responses import RedirectResponse

    response = RedirectResponse("/", status_code=303)
    token = _preview_token()
    if token and key and hmac.compare_digest(hmac.new(key.encode(), b"goal-engine-preview", "sha256").hexdigest(), token):
        response.set_cookie(PREVIEW_COOKIE, token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax",
                            secure=get_settings().cookie_secure)
    else:
        response.delete_cookie(PREVIEW_COOKIE)
    return response


# ---------------------------------------------------------------- expert hand-off
ADMIN_COOKIE = "ge_admin"


def _admin_token() -> str:
    key = os.getenv("ADMIN_KEY", "")
    return hmac.new(key.encode(), b"goal-engine-admin", "sha256").hexdigest() if len(key) >= 16 else ""


def _is_admin(request: Request) -> bool:
    token = _admin_token()
    return bool(token) and hmac.compare_digest(request.cookies.get(ADMIN_COOKIE, ""), token)


class ExpertIn(BaseModel):
    goal_id: int | None = None
    need: str = ""
    name: str = ""
    phone: str = ""
    email: str = ""
    contact: str = "whatsapp"
    consent: bool = False
    website: str = ""          # honeypot


class ExpertUpdate(BaseModel):
    status: str | None = None
    note: str | None = None


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


@app.post("/api/expert")
def ask_expert(body: ExpertIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        req = expert.create(db, visitor, body.model_dump())
    except expert.ExpertError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    expert.notify(req, mailer(), _public_base(request))
    return {"ok": True, "request": expert.as_dict(req), "requests": expert.mine(db, visitor)}


@app.get("/api/expert")
def my_expert_requests(db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return {"requests": expert.mine(db, visitor)}


@app.get("/admin")
def admin_page(request: Request, key: str = "") -> Response:
    from fastapi.responses import RedirectResponse

    token = _admin_token()
    if key:
        response = RedirectResponse("/admin", status_code=303)
        if token and hmac.compare_digest(hmac.new(key.encode(), b"goal-engine-admin", "sha256").hexdigest(), token):
            response.set_cookie(ADMIN_COOKIE, token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="strict",
                                secure=get_settings().cookie_secure)
        return response
    if not _is_admin(request):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return FileResponse(STATIC / "admin.html", headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"})


@app.get("/api/admin/requests")
def admin_requests(request: Request, status: str = "", db: Session = Depends(get_db)):
    if not _is_admin(request):
        raise HTTPException(404, "Not Found")
    return {"requests": expert.all_requests(db, status)}


@app.post("/api/admin/requests/{req_id}")
def admin_update(req_id: int, body: ExpertUpdate, request: Request, db: Session = Depends(get_db)):
    if not _is_admin(request):
        raise HTTPException(404, "Not Found")
    try:
        return expert.as_dict(expert.update(db, req_id, body.status, body.note), admin=True)
    except expert.ExpertError:
        raise HTTPException(404, "Not Found")


# ---------------------------------------------------------------- Microsoft agent: files (app/agents/office_agent.py)
class OfficeStartIn(BaseModel):
    name: str
    size: int
    agent_id: int | None = None


class OfficeFixIn(BaseModel):
    ops: list[str]


class OfficeCompareIn(BaseModel):
    other: str
    key: str = ""


def _office_call(fn):
    from app.agents.office_agent import OfficeAgentError

    try:
        return fn()
    except OfficeAgentError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/office/files")
def office_start(body: OfficeStartIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    from app.agents import office_agent as oa

    if body.agent_id is not None:
        agent = db.get(Agent, body.agent_id)
        if not agent or agent.visitor_id != visitor or agent.recipe != "office":
            raise HTTPException(404, "Not found")
    return _office_call(lambda: oa.file_dict(oa.start_upload(db, visitor, body.agent_id, body.name, body.size)))


@app.put("/api/office/files/{token}/chunk")
async def office_chunk(token: str, offset: int, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    from app.agents import office_agent as oa

    data = await request.body()
    return _office_call(lambda: oa.file_dict(oa.add_chunk(db, oa.own_file(db, token, visitor), offset, data)))


@app.post("/api/office/files/{token}/done")
def office_done(token: str, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    from app.agents import office_agent as oa

    return _office_call(lambda: oa.file_dict(oa.finish_upload(db, oa.own_file(db, token, visitor))))


@app.get("/api/office/files/{token}")
def office_get(token: str, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    from app.agents import office_agent as oa

    return _office_call(lambda: oa.file_dict(oa.own_file(db, token, visitor)))


@app.post("/api/office/files/{token}/fix")
def office_fix(token: str, body: OfficeFixIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    from app.agents import office_agent as oa

    return _office_call(lambda: oa.file_dict(oa.start_fix(db, oa.own_file(db, token, visitor), body.ops)))


@app.post("/api/office/files/{token}/compare")
def office_compare(token: str, body: OfficeCompareIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    from app.agents import office_agent as oa

    return _office_call(lambda: oa.file_dict(oa.start_compare(db, oa.own_file(db, token, visitor), oa.own_file(db, body.other, visitor), body.key)))


@app.get("/api/office/files/{token}/download")
def office_download(token: str, what: str = "file", db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    """The latest version of the file (every fix so far), or the comparison report."""
    from app.agents import office_agent as oa

    f = _office_call(lambda: oa.own_file(db, token, visitor))
    if what == "compare":
        path, name = f.result_path, f.result_name or "comparison.xlsx"
    else:
        path, name = (f.path if len(oa.steps(f)) > 1 else None), oa._named(f.name, "fixed")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Not ready")
    return FileResponse(path, filename=name, headers={"Cache-Control": "no-store"})


@app.post("/api/office/files/{token}/undo")
def office_undo(token: str, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    from app.agents import office_agent as oa

    return _office_call(lambda: oa.file_dict(oa.start_undo(db, oa.own_file(db, token, visitor))))


@app.delete("/api/office/files/{token}")
def office_delete(token: str, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    from app.agents import office_agent as oa

    f = _office_call(lambda: oa.own_file(db, token, visitor))
    oa.remove_file(f)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- brains learning from users (app/learning.py)
class LearnVoteIn(BaseModel):
    brain: str
    text: str = Field(max_length=2000)
    label: str = Field(max_length=80)


class LearnAskIn(BaseModel):
    brain: str
    text: str = Field(max_length=2000)
    guess: str = ""
    confidence: float = 0.0


class LearnStatusIn(BaseModel):
    status: str


@app.post("/api/learn/choice")
def learn_choice(body: LearnVoteIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    from app import learning

    try:
        return learning.vote(db, body.brain, body.text, body.label, visitor)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/learn/ask")
def learn_ask(body: LearnAskIn, db: Session = Depends(get_db)):
    from app import learning

    try:
        learning.ask_to_learn(db, body.brain, body.text, body.guess, body.confidence)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.get("/api/admin/learning")
def admin_learning(request: Request, status: str = "new", db: Session = Depends(get_db)):
    from app import learning

    if not _is_admin(request):
        raise HTTPException(404, "Not Found")
    return learning.overview(db, status)


@app.post("/api/admin/learning/{kind}/{item_id}")
def admin_learning_set(kind: str, item_id: int, body: LearnStatusIn, request: Request, db: Session = Depends(get_db)):
    from app import learning

    if not _is_admin(request) or not learning.set_status(db, kind, item_id, body.status):
        raise HTTPException(404, "Not Found")
    return {"ok": True}


@app.get("/api/learning/feed")
def learning_feed(key: str = "", db: Session = Depends(get_db)):
    """For the nightly learning only (LEARN_KEY): scrubbed sentences people asked us to learn."""
    from app import learning

    want = os.getenv("LEARN_KEY", "")
    if len(want) < 16 or not hmac.compare_digest(key, want):
        raise HTTPException(404, "Not Found")
    return {"requests": learning.feed(db)}


class MessageIn(BaseModel):
    text: str
    goal_id: int | None = None   # the goal the person is looking at


class StatusIn(BaseModel):
    status: Literal["active", "done", "dropped"]


@app.post("/api/privacy/delete-all")
def delete_all(request: Request, response: Response, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    counts = {}
    for vid in [visitor] + _earlier_ids(request):
        for k, v in privacy.delete_visitor(db, vid).items():
            counts[k] = counts.get(k, 0) + (v or 0)
    response.delete_cookie(COOKIE)
    response.delete_cookie(PREV_COOKIE)
    return {"ok": True, "deleted": counts}


@app.get("/privacy")
def privacy_page() -> FileResponse:
    return FileResponse(STATIC / "privacy.html")


@app.get("/connector/install.sh")
def connector_installer(request: Request) -> Response:
    text = (Path(__file__).resolve().parents[1] / "connector" / "install.sh").read_text()
    return Response(text.replace("__SERVER__", _public_base(request)), media_type="text/x-shellscript")


@app.get("/connector/goal_connector.py")
def connector_script() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[1] / "connector" / "goal_connector.py", media_type="text/x-python")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def live_status():
    """Home screen: one quiet line that the agents are up."""
    return {"online": True}


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
    if "sheet_url" in body.inputs:
        inputs["sheet_url"] = (body.inputs["sheet_url"] or "").strip()[:2000]
    if "csv_text" in body.inputs:
        privacy.set_rows(inputs, (body.inputs["csv_text"] or "").strip()[:200000])   # stored encrypted
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
        automation.request_code(db, visitor, body.email, body.business, mailer(), hi=getattr(body, "lang", "en") != "en")
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


# ---------------------------------------------------------------- database connector (runs on the user's server)
def _connector_from(request: Request, db: Session):
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    connector = dbagent.authenticate(db, token)
    if connector is None:
        raise HTTPException(401, "Unknown or disconnected connector")
    return connector


@app.post("/api/connector/hello")
async def connector_hello(request: Request, db: Session = Depends(get_db)):
    connector = _connector_from(request, db)
    info = await request.json()
    dbagent.hello(db, connector, info if isinstance(info, dict) else {})
    dbagent.queue(db, connector, "schema")   # fresh table list every time it starts
    if connector.can_write:
        logging.getLogger(__name__).info("Connector %s reports a database user that can write", connector.id)
    return {"ok": True, "poll_seconds": 4, "long_poll": 20}


@app.post("/api/connector/poll")
async def connector_poll(request: Request, db: Session = Depends(get_db)):
    """Long poll: the connector's request waits here (up to ?wait= seconds, max 25) until a job arrives,
    so a job starts the moment it's queued instead of on the next 4-second check."""
    connector = await run_in_threadpool(_connector_from, request, db)
    try:
        wait = max(0.0, min(25.0, float(request.query_params.get("wait", "0"))))
    except ValueError:
        wait = 0.0
    deadline = time.monotonic() + wait
    while True:
        jobs = await run_in_threadpool(dbagent.claim, db, connector)
        if jobs or time.monotonic() >= deadline or await request.is_disconnected():
            break
        await asyncio.sleep(0.4)
    return {"jobs": [{"id": j.id, "kind": j.kind, "payload": loads(j.payload, {})} for j in jobs]}


@app.post("/api/connector/upload/{job_id}")
async def connector_upload(job_id: int, request: Request, db: Session = Depends(get_db)):
    """The connector streams an export here (gzip). Written to disk piece by piece, never held in memory."""
    from app.models import ConnectorJob

    connector = await run_in_threadpool(_connector_from, request, db)
    job = await run_in_threadpool(db.get, ConnectorJob, job_id)
    if job is None or job.connector_id != connector.id or job.kind != "export" or job.status != "claimed":
        return JSONResponse({"error": "Unknown job"}, status_code=400)
    path = dbagent.save_upload_path(job)
    size = 0
    try:
        with open(path, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > dbagent.MAX_UPLOAD_BYTES:
                    raise ValueError("too big")
                fh.write(chunk)
    except ValueError:
        path.unlink(missing_ok=True)
        return JSONResponse({"error": "The export is too big. Add a WHERE condition or fewer columns"}, status_code=413)
    job.file_path = str(path)
    await run_in_threadpool(db.commit)
    return {"ok": True}


@app.post("/api/connector/result/{job_id}")
async def connector_result(job_id: int, request: Request, db: Session = Depends(get_db)):
    connector = _connector_from(request, db)
    data = await request.json()
    try:
        job = dbagent.complete(db, connector, job_id, data if isinstance(data, dict) else {})
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _after_db_job(db, job)
    return {"ok": True}


@dbagent_direct.on_done
def _after_db_job(db: Session, job) -> None:
    """A scheduled database report finished: email the CSV to the verified owner."""
    if not job.agent_id or job.status != "done" or not job.file_path:
        return
    agent = db.get(Agent, job.agent_id)
    if agent is None or agent.recipe != "db_report":
        return
    owner = automation.owner_of(db, agent.visitor_id)
    if owner is None or not owner.verified_at:
        return
    inputs = loads(agent.inputs, {}) or {}
    title = inputs.get("title") or "Database report"
    try:
        with open(job.file_path, "rb") as fh:
            data = fh.read()
        if job.file_path.endswith(".gz"):
            import gzip

            data = gzip.decompress(data)
        rows = (loads(job.result, {}) or {}).get("row_count", "")
        mailer().send(owner.email, f"{title} · {utcnow():%d %b}", f"{title}\n\nRows: {rows}\nThe CSV file is attached (opens in Excel).",
                      from_name="Goal Engine", attachments=[(f"{title[:40]}.csv", data, "text/csv")])
    except Exception:  # noqa: BLE001 - the next run will try again
        logging.getLogger(__name__).exception("Database report email failed")


class ConnectorIn(BaseModel):
    name: str = ""
    agent_id: int | None = None


class DirectIn(BaseModel):
    type: str = "mysql"
    host: str = ""
    port: str = ""
    database: str = ""
    user: str = ""
    password: str = ""
    name: str = ""
    agent_id: int | None = None
    allow_changes: bool = False


class DbJobIn(BaseModel):
    kind: str
    sql: str = ""
    table: str = ""


class DbReportIn(BaseModel):
    sql: str = ""
    table: str = ""
    every: str = "day"
    at: str = "09:00"
    weekday: int = 0
    title: str = ""


def _public_base(request: Request) -> str:
    if os.getenv("PUBLIC_URL"):
        return os.getenv("PUBLIC_URL", "").rstrip("/")
    base = str(request.base_url).rstrip("/")
    if request.headers.get("x-forwarded-proto") == "https" and base.startswith("http://"):
        base = "https://" + base[7:]
    return base


@app.post("/api/connectors")
def create_connector(body: ConnectorIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        connector, token = dbagent.create_connector(db, visitor, body.name)
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if body.agent_id:
        agent = own_agent(db, body.agent_id, visitor)
        inputs = loads(agent.inputs, {}) or {}
        inputs["connector_id"] = connector.id
        agent.inputs = dumps(inputs)
        db.commit()
    base = _public_base(request)
    command = f"curl -fsSL {base}/connector/install.sh | bash -s -- {token}"
    return {"connector": dbagent.connector_dict(connector, db), "token": token, "command": command}


@app.post("/api/connectors/direct")
def connect_direct(body: DirectIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        connector = dbagent_direct.connect(db, visitor, body.model_dump(), body.name, allow_changes=body.allow_changes)
    except (dbagent_direct.DirectError, dbagent.DatabaseError) as exc:
        return JSONResponse({"error": str(exc), "use_command": bool(getattr(exc, "use_command", False))}, status_code=400)
    if body.agent_id:
        agent = own_agent(db, body.agent_id, visitor)
        inputs = loads(agent.inputs, {}) or {}
        inputs["connector_id"] = connector.id
        agent.inputs = dumps(inputs)
        db.commit()
        return _agent_payload(db, agent, visitor)
    return {"connector": dbagent.connector_dict(connector, db)}


class PhpLinkIn(BaseModel):
    url: str = ""


@app.post("/api/agents/{agent_id}/php-connector")
def create_php_connector(agent_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    agent = own_agent(db, agent_id, visitor)
    try:
        connector = phpconn.create(db, visitor)
    except (dbagent.DatabaseError, dbagent_direct.DirectError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    inputs = loads(agent.inputs, {}) or {}
    inputs["connector_id"] = connector.id
    agent.inputs = dumps(inputs)
    db.commit()
    return _agent_payload(db, agent, visitor)


@app.get("/api/connectors/{connector_id}/php-file")
def download_php_file(connector_id: int, changes: int = 0, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        connector = dbagent.own_connector(db, connector_id, visitor)
    except dbagent.DatabaseError:
        raise HTTPException(404, "Not found")
    if connector.kind != "php" or not connector.secret:
        raise HTTPException(404, "Not found")
    phpconn.set_allow_changes(db, connector, bool(changes))
    return Response(phpconn.file_text(connector), media_type="application/x-httpd-php",
                    headers={"Content-Disposition": f'attachment; filename="{phpconn.file_name(connector)}"',
                             "Cache-Control": "no-store"})


@app.post("/api/connectors/{connector_id}/php-link")
def link_php_file(connector_id: int, body: PhpLinkIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        connector = dbagent.own_connector(db, connector_id, visitor)
        if connector.kind != "php":
            raise dbagent.DatabaseError("Not a website connection")
        dbagent_direct._rate_limit(visitor)
        phpconn.link(db, connector, body.url)
    except (dbagent.DatabaseError, dbagent_direct.DirectError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"connector": dbagent.connector_dict(connector, db)}


class DiagnoseIn(BaseModel):
    issue_id: str


class OptimizeIn(BaseModel):
    sql: str


class RecognizeIn(BaseModel):
    text: str
    lang: str = "en"


@app.post("/api/dbbrain/recognize")
def brain_recognize(body: RecognizeIn):
    from app.dbknow import brain

    rec = brain.recognize(body.text)
    hi = body.lang != "en"
    alts = [{**a, "text": brain.describe(a, hi)} for a in rec.get("alternatives", [])]
    return {**rec, "alternatives": [a for a in alts if a["text"]], "text": brain.describe(rec, hi)}


@app.get("/api/dbbrain/playbook/{issue_id}")
def brain_playbook(issue_id: str):
    from app.dbknow import service

    issue = service.playbook(issue_id) or service.error_entry(issue_id)
    if issue is None:
        raise HTTPException(404, "Not found")
    return issue


@app.post("/api/connectors/{connector_id}/diagnose")
def brain_diagnose(connector_id: int, body: DiagnoseIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    """Run a playbook's own read-only checks on this database."""
    from app.dbknow import service

    try:
        job, issue, checks = service.start_checks(db, _own_conn(db, connector_id, visitor), body.issue_id)
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"job": dbagent.job_dict(job), "playbook": issue, "checks": checks}


@app.post("/api/connectors/{connector_id}/optimize")
def brain_optimize(connector_id: int, body: OptimizeIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    """The solving loop: try the fixes on the real database and prove which one is the same answer and faster."""
    from app.dbknow import service

    try:
        job, analysis = service.start_optimize(db, _own_conn(db, connector_id, visitor), body.sql)
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"job": dbagent.job_dict(job), "analysis": analysis}


@app.get("/api/connector-info")
def connector_info():
    return {"server_ip": dbagent_direct.server_ip()}


@app.get("/api/connectors")
def list_connectors(db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return {"connectors": [dbagent.connector_dict(c, db) for c in dbagent.visitor_connectors(db, visitor)]}


@app.post("/api/connectors/{connector_id}/disconnect")
def disconnect_connector(connector_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        connector = dbagent.own_connector(db, connector_id, visitor)
    except dbagent.DatabaseError:
        raise HTTPException(404, "Connector not found")
    connector.status = "disconnected"
    connector.secret = None          # forget the saved password
    db.commit()
    return {"ok": True}


@app.post("/api/agents/{agent_id}/connect/{connector_id}")
def link_connector(agent_id: int, connector_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    agent = own_agent(db, agent_id, visitor)
    try:
        connector = dbagent.own_connector(db, connector_id, visitor)
    except dbagent.DatabaseError:
        raise HTTPException(404, "Connector not found")
    inputs = loads(agent.inputs, {}) or {}
    inputs["connector_id"] = connector.id
    agent.inputs = dumps(inputs)
    db.commit()
    return _agent_payload(db, agent, visitor)


@app.post("/api/connectors/{connector_id}/jobs")
def create_db_job(connector_id: int, body: DbJobIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        connector = dbagent.own_connector(db, connector_id, visitor)
        sql, summary = body.sql, None
        if body.table and body.kind == "query":
            sql, summary = dbagent.table_preview_sql(body.table), f"Preview · {body.table}"
        elif body.table and body.kind == "export":
            sql, summary = dbagent.table_export_sql(body.table), f"Excel · {body.table}"
        job = dbagent.queue(db, connector, body.kind, {"sql": sql} if sql else {}, summary=summary)
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return dbagent.job_dict(job)


class PlanIn(BaseModel):
    sql: str


class ApplyIn(BaseModel):
    plan_id: int


class UndoIn(BaseModel):
    change_id: int
    force: bool = False


def _own_conn(db: Session, connector_id: int, visitor: str):
    try:
        return dbagent.own_connector(db, connector_id, visitor)
    except dbagent.DatabaseError:
        raise HTTPException(404, "Not found")


@app.post("/api/connectors/{connector_id}/plan")
def plan_change(connector_id: int, body: PlanIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    """Step 1: show exactly which rows a change would touch. Nothing is changed."""
    try:
        return dbagent.job_dict(dbagent.queue(db, _own_conn(db, connector_id, visitor), "plan", {"sql": body.sql}))
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/connectors/{connector_id}/apply")
def apply_change(connector_id: int, body: ApplyIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    """Step 2: run exactly what was previewed (once, within 10 minutes), saving a copy for Undo first."""
    try:
        return dbagent.job_dict(dbagent.approve(db, _own_conn(db, connector_id, visitor), body.plan_id))
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/connectors/{connector_id}/undo")
def undo_change(connector_id: int, body: UndoIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        return dbagent.job_dict(dbagent.request_undo(db, _own_conn(db, connector_id, visitor), body.change_id, body.force))
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


def _own_job(db: Session, job_id: int, visitor: str):
    from app.models import Connector, ConnectorJob

    job = db.get(ConnectorJob, job_id)
    connector = db.get(Connector, job.connector_id) if job else None
    if job is None or connector is None or connector.visitor_id != visitor:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/connector-jobs/{job_id}")
def get_db_job(job_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return dbagent.job_dict(_own_job(db, job_id, visitor))


@app.get("/api/connector-jobs/{job_id}/download")
def download_db_job(job_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    job = _own_job(db, job_id, visitor)
    if not job.file_path or not os.path.exists(job.file_path):
        raise HTTPException(404, "No file for this job")
    if job.file_path.endswith(".gz"):
        # sent compressed; the browser unpacks it, so the user simply gets a .csv
        return FileResponse(job.file_path, media_type="text/csv", filename=f"export_{job.id}.csv",
                            headers={"Content-Encoding": "gzip", "Cache-Control": "no-store"})
    return FileResponse(job.file_path, media_type="text/csv", filename=f"export_{job.id}.csv")


@app.get("/api/connectors/{connector_id}/activity")
def connector_activity(connector_id: int, after: int = 0, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        connector = dbagent.own_connector(db, connector_id, visitor)
    except dbagent.DatabaseError:
        raise HTTPException(404, "Not found")
    return {"items": dbagent.activity(db, connector, after), "online": dbagent.is_online(connector), "status": connector.status}


@app.post("/api/agents/{agent_id}/db-report")
def schedule_db_report(agent_id: int, body: DbReportIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    agent = own_agent(db, agent_id, visitor)
    inputs = loads(agent.inputs, {}) or {}
    try:
        connector = dbagent.own_connector(db, inputs.get("connector_id") or 0, visitor)
        sql = dbagent.table_export_sql(body.table) if body.table else dbagent.read_only_sql(body.sql)
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    owner = automation.owner_of(db, visitor)
    if owner is None or not owner.verified_at:
        return JSONResponse({"error": "Verify your email first, so the report has somewhere to go"}, status_code=400)
    schedule = {"every": body.every if body.every in ("day", "week", "month") else "day", "at": body.at, "weekday": body.weekday, "day": 1}
    from app.agents.spec import next_run, schedule_text

    goal = db.get(Goal, agent.goal_id)
    hi = _goal_hi(goal)
    title = (body.title or (f"{body.table} report" if body.table else "Database report"))[:80]
    report = Agent(visitor_id=visitor, goal_id=agent.goal_id, recipe="db_report", status="active",
                   inputs=dumps({"connector_id": connector.id, "sql": sql, "schedule": schedule, "title": title}),
                   next_run_at=next_run(schedule, utcnow()))
    db.add(report)
    db.commit()
    reply = (f"{title}: {schedule_text(schedule, hi)} apne aap aapko email aayegi (CSV, Excel mein khulegi)." if hi
             else f"{title}: emailed to you {schedule_text(schedule, hi).lower()}, as a CSV that opens in Excel.")
    db.add(Message(visitor_id=visitor, goal_id=agent.goal_id, role="assistant", text=reply, relation="agent_run", provider="agent"))
    db.commit()
    return _agent_payload(db, agent, visitor, reply)


@app.post("/api/agents/{agent_id}/pause")
def pause_agent(agent_id: int, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    agent = own_agent(db, agent_id, visitor)
    agent.status, agent.next_run_at = "paused", None
    db.commit()
    goal = db.get(Goal, agent.goal_id)
    return {"agents": [agent_dict(a, db, _goal_hi(goal)) for a in goal_agents(db, agent.goal_id)]}


PREV_COOKIE = "ge_prev"


def _earlier_ids(request: Request) -> list[str]:
    """Earlier anonymous ids of this browser (from 'start fresh'), signed so nobody can add someone else's."""
    raw = request.cookies.get(PREV_COOKIE, "")
    ids, _, sig = raw.rpartition(".")
    if not ids or not hmac.compare_digest(sig, privacy.fingerprint("prev:" + ids)[2:]):
        return []
    return [i for i in ids.split(",") if VISITOR_RE.match(i)]


@app.post("/api/new-chat")
def new_chat(request: Request) -> JSONResponse:
    """Start fresh: the browser gets a new anonymous id, and remembers the old ones so 'Delete all my data' still covers them."""
    response = JSONResponse({"ok": True})
    ids = ",".join(([request.state.visitor_id] + _earlier_ids(request))[:30])
    response.set_cookie(PREV_COOKIE, f"{ids}.{privacy.fingerprint('prev:' + ids)[2:]}", max_age=60 * 60 * 24 * 180,
                        httponly=True, samesite="lax", secure=get_settings().cookie_secure)
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
