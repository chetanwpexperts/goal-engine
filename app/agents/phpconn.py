"""PHP file connector: the user uploads goal-connector-<id>.php to their hosting and pastes its link.

Goal Engine calls that file; the file finds the database settings next to it (wp-config.php, .env, ...),
so the password never leaves their hosting. Every call is signed with the file's own key, carries a
timestamp (valid 2 minutes) and a one-time nonce. Only public https addresses are called, and
redirects are re-checked before following.
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from sqlalchemy.orm import Session

from app.agents import direct
from app.models import Connector, utcnow

TEMPLATE = Path(__file__).resolve().parents[2] / "connector" / "goal-connector.php"
MAX_RESPONSE = 32 * 1024 * 1024


class PhpError(direct.DirectError):
    def __init__(self, message: str, missing: bool = False):
        super().__init__(message)
        self.missing = missing   # the file is gone or not answering like the connector (e.g. removed by a security scanner)


def _aes_key(key: str) -> bytes:
    return hashlib.sha256(("goal-engine-transport:" + key).encode()).digest()


def seal(key: str, data: dict) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import base64

    iv = secrets.token_bytes(12)
    return base64.b64encode(iv + AESGCM(_aes_key(key)).encrypt(iv, json.dumps(data).encode(), None)).decode()


def unseal(key: str, sealed: str) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import base64

    raw = base64.b64decode(sealed)
    return json.loads(AESGCM(_aes_key(key)).decrypt(raw[:12], raw[12:], None))


def create(db: Session, visitor_id: str, name: str = "") -> Connector:
    from app.agents import database as dbagent

    connector, _ = dbagent.create_connector(db, visitor_id, name or "Website database")
    connector.kind, connector.token_hint, connector.status = "php", "php", "waiting"
    connector.secret = direct.encrypt({"key": secrets.token_urlsafe(32), "url": ""})
    db.commit()
    return connector


def file_name(connector: Connector) -> str:
    # unguessable name, so the file isn't easy to find; the signature is what actually protects it
    return f"goal-connector-{connector.id}-{hashlib.sha256((connector.token_hash or '').encode()).hexdigest()[:10]}.php"


def file_text(connector: Connector) -> str:
    key = direct.decrypt(connector.secret or "")["key"]
    return TEMPLATE.read_text().replace("__CONNECTOR_ID__", str(connector.id)).replace("__KEY__", key)


def _check_url(url: str) -> str:
    url = (url or "").strip().strip("<>\"'")
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    allow_http = os.getenv("PHP_CONNECTOR_ALLOW_HTTP") == "1"
    if p.scheme != "https" and not (allow_http and p.scheme == "http"):
        raise PhpError("Use the https:// link of the file (your site needs SSL, which almost every hosting gives free)")
    if not p.hostname or not p.path.endswith(".php"):
        raise PhpError("Paste the full link of the uploaded file, ending in .php")
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError as exc:
        raise PhpError("That link doesn't look right") from exc
    if port not in (80, 443) and not allow_http:
        raise PhpError("Use the normal link of the file, without a port number")
    direct.public_ip(p.hostname, port)          # public addresses only, never our own server
    return url


def call(url: str, key: str, kind: str, payload: dict | None = None, timeout: int = 100) -> dict:
    body = json.dumps({"sealed": seal(key, {"kind": kind, "payload": payload or {}})})   # firewalls see no SQL
    for _ in range(3):                     # follow up to 2 redirects (http->https, www), each one re-checked
        url = _check_url(url)
        ts, nonce = str(int(time.time())), secrets.token_urlsafe(18)
        sig = hmac.new(key.encode(), f"{ts}\n{nonce}\n{body}".encode(), hashlib.sha256).hexdigest()
        try:
            r = requests.post(url, data=body, timeout=(10, timeout), allow_redirects=False, stream=True,
                              headers={"Content-Type": "application/json", "X-Goal-Time": ts, "X-Goal-Nonce": nonce,
                                       "X-Goal-Signature": sig, "User-Agent": "GoalEngine/1.0"})
        except requests.Timeout as exc:
            raise PhpError("Your website took too long to answer") from exc
        except requests.RequestException as exc:
            raise PhpError("Couldn't reach that link. Check it opens in your browser") from exc
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            url = urljoin(url, r.headers["Location"])
            continue
        break
    else:
        raise PhpError("That link keeps redirecting")
    data = b""
    for chunk in r.iter_content(65536):
        data += chunk
        if len(data) > MAX_RESPONSE:
            raise PhpError("The answer was too big")
    try:
        out = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        out = None
    if r.status_code in (404, 410) and not isinstance(out, dict):
        raise PhpError("No file at that link. It was removed (often by the hosting's security scanner) or the link is wrong", missing=True)
    if not isinstance(out, dict) or "ok" not in out:
        if r.status_code in (403, 406, 501):
            raise PhpError("The hosting's firewall blocked the request. Add the file to its allow list, then try again", missing=True)
        raise PhpError("That link didn't answer like the Goal Engine file. Upload the downloaded file as-is, without editing it", missing=True)
    if r.status_code == 403:
        raise PhpError("The file refused the request. Download the file again from this card and upload it (replace the old one)", missing=True)
    if "sealed" in out:
        try:
            out = unseal(key, out["sealed"])
        except Exception as exc:  # noqa: BLE001
            raise PhpError("The answer couldn't be verified. Download the file again and replace the old one", missing=True) from exc
    return out


def link(db: Session, connector: Connector, url: str) -> Connector:
    from app.agents import database as dbagent

    secret = direct.decrypt(connector.secret or "")
    url = _check_url(url)
    out = call(url, secret["key"], "hello", timeout=30)
    if not out.get("ok"):
        raise PhpError(out.get("error") or "The file couldn't open your database")
    info = out.get("result") or {}
    secret["url"] = url
    connector.secret = direct.encrypt(secret)
    connector.host_label = urlparse(url).hostname
    connector.db_type = str(info.get("db_type", ""))[:20]
    connector.db_name = str(info.get("db_name", ""))[:120]
    connector.version = str(info.get("db_version", ""))[:20]
    connector.can_write = bool(info.get("can_write"))
    connector.status, connector.last_seen_at = "online", utcnow()
    db.commit()
    dbagent.queue(db, connector, "schema")
    return connector


def run(connector: Connector, kind: str, payload: dict) -> dict:
    secret = direct.decrypt(connector.secret or "")
    if not secret.get("url"):
        raise PhpError("Paste the link of the uploaded file first")
    out = call(secret["url"], secret["key"], kind, payload)
    if not out.get("ok"):
        raise PhpError(out.get("error") or "The file couldn't run that")
    result = out.get("result") or {}
    if out.get("csv") is not None:
        result["csv"] = out["csv"]
    return result


def check_all(db: Session) -> int:
    """Every few hours: is each website file still there? (Security scanners sometimes remove it.)"""
    from sqlalchemy import select

    changed = 0
    for c in db.scalars(select(Connector).where(Connector.kind == "php", Connector.status.in_(("online", "missing")))):
        try:
            secret = direct.decrypt(c.secret or "")
            if not secret.get("url"):
                continue
            out = call(secret["url"], secret["key"], "hello", timeout=30)
            status = "online" if out.get("ok") else "missing"
        except PhpError as exc:
            status = "missing" if exc.missing else c.status
        except Exception:  # noqa: BLE001 - network blip: leave as is
            continue
        if status != c.status:
            c.status, changed = status, changed + 1
        if status == "online":
            c.last_seen_at = utcnow()
    db.commit()
    return changed


def file_url(connector: Connector) -> str:
    try:
        return direct.decrypt(connector.secret or "").get("url", "")
    except Exception:  # noqa: BLE001
        return ""
