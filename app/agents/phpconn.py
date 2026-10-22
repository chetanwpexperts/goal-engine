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
    pass


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
    body = json.dumps({"kind": kind, "payload": payload or {}})
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
    if r.status_code == 404 and not isinstance(out, dict):
        raise PhpError("No file at that link. Check the file was uploaded and the link is right")
    if not isinstance(out, dict) or "ok" not in out:
        raise PhpError("That link didn't answer like the Goal Engine file. Upload the downloaded file as-is, without editing it")
    if r.status_code == 403:
        raise PhpError("The file refused the request. Download the file again from this card and upload it (replace the old one)")
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
