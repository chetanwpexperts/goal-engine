"""S4b: DBaba on a laptop, reaching the database on another server through SSH (app/local/tunnel.py).

The lab: a private sshd (started here, as the user running the tests, on a free port, with its own host key and an
authorized test key) in front of the sandbox MariaDB and PostgreSQL. Everything goes through the real OpenSSH client:
accepting the host key by fingerprint, the tunnel, the report and the health job through it, "Find it for me" on the
SSH server, and every failure in plain words (unknown / changed host key, refused key, key with a passphrase, key
readable by others, nothing on the SSH port, forwarding switched off, nothing at the database port there).

Without sshd on this machine the lab tests skip, except in CI (CI installs openssh-server, so a skip there is a failure).
"""

import getpass
import json
import os
import shutil
import socket
import stat
import subprocess
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.local import checks, dbconn, report, tunnel
from app.local.config import Config
from app.local.jobs import Jobs
from app.local.server import make_app
from tests.test_local import MARIA, PG, needs_maria, needs_pg

SSHD = shutil.which("sshd") or ("/usr/sbin/sshd" if os.path.exists("/usr/sbin/sshd") else None)
if not SSHD and os.getenv("CI"):
    raise RuntimeError("CI must install openssh-server for the S4b SSH tests")
needs_sshd = pytest.mark.skipif(not SSHD or not shutil.which("ssh") or os.name == "nt", reason="no sshd / ssh here")


def _free():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _keygen(path, passphrase=""):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", passphrase, "-f", str(path)], check=True)
    os.chmod(path, 0o600)
    return path


class Lab:
    def __init__(self, root: Path, forwarding="local"):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.hostkey = _keygen(root / "host")
        self.key = _keygen(root / "user")
        self.other = _keygen(root / "other")
        self.locked = _keygen(root / "locked", "a passphrase")
        (root / "authorized_keys").write_text((root / "user.pub").read_text() + (root / "locked.pub").read_text())
        os.chmod(root / "authorized_keys", 0o600)
        self.port = _free()
        (root / "sshd_config").write_text(
            f"Port {self.port}\nListenAddress 127.0.0.1\nHostKey {self.hostkey}\nAuthorizedKeysFile {root / 'authorized_keys'}\n"
            f"PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n"
            f"AllowTcpForwarding {forwarding}\nPidFile {root / 'sshd.pid'}\nStrictModes no\nUsePAM no\n"
            f"MaxStartups 200\nLogLevel VERBOSE\n")
        # The lab makes far more unauthenticated connections (host key scans, failure cases) than a person would. OpenSSH
        # 9.8+ penalises a source address for that (PerSourcePenalties), which would block the lab, not test DBaba.
        if subprocess.run([SSHD, "-t", "-f", str(root / "sshd_config"), "-o", "PerSourcePenalties=no"],
                          capture_output=True).returncode == 0:
            with open(root / "sshd_config", "a") as fh:
                fh.write("PerSourcePenalties no\n")
        self.proc = subprocess.Popen([SSHD, "-D", "-f", str(root / "sshd_config"), "-E", str(root / "sshd.log")])
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        self.user = getpass.getuser()
        fp = subprocess.run(["ssh-keygen", "-l", "-E", "sha256", "-f", str(root / "host.pub")], capture_output=True,
                            text=True).stdout.split()[1]
        self.fingerprint = fp

    def log(self) -> str:
        """sshd's version and the end of its log, for a failure message."""
        ver = subprocess.run([SSHD, "-V"], capture_output=True, text=True).stderr.strip()
        try:
            tail = (self.root / "sshd.log").read_text(errors="replace").splitlines()[-15:]
        except OSError:
            tail = []
        return f"{ver}; sshd alive={self.proc.poll() is None}; log:\n" + "\n".join(tail)

    def ssh(self, **kw):
        return {"host": "127.0.0.1", "port": self.port, "user": self.user, "key_file": str(self.key), **kw}

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    if not SSHD:
        pytest.skip("no sshd")
    lab = Lab(tmp_path_factory.mktemp("sshlab"))
    yield lab
    tunnel.manager.close_all()
    lab.stop()


@pytest.fixture()
def kh(tmp_path, lab):
    """An empty DBaba known_hosts, accepted for this lab's server."""
    path = tmp_path / "known_hosts"
    tunnel.manager.close_all()
    tunnel.manager.configure(path)
    return path


def trusted_kh(kh, lab):
    assert tunnel.trust(lab.ssh(), kh, lab.fingerprint), lab.log()
    return kh


def mdb(lab, base=MARIA, **ssh):
    return {"type": base["engine"], "host": "127.0.0.1", "port": base["port"], "name": base["database"], "user": base["user"],
            "password": base["password"], "ssh": lab.ssh(**ssh)}


# ---------------------------------------------------------------- the settings (no SSH needed)
@pytest.mark.parametrize("bad, words", [
    ({"host": "-oProxyCommand=x", "user": "u"}, "SSH server's name"),
    ({"host": "a host", "user": "u"}, "SSH server's name"),
    ({"host": "h", "user": "-l"}, "SSH user name"),
    ({"host": "h", "user": "u v"}, "SSH user name"),
    ({"host": "h", "user": "u", "port": 0}, "SSH port"),
    ({"host": "h", "user": "u", "key_file": "id_ed25519"}, "full path"),
    ({"host": "h", "user": "u", "key_file": "/no/such/key"}, "no key file"),
    ("not a dict", "aren't valid"),
])
def test_ssh_details_are_checked_before_ssh_ever_runs(bad, words):
    ssh, why = tunnel.clean(bad)
    assert ssh is None and words in why


def test_ssh_details_defaults():
    ssh, why = tunnel.clean({"host": "db.example.org", "user": "deploy"})
    assert why == "" and ssh == {"host": "db.example.org", "port": 22, "user": "deploy", "key_file": ""}


def test_the_ssh_command_is_locked_down(tmp_path):
    t = tunnel.Tunnel({"host": "srv", "port": 2200, "user": "u", "key_file": "/k"}, tmp_path / "kh", [("127.0.0.1", 3306), ("::1", 5432)])
    t.local = [40001, 40002]
    cmd = t.command()
    joined = " ".join(cmd)
    for must in ("-F none", "-N", "-T", "BatchMode=yes", "StrictHostKeyChecking=yes", "ExitOnForwardFailure=yes",
                 "ForwardAgent=no", "ForwardX11=no", "PermitLocalCommand=no", "IdentitiesOnly=yes",
                 "-L 127.0.0.1:40001:127.0.0.1:3306", "-L 127.0.0.1:40002:[::1]:5432"):
        assert must in joined, must
    assert cmd[-2:] == ["--", "srv"] and "-g" not in cmd and "-R" not in cmd and "-D" not in cmd


# what OpenSSH 9.6 prints (lab, by hand and in the tests below) -> the reason's code
@pytest.mark.parametrize("said, code", [
    ("No ED25519 host key is known for [h]:22 and you have requested strict checking.\nHost key verification failed.", "host_key_unknown"),
    ("@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @\nHost key verification failed.", "host_key_changed"),
    ("u@h: Permission denied (publickey).", "auth"),
    ("Warning: Identity file /k not accessible: No such file or directory.\nu@h: Permission denied (publickey).", "key_unreadable"),
    ('Permissions 0644 for \'k\' are too open.\nLoad key "k": bad permissions\nu@h: Permission denied (publickey).', "key_permissions"),
    ("ssh: connect to host h port 22: Connection refused", "refused"),
    ("ssh: Could not resolve hostname h: Name or service not known", "dns"),
    ("ssh: connect to host h port 22: Connection timed out", "timeout"),
    ("Connection timed out during banner exchange", "timeout"),
    ("channel 1: open failed: administratively prohibited: open failed", "forwarding_forbidden"),
    ("channel 4: open failed: connect failed: Connection refused", "remote_db"),
    ("bind [127.0.0.1]:40001: Address already in use", "port_busy"),
])
def test_every_ssh_failure_has_its_plain_reason(said, code):
    text, got = tunnel.reason(said, {"host": "h", "port": 22, "user": "u", "key_file": "/k"}, "127.0.0.1:3306")
    assert got == code and text and "ssh -" not in text


def test_where_the_database_is():
    assert dbconn.via({"host": "127.0.0.1"}) == "here"
    assert dbconn.via({"host": "db.internal"}) == "network"
    assert dbconn.via({"host": "127.0.0.1", "ssh": {"host": "x"}}) == "ssh"


def test_report_never_compares_the_laptops_memory_with_the_servers_buffer_pool():
    """Through a tunnel the address is 127.0.0.1, but the memory DBaba can see is the laptop's, not the server's."""
    from tests.test_local_report import my_g, pg_g
    here = {"type": "mariadb", "host": "127.0.0.1", "port": 3306}
    ssh = {**here, "ssh": {"host": "srv", "port": 22, "user": "u"}}
    ram = 64 * 1024 ** 3
    ids = lambda out: {i["id"] for its in out.values() for i in its}  # noqa: E731
    assert "buffer_pool" in ids(checks.judge(my_g(), "mariadb", here, ram=ram))
    out = ids(checks.judge(my_g(), "mariadb", ssh, ram=ram))
    assert "buffer_pool" not in out and "memory_unseen" in out
    pg_here = {"type": "postgresql", "host": "127.0.0.1", "port": 5432}
    pg_ssh = {**pg_here, "ssh": {"host": "srv", "port": 22, "user": "u"}}
    off = pg_g(vars={**pg_g()["vars"], "ssl": ("off", None)})
    assert "shared_buffers" in ids(checks.judge(pg_g(), "postgresql", pg_here, ram=ram))
    got = ids(checks.judge(off, "postgresql", pg_ssh, ram=ram))
    assert "shared_buffers" not in got and "ssl_off" not in got            # the tunnel encrypts it
    assert "ssl_off" in ids(checks.judge(off, "postgresql", {**pg_here, "host": "db.internal"}, ram=ram))


# ---------------------------------------------------------------- host keys (the lab sshd)
@needs_sshd
def test_host_key_fingerprint_matches_the_servers_own(lab, kh):
    keys = tunnel.host_keys(lab.ssh())
    assert any(k["fingerprint"] == lab.fingerprint and k["type"] == "ED25519" for k in keys)
    assert not tunnel.trusted(lab.ssh(), kh)


@needs_sshd
def test_only_the_confirmed_fingerprint_is_accepted(lab, kh):
    assert not tunnel.trust(lab.ssh(), kh, "SHA256:" + "A" * 43)
    assert not tunnel.trust(lab.ssh(), kh, "not a fingerprint")
    assert not kh.exists() or not tunnel.trusted(lab.ssh(), kh)
    assert tunnel.trust(lab.ssh(), kh, lab.fingerprint) and tunnel.trusted(lab.ssh(), kh)
    assert stat.S_IMODE(kh.stat().st_mode) == 0o600


@needs_sshd
@needs_maria
def test_unknown_host_key_is_refused_and_its_fingerprint_shown(lab, kh):
    res = dbconn.test(mdb(lab))
    assert res["ok"] is False and res["code"] == "host_key_unknown"
    assert lab.fingerprint in [k["fingerprint"] for k in res["host_keys"]]


@needs_sshd
@needs_maria
def test_a_changed_host_key_is_refused_and_never_replaced(lab, kh, tmp_path):
    fake = _keygen(tmp_path / "fake")
    kind, blob = (tmp_path / "fake.pub").read_text().split()[:2]
    kh.write_text(f"[127.0.0.1]:{lab.port} {kind} {blob}\n")
    os.chmod(kh, 0o600)
    res = dbconn.test(mdb(lab))
    assert res["ok"] is False and res["code"] == "host_key_changed" and "CHANGED" in res["reason"], (res, lab.log())
    assert tunnel.trust(lab.ssh(), kh, lab.fingerprint)          # "trusted" already: the old line stays
    assert kh.read_text().count("\n") == 1 and blob in kh.read_text() and fake.exists()


# ---------------------------------------------------------------- the tunnel itself
@needs_sshd
@needs_maria
def test_mariadb_through_the_tunnel(lab, kh):
    trusted_kh(kh, lab)
    res = dbconn.test(mdb(lab))
    assert res["ok"] is True and "MariaDB" in res["version"]
    conn = dbconn.open_db(mdb(lab))
    try:
        assert conn.rows("SELECT 40 + 2")[1][0][0] == 42
    finally:
        dbconn.close(conn)


@needs_sshd
@needs_pg
def test_postgresql_through_the_tunnel(lab, kh):
    trusted_kh(kh, lab)
    res = dbconn.test(mdb(lab, PG))
    assert res["ok"] is True and res["version"][:2].isdigit()


@needs_sshd
@needs_maria
@needs_pg
def test_one_tunnel_per_database_and_reused(lab, kh):
    trusted_kh(kh, lab)
    a = tunnel.manager.local_end(lab.ssh(), "127.0.0.1", 3306)
    b = tunnel.manager.local_end(lab.ssh(), "127.0.0.1", 3306)
    c = tunnel.manager.local_end(lab.ssh(), "127.0.0.1", 5432)
    assert a == b and a != c and a[0] == "127.0.0.1"


@needs_sshd
@needs_maria
def test_a_dead_tunnel_is_reopened_by_the_next_use(lab, kh):
    """The laptop slept or the Wi-Fi changed: ssh exited; the next health check opens a new tunnel by itself."""
    trusted_kh(kh, lab)
    assert dbconn.test(mdb(lab))["ok"]
    for t in tunnel.manager._open.values():
        t.proc.kill()
        t.proc.wait()
    assert dbconn.test(mdb(lab))["ok"]


@needs_sshd
@needs_maria
def test_tunnel_listens_only_on_loopback(lab, kh):
    trusted_kh(kh, lab)
    _, port = tunnel.manager.local_end(lab.ssh(), "127.0.0.1", 3306)
    out = subprocess.run(["ss", "-Hltn", f"sport = :{port}"], capture_output=True, text=True).stdout if shutil.which("ss") else ""
    if out:
        assert all("127.0.0.1:" in line for line in out.splitlines()), out


@needs_sshd
@needs_maria
def test_wrong_key_is_refused_in_plain_words(lab, kh):
    trusted_kh(kh, lab)
    res = dbconn.test(mdb(lab, key_file=str(lab.other)))
    assert res["code"] == "auth" and "authorized_keys" in res["reason"]


@needs_sshd
@needs_maria
def test_a_key_with_a_passphrase_says_use_ssh_agent(lab, kh, monkeypatch):
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    trusted_kh(kh, lab)
    res = dbconn.test(mdb(lab, key_file=str(lab.locked)))
    assert res["code"] == "key_passphrase" and "ssh-add" in res["reason"]


@needs_sshd
@needs_maria
def test_a_key_readable_by_others_is_refused(lab, kh, tmp_path):
    trusted_kh(kh, lab)
    loose = tmp_path / "loose"
    shutil.copy(lab.key, loose)
    os.chmod(loose, 0o644)
    res = dbconn.test(mdb(lab, key_file=str(loose)))
    assert res["code"] == "key_permissions" and "chmod 600" in res["reason"]


@needs_sshd
@needs_maria
def test_nothing_on_the_ssh_port(lab, kh):
    res = dbconn.test(mdb(lab, port=_free()))
    assert res["ok"] is False and res["code"] == "refused" and "Nothing accepts SSH" in res["reason"]


@needs_sshd
def test_an_unknown_ssh_name(lab, kh):
    db = mdb(lab, host="no-such-host.invalid")
    res = dbconn.test(db)
    assert res["ok"] is False and res["code"] in ("dns", "timeout")


@needs_sshd
@needs_maria
def test_nothing_at_the_database_port_on_the_server(lab, kh):
    trusted_kh(kh, lab)
    res = dbconn.test({**mdb(lab), "port": _free()})
    assert res["ok"] is False and res["code"] == "remote_db" and "on the server" in res["reason"]


@needs_sshd
@needs_maria
def test_forwarding_switched_off_on_the_server(tmp_path, kh):
    strict = Lab(tmp_path / "strict", forwarding="no")
    try:
        tunnel.manager.configure(kh)
        assert tunnel.trust(strict.ssh(), kh, strict.fingerprint)
        db = {**mdb(strict), "ssh": strict.ssh()}
        res = dbconn.test(db)
        assert res["ok"] is False and res["code"] == "forwarding_forbidden" and "AllowTcpForwarding" in res["reason"]
    finally:
        tunnel.manager.close_all()
        strict.stop()


# ---------------------------------------------------------------- the features through the tunnel
@needs_sshd
@needs_maria
@needs_pg
def test_find_it_for_me_on_the_ssh_server(lab, kh):
    trusted_kh(kh, lab)
    found = dbconn.detect_via_ssh(lab.ssh())
    engines = {f["engine"] for f in found}
    assert "mariadb" in engines and "postgresql" in engines
    assert all("there" in f["how"] and f["host"] == "127.0.0.1" for f in found)
    assert tunnel.manager._probe is None


@needs_sshd
@needs_maria
def test_the_full_report_through_ssh_mariadb(lab, kh):
    trusted_kh(kh, lab)
    rep = report.scan(mdb(lab))
    got = {i["id"] for s in rep["sections"] for i in s["items"]}
    assert "unreachable" not in got and "memory_unseen" in got and "buffer_pool" not in got and rep["version"]


@needs_sshd
@needs_pg
def test_the_full_report_through_ssh_postgresql(lab, kh):
    trusted_kh(kh, lab)
    rep = report.scan(mdb(lab, PG))
    got = {i["id"] for s in rep["sections"] for i in s["items"]}
    assert "unreachable" not in got and "memory_unseen" in got and "ssl_off" not in got


@needs_sshd
@needs_maria
def test_health_job_through_ssh_and_when_the_server_goes(tmp_path, kh):
    gone = Lab(tmp_path / "gone")
    cfg = Config(tmp_path / "dbaba.json")
    tunnel.manager.configure(kh)
    assert tunnel.trust(gone.ssh(), kh, gone.fingerprint)
    cfg.update(lambda d: d.update(database=mdb(gone), setup_done=True))
    jobs = Jobs(cfg)
    jobs.health()
    assert jobs.connected is True
    tunnel.manager.close_all()
    gone.stop()
    jobs.health()
    al = [a for a in jobs.public_alerts() if a["level"] == "critical"]
    assert jobs.connected is False and al and "SSH" in al[0]["text"]


@needs_sshd
@needs_maria
def test_screen_setup_over_ssh_end_to_end(lab, tmp_path):
    tunnel.manager.close_all()
    app = make_app(str(tmp_path / "dbaba.json"), 8765, "TOKEN-S", announce=lambda line: None)
    c = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
    H = {"X-DBaba-Setup": "TOKEN-S"}
    body = {**MARIA, "host": "127.0.0.1", "ssh": lab.ssh()}
    first = c.post("/api/setup/test", headers=H, json=body).json()
    assert first["ok"] is False and first["code"] == "host_key_unknown" and first["host_keys"], (first, lab.log())
    seen = c.post("/api/setup/ssh", headers=H, json={"ssh": lab.ssh()}).json()
    assert seen["trusted"] is False and lab.fingerprint in [k["fingerprint"] for k in seen["keys"]]
    assert c.post("/api/setup/ssh", headers=H, json={"ssh": lab.ssh(), "accept": "SHA256:" + "B" * 43}).json()["ok"] is False
    assert c.post("/api/setup/ssh", headers=H, json={"ssh": lab.ssh(), "accept": lab.fingerprint}).json()["trusted"] is True
    found = c.post("/api/setup/detect", headers=H, json={"ssh": lab.ssh()}).json()["found"]
    assert any(f["engine"] == "mariadb" for f in found)
    assert c.post("/api/setup/test", headers=H, json=body).json()["ok"] is True
    saved = c.post("/api/setup/save", headers=H, json={**body, "screen_password": "screen pass 9"}).json()
    assert saved["ok"] is True
    on_disk = json.loads((tmp_path / "dbaba.json").read_text())
    assert set(on_disk["database"]["ssh"]) == {"host", "port", "user", "key_file"}        # a path, never a key or password
    assert "PRIVATE KEY" not in (tmp_path / "dbaba.json").read_text()
    K = {"Authorization": f"Bearer {saved['api_key']}"}
    st = c.get("/api/state", headers=K).json()["database"]
    assert st["via"] == "ssh" and st["ssh"] == {"host": "127.0.0.1", "port": lab.port, "user": lab.user}
    for _ in range(80):
        r = c.get("/api/report", headers=K).json()
        if r["at"]:
            break
        time.sleep(0.5)
    assert r["at"] and not any(i["id"] == "unreachable" for s in r["sections"] for i in s["items"])
    # back to a direct address (DBaba moved onto the server itself): "ssh": null
    direct = c.post("/api/database", headers=K, json={"ssh": None}).json()
    assert direct["ok"] is True and "ssh" not in json.loads((tmp_path / "dbaba.json").read_text())["database"]
    assert c.get("/api/state", headers=K).json()["database"]["via"] == "here"
    tunnel.manager.close_all()


@needs_sshd
def test_setup_ssh_needs_the_token_or_a_login(lab, tmp_path):
    app = make_app(str(tmp_path / "dbaba.json"), 8765, "TOKEN-T", announce=lambda line: None)
    c = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
    assert c.post("/api/setup/ssh", json={"ssh": lab.ssh()}).status_code == 401
    far = TestClient(app, base_url="http://127.0.0.1:8765", client=("10.0.0.9", 50000))
    assert far.post("/api/setup/ssh", headers={"X-DBaba-Setup": "TOKEN-T"}, json={"ssh": lab.ssh()}).status_code == 401
    bad = c.post("/api/setup/ssh", headers={"X-DBaba-Setup": "TOKEN-T"}, json={"ssh": {"host": "-oProxyCommand=x", "user": "u"}}).json()
    assert bad["ok"] is False


def test_a_laptop_that_was_off_at_scan_time_scans_when_it_is_back(tmp_path):
    """The laptop slept through 02:40: the day's scan runs at the next tick after it wakes, once, not again that day."""
    import datetime as dt
    jobs = Jobs(Config(tmp_path / "dbaba.json"))
    scan = {**jobs.settings()["scan"], "at": "02:40"}
    local = dt.datetime.now().astimezone().tzinfo
    woke = dt.datetime(2026, 11, 27, 9, 15, tzinfo=local)
    two_days_ago = {"scan": {"at": (woke - dt.timedelta(days=2)).isoformat()}}
    assert jobs.due("scan", scan, woke, two_days_ago) is True
    assert jobs.due("scan", scan, woke, {"scan": {"at": woke.isoformat()}}) is False
    early = dt.datetime(2026, 11, 27, 1, 0, tzinfo=local)        # before today's time: yesterday's run counts
    assert jobs.due("scan", scan, early, {"scan": {"at": (early - dt.timedelta(hours=22)).isoformat()}}) is False
