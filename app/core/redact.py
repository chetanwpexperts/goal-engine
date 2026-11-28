"""Pure text redaction shared by learning and privacy."""

import re


_SCRUB = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"\b(?:https?://|www\.)\S+", re.I), "<link>"),
    (re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|in|net|org|io|co|dev|app|info|biz|xyz|shop|site|online)\b\S*", re.I), "<link>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"(?i)\b(password|passwd|pass|pwd|token|secret|api[_ -]?key|key)\b\s*[:=]?\s*\S+"), r"\1 <hidden>"),
    (re.compile(r"\+?\d[\d\s-]{8,}\d"), "<number>"),
    (re.compile(r"\b\d{6,}\b"), "<number>"),          # error codes (4-5 digits) stay: they're the useful part
    (re.compile(r"\b[A-Za-z0-9_\-]{24,}\b"), "<hidden>"),
    (re.compile(r"'[^']{1,80}'|\"[^\"]{1,80}\""), "'…'"),   # quoted values in error messages
]


def scrub(text: str) -> str:
    t = " ".join((text or "").split())[:600]
    for rx, rep in _SCRUB:
        t = rx.sub(rep, t)
    return t[:300]


def scrub_detail(text: str) -> str:
    """Clean longer, already masked detail without removing quoted values."""
    d = (text or "").strip()[:4000]
    for rx, rep in _SCRUB[:-1]:
        d = rx.sub(rep, d)
    return d
