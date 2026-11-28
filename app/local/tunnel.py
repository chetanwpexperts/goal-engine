"""Reaching a database on another server through SSH, when DBaba runs on a laptop (S4b: nothing installed on the server).

DBaba starts the computer's own OpenSSH client (`ssh`, on Linux, macOS and Windows 10+) with a local forward:

    127.0.0.1:<free port on this computer>  ->  SSH server  ->  <database host as the SSH server sees it>:<port>

and then talks to the database at 127.0.0.1:<free port> exactly as if it ran here. Rules, all lab-proven
(tests/test_local_ssh.py against a private sshd in front of MariaDB and PostgreSQL):

* Keys only: a key file (absolute path) or the computer's ssh-agent. No SSH password or passphrase is ever asked for or
  stored (BatchMode=yes). A key with a passphrase works once it is loaded into ssh-agent.
* The server's host key must be accepted first, by its fingerprint (SHA256:...), into DBaba's own known_hosts file
  (data/local/known_hosts). Unknown or CHANGED keys are refused (StrictHostKeyChecking=yes); a changed key is never
  replaced by DBaba, the person removes it on purpose.
* The forward listens on 127.0.0.1 only. `-F none`: the person's ssh config can't add forwards, commands or proxies.
  No agent or X11 forwarding, no remote command (-N), no terminal (-T).
* Each failure is one plain sentence, from what OpenSSH 9.6 itself prints (lab):
    "No ED25519 host key is known for ..." / "Host key verification failed."       -> key not accepted yet
    "REMOTE HOST IDENTIFICATION HAS CHANGED!"                                       -> key changed since accepted
    "Permission denied (publickey)."                                                -> key refused (wrong key or user)
    "Identity file ... not accessible" / "bad permissions" / "incorrect passphrase" -> the key file itself
    "Connection refused" / "Could not resolve hostname" / "timed out"               -> the SSH address
    "open failed: administratively prohibited"                                      -> sshd has AllowTcpForwarding no
    "open failed: connect failed: Connection refused"                               -> nothing at the database port there
* Ready means: ssh said "Authenticated to" (LogLevel=VERBOSE) and the local port is taken. DBaba never opens a test
  connection through the tunnel just to see if it is up (a bare connect counts as a connection error in MySQL / MariaDB).
"""

import atexit
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
_HOST = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")       # a name, an IPv4 address or an IPv6 address (with ':')
_FP = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
START_TIMEOUT = 20.0


class TunnelError(ValueError):
    """`str(exc)` is the plain sentence for the person; `code` says which kind of failure it was."""

    def __init__(self, text: str, code: str, host_keys: list | None = None):
        super().__init__(text)
        self.code = code
        self.host_keys = host_keys or []


# ---------------------------------------------------------------- the settings
def clean(ssh) -> tuple[dict | None, str]:
    """The `ssh` part of the database settings, checked: {"host", "port", "user", "key_file"}."""
    if not isinstance(ssh, dict):
        return None, "The SSH details aren't valid."
    host = str(ssh.get("host") or "").strip()
    user = str(ssh.get("user") or "").strip()
    key = str(ssh.get("key_file") or "").strip()
    try:
        port = 22 if ssh.get("port") in (None, "") else int(ssh["port"])
    except (TypeError, ValueError):
        port = 0
    if not host or not _HOST.match(host) or host.startswith("-"):
        return None, "Give the SSH server's name or IP address (letters, digits, dots and dashes)."
    if not user or not _NAME.match(user) or user.startswith("-"):
        return None, "Give the SSH user name (letters, digits, dots, dashes and underscores)."
    if not 1 <= port <= 65535:
        return None, "The SSH port must be a number from 1 to 65535 (usually 22)."
    if key:
        if len(key) > 4096 or any(ord(c) < 32 for c in key) or not os.path.isabs(key):
            return None, "Give the full path of the private key file (for example /home/you/.ssh/id_ed25519), or leave it empty to use ssh-agent."
        if not os.path.isfile(key):
            return None, f"There is no key file at {key}."
    return {"host": host, "port": port, "user": user, "key_file": key}, ""


def ssh_binary() -> str | None:
    return shutil.which("ssh")


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise TunnelError(f"DBaba needs the OpenSSH client ({tool}) on this computer. Linux: install openssh-client; "
                          "macOS: it is built in; Windows: Settings > Optional features > OpenSSH Client.", "no_ssh")
    return path


def _dest(ssh: dict) -> str:
    return ssh["host"] if ssh["port"] == 22 else f"[{ssh['host']}]:{ssh['port']}"


def where(ssh: dict) -> str:
    return f"{ssh['user']}@{ssh['host']}" + ("" if ssh["port"] == 22 else f" port {ssh['port']}")


# ---------------------------------------------------------------- host keys
def host_keys(ssh: dict, timeout: int = 8) -> list[dict]:
    """The keys the server offers right now: [{"type", "fingerprint", "line"}]. Empty when nothing answers."""
    scan = _need("ssh-keyscan")
    keygen = _need("ssh-keygen")
    try:
        out = subprocess.run([scan, "-T", str(timeout), "-p", str(ssh["port"]), "--", ssh["host"]], capture_output=True,
                             text=True, timeout=timeout + 5, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    keys = []
    for line in out.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        try:
            fp = subprocess.run([keygen, "-l", "-E", "sha256", "-f", "-"], input=line + "\n", capture_output=True, text=True,
                                timeout=10).stdout.split()
        except (OSError, subprocess.TimeoutExpired):
            continue
        if len(fp) >= 2 and _FP.match(fp[1]):
            kind = fp[-1].strip("()") if fp[-1].startswith("(") else line.split()[1]
            keys.append({"type": kind, "fingerprint": fp[1], "line": line.strip()})
    order = {"ED25519": 0, "ECDSA": 1, "RSA": 2}
    return sorted(keys, key=lambda k: order.get(k["type"], 9))


def trusted(ssh: dict, known_hosts: Path) -> bool:
    if not Path(known_hosts).is_file():
        return False
    try:
        return subprocess.run([_need("ssh-keygen"), "-F", _dest(ssh), "-f", str(known_hosts)], capture_output=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def trust(ssh: dict, known_hosts: Path, fingerprint: str) -> bool:
    """Accept the server's key whose fingerprint the person confirmed. The key is scanned again now and only a key
    with exactly that fingerprint is written; an already-known host is never overwritten (a changed key stays refused)."""
    if not isinstance(fingerprint, str) or not _FP.match(fingerprint.strip()):
        return False
    hit = [k for k in host_keys(ssh) if k["fingerprint"] == fingerprint.strip()]
    if not hit:
        return False
    if trusted(ssh, known_hosts):
        return True
    known_hosts = Path(known_hosts)
    known_hosts.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(known_hosts, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(hit[0]["line"] + "\n")
    return True


# ---------------------------------------------------------------- reasons
def _key_state(key_file: str) -> str:
    """'ok', 'passphrase' or 'unreadable', by asking ssh-keygen for the public half with an empty passphrase."""
    if not key_file:
        return "ok"
    keygen = shutil.which("ssh-keygen")
    if not keygen:
        return "ok"
    try:
        r = subprocess.run([keygen, "-y", "-P", "", "-f", key_file], capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return "ok"
    if r.returncode == 0:
        return "ok"
    return "passphrase" if "passphrase" in r.stderr.lower() else "unreadable"


def reason(stderr: str, ssh: dict, remote: str = "") -> tuple[str, str]:
    """(plain sentence, code) for what ssh printed."""
    low = stderr.lower()
    at = where(ssh)
    if "remote host identification has changed" in low:
        return (f"The SSH key of {ssh['host']} has CHANGED since you accepted it. That happens when a server is reinstalled, "
                "but it can also mean someone is in between. Ask whoever runs the server before trusting the new key; DBaba "
                "won't connect until the old key is removed from its known_hosts file.", "host_key_changed")
    if "host key is known" in low or "host key verification failed" in low:
        return (f"DBaba doesn't know the SSH key of {ssh['host']} yet. Check its fingerprint with whoever runs the server, "
                "then accept it.", "host_key_unknown")
    if "bad permissions" in low or "unprotected private key" in low:
        return (f"SSH refuses the key file {ssh['key_file']} because other users can read it. Make it private: "
                f"chmod 600 {ssh['key_file']}", "key_permissions")
    if "not accessible" in low:
        return f"The key file {ssh['key_file']} can't be read (missing, or DBaba's account can't open it).", "key_unreadable"
    if "permission denied" in low:
        state = _key_state(ssh.get("key_file") or "")
        if state == "passphrase":
            return ("This key has a passphrase, and DBaba never asks for or stores one. Load it into ssh-agent first "
                    f"(ssh-add {ssh['key_file']}), then try again.", "key_passphrase")
        return (f"The SSH server refused the key for {at}. Check the user name, and that the key's .pub line is in "
                "~/.ssh/authorized_keys of that user on the server.", "auth")
    if "could not resolve hostname" in low:
        return f"The name {ssh['host']} wasn't found. Check the spelling, or use the server's IP address.", "dns"
    if "connection refused" in low and "open failed" not in low:
        return (f"Nothing accepts SSH at {ssh['host']} port {ssh['port']}. Is the SSH server running, and is that its port?",
                "refused")
    if "timed out" in low:
        return (f"No answer from {ssh['host']} port {ssh['port']}: a firewall may be dropping it, or the address is wrong.",
                "timeout")
    if "administratively prohibited" in low:
        return ("SSH works, but this server doesn't allow forwarding to the database (AllowTcpForwarding no in sshd_config). "
                "Ask whoever runs it to allow it for this user.", "forwarding_forbidden")
    if "open failed" in low:
        return (f"SSH works, but nothing answers at {remote} on the server. Check the database's address as the server "
                "sees it (usually 127.0.0.1, port 3306 or 5432).", "remote_db")
    if "address already in use" in low:
        return "The local port DBaba picked was taken a moment later.", "port_busy"
    return f"SSH to {at} didn't work. Check the address, the user and the key.", "ssh"


# ---------------------------------------------------------------- one tunnel
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_taken(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
        return False


def _remote(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


class Tunnel:
    def __init__(self, ssh: dict, known_hosts: Path, forwards: list[tuple[str, int]]):
        self.ssh = ssh
        self.known_hosts = Path(known_hosts)
        self.forwards = forwards                    # [(host as the SSH server sees it, port)]
        self.local: list[int] = []
        self.proc: subprocess.Popen | None = None
        self._err: list[str] = []
        self._authed = threading.Event()

    def command(self) -> list[str]:
        cmd = [_need("ssh"), "-F", "none", "-N", "-T",
               "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={self.known_hosts}",
               "-o", f"GlobalKnownHostsFile={os.devnull}", "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "ForwardAgent=no",
               "-o", "ForwardX11=no", "-o", "PermitLocalCommand=no", "-o", "LogLevel=VERBOSE",
               "-p", str(self.ssh["port"]), "-l", self.ssh["user"]]
        if self.ssh.get("key_file"):
            cmd += ["-o", "IdentitiesOnly=yes", "-i", self.ssh["key_file"]]
        for (host, port), lp in zip(self.forwards, self.local):
            cmd += ["-L", f"127.0.0.1:{lp}:{_remote(host, port)}"]
        return cmd + ["--", self.ssh["host"]]

    def _read(self):
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line.startswith("Authenticated to "):
                self._authed.set()
            self._err.append(line)
            del self._err[:-40]

    def stderr(self) -> str:
        return "\n".join(self._err)

    def start(self, timeout: float = START_TIMEOUT) -> list[int]:
        if not self.known_hosts.is_file() or not trusted(self.ssh, self.known_hosts):
            keys = host_keys(self.ssh)
            if not keys:
                text, code = reason("", self.ssh)
                probe = self._probe_text()
                if probe:
                    text, code = reason(probe, self.ssh)
                raise TunnelError(text, code)
            raise TunnelError(*reason("Host key verification failed.", self.ssh), host_keys=keys)
        for attempt in range(3):
            self.local = [_free_port() for _ in self.forwards]
            try:
                self.proc = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE, **_no_window())
            except OSError as exc:
                raise TunnelError(f"Couldn't start ssh on this computer ({exc.strerror or exc}).", "no_ssh") from exc
            threading.Thread(target=self._read, daemon=True, name="dbaba-ssh").start()
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if self.proc.poll() is not None:
                    break
                if self._authed.is_set() and all(_port_taken(p) for p in self.local):
                    return self.local
                time.sleep(0.05)
            alive = self.proc.poll() is None
            self.stop()
            text, code = reason(self.stderr(), self.ssh)
            if code == "port_busy" and attempt < 2:
                continue
            if alive:
                raise TunnelError(f"SSH to {where(self.ssh)} didn't finish connecting in {int(timeout)} seconds.", "timeout")
            raise TunnelError(text, code)
        raise TunnelError("No free local port for the tunnel.", "port_busy")

    def _probe_text(self) -> str:
        """Nothing offered a host key: ask ssh itself why (refused, unknown name, timeout) without logging in."""
        try:
            r = subprocess.run([_need("ssh"), "-F", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                                "-o", f"UserKnownHostsFile={os.devnull}", "-o", f"GlobalKnownHostsFile={os.devnull}",
                                "-o", "StrictHostKeyChecking=yes", "-p", str(self.ssh["port"]), "-l", self.ssh["user"], "-N",
                                "--", self.ssh["host"]], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL,
                               **_no_window())
            return r.stderr
        except (OSError, subprocess.TimeoutExpired):
            return "timed out"

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        p, self.proc = self.proc, None
        if p is None:
            return
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)


def _no_window() -> dict:
    return {"creationflags": 0x08000000} if os.name == "nt" else {}     # CREATE_NO_WINDOW: no console flashing on Windows


# ---------------------------------------------------------------- the tunnels DBaba keeps open
class Manager:
    """One tunnel per (SSH server, database address); reopened by the next use when it has died (laptop asleep, Wi-Fi)."""

    def __init__(self):
        self.known_hosts: Path | None = None
        self._lock = threading.Lock()
        self._open: dict[tuple, Tunnel] = {}

    def configure(self, known_hosts: Path):
        self.known_hosts = Path(known_hosts)

    def _kh(self) -> Path:
        if self.known_hosts is None:
            raise TunnelError("SSH isn't set up in this DBaba.", "ssh")
        return self.known_hosts

    def local_end(self, ssh: dict, host: str, port: int) -> tuple[str, int]:
        key = (ssh["host"], ssh["port"], ssh["user"], ssh.get("key_file") or "", host, int(port))
        with self._lock:
            t = self._open.get(key)
            if t is not None and t.alive():
                return "127.0.0.1", t.local[0]
            if t is not None:
                t.stop()
            t = Tunnel(ssh, self._kh(), [(host, int(port))])
            t.start()
            self._open[key] = t
            return "127.0.0.1", t.local[0]

    def why_failed(self, ssh: dict, host: str, port: int) -> tuple[str, str] | None:
        """After the database didn't answer through an open tunnel: what ssh said about it, if anything."""
        key = (ssh["host"], ssh["port"], ssh["user"], ssh.get("key_file") or "", host, int(port))
        t = self._open.get(key)
        if t is None:
            return None
        time.sleep(0.2)                              # ssh writes "open failed" a moment after the refused channel
        text, code = reason(t.stderr(), ssh, _remote(host, int(port)))
        return (text, code) if code in ("remote_db", "forwarding_forbidden") else None

    def detect(self, ssh: dict, targets=(("127.0.0.1", 3306), ("127.0.0.1", 5432))) -> list[tuple[str, int, int]]:
        """Short-lived tunnels for "Find it for me" on the SSH server: [(host, port, local port)], caller probes them."""
        t = Tunnel(ssh, self._kh(), list(targets))
        ports = t.start()
        self._probe = t
        return [(h, p, lp) for (h, p), lp in zip(targets, ports)]

    def end_detect(self):
        t = getattr(self, "_probe", None)
        if t:
            t.stop()
            self._probe = None

    def close_all(self):
        with self._lock:
            for t in self._open.values():
                t.stop()
            self._open.clear()
        self.end_detect()


manager = Manager()
atexit.register(manager.close_all)
