#!/usr/bin/env bash
set -euo pipefail
umask 077
SERVICE=dbaba.service
die() { printf '%s\n' "$*" >&2; exit 1; }
if [[ $(id -u) -eq 0 ]]; then
  DIR=/opt/dbaba
  unit=/etc/systemd/system/$SERVICE
  ctl=(systemctl)
  system=1
else
  DIR=$HOME/dbaba
  unit=$HOME/.config/systemd/user/$SERVICE
  ctl=(systemctl --user)
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  system=0
fi
[[ "$DIR" =~ ^/[A-Za-z0-9_./-]+$ && "$DIR" != /dbaba ]] || die "Please use the original installation directory."
[[ ! -L "$DIR" && ! -L "$unit" ]] || die "Please use the regular installation directory and service file."
if [[ ! -d "$DIR" && ! -f "$unit" ]]; then
  echo "DBaba is already removed."
  exit 0
fi
[[ -f "$DIR/.dbaba-install" || -f "$unit" ]] || die "Please use the DBaba installation directory."
command -v flock >/dev/null || die "Please install flock before removing DBaba."
[[ ! -L "$DIR/.update-lock" ]] || die "Please use a regular update lock file."
if [[ -d "$DIR" ]]; then
  exec 9>"$DIR/.update-lock"
  flock -n 9 || die "Please finish the active DBaba update before uninstalling."
fi
if [[ -t 0 ]]; then
  exec 3<&0
elif ( : </dev/tty ) 2>/dev/null; then
  exec 3</dev/tty
else
  exec 3<&0
fi
echo "Remove DBaba from $DIR, including its saved settings and data? [y/N]"
IFS= read -r -u 3 answer || exit 0
case "$answer" in y|Y|yes|YES|Yes) ;; *) echo "DBaba is kept as it is."; exit 0 ;; esac

if [[ -f "$unit" ]]; then
  "${ctl[@]}" stop "$SERVICE"
fi

if [[ -f "$DIR/dbaba.json" ]]; then
  [[ ! -L "$DIR/dbaba.json" ]] || die "Please use a regular configuration file."
  PY=""
  for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
      PY=$(command -v "$candidate")
      break
    fi
  done
  [[ -n "$PY" ]] || die "Please install Python 3.11 or newer to review database account cleanup."
  "$PY" - "$DIR/dbaba.json" <<'PY'
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)
with os.fdopen(fd) as f:
    db = json.load(f).get("database")
if not isinstance(db, dict) or db.get("user") != "dbaba_ro":
    sys.exit(0)
if db.get("host") != "127.0.0.1":
    print("The saved database is on another host. Please ask its administrator to review the account separately.")
    sys.exit(0)
kind = db.get("type")
socket_path = db.get("socket") or ""
if not isinstance(socket_path, str) or (socket_path and not socket_path.startswith("/")):
    raise SystemExit("Please review the saved socket path before removing the account.")
if kind == "postgresql":
    if db.get("port") != 5432:
        print("Please ask your database administrator to review the account on the saved custom port.")
        sys.exit(0)
    sql = "DROP ROLE dbaba_ro;"
    client = shutil.which("psql")
    admin_socket = socket_path or next(
        (p for p in ("/run/postgresql", "/var/run/postgresql", "/tmp")
         if Path(p, ".s.PGSQL.5432").is_socket()), "/var/run/postgresql")
    prefix = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else ["sudo", "-n", "-u", "postgres"]
    cmd = prefix + [client, "-X", "-w", "-U", "postgres", "-h", admin_socket,
                    "-p", "5432", "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                    "--single-transaction", "-f", "-"]
elif kind in ("mysql", "mariadb"):
    if db.get("port") != 3306:
        print("Please ask your database administrator to review the account on the saved custom port.")
        sys.exit(0)
    host = "localhost" if socket_path else "127.0.0.1"
    sql = f"DROP USER 'dbaba_ro'@'{host}';"
    clients = ("mariadb", "mysql") if kind == "mariadb" else ("mysql", "mariadb")
    client = next((shutil.which(c) for c in clients if shutil.which(c)), None)
    prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
    cmd = prefix + [client, "--no-defaults", "--protocol=SOCKET", "--user=root",
                    "--batch", "--silent", "--connect-timeout=5"]
    if socket_path:
        cmd.append("--socket=" + socket_path)
else:
    print("Please ask your administrator to review the saved database account separately.")
    sys.exit(0)

print("Administrative access is needed only to remove the database account.")
print("SQL to run:")
print(sql)
print("Drop this database account? [y/N]", flush=True)
with os.fdopen(3, "r", closefd=False) as answers:
    approved = answers.readline().strip().lower() in ("y", "yes")
if not approved:
    print("The database account is kept. Its saved password will be removed with the installation.")
    sys.exit(0)
if not client:
    raise SystemExit("Please install the database client and run uninstall again. Your settings are kept for now.")
env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k != "MYSQL_PWD"}
try:
    if os.geteuid() != 0:
        subprocess.run(["sudo", "-v"], check=True)
    result = subprocess.run(cmd, input=sql + "\n", text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, timeout=60)
    if result.returncode:
        raise SystemExit("Please ask your administrator to review the approved SQL, then run uninstall again. Your settings are kept for now.")
except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
    raise SystemExit("Please arrange administrative database access and run uninstall again. Your settings are kept for now.")
print("Database account removed.")
PY
fi

if [[ -f "$unit" ]]; then
  "${ctl[@]}" disable "$SERVICE"
  rm -f -- "$unit"
  "${ctl[@]}" daemon-reload
  "${ctl[@]}" reset-failed "$SERVICE" >/dev/null 2>&1 || true
fi
remove_account=0
if (( system )) && [[ -f "$DIR/.system-account-created" ]]; then
  if getent passwd dbaba >/dev/null &&
     [[ "$(getent passwd dbaba | cut -d: -f6)" == "$DIR" ]] &&
     [[ "$(getent passwd dbaba | cut -d: -f7)" == */nologin ]]; then
    remove_account=1
  fi
fi
cd /
rm -rf -- "$DIR"
if (( remove_account )); then
  userdel dbaba
fi
echo "DBaba service, files and local data are removed."
if (( ! system )); then
  echo "Your user systemd session is kept for other services."
fi
