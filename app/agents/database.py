"""Database agents through a connector that runs on the user's own server.

Safety model:
- The connector holds the database password; this server never sees it.
- The connector only calls out (poll), runs job kinds it allows, and enforces read-only itself.
- This server also refuses anything that isn't a single read-only statement, so two independent
  checks must both pass.
- Tokens are shown once and stored hashed. Disconnecting kills the token immediately.
"""

import hashlib
import json
import os
import re
import secrets
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, Connector, ConnectorJob, Goal, dumps, loads, utcnow
from app.mind import packs


EXPORT_DIR = Path(__file__).resolve().parents[2] / "data" / "exports"
JOB_KINDS = ("schema", "query", "export", "explain", "health", "live", "plan", "change", "undo", "checks", "optimize")
PLAN_TTL = timedelta(minutes=10)      # Apply must follow a fresh preview
UNDO_TTL = timedelta(days=7)
MAX_OPEN_JOBS = 4            # per user at once: keeps our server and theirs light
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
JOB_TTL = timedelta(minutes=10)
ONLINE_WINDOW = timedelta(seconds=45)
PREVIEW_ROWS = 50
MAX_RESULT_BYTES = 25 * 1024 * 1024


class DatabaseError(ValueError):
    pass


# ---------------------------------------------------------------- read-only SQL check (the connector repeats it)
_STRINGS = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`")
_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*|#[^\n]*", re.S)
_WRITE_WORDS = re.compile(r"\b(insert|update|delete|replace|merge|drop|alter|create|truncate|rename|grant|revoke|lock|unlock|"
                          r"call|exec|execute|load|handler|set|copy|vacuum|analyze\s+table|optimize|repair|into\s+(out|dump)file|"
                          r"pg_sleep|sleep|benchmark|load_file|pg_read_file|lo_import|dblink)\b", re.I)


def read_only_sql(sql: str) -> str:
    """Return the cleaned statement if it's a single read-only query; raise otherwise."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise DatabaseError("The query is empty")
    if len(text) > 20000:
        raise DatabaseError("The query is too long")
    bare = _COMMENTS.sub(" ", _STRINGS.sub("''", text))
    if ";" in bare:
        raise DatabaseError("Only one query at a time")
    if not re.match(r"^\s*(select|with|show|describe|desc|explain)\b", bare, re.I):
        raise DatabaseError("Only reading queries (SELECT, SHOW, DESCRIBE, EXPLAIN) are allowed at this level")
    if _WRITE_WORDS.search(bare):
        raise DatabaseError("This query contains a command that could change data, so it isn't allowed")
    return text


def _ident(name: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_$]{0,63}(\.[A-Za-z_][A-Za-z0-9_$]{0,63})?$", name or ""):
        raise DatabaseError("That table name doesn't look right")
    return name


def table_preview_sql(table: str, limit: int = PREVIEW_ROWS) -> str:
    return f"SELECT * FROM {_ident(table)} LIMIT {int(limit)}"


def table_export_sql(table: str) -> str:
    return f"SELECT * FROM {_ident(table)}"


# ---------------------------------------------------------------- connectors
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_connector(db: Session, visitor_id: str, name: str) -> tuple[Connector, str]:
    if len(list(db.scalars(select(Connector.id).where(Connector.visitor_id == visitor_id,
                                                       Connector.status != "disconnected")))) >= 5:
        raise DatabaseError("You already have 5 connectors. Disconnect one first")
    token = "gec_" + secrets.token_urlsafe(32)
    connector = Connector(visitor_id=visitor_id, name=(name or "My database").strip()[:80], token_hash=_hash(token),
                          token_hint=token[:10], status="waiting")
    db.add(connector)
    db.commit()
    return connector, token


def authenticate(db: Session, token: str) -> Connector | None:
    if not token or not token.startswith("gec_"):
        return None
    connector = db.scalar(select(Connector).where(Connector.token_hash == _hash(token)))
    if connector is None or connector.status == "disconnected" or connector.kind in ("direct", "php"):
        return None
    return connector


def is_online(connector: Connector) -> bool:
    if connector.kind in ("direct", "php"):
        return connector.status == "online"
    return bool(connector.last_seen_at and utcnow() - connector.last_seen_at < ONLINE_WINDOW
                and connector.status != "disconnected")


def connector_dict(connector: Connector, db: Session | None = None) -> dict:
    schema = None
    if db is not None:
        job = db.scalar(select(ConnectorJob).where(ConnectorJob.connector_id == connector.id, ConnectorJob.kind == "schema",
                                                   ConnectorJob.status == "done").order_by(ConnectorJob.id.desc()).limit(1))
        schema = loads(job.result, None) if job else None
    return {
        "id": connector.id, "name": connector.name, "status": connector.status, "online": is_online(connector),
        "db_type": connector.db_type, "db_name": connector.db_name, "can_write": bool(connector.can_write),
        "host": connector.host_label, "token_hint": connector.token_hint, "kind": connector.kind or "agent",
        "allow_changes": bool(connector.allow_changes),
        "last_seen_at": connector.last_seen_at.isoformat() if connector.last_seen_at else None,
        "tables": (schema or {}).get("tables", []),
        **({"file_url": _php_url(connector), "file_name": _php_name(connector)} if connector.kind == "php" else {}),
    }


def _php_url(connector: Connector) -> str:
    from app.agents import phpconn

    return phpconn.file_url(connector)


def _php_name(connector: Connector) -> str:
    from app.agents import phpconn

    return phpconn.file_name(connector)


def hello(db: Session, connector: Connector, info: dict) -> None:
    connector.db_type = str(info.get("db_type", ""))[:20] or None
    connector.db_name = str(info.get("db_name", ""))[:120] or None
    connector.version = str(info.get("version", ""))[:20] or None
    connector.host_label = str(info.get("host", ""))[:120] or None
    connector.can_write = bool(info.get("can_write"))
    connector.allow_changes = bool(info.get("allow_changes"))
    connector.status = "online"
    connector.last_seen_at = utcnow()
    db.commit()


# ---------------------------------------------------------------- jobs
def normalize_sql(sql: str) -> str:
    """For the activity log: the shape of the query without any values (WHERE email = ?)."""
    sql = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "?", sql or "")
    sql = re.sub(r"\b\d+(\.\d+)?\b", "?", sql)
    return re.sub(r"\s+", " ", sql).strip()[:200]


def default_summary(kind: str, payload: dict) -> str:
    return {"schema": "Read the table list", "health": "Health check", "live": "Live view"}.get(kind) or \
        f"{ {'query': 'Query', 'export': 'Excel', 'explain': 'EXPLAIN'}.get(kind, kind) } · {normalize_sql(payload.get('sql', ''))}"


def queue(db: Session, connector: Connector, kind: str, payload: dict | None = None, agent_id: int | None = None,
          summary: str | None = None) -> ConnectorJob:
    if connector.status == "disconnected":
        raise DatabaseError("This connector is disconnected")
    if kind not in JOB_KINDS:
        raise DatabaseError("Unknown job")
    payload = dict(payload or {})
    if kind in ("query", "export", "explain"):
        payload["sql"] = read_only_sql(payload.get("sql", ""))
    if kind == "checks":
        qs = payload.get("queries") or []
        if not isinstance(qs, list) or not qs or len(qs) > 10:
            raise DatabaseError("Between 1 and 10 checks")
        payload["queries"] = [read_only_sql(q) for q in qs]
    if kind == "optimize":
        payload["sql"] = read_only_sql(payload.get("sql", ""))
        payload["rewrites"] = [read_only_sql(q) for q in (payload.get("rewrites") or [])[:5]]
    if kind in ("plan", "change", "undo") and not connector.allow_changes:
        raise DatabaseError("Changes are switched off for this database")
    if kind == "plan":
        from app.agents.direct import gc

        try:
            ch = gc.parse_change(payload.get("sql", ""))
        except ValueError as exc:
            raise DatabaseError(str(exc)) from exc
        payload["sql"] = ch["sql"]
        summary = f"Preview change · {normalize_sql(ch['sql'])}"
    payload["limit"] = min(int(payload.get("limit") or (PREVIEW_ROWS if kind == "query" else 100000)), 100000)
    open_jobs = list(db.scalars(select(ConnectorJob).where(ConnectorJob.connector_id == connector.id,
                                                           ConnectorJob.status.in_(("queued", "claimed")))))
    if kind == "live":
        same = next((j for j in open_jobs if j.kind == "live"), None)
        if same is not None:
            return same                    # one live view at a time, however often the screen asks
    if len([j for j in open_jobs if j.created_at > utcnow() - JOB_TTL]) >= MAX_OPEN_JOBS and kind != "schema":
        raise DatabaseError("A few jobs are still running on this database. Try again in a moment")
    job = ConnectorJob(connector_id=connector.id, agent_id=agent_id, kind=kind, payload=dumps(payload), status="queued",
                       summary=(summary or default_summary(kind, payload))[:300])
    db.add(job)
    db.commit()
    if connector.kind in ("direct", "php"):
        from app.agents import direct

        direct.start(job.id)
        db.refresh(job)
    return job


def approve(db: Session, connector: Connector, plan_id: int) -> ConnectorJob:
    """Apply = run exactly the previewed statement, once, within 10 minutes, expecting the previewed row count."""
    plan = db.get(ConnectorJob, plan_id)
    if plan is None or plan.connector_id != connector.id or plan.kind != "plan" or plan.status != "done":
        raise DatabaseError("Preview the change first")
    result = loads(plan.result, {}) or {}
    if result.get("used"):
        raise DatabaseError("This preview was already applied. Preview again for a new change")
    if utcnow() - plan.finished_at > PLAN_TTL:
        raise DatabaseError("The preview is more than 10 minutes old. Preview again")
    if result.get("blocked"):
        raise DatabaseError(result["blocked"])
    sql = (loads(plan.payload, {}) or {}).get("sql", "")
    result["used"] = True
    plan.result = dumps(result)
    db.commit()
    from app.agents.direct import gc

    ch = gc.parse_change(sql)
    return queue(db, connector, "change", {"sql": sql, "expected": result.get("count"), "plan_id": plan.id},
                 summary=normalize_sql(sql))


def request_undo(db: Session, connector: Connector, change_id: int, force: bool = False) -> ConnectorJob:
    change = db.get(ConnectorJob, change_id)
    if change is None or change.connector_id != connector.id or change.kind != "change" or change.status != "done":
        raise DatabaseError("Nothing to undo")
    result = loads(change.result, {}) or {}
    if result.get("undone"):
        raise DatabaseError("Already undone")
    if not (result.get("undo") or {}).get("available") or utcnow() - change.finished_at > UNDO_TTL:
        raise DatabaseError("The saved copy for this change is gone (kept 7 days), so it can't be undone")
    pending = db.scalar(select(ConnectorJob).where(ConnectorJob.connector_id == connector.id, ConnectorJob.kind == "undo",
                                                   ConnectorJob.status.in_(("queued", "claimed"))))
    if pending is not None:
        raise DatabaseError("An undo is already running")
    return queue(db, connector, "undo", {"change_job_id": change.id, "force": bool(force)},
                 summary=f"Undo · {result.get('table', '')} · change #{change.id}" + (" (overwrite newer edits)" if force else ""))


def save_upload_path(job: ConnectorJob) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORT_DIR / f"job_{job.id}_{secrets.token_hex(6)}.csv.gz"


def claim(db: Session, connector: Connector, limit: int = 3) -> list[ConnectorJob]:
    now = utcnow()
    connector.last_seen_at = now
    if connector.status == "waiting":
        connector.status = "online"
    jobs = []
    for job in db.scalars(select(ConnectorJob).where(ConnectorJob.connector_id == connector.id, ConnectorJob.status == "queued")
                          .order_by(ConnectorJob.id).limit(limit)):
        if now - job.created_at > JOB_TTL:
            job.status, job.error, job.finished_at = "expired", "The connector didn't pick this up in time", now
            continue
        job.status, job.claimed_at = "claimed", now
        jobs.append(job)
    db.commit()
    return jobs


def complete(db: Session, connector: Connector, job_id: int, data: dict, file_path=None) -> ConnectorJob:
    job = db.get(ConnectorJob, job_id)
    if job is None or job.connector_id != connector.id or job.status not in ("claimed", "queued"):
        raise DatabaseError("Unknown job")
    now = utcnow()
    connector.last_seen_at = now
    job.finished_at = now
    if not data.get("ok"):
        job.status, job.error = "failed", str(data.get("error") or "failed")[:500]
        db.commit()
        return job
    result = data.get("result") or {}
    csv_text = data.get("csv")
    if file_path:
        job.file_path = str(file_path)
    if job.kind == "export" and isinstance(csv_text, str):       # older connectors send the CSV inline
        if len(csv_text.encode()) > MAX_RESULT_BYTES:
            job.status, job.error = "failed", "The export is bigger than 25 MB. Add a WHERE condition or fewer columns"
            db.commit()
            return job
        import gzip

        path = save_upload_path(job)
        with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
            fh.write(csv_text)
        job.file_path = str(path)
    if job.kind == "export" and not (job.file_path and os.path.exists(job.file_path)):
        job.status, job.error = "failed", "The export file didn't arrive. Try again"
        db.commit()
        return job
    if job.kind == "undo":
        change = db.get(ConnectorJob, (loads(job.payload, {}) or {}).get("change_job_id") or 0)
        if change is not None:
            cr = loads(change.result, {}) or {}
            cr["undone"], cr["undone_by"] = True, job.id
            change.result, change.undo_blob = dumps(cr), None
    raw = json.dumps(result, ensure_ascii=False, default=str)
    job.result = raw if len(raw) < 2_000_000 else json.dumps({"truncated": True})
    job.status = "done"
    db.commit()
    return job


def activity(db: Session, connector: Connector, after: int = 0, limit: int = 40) -> list[dict]:
    q = select(ConnectorJob).where(ConnectorJob.connector_id == connector.id, ConnectorJob.kind != "live", ConnectorJob.id > after)
    out = []
    for j in db.scalars(q.order_by(ConnectorJob.id.desc()).limit(limit)):
        r = loads(j.result, {}) or {}
        rows = r.get("row_count")
        if rows is None and j.kind in ("change", "undo"):
            rows = r.get("rows")
        if rows is None and j.kind == "plan":
            rows = r.get("count")
        if rows is None and j.kind == "schema":
            rows = len(r.get("tables", []))
        ms = int((j.finished_at - (j.claimed_at or j.created_at)).total_seconds() * 1000) if j.finished_at else None
        undo = None
        if j.kind == "change" and j.status == "done":
            fresh = j.finished_at and utcnow() - j.finished_at < UNDO_TTL
            undo = {"available": bool((r.get("undo") or {}).get("available") and fresh and not r.get("undone")),
                    "undone": bool(r.get("undone")), "where": (r.get("undo") or {}).get("where")}
        out.append({"id": j.id, "kind": j.kind, "summary": j.summary or j.kind, "status": j.status, "error": j.error,
                    "rows": rows, "ms": ms, "at": j.created_at.isoformat(), "undo": undo})
    return out


def job_dict(job: ConnectorJob) -> dict:
    return {"id": job.id, "kind": job.kind, "status": job.status, "error": job.error,
            "result": loads(job.result, None), "download": bool(job.file_path),
            "created_at": job.created_at.isoformat(), "finished_at": job.finished_at.isoformat() if job.finished_at else None}


def own_connector(db: Session, connector_id: int, visitor_id: str) -> Connector:
    connector = db.get(Connector, connector_id)
    if connector is None or connector.visitor_id != visitor_id:
        raise DatabaseError("Connector not found")
    return connector


def visitor_connectors(db: Session, visitor_id: str) -> list[Connector]:
    return list(db.scalars(select(Connector).where(Connector.visitor_id == visitor_id, Connector.status != "disconnected")
                           .order_by(Connector.id.desc())))


# ---------------------------------------------------------------- the database agent (one per goal)
DB_WORDS = packs.words("database", "about")            # the field's words come from its knowledge pack
SQL_TEXT = re.compile(r"\bselect\b[\s\S]+?\bfrom\b", re.I)


def wants_database(goal: Goal | None, text: str) -> bool:
    """A database job the connector can do (not a pasted query to review, that's the SQL checker)."""
    if goal is None or SQL_TEXT.search(text or ""):
        return False
    from app.dbknow import brain

    rec = brain.recognize(text)
    if rec["kind"] == "error" or (rec["kind"] == "issue" and rec["confidence"] >= 0.55):
        return True                       # "too many connections", "Hindi text ???? ban jata hai", a pasted DB error
    if goal.intent == "slow_db_query":   # a slow query: the SQL checker, unless they ask about the whole database
        return bool(re.search(r"\b(health|size|tables|connections|kitna bada|poora database)\b", text or "", re.I))
    return goal.intent == "db_automation" or bool(DB_WORDS.search(text or ""))


def goal_agent(db: Session, goal: Goal) -> Agent | None:
    return db.scalar(select(Agent).where(Agent.goal_id == goal.id, Agent.recipe == "database").order_by(Agent.id.desc()))


def later_parts(text: str) -> list[str]:
    """Things asked for that belong to the next safety levels."""
    t = (text or "").lower()
    out = []
    if re.search(r"\b(backup|restore)\b", t):
        out.append("backup")
    if re.search(r"\b(insert|update|delete|import|badal|hata|migration|migrate|create index)\b", t):
        out.append("write")
    return out


def detect_mode(text: str) -> str:
    t = (text or "").lower()
    if re.search(r"\b(report)\b", t) and re.search(r"\b(roz|daily|har din|weekly|har hafte|monthly|every|subah|schedule|email)\b", t):
        return "report"
    if re.search(r"\b(excel|csv|export|download|sheet|nikaal|nikal)\b", t):
        return "export"
    if re.search(r"\b(slow|health|size|speed|heavy|load|performance|index)\b", t):
        return "health"
    later = later_parts(t)
    return later[0] if later else "explore"


def create_agent(db: Session, goal: Goal, mode: str) -> Agent:
    agent = Agent(visitor_id=goal.visitor_id, goal_id=goal.id, recipe="database", status="proposed",
                  inputs=dumps({"mode": mode, "connector_id": None}))
    db.add(agent)
    db.flush()
    return agent


MODE_TEXT = {
    "explore": ("Apne database ki tables aur data dekho", "See your database's tables and data"),
    "export": ("Database se data Excel/CSV mein nikaalo", "Get data from your database into Excel/CSV"),
    "report": ("Database ki report schedule pe email karo", "Email a database report on a schedule"),
    "health": ("Database ki health aur slow queries check karo", "Check database health and slow queries"),
    "backup": ("Database backup (agla level, jald aa raha)", "Database backups (next level, coming soon)"),
    "write": ("Data badalna (agla level, har badlaav approval ke saath)", "Changing data (next level, every change approved)"),
}


def blocks(mode: str, hi: bool) -> list[dict]:
    steps = [{"kind": "connect", "text": "Aapke database se sirf padhne ke liye judunga (read-only), kuch badlunga nahi" if hi
              else "Connect to your database read-only; nothing gets changed"}]
    if mode == "export":
        steps += [{"kind": "think", "text": "Table ya query chuno, pehle 50 rows ka preview" if hi else "Pick a table or write a query; preview 50 rows first"},
                  {"kind": "do", "text": "Poora data CSV/Excel file mein dunga" if hi else "Give you the full data as a CSV/Excel file"}]
    elif mode == "report":
        steps += [{"kind": "think", "text": "Report ki query chuno aur preview dekho" if hi else "Choose the report query and preview it"},
                  {"kind": "when", "text": "Aapke chune samay pe chalega" if hi else "Runs on the schedule you choose"},
                  {"kind": "do", "text": "CSV report aapko email karunga" if hi else "Email you the CSV report"}]
    elif mode == "health":
        steps += [{"kind": "check", "text": "Sabse badi tables, slow queries, connections aur indexes check karunga" if hi
                   else "Check the biggest tables, slow queries, connections and indexes"},
                  {"kind": "do", "text": "Kya theek karna hai, saaf shabdon mein bataunga" if hi else "Tell you what to fix, in plain words"}]
    elif mode in ("backup", "write"):
        steps += [{"kind": "check", "text": MODE_TEXT[mode][0 if hi else 1]}]
    else:
        steps += [{"kind": "think", "text": "Saari tables, rows aur size dikhaunga" if hi else "Show every table with rows and size"},
                  {"kind": "do", "text": "Kisi bhi table ka data dekh ya download kar sakte ho" if hi else "View or download any table's data"}]
    return steps


def _blocks_for(mode: str, hi: bool, connector) -> list[dict]:
    steps = blocks(mode, hi)
    if connector is not None and connector.allow_changes:
        steps[0] = {"kind": "connect", "text": "Database padhunga; badlaav sirf aapke preview aur Apply ke baad, hamesha Undo ke saath" if hi
                    else "Read your database; change data only after your preview and Apply, always with Undo"}
    return steps


def database_dict(db: Session, agent: Agent, hi: bool) -> dict:
    inputs = loads(agent.inputs, {}) or {}
    mode = inputs.get("mode", "explore")
    connector = db.get(Connector, inputs["connector_id"]) if inputs.get("connector_id") else None
    if connector is not None and connector.status == "disconnected":
        connector = None
    others = [connector_dict(c, db) for c in visitor_connectors(db, agent.visitor_id)]
    return {
        "id": agent.id, "lang": "hi" if hi else "en", "recipe": "database", "database": True, "mode": mode,
        "name": MODE_TEXT[mode][0 if hi else 1].split(" (")[0], "status": agent.status, "access": "connector",
        "available": mode not in ("backup", "write"), "blocks": _blocks_for(mode, hi, connector),
        "connector": connector_dict(connector, db) if connector else None, "connectors": others,
        "runs": [], "owner": _owner(db, agent.visitor_id), "needs_owner": mode == "report", "brain": _brain_view(inputs.get("brain"), hi),
    }


def _brain_view(rec, hi: bool):
    if not rec or not rec.get("kind"):
        return None
    from app.dbknow import brain

    return {**rec, "text": brain.describe(rec, hi)}


def _owner(db: Session, visitor_id: str) -> dict:
    from app.agents.automation import owner_of

    owner = owner_of(db, visitor_id)
    return {"email": owner.email if owner else "", "verified": bool(owner and owner.verified_at),
            "business": owner.business_name if owner else ""}


def plan_reply(mode: str, hi: bool, has_connector: bool, later: list[str] | None = None) -> str:
    title = MODE_TEXT[mode][0 if hi else 1]
    if mode not in ("backup", "write") and later:
        names = ", ".join(("backup" if p == "backup" else ("data badalna" if hi else "changing data")) for p in later)
        note = (f" {names.capitalize()} agle level mein aayega." if hi else f" {names.capitalize()} comes in the next level.")
        return plan_reply(mode, hi, has_connector) + note
    if mode in ("backup", "write"):
        return (f"Main ye karunga: {title}. Pehle read-only connector lagaate hain; badlaav wala level uske baad aayega."
                if hi else f"Here's what I'll do: {title}. First we connect read-only; the changes level comes after that.")
    if has_connector:
        return (f"Main ye karunga: {title}. Aapka database juda hua hai, table chuno." if hi
                else f"Here's what I'll do: {title}. Your database is connected; pick a table.")
    return (f"Main ye karunga: {title}. Bas batao aapka database kahan hai, 1-2 step mein jud jayega."
            if hi else f"Here's what I'll do: {title}. Just tell me where your database is; it connects in a step or two.")


def clean_exports(max_age_hours: int = 24) -> None:
    """Exported files are kept for a day, then deleted."""
    import time

    if not EXPORT_DIR.exists():
        return
    cutoff = time.time() - max_age_hours * 3600
    for f in EXPORT_DIR.glob("job_*.csv"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass



def mind_state(db: Session, agent: Agent) -> dict:
    """What the shared mind may build next steps on."""
    from app.agents.core import goal_agents
    from app.models import AgentRun

    checked = any(r.status == "done" for a in goal_agents(db, agent.goal_id) if a.recipe == "sql_review"
                  for r in db.scalars(select(AgentRun).where(AgentRun.agent_id == a.id).limit(1)))
    return {"connected": bool(visitor_connectors(db, agent.visitor_id)), "query_checked": checked}


# ------------------------------------------------------------------ hooks the brain calls (named in the database pack)
def wants_turn(db: Session, goal: Goal, text: str, relation: str, held: bool) -> bool:
    from app.agents.core import query_in

    if relation in ("howto", "chit_chat") and query_in(text):
        return True                       # a pasted query read as a how-to ("this one is slow: SELECT ..."): check the query
    return relation not in ("howto", "safety", "chit_chat") and wants_database(goal, text)


def _check_pasted_query(db: Session, goal: Goal, text: str, hi: bool):
    """Run the SQL checker on the query in this message, in this goal."""
    from app.agents.core import RECIPES, AgentError, goal_agents, inputs_from_text, run_agent
    from app.engine import _after_run

    recipe = RECIPES["sql_review"]
    found = inputs_from_text(recipe, text, {})
    agent = next((a for a in goal_agents(db, goal.id) if a.recipe == "sql_review"), None)
    if agent is None:
        agent = Agent(visitor_id=goal.visitor_id, goal_id=goal.id, recipe="sql_review", inputs=dumps(found), status="proposed")
        db.add(agent)
    else:
        agent.inputs = dumps({**(loads(agent.inputs, {}) or {}), **found})
    goal.intent = "slow_db_query"
    db.flush()
    try:
        run = run_agent(db, agent, hi)
        reply, _ = _after_run(agent, run, hi)
    except AgentError as exc:
        return str(exc), [], "agent_plan"
    return reply, [], "agent_run"


def _sure(rec: dict) -> bool:
    """Sure of the problem: very likely, or a clear winner over the next guess (more problems known = lower top scores)."""
    nxt = (rec.get("alternatives") or [{}])[0].get("confidence", 0)
    return rec["confidence"] >= 0.6 or (rec["confidence"] >= 0.35 and rec["confidence"] >= 4 * nxt)


def take_turn(db: Session, goal: Goal, text: str, turn, visitor_id: str):
    """Database work: the agent works through a connector on the user's own server."""
    from app.agents.core import query_in
    from app.dbknow import brain as dbbrain

    if query_in(text):                    # a pasted query is for the SQL checker, whatever else the message says
        return _check_pasted_query(db, goal, text, turn.hi)

    mode = detect_mode(text)
    rec = dbbrain.recognize(text)
    agent = goal_agent(db, goal)
    if agent is None:
        agent = create_agent(db, goal, mode)
    inputs = loads(agent.inputs, {}) or {}
    if mode != "explore":
        inputs["mode"] = mode
    if rec["kind"] in ("sql", "error") or (rec["kind"] == "do" and rec["confidence"] >= 0.35) or (
            rec["kind"] == "issue" and _sure(rec)):                   # name a specific problem only when sure
        inputs["brain"] = {**rec, "sql": text if rec["kind"] == "sql" else ""}
    agent.inputs = dumps(inputs)
    goal.title = MODE_TEXT[inputs.get("mode", "explore")][0 if turn.hi else 1].split(" (")[0]
    goal.missing_info = "[]"
    goal.pending = None
    goal.next_action_type = "execute"
    goal.next_action = "Connect the database, then pick a table"
    db.commit()
    has = bool(visitor_connectors(db, visitor_id))
    reply = plan_reply(mode, turn.hi, has, later_parts(text))
    b = inputs.get("brain") or {}
    seen = dbbrain.describe(b, turn.hi) if (b.get("kind") in ("issue", "error", "sql") or b.get("id") == "slow_query") else ""
    if seen:
        reply = (f"Samjha: {seen}. " if turn.hi else f"Understood: {seen}. ") + (
            ("Database juda hua hai, card mein check shuru karo." if has else "Bas batao database kahan hai; judte hi main khud check karke wajah aur fix dhoondhunga.") if turn.hi
            else ("Your database is connected; start the check on the card." if has else "Just tell me where your database is; once it's connected I'll check it myself and find the cause and the fix."))
    return reply, [], "agent_plan"


# ------------------------------------------------------------------ after a query check: "optimize it", "why slow", "kya karu"
SQL_FOLLOW_RE = re.compile(r"\b(optimi[sz]\w*|faster|speed( (it|this|that))? up|improve|better|rewrite|re-write|fix (it|this|the query)|tune|slow|fast|"
                           r"index(es)?|why|explain|kaise|kya karu|kya karna|theek karo|tez|jaldi|behtar|sudharo|samjhao)\b", re.I)
DEV_FOLLOW_RE = re.compile(r"तेज़|तेज|ऑप्टिमाइ|बेहतर|सुधार|धीम|स्लो|इंडेक्स|क्यों|क्यूं|समझा|बदल|फ़र्क|फर्क|अंतर|वजह|कारण")
NEW_SQL_RE = re.compile(r"\b(select|update|delete|insert)\b[\s\S]+\b(from|into|set)\b", re.I)


def _sql_agent(db, goal):
    from app.agents.core import goal_agents
    from app.models import AgentRun

    a = next((x for x in goal_agents(db, goal.id) if x.recipe == "sql_review"), None)
    if a is None:
        return None, None
    run = db.scalars(select(AgentRun).where(AgentRun.agent_id == a.id).order_by(AgentRun.id.desc()).limit(1)).first()
    return a, run


def holds_conversation(db, goal, text: str, any_text: bool = False):
    """A query was checked in this conversation and the person asks about it: answer about THAT query."""
    if any_text or NEW_SQL_RE.search(text or "") or not (SQL_FOLLOW_RE.search(text or "") or DIFF_RE.search(text or "")
                                                         or WHY_RE.search(text or "") or DEV_FOLLOW_RE.search(text or "")):
        return None
    a, run = _sql_agent(db, goal)
    return a if run is not None and run.status == "done" else None


DIFF_RE = re.compile(r"\b(what (did )?you change|what changed|what is the diff\w*|diff\w*|compare|original|kya badla|kya change|"
                     r"farak|fark|antar|same (hi )?hai|any change)\b", re.I)
WHY_RE = re.compile(r"\b(why|kyun|kyu|reason|wajah|explain|samjhao|kaise slow|what makes)\b|क्यों|क्यूं|वजह|कारण|समझा", re.I)
DIFF_DEV_RE = re.compile(r"बदला|बदलाव|फ़र्क|फर्क|अंतर|क्या बदल")
INDEX_RE = re.compile(r"\b(which|what|kaun\w*|konsa|kaunsa)\b[^?]*\bindex(es)?\b|\bindex(es)?\b[^?]*\b(kaun\w*|konsa|lagau\w*|lagana|lagaye|need|add|chahiye)\b"
                      r"|इंडेक्स", re.I)


def continue_turn(db, goal, agent, text: str):
    """Answers about the query that was checked: the optimised version, what changed, or why it's slow.
    Worked out again from the query itself, so it's never a stale or repeated answer."""
    from app.agents.sql import analyze_sql, rewrite, unwrap
    from app.ml.normalize import detect_language
    from app.models import Message

    hi = detect_language(text) in ("Hinglish", "Hindi") or (goal.detected_language in ("Hinglish", "Hindi") and len(text.split()) < 6)
    inputs = loads(agent.inputs, {}) or {}
    query = unwrap(inputs.get("query", ""))
    res = analyze_sql(query, inputs.get("explain", ""))
    better = res.get("rewritten") or rewrite(query)
    changed = _norm(better) != _norm(query)
    last = db.scalars(select(Message).where(Message.goal_id == goal.id, Message.role == "assistant")
                      .order_by(Message.id.desc()).limit(1)).first()
    ask = ("diff" if DIFF_RE.search(text or "") or DIFF_DEV_RE.search(text or "") else "why" if WHY_RE.search(text or "")
           else "index" if INDEX_RE.search(text or "") else "optimize")
    asked = ask
    reply = _sql_reply(ask, query, better, changed, res, hi)
    if last is not None and _norm(last.text) == _norm(reply):          # never the same answer twice in a row
        if inputs.get("last_ask") == ask:                              # they asked the same thing again: the other angle
            ask = {"optimize": "why", "why": "diff", "diff": "why", "index": "why"}[ask]
            reply = _sql_reply(ask, query, better, changed, res, hi)
        else:                                                          # it's what my last answer already said: say so, in short
            reply = (("Ye upar wale jawab mein hi hai, short mein:\n\n" if hi else "That's what my last answer covered; in short:\n\n")
                     + _first_part(_sql_reply(ask, query, better, changed, res, hi)))
    inputs["last_ask"] = asked
    agent.inputs = dumps(inputs)
    return reply, [], hi


def _first_part(reply: str) -> str:
    """The opening of an answer: its first paragraph, and what that paragraph introduces."""
    paras = reply.split("\n\n")
    return "\n\n".join(paras[:2] if paras[0].rstrip().endswith(":") and len(paras) > 1 else paras[:1])


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "")).strip().rstrip(";").lower()


def _sql_reply(ask, query, better, changed, res, hi) -> str:
    notes = [n["hi" if hi else "en"] for n in res.get("notes", [])]
    fixes = [f for f in res.get("findings", [])
             if not f["title_en"].lower().startswith(("these columns need an index", "missing indexes"))]
    idx = res.get("indexes") or []
    parts = []
    if ask == "diff":
        if changed:
            parts.append(("Ye badla (result wahi rahega):" if hi else "What I changed (the result stays the same):"))
            parts += [f"- {f['title_hi' if hi else 'title_en']}: {f['fix_hi' if hi else 'fix_en']}" for f in fixes] or \
                     [("- Query ka likhne ka tareeka, result wahi" if hi else "- How it's written, not what it returns")]
            parts.append(("Pehle:" if hi else "Before:") + f"\n\n{query}\n\n" + ("Ab:" if hi else "After:") + f"\n\n{better}")
        elif fixes:
            parts.append("Maine aapki query ka text nahi badla: neeche ki baatein badalne se result badal sakta hai, isliye ye faisla aapka hai:"
                         if hi else "I didn't change your query's text: changing the points below could change what it returns, so "
                         "they're your call:")
            parts += [f"- {f['title_hi' if hi else 'title_en']}: {f['fix_hi' if hi else 'fix_en']}" for f in fixes]
        else:
            parts.append(("Maine aapki query ka text nahi badla: ye pehle se theek likhi hai, isme koi aisa pattern nahi jo index ko rokta ho. "
                          + ("Farak sirf index ka hai (neeche), query wahi rahegi." if idx else "Koi naya index bhi nahi chahiye.")) if hi else
                         ("I didn't change your query's text: it's already written well, with nothing in it that stops an index "
                          "being used. " + ("The only difference is the indexes below; your query stays exactly as it is." if idx
                                            else "It needs no new index either.")))
        if idx:
            parts.append(("Index:" if hi else "Indexes:") + "\n\n" + "\n".join(idx))
        return "\n\n".join(parts)
    if ask == "why":
        parts.append("Ye query slow kyun ho sakti hai:" if hi else "Why this query can be slow:")
        reasons = []
        if idx:
            reasons.append(("Jin columns pe ye filter aur join karti hai unpe index na ho to MySQL har table poori padhta hai: "
                            + ", ".join(re.sub(r"^ALTER TABLE (\S+) ADD INDEX \S+ \((.+)\);$", r"\1 (\2)", x) for x in idx)) if hi else
                           ("Without an index on the columns it filters and joins on, MySQL reads each table in full: "
                            + ", ".join(re.sub(r"^ALTER TABLE (\S+) ADD INDEX \S+ \((.+)\);$", r"\1 (\2)", x) for x in idx)))
        reasons += [f["title_hi" if hi else "title_en"] + ": " + f["fix_hi" if hi else "fix_en"] for f in fixes]
        reasons += notes
        parts += [f"- {r}" for r in reasons] or [("- Isme slow hone wala koi pattern nahi dikha; EXPLAIN output paste karo." if hi else
                                                  "- Nothing in it looks slow by itself; paste the EXPLAIN output and I'll find the step.")]
        if res.get("check_query"):
            parts.append(("Pehle dekho kaunse index pehle se hain:" if hi else "First see which indexes you already have:")
                         + f"\n\n{res['check_query']}")
        return "\n\n".join(parts)
    if ask == "index":
        if not idx:
            parts.append("Is query ke liye koi naya index nahi chahiye: jin columns pe ye filter ya join karti hai wo primary key hain, "
                         "pehle se indexed hain, ya index unke kaam nahi aata." if hi else
                         "No new index is needed for this query: the columns it filters and joins on are primary keys, already "
                         "indexed, or not ones an index can help with.")
            if fixes:
                parts.append(("Speed in baaton se aayegi:\n" if hi else "The speed-up comes from these points instead:\n")
                             + "\n".join(f"- {f['fix_hi' if hi else 'fix_en']}" for f in fixes[:5]))
            return "\n\n".join(parts)
        if res.get("check_query"):
            parts.append(("Is query ke liye ye index chahiye, agar pehle se nahi hain. Pehle ye chalao, dekho kaunse hain:" if hi else
                          "This query needs these indexes, if you don't have them yet. First run this to see which you have:")
                         + f"\n\n{res['check_query']}")
            parts.append(("Phir inme se sirf wo lagao jo upar nahi dikhe:" if hi else "Then add only the ones it doesn't show:")
                         + "\n\n" + "\n".join(idx))
        else:
            parts.append(("Is query ke liye ye index lagao (aapke table structure se check kiye):" if hi else
                          "Add these indexes for this query (checked against your table structure):") + "\n\n" + "\n".join(idx))
        if res.get("assumed_primary"):
            ap = res["assumed_primary"]
            parts.append(f"{', '.join(ap)} ko primary key maana (inpe pehle se index hota hai)." if hi else
                         f"I took {', '.join(ap)} to be {'primary keys' if len(ap) > 1 else 'the primary key'} (already indexed).")
        return "\n\n".join(parts)
    # optimize
    if changed:
        parts.append(("Aapki query ka behtar roop (result wahi rahega):" if hi else "Your query, optimised (same result):") + f"\n\n{better}")
        if fixes:
            parts.append(("Kya badla:\n" if hi else "What changed:\n") + "\n".join(f"- {f['fix_hi' if hi else 'fix_en']}" for f in fixes[:5]))
    elif fixes:
        parts.append(("Query ka text maine nahi badla: inhe badalne se result badal sakta hai, isliye faisla aapka. Ye baatein ise tez "
                      "karengi:\n" if hi else "I haven't changed the query's text: these changes could change what it returns, so "
                      "they're your call. They're what makes it faster:\n") + "\n".join(f"- {f['fix_hi' if hi else 'fix_en']}" for f in fixes[:5]))
    elif idx:
        parts.append("Aapki query pehle se theek likhi hai, iske text mein badalne layak kuch nahi; isliye wahi query rakho. "
                     "Speed index aur in baaton se aayegi:" if hi else
                     "Your query is already written well, so I'm not changing its text. The speed-up comes from the indexes "
                     "and these points:")
    else:
        parts.append("Aapki query pehle se theek likhi hai aur koi naya index bhi nahi chahiye; isliye wahi query rakho." if hi else
                     "Your query is already written well and needs no new index, so I'm not changing anything.")
    if notes:
        parts.append("\n".join(f"- {n}" for n in notes))
    if idx:
        if res.get("check_query"):
            parts.append(("Pehle ye chalao, dekho kaunse index pehle se hain:" if hi else
                          "First run this to see which indexes you already have:") + f"\n\n{res['check_query']}")
            parts.append(("Phir inme se sirf wo lagao jo upar nahi dikhe:" if hi else "Then add only the ones it doesn't show:")
                         + "\n\n" + "\n".join(idx))
        else:
            parts.append(("Ye index lagao:" if hi else "Add these indexes:") + "\n\n" + "\n".join(idx))
    if res.get("assumed_primary"):
        ap = res["assumed_primary"]
        parts.append(f"{', '.join(ap)} ko primary key maana (inpe pehle se index hota hai)." if hi else
                     f"I took {', '.join(ap)} to be {'primary keys' if len(ap) > 1 else 'the primary key'} (already indexed).")
    parts.append(("Index lagane ke baad EXPLAIN chala ke output paste karo, main confirm kar dunga." if hi else
                  "After adding them, paste the query's EXPLAIN output and I'll confirm the indexes are used.") if idx else
                 ("Phir bhi slow lage to EXPLAIN chala ke output paste karo, main wo step dhoondh dunga." if hi else
                  "If it's still slow, paste the query's EXPLAIN output and I'll find the slow step."))
    return "\n\n".join(parts)
