"""Run all three local contract examples against a loopback-only mock."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import pytest

ROOT = Path(__file__).resolve().parents[2]
GROUPS = {
    "health": ["/api/health"], "state": ["/api/state"],
    "chat": ["/api/new-chat", "/api/message"],
    "alerts": ["/api/alerts/a/ack"],
    "report": ["/api/report", "/api/report/scan", "/api/report.html"],
    "jobs": ["/api/jobs", "/api/jobs/scan"],
    "setup": ["/api/setup/detect", "/api/setup/test", "/api/setup/save"],
    "session": ["/api/login", "/api/state", "/api/logout"],
    "reset": ["/api/setup/reset"],
}
@pytest.fixture
def backend():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size)) if size else None
            calls.append((self.path, self.command, body))
            setup = self.path in GROUPS["setup"]
            auth = self.headers.get("Authorization") == "Bearer fixture-key"
            session = self.headers.get("Cookie") == "dbaba_session=fixture"
            if (setup and self.headers.get("X-DBaba-Setup") != "fixture-token") or (not setup and self.path not in ("/api/health", "/api/login") and not (auth or session)):
                self.send_error(401)
                return
            result = {"ok": True}
            if self.path == "/api/setup/save":
                result["api_key"] = "fixture-key"
            elif self.path == "/api/message":
                result["reply"] = "Example reply"
            elif self.path == "/api/report":
                result = {"at": None, "score": None, "sections": []}
            elif self.path == "/api/jobs":
                result = {"jobs": []}
            elif self.path == "/api/setup/detect":
                result = {"found": []}
            elif self.path == "/api/report/scan":
                result = {"started": True}
            elif self.path == "/api/report.html":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<html>Report</html>")
                return
            self.send_response(200)
            if self.path == "/api/login":
                self.send_header("Set-Cookie", "dbaba_session=fixture; Path=/")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        do_GET = do_POST = handle_request
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

@pytest.mark.parametrize("language", ["py", "js", "php"])
def test_example_groups(language, backend, tmp_path):
    exe = {"py": sys.executable, "js": shutil.which("node"), "php": shutil.which("php")}[language]
    if not exe:
        pytest.skip("Runtime unavailable")
    if language == "php" and subprocess.run([exe, "-r", "exit(extension_loaded('curl')?0:1);"]).returncode:
        pytest.skip("PHP cURL unavailable")
    port, calls = backend
    env = dict(os.environ, DBABA_URL=f"http://127.0.0.1:{port}", DBABA_API_KEY="fixture-key",
               DBABA_SETUP_TOKEN="fixture-token", DBABA_ALERT_ID="a", DBABA_JOB_ID="scan", DBABA_CONFIRM_RESET="yes")
    for group, expected in GROUPS.items():
        calls.clear()
        child_env = env.copy()
        if group == "session":
            child_env.pop("DBABA_API_KEY")
        body = {"engine":"mysql","host":"127.0.0.1","port":3306,"database":"sample","user":"sample",
                "password":"fixture-password","screen_password":"fixture-screen"}
        run = subprocess.run([exe, str(ROOT / "examples" / f"local_contract.{language}"), group],
                             cwd=tmp_path, env=child_env, input=json.dumps(body), text=True, capture_output=True, timeout=40)
        assert run.returncode == 0, run.stderr
        assert [x[0] for x in calls] == expected
        for secret in ("fixture-key","fixture-password","fixture-screen","fixture-token"):
            assert secret not in run.stdout + run.stderr
    assert (tmp_path / "report.html").read_text() == "<html>Report</html>"
    assert (tmp_path / "dbaba-key").read_text() == "fixture-key"
