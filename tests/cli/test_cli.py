import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import zipfile

import pytest

from dbaba_cli import main, base_url, configuration, secret_values


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = tmp_path / "dbaba.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DBABA_CONFIG", str(path))
    monkeypatch.setattr("dbaba_cli.config_paths", lambda: (path, tmp_path / "fallback.json"))
    # Never read the host's systemd journal during report tests.
    monkeypatch.setattr("dbaba_cli.platform.system", lambda: "TestOS")
    return path


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.delenv("DBABA_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv("DBABA_API_KEY", "test-only")
    calls = []
    alerts = [{"id": "a", "level": "warning", "title": "Example alert", "text": "Check indexes", "at": "2026-01-01T00:00:00Z", "acknowledged": True}]
    job = {"id": "j", "name": "Scan", "enabled": True, "every": "day", "at": "08:00", "last_run": None}

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            size = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(size)) if size else None
            calls.append((self.path, body, self.headers.get("Cookie"), self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer test-only":
                self.send_error(401)
                return
            if self.path == "/api/health":
                result = {"ok": True, "version": "1.2.3"}
            elif self.path == "/api/state":
                result = {"version": "1.2.3", "setup_done": True,
                          "database": {"engine": "mysql", "host": "127.0.0.1", "port": 3306, "name": "sample", "connected": True},
                          "alerts": alerts, "last_scan": {"at": "2026-01-01T00:00:00Z", "score": 90}}
            elif self.path == "/api/alerts/a/ack":
                alerts[0]["acknowledged"] = True
                result = {"ok": True}
            elif self.path.startswith("/api/alerts/"):
                self.send_error(404)
                return
            elif self.path == "/api/report/scan":
                result = {"started": True}
            elif self.path == "/api/report":
                result = {"at": "2026-01-01T00:00:00Z", "score": 90, "engine": "mysql",
                          "sections": [{"id": "s", "title": "Indexes", "items": [{"id": "i", "severity": "warning", "title": "Missing index", "what": "Scan", "why": "Cost", "fix": {"sql": "CREATE INDEX example ON sample(id)", "needs_approval": True}}]}]}
            elif self.path == "/api/report.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html>Sample report</html>")
                return
            elif self.path == "/api/jobs":
                result = {"jobs": [job]}
            elif self.path == "/api/jobs/j":
                job.update(body)
                result = job
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
    output = capsys.readouterr().out
    assert "DBaba is running" in output
    assert "1.2.3" in output
    assert "connected" in output


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
    assert main(["support-report"]) == 0
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
    assert main(["support-report"]) == 0
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


def test_config_override_precedes_search(isolated_config, monkeypatch):
    isolated_config.write_text('{"port": 9123}', encoding="utf-8")
    fallback = isolated_config.parent / "fallback.json"
    fallback.write_text('{"port": 9999}', encoding="utf-8")
    monkeypatch.setattr("dbaba_cli.config_paths", lambda: (fallback,))
    assert configuration() == (isolated_config.parent, {"port": 9123})
    monkeypatch.delenv("DBABA_CONFIG")
    assert configuration() == (fallback.parent, {"port": 9999})
    fallback.unlink()
    assert configuration() == (isolated_config.parent, {})


def test_missing_explicit_config_does_not_fall_back(isolated_config):
    isolated_config.unlink()
    with pytest.raises(FileNotFoundError):
        configuration()


def test_secret_values_only_long_strings():
    assert set(secret_values({"n": 16384, "r": 8, "p": 1, "enabled": True,
                              "short": "1234567", "long": "12345678",
                              "nested": ["long-secret", False, 123456789]})) == {
        "12345678", "long-secret"
    }


def test_report_preserves_host_and_versions(isolated_config, monkeypatch):
    monkeypatch.setattr("dbaba_cli.version", lambda: "0.1.8")
    isolated_config.write_text(json.dumps({
        "port": 8765,
        "database": {"host": "127.0.0.1", "password": "long-password"},
        "screen_password": {"algorithm": "scrypt", "n": 16384, "r": 8, "p": 1,
                            "hash": "long-hash", "salt": "long-salt"},
        "nested": {"secret": "1", "token": "8", "key": True},
        "number": 12345678,
        "another_secret": "12345678",
    }), encoding="utf-8")
    Path("dbaba.log").write_text(
        "127.0.0.1 version 0.1.8 n=16384 r=8 p=1 long-password long-hash long-salt\n",
        encoding="utf-8",
    )
    assert main(["support-report"]) == 0
    with zipfile.ZipFile(next(Path.cwd().glob("dbaba-report-*.zip"))) as archive:
        config = json.loads(archive.read("dbaba.json"))
        assert config["database"] == {"host": "127.0.0.1"}
        assert config["number"] == 12345678
        assert config["nested"] == {}
        assert "screen_password" not in config
        assert b"DBaba 0.1.8" in archive.read("versions.txt")
        log = archive.read("dbaba.log").decode()
        assert "127.0.0.1 version 0.1.8 n=16384 r=8 p=1" in log
        for secret in ("long-password", "long-hash", "long-salt"):
            assert secret not in log


def test_alerts(local, capsys):
    assert main(["alerts"]) == 0
    assert main(["alerts", "--ack", "a"]) == 0
    assert main(["alerts", "--ack", "missing"]) == 2
    captured = capsys.readouterr()
    assert "Alert acknowledged" in captured.out
    assert "test-only" not in captured.out + captured.err


def test_health_report(local, capsys, tmp_path):
    assert main(["report", "--scan"]) == 0
    assert main(["report"]) == 1
    dest = tmp_path / "report.html"
    assert main(["report", "--html", str(dest)]) == 1
    assert dest.read_bytes() == b"<html>Sample report</html>"
    captured = capsys.readouterr()
    assert "Requires approval" in captured.out
    assert "test-only" not in captured.out + captured.err


def test_jobs(local, capsys):
    calls, _ = local
    assert main(["jobs"]) == 0
    assert main(["jobs", "j", "--off", "--at", "09:30"]) == 0
    assert calls[-1][1] == {"enabled": False, "at": "09:30"}
    assert main(["jobs", "j", "--on"]) == 0
    assert calls[-1][1] == {"enabled": True, "at": "09:30"}
    assert main(["jobs", "missing"]) == 1
    assert main(["jobs", "j", "--at", "25:00"]) == 2
    assert "test-only" not in capsys.readouterr().out


@pytest.mark.parametrize("args", [["status"], ["alerts"], ["report"], ["jobs"], ["ask", "question"]])
def test_missing_key(local, monkeypatch, capsys, args):
    monkeypatch.delenv("DBABA_API_KEY")
    assert main(args) == 2
    captured = capsys.readouterr()
    assert "test-only" not in captured.out + captured.err


def test_key_file(local, monkeypatch, tmp_path):
    monkeypatch.delenv("DBABA_API_KEY")
    key = tmp_path / ".config" / "dbaba" / "key"
    key.parent.mkdir(parents=True)
    key.write_text("test-only")
    key.chmod(0o600)
    assert main(["status"]) == 0


def test_support_migration(capsys):
    assert main(["report", "--zip"]) == 1
    assert capsys.readouterr().out.strip() == "The support ZIP is now: dbaba support-report"


def test_remote_flag(local, monkeypatch):
    calls, port = local
    monkeypatch.setenv("DBABA_URL", f"http://localhost:{port}")
    assert main(["status"]) == 2
    assert not calls
    monkeypatch.setenv("DBABA_URL", "https://example.invalid")
    assert base_url({}, True) == "https://example.invalid"


def test_explicit_serve_config(monkeypatch, tmp_path):
    config = tmp_path / "new" / "dbaba.json"
    monkeypatch.setattr("dbaba_cli.util.find_spec", lambda name: object())
    def execute(exe, cmd, env):
        assert cmd[-4:] == ["--port", "9123", "--config", str(config)]
        assert env["DBABA_CONFIG"] == str(config)
        assert env["GEMINI_API_KEY"] == ""
        raise SystemExit(0)
    monkeypatch.setattr("dbaba_cli.os.execve", execute)
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--port", "9123", "--config", str(config)])
    assert exc.value.code == 0


def test_report_before_first_scan(monkeypatch, capsys):
    from dbaba_cli.contract import commands_for
    from argparse import Namespace

    class EmptyReport:
        def call(self, path):
            assert path == "/api/report"
            return {"at": None, "score": None, "engine": "mysql",
                    "version": "1.0", "pending": True, "sections": []}

    assert commands_for(EmptyReport(), Namespace(command="report", scan=False, html=None)) == 0
    out = capsys.readouterr().out
    assert "No scan yet" in out
    assert "Scan in progress." in out
    assert "None/100" not in out
