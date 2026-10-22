"""Privacy rules, enforced in code (and covered by tests in tests/test_privacy.py).

1. Data read from a user's database is not kept: previews and query results are wiped
   15 minutes after they're made, Excel files 1 hour after. Only table names and row counts stay.
2. Whatever has to be kept to do the work is encrypted: database passwords, pasted customer lists.
3. Customer details inside agent run results (names, emails, message text) are wiped after 24 hours;
   the "already emailed" log keeps only a one-way fingerprint of each address, and is deleted after 30 days.
4. "Delete all my data" removes everything for that visitor, at once.
   Live views (running queries) are deleted 2 minutes after they're shown; the activity log keeps no data values and is kept 30 days.
5. Visitors inactive for 90 days (and with no agent switched on) are deleted automatically.
6. Saved database passwords not used for 30 days are deleted.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (Agent, AgentRun, Connector, ConnectorJob, Goal, Message, OwnerContact, SentEmail, UsageCounter,
                        dumps, loads, utcnow)

DB_RESULT_TTL = timedelta(minutes=15)
LIVE_TTL = timedelta(minutes=2)
ACTIVITY_TTL = timedelta(days=30)
EXPORT_TTL = timedelta(hours=1)
RUN_DETAIL_TTL = timedelta(hours=24)
SENT_LOG_TTL = timedelta(days=30)
INACTIVE_TTL = timedelta(days=90)
PASSWORD_UNUSED_TTL = timedelta(days=30)
KEY_FILE = Path(__file__).resolve().parents[1] / "data" / ".secret_key"


# ---------------------------------------------------------------- encryption
def _key_material() -> bytes:
    secret = os.getenv("APP_SECRET", "")
    if len(secret) >= 16:
        return secret.encode()
    # No APP_SECRET: keep a random key on disk, readable only by the app
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(48))
    return KEY_FILE.read_text().strip().encode()


def _fernet(purpose: str):
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(purpose.encode() + b":" + _key_material()).digest()))


def encrypt(data, purpose: str = "data") -> str:
    return _fernet(purpose).encrypt(json.dumps(data).encode()).decode()


def decrypt(token: str, purpose: str = "data"):
    return json.loads(_fernet(purpose).decrypt(token.encode()))


def fingerprint(value: str) -> str:
    """One-way: lets us say 'already emailed today' without keeping the address."""
    return "h:" + hmac.new(_key_material(), value.strip().lower().encode(), hashlib.sha256).hexdigest()[:40]


# ---------------------------------------------------------------- pasted customer lists (encrypted at rest)
def set_rows(inputs: dict, text: str) -> None:
    inputs.pop("csv_text", None)
    inputs["csv_enc"] = encrypt(text, "rows") if text else ""


def get_rows(inputs: dict) -> str:
    if inputs.get("csv_enc"):
        try:
            return decrypt(inputs["csv_enc"], "rows")
        except Exception:  # noqa: BLE001 - key changed: treat as not added
            return ""
    return inputs.get("csv_text", "")    # rows saved before encryption


# ---------------------------------------------------------------- delete everything for one visitor
def delete_visitor(db: Session, visitor_id: str) -> dict:
    counts = {}
    connector_ids = list(db.scalars(select(Connector.id).where(Connector.visitor_id == visitor_id)))
    if connector_ids:
        for path in db.scalars(select(ConnectorJob.file_path).where(ConnectorJob.connector_id.in_(connector_ids),
                                                                    ConnectorJob.file_path.is_not(None))):
            _remove(path)
        counts["database_jobs"] = db.execute(delete(ConnectorJob).where(ConnectorJob.connector_id.in_(connector_ids))).rowcount
    counts["connections"] = db.execute(delete(Connector).where(Connector.visitor_id == visitor_id)).rowcount
    agent_ids = list(db.scalars(select(Agent.id).where(Agent.visitor_id == visitor_id)))
    if agent_ids:
        counts["agent_runs"] = db.execute(delete(AgentRun).where(AgentRun.agent_id.in_(agent_ids))).rowcount
    counts["agents"] = db.execute(delete(Agent).where(Agent.visitor_id == visitor_id)).rowcount
    counts["email_log"] = db.execute(delete(SentEmail).where(SentEmail.visitor_id == visitor_id)).rowcount
    counts["email"] = db.execute(delete(OwnerContact).where(OwnerContact.visitor_id == visitor_id)).rowcount
    counts["messages"] = db.execute(delete(Message).where(Message.visitor_id == visitor_id)).rowcount
    counts["goals"] = db.execute(delete(Goal).where(Goal.visitor_id == visitor_id)).rowcount
    db.execute(delete(UsageCounter).where(UsageCounter.scope == f"visitor:{visitor_id}"))
    db.commit()
    return counts


def _remove(path) -> None:
    try:
        if path:
            os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------- automatic clean-up (runs from the scheduler)
PERSONAL_RUN_KEYS = ("messages", "sent_to", "skipped", "report", "failed", "rows", "mapping")


def maintain(db: Session, now=None) -> dict:
    now = now or utcnow()
    done = {"db_results": 0, "exports": 0, "runs": 0, "sent_log": 0, "passwords": 0, "visitors": 0}

    # 1. results read from users' databases
    for job in db.scalars(select(ConnectorJob).where(ConnectorJob.kind.in_(("query", "export", "explain")),
                                                     ConnectorJob.finished_at.is_not(None),
                                                     ConnectorJob.finished_at < now - DB_RESULT_TTL,
                                                     ConnectorJob.result.is_not(None))):
        if job.kind == "export" and job.file_path and job.finished_at >= now - EXPORT_TTL:
            continue          # file still downloadable; its row data is cleared below when the file goes
        result = loads(job.result, {}) or {}
        if result.get("removed"):
            continue
        job.result = dumps({"row_count": result.get("row_count"), "removed": True})
        job.payload = dumps({"removed": True})
        if job.file_path:
            _remove(job.file_path)
            job.file_path = None
            done["exports"] += 1
        done["db_results"] += 1

    # 1b. live view snapshots (running queries etc.) are only for the moment they're shown
    for job in db.scalars(select(ConnectorJob).where(ConnectorJob.kind == "live", ConnectorJob.finished_at.is_not(None),
                                                     ConnectorJob.finished_at < now - LIVE_TTL)):
        db.delete(job)
        done["db_results"] += 1
    # 1c. the activity log itself (no data values in it) is kept 30 days
    old_jobs = list(db.scalars(select(ConnectorJob).where(ConnectorJob.created_at < now - ACTIVITY_TTL)))
    for job in old_jobs:
        _remove(job.file_path)
        db.delete(job)

    # 2. customer details inside agent runs
    for run in db.scalars(select(AgentRun).where(AgentRun.finished_at.is_not(None), AgentRun.finished_at < now - RUN_DETAIL_TTL,
                                                 AgentRun.result.is_not(None))):
        result = loads(run.result, {}) or {}
        if any(k in result for k in PERSONAL_RUN_KEYS):
            for k in PERSONAL_RUN_KEYS:
                if isinstance(result.get(k), list):
                    result[k + "_count"] = len(result[k])
                result.pop(k, None)
            result["details_removed"] = True
            run.result = dumps(result)
            done["runs"] += 1

    # 3. "already emailed" log
    done["sent_log"] = db.execute(delete(SentEmail).where(SentEmail.sent_at < now - SENT_LOG_TTL)).rowcount

    # 4. saved database passwords that nobody used for a month
    for c in db.scalars(select(Connector).where(Connector.secret.is_not(None),
                                                func.coalesce(Connector.last_seen_at, Connector.created_at) < now - PASSWORD_UNUSED_TTL)):
        c.secret, c.status = None, "disconnected"
        done["passwords"] += 1
    db.commit()

    # 5. inactive visitors with nothing switched on
    last_seen = {}
    for vid, at in db.execute(select(Message.visitor_id, func.max(Message.created_at)).group_by(Message.visitor_id)):
        last_seen[vid] = at
    for vid, at in db.execute(select(Goal.visitor_id, func.max(Goal.updated_at)).group_by(Goal.visitor_id)):
        if at and (vid not in last_seen or at > last_seen[vid]):
            last_seen[vid] = at
    busy = set(db.scalars(select(Agent.visitor_id).where(Agent.status == "active")))
    for vid, at in last_seen.items():
        if at and at < now - INACTIVE_TTL and vid not in busy:
            delete_visitor(db, vid)
            done["visitors"] += 1
    return done
