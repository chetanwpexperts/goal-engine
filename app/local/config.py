"""dbaba.json: the one file that holds this installation's settings (docs/dbaba-local-contract.md).

The Linux installer (deploy/onprem/install.sh) and the Docker entrypoint already write it; this module reads that exact
format and never invents another:

    {"version": 1, "port": 8765,
     "screen_password": {"algorithm": "scrypt", "salt": hex, "hash": hex, "n": 16384, "r": 8, "p": 1} | null,
     "database": {"type": "mysql|mariadb|postgresql", "host", "port", "name", "user", "password", "socket"?} | null,
     "api_keys": [{"name", "hash": sha256 hex, "created"}], "updates": {...}, "alerts": {...},
     "setup_done": bool (optional: an older file is set up when it has a database), "jobs": {id: {"enabled", "at"}}}

Every write is atomic (temporary file, mode 0600, then rename), so a crash never leaves half a file. The file holds the
read-only database password the same way the installer stores it; it is readable only by DBaba's own account.
"""

import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path

ENGINES = ("mysql", "mariadb", "postgresql")
_lock = threading.RLock()


class ConfigError(ValueError):
    pass


def default(port: int) -> dict:
    return {"version": 1, "port": int(port), "screen_password": None, "database": None, "api_keys": [],
            "updates": {"enabled": True}, "alerts": {"email": None}, "setup_done": False}


class Config:
    def __init__(self, path: str | os.PathLike, port: int = 8765):
        self.path = Path(path).expanduser().resolve()
        with _lock:
            if self.path.is_symlink():
                raise ConfigError("dbaba.json must be a regular file, not a link")
            if not self.path.exists():
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._write(default(port))
            self.data = self._read()

    # ---- file
    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Can't read {self.path.name}: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ConfigError(f"{self.path.name} is not a DBaba version 1 settings file")
        return data

    def _write(self, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".dbaba-", suffix=".json")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def update(self, fn) -> dict:
        """fn(data) changes the settings in place; re-read first so another writer's change isn't lost."""
        with _lock:
            data = self._read()
            fn(data)
            self._write(data)
            self.data = data
            return data

    @property
    def data_dir(self) -> Path:
        d = self.path.parent / "data" / "local"
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        return d

    # ---- state
    @property
    def setup_done(self) -> bool:
        if "setup_done" in self.data:
            return bool(self.data["setup_done"])
        return bool(self.data.get("database"))          # a file the installer wrote with a database is set up

    @property
    def database(self) -> dict | None:
        db = self.data.get("database")
        return db if isinstance(db, dict) and db.get("type") in ENGINES else None

    # ---- the screen password (the installer's scrypt format)
    def has_password(self) -> bool:
        p = self.data.get("screen_password")
        return isinstance(p, dict) and bool(p.get("hash"))

    def check_password(self, password: str) -> bool:
        p = self.data.get("screen_password")
        if not isinstance(p, dict) or p.get("algorithm", "scrypt") != "scrypt" or not password:
            return False
        try:
            digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(p["salt"]), n=int(p["n"]), r=int(p["r"]),
                                    p=int(p["p"]), dklen=64)
        except (KeyError, ValueError, TypeError):
            return False
        return hmac.compare_digest(digest.hex(), str(p.get("hash", "")))

    @staticmethod
    def password_record(password: str) -> dict:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
        return {"algorithm": "scrypt", "salt": salt.hex(), "hash": digest.hex(), "n": 16384, "r": 8, "p": 1}

    # ---- API keys: only their SHA-256 is kept; the key itself is shown once
    def new_api_key(self, name: str) -> str:
        key = "dbk_" + secrets.token_urlsafe(32)
        rec = {"name": name[:40], "hash": hashlib.sha256(key.encode()).hexdigest(),
               "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        self.update(lambda d: d.setdefault("api_keys", []).append(rec))
        return key

    def check_api_key(self, key: str) -> bool:
        if not key or len(key) > 4096:
            return False
        h = hashlib.sha256(key.encode()).hexdigest()
        return any(isinstance(k, dict) and hmac.compare_digest(str(k.get("hash", "")), h) for k in self.data.get("api_keys") or [])
