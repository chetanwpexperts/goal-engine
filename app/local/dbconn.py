"""Finding and reaching the database DBaba looks after, read-only (the connector's own driver code).

* detect(): which databases listen on this machine. It sends no password and runs no SQL: a MySQL / MariaDB server
  greets every new connection with its version (that greeting says "MariaDB" for MariaDB), and a PostgreSQL server
  answers a TLS question with one byte ('S' or 'N'). Socket files are checked too.
* test(): connect with the details given and say in plain words why it failed.
* open_db(): a read-only connection for the report and the checks (goal_connector: read-only transactions, time limits).
* A database with an "ssh" part is reached through an SSH tunnel from this computer (app/local/tunnel.py, S4b); its
  "host" and "port" are then the database's address as the SSH server sees it (usually 127.0.0.1).
"""

import contextlib
import importlib.util
import os
import socket
import struct
from pathlib import Path

from app.local import tunnel

_spec = importlib.util.spec_from_file_location("goal_connector", Path(__file__).resolve().parents[2] / "connector" / "goal_connector.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)

SOCKETS = {"mysql": ("/run/mysqld/mysqld.sock", "/var/run/mysqld/mysqld.sock", "/var/lib/mysql/mysql.sock", "/tmp/mysql.sock"),
           "postgresql": ("/run/postgresql/.s.PGSQL.5432", "/var/run/postgresql/.s.PGSQL.5432", "/tmp/.s.PGSQL.5432")}


def _mysql_greeting(host: str, port: int, timeout: float = 1.0) -> str | None:
    """The version a MySQL / MariaDB server announces on connect, or None when nothing of that kind answers."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            head = s.recv(4)
            if len(head) < 4:
                return None
            n = head[0] | head[1] << 8 | head[2] << 16
            body = b""
            while len(body) < min(n, 512):
                part = s.recv(min(n, 512) - len(body))
                if not part:
                    break
                body += part
    except OSError:
        return None
    if not body or body[0] != 10:                     # protocol 10: then the version, ending with a zero byte
        return None
    return body[1:body.find(b"\0", 1)].decode("ascii", "replace")


def _postgres_answers(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(struct.pack("!ii", 8, 80877103))       # SSLRequest: a PostgreSQL server replies b'S' or b'N'
            return s.recv(1) in (b"S", b"N")
    except OSError:
        return False


def detect(hosts=("127.0.0.1",)) -> list[dict]:
    found = []
    if os.path.exists("/.dockerenv"):
        hosts = tuple(hosts) + ("host.docker.internal",)
    for host in hosts:
        version = _mysql_greeting(host, 3306)
        if version is not None:
            engine = "mariadb" if "mariadb" in version.lower() else "mysql"
            if version.startswith("5.5.5-"):              # MariaDB's old compatibility prefix (lab: '5.5.5-10.11.14-MariaDB')
                version = version[6:]
            found.append({"engine": engine, "host": host, "port": 3306, "version": version,
                          "how": f"{'MariaDB' if engine == 'mariadb' else 'MySQL'} {version.split('-')[0]} answers on {host}:3306"})
        if _postgres_answers(host, 5432):
            found.append({"engine": "postgresql", "host": host, "port": 5432, "version": "",
                          "how": f"PostgreSQL answers on {host}:5432"})
    for engine, paths in SOCKETS.items():
        for p in paths:
            try:
                is_sock = Path(p).is_socket()
            except OSError:
                is_sock = False
            if is_sock and not any(f["engine"] in ((engine,) if engine == "postgresql" else ("mysql", "mariadb")) for f in found):
                found.append({"engine": engine, "host": "127.0.0.1", "port": 5432 if engine == "postgresql" else 3306,
                              "version": "", "how": f"a {'PostgreSQL' if engine == 'postgresql' else 'MySQL / MariaDB'} socket file "
                                                    f"is at {p}; it may listen only on that file"})
    return found


def driver_cfg(db: dict) -> dict:
    return {"type": "postgres" if db["type"] == "postgresql" else "mysql", "host": db.get("host") or "127.0.0.1",
            "port": int(db.get("port") or (5432 if db["type"] == "postgresql" else 3306)),
            "user": db.get("user") or "", "password": db.get("password") or "", "database": db.get("name") or ""}


def default_port(engine: str) -> int:
    return 5432 if engine == "postgresql" else 3306


def via(db: dict) -> str:
    """Where the database is from DBaba's point of view: 'here' (this machine), 'ssh' (through a tunnel), 'network'."""
    if db.get("ssh"):
        return "ssh"
    return "here" if (db.get("host") or "127.0.0.1") in ("127.0.0.1", "localhost", "::1") else "network"


def open_db(db: dict):
    cfg = driver_cfg(db)
    ssh = None
    if db.get("ssh"):
        ssh, why = tunnel.clean(db["ssh"])
        if ssh is None:
            raise ConnectError(why)
        try:
            cfg["host"], cfg["port"] = tunnel.manager.local_end(ssh, cfg["host"], cfg["port"])
        except tunnel.TunnelError as exc:
            raise ConnectError(str(exc), exc.code, exc.host_keys) from exc
    try:
        return gc.Database(cfg).connect()
    except SystemExit as exc:                          # the driver isn't installed
        raise ConnectError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - one plain sentence for every driver error
        if ssh:
            said = tunnel.manager.why_failed(ssh, driver_cfg(db)["host"], driver_cfg(db)["port"])
            if said:
                raise ConnectError(said[0], said[1]) from exc
        raise ConnectError(reason(exc, db)) from exc


class ConnectError(ValueError):
    def __init__(self, text: str, code: str = "", host_keys: list | None = None):
        super().__init__(text)
        self.code = code
        self.host_keys = host_keys or []


def detect_via_ssh(ssh: dict) -> list[dict]:
    """"Find it for me" on the SSH server: the same greetings as detect(), sent through a short-lived tunnel."""
    found = []
    try:
        ends = tunnel.manager.detect(ssh)
    except tunnel.TunnelError as exc:
        raise ConnectError(str(exc), exc.code, exc.host_keys) from exc
    try:
        for host, port, lp in ends:
            if port == 3306:
                version = _mysql_greeting("127.0.0.1", lp, timeout=3.0)
                if version is not None:
                    engine = "mariadb" if "mariadb" in version.lower() else "mysql"
                    if version.startswith("5.5.5-"):
                        version = version[6:]
                    found.append({"engine": engine, "host": host, "port": port, "version": version,
                                  "how": f"{'MariaDB' if engine == 'mariadb' else 'MySQL'} {version.split('-')[0]} answers on "
                                         f"{tunnel.where(ssh)} ({host}:{port} there)"})
            elif _postgres_answers("127.0.0.1", lp, timeout=3.0):
                found.append({"engine": "postgresql", "host": host, "port": port, "version": "",
                              "how": f"PostgreSQL answers on {tunnel.where(ssh)} ({host}:{port} there)"})
    finally:
        with contextlib.suppress(Exception):
            tunnel.manager.end_detect()
    return found


def reason(exc: Exception, db: dict) -> str:
    msg = str(exc)
    low = msg.lower()
    where = f"{db.get('host') or '127.0.0.1'}:{db.get('port')}"
    if "1044" in msg or "permission denied for database" in low:
        # lab (MariaDB 10.11): a database that doesn't exist gives this user 1044 too; the server won't say which
        return (f"This user can't open a database named '{db.get('name')}' on {where}: either it doesn't exist (check the name) "
                "or the user has no rights on it (give it SELECT).")
    if "1045" in msg or "1698" in msg or "access denied" in low or "password authentication failed" in low:
        return "The database said no to this user and password. Check both (they are case-sensitive)."
    if "1049" in msg or ("database" in low and "does not exist" in low):
        return f"There is no database named '{db.get('name')}' on {where}."
    if "1130" in msg or "is not allowed to connect" in low or "no pg_hba.conf entry" in low:
        return (f"The database at {where} doesn't accept this user from DBaba's address. Allow it for that address "
                "(MySQL / MariaDB: a user@host grant; PostgreSQL: a line in pg_hba.conf).")
    if any(w in low for w in ("connection refused", "2003", "could not connect", "can't connect", "timed out", "timeout",
                              "name or service not known", "nodename nor servname", "no route")):
        return f"Nothing answered at {where}. Check the host and port, and that the database is running."
    return "Couldn't connect with these details. Check the host, port, database name, user and password."


def test(db: dict) -> dict:
    try:
        conn = open_db(db)
    except ConnectError as exc:
        out = {"ok": False, "version": "", "reason": str(exc)}
        if exc.code:
            out["code"] = exc.code
        if exc.host_keys:
            out["host_keys"] = [{"type": k["type"], "fingerprint": k["fingerprint"]} for k in exc.host_keys]
        return out
    try:
        version = conn.version()
        writes = conn.can_write()
    finally:
        close(conn)
    out = {"ok": True, "version": version, "reason": ""}
    if writes:
        out["warning"] = ("This user can change data. DBaba only reads, but a read-only user is safer: "
                          "create one with SELECT rights only.")
    return out


def close(conn) -> None:
    try:
        conn.conn.close()
    except Exception:  # noqa: BLE001
        pass
