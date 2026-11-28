"""Keys, encryption and one-way fingerprints (rule 2 of app/services privacy, docs/architecture.md "Security").

Lives in the security layer so products can encrypt what they keep (database passwords, pasted customer lists, undo
records) without reaching up into services. app/privacy.py re-exports these names for its own callers.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

KEY_FILE = Path(__file__).resolve().parents[2] / "data" / ".secret_key"


# ---------------------------------------------------------------- encryption
def _key_material() -> bytes:
    secret = os.getenv("APP_SECRET", "")
    if len(secret) >= 16:
        return secret.encode()
    # No APP_SECRET: keep a random key on disk, readable only by the app
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(48))
    return KEY_FILE.read_text().strip().encode()


def _fernet(purpose: str):
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(purpose.encode() + b":" + _key_material()).digest()))


def encrypt(data, purpose: str = "data") -> str:
    return _fernet(purpose).encrypt(json.dumps(data).encode()).decode()


def decrypt(token: str, purpose: str = "data"):
    return json.loads(_fernet(purpose).decrypt(token.encode()))


def fingerprint(value: str) -> str:
    """One-way: lets us say 'already emailed today' without keeping the address."""
    return "h:" + hmac.new(_key_material(), value.strip().lower().encode(), hashlib.sha256).hexdigest()[:40]


# ---------------------------------------------------------------- pasted customer lists (encrypted at rest)
def set_rows(inputs: dict, text: str) -> None:
    inputs.pop("csv_text", None)
    inputs["csv_enc"] = encrypt(text, "rows") if text else ""


def get_rows(inputs: dict) -> str:
    if inputs.get("csv_enc"):
        try:
            return decrypt(inputs["csv_enc"], "rows")
        except Exception:  # noqa: BLE001 - key changed: treat as not added
            return ""
    return inputs.get("csv_text", "")    # rows saved before encryption
