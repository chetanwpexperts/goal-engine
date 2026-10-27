"""Reading a request by its meaning, not by fixed sentences.

The words of the message are turned into meanings (app/officeknow/lexicon.json: single words in English, Hinglish and
Hindi, typos allowed), and the person's own file adds what it knows: which column they named, which cell value,
which number. An order is understood when the meanings of one of its recipes are all there, in any order and any
wording. Nothing here matches a whole sentence. No AI.
"""

import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

from app.ml.normalize import tokens

LEX = Path(__file__).resolve().parents[1] / "officeknow" / "lexicon.json"
UNITS = {"k": 1_000, "thousand": 1_000, "hazar": 1_000, "hazaar": 1_000, "l": 100_000, "lakh": 100_000, "lakhs": 100_000,
         "lac": 100_000, "lacs": 100_000, "lakh.": 100_000, "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000,
         "karod": 10_000_000, "m": 1_000_000, "million": 1_000_000, "mn": 1_000_000}
NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?:\s*([a-z]{1,9}\.?))?(?![\w])", re.I)
DATE_MASK = re.compile(r"(?<![\w])((?:dd|mm|yyyy|yy|mmm)[-/. ](?:dd|mm|mmm|yyyy|yy)[-/. ](?:dd|mm|yyyy|yy))(?![\w])", re.I)
QUOTED = re.compile(r"['\"“”‘’]([^'\"“”‘’]{1,80})['\"“”‘’]")
SYMBOLS = {">": "above", "<": "below", "=": "equal", ">=": "above", "<=": "below"}


@lru_cache(maxsize=1)
def lexicon() -> dict:
    lex = json.loads(LEX.read_text(encoding="utf-8"))
    words: dict[str, set[str]] = {}
    for concept, ws in lex["concepts"].items():
        for w in ws:
            words.setdefault(w.lower(), set()).add(concept)
    lex["_words"] = words
    lex["_long"] = sorted((w for w in words if len(w) >= 5 and " " not in w), key=len)
    return lex


@lru_cache(maxsize=20000)
def _fuzzy(tok: str) -> tuple[str, ...]:
    """A misspelt word -> the known word it most likely is ('higlight' -> highlight). Long words only."""
    if tok.isdigit() or len(tok) < 4:
        return ()
    if len(tok) == 4:
        # 'free' (freeze with a letter lost): a 4-letter start of exactly one meaning
        words = lexicon()["_words"]
        starts = [w for w in lexicon()["_long"] if w.startswith(tok)]
        meanings = {frozenset(words[w]) for w in starts}
        return (starts[0],) if starts and len(meanings) == 1 else ()
    best = [w for w in difflib.get_close_matches(tok, lexicon()["_long"], n=3, cutoff=0.8) if abs(len(w) - len(tok)) <= 2]
    return tuple(best[:1])


class Reading:
    """What one message means: meanings with where they were said, plus what came from the file."""

    def __init__(self, text: str, known: set[str] | frozenset = frozenset()):
        """known: the words of the person's own file (column names, sheet names): never 'corrected' into an order word."""
        self.text = text or ""
        lex = lexicon()
        raw = tokens(self.text)
        joined = []
        i = 0
        while i < len(raw):
            pair = f"{raw[i]} {raw[i + 1]}" if i + 1 < len(raw) else None
            if pair and pair in lex["multiword"]:
                joined += lex["multiword"][pair].split()
                i += 2
            else:
                joined.append(raw[i])
                i += 1
        self.tokens = joined
        self.at: dict[str, list[int]] = {}
        for pos, tok in enumerate(joined):
            cs = lex["_words"].get(tok)
            if cs is None and tok not in known:
                cs = set().union(*(lex["_words"][w] for w in _fuzzy(tok))) if _fuzzy(tok) else set()
            for c in cs or ():
                self.at.setdefault(c, []).append(pos)
        for sym, c in SYMBOLS.items():
            if re.search(rf"(?<![<>=]){re.escape(sym)}(?![<>=])\s*\d", self.text):
                self.at.setdefault(c, []).append(-1)
        if "₹" in self.text:
            self.at.setdefault("money", []).append(-1)
        self.numbers = numbers(self.text)
        if self.numbers:
            self.at.setdefault("NUMBER", []).append(-1)
        self.mask = DATE_MASK.search(self.text)
        if self.mask:
            self.at.setdefault("DATE_MASK", []).append(-1)
        self.quoted = QUOTED.findall(self.text)
        # "first row", "top row", "pehli row": the heading row; "first column", "column a": the first column
        for p in self.at.get("first", []):
            near = joined[p + 1:p + 3]
            if any("row" in lex["_words"].get(w, ()) for w in near):
                self.at.setdefault("FIRST_ROW", []).append(p)
            if any("column" in lex["_words"].get(w, ()) for w in near):
                self.at.setdefault("FIRST_COL", []).append(p)
        if re.search(r"\b(column|col)\s+a\b", self.text, re.I):
            self.at.setdefault("FIRST_COL", []).append(-1)

    def has(self, *concepts) -> bool:
        return any(c in self.at for c in concepts)

    def first(self, concept) -> int | None:
        p = self.at.get(concept)
        return min(p) if p else None

    def asking(self) -> bool:
        """A question about the file, not an order. Asking politely for work ('can you...', 'kya aap ... kar do') is an order."""
        toks = self.tokens
        if not toks:
            return False
        q_first = toks[0] in lexicon()["concepts"]["question"] or (len(toks) > 1 and toks[1] in ("many", "much") and toks[0] == "how")
        polite_first = toks[0] in ("can", "could", "would", "will", "please", "plz", "pls", "kindly", "zara", "kripya") or \
            (toks[0] == "kya" and len(toks) > 1 and toks[1] in ("aap", "tum", "you"))
        if polite_first:
            return False
        if q_first:
            return True
        doing = self.has("act", "remove", "change", "replace", "highlight", "freeze", "rename", "split", "round", "bold", "polite", "fit")
        return self.text.strip().endswith("?") and not doing


def numbers(text: str) -> list[float]:
    """Numbers as people write them: 25k, 1 lakh, 1L, 1,00,000, 2.5 cr."""
    out = []
    for m in NUM.finditer(text or ""):
        if DATE_MASK.search(m.group(0)):
            continue
        v = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower().rstrip(".")
        if unit and unit not in UNITS and len(unit) >= 4:
            close = difflib.get_close_matches(unit, [u for u in UNITS if len(u) >= 4], n=1, cutoff=0.75)
            unit = close[0] if close else unit
        v *= UNITS.get(unit, 1)
        out.append(int(v) if float(v).is_integer() else v)
    return out


def recipes() -> dict:
    return {k: v for k, v in lexicon()["orders"].items() if k != "about"}


def satisfied(r: Reading, recipe: dict) -> bool:
    if any(r.has(n) for n in recipe.get("not", [])):
        return False
    return all(r.has(*group) for group in recipe["all"])


def orders(r: Reading) -> list[tuple[int, str]]:
    """Every order whose meaning is all there, the most specific first."""
    generic = set(lexicon().get("generic", []))
    found = []
    for op, rs in recipes().items():
        best = None
        for rec in rs:
            if satisfied(r, rec):
                sc = sum(0.3 if all(c in generic for c in g) else 1.0 for g in rec["all"]) + 0.01 * len(rec.get("not", []))
                best = sc if best is None else max(best, sc)
        if best is not None:
            found.append((best, op))
    return sorted(found, key=lambda x: (-x[0], x[1]))


def colour(r: Reading, default: str | None = None) -> str | None:
    hits = [(min(r.at[c]), c) for c in lexicon()["colours"] if c in r.at]
    return min(hits)[1] if hits else default


def free_spans(r: Reading, skip: set[str] = frozenset()) -> list[str]:
    """Runs of words that aren't meanings: the person's own words (names, values, new titles)."""
    lex = lexicon()["_words"]
    spans, cur = [], []
    for t in r.tokens:
        if t in lex or _fuzzy(t) or t in skip:
            if cur:
                spans.append(" ".join(cur))
                cur = []
        else:
            cur.append(t)
    if cur:
        spans.append(" ".join(cur))
    return spans
