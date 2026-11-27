"""Private HTTPS Passenger entry point for the S4 screen."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def unavailable(message):
    def respond(environ, start_response):
        body = message.encode("utf-8")
        start_response("503 Service Unavailable", [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ])
        return [body]
    return respond


def build():
    if sys.version_info < (3, 11):
        return unavailable("Please select Python 3.11 or newer in Setup Python App.")
    try:
        from a2wsgi import ASGIMiddleware
    except ImportError:
        return unavailable("Please install deploy/onprem/cpanel/requirements-cpanel.txt.")
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from app.local.__main__ import _env
    config_path = ROOT / "dbaba.json"
    _env(config_path)
    # Never inherit the hosting account's unrelated working database.
    os.environ["DATABASE_URL"] = "sqlite:///" + str(ROOT / "data" / "dbaba.db")
    from app.local.config import Config, ConfigError
    from app.local.server import make_app
    try:
        if config_path.is_symlink():
            raise ConfigError("Settings must not be a symlink")
        cfg = Config(config_path)
        os.chmod(config_path, 0o600)
        port = int(cfg.data.get("port", 8765))
        if not 1024 <= port <= 65535:
            raise ValueError("Invalid port")
        if not cfg.has_password():
            return unavailable("Set the DBaba screen password in the private settings file, then restart the Python App.")
    except (OSError, ValueError, TypeError):
        return unavailable("Check the private dbaba.json settings and permissions.")
    # Request-driven only: do not start the S4 periodic scheduler.
    wrapped = ASGIMiddleware(make_app(str(config_path), port, setup_token=None,
                                      announce=lambda line: None))

    def authenticated(environ, start_response):
        if environ.get("HTTPS", "").lower() != "on" or not environ.get("REMOTE_USER"):
            start_response("403 Forbidden", [("Content-Type", "text/plain"),
                                             ("Cache-Control", "no-store")])
            return [b"Please use the private HTTPS address and sign in."]
        # Apache has verified HTTPS; ensure S4 issues a Secure session cookie.
        environ = dict(environ, **{"wsgi.url_scheme": "https"})
        return wrapped(environ, start_response)
    return authenticated


application = build()
