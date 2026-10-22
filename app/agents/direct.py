"""Direct database connection: the user types the details on Goal Engine and our server connects.

* Uses the same read-only code as the connector (connector/goal_connector.py): only SELECT/SHOW/EXPLAIN,
  read-only transactions, time limits.
* The password is stored encrypted (key from APP_SECRET) and only decrypted for the moment of a job.
* Only public addresses: nobody can use this form to reach databases on our own server or network.
* A few attempts per visitor, so the form can't be used to guess passwords.
"""

import base64
import hashlib
import importlib.util
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Connector, ConnectorJob, dumps, loads, utcnow

log = logging.getLogger(__name__)

_spec = importlib.util.spec_from_file_location("goal_connector", Path(__file__).resolve().parents[2] / "connector" / "goal_connector.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)

ATTEMPTS_PER_10_MIN = 8
_attempts: dict[str, list[float]] = {}
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(4)       # at most 4 database jobs at once on our server
_done_hooks = []


class DirectError(ValueError):
    pass


def on_done(fn):
    _done_hooks.append(fn)
    return fn


# ---------------------------------------------------------------- secrets
def _fernet():
    from cryptography.fernet import Fernet

    secret = os.getenv("APP_SECRET", "")
    if len(secret) < 16:
        raise DirectError("Direct database connections aren't switched on on this server yet (APP_SECRET missing)")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("db-credentials:" + secret).encode()).digest()))


def encrypt(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt(token: str) -> dict:
    from cryptography.fernet import InvalidToken

    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise DirectError("Saved database details can't be read any more. Connect it again") from exc


# ---------------------------------------------------------------- network safety
def server_ip() -> str:
    return os.getenv("SERVER_IP", "").strip()


def public_ip(host: str, port: int) -> str:
    """Resolve the host and refuse private, local or reserved addresses (checked again at every job)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise DirectError(f"Couldn't find the server '{host}'. Check the host name") from exc
    allow_private = os.getenv("DIRECT_DB_ALLOW_PRIVATE") == "1"
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not allow_private and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                                  or ip.is_unspecified or (ip.version == 6 and ip.ipv4_mapped and not ip.ipv4_mapped.is_global)):
            raise DirectError("That address is a private or local one, so Goal Engine can't reach it. "
                              "Use the database's public host name or IP (or the one-command connector on your server)")
        ips.append(ip)
    ips.sort(key=lambda ip: ip.version)          # IPv4 first
    return str(ips[0])


def _rate_limit(visitor_id: str) -> None:
    now = time.time()
    with _lock:
        recent = [t for t in _attempts.get(visitor_id, []) if now - t < 600]
        if len(recent) >= ATTEMPTS_PER_10_MIN:
            raise DirectError("Too many tries. Wait 10 minutes and check the details")
        recent.append(now)
        _attempts[visitor_id] = recent


def _open(details: dict):
    port = int(details.get("port") or (3306 if details["type"] == "mysql" else 5432))
    cfg = {**details, "host": public_ip(details["host"], port), "port": port}
    try:
        return gc.Database(cfg).connect()
    except SystemExit as exc:          # driver missing on our side
        raise DirectError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - turn driver errors into one plain sentence
        msg = str(exc)
        if "1045" in msg or "1698" in msg or "Access denied" in msg or "password authentication failed" in msg:
            raise DirectError("The database said no to this user/password") from exc
        if "1049" in msg or ("database" in msg and "does not exist" in msg):
            raise DirectError("No database with that name on this server") from exc
        if "1130" in msg or "is not allowed to connect" in msg or "no pg_hba.conf entry" in msg:
            ip = server_ip()
            raise DirectError("The database is blocking Goal Engine's server. Allow remote access for "
                              + (f"IP {ip}" if ip else "our server's IP") + " (cPanel: Remote MySQL), then try again") from exc
        if any(w in msg.lower() for w in ("timed out", "timeout", "can't connect", "connection refused", "2003", "could not connect")):
            ip = server_ip()
            raise DirectError(f"Couldn't reach {details['host']}:{port}. The database may only accept local connections. "
                              "On cPanel, add " + (f"IP {ip}" if ip else "our server's IP") + " under Remote MySQL. "
                              "On a VPS, use the one-command connector instead") from exc
        raise DirectError(f"Couldn't connect: {msg[:160]}") from exc


def _clean_form(form: dict) -> dict:
    kind = {"mariadb": "mysql", "postgresql": "postgres", "pgsql": "postgres"}.get((form.get("type") or "mysql").lower(),
                                                                                 (form.get("type") or "mysql").lower())
    if kind not in ("mysql", "postgres"):
        raise DirectError("MySQL, MariaDB or PostgreSQL only for now")
    host = (form.get("host") or "").strip()
    if host.startswith(("http://", "https://")):
        host = host.split("://", 1)[1]
    host = host.split("/")[0]
    port = str(form.get("port") or "").strip()
    if ":" in host and not port and host.count(":") == 1:
        host, port = host.split(":")
    if not port.isdigit() or not 0 < int(port) < 65536:
        port = "3306" if kind == "mysql" else "5432"
    details = {"type": kind, "host": host[:200], "port": port, "database": (form.get("database") or "").strip()[:120],
               "user": (form.get("user") or "").strip()[:120], "password": form.get("password") or ""}
    if not details["host"] or not details["database"] or not details["user"]:
        raise DirectError("Fill in the host, database name and user")
    return details


def connect(db: Session, visitor_id: str, form: dict, name: str = "") -> Connector:
    from app.agents import database as dbagent

    details = _clean_form(form)
    _rate_limit(visitor_id)
    secret = encrypt(details)                     # fails early if the server can't keep secrets
    conn = _open(details)
    try:
        version, can_write = conn.version(), conn.can_write()
    finally:
        _close(conn)
    connector, _ = dbagent.create_connector(db, visitor_id, name or details["database"])
    connector.kind, connector.secret = "direct", secret
    connector.token_hint = "direct"
    connector.db_type, connector.db_name, connector.version = details["type"], details["database"], version[:20]
    connector.host_label, connector.can_write = details["host"], bool(can_write)
    connector.status, connector.last_seen_at = "online", utcnow()
    db.commit()
    dbagent.queue(db, connector, "schema")
    return connector


def _close(conn) -> None:
    try:
        conn.conn.close()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- running jobs on our side
def start(job_id: int) -> None:
    if os.getenv("DIRECT_DB_SYNC") == "1":
        run_job(job_id)
    else:
        threading.Thread(target=run_job, args=(job_id,), daemon=True, name=f"db-job-{job_id}").start()


def run_job(job_id: int) -> None:
    from app.agents import database as dbagent
    from app.db import get_db

    gen = get_db()
    db = next(gen)
    try:
        job = db.get(ConnectorJob, job_id)
        connector = db.get(Connector, job.connector_id) if job else None
        if job is None or connector is None or job.status != "queued":
            return
        job.status, job.claimed_at = "claimed", utcnow()
        db.commit()
        with _slots:
            try:
                conn = _open(decrypt(connector.secret or ""))
                try:
                    data = gc.handle(conn, {"kind": job.kind, "payload": loads(job.payload, {})})
                finally:
                    _close(conn)
                csv_text = data.pop("csv", None)
                body = {"ok": True, "result": data, **({"csv": csv_text} if csv_text is not None else {})}
            except Exception as exc:  # noqa: BLE001 - every failure is reported on the job
                body = {"ok": False, "error": str(exc)[:300]}
        job = dbagent.complete(db, connector, job.id, body)
        for hook in _done_hooks:
            try:
                hook(db, job)
            except Exception:  # noqa: BLE001
                log.exception("Database job hook failed")
    except Exception:  # noqa: BLE001
        log.exception("Direct database job %s failed", job_id)
    finally:
        db.close()
