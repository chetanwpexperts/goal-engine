import logging
import os
import threading
from contextlib import asynccontextmanager
import re
import secrets
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.router import AIRouter, AIUnavailable
from app.ml.brain import Brain
from app.agents import automation
from app.agents import database as dbagent
from app.agents import direct as dbagent_direct
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
                dbagent.clean_exports()
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
    return {"ok": True, "poll_seconds": 4}


@app.post("/api/connector/poll")
def connector_poll(request: Request, db: Session = Depends(get_db)):
    connector = _connector_from(request, db)
    jobs = dbagent.claim(db, connector)
    return {"jobs": [{"id": j.id, "kind": j.kind, "payload": loads(j.payload, {})} for j in jobs]}


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
        connector = dbagent_direct.connect(db, visitor, body.model_dump(), body.name)
    except (dbagent_direct.DirectError, dbagent.DatabaseError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if body.agent_id:
        agent = own_agent(db, body.agent_id, visitor)
        inputs = loads(agent.inputs, {}) or {}
        inputs["connector_id"] = connector.id
        agent.inputs = dumps(inputs)
        db.commit()
        return _agent_payload(db, agent, visitor)
    return {"connector": dbagent.connector_dict(connector, db)}


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
        sql = body.sql
        if body.table and body.kind == "query":
            sql = dbagent.table_preview_sql(body.table)
        elif body.table and body.kind == "export":
            sql = dbagent.table_export_sql(body.table)
        job = dbagent.queue(db, connector, body.kind, {"sql": sql} if sql else {})
    except dbagent.DatabaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return dbagent.job_dict(job)


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
    return FileResponse(job.file_path, media_type="text/csv", filename=f"export_{job.id}.csv")


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
