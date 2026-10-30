"""The locked database area: only verified accounts, opened with a fresh email code, locked after 15 idle minutes.

* Every connection endpoint answers 404 to anyone not logged in (it doesn't exist for them).
* Typed database passwords live (encrypted) only while the area is open: locking, logging out or 15 idle minutes
  wipes them, and the database must be connected again.
* Each database connected sends the owner an email; every attempt is in the connection log.
* Per-account limits: connect attempts, different servers tried (no probing through us), jobs waiting.
"""

import logging
import os
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import accounts
from app.db import get_db
from app.models import Connector, ConnectorJob, DbConnectLog, User, UserSession, utcnow

log = logging.getLogger(__name__)
router = APIRouter()

IDLE_LOCK = timedelta(minutes=15)
TOUCH_EVERY = timedelta(seconds=30)
LOG_KEEP = timedelta(days=90)
CONNECTS_PER_HOUR = 10            # per account
HOSTS_PER_DAY = 5                 # different database servers per account per day: no scanning through us
JOBS_WAITING_PER_USER = 3
JOBS_WAITING_TOTAL = 40

# Everything that reaches a user's database. NOT /api/connector/ (the program on their server, with its own token)
# and not /api/connector-info (our server's public IP, shown in instructions).
LOCKED = re.compile(r"^/api/(connectors(/|$)|connector-jobs/|agents/\d+/(php-connector|connector|connect/))")


def enabled() -> bool:
    return os.getenv("DB_AREA_LOCK", "on").strip().lower() != "off"


def connect_open(user=None) -> bool:
    """DB_CONNECT=off: connecting databases is closed (launch fallback); paste answers keep working.
    DB_CONNECT_TESTERS=a@x.com,b@y.com: those confirmed accounts can still connect, to test it before it opens."""
    if os.getenv("DB_CONNECT", "on").strip().lower() != "off":
        return True
    testers = {e.strip().lower() for e in os.getenv("DB_CONNECT_TESTERS", "").split(",") if e.strip()}
    return bool(user is not None and getattr(user, "email_verified", False) and (user.email or "").lower() in testers)


NOT_FOUND = JSONResponse({"detail": "Not Found"}, status_code=404)


def _locked_reply(state: str) -> JSONResponse:
    msg = {"verify": "Confirm your email first, then you can connect a database.",
           "locked": "Your database area is locked. We'll email you a code to open it.",
           "soon": "Connecting your database opens in a few days. Until then, paste the error and get the cause and the fix."}[state]
    return JSONResponse({"error": msg, "db_area": state}, status_code=403,
                        headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"})


def state(db: Session, user: User | None, sess: UserSession | None) -> str:
    """none (logged out) / verify / locked / open"""
    if user is None or sess is None:
        return "none"
    if not connect_open(user):
        return "soon"
    if not user.email_verified:
        return "verify"
    if sess.stepup_at is None or sess.db_seen_at is None or utcnow() - sess.db_seen_at > IDLE_LOCK:
        return "locked"
    return "open"


def guard(request: Request):
    """Middleware helper: None to let the request through, or the reply to send instead."""
    if not LOCKED.match(request.url.path):
        return None
    user, sess = accounts.current_user(request), getattr(request.state, "user_session", None)
    if not enabled():
        return None if connect_open(user) else _locked_reply("soon")
    from app.db import get_engine

    with Session(get_engine(), expire_on_commit=False) as db:
        st = state(db, user, sess)
        if st == "none":
            return NOT_FOUND
        if st != "open":
            if st == "locked" and sess.stepup_at is not None:
                lock(db, sess)               # it went idle: the typed passwords go now
            return _locked_reply(st)
        if utcnow() - sess.db_seen_at > TOUCH_EVERY:
            db.query(UserSession).filter(UserSession.id == sess.id).update({"db_seen_at": utcnow()})
            db.commit()
    return None


def _wipe_passwords(db: Session, user: User) -> int:
    """Typed database details exist only while the area is open somewhere for this account."""
    open_elsewhere = db.scalar(select(func.count()).select_from(UserSession).where(
        UserSession.user_id == user.id, UserSession.stepup_at.is_not(None), UserSession.db_seen_at > utcnow() - IDLE_LOCK))
    if open_elsewhere:
        return 0
    n = 0
    for c in db.scalars(select(Connector).where(Connector.visitor_id == user.visitor_id, Connector.kind == "direct",
                                                Connector.secret.is_not(None))):
        c.secret, c.status = None, "disconnected"
        n += 1
    db.commit()
    return n


def lock(db: Session, sess: UserSession) -> int:
    db.query(UserSession).filter(UserSession.id == sess.id).update({"stepup_at": None, "db_seen_at": None})
    db.commit()
    user = db.get(User, sess.user_id)
    return _wipe_passwords(db, user) if user else 0


def maintain(db: Session) -> dict:
    """Scheduler: lock idle areas (and wipe their passwords) even if nobody comes back; trim the log."""
    locked = 0
    for s in db.scalars(select(UserSession).where(UserSession.stepup_at.is_not(None),
                                                  (UserSession.db_seen_at.is_(None)) | (UserSession.db_seen_at < utcnow() - IDLE_LOCK))):
        lock(db, s)
        locked += 1
    # sessions that ended (logout, expiry) must not leave passwords behind either
    for user in db.scalars(select(User).join(Connector, Connector.visitor_id == User.visitor_id)
                           .where(Connector.kind == "direct", Connector.secret.is_not(None)).distinct()):
        _wipe_passwords(db, user)
    trimmed = db.execute(delete(DbConnectLog).where(DbConnectLog.at < utcnow() - LOG_KEEP)).rowcount
    db.commit()
    return {"db_areas_locked": locked, "db_log_trimmed": trimmed}


# ---------------------------------------------------------------- limits and log around a connect
def before_connect(db: Session, user: User, host: str, ip: str) -> None:
    from app.agents.direct import DirectError

    now = utcnow()
    tries = db.scalar(select(func.count()).select_from(DbConnectLog).where(DbConnectLog.user_id == user.id,
                                                                          DbConnectLog.at > now - timedelta(hours=1)))
    if tries >= CONNECTS_PER_HOUR:
        raise DirectError("Too many connection tries. Please wait an hour.")
    hosts = set(db.scalars(select(DbConnectLog.host).where(DbConnectLog.user_id == user.id, DbConnectLog.at > now - timedelta(days=1))))
    if host.lower() not in hosts and len(hosts) >= HOSTS_PER_DAY:
        log.warning("db area: account %s tried a %dth database server today", user.id, len(hosts) + 1)
        raise DirectError(f"You've tried {HOSTS_PER_DAY} different database servers today. Please try again tomorrow, "
                          "or ask for an expert if you need more.")


def record(db: Session, user: User, form: dict, ip: str, ok: bool, note: str = "") -> None:
    db.add(DbConnectLog(user_id=user.id, host=(form.get("host") or "")[:200].lower(), database=(form.get("database") or "")[:120],
                        ip=ip[:64], ok=ok, note=note[:200]))
    db.commit()


def tell_owner(user: User, host: str, database: str, ip: str) -> None:
    """An email for every database connected, so a stolen login can't connect one quietly."""
    when = utcnow().strftime("%d %b %Y, %H:%M UTC")
    body = (f"Hi {user.full_name.split()[0] if user.full_name else ''},\n\n"
            f"A database was just connected to your Goal Engine account:\n\n"
            f"  Database: {database}\n  Server: {host}\n  When: {when}\n  From address: {ip or 'unknown'}\n\n"
            "If this was you, there's nothing to do.\n"
            "If it wasn't, change your password now (Log in > Forgot password) and change that database user's password too.\n\n"
            "Goal Engine")
    try:
        m = accounts.mailer()
        if getattr(m, "configured", True):
            m.send(user.email, "A database was connected to your Goal Engine account", body)
    except Exception:  # noqa: BLE001 - the connect itself already succeeded
        log.exception("Couldn't send the database-connected email")


def check_queue(db: Session, visitor_id: str) -> None:
    from app.agents.database import DatabaseError

    waiting = (ConnectorJob.status.in_(("queued", "claimed")), ConnectorJob.created_at > utcnow() - timedelta(minutes=10))
    mine = db.scalar(select(func.count()).select_from(ConnectorJob).join(Connector, Connector.id == ConnectorJob.connector_id)
                     .where(Connector.visitor_id == visitor_id, Connector.kind.in_(("direct", "php")), *waiting))
    if mine >= JOBS_WAITING_PER_USER:
        raise DatabaseError("Your earlier checks are still running. Please wait a few seconds.")
    total = db.scalar(select(func.count()).select_from(ConnectorJob).join(Connector, Connector.id == ConnectorJob.connector_id)
                      .where(Connector.kind.in_(("direct", "php")), *waiting))
    if total >= JOBS_WAITING_TOTAL:
        raise DatabaseError("Goal Engine is busy right now. Please try again in a minute.")


# ---------------------------------------------------------------- endpoints
class CodeIn(BaseModel):
    code: str = ""


def _who(request: Request, db: Session):
    user, sess = accounts.current_user(request), getattr(request.state, "user_session", None)
    if user is None or sess is None:
        return None, None
    return db.get(User, user.id), db.get(UserSession, sess.id)


def _status(db: Session, user: User, sess: UserSession) -> dict:
    st = state(db, user, sess)
    left = None
    if st == "open":
        left = max(1, int((IDLE_LOCK - (utcnow() - sess.db_seen_at)).total_seconds() // 60))
    return {"state": st, "minutes_left": left, "email": user.email}


@router.get("/api/db-area")
def area_status(request: Request, db: Session = Depends(get_db)):
    user, sess = _who(request, db)
    if user is None:
        return NOT_FOUND
    return _status(db, user, sess)


@router.post("/api/db-area/code")
def area_code(request: Request, db: Session = Depends(get_db)):
    user, sess = _who(request, db)
    if user is None:
        return NOT_FOUND
    if not connect_open(user):
        return _locked_reply("soon")
    if not user.email_verified:
        return _locked_reply("verify")
    try:
        accounts.send_code(db, user, "database", accounts.client_ip(request))
    except accounts.AccountError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)
    return {"ok": True, "email": user.email}


@router.post("/api/db-area/open")
def area_open(body: CodeIn, request: Request, db: Session = Depends(get_db)):
    user, sess = _who(request, db)
    if user is None:
        return NOT_FOUND
    if not connect_open(user):
        return _locked_reply("soon")
    if not user.email_verified:
        return _locked_reply("verify")
    try:
        accounts.check_code(db, user, "database", body.code)
    except accounts.AccountError as exc:
        return JSONResponse({"error": str(exc), "field": exc.field}, status_code=exc.status)
    sess.stepup_at = sess.db_seen_at = utcnow()
    db.commit()
    return _status(db, user, sess)


@router.post("/api/db-area/lock")
def area_lock(request: Request, db: Session = Depends(get_db)):
    user, sess = _who(request, db)
    if user is None:
        return NOT_FOUND
    wiped = lock(db, sess)
    return {"state": "locked", "passwords_removed": wiped}


@router.get("/api/db-area/log")
def area_log(request: Request, db: Session = Depends(get_db)):
    user, sess = _who(request, db)
    if user is None:
        return NOT_FOUND
    rows = db.scalars(select(DbConnectLog).where(DbConnectLog.user_id == user.id).order_by(DbConnectLog.id.desc()).limit(30))
    return {"log": [{"at": r.at.isoformat(), "host": r.host, "database": r.database, "ip": r.ip, "ok": r.ok, "note": r.note} for r in rows]}
