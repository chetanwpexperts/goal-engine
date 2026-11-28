"""DBaba's own screen and API when it runs on the person's server or laptop (S4; docs/dbaba-local-contract.md).

Never the whole website: no accounts, captcha, admin, enquiry or other agents. One FastAPI app with only these routes:

  GET  /                      the screen (log in, the 3-step setup, then Chat / Report / Automation)
  GET  /api/health            open: {"ok": true, "version"}
  POST /api/login | /api/logout   the screen password (dbaba.json "screen_password", scrypt) -> a session cookie
  GET  /api/state             setup, database, alerts, last scan
  POST /api/setup/detect | test | save | reset; POST /api/setup/ssh (an SSH server's key: see it, accept it)
  GET  /api/report, /api/report.html; POST /api/report/scan
  GET  /api/jobs; POST /api/jobs/{id}
  POST /api/alerts/{id}/ack
  POST /api/new-chat, /api/message   DBaba's own reasoning (no AI, nothing sent anywhere)

Who may call what:
  * /api/health and /api/login: anyone who can reach the port (bound to 127.0.0.1 unless --host says otherwise).
  * everything else: an API key (Authorization: Bearer, only its SHA-256 is stored) or the session cookie of a
    logged-in screen.
  * the setup calls, while setup isn't done: also the one-time setup token printed at start (DBABA-SETUP-TOKEN), but only
    from 127.0.0.1, so nobody else on the network can take over a fresh install. After setup they answer 409 until an
    authenticated /api/setup/reset.
Defences: the Host header must name this machine's loopback (or a name in dbaba.json "allowed_hosts"), so a web page
can't reach the screen by DNS rebinding; cookie-authenticated changes must be JSON (a cross-site form can't send it);
5 wrong passwords from one address lock the login for 10 minutes; security headers on every reply.
"""

import hmac
import os
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.products.dbaba.local import dbconn, tunnel
from app.products.dbaba.local.config import ENGINES, Config
from app.products.dbaba.local.jobs import Jobs
from app.security.crawlers import CrawlerMiddleware
from app.security.permissions import require
from app.security.audit import record_local

STATIC = Path(__file__).resolve().parents[3] / "static"
COOKIE = "dbaba_session"
SESSION_IDLE = 12 * 3600
LOGIN_TRIES, LOGIN_WINDOW = 5, 600
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
ID_RX = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TIME_RX = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def version() -> str:
    try:
        from importlib import metadata

        return metadata.version("dbaba")
    except Exception:  # noqa: BLE001 - running from a checkout
        return "0.1.0"


def err(status: int, text: str) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


def make_app(config_path: str, port: int = 8765, setup_token: str | None = None, announce=print) -> FastAPI:
    cfg = Config(config_path, port)
    jobs = Jobs(cfg)
    app = FastAPI(title="DBaba", docs_url=None, redoc_url=None, openapi_url=None)
    st = app.state
    st.cfg, st.jobs, st.sessions, st.fails = cfg, jobs, {}, {}
    st.token = setup_token if not cfg.setup_done else None
    st.chat = None
    st.announce = announce
    tunnel.manager.configure(cfg.data_dir / "known_hosts")     # DBaba's own list of accepted SSH server keys (S4b)

    # ---------------------------------------------------------------- who is asking
    def client_ip(req: Request) -> str:
        return req.client.host if req.client else ""

    def session_ok(req: Request) -> bool:
        tok = req.cookies.get(COOKIE)
        exp = st.sessions.get(tok) if tok else None
        if not exp or exp < time.time():
            st.sessions.pop(tok, None)
            return False
        st.sessions[tok] = time.time() + SESSION_IDLE
        return True

    def key_ok(req: Request) -> bool:
        auth = req.headers.get("authorization", "")
        return auth[:7].lower() == "bearer " and cfg.check_api_key(auth[7:].strip())

    def token_ok(req: Request) -> bool:
        t = req.headers.get("x-dbaba-setup", "")
        return bool(st.token and t and hmac.compare_digest(t, st.token) and client_ip(req) in LOOPBACK)

    def who(req: Request) -> str | None:
        if key_ok(req):
            return "key"
        if session_ok(req):
            return "session"
        if token_ok(req):
            return "token"
        return None

    def audit(req: Request, action: str, target: str, actor: str | None = None):
        actor = actor or {"key": "api_key", "session": "local_screen", "token": "local_setup"}.get(who(req), "anonymous")
        record_local(cfg.data_dir, actor, action, target, client_ip(req))

    # Route-specific credential alternatives preserve the original contract.
    def route_permission(req: Request, perm: str):
        path = req.url.path
        if perm == "local_setup":
            if path == "/api/state":
                return bool(need(req, "key", "session", "token"))
            if path == "/api/setup/ssh" and cfg.setup_done:
                return bool(need(req, "key", "session"))
            if path in ("/api/setup/detect", "/api/setup/test", "/api/setup/save", "/api/setup/ssh"):
                if cfg.setup_done:
                    # Preserve the original 409, even without credentials.
                    return True
                return bool(need(req, "session", "token"))
        if perm == "api_key" and path in (
            "/api/database", "/api/keys", "/api/setup/reset", "/api/report",
            "/api/report.html", "/api/report/scan", "/api/jobs", "/api/new-chat", "/api/message",
        ):
            return bool(need(req, "key", "session"))
        if perm == "api_key" and (path.startswith("/api/jobs/") or path.startswith("/api/alerts/")):
            return bool(need(req, "key", "session"))
        return None

    st.route_permission = route_permission

    class PermissionDenied(HTTPException):
        pass

    def permission_denied(req: Request, perm: str):
        path = req.url.path
        if perm == "local_screen":
            message = "Log in on the screen to revoke an API key." if path.endswith("/revoke") else "Log in on the screen to create an API key."
        elif perm == "local_setup" and path != "/api/state" and not (path == "/api/setup/ssh" and cfg.setup_done):
            message = "Log in, or use the setup token from 127.0.0.1."
        else:
            message = "Log in first."
        raise PermissionDenied(status_code=401, detail=message)

    @app.exception_handler(PermissionDenied)
    async def permission_error(req: Request, exc: PermissionDenied):
        return err(exc.status_code, exc.detail)

    st.permission_denied = permission_denied

    # Reuse the existing precedence and setup-token checks for declarations.
    st.permission_checks = {
        "api_key": lambda req: who(req) == "key",
        "local_screen": lambda req: who(req) == "session",
        "local_setup": lambda req: not cfg.setup_done and who(req) in ("session", "token"),
    }

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
        allowed = LOOPBACK | {h.lower() for h in (cfg.data.get("allowed_hosts") or []) if isinstance(h, str)}
        if host not in allowed and os.environ.get("DBABA_ALLOWED_HOSTS"):
            allowed |= {h.strip().lower() for h in os.environ["DBABA_ALLOWED_HOSTS"].split(",") if h.strip()}
        if host not in allowed:
            resp = err(403, "This address isn't allowed. Open DBaba as http://127.0.0.1:<port>, or add the name to "
                            "\"allowed_hosts\" in dbaba.json.")
        elif (request.method == "POST" and request.url.path not in ("/api/login",) and request.cookies.get(COOKIE)
              and not request.headers.get("authorization")
              and not (request.headers.get("content-type") or "").startswith("application/json")):
            resp = err(415, "Send JSON")                 # a cross-site form can't send JSON without asking first
        else:
            resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                                   "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return resp

    def need(req: Request, *kinds):
        k = who(req)
        return k if k in kinds else None

    async def body(req: Request) -> dict:
        try:
            data = await req.json()
        except Exception:  # noqa: BLE001
            return {}
        return data if isinstance(data, dict) else {}

    # ---------------------------------------------------------------- the screen
    @app.get("/", dependencies=[Depends(require("public"))])
    def screen():
        page = STATIC / "local.html"
        return FileResponse(page) if page.exists() else HTMLResponse("<p>DBaba is running.</p>")

    @app.get("/local/{name}", dependencies=[Depends(require("public"))])
    def asset(name: str):
        if name not in ("local.js", "local.css"):
            return err(404, "Not found")
        return FileResponse(STATIC / name, media_type="text/css" if name.endswith(".css") else "application/javascript")

    @app.get("/api/health", dependencies=[Depends(require("public"))])
    def health():
        return {"ok": True, "version": version()}

    @app.post("/api/login", dependencies=[Depends(require("public"))])
    async def login(req: Request):
        ip = client_ip(req)
        now = time.time()
        tries = [t for t in st.fails.get(ip, []) if now - t < LOGIN_WINDOW]
        st.fails[ip] = tries
        if len(tries) >= LOGIN_TRIES:
            return err(429, "Too many wrong passwords. Wait 10 minutes and try again.")
        if not cfg.has_password():
            return err(409, "No screen password is set yet. Finish setup first.")
        data = await body(req)
        pw = data.get("password") if isinstance(data.get("password"), str) else ""
        if len(pw) > 4096 or not cfg.check_password(pw):
            tries.append(now)
            if len(tries) == LOGIN_TRIES:
                audit(req, "login_lockout", "screen", "anonymous")
            return err(401, "That password isn't right.")
        st.fails.pop(ip, None)
        tok = secrets.token_urlsafe(32)
        st.sessions[tok] = now + SESSION_IDLE
        audit(req, "login", "screen", "local_screen")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, tok, httponly=True, samesite="strict", secure=req.url.scheme == "https", max_age=SESSION_IDLE)
        return resp

    @app.post("/api/logout", dependencies=[Depends(require("public"))])
    def logout(req: Request):
        if st.sessions.pop(req.cookies.get(COOKIE), None) is not None:
            audit(req, "logout", "screen", "local_screen")
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    # ---------------------------------------------------------------- state
    @app.get("/api/state", dependencies=[Depends(require("local_setup"))])
    def state(req: Request):
        if not need(req, "key", "session", "token"):
            return err(401, "Log in first.")
        db = cfg.database
        last = jobs.last_report()
        return {"version": version(), "setup_done": cfg.setup_done,
                "database": ({"engine": db["type"], "host": db.get("host"), "port": db.get("port"), "name": db.get("name"),
                              "connected": bool(jobs.connected), "via": dbconn.via(db),
                              "ssh": ({k: db["ssh"].get(k) for k in ("host", "port", "user")} if db.get("ssh") else None)}
                             if db else None),
                "alerts": jobs.public_alerts(), "last_scan": {"at": last["at"], "score": last["score"]} if last else None,
                "has_password": cfg.has_password()}

    # ---------------------------------------------------------------- setup
    def setup_gate(req: Request):
        if cfg.setup_done:
            return err(409, "Setup is already done. Reset it first (POST /api/setup/reset).")
        if not need(req, "session", "token"):
            return err(401, "Log in, or use the setup token from 127.0.0.1.")
        return None

    def database_from(data: dict) -> tuple[dict | None, str]:
        engine = data.get("engine")
        if engine not in ENGINES:
            return None, "Choose MySQL, MariaDB or PostgreSQL."
        try:
            port_ = int(data.get("port") or (5432 if engine == "postgresql" else 3306))
        except (TypeError, ValueError):
            port_ = 0
        if not 1 <= port_ <= 65535:
            return None, "The port must be a number from 1 to 65535."
        fields = {k: data.get(k) for k in ("host", "database", "user", "password")}
        if any(v is not None and (not isinstance(v, str) or len(v) > 4096 or "\0" in v) for v in fields.values()):
            return None, "One of the fields isn't valid text."
        if not fields["database"] or not fields["user"]:
            return None, "Give the database name and the user."
        out = {"type": engine, "host": (fields["host"] or "127.0.0.1").strip(), "port": port_,
               "name": fields["database"].strip(), "user": fields["user"].strip(), "password": fields["password"] or ""}
        if data.get("ssh"):                       # S4b: the database is on another server, reached through SSH from here
            ssh, why = tunnel.clean(data["ssh"])
            if ssh is None:
                return None, why
            if not re.match(r"^[A-Za-z0-9._:-]{1,253}$", out["host"]) or out["host"].startswith("-"):
                return None, "Give the database's address as the SSH server sees it (usually 127.0.0.1)."
            out["ssh"] = ssh
        return out, ""

    def ssh_from(data: dict):
        ssh, why = tunnel.clean(data.get("ssh"))
        return ssh, why

    @app.post("/api/setup/detect", dependencies=[Depends(require("local_setup"))])
    async def setup_detect(req: Request):
        gate = setup_gate(req)
        if gate:
            return gate
        data = await body(req)
        if data.get("ssh"):                       # look on the SSH server instead of this computer
            ssh, why = ssh_from(data)
            if ssh is None:
                return {"found": [], "reason": why}
            try:
                found = await run_in_threadpool(dbconn.detect_via_ssh, ssh)
            except dbconn.ConnectError as exc:
                out = {"found": [], "reason": str(exc), "code": exc.code}
                if exc.host_keys:
                    out["host_keys"] = [{"type": k["type"], "fingerprint": k["fingerprint"]} for k in exc.host_keys]
                return out
        else:
            found = dbconn.detect()
        return {"found": [{k: f[k] for k in ("engine", "host", "port", "how")} for f in found]}

    @app.post("/api/setup/ssh", dependencies=[Depends(require("local_setup"))])
    async def setup_ssh(req: Request):
        """An SSH server's host keys: {"ssh"} -> {"trusted", "keys": [{"type", "fingerprint"}]}; with "accept":
        "SHA256:..." the key with that fingerprint is accepted (scanned again now, never replacing a known key)."""
        if cfg.setup_done:
            if not need(req, "key", "session"):
                return err(401, "Log in first.")
        else:
            gate = setup_gate(req)
            if gate:
                return gate
        data = await body(req)
        ssh, why = ssh_from(data)
        if ssh is None:
            return {"ok": False, "reason": why}
        kh = cfg.data_dir / "known_hosts"
        try:
            if data.get("accept"):
                if not await run_in_threadpool(tunnel.trust, ssh, kh, data["accept"]):
                    return {"ok": False, "reason": "The server didn't offer a key with that fingerprint just now. Check it again."}
                audit(req, "settings_change", "settings")
                return {"ok": True, "trusted": True, "keys": []}
            if await run_in_threadpool(tunnel.trusted, ssh, kh):
                return {"ok": True, "trusted": True, "keys": []}
            keys = await run_in_threadpool(tunnel.host_keys, ssh)
        except tunnel.TunnelError as exc:
            return {"ok": False, "reason": str(exc)}
        if not keys:
            return {"ok": False, "reason": f"No SSH server answered at {ssh['host']} port {ssh['port']}."}
        return {"ok": True, "trusted": False, "keys": [{"type": k["type"], "fingerprint": k["fingerprint"]} for k in keys]}

    @app.post("/api/setup/test", dependencies=[Depends(require("local_setup"))])
    async def setup_test(req: Request):
        gate = setup_gate(req)
        if gate:
            return gate
        db, why = database_from(await body(req))
        if db is None:
            return {"ok": False, "version": "", "reason": why}
        return await run_in_threadpool(dbconn.test, db)

    @app.post("/api/setup/save", dependencies=[Depends(require("local_setup"))])
    async def setup_save(req: Request):
        gate = setup_gate(req)
        if gate:
            return gate
        kind = who(req)
        data = await body(req)
        db, why = database_from(data)
        if db is None:
            return {"ok": False, "reason": why}
        pw = data.get("screen_password")
        if pw is not None and (not isinstance(pw, str) or not 8 <= len(pw) <= 4096):
            return {"ok": False, "reason": "The screen password needs at least 8 characters."}
        if not pw and not cfg.has_password():
            return {"ok": False, "reason": "Choose a screen password (at least 8 characters)."}
        res = await run_in_threadpool(dbconn.test, db)
        if not res["ok"]:
            return {k: v for k, v in res.items() if k in ("ok", "reason", "code", "host_keys")}

        def apply(d):
            d["database"] = db
            if pw:
                d["screen_password"] = Config.password_record(pw)
            d["setup_done"] = True
        cfg.update(apply)
        key = cfg.new_api_key("desktop" if kind == "token" else "screen")
        actor = "local_setup" if kind == "token" else "local_screen"
        audit(req, "settings_change", "setup", actor)
        audit(req, "database_change", "database", actor)
        audit(req, "api_key_create", "api_key", actor)
        st.token = None
        jobs.connected = True
        threading.Thread(target=jobs.health, daemon=True, name="dbaba-first-health").start()
        jobs.scan_in_background()
        out = {"ok": True, "api_key": key}
        if res.get("warning"):
            out["warning"] = res["warning"]
        return out

    # ---------------------------------------------------------------- after setup: the database address, API keys
    @app.post("/api/database", dependencies=[Depends(require("api_key"))])
    async def change_database(req: Request):
        """Point DBaba at a new address (an SSH tunnel's new local port, a moved server) without setting up again: the
        new details are tested first and saved only if they work; a missing password keeps the saved one."""
        if not need(req, "key", "session"):
            return err(401, "Log in first.")
        if not cfg.setup_done or not cfg.database:
            return err(409, "Finish setup first.")
        data = await body(req)
        old = cfg.database
        merged = {"engine": data.get("engine") or old["type"], "host": data.get("host") or old.get("host"),
                  "port": data.get("port") or old.get("port"), "database": data.get("database") or old.get("name"),
                  "user": data.get("user") or old.get("user"),
                  "password": data["password"] if isinstance(data.get("password"), str) else old.get("password", ""),
                  "ssh": data["ssh"] if "ssh" in data else old.get("ssh")}     # "ssh": null -> reach it directly again
        db, why = database_from(merged)
        if db is None:
            return {"ok": False, "reason": why}
        res = await run_in_threadpool(dbconn.test, db)
        if not res["ok"]:
            return {k: v for k, v in res.items() if k in ("ok", "reason", "code", "host_keys")}

        def apply(d):
            new = {**(d.get("database") or {}), **db}
            if not db.get("ssh"):
                new.pop("ssh", None)
            d["database"] = new
        cfg.update(apply)
        audit(req, "database_change", "database")
        jobs.connected = True
        out = {"ok": True, "version": res["version"]}
        if res.get("warning"):
            out["warning"] = res["warning"]
        return out

    @app.get("/api/keys", dependencies=[Depends(require("api_key"))])
    def keys(req: Request):
        if not need(req, "key", "session"):
            return err(401, "Log in first.")
        return {"keys": cfg.list_api_keys()}

    @app.post("/api/keys", dependencies=[Depends(require("local_screen"))])
    async def new_key(req: Request):
        """Only a logged-in screen makes keys (a key can't make more keys). The key is shown once."""
        if need(req, "session") != "session":
            return err(401, "Log in on the screen to create an API key.")
        name = (await body(req)).get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[\w .-]{1,40}", name.strip()):
            return err(400, "Give the key a short name (letters, digits, space, . - _).")
        key = cfg.new_api_key(name.strip())
        audit(req, "api_key_create", "api_key")
        return {"api_key": key, "keys": cfg.list_api_keys()}

    @app.post("/api/keys/{kid}/revoke", dependencies=[Depends(require("local_screen"))])
    def revoke_key(kid: str, req: Request):
        if need(req, "session") != "session":
            return err(401, "Log in on the screen to revoke an API key.")
        if not re.fullmatch(r"[0-9a-f]{12}", kid) or not cfg.revoke_api_key(kid):
            return err(404, "No such key")
        audit(req, "api_key_revoke", "api_key")
        return {"ok": True, "keys": cfg.list_api_keys()}

    @app.post("/api/setup/reset", dependencies=[Depends(require("api_key"))])
    def setup_reset(req: Request):
        if not need(req, "key", "session"):
            return err(401, "Log in first.")
        cfg.update(lambda d: d.__setitem__("setup_done", False))
        audit(req, "settings_change", "setup")
        st.token = secrets.token_urlsafe(24)
        st.announce(f"DBABA-SETUP-TOKEN {st.token}")
        return {"ok": True}

    # ---------------------------------------------------------------- report
    @app.get("/api/report", dependencies=[Depends(require("api_key"))])
    def get_report(req: Request):
        if not need(req, "key", "session"):
            return err(401, "Log in first.")
        rep = jobs.last_report()
        if rep is None:
            return {"at": None, "score": None, "engine": (cfg.database or {}).get("type"), "sections": [],
                    "pending": bool(jobs.scanning)}
        return {**rep, "pending": bool(jobs.scanning)}

    @app.get("/api/report.html", dependencies=[Depends(require("api_key"))])
    def get_report_html(req: Request):
        if not need(req, "key", "session"):
            return err(401, "Log in first.")
        from app.products.dbaba.local.report import as_html

        rep = jobs.last_report() or {"at": None, "score": 0, "engine": None, "sections": []}
        return HTMLResponse(as_html(rep), headers={"Content-Disposition": 'attachment; filename="dbaba-report.html"'})

    @app.post("/api/report/scan", dependencies=[Depends(require("api_key"))])
    def start_scan(req: Request):
        if not need(req, "key", "session"):
            return err(401, "Log in first.")
        return {"started": jobs.scan_in_background()}

    # ---------------------------------------------------------------- jobs and alerts
    @app.get("/api/jobs", dependencies=[Depends(require("api_key"))])
    def list_jobs(req: Request):
        if not need(req, "key", "session"):
            return err(401, "Log in first.")
        return {"jobs": jobs.listing()}

    @app.post("/api/jobs/{jid}", dependencies=[Depends(require("api_key"))])
    async def change_job(jid: str, req: Request):
        if not need(req, "key", "session"):
            return err(401, "Log in first.")
        data = await body(req)
        if not ID_RX.match(jid):
            return err(404, "No such job")
        at = data.get("at")
        if not isinstance(data.get("enabled"), bool) or (at is not None and not (isinstance(at, str) and TIME_RX.match(at))):
            return err(400, "Send {\"enabled\": true|false, \"at\": \"HH:MM\" or null}")
        try:
            result = jobs.change(jid, data["enabled"], at)
            audit(req, "settings_change", "jobs")
            return result
        except KeyError:
            return err(404, "No such job")

    @app.post("/api/alerts/{aid}/ack", dependencies=[Depends(require("api_key"))])
    def ack(aid: str, req: Request):
        if not need(req, "key", "session"):
            return err(401, "Log in first.")
        if not ID_RX.match(aid) or not jobs.ack(aid):
            return err(404, "No such alert")
        return {"ok": True}

    # ---------------------------------------------------------------- chat: DBaba's own reasoning
    def chat_engine():
        if st.chat is None:
            from app.ai.router import AIRouter
            from app.core.config import get_settings
            from app.core.db import init_db
            from app.engine.ml.brain import Brain

            init_db()
            brain = Brain()
            brain.model.load()
            st.chat = {"router": AIRouter.from_settings(get_settings()), "brain": brain, "lock": threading.Lock()}
        return st.chat

    def visitor() -> str:
        path = cfg.data_dir / "chat_id"
        try:
            v = path.read_text().strip()
        except OSError:
            v = ""
        if not re.fullmatch(r"local-[A-Za-z0-9_-]{16,}", v):
            v = "local-" + secrets.token_urlsafe(18)
            path.write_text(v)
            os.chmod(path, 0o600)
        return v

    @app.post("/api/new-chat", dependencies=[Depends(require("api_key"))])
    def new_chat(req: Request):
        if not need(req, "key", "session"):
            return err(401, "Log in first.")
        path = cfg.data_dir / "chat_id"
        path.write_text("local-" + secrets.token_urlsafe(18))
        os.chmod(path, 0o600)
        return {"ok": True}

    @app.post("/api/message", dependencies=[Depends(require("api_key"))])
    async def message(req: Request):
        if not need(req, "key", "session"):
            return err(401, "Log in first.")
        data = await body(req)
        text = data.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            return err(400, "Write a message (up to 10,000 characters).")
        from starlette.concurrency import run_in_threadpool

        def run():
            from app.core.config import get_settings
            from app.core.db import get_db
            from app.engine.goal import InvalidMessage
            # known debt (tests/architecture_baseline.json): the on-server app has no startup of its own to hand the chat in
            from app.services.goals import handle_message

            ce = chat_engine()
            gen = get_db()
            db = next(gen)
            try:
                with ce["lock"]:
                    res = handle_message(db, visitor(), text, ce["router"], get_settings(), brain=ce["brain"])
                return {"reply": res.get("reply", ""), "relation": res.get("relation")}
            except InvalidMessage as exc:
                return {"error": str(exc)}
            except Exception:  # noqa: BLE001 - one plain sentence, never a stack trace
                import logging

                logging.getLogger("dbaba.local").exception("chat failed")
                return {"error": "DBaba couldn't answer that just now. Please try again."}
            finally:
                gen.close()
        out = await run_in_threadpool(run)
        return err(400, out["error"]) if "error" in out else out

    app.add_middleware(CrawlerMiddleware)
    return app
