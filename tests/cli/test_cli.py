import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import zipfile

import pytest

from dbaba_cli import main, base_url


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("DBABA_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv("DBABA_API_KEY", "test-only")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            size = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(size)) if size else None
            calls.append((self.path, body, self.headers.get("Cookie"), self.headers.get("Authorization")))
            if self.path == "/api/health":
                result = {"ok": True}
            elif self.path == "/api/new-chat":
                result = {"ok": True}
            elif body["text"] == "question":
                result = {"reply": "Which database is this for?", "goal": {"id": 42}}
            elif "bad" in body["text"]:
                result = {"reply": "Fixed 2 mistakes"}
            else:
                result = {"reply": "Looks good"}
            self.send_response(200)
            if self.path == "/api/new-chat":
                self.send_header("Set-Cookie", "ge_vid=visitor; Path=/")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        do_GET = do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DBABA_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        yield calls, server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_status(local, capsys):
    assert main(["status"]) == 0
    assert capsys.readouterr().out.strip() == "running"


def test_ask(local, capsys):
    calls, _ = local
    assert main(["ask", "question"]) == 0
    assert calls[-1] == ("/api/message", {"text": "MySQL", "goal_id": 42},
                         "ge_vid=visitor", "Bearer test-only")
    assert capsys.readouterr().out.strip() == "Looks good"


def test_check(local, monkeypatch):
    calls, port = local
    monkeypatch.delenv("DBABA_URL")
    Path("dbaba.json").write_text(json.dumps({"port": port, "database": {"type": "postgresql"}}))
    Path("query.sql").write_text("SELECT 1;")
    assert main(["check", "query.sql"]) == 0
    assert calls[-1][1] == {"text": "PostgreSQL: SELECT 1;"}
    monkeypatch.setattr("sys.stdin", io.StringIO("bad query"))
    assert main(["check", "-", "--db", "mariadb"]) == 1
    assert calls[-1][1] == {"text": "MariaDB: bad query"}


def test_unreachable(local, monkeypatch, capsys):
    monkeypatch.setenv("DBABA_URL", "http://127.0.0.1:1")
    assert main(["status"]) == 2
    assert capsys.readouterr().out.strip() == "not running"
    assert main(["ask", "question"]) == 2
    monkeypatch.setattr("sys.stdin", io.StringIO("SELECT 1;"))
    assert main(["check", "-"]) == 2


def test_remote_policy(local, monkeypatch, capsys):
    for url in ("https://example.invalid", "http://8.8.8.8", "http://192.168.1.4"):
        monkeypatch.setenv("DBABA_URL", url)
        with pytest.raises(ValueError):
            base_url({}, False)
    assert base_url({}, True) == "http://192.168.1.4"
    assert "Warning" in capsys.readouterr().err
    monkeypatch.setenv("DBABA_URL", "http://8.8.8.8")
    with pytest.raises(ValueError):
        base_url({}, True)
    monkeypatch.setenv("DBABA_URL", "https://example.invalid")
    assert base_url({}, True) == "https://example.invalid"


def test_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip()


@pytest.mark.parametrize("local_entry", [False, True])
def test_serve(local, monkeypatch, local_entry):
    monkeypatch.setattr("dbaba_cli.util.find_spec", lambda name: object() if local_entry else None)
    def run(cmd, env, cwd):
        assert cmd[-2:] == ["--port", "9123"]
        assert cmd[2] == ("app.local" if local_entry else "uvicorn")
        assert env["ENGINE_MODE"] == "ml"
        assert env["DBABA_LOCAL"] == "1"
        assert env["GEMINI_API_KEY"] == ""
        assert env["DATABASE_URL"].endswith("/data/dbaba.db")
        assert cwd == Path.cwd()
        return 0
    monkeypatch.setattr("dbaba_cli.subprocess.call", run)
    assert main(["serve", "--port", "9123"]) == 0


def test_report(local):
    secrets = ["pass-value", "hash-value", "salt-value", "key-value", "nested-value"]
    Path("dbaba.json").write_text(json.dumps({
        "port": 8765, "database": {"password": secrets[0], "type": "mysql"},
        "screen_password": {"hash": secrets[1], "salt": secrets[2]},
        "api_keys": [{"hash": secrets[3]}], "nested": [{"secret": secrets[4]}],
        "other": secrets[0],
    }))
    Path("dbaba.log").write_text("old-line\n" * 201 + " ".join(secrets) +
                                 " Bearer unknown-token password=unknown-pass pwd=unknown-pwd "
                                 "postgresql://user:unknown-url@host\n")
    assert main(["report"]) == 0
    with zipfile.ZipFile(next(Path.cwd().glob("dbaba-report-*.zip"))) as archive:
        all_text = "\n".join(archive.read(name).decode() for name in archive.namelist())
        for secret in secrets + ["unknown-token", "unknown-pass", "unknown-pwd", "unknown-url"]:
            assert secret not in all_text
        config = json.loads(archive.read("dbaba.json"))
        assert config["database"] == {"type": "mysql"}
        assert "screen_password" not in config
        assert "api_keys" not in config
        assert len(archive.read("dbaba.log").splitlines()) == 200


def test_unreadable_log(local):
    Path("dbaba.log").write_bytes(b"\xff")
    assert main(["report"]) == 0
    with zipfile.ZipFile(next(Path.cwd().glob("dbaba-report-*.zip"))) as archive:
        assert "dbaba.log" not in archive.namelist()
        assert b"Log not included" in archive.read("versions.txt")


def test_redirect_not_followed(local, monkeypatch):
    calls, port = local
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/api/health")
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("DBABA_URL", f"http://127.0.0.1:{server.server_port}")
        assert main(["status"]) == 2
        assert not calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
