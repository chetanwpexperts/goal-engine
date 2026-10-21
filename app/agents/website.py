"""Website Checker agent: real checks on a public website, explained in plain words.

Every finding is backed by evidence the owner can verify. If a check can't prove a
problem, it reports nothing.
"""

import re
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.agents.safety import Page, UnsafeURL, fetch, normalize_url


WP_RE = re.compile(r"WordPress\s+(\d+(?:\.\d+){0,2})", re.I)
PHP_RE = re.compile(r"PHP/(\d+(?:\.\d+){0,2})", re.I)
COPYRIGHT_RE = re.compile(r"(?:©|&copy;|\(c\)|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", re.I)
MIN_WP = (6, 4)
MIN_PHP = (8, 1)
SLOW_SECONDS = 2.5


@dataclass
class Finding:
    code: str
    severity: int            # 3 critical, 2 important, 1 minor, 0 good
    title_en: str
    title_hi: str
    evidence: str
    fix_en: str = ""
    fix_hi: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _ver(value: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", value)[:3])


def ssl_days_left(host: str, timeout: float = 8.0) -> int | None:
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return (expires - datetime.now(timezone.utc)).days
    except Exception:  # noqa: BLE001 - unknown is fine; the main fetch reports real SSL errors
        return None


def check_html(page: Page, now: datetime) -> list[Finding]:
    found: list[Finding] = []
    html = page.text or ""
    if len(html) < 300:
        return found
    soup = BeautifulSoup(html, "html.parser")
    lowered = html.lower()

    if not soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)}):
        found.append(Finding("not_mobile", 2, "Website doesn't fit phone screens", "Website phone ki screen pe theek se fit nahi hoti",
                             "no <meta name=\"viewport\"> tag on the homepage",
                             "Add a responsive viewport tag and check every page on a phone.",
                             "Responsive viewport tag lagao aur har page phone pe check karo."))
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    if not title:
        found.append(Finding("no_title", 1, "Homepage has no title for Google", "Homepage ka Google wala title nahi hai",
                             "empty <title> tag", "Add a title with your business name and city.",
                             "Business ka naam aur shehar wala title lagao."))
    if not soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)}):
        found.append(Finding("no_description", 1, "No description shown on Google", "Google pe dikhne wala description nahi hai",
                             "no meta description tag", "Add a 1-2 line description of what you offer.",
                             "1-2 line ka description lagao ki aap kya dete ho."))
    has_call = bool(soup.find("a", href=re.compile(r"^tel:", re.I)))
    has_whatsapp = "wa.me/" in lowered or "api.whatsapp.com" in lowered or "whatsapp://" in lowered
    if not has_call and not has_whatsapp:
        found.append(Finding("no_contact_button", 2, "No tap-to-call or WhatsApp button", "Call ya WhatsApp ka button nahi hai",
                             "no tel: or WhatsApp link on the homepage",
                             "Add a call and a WhatsApp button at the top; most visitors are on phones.",
                             "Upar call aur WhatsApp button lagao; zyada log phone se aate hai."))
    gen = soup.find("meta", attrs={"name": re.compile(r"^generator$", re.I)})
    if gen and gen.get("content"):
        m = WP_RE.search(gen["content"])
        if m and _ver(m.group(1)) < MIN_WP:
            found.append(Finding("old_wordpress", 2, f"Old WordPress version ({m.group(1)})", f"Purana WordPress version ({m.group(1)})",
                                 f"generator tag: {gen['content'][:60]}",
                                 "Back up, then update WordPress, theme and plugins; old versions get hacked.",
                                 "Backup lekar WordPress, theme aur plugins update karo; purane version hack hote hai."))
    if (page.final_url or "").startswith("https://"):
        insecure = [t.get("src") or t.get("href") for t in soup.find_all(["script", "img", "iframe"])
                    if str(t.get("src") or "").startswith("http://")]
        if insecure:
            found.append(Finding("mixed_content", 1, "Some parts load insecurely", "Kuch hisse insecure tareeke se load hote hai",
                                 f"insecure resource: {insecure[0][:100]}",
                                 "Change http:// links to https:// so the padlock stays.",
                                 "http:// links ko https:// karo taaki padlock bana rahe."))
    years = [int(y) for y in COPYRIGHT_RE.findall(" ".join(f.get_text(" ") for f in soup.find_all("footer")) or html[-5000:])]
    if years and max(years) <= now.year - 2:
        found.append(Finding("old_copyright", 1, f"Footer still says © {max(years)}", f"Footer mein abhi bhi © {max(years)} likha hai",
                             f"footer copyright year {max(years)}", "Update the year; it makes the business look inactive.",
                             "Saal update karo; isse business band jaisa lagta hai."))
    images = soup.find_all("img")
    if len(images) > 60:
        found.append(Finding("heavy_page", 1, f"Homepage loads {len(images)} images", f"Homepage pe {len(images)} images load hoti hai",
                             f"{len(images)} <img> tags", "Compress images and lazy-load the rest.",
                             "Images compress karo aur baaki lazy-load."))
    return found


def check_website(raw_url: str, *, fetcher: Callable[..., Page] = fetch,
                  ssl_days: Callable[[str], int | None] = ssl_days_left, now: datetime | None = None) -> dict:
    """Run every check. Returns a plain dict ready to store and show."""
    now = now or datetime.now()
    try:
        url = normalize_url(raw_url)
    except UnsafeURL as exc:
        return {"ok": False, "url": raw_url, "error": str(exc), "findings": []}
    host = urlparse(url).hostname or ""
    https_url = f"https://{host}/"
    findings: list[Finding] = []

    page = fetcher(https_url, verify=True)
    if page.error == "unsafe":
        return {"ok": False, "url": url, "error": page.error_detail, "findings": []}
    if page.error == "dns":
        findings.append(Finding("no_dns", 3, "The domain doesn't work at all", "Domain hi kaam nahi kar raha",
                                f"{host} does not resolve", "Check the domain renewal and DNS settings at your domain provider.",
                                "Domain provider pe renewal aur DNS settings check karo."))
        return _report(url, findings, None)
    if page.error == "ssl":
        findings.append(Finding("ssl_error", 3, "Browsers show a security warning", "Browser security warning dikhata hai",
                                f"HTTPS check: {page.error_detail}", "Renew or install the SSL certificate (Let's Encrypt is free).",
                                "SSL certificate renew ya install karo (Let's Encrypt free hai)."))
        page = fetcher(https_url, verify=False)
    elif page.error in ("connect", "timeout"):
        plain = fetcher(f"http://{host}/", verify=True)
        if plain.status and not plain.error:
            findings.append(Finding("no_https", 3, "Website is marked \"Not secure\"", "Website pe \"Not secure\" dikhta hai",
                                    f"https://{host} doesn't respond, http:// works", "Turn on HTTPS with a free SSL certificate.",
                                    "Free SSL certificate se HTTPS on karo."))
            page = plain
    if page.error and not page.status:
        retry = fetcher(https_url, verify=False)
        if retry.error and not retry.status:
            findings.append(Finding("down", 3, "Website is not opening", "Website khul hi nahi rahi",
                                    f"{https_url}: {retry.error_detail} (checked twice)",
                                    "Check hosting status and renewal; restart the server or contact the host.",
                                    "Hosting ka status aur renewal check karo; server restart ya host se baat karo."))
            return _report(url, findings, None)
        page = retry

    if page.status >= 500:
        findings.append(Finding("server_error", 3, f"Website shows an error (HTTP {page.status})", f"Website error dikha rahi hai (HTTP {page.status})",
                                f"{page.final_url} returned HTTP {page.status}", "Check the server/app error log for the cause.",
                                "Server/app ke error log mein wajah dekho."))
    elif page.status in (404, 410):
        findings.append(Finding("not_found", 3, "Homepage says page not found", "Homepage pe page not found aata hai",
                                f"HTTP {page.status}", "Fix the homepage route or redirect.", "Homepage ka route ya redirect theek karo."))
    elif page.status in (401, 403, 429):
        return _report(url, findings, page, note="The site blocks automatic checks, so only basic checks were possible.")

    if page.final_url.startswith("http://") and not any(f.code == "no_https" for f in findings):
        findings.append(Finding("no_https", 3, "Website is marked \"Not secure\"", "Website pe \"Not secure\" dikhta hai",
                                f"redirects to insecure {page.final_url}", "Redirect all traffic to HTTPS.",
                                "Saara traffic HTTPS pe redirect karo."))
    if page.seconds > SLOW_SECONDS and page.status < 400:
        findings.append(Finding("slow", 2, f"Slow to respond ({page.seconds:.1f}s)", f"Jawab dene mein der ({page.seconds:.1f}s)",
                                f"server answered after {page.seconds:.1f}s", "Turn on caching, compress images, or upgrade hosting.",
                                "Caching on karo, images chhoti karo, ya hosting upgrade karo."))
    php = PHP_RE.search(page.headers.get("x-powered-by", ""))
    if php and _ver(php.group(1)) < MIN_PHP:
        findings.append(Finding("old_php", 2, f"Old server software (PHP {php.group(1)})", f"Purana server software (PHP {php.group(1)})",
                                f"X-Powered-By: {page.headers.get('x-powered-by', '')[:50]}",
                                "Upgrade PHP from the hosting panel after testing on a copy.",
                                "Copy pe test karke hosting panel se PHP upgrade karo."))
    if page.status < 400:
        findings.extend(check_html(page, now))
    if page.final_url.startswith("https://") and not any(f.code == "ssl_error" for f in findings):
        days = ssl_days(host)
        if days is not None and days < 15:
            findings.append(Finding("ssl_expiring", 2, f"Security certificate expires in {days} days", f"Security certificate {days} din mein expire hoga",
                                    f"certificate valid for {days} more days", "Renew it now, or turn on auto-renewal.",
                                    "Abhi renew karo ya auto-renewal on karo."))
    return _report(url, findings, page)


def _report(url: str, findings: list[Finding], page: Page | None, note: str = "") -> dict:
    findings.sort(key=lambda f: f.severity, reverse=True)
    penalty = sum({3: 30, 2: 12, 1: 4}.get(f.severity, 0) for f in findings)
    return {
        "ok": True,
        "url": url,
        "final_url": page.final_url if page else None,
        "status": page.status if page else None,
        "seconds": round(page.seconds, 2) if page else None,
        "score": max(0, 100 - penalty),
        "findings": [f.as_dict() for f in findings],
        "note": note,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
