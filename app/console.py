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

import re
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


# ---------------------------------------------------------------- Ask DBaba: any issue, in words, an error, code or SQL
class AskIn(BaseModel):
    text: str = ""


ENGINE_ID = {"mysql": "mysql", "postgres": "postgresql"}
LIVE_CHECK_ROWS = 50


def _open_quiet(c: Connector | None):
    """The developer's connected database for read-only checks, or None (not connected / can't reach it now)."""
    from app.agents import direct

    if c is None or not c.secret:
        return None
    try:
        conn = direct._open(direct.decrypt(c.secret))
    except Exception:  # noqa: BLE001 - the answer then says it's based on what was pasted
        return None
    if (c.db_type or "mysql") == "mysql":
        for limit in ("SET SESSION max_execution_time = 10000", "SET SESSION max_statement_time = 10"):   # MySQL / MariaDB
            try:
                conn.conn.cursor().execute(limit)
            except Exception:  # noqa: BLE001
                pass
    return conn


def _query_blocks(q: str, engine: str | None, hi: bool, connected: bool) -> list[dict]:
    """A query (maybe inside PHP / .NET / Java / Node code): mistakes first, then speed, with the queries to use."""
    from app.agents.sql import analyze_sql

    res = analyze_sql(q, engine=engine)
    if not res.get("ok"):
        return [{"type": "text", "title": "Query" if not hi else "Query", "body": res.get("error") or ""}]
    L = "hi" if hi else "en"
    blocks = []
    corr = res.get("correct") or {}
    mistakes = [{"kind": f["kind"], "title": f["title_" + L], "evidence": f.get("evidence"), "fix": f["fix_" + L],
                 "basis": ((f.get("basis") or {}).get(L) or "")} for f in corr.get("findings") or []]
    blocks.append({"type": "findings", "title": ("Galtiyan" if hi else "Mistakes") if mistakes else
                   ("Query sahi lagti hai" if hi else "No mistakes found in the query"), "items": mistakes})
    fixed = corr.get("corrected")
    if fixed:
        sure = any(f["kind"] in ("error", "wrong") for f in corr.get("findings") or [])
        blocks.append({"type": "sql", "title": ("Theek ki hui query" if hi else "Corrected query") if sure else
                       ("Agar aapka matlab yahi tha" if hi else "If that's what you meant"), "sql": fixed, "run": connected})
    speed = [{"kind": "speed", "title": f["title_" + L], "evidence": f.get("evidence"), "fix": f["fix_" + L]}
             for f in res.get("findings") or [] if f.get("kind") not in ("error", "wrong", "check")]
    if speed and not any(f["kind"] in ("error", "wrong") for f in corr.get("findings") or []):
        blocks.append({"type": "findings", "title": "Speed" if not hi else "Speed", "items": speed})
        if res.get("rewritten") and res["rewritten"] != fixed:
            blocks.append({"type": "sql", "title": "Faster version (same rows)" if not hi else "Tez version (wahi rows)",
                           "sql": res["rewritten"], "run": connected})
        if res.get("indexes"):
            blocks.append({"type": "sql", "title": ("Indexes worth adding (check they don't exist yet)" if not hi else
                                                    "Ye index lagane layak (pehle dekh lo pehle se to nahi)"),
                           "sql": "\n".join(res["indexes"]), "run": False})
        if res.get("check_query") and connected:
            blocks.append({"type": "sql", "title": "See the indexes these tables have now" if not hi else "In tables ke abhi ke index dekho",
                           "sql": res["check_query"], "run": True})
    reading = (res.get("reading") or {}).get(L)
    if reading:
        blocks.append({"type": "text", "title": "How DBaba read this query" if not hi else "DBaba ne query kaise padhi",
                       "body": reading, "folded": True})
    return blocks


def _diagnosis_blocks(text: str, engine: str | None, hi: bool, conn) -> list[dict]:
    """An error, or a problem in words: the likely causes, proven or ruled out on the connected database when there is one."""
    from app.dbknow import brain as dbbrain
    from app.dbknow import reasoning, service

    blocks = []
    result = reasoning.diagnose(text, engine=engine)
    if conn is not None and reasoning.live_plan(result):
        try:
            live = reasoning.run_live(reasoning.live_plan(result), lambda sql, params: conn.rows(sql, params, limit=LIVE_CHECK_ROWS)[1])
            live = {k: v for k, v in live.items() if v is not None}
            if live:
                result = reasoning.diagnose(text, live, engine=engine)
        except Exception:  # noqa: BLE001 - a check that can't run leaves the answer based on the text
            pass
    body = reasoning.report(result, hi, signed_in=True, can_connect=True)
    if body:
        body = body.replace("Open your database area and connect your database, and I'll check it there and prove it.",
                            "Connect your database here and ask again: I'll check it on your database and prove it.")
        body = body.replace("Database area kholo aur database jodo: main khud check karke saboot ke saath bataunga.",
                            "Yahan database jodo aur dobara poocho: main aapke database pe check karke saboot dunga.")
        blocks.append({"type": "text", "title": reasoning.headline(result, hi) or ("Likely causes" if not hi else "Aam wajah"),
                       "body": body, "live": bool(result.get("live"))})
    rec = dbbrain.recognize(text)
    nxt = (rec.get("alternatives") or [{}])[0].get("confidence", 0)
    sure = rec["kind"] == "issue" and (rec["confidence"] >= 0.6 or (rec["confidence"] >= 0.35 and rec["confidence"] >= 4 * nxt))
    issue = service.playbook(rec["id"]) if sure else None
    if issue and not blocks:
        L = "hi" if hi else "en"
        lines = [("Aam wajah:" if hi else "Usual causes:")] + [f"- {c['title_' + L]}" for c in issue.get("causes", [])]
        lines += ["", "Theek karne ke liye:" if hi else "Fixes:"] + [f"- {f['steps_' + L]}" for f in issue.get("fixes", [])]
        if issue.get("verify"):
            lines += ["", ("Pakka kaise: " if hi else "How to confirm it's fixed: ") + issue["verify"].get(L, "")]
        blocks.append({"type": "text", "title": issue["title_" + L], "body": "\n".join(lines)})
    if issue and conn is not None:                             # its read-only checks, run on the developer's database now
        if blocks and blocks[0]["type"] == "text" and not blocks[0].get("live"):
            keep = [ln for ln in blocks[0]["body"].split("\n") if not ln.startswith((
                "Based on what you pasted", "Connect your database here", "Ye aapke paste kiye", "Yahan database jodo"))]
            keep.append("The checks below ran on your database just now (read-only): compare them with the causes above."
                        if not hi else "Neeche ki checks abhi aapke database pe chali (sirf padhne wali): upar ki wajahon se milao.")
            blocks[0] = {**blocks[0], "body": "\n".join(keep).rstrip(), "live": True}
        db_type = "postgres" if engine == "postgresql" else "mysql"
        for chk in service.checks_for(issue, db_type)[:6]:
            try:
                out = conn.query(chk["sql"], LIVE_CHECK_ROWS)
                blocks.append({"type": "table", "title": chk.get("look_for") or "", "sql": chk["sql"], "columns": out["columns"],
                               "rows": [[_cell(v) for v in r] for r in out["rows"]], "limited": out["limited"]})
            except Exception as exc:  # noqa: BLE001 - e.g. the user lacks the right to read that view
                blocks.append({"type": "table", "title": chk.get("look_for") or "", "sql": chk["sql"], "error": _db_message(exc)})
    return blocks


LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|`((?:[^`\\]|\\.)*)`', re.S)
SQL_START = re.compile(r"^\s*\(?\s*(select|with|update|delete|insert|replace)\b", re.I)
SQL_BODY = re.compile(r"\b(from|set|into|values)\b", re.I)


def sql_in_code(text: str) -> str | None:
    """The SQL inside a program's string literals: "SELECT ... " + "FROM ..." joined as the program would (Java, C#,
    JavaScript, Python, PHP), the longest one that reads as a query. None when there is none."""
    found, group, last_end = [], [], None
    for m in LITERAL.finditer(text or ""):
        body = next(g for g in m.groups() if g is not None)
        between = text[last_end:m.start()] if last_end is not None else ""
        if group and not re.fullmatch(r"[\s+.&]*", between):        # not joined to the previous piece: a new string
            found.append(" ".join(group))
            group = []
        group.append(body.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n").replace("\\t", " "))
        last_end = m.end()
    if group:
        found.append(" ".join(group))
    sqls = [re.sub(r"[ \t]+", " ", s).strip() for s in found if SQL_START.match(s) and SQL_BODY.search(s)]
    return max(sqls, key=len) if sqls else None


GLUED_IN_SQL = [  # inside the SQL string itself
    (re.compile(r"\$\{[^}]+\}"), "js"),                          # `... ${email}`  (JavaScript template)
    (re.compile(r"\{\$[A-Za-z_]\w*|\$[A-Za-z_]\w*"), "php"),    # "... '$email'" / {$id}  ($1 placeholders are fine)
]
GLUED_AROUND = [  # how the program builds the string
    (re.compile(r"\b[fF](\"|')\s*(select|with|update|delete|insert)\b[^\n]*?\{[^}]+\}", re.I), "python"),   # f"... {email}"
    (re.compile(r"(\"|')\s+%\s*[(\w]|(\"|')%\s*\(|(\"|')\s*\.format\(|\bsprintf\s*\(|String\.format\s*\(", re.I), "fmt"),
    (re.compile(r"[\"'`]\s*\+\s*[A-Za-z_][\w.\[\]()]*\s*(\+|;|\))|[\"']\s*\.\s*\$[A-Za-z_]\w*"), "concat"),
]
PARAMS = {
    "php": "PDO: $st = $pdo->prepare('SELECT ... WHERE email = ?'); $st->execute([$email]);  (mysqli: prepare + bind_param)",
    "java": "PreparedStatement ps = conn.prepareStatement(\"SELECT ... WHERE email = ?\"); ps.setString(1, email);",
    "dotnet": "var cmd = new SqlCommand(\"SELECT ... WHERE Email = @email\", conn); cmd.Parameters.AddWithValue(\"@email\", email);",
    "js": "await pool.query('SELECT ... WHERE email = ?', [email])  (node-postgres: $1 and [email])",
    "python": "cursor.execute('SELECT ... WHERE email = %s', (email,))",
}


def _code_language(text: str, hint: str) -> str:
    if re.search(r"<\?php|\$[a-z_]\w*\s*=|mysqli_|->prepare|PDO", text):
        return "php"
    if re.search(r"SqlCommand|Parameters\.Add|\bvar\s+\w+\s*=\s*new\b|using\s*\(", text):
        return "dotnet"
    if re.search(r"\bString\s+\w+\s*=|PreparedStatement|createStatement|executeQuery", text):
        return "java"
    if re.search(r"\bconst\s|\blet\s|await\s|=>|require\(", text):
        return "js"
    if re.search(r"\bdef\s|cursor\.execute|\bimport\s", text):
        return "python"
    return {"js": "js", "php": "php", "python": "python"}.get(hint, "")


def injection_finding(text: str, sql: str, hi: bool) -> dict | None:
    """A value glued into the SQL text by the program (SQL injection: someone types SQL into a form field). Defensive only."""
    hit = None
    for rx, how in GLUED_IN_SQL:
        m = rx.search(sql or "")
        if m:
            hit = (m.group(0), how)
            break
    if hit is None:
        for rx, how in GLUED_AROUND:
            m = rx.search(text or "")
            if m:
                hit = (m.group(0), how)
                break
    if hit is None:
        return None
    lang = _code_language(text, hit[1])
    fix = ("Pass the value as a parameter so the database never reads it as SQL. " if not hi else
           "Value ko parameter ki tarah do, taaki database use kabhi SQL na samjhe. ") + (PARAMS.get(lang) or
           "Use your database library's placeholders (? or $1 or @name) and pass the values separately.")
    return {"kind": "wrong", "title": ("The query is built by gluing values into the SQL text: SQL injection risk" if not hi else
                                       "Query mein value seedhi jodi gayi hai: SQL injection ka khatra"),
            "evidence": hit[0][:120], "fix": fix,
            "basis": ("A value typed by a user can end the string and add its own SQL. Parameters keep data and SQL apart."
                      if not hi else "User ki likhi value string band karke apni SQL jod sakti hai. Parameter data aur SQL ko alag rakhte hain.")}


@router.post("/ask")
def ask(body: AskIn, db: Session = Depends(get_db), visitor: str = Depends(_visitor)):
    """DBaba answers a developer's issue: a question, an error, a query or code with a query in it (PHP, .NET, Java, Node...),
    or a problem in words. Read-only checks run on the connected database when one is connected. Nothing is kept: no
    conversation, no goal, no learning feed (this area is private)."""
    from app.agents import direct
    from app.agents.core import query_in
    from app.dbknow import concepts
    from app.dbknow import engines as E
    from app.dbknow import reasoning

    text = (body.text or "").strip()
    if not text:
        return _error("Describe the issue, or paste the error, the code or the query.")
    if len(text) > 30000:
        return _error("That's very long. Paste the error and the part of the code with the query.")
    if not _rate(visitor):
        return _error("That's a lot of questions in one minute. Please wait a few seconds.", 429)
    c, _why = _live(db, visitor)
    if c is not None:
        _touch(db, c)
    lang = E.lang_of(text)
    hi = lang != "en"
    engine = E.named(text) or (ENGINE_ID.get(c.db_type or "") if c is not None else None) or E.shown(text)[0]
    blocks: list[dict] = []
    m = concepts.match(text)
    if m is not None and m.get("concept") is not None:
        reply, _taps = concepts.answer({**m, "engine": E.named(text) or m.get("engine") or engine}, text)
        title, _, rest = reply.partition("\n")
        blocks.append({"type": "text", "title": title, "body": rest.strip()})
        return _reply({"blocks": blocks, "engine": engine, "connected": c is not None, "seconds_left": _seconds_left(c) if c else None})
    q = query_in(text)
    has_error = bool(reasoning.evidence(text))
    if q is None:                                              # code with the query in a string (PHP, Java, .NET, Node...)
        q = sql_in_code(text)
    conn = None
    if (has_error or q is None) and c is not None:
        if not direct._slots.acquire(timeout=15):
            return _error("Goal Engine is busy running other checks. Please try again in a moment.", 503)
        try:
            conn = _open_quiet(c)
            try:
                blocks += _diagnosis_blocks(text, engine, hi, conn)
            finally:
                if conn is not None:
                    direct._close(conn)
        finally:
            direct._slots.release()
    elif has_error or q is None:
        blocks += _diagnosis_blocks(text, engine, hi, None)
    if q is not None:
        qb = _query_blocks(q, engine, hi, c is not None)
        inj = injection_finding(text, sql_in_code(text) or q, hi) if not SQL_START.match(text) else None   # code, not a plain query
        if inj and qb and qb[0]["type"] == "findings":
            qb[0]["items"].insert(0, inj)
            qb[0]["title"] = "Mistakes" if not hi else "Galtiyan"
        blocks += qb
    if not blocks:
        near = concepts.nearest(text)
        L = {"en": "en", "hinglish": "hinglish", "hindi": "hindi"}[lang]
        body = ("I couldn't pin this down yet. What helps most: the exact error message (with its code), the query or the code "
                "that runs it, and what you expected to happen." if not hi else
                "Abhi ye pakad nahi paaya. Sabse zyada madad: poora error message (code ke saath), query ya wo code jo use chalata "
                "hai, aur aap kya hona chahte the.")
        if near:
            body += "\n\n" + ("Related topics I know:" if not hi else "Isse jude topics:") + "\n" + "\n".join(
                f"- {x['title'].get(L) or x['title']['en']}" for x in near)
        blocks.append({"type": "text", "title": "Tell me a bit more" if not hi else "Thoda aur batao", "body": body})
    return _reply({"blocks": blocks, "engine": engine, "connected": c is not None,
                   "seconds_left": _seconds_left(c) if c is not None else None})


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
