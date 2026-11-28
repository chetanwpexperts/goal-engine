"""Record security events without retaining credentials or free-form content."""
import hashlib
import hmac
import ipaddress
import re

from app.data.audit import append
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
    digest = hmac.new(_key_material(), ("audit-ip:" + address).encode(), hashlib.sha256).hexdigest()
    append(db, who, action, target, digest)
