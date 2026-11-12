"""SQL console: a developer connects their own database with typed details and runs queries, rows straight back.

What the server keeps, and what it never keeps:
* Never kept: the queries typed and the rows they return. A query runs while the request is open and the rows go back
  in the same reply. Nothing is written to Goal Engine's database, files, logs or learning feed. History lives only in
  the developer's own browser tab.
* Kept while connected: the connection details, encrypted (app/privacy.py), so each query can open the database.
  They are wiped on Disconnect, after IDLE without typing or running anything, when the database area locks
  (app/dbarea.py, 15 idle minutes), on log out, and by the scheduler even if nobody comes back.
* Kept for security: the connection log (time, server, database, address, success), as for every connection.

Same protections as every typed connection (app/agents/direct.py): only public addresses, ports above 1024, tries
limited per browser / address / server / account; one read-only statement at a time (SELECT, WITH, SHOW, DESCRIBE,
EXPLAIN), run in a read-only transaction with a time limit; columns that hold passwords or tokens are masked.
Every endpoint sits inside the locked database area: 404 for anyone not logged in, 403 until the area is opened.
"""

import threading
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Connector, utcnow

router = APIRouter(prefix="/api/console")

IDLE = timedelta(minutes=10)      # no typing and no query for this long: disconnected, details wiped
MAX_ROWS = 1000                   # rows shown per query (the reply says when there were more)
MAX_CELL = 20_000                 # characters per value shown (long text is cut, and says so)
RUNS_PER_MINUTE = 60              # per developer: a console, not a scraper
MARK = "console"                  # Connector.token_hint of console connections (kept apart from the agents' ones)
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "X-Robots-Tag": "noindex, nofollow"}

_runs: dict[str, list[float]] = {}
_runs_lock = threading.Lock()


class ConnectIn(BaseModel):
    type: str = "mysql"
    host: str = ""
    port: str = ""
    database: str = ""
    user: str = ""
    password: str = ""


class SqlIn(BaseModel):
    sql: str = ""


def _visitor(request: Request) -> str:
    return request.state.visitor_id


def _reply(data: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


def _error(message: str, status: int = 400, **extra) -> JSONResponse:
    return _reply({"error": message, **extra}, status)


# ---------------------------------------------------------------- the one console connection of a developer
def _mine(db: Session, visitor: str) -> list[Connector]:
    return list(db.scalars(select(Connector).where(Connector.visitor_id == visitor, Connector.kind == "direct",
                                                   Connector.token_hint == MARK, Connector.status != "disconnected")))


def _end(db: Session, c: Connector) -> None:
    c.secret, c.status = None, "disconnected"
    db.commit()


def _seconds_left(c: Connector) -> int:
    if c.last_seen_at is None:
        return 0
    return max(0, int((c.last_seen_at + IDLE - utcnow()).total_seconds()))


def _live(db: Session, visitor: str) -> tuple[Connector | None, str]:
    """(the connection, '') or (None, why): 'none' / 'timed_out'."""
    live, why = None, "none"
    for c in _mine(db, visitor):
        if c.secret and _seconds_left(c) > 0 and live is None:
            live = c
        else:
            why = "timed_out" if c.secret else why
            _end(db, c)
    return live, ("" if live else why)


def _touch(db: Session, c: Connector) -> None:
    c.last_seen_at = utcnow()
    db.commit()


def _state(c: Connector) -> dict:
    return {"connected": True, "db_type": c.db_type, "database": c.db_name, "host": c.host_label, "version": c.version,
            "can_write": bool(c.can_write), "seconds_left": _seconds_left(c), "idle_seconds": int(IDLE.total_seconds()),
            "max_rows": MAX_ROWS}


def _gone(why: str) -> JSONResponse:
    if why == "timed_out":
        return _error("Disconnected after 10 minutes without typing. Your details were wiped; connect again to go on.",
                      410, connected=False, reason="timed_out")
    return _error("Not connected. Connect your database first.", 409, connected=False, reason="none")


def maintain(db: Session) -> int:
    """Scheduler: wipe console connections that went idle, even if the developer never comes back."""
    n = 0
    for c in db.scalars(select(Connector).where(Connector.token_hint == MARK, Connector.status != "disconnected")):
        if not c.secret or _seconds_left(c) <= 0:
            c.secret, c.status = None, "disconnected"
            n += 1
    db.commit()
    return n


# ---------------------------------------------------------------- talking to the database
def _cell(v):
    if isinstance(v, str) and len(v) > MAX_CELL:
        return v[:MAX_CELL] + f"… [{len(v) - MAX_CELL:,} more characters]"
    return v


def _schema(conn) -> list[dict]:
    try:
        tables = conn.schema()["tables"][:500]
    except Exception:  # noqa: BLE001 - the list of tables is a help, never a reason to fail the connect
        return []
    return [{"name": t["name"], "rows": t.get("rows", 0), "columns": [{"name": c["name"], "type": c["type"], "key": c["key"]}
                                                                    for c in t.get("columns", [])[:300]]} for t in tables]


def _rate(visitor: str) -> bool:
    now = time.time()
    with _runs_lock:
        recent = [t for t in _runs.get(visitor, []) if now - t < 60]
        if len(recent) >= RUNS_PER_MINUTE:
            _runs[visitor] = recent
            return False
        recent.append(now)
        _runs[visitor] = recent
    return True


def _db_message(exc: Exception) -> str:
    """The database's own words for a failed query (what a developer needs), never anything of ours."""
    args = getattr(exc, "args", ())
    if len(args) >= 2 and isinstance(args[0], int):            # MySQL / MariaDB: (code, message)
        return f"ERROR {args[0]}: {args[1]}"[:1000]
    text = str(exc).strip()
    code = getattr(exc, "sqlstate", None)
    return (f"ERROR {code}: {text}" if code else text)[:1000] or "The query failed"


# ---------------------------------------------------------------- endpoints
@router.get("/state")
def state(db: Session = Depends(get_db), visitor: str = Depends(_visitor)):
    c, why = _live(db, visitor)
    if c is None:
        return _reply({"connected": False, "reason": why, "idle_seconds": int(IDLE.total_seconds())})
    return _reply(_state(c))


@router.post("/connect")
def connect(body: ConnectIn, request: Request, db: Session = Depends(get_db), visitor: str = Depends(_visitor)):
    from app import accounts, dbarea
    from app.agents import database as dbagent
    from app.agents import direct

    for old in _mine(db, visitor):                           # one console connection at a time
        _end(db, old)
    fwd = [x.strip() for x in (request.headers.get("x-forwarded-for") or "").split(",") if x.strip()]
    client_ip = fwd[-1] if fwd else (request.client.host if request.client else "")
    me = accounts.current_user(request)
    user = db.get(accounts.User, me.id) if me else None
    form = body.model_dump()
    try:
        if user is not None and dbarea.enabled():
            dbarea.before_connect(db, user, (form.get("host") or "").strip().lower()[:200], client_ip)
        details = direct._clean_form(form)
        direct._rate_limit(visitor, client_ip, details["host"])
        secret = direct.encrypt(details)                     # fails early if the server can't keep secrets
        conn = direct._open(details)
        try:
            version, can_write, tables = conn.version(), conn.can_write(), _schema(conn)
        finally:
            direct._close(conn)
        c, _token = dbagent.create_connector(db, visitor, details["database"])
    except (direct.DirectError, dbagent.DatabaseError) as exc:
        if user is not None:
            dbarea.record(db, user, form, client_ip, False, str(exc)[:200])
        return _error(str(exc))
    c.kind, c.token_hint, c.secret = "direct", MARK, secret
    c.db_type, c.db_name, c.version = details["type"], details["database"], version[:20]
    c.host_label, c.can_write, c.allow_changes = details["host"], bool(can_write), False
    c.status, c.last_seen_at = "online", utcnow()
    db.commit()
    if user is not None:
        dbarea.record(db, user, form, client_ip, True)
        dbarea.tell_owner(user, c.host_label or "", c.db_name or "", client_ip)
    return _reply({**_state(c), "user": details["user"], "tables": tables})


@router.post("/run")
def run(body: SqlIn, db: Session = Depends(get_db), visitor: str = Depends(_visitor)):
    from app.agents import database as dbagent
    from app.agents import direct

    c, why = _live(db, visitor)
    if c is None:
        return _gone(why)
    try:
        sql = dbagent.read_only_sql(body.sql)
    except dbagent.DatabaseError as exc:
        return _error(f"{exc}. This console only reads: SELECT, WITH, SHOW, DESCRIBE, EXPLAIN.", reason="not_read_only")
    if not _rate(visitor):
        return _error("That's a lot of queries in one minute. Please wait a few seconds.", 429)
    _touch(db, c)
    details = direct.decrypt(c.secret or "")
    if not direct._slots.acquire(timeout=15):
        return _error("Goal Engine is busy running other queries. Please try again in a moment.", 503)
    try:
        started = time.monotonic()
        try:
            conn = direct._open(details)
        except direct.DirectError as exc:
            return _error(str(exc))
        try:
            out = conn.query(sql, MAX_ROWS)
        except Exception as exc:  # noqa: BLE001 - the database's own error goes back to the developer, and nowhere else
            return _error(_db_message(exc), reason="db_error", ms=int((time.monotonic() - started) * 1000))
        finally:
            direct._close(conn)
        ms = int((time.monotonic() - started) * 1000)
    finally:
        direct._slots.release()
    _touch(db, c)
    return _reply({"columns": out["columns"], "rows": [[_cell(v) for v in r] for r in out["rows"]],
                   "row_count": out["row_count"], "limited": out["limited"], "hidden_columns": out["hidden_columns"],
                   "ms": ms, "seconds_left": _seconds_left(c)})


@router.post("/check")
def check(body: SqlIn, db: Session = Depends(get_db), visitor: str = Depends(_visitor)):
    """DBaba reads the query (no database needed, nothing stored): mistakes, the fundamental behind each, a corrected query."""
    from app.agents import sqlcorrect

    c, _why = _live(db, visitor)
    if c is not None:
        _touch(db, c)
    sql = (body.sql or "").strip()
    if not sql:
        return _error("Type a query first.")
    if len(sql) > 20000:
        return _error("The query is too long to check here.")
    engine = {"mysql": "mysql", "postgres": "postgresql"}.get(c.db_type or "") if c is not None else None
    try:
        res = sqlcorrect.check(sql, engine=engine)
    except Exception:  # noqa: BLE001 - a reader bug must never break the console
        return _error("DBaba couldn't read this query.")
    keep = ("kind", "topic", "title_en", "evidence", "fix_en")
    return _reply({"findings": [{k: f.get(k) for k in keep} for f in res.get("findings", [])],
                   "corrected": res.get("corrected"), "engine": engine})


@router.get("/schema")
def schema(db: Session = Depends(get_db), visitor: str = Depends(_visitor)):
    from app.agents import direct

    c, why = _live(db, visitor)
    if c is None:
        return _gone(why)
    _touch(db, c)
    try:
        conn = direct._open(direct.decrypt(c.secret or ""))
    except direct.DirectError as exc:
        return _error(str(exc))
    try:
        return _reply({"tables": _schema(conn)})
    finally:
        direct._close(conn)


@router.post("/alive")
def alive(db: Session = Depends(get_db), visitor: str = Depends(_visitor)):
    """The developer is typing: the 10-minute clock starts again."""
    c, why = _live(db, visitor)
    if c is None:
        return _gone(why)
    _touch(db, c)
    return _reply({"seconds_left": _seconds_left(c)})


@router.post("/disconnect")
def disconnect(db: Session = Depends(get_db), visitor: str = Depends(_visitor)):
    for c in _mine(db, visitor):
        _end(db, c)
    return _reply({"connected": False, "reason": "disconnected"})
