"""Record security events without retaining credentials or free-form content."""
import hashlib
import hmac
import ipaddress
import re
import os
import secrets
import threading
from pathlib import Path

from app.data.audit import append, local_session
from app.security.secrets import _key_material


ACTIONS = frozenset({
    "login", "logout", "login_lockout", "account_delete", "data_delete",
    "admin_action", "api_key_create", "api_key_revoke", "database_change", "settings_change",
})
ACTORS = frozenset({"anonymous", "visitor", "admin", "local_screen", "api_key", "local_setup"})
TARGETS = frozenset({
    "account", "personal_data", "screen", "database", "api_key", "setup",
    "settings", "jobs", "enquiries", "learning", "expert_requests", "admin",
})


def record(db, who, action, target, ip):
    return _record(db, who, action, target, ip, _key_material)


def _record(db, who, action, target, ip, key_material):
    """Stage a record in the caller's transaction; only fixed labels or numeric IDs.

    Never pass request bodies, credentials, key names, database addresses or
    arbitrary identifiers. Unrecognized metadata is rejected, not persisted.
    """
    if action not in ACTIONS:
        raise ValueError("Unknown audit action")
    if not isinstance(who, str) or (who not in ACTORS and not re.fullmatch(r"account:[0-9]{1,20}", who)):
        raise ValueError("Invalid audit actor")
    if not isinstance(target, str) or (target not in TARGETS and not re.fullmatch(
        r"(?:account|enquiries|learning|expert_requests):[0-9]{1,20}", target
    )):
        raise ValueError("Invalid audit target")
    try:
        address = ipaddress.ip_address(ip).compressed
    except (ValueError, TypeError):
        address = ""
    digest = hmac.new(key_material(), ("audit-ip:" + address).encode(), hashlib.sha256).hexdigest()
    append(db, who, action, target, digest)


_local_lock = threading.Lock()


def record_local(directory: Path, who, action, target, ip):
    """Keep local audit data and its independent hashing secret in the install data directory."""
    with _local_lock:
        key_path = directory / ".audit_key"
        try:
            fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as fh:
                fh.write(secrets.token_bytes(32))
                fh.flush()
                os.fsync(fh.fileno())
        key = key_path.read_bytes()
        if len(key) != 32:
            raise ValueError("Invalid local audit key")
        with local_session(directory / "audit.sqlite3") as db:
            _record(db, who, action, target, ip, lambda: key)
