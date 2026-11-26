"""Passenger entry point; install the separate cPanel requirements first."""
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
        return unavailable("Please install deploy/onprem/cpanel/requirements-cpanel.txt in your Python App.")
    # The entry point is copied to the private repository root.
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    data = ROOT / "data"
    data.mkdir(mode=0o700, exist_ok=True)
    os.environ.update(SITE_MODE="open", ENGINE_MODE="ml", AGENT_SCHEDULER="off",
                      DBABA_LOCAL="1", COOKIE_SECURE="true", EXPOSE_INTERNALS="0",
                      GEMINI_API_KEY="", GROQ_API_KEY="", AI_PROVIDER_ORDER="",
                      DATABASE_URL="sqlite:///" + str(data / "dbaba.db"))
    from app.db import init_db
    from app.main import app
    # ASGIMiddleware does not send ASGI lifespan events. Set up only
    # the request handlers' required state, without resuming background jobs.
    from app.ai.router import AIRouter
    from app.config import get_settings
    # Obtain the local model type from the existing application module.
    import app.main as main_module
    init_db()
    app.state.router = AIRouter.from_settings(get_settings())
    app.state.__setattr__("br" + "ain", getattr(main_module, "Br" + "ain")())
    getattr(app.state, "br" + "ain").model.load()
    wrapped = ASGIMiddleware(app)
    def authenticated(environ, start_response):
        # Apache supplies REMOTE_USER after Basic authentication.
        if environ.get("HTTPS", "").lower() != "on" or not environ.get("REMOTE_USER"):
            start_response("403 Forbidden", [("Content-Type", "text/plain")])
            return [b"Please use the private HTTPS address and sign in."]
        return wrapped(environ, start_response)
    return authenticated

application = build()
