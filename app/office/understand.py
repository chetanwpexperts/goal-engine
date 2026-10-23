"""Understanding any file the way a data analyst would, before answering anything about it.

1. Every column gets a role from its values and its heading: id, name, category, amount, quantity, date, yes/no,
   email, phone, text.
2. Everyday things people ask about (gender, age, experience, salary, marks, profit, ... from the Excel agent's
   pack: "concepts") are looked up: is there a column for it, can it be worked out from other columns
   (gender from first names, age from date of birth, experience from joining date, email domain, profit from
   sales and cost), or is it not in the file at all.
3. The question is then answered against that: a worked-out column is added for the answer (and says it's an
   estimate when it is), and something that isn't in the file is said plainly, with what the file does have.
No AI."""

import json
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from app.mind import packs
from app.office.sheetdata import Table

NAMES_FILE = Path(__file__).resolve().parents[1] / "knowledge" / "data" / "first_names.json"
ID_RE = re.compile(r"\b(id|no|number|code|sr|s\.?no|roll|ref|serial)\b|\bno\.", re.I)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
PHONE_RE = re.compile(r"^\+?[\d\s-]{10,14}$")


class Missing(Exception):
    """The question is about something this file doesn't have and that can't be worked out. Said as an answer."""

    def __init__(self, hi, en):
        super().__init__(en)
        self.hi, self.en, self.options, self.answer = hi, en, [], True


def _pack() -> dict:
    return packs.get("office")


def concepts() -> dict:
    return _pack().get("concepts", {})


@lru_cache(maxsize=1)
def _names():
    data = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    return set(data["female"]), set(data["male"]), set(data["either"])


def _words_rx(words):
    alts = sorted({re.escape(w).replace(r"\ ", r"\s+") for w in words}, key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(alts) + r")(?![\w])", re.I) if alts else None


def _hdr_words(h: str) -> set[str]:
    return set(re.findall(r"[a-z0-9/]+", (h or "").lower()))


# ---------------------------------------------------------------- 1. what each column is
def roles(t: Table) -> dict[int, str]:
    cached = getattr(t, "_roles", None)
    if cached is not None:
        return cached
    cat_words = set(_pack().get("category_words", []))
    name_words = _pack().get("name_words", [])
    out = {}
    n = len(t.rows)
    for i, h in enumerate(t.header):
        if not h:
            continue
        kind = t.kind(i)
        hl = h.lower()
        vals = [v for v in t.col(i)[:3000] if v not in (None, "")]
        sv = [str(v).strip() for v in vals]
        distinct = len({v.lower() for v in sv})
        ratio = distinct / max(1, len(sv))
        if kind == "date":
            out[i] = "date"
        elif re.search(r"\b(phone|mobile|contact|whatsapp|cell|tel)\b", hl) or (kind == "number" and sv and sum(
                1 for v in sv if re.fullmatch(r"\+?\d{10,13}", v.replace(".0", ""))) >= 0.6 * len(sv)):
            out[i] = "phone"
        elif kind == "number" and distinct <= 12 and n >= 8 and ratio < 0.3 and (_hdr_words(h) & cat_words or re.search(r"class|grade|std|level|floor|shift", hl)):
            out[i] = "category"                     # 'Class' 6/7/8: numbers that are groups, not amounts
        elif kind == "number":
            if ID_RE.search(h) and ratio > 0.9:
                out[i] = "id"
            elif re.search(r"qty|quantity|units|pieces|count|nos\b", hl):
                out[i] = "quantity"
            elif re.search(r"%|percent|rate\b", hl):
                out[i] = "percent"
            else:
                out[i] = "amount"
        elif sv and sum(1 for v in sv if EMAIL_RE.match(v)) >= 0.6 * len(sv):
            out[i] = "email"
        elif sv and sum(1 for v in sv if PHONE_RE.match(v)) >= 0.6 * len(sv):
            out[i] = "phone"
        elif sv and {v.lower() for v in sv} <= {"yes", "no", "y", "n", "true", "false", "haan", "nahi", "done", "pending"} and distinct <= 2:
            out[i] = "yesno"
        elif (any(w in hl for w in name_words) or re.search(r"\bname\b", hl) or _looks_like_people(sv, ratio)) and not (
                n >= 8 and ratio < 0.3 and distinct <= 40):
            out[i] = "name"                                 # (a 'Dr Name' / 'Party Name' with a few repeating values is a category)
        elif ID_RE.search(h) and ratio > 0.9:
            out[i] = "id"
        elif (_hdr_words(h) & cat_words) or (sv and 2 <= distinct <= min(40, max(2, int(0.5 * len(sv)))) and n >= 4):
            out[i] = "category"
        else:
            out[i] = "text"
    t._roles = out
    return out


def _looks_like_people(sv, ratio) -> bool:
    """A 'Guest' or 'Driver' column full of 'Aditi Sharma', 'Rohan Gill': people's names, whatever the heading says."""
    if not sv or ratio < 0.25:
        return False
    female, male, either = _names()
    known = sum(1 for v in sv[:500] if _first_word(v) in female or _first_word(v) in male or _first_word(v) in either)
    return known >= 0.5 * min(len(sv), 500)


def categories(t: Table) -> list[tuple[int, str]]:
    """(column, its most common value) for real categories, the better ones first."""
    from collections import Counter

    r = roles(t)
    cat_words = set(_pack().get("category_words", []))
    out = []
    for i, role in r.items():
        if role != "category":
            continue
        sv = [str(v).strip() for v in t.col(i)[:3000] if v not in (None, "")]
        if not sv:
            continue
        distinct = len({v.lower() for v in sv})
        hint = bool(_hdr_words(t.header[i]) & cat_words)
        out.append((0 if hint else 1, distinct, i, Counter(sv).most_common(1)[0][0]))
    out.sort()
    return [(i, v) for _, _, i, v in out]


def _first_name_col(t: Table) -> tuple[int, bool] | None:
    """(column, whole name?) holding people's first names."""
    r = roles(t)
    first = next((i for i, role in r.items() if role == "name" and re.search(r"first|given", t.header[i], re.I)), None)
    if first is not None:
        return first, False
    full = next((i for i, role in r.items() if role == "name" and not re.search(r"last|sur|father|company|item|product|vendor|customer|party|shop|firm", t.header[i], re.I)), None)
    return (full, True) if full is not None else None


# ---------------------------------------------------------------- 2. working things out from other columns
def _years_since(d, today=None):
    today = today or date.today()
    if isinstance(d, datetime):
        d = d.date()
    if not isinstance(d, date):
        return None
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def _date_col(t, rx):
    return next((i for i, h in enumerate(t.header) if h and t.kind(i) == "date" and re.search(rx, h, re.I)), None)


def _gender_source(t):
    fc = _first_name_col(t)
    if fc is None:
        return None
    female, male, either = _names()
    col = fc[0]
    known = 0
    for r in t.rows[:2000]:
        first = _first_word(r[col] if col < len(r) else None)
        if first and first not in either and (first in female or first in male):
            known += 1
    return col if known >= max(1, min(len(t.rows), 2000) // 3) else None     # mostly unknown names: no fair estimate


def _first_word(v):
    w = [x for x in re.findall(r"[A-Za-z]+", str(v or "")) if x.lower() not in ("mr", "mrs", "ms", "miss", "dr", "shri", "smt", "kumari", "sri")]
    return w[0].lower() if w else ""


def _gender(t, arg=None):
    col = _gender_source(t)
    if col is None:
        return None
    female, male, either = _names()
    vals, unclear = [], []
    for r in t.rows:
        raw = r[col] if col < len(r) else None
        first = _first_word(raw)
        if first in female and first not in either:
            vals.append("Female")
        elif first in male and first not in either:
            vals.append("Male")
        else:
            vals.append("Unclear")
            if raw not in (None, "") and str(raw).strip() not in unclear:
                unclear.append(str(raw).strip())
    return "Gender (estimated from names)", vals, t.header[col], unclear


def _age(t, arg=None):
    c = _date_col(t, r"birth|dob|d\.o\.b|janam")
    if c is None:
        return None
    return "Age (from date of birth)", [_years_since(r[c] if c < len(r) else None) for r in t.rows], t.header[c], []


def _tenure(t, arg=None):
    c = _date_col(t, r"join|joining|doj|hire|start|since")
    if c is None:
        return None
    return "Years since joining", [_years_since(r[c] if c < len(r) else None) for r in t.rows], t.header[c], []


def _domain(t, arg=None):
    c = next((i for i, role in roles(t).items() if role == "email"), None)
    if c is None:
        return None
    # 'gmail' for name@gmail.com: the way people say it
    vals = [(str(r[c]).split("@", 1)[1].lower().split(".")[0] if c < len(r) and r[c] and "@" in str(r[c]) else None) for r in t.rows]
    return "Email domain", vals, t.header[c], []


def _qty_col(t):
    return next((i for i, role in roles(t).items() if role == "quantity"), None)


def _profit(t, arg=None):
    num = [i for i, role in roles(t).items() if role == "amount"]
    sale = next((i for i in num if re.search(r"sale|revenue|selling|\bsp\b|income", t.header[i], re.I)), None)
    cost = next((i for i in num if re.search(r"cost|purchase|\bcp\b|buying|expense", t.header[i], re.I)), None)
    if sale is None or cost is None or sale == cost:
        return None
    # unit prices with a quantity: profit per line = (selling - cost) x quantity
    q = _qty_col(t) if re.search(r"price|rate|per", t.header[sale] + t.header[cost], re.I) else None

    def diff(r):
        a, b = (r[sale] if sale < len(r) else None), (r[cost] if cost < len(r) else None)
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
            return None
        if q is not None:
            n = r[q] if q < len(r) else None
            return (a - b) * n if isinstance(n, (int, float)) else None
        return a - b
    how = f"({t.header[sale]} minus {t.header[cost]}) x {t.header[q]}" if q is not None else f"{t.header[sale]} minus {t.header[cost]}"
    return "Profit", [diff(r) for r in t.rows], how, []


def _line_total(t, arg=None):
    q = _qty_col(t)
    if q is None or arg is None:
        return None

    def mul(r):
        a, b = (r[q] if q < len(r) else None), (r[arg] if arg < len(r) else None)
        return a * b if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
    return f"Amount ({t.header[q]} x {t.header[arg]})", [mul(r) for r in t.rows], f"{t.header[q]} x {t.header[arg]}", []


def _marks_total(t, arg=None):
    parts = {p.lower() for p in concepts().get("marks", {}).get("parts", [])}
    cols = [i for i in range(len(t.header)) if t.header[i] and t.kind(i) == "number" and (_hdr_words(t.header[i]) & parts)]
    if len(cols) < 1:
        return None

    def tot(r):
        vals = [r[i] for i in cols if i < len(r) and isinstance(r[i], (int, float))]
        return sum(vals) if vals else None
    names = ", ".join(t.header[i] for i in cols)
    return f"Total marks ({names})", [tot(r) for r in t.rows], names, []


DERIVE = {"gender_from_names": _gender, "age_from_birth": _age, "tenure_from_joining": _tenure,
          "domain_from_email": _domain, "profit_from_sales_cost": _profit, "line_total": _line_total,
          "marks_total": _marks_total}


def can_derive(how: str, t: Table) -> bool:
    fn = DERIVE.get(how)
    if fn is None:
        return False
    if how == "gender_from_names":
        return _gender_source(t) is not None
    return fn(t) is not None


def derive(how: str, t: Table, arg=None) -> tuple[Table, dict]:
    """The sheet with one worked-out column added at the end, and what it was worked out from."""
    header, values, source, unclear = DERIVE[how](t, arg)
    nt = Table(t.name, list(t.header) + [header], [list(r) + [values[k]] for k, r in enumerate(t.rows)], t.header_row, t.truncated)
    return nt, {"how": how, "from": source, "header": header, "unclear": unclear}


# ---------------------------------------------------------------- 3. the question against the file
def _concepts() -> dict:
    return concepts()


def mentioned(text: str, tables=None) -> dict[str, set[str]]:
    """{concept: the values named, e.g. {'Female'} for 'how many girls'} for every everyday thing the question names.
    A word that is a value in the file ('Salary' as an expense head) is that value, not the concept."""
    out = {}
    low = text or ""
    cells = set()
    for t in tables or []:
        for i, role in roles(t).items():
            if role == "category":
                cells |= {str(v).strip().lower() for v in t.col(i)[:3000] if isinstance(v, str)}
    for name, c in concepts().items():
        rx = _words_rx(c.get("words", []))
        if not rx:
            continue
        hits = [m.group(0).lower() for m in rx.finditer(low)]
        if not hits or all(h in cells for h in hits):
            continue
        vals = set()
        for canon, variants in (c.get("values") or {}).items():
            vrx = _words_rx([v for v in variants if len(v) > 1] + [canon])
            if vrx and vrx.search(low):
                vals.add(canon)
        out[name] = vals
    return out


def column_for(concept: str, tables: list[Table]):
    """(sheet, column) that holds this concept, by heading or by its values (F/M, Female/Male...)."""
    c = concepts()[concept]
    cues = {w.lower() for w in c.get("headers", [])}
    known = {v.lower() for vs in (c.get("values") or {}).values() for v in vs} | {k.lower() for k in (c.get("values") or {})}
    best = None
    for t in tables:
        for i, h in enumerate(t.header):
            if not h:
                continue
            hit = bool(_hdr_words(h) & cues) or any(" " in q and q in h.lower() for q in cues)
            if not hit and known and roles(t).get(i) in ("category", "yesno"):
                seen = {str(v).strip().lower() for v in t.col(i)[:3000] if v not in (None, "")}
                hit = len(seen) >= 2 and seen <= known | {"", "-", "na", "n/a", "unknown", "other", "others"}
            if hit and (best is None or len(t.rows) > len(best[0].rows)):
                best = (t, i)
    return best


def cell_values(t: Table, col: int, concept: str, canon: str) -> list[str]:
    """The cells in this column that mean canon ('F', 'Female', 'Ladki' for Female)."""
    variants = {v.lower() for v in (concepts()[concept].get("values") or {}).get(canon, [])} | {canon.lower()}
    cells = {str(v).strip() for v in t.col(col)[:5000] if v not in (None, "")}
    return sorted(c for c in cells if c.lower() in variants)


def missing_message(concept: str, tables: list[Table]) -> Missing:
    label = concepts()[concept].get("label", concept)
    m = Missing(f"Is file mein {label} ki jaankari nahi hai, aur kisi aur column se bhi nahi nikal sakti, isliye ye nahi bata sakta. "
                f"File mein ye hai: {_what_it_has(tables)}.",
                f"This file has no {label} information, and it can't be worked out from the other columns, so I can't answer that. "
                f"What the file has: {_what_it_has(tables)}.")
    m.concept = concept
    return m


def _what_it_has(tables) -> str:
    parts = []
    for t in sorted(tables, key=lambda x: -len(x.rows))[:6]:
        r = roles(t)
        keep = [t.header[i] for i, role in r.items() if role in ("category", "amount", "quantity", "date", "name")][:6]
        if keep:
            parts.append(f"{t.name}: {', '.join(keep)}")
    return "; ".join(parts)
