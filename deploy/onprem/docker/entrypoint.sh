#!/bin/sh
set -eu
umask 077
cd /opt/dbaba
printf '%s\n' "Publish this port only on 127.0.0.1 (e.g. -p 127.0.0.1:8765:8765); never on all interfaces." >&2

PORT=$(python - <<'PY'
import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path

path = Path("/data/dbaba.json")

def port(value):
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise SystemExit("Please enter a valid port from 1024 to 65535.")
    if not 1024 <= number <= 65535:
        raise SystemExit("Please enter a valid port from 1024 to 65535.")
    return number

if path.is_symlink():
    raise SystemExit("Please use a regular dbaba.json file.")
if not path.exists():
    password = os.environ.get("DBABA_PASSWORD", "")
    if len(password) < 8:
        raise SystemExit("Please set DBABA_PASSWORD to at least 8 characters for the first start.")
    screen_port = port(os.environ.get("DBABA_PORT") or "8765")
    kind = os.environ.get("DBABA_DB_TYPE", "").lower()
    database = None
    if kind:
        if kind not in ("mysql", "mariadb", "postgresql"):
            raise SystemExit("Please choose mysql, mariadb or postgresql for DBABA_DB_TYPE.")
        name = os.environ.get("DBABA_DB_NAME", "")
        user = os.environ.get("DBABA_DB_USER", "")
        if not name or not user:
            raise SystemExit("Please provide the database name and read-only user.")
        database = {
            "type": kind,
            "host": os.environ.get("DBABA_DB_HOST") or "host.docker.internal",
            "port": port(os.environ.get("DBABA_DB_PORT") or ("5432" if kind == "postgresql" else "3306")),
            "name": name,
            "user": user,
            "password": os.environ.get("DBABA_DB_PASSWORD", ""),
        }
    elif any(os.environ.get("DBABA_DB_" + key) for key in ("HOST", "PORT", "NAME", "USER", "PASSWORD")):
        raise SystemExit("Please set DBABA_DB_TYPE to configure a database.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
    config = {
        "version": 1, "port": screen_port,
        "screen_password": {"algorithm": "scrypt", "salt": salt.hex(), "hash": digest.hex(),
                            "n": 16384, "r": 8, "p": 1},
        "database": database, "api_keys": [], "updates": {"enabled": True},
        "alerts": {"email": None},
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            os.fchmod(f.fileno(), 0o600)
            json.dump(config, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        # Publish without overwriting another process's configuration.
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
try:
    with path.open() as f:
        config = json.load(f)
    if config["version"] != 1:
        raise ValueError
    print(port(config["port"]))
except (OSError, ValueError, KeyError, TypeError):
    raise SystemExit("Please check the saved DBaba configuration.")
PY
)
unset DBABA_PASSWORD DBABA_DB_PASSWORD
export PORT
# Keep local mode and disabled providers even if runtime env overrides image defaults.
export SITE_MODE=open ENGINE_MODE=ml AGENT_SCHEDULER=on DBABA_LOCAL=1
export COOKIE_SECURE=false EXPOSE_INTERNALS=0 DATABASE_URL=sqlite:////data/dbaba.db
export GEMINI_API_KEY='' GROQ_API_KEY='' AI_PROVIDER_ORDER='' UVICORN_HOST=0.0.0.0
if python -c "import app.local" >/dev/null 2>&1; then
    exec python -m app.local serve --port "$PORT" --config /data/dbaba.json --host 0.0.0.0
fi
printf '%s\n' "This image has no screen password yet; publish it only on 127.0.0.1." >&2
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
