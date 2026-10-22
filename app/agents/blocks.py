"""Building blocks that do real work on a business's own list (a Google Sheet or CSV).

read -> find the right rows -> write each message -> send / summarise.
Columns are matched by meaning ("Customer Name", "naam", "Client" all mean name), so owners
never have to rename anything.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.agents.safety import UnsafeURL, fetch, normalize_url


MAX_ROWS = 5000
MAX_COLS = 40


class BlockError(ValueError):
    """A problem the owner can fix (wrong link, missing column...). Message is shown to them."""


@dataclass
class Table:
    columns: list[str]
    rows: list[dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------- read
def parse_csv(text: str) -> Table:
    text = (text or "").lstrip("﻿").strip()
    if not text:
        raise BlockError("The sheet is empty")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        raise BlockError("The sheet is empty")
    header = [h.strip() or f"Column {i + 1}" for i, h in enumerate(rows[0][:MAX_COLS])]
    if len(header) < 2:
        raise BlockError("The sheet needs at least 2 columns (for example Name and Email)")
    table = Table(columns=header)
    for raw in rows[1:MAX_ROWS + 1]:
        if not any(cell.strip() for cell in raw):
            continue
        table.rows.append({col: (raw[i].strip() if i < len(raw) else "") for i, col in enumerate(header)})
    return table


def sheet_csv_url(url: str) -> str:
    """Turn a normal Google Sheets link into its CSV download link."""
    m = re.search(r"docs\.google\.com/spreadsheets/d/e/([\w-]+)", url)
    if m:
        gid = re.search(r"gid=(\d+)", url)
        return f"https://docs.google.com/spreadsheets/d/e/{m.group(1)}/pub?output=csv" + (f"&gid={gid.group(1)}" if gid else "")
    m = re.search(r"docs\.google\.com/spreadsheets/d/([\w-]+)", url)
    if m:
        gid = re.search(r"gid=(\d+)", url)
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")
    return url


def load_sheet(url: str, fetcher=None) -> Table:
    fetcher = fetcher or fetch
    try:
        target = normalize_url(sheet_csv_url(url.strip()))
    except UnsafeURL as exc:
        raise BlockError(str(exc)) from exc
    page = fetcher(target)
    if page.error:
        raise BlockError(f"Couldn't open the sheet: {page.error_detail}")
    if page.status in (401, 403) or "accounts.google.com" in (page.final_url or ""):
        raise BlockError("The sheet is private. In Google Sheets: Share → General access → \"Anyone with the link\" (Viewer).")
    if page.status >= 400:
        raise BlockError(f"Couldn't open the sheet (HTTP {page.status})")
    if "<html" in page.text[:500].lower():
        raise BlockError("That link opens a web page, not a sheet. Share the Google Sheet link (Anyone with the link can view).")
    return parse_csv(page.text)


# ---------------------------------------------------------------- understand columns
FIELD_WORDS: dict[str, tuple[str, ...]] = {
    "name": ("name", "customer", "client", "patient", "student", "parent", "member", "naam", "party"),
    "email": ("email", "e-mail", "mail id", "mail"),
    "phone": ("phone", "mobile", "whatsapp", "contact", "number"),
    "amount": ("amount due", "balance", "pending", "outstanding", "due amount", "baaki", "fees due", "fee due", "amount", "fees", "fee", "rs", "total"),
    "due_date": ("due date", "due on", "payment date", "last date", "due", "deadline"),
    "appointment": ("appointment", "booking", "slot", "session", "visit date", "class date", "date and time", "appt"),
    "last_visit": ("last visit", "last order", "last purchase", "last seen", "last came", "last date", "last"),
    "birthday": ("birthday", "date of birth", "dob", "birth", "janamdin"),
    "visit_date": ("visit date", "purchase date", "order date", "bill date", "date"),
    "status": ("status", "paid", "payment status"),
}


def map_fields(columns: list[str], wanted: list[str]) -> dict[str, str | None]:
    """Pick the best column for each meaning. Longer, more specific phrases win."""
    lowered = {c: re.sub(r"[_\-]+", " ", c.lower()).strip() for c in columns}
    used: set[str] = set()
    mapping: dict[str, str | None] = {}
    for fld in wanted:
        best, score = None, 0
        for col, low in lowered.items():
            if col in used:
                continue
            for word in FIELD_WORDS.get(fld, (fld,)):
                if low == word:
                    s = 100 + len(word)
                elif re.search(rf"\b{re.escape(word)}\b", low):
                    s = 50 + len(word)
                else:
                    continue
                if s > score:
                    best, score = col, s
        mapping[fld] = best
        if best:
            used.add(best)
    return mapping


DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %B %Y",
                "%b %d %Y", "%B %d %Y", "%Y/%m/%d", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M", "%d %b")


def parse_date(value: str, today: date | None = None) -> date | None:
    value = (value or "").strip().replace(",", "")
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if fmt == "%d %b":
            parsed = parsed.replace(year=(today or date.today()).year)
        return parsed.date()
    return None


def parse_amount(value: str) -> float | None:
    cleaned = re.sub(r"[^\d.\-]", "", (value or "").replace(",", ""))
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except ValueError:
        return None


def is_paid(value: str) -> bool:
    return (value or "").strip().lower() in ("paid", "yes", "done", "received", "cleared", "complete", "completed", "haan", "ho gaya")


# ---------------------------------------------------------------- find the right rows
RULES = {
    "all": "every row",
    "amount_due": "who still owe money",
    "due_soon": "whose payment is due in the next {days} days or overdue",
    "overdue": "whose payment date has passed",
    "tomorrow": "whose appointment is tomorrow",
    "today": "whose appointment is today",
    "inactive": "who haven't come back in {days} days",
    "birthday_today": "whose birthday is today",
    "yesterday": "who visited or bought yesterday",
}
RULES_HI = {
    "all": "har row",
    "amount_due": "jinka paisa baaki hai",
    "due_soon": "jinka payment {days} din mein due hai ya nikal chuka hai",
    "overdue": "jinki payment date nikal chuki hai",
    "tomorrow": "jinka appointment kal hai",
    "today": "jinka appointment aaj hai",
    "inactive": "jo {days} din se wapas nahi aaye",
    "birthday_today": "jinka aaj birthday hai",
    "yesterday": "jo kal aaye ya kharida",
}
RULE_FIELDS = {
    "all": [], "amount_due": ["amount"], "due_soon": ["due_date"], "overdue": ["due_date"], "tomorrow": ["appointment"],
    "today": ["appointment"], "inactive": ["last_visit"], "birthday_today": ["birthday"], "yesterday": ["visit_date"],
}


def select_rows(table: Table, fields: dict[str, str | None], rule: str, days: int = 3, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    status_col = fields.get("status")
    for row in table.rows:
        if status_col and is_paid(row.get(status_col, "")) and rule in ("amount_due", "due_soon", "overdue"):
            continue
        get = lambda f: row.get(fields.get(f) or "", "")  # noqa: E731
        keep = False
        if rule == "all":
            keep = True
        elif rule == "amount_due":
            amt = parse_amount(get("amount"))
            keep = amt is not None and amt > 0
        elif rule in ("due_soon", "overdue"):
            d = parse_date(get("due_date"), today)
            if d:
                keep = d < today if rule == "overdue" else d <= today + timedelta(days=days)
            if keep and fields.get("amount"):
                amt = parse_amount(get("amount"))
                keep = amt is None or amt > 0
        elif rule in ("tomorrow", "today"):
            d = parse_date(get("appointment"), today)
            keep = d == today + timedelta(days=1 if rule == "tomorrow" else 0)
        elif rule == "inactive":
            d = parse_date(get("last_visit"), today)
            keep = d is not None and d <= today - timedelta(days=days)
        elif rule == "birthday_today":
            d = parse_date(get("birthday"), today)
            keep = d is not None and (d.month, d.day) == (today.month, today.day)
        elif rule == "yesterday":
            d = parse_date(get("visit_date"), today)
            keep = d == today - timedelta(days=1)
        if keep:
            out.append(row)
    return out


# ---------------------------------------------------------------- write each message
PLACEHOLDER = re.compile(r"\{([^{}]{1,60})\}")


def fill(template: str, row: dict, fields: dict[str, str | None], extra: dict | None = None) -> str:
    def value(key: str) -> str:
        key_l = key.strip().lower()
        if extra and key_l in extra:
            return str(extra[key_l])
        col = fields.get(key_l)
        if col:
            raw = row.get(col, "")
            if key_l == "amount":
                amt = parse_amount(raw)
                return f"{amt:,.0f}" if amt is not None else raw
            if key_l == "name":
                return raw.split()[0].title() if raw else "there"
            return raw
        for col in row:  # the owner may use their own column names: {Customer Name}
            if col.lower() == key_l:
                return row[col]
        return ""
    return PLACEHOLDER.sub(lambda m: value(m.group(1)), template)


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}$")


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match((value or "").strip()))


def summarize(table: Table, rows: list[dict], fields: dict[str, str | None], hi: bool) -> str:
    lines = []
    total_amt = sum((parse_amount(r.get(fields.get("amount") or "", "")) or 0) for r in rows) if fields.get("amount") else None
    if hi:
        lines.append(f"Kul rows: {len(table.rows)} · Is report mein: {len(rows)}")
        if total_amt is not None:
            lines.append(f"Kul amount: ₹{total_amt:,.0f}")
    else:
        lines.append(f"Rows in sheet: {len(table.rows)} · In this report: {len(rows)}")
        if total_amt is not None:
            lines.append(f"Total amount: {total_amt:,.0f}")
    name_col = fields.get("name")
    for r in rows[:15]:
        bits = [r.get(name_col, "") if name_col else ""]
        if fields.get("amount"):
            bits.append(r.get(fields["amount"], ""))
        for f in ("due_date", "appointment", "last_visit"):
            if fields.get(f):
                bits.append(r.get(fields[f], ""))
        lines.append("• " + " · ".join(b for b in bits if b))
    if len(rows) > 15:
        lines.append(f"… +{len(rows) - 15} more")
    return "\n".join(lines)
