#!/usr/bin/env bash
# curl -fsSL __SERVER__/dbaba/install.sh | bash
set -euo pipefail
umask 077
SERVER="${DBABA_SERVER:-__SERVER__}"
SERVICE=dbaba.service
die() { printf '%s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

[[ -r /etc/os-release ]] || die "Please use a supported Linux distribution."
. /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
  ubuntu:22.04) python_help="sudo apt-get update && sudo apt-get install -y python3.11 python3.11-venv" ;;
  ubuntu:24.04|debian:12) python_help="sudo apt-get update && sudo apt-get install -y python3 python3-venv" ;;
  almalinux:9|almalinux:9.*|rocky:9|rocky:9.*) python_help="sudo dnf install -y python3.11 python3.11-pip" ;;
  *) die "Please use Ubuntu 22.04/24.04, Debian 12, AlmaLinux 9 or Rocky Linux 9." ;;
esac
PY=""
for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    PY=$(command -v "$candidate")
    break
  fi
done
[[ -n "$PY" ]] || die "Python 3.11 or newer is needed. Run: $python_help"
"$PY" -c 'import venv, ensurepip' >/dev/null 2>&1 || die "Python virtual environment support is needed. Run: $python_help"
for tool in curl systemctl; do
  command -v "$tool" >/dev/null || die "Please install $tool and run the installer again."
done

system=0
owner=$(id -un)
if [[ $(id -u) -eq 0 ]]; then
  system=1
  DIR=/opt/dbaba
  owner=dbaba
  unit=/etc/systemd/system/$SERVICE
  ctl=(systemctl)
  command -v runuser >/dev/null || die "Please install util-linux."
  systemctl show-environment >/dev/null || die "Please start systemd on this server first."
else
  DIR=$HOME/dbaba
  unit=$HOME/.config/systemd/user/$SERVICE
  ctl=(systemctl --user)
  command -v loginctl >/dev/null || die "Please install systemd-logind."
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  loginctl enable-linger "$owner" || die "Please ask your administrator to run: sudo loginctl enable-linger $owner"
  "${ctl[@]}" show-environment >/dev/null || die "Please log in through SSH to start your user systemd session, then retry."
fi
[[ "$DIR" =~ ^/[A-Za-z0-9_./-]+$ && "$DIR" != /dbaba ]] || die "Please use a home directory with a simple absolute path."
[[ ! -L "$DIR" && ! -L "$unit" ]] || die "Please use a regular installation directory and service file."

# Downloaded scripts use the terminal; local test scripts can read piped answers.
if [[ -t 0 ]]; then
  exec 3<&0
elif ( : </dev/tty ) 2>/dev/null; then
  exec 3</dev/tty
elif [[ -n "${DBABA_SRC:-}" ]]; then
  exec 3<&0
else
  die "Please run the download command in an interactive terminal."
fi

stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
if [[ -n "${DBABA_SRC:-}" ]]; then
  [[ -d "$DBABA_SRC" ]] || die "Please set DBABA_SRC to a release or repository directory."
  source_kind=directory
  source_path=$(cd "$DBABA_SRC" && pwd -P)
else
  [[ "$SERVER" == https://* ]] || die "Please use the published HTTPS installer URL."
  say "Downloading the DBaba release."
  curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --retry 2 --connect-timeout 15 --max-time 600 "${SERVER%/}/dbaba/dbaba.tar.gz" -o "$stage/release.tar.gz"
  source_kind=archive
  source_path=$stage/release.tar.gz
fi

# Stage regular files and directories; omit local configuration and reject links.
"$PY" - "$source_kind" "$source_path" "$stage/source" <<'PY'
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath

kind, source, output = sys.argv[1:]
out = Path(output)
out.mkdir()
ignored = {".git", ".github", ".venv", "__pycache__", ".pytest_cache",
           ".mypy_cache", ".ruff_cache", "node_modules"}
keep = {"data", "dbaba.json", ".dbaba-install", ".system-account-created"}

def skip(parts):
    return any(p in ignored or p == ".env" or p.startswith(".env.") for p in parts) or (
        bool(parts) and parts[0] in keep)

def copy_tree(src, dest, relative=()):
    for item in src.iterdir():
        parts = (*relative, item.name)
        if skip(parts):
            continue
        target = dest / item.name
        if item.is_symlink():
            raise SystemExit("Please use a release containing regular files and directories.")
        if item.is_dir():
            target.mkdir()
            copy_tree(item, target, parts)
        elif item.is_file():
            shutil.copyfile(item, target)
            target.chmod(0o755 if item.stat().st_mode & 0o111 else 0o644)
        else:
            raise SystemExit("Please use a release containing regular files and directories.")

if kind == "directory":
    src = Path(source)
    if src in out.resolve().parents:
        raise SystemExit("Please choose a source outside the temporary directory.")
    copy_tree(src, out)
else:
    with tarfile.open(source, "r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise SystemExit("Please use a release with safe relative paths.")
            if not path.parts or skip(path.parts):
                continue
            if not member.isdir() and not member.isfile():
                raise SystemExit("Please use a release containing regular files and directories.")
            target = out.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as src, target.open("wb") as dest:
                    shutil.copyfileobj(src, dest)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    if not (out / "requirements.txt").is_file():
        children = list(out.iterdir())
        if len(children) == 1 and children[0].is_dir():
            wrapped = children[0]
            for item in list(wrapped.iterdir()):
                item.rename(out / item.name)
            wrapped.rmdir()
    for item in list(out.iterdir()):
        if skip((item.name,)):
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
for name in ("requirements.txt", "app/main.py", "deploy/onprem/dbaba.service",
             "deploy/onprem/uninstall.sh"):
    if not (out / name).is_file():
        raise SystemExit(f"The release needs {name}. Please use a complete DBaba release.")
PY

as_owner() {
  if (( system )); then
    runuser -u "$owner" -- "$@"
  else
    "$@"
  fi
}

if [[ -d "$DIR" && ! -f "$DIR/.dbaba-install" && -n "$(ls -A "$DIR")" ]]; then
  die "The installation directory contains other files. Please move them before installing."
fi
[[ ! -L "$DIR/data" && ! -L "$DIR/dbaba.json" && ! -L "$DIR/.venv" ]] ||
  die "Please use regular data, configuration and environment paths."
created_account=0
if (( system )); then
  if getent passwd "$owner" >/dev/null; then
    account_home=$(getent passwd "$owner" | cut -d: -f6)
    account_shell=$(getent passwd "$owner" | cut -d: -f7)
    [[ "$account_home" == "$DIR" && "$account_shell" == */nologin ]] ||
      die "The dbaba system account already has another purpose. Please choose a clean server."
  else
    useradd --system --user-group --home-dir "$DIR" --shell /usr/sbin/nologin "$owner"
    created_account=1
  fi
  install -d -m 700 -o "$owner" -g "$(id -gn "$owner")" "$DIR"
  # The root-owned staging tree is readable, never writable, by the service account.
  chmod 711 "$stage"
  chmod -R a+rX "$stage/source"
else
  mkdir -p "$DIR"
  chmod 700 "$DIR"
fi
as_owner touch "$DIR/.dbaba-install"
if (( created_account )); then
  as_owner touch "$DIR/.system-account-created"
fi
as_owner mkdir -p "$DIR/data"
as_owner chmod 700 "$DIR/data"

if [[ -f "$DIR/dbaba.json" ]]; then
  say "Keeping your existing configuration and data."
  as_owner chmod 600 "$DIR/dbaba.json"
  port=$(as_owner "$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["port"])' "$DIR/dbaba.json")
else
  say "Screen port [8765]:"
  IFS= read -r -u 3 port || die "Please enter a port."
  port="${port:-8765}"
fi
[[ "$port" =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1024 && 10#$port <= 65535 )) ||
  die "Please choose a port from 1024 to 65535."
port=$((10#$port))

if [[ -f "$unit" ]]; then
  "${ctl[@]}" stop "$SERVICE"
fi

"$PY" - "$port" <<'PY'
import socket, sys
try:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit("Please choose a screen port that is available on this server.")
PY

if [[ ! -f "$DIR/dbaba.json" ]]; then
  say "Choose a screen password (at least 8 characters):"
  IFS= read -rs -u 3 screen || die "Please enter a screen password."
  say ""
  say "Enter the screen password again:"
  IFS= read -rs -u 3 confirm || die "Please enter the screen password again."
  say ""
  [[ "$screen" == "$confirm" && ${#screen} -ge 8 ]] || die "Please enter matching passwords of at least 8 characters."

  printf '%s' "$screen" | as_owner "$PY" -c '
import hashlib, json, os, secrets, sys, tempfile
from pathlib import Path
path = Path(sys.argv[1])
salt = secrets.token_bytes(16)
digest = hashlib.scrypt(sys.stdin.buffer.read(), salt=salt, n=16384, r=8, p=1, dklen=64)
config = {"version": 1, "port": int(sys.argv[2]),
          "screen_password": {"algorithm": "scrypt", "salt": salt.hex(), "hash": digest.hex(),
                              "n": 16384, "r": 8, "p": 1},
          "database": None, "api_keys": [], "updates": {"enabled": True},
          "alerts": {"email": None}}
with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
    os.fchmod(f.fileno(), 0o600)
    json.dump(config, f, indent=2)
    f.write("\n")
    tmp = f.name
os.replace(tmp, path)
' "$DIR/dbaba.json" "$port"
  unset screen confirm

  # Detection uses local TCP connections and socket file checks, not SQL.
  "$PY" - <<'PY'
import shutil, socket
from pathlib import Path
for label, port, clients, sockets in (
    ("MySQL / MariaDB", 3306, ("mysql", "mariadb"),
     ("/run/mysqld/mysqld.sock", "/var/lib/mysql/mysql.sock", "/tmp/mysql.sock")),
    ("PostgreSQL", 5432, ("psql",),
     ("/run/postgresql/.s.PGSQL.5432", "/tmp/.s.PGSQL.5432")),
):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            listening = True
    except OSError:
        listening = False
    available = ", ".join(c for c in clients if shutil.which(c)) or "install a client to create an account"
    found = ", ".join(s for s in sockets if Path(s).is_socket()) or "none found"
    print(f"{label}: localhost port {port} {'open' if listening else 'not detected'}; clients: {available}; sockets: {found}")
PY
  say "Choose your database: 1) MySQL  2) MariaDB  3) PostgreSQL  0) Set up later [0]"
  IFS= read -r -u 3 choice || die "Please choose a database or enter 0."
  case "${choice:-0}" in
    0) say "You can add a database later." ;;
    1|2|3)
      if [[ "$choice" == 3 ]]; then
        dbtype=postgresql; dbport=5432
        client=$(command -v psql || true)
        default_name=postgres
      else
        dbtype=mysql; [[ "$choice" == 2 ]] && dbtype=mariadb
        dbport=3306; default_name=""
        if [[ "$dbtype" == mariadb ]]; then
          client=$(command -v mariadb || command -v mysql || true)
        else
          client=$(command -v mysql || command -v mariadb || true)
        fi
      fi
      if [[ -z "$client" ]]; then
        say "Please install the local database client, then configure the database in DBaba."
      else
        say "Database name${default_name:+ [$default_name]}:"
        IFS= read -r -u 3 dbname || die "Please enter the database name."
        dbname="${dbname:-$default_name}"
        [[ -n "$dbname" && ${#dbname} -le 128 ]] || die "Please enter a database name up to 128 characters."
        if [[ "$dbtype" == postgresql ]]; then
          say "Unix socket directory for DBaba (blank for TCP):"
        else
          say "Unix socket file for DBaba (blank for TCP):"
        fi
        IFS= read -r -u 3 dbsocket || die "Please enter a socket path or leave it blank."
        [[ -z "$dbsocket" || "$dbsocket" == /* ]] || die "Please enter an absolute socket path."
        say "Administrative access is needed only to create the read-only database account."
        "$PY" - "$DIR/dbaba.json" "$dbtype" "$dbname" "$dbport" "$dbsocket" "$client" "$owner" <<'PY'
import base64
import hashlib
import hmac
import json
import os
import pwd
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

config_path, kind, name, port, socket_path, client, owner = sys.argv[1:]
port = int(port)
config_path = Path(config_path)
account = pwd.getpwnam(owner)
password = secrets.token_urlsafe(32)
db = {"type": kind, "host": "127.0.0.1", "port": port,
      "name": name, "user": "dbaba_ro", "password": password}
if socket_path:
    db["socket"] = socket_path

def persist():
    fd = os.open(config_path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        config = json.load(f)
    config["database"] = db
    with tempfile.NamedTemporaryFile(mode="w", dir=config_path.parent, delete=False) as f:
        os.fchmod(f.fileno(), 0o600)
        if os.geteuid() == 0:
            os.fchown(f.fileno(), account.pw_uid, account.pw_gid)
        json.dump(config, f, indent=2)
        f.write("\n")
        tmp = f.name
    os.replace(tmp, config_path)

if kind == "postgresql":
    # Send a SCRAM verifier instead of the clear password to the server.
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 4096)
    client_key = hmac.digest(salted, b"Client Key", "sha256")
    stored = hashlib.sha256(client_key).digest()
    server_key = hmac.digest(salted, b"Server Key", "sha256")
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    verifier = "SCRAM-SHA-256$4096:" + b64(salt) + "$" + b64(stored) + ":" + b64(server_key)
    create = ("CREATE ROLE dbaba_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
              "NOREPLICATION NOBYPASSRLS PASSWORD '<generated password verifier>';")
    actual = create.replace("<generated password verifier>", verifier)
    grants = ("GRANT pg_read_all_data, pg_monitor TO dbaba_ro;\n"
              "ALTER ROLE dbaba_ro SET default_transaction_read_only = on;")
    admin_socket = socket_path or next(
        (p for p in ("/run/postgresql", "/var/run/postgresql", "/tmp")
         if Path(p, ".s.PGSQL.5432").is_socket()), "/var/run/postgresql")
    prefix = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else ["sudo", "-n", "-u", "postgres"]
    cmd = prefix + [client, "-X", "-w", "-U", "postgres", "-h", admin_socket,
                    "-p", str(port), "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                    "--single-transaction", "-f", "-"]
    print("PostgreSQL read and monitoring roles cover the cluster. Existing PUBLIC privileges also apply.")
else:
    host = "localhost" if socket_path else "127.0.0.1"
    user = f"'dbaba_ro'@'{host}'"
    create = f"CREATE USER {user} IDENTIFIED BY '<generated password>';"
    actual = create.replace("<generated password>", password)
    grants = f"GRANT SELECT, PROCESS, SHOW VIEW, REPLICATION CLIENT ON *.* TO {user};"
    prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
    cmd = prefix + [client, "--no-defaults", "--protocol=SOCKET", "--user=root",
                    "--batch", "--silent", "--connect-timeout=5"]
    if socket_path:
        cmd.append("--socket=" + socket_path)
    print("These read and monitoring privileges cover all databases on this server.")

print("SQL to run (the generated password or verifier is kept private):")
print(create)
print(grants)
print("Create this read-only database account? [y/N]", flush=True)
with os.fdopen(3, "r", closefd=False) as answers:
    approved = answers.readline().strip().lower() in ("y", "yes")
if not approved:
    print("You can set up the database later.")
    sys.exit(0)
if os.geteuid() != 0:
    try:
        subprocess.run(["sudo", "-v"], check=True)
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit("Please arrange administrative database access and configure the database later.")

# SQL goes through standard input. Keep client output private because an error
# could include a generated credential.
env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k != "MYSQL_PWD"}
def execute(sql):
    try:
        result = subprocess.run(cmd, input=sql + "\n", text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, timeout=60)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

if kind == "postgresql":
    if not execute(actual + "\n" + grants):
        raise SystemExit("Please ask your administrator to review the approved SQL (PostgreSQL 14+ is required).")
    persist()
else:
    if not execute(actual):
        raise SystemExit("Please check administrative socket access and whether dbaba_ro already exists.")
    # Save the new account before granting privileges so cleanup remains possible.
    persist()
    if not execute(grants):
        raise SystemExit("The account is saved in dbaba.json. Please ask your administrator to review the approved grants before using it.")
print("Read-only database account created. Its password is saved in dbaba.json.")
PY
      fi
      ;;
    *) die "Please enter 0, 1, 2 or 3." ;;
  esac
fi

say "Installing the release in $DIR."
# Replace code as the service owner. Retain persistent files and markers.
as_owner "$PY" - "$stage/source" "$DIR" <<'PY'
import shutil
import sys
from pathlib import Path
src, dst = map(Path, sys.argv[1:])
keep = {"data", "dbaba.json", ".venv", ".dbaba-install", ".system-account-created"}
for item in dst.iterdir():
    if item.name in keep:
        continue
    if item.is_symlink() or not item.is_dir():
        item.unlink()
    else:
        shutil.rmtree(item)
for item in src.iterdir():
    if item.name in keep:
        continue
    target = dst / item.name
    if item.is_dir():
        shutil.copytree(item, target)
    else:
        shutil.copy2(item, target)
PY

as_owner rm -rf -- "$DIR/.venv"
as_owner "$PY" -m venv "$DIR/.venv" || die "Please install Python virtual environment support: $python_help"
as_owner "$DIR/.venv/bin/python" -m pip --isolated install --disable-pip-version-check --no-input -r "$DIR/requirements.txt"

fallback=0
if [[ -f "$DIR/app/local.py" ]]; then
  command_line="$DIR/.venv/bin/python -m app.local serve --port $port"
else
  fallback=1
  command_line="$DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $port"
  say "Important: this release's screen has no password protection yet. Use the SSH tunnel only, and keep the server accessible to trusted users."
fi

if (( system )); then
  service_user="User=$owner"
  private_tmp=yes
  saved_socket=$(as_owner "$PY" -c 'import json,sys; print((json.load(open(sys.argv[1])).get("database") or {}).get("socket") or "")' "$DIR/dbaba.json")
  case "$saved_socket" in /tmp|/tmp/*|/var/tmp|/var/tmp/*) private_tmp=no ;; esac
  hardening=$(printf 'PrivateTmp=%s\nProtectSystem=strict\nReadWritePaths=%s\nProtectHome=read-only' "$private_tmp" "$DIR")
  target=multi-user.target
else
  service_user=""
  hardening=""
  target=default.target
fi
"$PY" - "$stage/source/deploy/onprem/dbaba.service" "$stage/rendered.service" \
  "$DIR" "$command_line" "$service_user" "$hardening" "$target" <<'PY'
import sys
from pathlib import Path
template, output, *values = sys.argv[1:]
text = Path(template).read_text()
for key, value in zip(("DIR", "COMMAND", "SERVICE_USER", "SYSTEM_HARDENING", "TARGET"), values):
    text = text.replace("@" + key + "@", value)
Path(output).write_text(text)
PY
mkdir -p "$(dirname "$unit")"
install -m 644 "$stage/rendered.service" "$unit"
"${ctl[@]}" daemon-reload
"${ctl[@]}" enable "$SERVICE"
"${ctl[@]}" restart "$SERVICE"

say "Waiting for DBaba to start (up to 3 minutes)."
healthy=0
deadline=$((SECONDS + 180))
while (( SECONDS < deadline )); do
  if "${ctl[@]}" is-active --quiet "$SERVICE" &&
    curl --noproxy '*' --fail --silent --show-error --max-time 2 \
      "http://127.0.0.1:$port/api/health" 2>/dev/null |
      "$PY" -c 'import json,sys; sys.exit(json.load(sys.stdin).get("ok") is not True)' 2>/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done
if (( ! healthy )); then
  if (( system )); then
    die "Please check the service log: sudo journalctl -u dbaba.service -n 50"
  else
    die "Please check the service log: journalctl --user -u dbaba.service -n 50"
  fi
fi

# Verify the actual listening sockets as well as the HTTP response.
if ! "$PY" - "$port" <<'PY'
import socket, struct, sys
from pathlib import Path
port = int(sys.argv[1])
found = False
for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
    path = Path(filename)
    if not path.exists():
        continue
    for line in path.read_text().splitlines()[1:]:
        fields = line.split()
        address, number = fields[1].split(":")
        if fields[3] != "0A" or int(number, 16) != port:
            continue
        if filename.endswith("tcp6") or socket.inet_ntoa(struct.pack("=I", int(address, 16))) != "127.0.0.1":
            sys.exit(1)
        found = True
sys.exit(not found)
PY
then
  "${ctl[@]}" stop "$SERVICE"
  die "Please review the release's listener configuration. DBaba requires 127.0.0.1 only."
fi
say "DBaba is running and will start automatically after a reboot."
if (( fallback )); then
  say "Important: screen authentication will be available in a later release. Use the SSH tunnel; other local server users can reach the loopback port."
fi
say "From your laptop, open an SSH tunnel (replace user and server):"
say "  ssh -L $port:127.0.0.1:$port user@server"
say "Then open http://127.0.0.1:$port in your browser."
