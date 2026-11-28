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

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from app.domain.schemas.main import (
    ExpertIn,
    ExpertUpdate,
    LearnVoteIn,
    LearnAskIn,
    LearnStatusIn,
    MessageIn,
    StatusIn,
    ConnectorIn,
    DirectIn,
    DbJobIn,
    DbReportIn,
    DiagnoseIn,
    OptimizeIn,
    RecognizeIn,
    PlanIn,
    ApplyIn,
    UndoIn,
    ExcelIn,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.router import AIRouter, AIUnavailable
from app.ml.brain import Brain
from app.agents import automation
from app import accounts, admin, console, dbarea, enquiry, expert, privacy
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
from app.security.visitor import COOKIE, visitor_id
from app.security.admin import ADMIN_COOKIE, _admin_token, _is_admin
from app.security.site import PREVIEW_COOKIE, _site_open, _preview_token, _has_preview
from app.services.output import GOAL_INTERNAL, public
from app.core.paths import STATIC
from app.security.headers import CONSOLE_CSP, ENQUIRY_CSP
from app.services.urls import _public_base
from app.services import health as health_service
from app.api.routes import pages as pages_routes, expert as expert_routes, health as health_routes, learning_feed as learning_feed_routes
from app.api.routes.pages import robots, preview, privacy_page, product_page, how_page, self_hosted_page, enquiry_page, index
from app.api.routes.expert import ask_expert, my_expert_requests, admin_requests, admin_update
from app.api.routes.learning_feed import learning_feed
from app.domain.schemas.office import OfficeStartIn, OfficeFixIn, OfficeCompareIn
from app.domain.schemas.agents import StartIn, OwnerIn
from app.api.routes import office as office_routes, agents as agents_routes
from app.api.routes.office import office_start, office_chunk, office_done, office_answer, office_get, office_fix, office_compare, office_download, office_undo, office_delete
from app.api.routes.agents import start_agent, preview_agent, activate_agent, owner_verify, my_agents, link_connector, pause_agent
from app.services.office import _office_call
from app.services.agents import own_agent, _goal_hi, _agent_payload
from app.services import agents as agent_service
from app.services.goals import own_goal


logging.basicConfig(level=logging.INFO)
VISITOR_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")

@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    if not hasattr(application.state, "router"):
        application.state.router = AIRouter.from_settings(get_settings())
    if not hasattr(application.state, "brain"):
        application.state.brain = Brain()
    application.state.brain.model.load()  # train or load the ML model before the first visitor
    try:                                  # and the problem recognisers, so the first visitor never waits for training
        from app.dbknow import brain as _dbb
        from app.officeknow import brain as _ofb

        _dbb._train()
        _ofb._train()
    except Exception:  # noqa: BLE001
        logging.getLogger("goal-engine").warning("Recognisers not warmed", exc_info=True)
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


app = FastAPI(title="Goal Engine", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
_MIN_JS: dict = {}


app.include_router(accounts.router)
app.include_router(dbarea.router)
app.include_router(console.router)
app.include_router(enquiry.router)
app.include_router(admin.router)


@app.get("/static/app.js", include_in_schema=False)
def app_js():
    """The page's script, minified: no comments or notes about how things work reach the browser."""
    from fastapi.responses import Response

    path = STATIC / "app.js"
    key = path.stat().st_mtime
    if _MIN_JS.get("key") != key:
        import rjsmin

        _MIN_JS.update(key=key, body=rjsmin.jsmin(path.read_text(encoding="utf-8"), keep_bang_comments=False))
    return Response(_MIN_JS["body"], media_type="application/javascript", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")

app.include_router(pages_routes.router)
app.include_router(expert_routes.router)
app.include_router(health_routes.router)
app.include_router(learning_feed_routes.router)
app.include_router(office_routes.router)
app.include_router(agents_routes.router)

# Resolve the legacy mailer hook at request time (existing callers may replace it).
app.state.mailer = lambda: mailer()


def health() -> dict:
    return health_service.health(app.state.router)



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
                if time.monotonic() - _last_maintenance > 600:
                    _last_maintenance = time.monotonic()
                    log.info("Privacy clean-up: %s", privacy.maintain(db))
                    log.info("Account clean-up: %s", accounts.maintain(db))
                    log.info("Database area: %s", dbarea.maintain(db))
                    log.info("SQL console connections ended: %s", console.maintain(db))
                    log.info("Old enquiries deleted: %s", enquiry.maintain(db))
                    from app.limits import prune_bursts

                    prune_bursts(db)
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - the scheduler must keep going
            log.exception("Scheduler tick failed")


# ---------------------------------------------------------------- coming soon (closed until SITE_MODE=open)
OPEN_PATHS = ("/api/health", "/api/connector/", "/connector/", "/robots.txt", "/preview", "/static/coming-soon.html",
              "/admin", "/api/admin/", "/static/admin.", "/api/learning/feed",   # admin and the feed have their own keys
              "/enquiry", "/api/enquiry", "/static/enquiry.")      # people interested can reach us before launch too


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
        if not path.startswith(("/static/", "/api/", "/connector/")):
            # the same address shows the coming-soon page or the app depending on the preview cookie: never cache it
            response.headers["Cache-Control"] = "no-store"
            response.headers["Vary"] = "Cookie"
    return response


# ---------------------------------------------------------------- expert hand-off
@app.middleware("http")
async def visitor_cookie(request: Request, call_next):
    """Anonymous visitor id, so each person only ever sees their own goals."""
    visitor = request.cookies.get(COOKIE, "")
    fresh = not VISITOR_RE.match(visitor)
    if fresh:
        visitor = secrets.token_urlsafe(24)
    request.state.visitor_id = visitor
    await run_in_threadpool(accounts.resolve, request)
    if request.state.user is not None:
        # logged in: the account's own chats, on every device (the guest cookie is left as it is)
        request.state.visitor_id = request.state.user.visitor_id
    blocked = await run_in_threadpool(dbarea.guard, request)     # the locked database area
    response = blocked if blocked is not None else await call_next(request)
    if fresh:
        response.set_cookie(COOKIE, visitor, max_age=60 * 60 * 24 * 180, httponly=True,
                            samesite="lax", secure=get_settings().cookie_secure)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


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
    return FileResponse(STATIC / "admin.html", headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store",
                                                        "Content-Security-Policy": ADMIN_CSP, "X-Frame-Options": "DENY",
                                                        "Referrer-Policy": "no-referrer"})


# ---------------------------------------------------------------- Microsoft agent: files (app/agents/office_agent.py)
# ---------------------------------------------------------------- brains learning from users (app/learning.py)
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


@app.post("/api/privacy/delete-all")
def delete_all(request: Request, response: Response, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    counts = {}
    for vid in [visitor] + _earlier_ids(request):
        for k, v in privacy.delete_visitor(db, vid).items():
            counts[k] = counts.get(k, 0) + (v or 0)
    user = accounts.current_user(request)
    if user is not None:
        counts["account"] = accounts.delete_account(db, user.id)
        response.delete_cookie(accounts.SESSION_COOKIE)
    response.delete_cookie(COOKIE)
    response.delete_cookie(PREV_COOKIE)
    return {"ok": True, "deleted": counts}


# the site's other pages: what the product does, how it works, installing it on your own server
@app.get("/connector/install.sh")
def connector_installer(request: Request) -> Response:
    text = (Path(__file__).resolve().parents[1] / "connector" / "install.sh").read_text()
    return Response(text.replace("__SERVER__", _public_base(request)), media_type="text/x-shellscript")


@app.get("/connector/goal_connector.py")
def connector_script() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[1] / "connector" / "goal_connector.py", media_type="text/x-python")




@app.get("/console", include_in_schema=False)
def console_page() -> FileResponse:
    """The SQL console: its own page, its own strict rules (no other site's code, no framing, nothing cached)."""
    return FileResponse(STATIC / "console.html", headers={"Content-Security-Policy": CONSOLE_CSP, "X-Frame-Options": "DENY",
                                                          "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
                                                          "X-Robots-Tag": "noindex, nofollow"})


ADMIN_CSP = CONSOLE_CSP


@app.get("/api/known")
def known() -> dict:
    """How many problems, errors, questions and fixes the agents know (grows every night)."""
    import glob
    import json as _json

    n = 0
    for f in glob.glob(str(Path(__file__).resolve().parent / "dbknow" / "*.json")) + \
            glob.glob(str(Path(__file__).resolve().parent / "officeknow" / "*.json")):
        try:
            data = _json.loads(Path(f).read_text(encoding="utf-8"))
            if isinstance(data, list):              # lists of problems/errors/formulas; dict files are settings, not fixes
                n += len(data)
        except (OSError, ValueError):
            continue
    return {"fixes": n}


@app.get("/api/status")
def live_status():
    """Home screen: one quiet line that the agents are up."""
    return {"online": True}


@app.get("/api/state")
def state(db: Session = Depends(get_db), visitor: str = Depends(visitor_id)) -> dict:
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


@app.post("/api/message")
def post_message(body: MessageIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    try:
        result = handle_message(db, visitor, body.text, app.state.router, get_settings(),
                                brain=app.state.brain, goal_id=body.goal_id, client_ip=accounts.client_ip(request))
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


def mailer() -> SmtpMailer:
    return SmtpMailer()


def owner_code(body: OwnerIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return agent_service.owner_code(body, db, visitor, mailer)


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
        files = [(f"{title[:40]}.csv", data, "text/csv")]
        text = f"{title}\n\nRows: {rows}\nThe CSV file is attached (opens in Excel)."
        try:                                        # S3: the same rows as a workbook with a live pivot and its chart
            from app.agents import sqlexcel
            from app.models import Connector

            info = sqlexcel.for_job(job, db.get(Connector, job.connector_id))
            with open(info["path"], "rb") as fh:
                files.append((f"{title[:40]}.xlsx", fh.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
            text = f"{title}\n\nRows: {rows}\n{info['say']}\nAttached: the Excel workbook and the plain CSV."
        except Exception:  # noqa: BLE001 - the CSV still goes
            logging.getLogger(__name__).exception("Report workbook failed")
        mailer().send(owner.email, f"{title} · {utcnow():%d %b}", text, from_name="Goal Engine", attachments=files)
    except Exception:  # noqa: BLE001 - the next run will try again
        logging.getLogger(__name__).exception("Database report email failed")


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
def connect_direct(body: DirectIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    # the address nginx saw (the right-most forwarded one); the left-most part can be faked by the client
    fwd = [x.strip() for x in (request.headers.get("x-forwarded-for") or "").split(",") if x.strip()]
    client_ip = fwd[-1] if fwd else (request.client.host if request.client else "")
    user = db.get(accounts.User, accounts.current_user(request).id) if accounts.current_user(request) else None
    form = body.model_dump()
    try:
        if user is not None and dbarea.enabled():
            dbarea.before_connect(db, user, (form.get("host") or "").strip().lower()[:200], client_ip)
        connector = dbagent_direct.connect(db, visitor, form, body.name, allow_changes=body.allow_changes,
                                           client_ip=client_ip)
    except (dbagent_direct.DirectError, dbagent.DatabaseError) as exc:
        if user is not None:
            dbarea.record(db, user, form, client_ip, False, str(exc)[:200])
        return JSONResponse({"error": str(exc), "use_command": bool(getattr(exc, "use_command", False))}, status_code=400)
    if user is not None:
        dbarea.record(db, user, form, client_ip, True)
        dbarea.tell_owner(user, connector.host_label or "", connector.db_name or "", client_ip)
    if body.agent_id:
        agent = own_agent(db, body.agent_id, visitor)
        inputs = loads(agent.inputs, {}) or {}
        inputs["connector_id"] = connector.id
        agent.inputs = dumps(inputs)
        db.commit()
        return _agent_payload(db, agent, visitor)
    return {"connector": dbagent.connector_dict(connector, db)}


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


@app.post("/api/connector-jobs/{job_id}/excel")
def db_job_excel(job_id: int, body: ExcelIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    """S3: the export as a workbook: Data, a Pivot with live COUNTIFS / SUMIFS and a chart, About. Nothing is sent anywhere."""
    from app.agents import sqlexcel
    from app.models import Connector

    job = _own_job(db, job_id, visitor)
    try:
        info = sqlexcel.for_job(job, db.get(Connector, job.connector_id), body.by or None, body.measure or None, body.hi, body.words)
    except sqlexcel.ExcelError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    p = info["pivot"]
    return {"say": info["say"], "notes": info["notes"], "chart": info["chart"], "rows": info["rows"], "sheets": info["sheets"],
            "columns": info["columns"],
            "pivot": {k: p[k] for k in ("by", "measure", "mode", "groups", "formulas")} if p else None,
            "download": f"/api/connector-jobs/{job.id}/excel?tag={info['tag']}"}


@app.get("/api/connector-jobs/{job_id}/excel")
def db_job_excel_file(job_id: int, tag: str = "", db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    job = _own_job(db, job_id, visitor)
    path = f"{job.file_path}.{tag}.xlsx" if job.file_path and re.fullmatch(r"[0-9a-f]{10}", tag or "") else ""
    if not path or not os.path.exists(path):
        raise HTTPException(404, "No workbook for this job")
    return FileResponse(path, filename=f"export_{job.id}.xlsx", headers={"Cache-Control": "no-store"},
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


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
    return public({"goal": goal.as_dict(), "messages": _messages(db, visitor, goal_id=goal.id),
                   "agents": [agent_dict(a, db, _goal_hi(goal)) for a in goal_agents(db, goal.id)]})


@app.post("/api/goals/{goal_id}/status")
def set_status(goal_id: int, body: StatusIn, db: Session = Depends(get_db),
               visitor: str = Depends(visitor_id)) -> dict:
    goal = own_goal(db, goal_id, visitor)
    goal.status = body.status
    db.commit()
    return public({"goal": goal.as_dict(), "goals": [g.summary() for g in all_goals(db, visitor)]})


def _messages(db: Session, visitor: str, goal_id: int | None = None, limit: int = 100) -> list[dict]:
    query = select(Message).where(Message.visitor_id == visitor)
    if goal_id is not None:
        query = query.where(Message.goal_id == goal_id)
    rows = list(db.scalars(query.order_by(Message.id.desc()).limit(limit)))
    return [{"role": m.role, "text": m.text, "relation": m.relation, "goal_id": m.goal_id}
            for m in reversed(rows)]
