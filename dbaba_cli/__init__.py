"""Command line for the local DBaba service."""
import argparse
from collections import deque
from datetime import date
from http.cookiejar import CookieJar
from importlib import metadata, util
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from urllib import request
from urllib.parse import urlsplit, quote, quote_plus
from .contract import commands_for, api_key
import zipfile

DATABASES = {"mysql": "MySQL", "mariadb": "MariaDB", "postgresql": "PostgreSQL"}
PRIVATE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
SENSITIVE = ("password", "pass", "secret", "token", "key", "hash", "salt")
DEFAULT_PORT = 8765


def version():
    try:
        return metadata.version("dbaba")
    except metadata.PackageNotFoundError:
        return "0.1.0"


def config_paths():
    return (Path.cwd() / "dbaba.json", Path.home() / "dbaba" / "dbaba.json",
            Path("/opt/dbaba/dbaba.json"))


def configuration():
    override = os.environ.get("DBABA_CONFIG")
    # An explicit path is authoritative: do not silently use another installation.
    paths = (Path(override).expanduser().resolve(),) if override else config_paths()
    for path in paths:
        if override or path.is_file():
            config = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise ValueError("Invalid configuration.")
            return path.parent, config
    return Path.cwd(), {}


def port_number(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return port


def base_url(config, allow_remote):
    return f"http://127.0.0.1:{port_number(config.get('port', DEFAULT_PORT))}"


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = request.build_opener(request.ProxyHandler({}),
                                          NoRedirect(), request.HTTPCookieProcessor(CookieJar()))
        self.headers = {"Content-Type": "application/json"}
        key = api_key()
        if key:
            self.headers["Authorization"] = "Bearer " + key

    def call(self, path, method="GET", payload=None, raw=False):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(self.base + path, data=body, headers=self.headers, method=method)
        with self.opener.open(req, timeout=120) as response:
            if raw:
                return response.read()
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError("Invalid API response.")
        return result

    def ask(self, text, database):
        self.call("/api/new-chat", "POST")
        result = self.call("/api/message", "POST", {"text": text})
        reply = result["reply"]
        if reply.startswith("Which database is this for?"):
            goal = result.get("goal") or {}
            if goal.get("id") is not None:
                followup = {"text": database, "goal_id": goal["id"]}
            else:
                followup = {"text": f"{database}: {text}"}
            result = self.call("/api/message", "POST", followup)
        return result["reply"]


def serve(root, port, config_path=None):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = root / "data"
    data.mkdir(mode=0o700, exist_ok=True)
    env = dict(os.environ, SITE_MODE="open", ENGINE_MODE="ml", AGENT_SCHEDULER="on",
               DBABA_LOCAL="1", UVICORN_HOST="127.0.0.1",
               DATABASE_URL="sqlite:///" + str(data / "dbaba.db"),
               COOKIE_SECURE="false", EXPOSE_INTERNALS="0", GEMINI_API_KEY="",
               GROQ_API_KEY="", AI_PROVIDER_ORDER="", PYTHONUNBUFFERED="1")
    if util.find_spec("app.local") is not None:
        cmd = [sys.executable, "-m", "app.local", "serve", "--port", str(port)]
        if config_path:
            cmd += ["--config", str(config_path)]
    elif config_path:
        print("Install a DBaba engine with local setup support.", file=sys.stderr)
        return 2
    else:
        print("Warning: this release's screen has no password protection yet; reach it only through an SSH tunnel.",
              file=sys.stderr)
        cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)]
    old = os.umask(0o077)
    try:
        if config_path:
            env["DBABA_CONFIG"] = str(config_path)
            os.chdir(root)
            os.execve(sys.executable, cmd, env)
        return subprocess.call(cmd, env=env, cwd=root)
    except KeyboardInterrupt:
        return 130
    finally:
        os.umask(old)


def sensitive(key):
    return any(word in key.lower() for word in SENSITIVE)


def secret_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from secret_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from secret_values(item)
    elif isinstance(value, str) and len(value) >= 8:
        yield value


def clean_config(value, secrets):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if sensitive(key):
                secrets.update(secret_values(item))
            else:
                result[key] = clean_config(item, secrets)
        return result
    if isinstance(value, list):
        return [clean_config(item, secrets) for item in value]
    return value


def redact(text, secrets):
    text = re.sub(r"(?i)(\bBearer\s+)\S+", r"\1***", text)
    text = re.sub(r"""(?i)(\b(?:password|pwd)\s*=\s*)(?:"[^"]*"|'[^']*'|[^\s&;,]+)""", r"\1***", text)
    text = re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:)[^@\s/]+@", r"\1***@", text)
    variants = {v for s in secrets if s for v in (s, quote(s, safe=""), quote_plus(s),
                                                   json.dumps(s, ensure_ascii=False)[1:-1])}
    if variants:
        text = re.sub("|".join(re.escape(s) for s in sorted(variants, key=len, reverse=True)), "***", text)
    return text


def safe_config(value, secrets):
    if isinstance(value, dict):
        return {redact(k, secrets): safe_config(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_config(v, secrets) for v in value]
    if isinstance(value, str):
        return redact(value, secrets)
    return value


def report(root, config):
    secrets = set()
    cleaned = clean_config(config, secrets)
    api_key = os.environ.get("DBABA_API_KEY", "")
    if len(api_key) >= 8:
        secrets.add(api_key)
    notes = ["DBaba " + version(), "Python " + platform.python_version(),
             "OS " + platform.system() + " " + platform.release()]
    for package in ("fastapi", "uvicorn", "SQLAlchemy"):
        try:
            notes.append(package + " " + metadata.version(package))
        except metadata.PackageNotFoundError:
            notes.append(package + " not installed")
    log = None
    try:
        for path in (root / "dbaba.log", root / "logs" / "dbaba.log", root / "data" / "dbaba.log"):
            if path.is_file():
                with path.open(encoding="utf-8") as stream:
                    log = "".join(deque(stream, maxlen=200))
                break
        if log is None and platform.system() == "Linux":
            for flags in (["--user"], []):
                try:
                    result = subprocess.run(["journalctl", *flags, "-u", "dbaba.service", "-n", "200",
                                             "--no-pager", "-o", "cat"], capture_output=True,
                                            text=True, timeout=10, check=False)
                    if result.returncode == 0 and result.stdout.strip() not in ("", "-- No entries --"):
                        log = "\n".join(result.stdout.splitlines()[-200:])
                        break
                except (OSError, subprocess.SubprocessError):
                    continue
        if log is not None:
            log = redact(log, secrets)
        else:
            notes.append("Log not included: no readable log found.")
    except (OSError, UnicodeError, ValueError, re.error):
        log = None
        notes.append("Log not included: could not read or safely sanitize it.")
    path = Path.cwd() / f"dbaba-report-{date.today().isoformat()}.zip"
    # Create a private new archive rather than following an existing symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream, zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("versions.txt", redact("\n".join(notes) + "\n", secrets))
            archive.writestr("dbaba.json", json.dumps(safe_config(cleaned, secrets), indent=2))
            if log is not None:
                archive.writestr("dbaba.log", log)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    print(path.name)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dbaba")
    parser.add_argument("--allow-remote", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "ask", "check", "serve", "report", "support-report", "alerts", "jobs", "version"):
        command = commands.add_parser(name)
        if name in ("status", "ask", "check"):
            command.add_argument("--allow-remote", action="store_true", default=argparse.SUPPRESS)
        if name == "ask":
            command.add_argument("question")
        if name == "check":
            command.add_argument("file")
            command.add_argument("--db", choices=DATABASES)
        if name == "serve":
            command.add_argument("--port", type=int)
            command.add_argument("--config", type=Path)
        if name == "alerts":
            command.add_argument("--ack")
        if name == "report":
            command.add_argument("--scan", action="store_true")
            command.add_argument("--html", type=Path)
        if name == "jobs":
            command.add_argument("id", nargs="?")
            switches = command.add_mutually_exclusive_group()
            switches.add_argument("--on", action="store_true")
            switches.add_argument("--off", action="store_true")
            command.add_argument("--at")
    args = parser.parse_args(argv)
    if args.command == "version":
        print(version())
        return 0
    try:
        if args.command == "serve" and args.config is not None:
            path = args.config.expanduser().resolve()
            config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            return serve(path.parent, port_number(args.port if args.port is not None else config.get("port", DEFAULT_PORT)), path)
        root, config = configuration()
        if args.command == "serve":
            return serve(root, port_number(args.port if args.port is not None else config.get("port", DEFAULT_PORT)))
        if args.command == "support-report":
            return report(root, config)
        client = Client(base_url(config, args.allow_remote or os.environ.get("DBABA_ALLOW_REMOTE") == "1"))
        if args.command in ("status", "alerts", "report", "jobs"):
            return commands_for(client, args)
        database = (config.get("database") or {}).get("type", "mysql")
        database = DATABASES.get(getattr(args, "db", None) or str(database).lower(), "MySQL")
        if args.command == "check":
            query = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
            text = f"{database}: {query}"
        else:
            text = args.question
        reply = client.ask(text, database)
        print(reply)
        if args.command == "check" and re.search(r"\bFixed\s+[1-9]\d*\s+mistake", reply, re.I):
            return 1
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        if args.command == "status":
            print("not running")
        else:
            print("Could not complete the command. Check the local configuration, input and service.", file=sys.stderr)
        return 2
