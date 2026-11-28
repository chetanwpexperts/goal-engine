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
from app.domain.schemas import (
    ExpertIn, ExpertUpdate, LearnVoteIn, LearnAskIn, LearnStatusIn, MessageIn, StatusIn, ConnectorIn, DirectIn, DbJobIn, DbReportIn, DiagnoseIn, OptimizeIn, RecognizeIn, PlanIn, ApplyIn, UndoIn, ExcelIn
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.router import AIRouter, AIUnavailable
from app.engine.ml.brain import Brain
from app.products.shared import automation
from app import accounts, admin, console, dbarea, enquiry, expert, privacy
from app.products.dbaba import database as dbagent
from app.products.shared import direct as dbagent_direct
from app.products.shared.core import AgentError, agent_dict, claim_due, goal_agents, run_agent
from app.products.shared.mailer import SmtpMailer
from app.products.shared.replies import _after_run
from app.domain.models import Agent, dumps, loads, utcnow
from app.core.config import get_settings
from app.core.db import get_db, init_db
from app.services.goals import InvalidMessage, all_goals, handle_message
from app.limits import LimitReached, usage_today
from app.domain.models import Goal, Message
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


from app.services.runtime import lifespan, _scheduler
from app.services.db_reports import _run_db_report, _after_db_job
from app.services import db_reports as report_service
from app.services.visitor_history import PREV_COOKIE, VISITOR_RE, _earlier_ids
from app.services.chat import _messages
from app.services.connector import _connector_from
from app.services.db_jobs import _own_conn, _own_job
from app.api.routes import assets, site_extra, chat, privacy as privacy_routes, learning as learning_routes, connector as connector_routes, db_jobs, db_reports
from app.api.routes.assets import app_js, _MIN_JS
from app.api.routes.site_extra import admin_page, connector_installer, connector_script, console_page, known, live_status, ADMIN_CSP
from app.api.routes.chat import state, post_message, new_chat, get_goal, set_status
from app.api.routes.privacy import delete_all
from app.api.routes.learning import learn_choice, learn_ask, admin_learning, admin_learning_set
from app.api.routes.connector import connector_hello, connector_poll, connector_upload, connector_result, create_connector, connect_direct, connector_info, list_connectors, disconnect_connector, connector_activity
from app.api.routes.db_jobs import brain_recognize, brain_playbook, brain_diagnose, brain_optimize, create_db_job, plan_change, apply_change, undo_change, get_db_job, download_db_job, db_job_excel, db_job_excel_file
from app.api.routes.db_reports import schedule_db_report


logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Goal Engine", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.state.permission_checks = {"api_key": connector_routes.service.api_key_allowed}
app.include_router(accounts.router)
app.include_router(dbarea.router)
app.include_router(console.router)
app.include_router(enquiry.router)
app.include_router(admin.router)
app.include_router(assets.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(pages_routes.router)
app.include_router(expert_routes.router)
app.include_router(health_routes.router)
app.include_router(learning_feed_routes.router)
app.include_router(office_routes.router)
app.include_router(agents_routes.router)
app.include_router(site_extra.router)
app.include_router(learning_routes.router)
app.include_router(privacy_routes.router)
app.include_router(chat.router)
app.include_router(connector_routes.router)
app.include_router(db_jobs.router)
app.include_router(db_reports.router)

# Compatibility hooks are resolved at call time for existing callers.
mailer = SmtpMailer
app.state.mailer = lambda: mailer()
report_service.mailer = lambda: mailer()
dbagent_direct.on_done(_after_db_job)

# Keep the old callable signatures without registering duplicate routes.
def health() -> dict:
    return health_service.health(app.state.router)


def owner_code(body: OwnerIn, db: Session = Depends(get_db), visitor: str = Depends(visitor_id)):
    return agent_service.owner_code(body, db, visitor, mailer)


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
