"""Run with python -m pytest deploy/onprem/cpanel/test_passenger.py."""
import importlib.util
import json
import stat
from pathlib import Path
from wsgiref.util import setup_testing_defaults
from io import BytesIO


def test_passenger(tmp_path, monkeypatch):
    from app.local.config import Config
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.path", __import__("sys").path.copy())
    for key in ("DATABASE_URL", "ENGINE_MODE", "OPENAI_API_KEY", "SITE_MODE",
                "EXPOSE_INTERNALS", "DBABA_LOCAL", "AGENT_SCHEDULER",
                "VISITOR_DAILY_CAP", "AI_DAILY_CAP", "COOKIE_SECURE",
                "DB_AREA_LOCK", "GEMINI_API_KEY", "GROQ_API_KEY",
                "AI_PROVIDER_ORDER", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test")
    source = Path(__file__).with_name("passenger_wsgi.py")
    target = tmp_path / source.name
    target.write_text(source.read_text())
    spec = importlib.util.spec_from_file_location("cpanel_test_entry", target)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path = tmp_path / "dbaba.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    cfg = Config(path)
    cfg.update(lambda d: d.update(screen_password=Config.password_record("test-password"),
                                  allowed_hosts=["dbaba.example.com"]))
    app = mod.build()

    def request(path, payload=None, cookie="", host="dbaba.example.com", https="on", user="dbaba"):
        env = {}
        setup_testing_defaults(env)
        body = json.dumps(payload).encode() if payload is not None else b""
        env.update(PATH_INFO=path, REQUEST_METHOD="POST" if payload is not None else "GET",
                   HTTP_HOST=host, HTTPS=https, REMOTE_USER=user, HTTP_COOKIE=cookie,
                   CONTENT_TYPE="application/json", CONTENT_LENGTH=str(len(body)))
        env["wsgi.input"] = BytesIO(body)
        result = []
        response = app(env, lambda status, headers, *exc: result.extend([status, dict(headers)]))
        try:
            data = b"".join(response)
        finally:
            if hasattr(response, "close"):
                response.close()
        return int(result[0].split()[0]), result[1], data

    assert request("/api/health", user="")[0] == 403
    assert request("/api/health", https="off")[0] == 403
    assert request("/api/health", host="other.example.com")[0] == 403
    assert json.loads(request("/api/health")[2])["ok"] is True
    assert request("/api/state")[0] == 401
    assert request("/api/setup/detect", {})[0] == 401
    assert request("/api/login", {"password": "wrong"})[0] == 401
    status, headers, _ = request("/api/login", {"password": "test-password"})
    assert status == 200
    cookie = next(v for k, v in headers.items() if k.lower() == "set-cookie")
    assert "secure" in cookie.lower() and "httponly" in cookie.lower()
    assert request("/api/state", cookie=cookie.split(";")[0])[0] == 200
    assert request("/api/setup/test", {}, cookie=cookie.split(";")[0])[0] == 200
    assert request("/admin")[0] == 404
    import os
    assert os.environ["ENGINE_MODE"] == "ml"
    assert os.environ["OPENAI_API_KEY"] == ""
