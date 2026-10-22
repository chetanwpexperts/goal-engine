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
import re
import secrets
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, Connector, ConnectorJob, Goal, dumps, loads, utcnow


EXPORT_DIR = Path(__file__).resolve().parents[2] / "data" / "exports"
JOB_KINDS = ("schema", "query", "export", "explain", "health")
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
    connector.status = "online"
    connector.last_seen_at = utcnow()
    db.commit()


# ---------------------------------------------------------------- jobs
def queue(db: Session, connector: Connector, kind: str, payload: dict | None = None, agent_id: int | None = None) -> ConnectorJob:
    if connector.status == "disconnected":
        raise DatabaseError("This connector is disconnected")
    if kind not in JOB_KINDS:
        raise DatabaseError("Unknown job")
    payload = dict(payload or {})
    if kind in ("query", "export", "explain"):
        payload["sql"] = read_only_sql(payload.get("sql", ""))
    payload["limit"] = min(int(payload.get("limit") or (PREVIEW_ROWS if kind == "query" else 100000)), 100000)
    job = ConnectorJob(connector_id=connector.id, agent_id=agent_id, kind=kind, payload=dumps(payload), status="queued")
    db.add(job)
    db.commit()
    if connector.kind in ("direct", "php"):
        from app.agents import direct

        direct.start(job.id)
        db.refresh(job)
    return job


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


def complete(db: Session, connector: Connector, job_id: int, data: dict) -> ConnectorJob:
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
    if job.kind == "export" and isinstance(csv_text, str):
        if len(csv_text.encode()) > MAX_RESULT_BYTES:
            job.status, job.error = "failed", "The export is bigger than 25 MB. Add a WHERE condition or fewer columns"
            db.commit()
            return job
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPORT_DIR / f"job_{job.id}_{secrets.token_hex(6)}.csv"
        path.write_text(csv_text, encoding="utf-8")
        job.file_path = str(path)
    raw = json.dumps(result, ensure_ascii=False, default=str)
    job.result = raw if len(raw) < 2_000_000 else json.dumps({"truncated": True})
    job.status = "done"
    db.commit()
    return job


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
DB_WORDS = re.compile(r"\b(database|databse|db|mysql|mariadb|postgres\w*|phpmyadmin)\b", re.I)
SQL_TEXT = re.compile(r"\bselect\b[\s\S]+?\bfrom\b", re.I)


def wants_database(goal: Goal | None, text: str) -> bool:
    """A database job the connector can do (not a pasted query to review, that's the SQL checker)."""
    if goal is None or SQL_TEXT.search(text or ""):
        return False
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
        "available": mode not in ("backup", "write"), "blocks": blocks(mode, hi),
        "connector": connector_dict(connector, db) if connector else None, "connectors": others,
        "runs": [], "owner": _owner(db, agent.visitor_id), "needs_owner": mode == "report",
    }


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
