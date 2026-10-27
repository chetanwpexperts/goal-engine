"""Direct database connection: the user types the details on Goal Engine and our server connects.

* Uses the same read-only code as the connector (connector/goal_connector.py): only SELECT/SHOW/EXPLAIN,
  read-only transactions, time limits.
* The password is stored encrypted (key from APP_SECRET) and only decrypted for the moment of a job.
* Only public addresses: nobody can use this form to reach databases on our own server or network.
* A few attempts per visitor, so the form can't be used to guess passwords.
"""

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

ATTEMPTS_PER_10_MIN = 8          # per browser
IP_ATTEMPTS_PER_10_MIN = 12      # per network address: a new cookie doesn't give new tries
TARGET_ATTEMPTS_PER_10_MIN = 10  # per database server, from everyone together: no password guessing through us
ALL_ATTEMPTS_PER_10_MIN = 120    # the whole form, so it can't be used to scan the internet
MIN_PORT = 1024                  # databases listen above 1024; below are SSH, mail, web... never ours to touch
_attempts: dict[str, list[float]] = {}
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(4)       # at most 4 database jobs at once on our server
_done_hooks = []


class DirectError(ValueError):
    def __init__(self, message: str, use_command: bool = False):
        super().__init__(message)
        self.use_command = use_command


def on_done(fn):
    _done_hooks.append(fn)
    return fn


# ---------------------------------------------------------------- secrets (see app/privacy.py)
def encrypt(data: dict) -> str:
    from app import privacy

    return privacy.encrypt(data, "db-credentials")


def decrypt(token: str) -> dict:
    from app import privacy

    try:
        return privacy.decrypt(token, "db-credentials")
    except Exception as exc:  # noqa: BLE001
        raise DirectError("Saved database details can't be read any more. Connect it again") from exc


# ---------------------------------------------------------------- network safety
def server_ip() -> str:
    return os.getenv("SERVER_IP", "").strip()


def _own_addresses() -> set[str]:
    """This server's own addresses: the form must never reach databases on our own machine."""
    own = {a.strip() for a in os.getenv("SERVER_IP", "").split(",") if a.strip()}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            own.add(info[4][0])
    except OSError:
        pass
    try:
        import subprocess

        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3).stdout
        own.update(out.split())
    except (OSError, subprocess.SubprocessError):
        pass
    return own


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
        if not allow_private and (ip.is_loopback or ip.is_unspecified):
            raise DirectError(f"'{host}' means \"this same server\", so your database only works inside your own server. "
                              "Use the one-command option below: it connects from inside, and your password stays there",
                              use_command=True)
        if not allow_private and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                                  or ip.is_unspecified or (ip.version == 6 and ip.ipv4_mapped and not ip.ipv4_mapped.is_global)):
            raise DirectError("That's a private network address, so Goal Engine can't reach it from outside. "
                              "Use the one-command option below instead", use_command=True)
        if not allow_private and str(ip) in _own_addresses():
            raise DirectError("That's Goal Engine's own server, so it can't be connected this way. Use the one-command option below",
                              use_command=True)
        ips.append(ip)
    ips.sort(key=lambda ip: ip.version)          # IPv4 first
    return str(ips[0])


def _rate_limit(visitor_id: str, client_ip: str = "", target: str = "") -> None:
    """Tries are counted per browser, per network address, per database server and in total."""
    now = time.time()
    keys = [(f"v:{visitor_id}", ATTEMPTS_PER_10_MIN), ("all", ALL_ATTEMPTS_PER_10_MIN)]
    if client_ip:
        keys.append((f"ip:{client_ip}", IP_ATTEMPTS_PER_10_MIN))
    if target:
        keys.append((f"t:{target.lower()}", TARGET_ATTEMPTS_PER_10_MIN))
    with _lock:
        for key, limit in keys:
            recent = [t for t in _attempts.get(key, []) if now - t < 600]
            _attempts[key] = recent
            if len(recent) >= limit:
                log.warning("direct connect limit hit: %s", key.split(":")[0])
                raise DirectError("Too many tries. Wait 10 minutes and check the details")
        for key, _ in keys:
            _attempts[key].append(now)


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
                              "On a VPS, use the one-command option below", use_command=True) from exc
        log.info("direct connect failed: %s", msg[:300])
        raise DirectError("Couldn't connect to that database. Check the host, port, database name and user") from exc


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
    if int(port) < MIN_PORT:
        raise DirectError("Databases use ports above 1024 (MySQL 3306, PostgreSQL 5432). Check the port")
    details = {"type": kind, "host": host[:200], "port": port, "database": (form.get("database") or "").strip()[:120],
               "user": (form.get("user") or "").strip()[:120], "password": form.get("password") or ""}
    if not details["host"] or not details["database"] or not details["user"]:
        raise DirectError("Fill in the host, database name and user")
    return details


def connect(db: Session, visitor_id: str, form: dict, name: str = "", allow_changes: bool = False, client_ip: str = "") -> Connector:
    from app.agents import database as dbagent

    details = _clean_form(form)
    _rate_limit(visitor_id, client_ip, details["host"])
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
    if allow_changes and not can_write:
        raise DirectError("You asked for approved changes, but this database user can only read. Use a login that can change data, or leave changes off")
    connector.host_label, connector.can_write = details["host"], bool(can_write)
    connector.allow_changes = bool(allow_changes and can_write)
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


def _write_job(db: Session, connector: Connector, job: ConnectorJob) -> dict:
    """Approved change or its undo, for a database connected with typed details. The undo copy is kept encrypted."""
    from app import privacy

    payload = loads(job.payload, {}) or {}
    conn = _open({**decrypt(connector.secret or ""), "writer": True})
    try:
        if job.kind == "change":
            record, info = conn.apply_change(payload.get("sql", ""), payload.get("expected"))
            job.undo_blob = privacy.encrypt(record, "undo")
            return {**info, "undo": {"available": True, "where": "Goal Engine, encrypted"}}
        change = db.get(ConnectorJob, payload.get("change_job_id") or 0)
        if change is None or not change.undo_blob:
            raise ValueError("The saved copy for this change is gone, so it can't be undone")
        return conn.undo_change(privacy.decrypt(change.undo_blob, "undo"), force=bool(payload.get("force")))
    finally:
        _close(conn)


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
            path = dbagent.save_upload_path(job) if job.kind == "export" else None   # exports go straight to disk
            try:
                if connector.kind == "php":
                    from app.agents import phpconn

                    data = phpconn.run(connector, job.kind, loads(job.payload, {}), export_path=path, job_id=job.id)
                elif job.kind in ("change", "undo"):
                    data = _write_job(db, connector, job)
                else:
                    conn = _open(decrypt(connector.secret or ""))
                    try:
                        data = gc.handle(conn, {"kind": job.kind, "payload": loads(job.payload, {})}, export_path=path)
                    finally:
                        _close(conn)
                body = {"ok": True, "result": data}
            except Exception as exc:  # noqa: BLE001 - every failure is reported on the job
                prefix = "CONFLICT: " if isinstance(exc, gc.ChangeConflict) else ""
                body = {"ok": False, "error": (prefix + str(exc))[:300]}
                if getattr(exc, "missing", False):
                    connector.status = "missing"      # the card will say the file needs uploading again
        if not body.get("ok") and path is not None:
            path.unlink(missing_ok=True)
            path = None
        job = dbagent.complete(db, connector, job.id, body, file_path=path)
        for hook in _done_hooks:
            try:
                hook(db, job)
            except Exception:  # noqa: BLE001
                log.exception("Database job hook failed")
    except Exception:  # noqa: BLE001
        log.exception("Direct database job %s failed", job_id)
    finally:
        db.close()
