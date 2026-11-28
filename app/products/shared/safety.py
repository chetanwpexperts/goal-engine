"""Safe fetching of user-supplied URLs.

Users type any address, so the server must never be tricked into calling itself or a
private network (SSRF). Every hop, including redirects, is checked before connecting.
"""

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests


USER_AGENT = "Mozilla/5.0 (compatible; GoalEngineSiteCheck/1.0; +https://solve.chetanbuilds.com)"
MAX_BYTES = 1_500_000
MAX_REDIRECTS = 5


class UnsafeURL(ValueError):
    pass


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip().strip("<>\"'")
    if not raw:
        raise UnsafeURL("Website address is empty")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURL("That doesn't look like a website address") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURL("Only normal website addresses (http/https) can be checked")
    if port not in (None, 80, 443):
        raise UnsafeURL("Only standard web ports can be checked")
    host = parsed.hostname.lower().rstrip(".")
    if "." not in host or host.endswith((".local", ".internal", ".localhost")):
        raise UnsafeURL("That doesn't look like a public website address")
    return f"{parsed.scheme}://{host}{parsed.path or '/'}" + (f"?{parsed.query}" if parsed.query else "")


def assert_public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURL("domain does not resolve") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified or (ip.version == 6 and ip.ipv4_mapped and not ip.ipv4_mapped.is_global)):
            raise UnsafeURL("That address points to a private network and can't be checked")


@dataclass
class Page:
    url: str
    final_url: str = ""
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    seconds: float = 0.0
    redirects: list[str] = field(default_factory=list)
    error: str = ""           # dns / ssl / timeout / connect / unsafe / other
    error_detail: str = ""


def fetch(url: str, *, verify: bool = True, timeout: float = 12.0, resolve=assert_public_host) -> Page:
    page = Page(url=url)
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            resolve(urlparse(current).hostname or "")
            response = requests.get(current, timeout=timeout, verify=verify, allow_redirects=False, stream=True,
                                    headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
            page.seconds += response.elapsed.total_seconds()
            if response.is_redirect and response.headers.get("location"):
                nxt = urljoin(current, response.headers["location"])
                if urlparse(nxt).scheme not in ("http", "https"):
                    raise UnsafeURL("redirect to a non-web address")
                page.redirects.append(nxt)
                response.close()
                current = nxt
                continue
            page.status = response.status_code
            page.final_url = current
            page.headers = {k.lower(): v for k, v in response.headers.items()}
            ctype = page.headers.get("content-type", "text/html")
            if any(t in ctype for t in ("html", "csv", "text/plain", "json")):
                raw = response.raw.read(MAX_BYTES, decode_content=True)
                page.text = raw.decode(response.encoding or "utf-8", errors="replace")
            response.close()
            return page
        page.error, page.error_detail = "other", "too many redirects"
    except UnsafeURL as exc:
        page.error, page.error_detail = ("dns" if "resolve" in str(exc) else "unsafe"), str(exc)
    except requests.exceptions.SSLError as exc:
        text = str(exc).lower()
        reason = ("certificate has expired" if "expired" in text else
                  "certificate is for a different domain" if "hostname" in text or "match" in text else
                  "certificate could not be verified")
        page.error, page.error_detail = "ssl", reason
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
        page.error, page.error_detail = "timeout", "no response within time limit"
    except requests.exceptions.ConnectionError:
        page.error, page.error_detail = "connect", "connection refused or reset"
    except requests.RequestException as exc:
        page.error, page.error_detail = "other", str(exc)[:200]
    return page
