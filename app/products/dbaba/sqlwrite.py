"""Night S1: writing a query from a description ("write a query to list ...", "... dikhao", "... दिखाओ").

How it reasons (the way a careful person writes a query from a request):
  1. the names: which tables and columns the words point at. From the person's own CREATE TABLE when they pasted one
     (or a connected database's schema), otherwise from the words themselves: snake_case names, a table named in the
     plural, and the usual names for what a verb means ("earn" / "paid" -> the money column, "newest" -> the date
     column). A name it had to guess is marked in the query ("-- check: ...") for the person to confirm.
  2. what one result row is: a row of the table, one row per group ("per", "each", "by"), or one total.
  3. which rows: every condition said (comparisons, ranges, lists, NULL / not NULL, starts with / contains), with the
     right bracket around an OR of one column's values (AND binds tighter than OR, fundamental 'precedence').
  4. how the tables join: along the foreign key (the child's column that names the parent); a LEFT JOIN when the words
     want every row of the first table "including those with none", NOT EXISTS for "without any".
  5. order and cut: ORDER BY what was asked (newest first, highest ...), then the top N.
  6. the engine's words (rule A): LIMIT on MySQL / MariaDB / PostgreSQL / SQLite, TOP on SQL Server, FETCH FIRST on Oracle.
Then the written query goes through DBaba's own checker (app/agents/sqlcorrect.py): a query the checker would call an
error or wrong is never shown; the honest answer then is "I can't build that one yet" and what is needed.

Proven by tests/fuzz/nl2sql.py: random schemas, random requests in the three languages, every built query run on
MariaDB and PostgreSQL and compared with the generator's own correct query.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

WORDS_FILE = Path(__file__).resolve().parent / "dbknow" / "core" / "write_words.json"

# "write a query", "query to", "sql for", "kaise likhu", or a request verb followed by what to get
ASK_RE = re.compile(
    r"\b(?:write|give|create|build|make|need|want|help me (?:write|with (?:writing|a new)))\b[^.?!\n]{0,40}?\b(?:sql|query|queries|select)\b|"
    r"\b(?:sql|query)\s+(?:to|for|that|which|jo|ke liye)\b|\bquery\s+(?:likho|likh do|banao|chahiye)|"
    r"\b(?:query|sql)\b[^.?!\n]{0,30}\bkaise\s+likh|\bkaise\s+likh\w*\b[^.?!\n]{0,30}\b(?:query|sql)\b|"
    r"(?<![\w\u0900-\u097F])(?:क्वेरी|query)[^.?!\n]{0,20}(?:लिखो|लिखें|लिखूं|बनाओ)|\bselect\s+statement\s+(?:to|for)\b", re.I)
VERB_RE = re.compile(r"\b(?:fetch|list|show|display|find|retrieve|get|return|count|calculate|compute|select|pull|rank|delete|remove|"
                     r"increase|decrease|raise|reduce)\b", re.I)
NUM = r"-?\d[\d,]*(?:\.\d+)?"
# pasted code or a written query (for the checker), never a request to write one
CODE_RE = re.compile(r"\bselect\s+[\w.*,`\"\s]+?\s+from\s+[\w.`\"]+\s*(?:\w+\s+)?(?:where|order|join|group|limit|inner|left|;|$)|"
                     r"\brequire\(|^\s*(?:import|from)\s+\w+|\.(?:query|execute|prepare)\s*\(|\$\{|\bcursor\(\)", re.I | re.M)


@lru_cache(maxsize=1)
def words() -> dict:
    return json.loads(WORDS_FILE.read_text(encoding="utf-8"))


class _Lit:
    """A plain string that answers .group(0), so keep() can take a regex match or a piece of text alike."""

    def __init__(self, s: str):
        self.s = s

    def group(self, _i=0):
        return self.s


@lru_cache(maxsize=1)
def _phrases() -> list:
    """The phrase map, compiled; {B} / {E}: a word edge for Latin and Devanagari letters alike."""
    edge_b, edge_e = r"(?<![\w\u0900-\u097F])", r"(?![\w\u0900-\u097F])"
    return [(re.compile(p.replace("{B}", edge_b).replace("{E}", edge_e), re.I), rep) for p, rep in words()["phrases"]]


# ------------------------------------------------------------------------------------------------ is it a request
def is_request(text: str) -> bool:
    """A request to WRITE a query (not a pasted query to check, not a concept question)."""
    from app.agents.core import query_in

    t = text or ""
    if not t.strip() or query_in(t) or CODE_RE.search(t):
        return False
    from app.agents import sqlpuzzle

    if sqlpuzzle.is_puzzle(t):                                  # 'given (1, NULL, 3), what does AVG return?': worked out, not written
        return False
    if re.search(r"\b(why|kyun|kyu|क्यों|error|slow|explain|what is|what does|difference|vs\.?)\b", t, re.I) and not ASK_RE.search(t):
        return False
    if _lesson(t):
        return False
    if ASK_RE.search(t):
        return True
    norm = _normalize(t)
    # "list the employees whose ...", "employees jinki salary ... dikhao", "how many orders have status 'x'":
    # a request verb + a table-ish noun (not 'queries', 'indexes', 'rows'...) + a condition
    skip = set(words()["not_tables"])
    nouns = [w for w in re.findall(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b|\b[a-z]{3,}s\b", norm) if w not in skip]
    # a single-word table ('staff', 'inventory') counts when it is clearly the thing asked from / filtered
    nouns += [w for w in re.findall(r"\b([a-z]{3,})\s+(?:where|whose|have|has|that|who)\b", norm) + re.findall(r"\bfrom\s+([a-z]{3,})\b", t.lower())
              if w not in skip and w not in ("many", "much", "data", "database", "table")]
    if SHAPE_RE.search(norm):                       # night S2 shapes: manager rows, pairs, Nth, %, ranks, changes
        return True
    asks = VERB_RE.search(norm) or re.search(r"\bhow many\b", norm) or re.search(
        r"\b(?:total|average|avg|sum|maximum|minimum|count)\b[^.?!]{0,40}\b(?:per|each|by)\b|"
        r"\b(?:per|each)\b[^.?!]{0,40}\b(?:total|average|avg|sum|maximum|minimum|count)\b", norm)
    return bool(asks and nouns and re.search(
        r"\b(?:where|whose|who|with|from|of|in|per|each|top \d+|sorted|ordered|that|have|has|having|no|non-null|null|"
        r"duplicate|oldest|newest|highest|lowest|between|starting|starts|begins?)\b|[<>=]", norm))


SHAPE_RE = re.compile(
    r"\b(?:their|his|her|its)\s+(?:direct\s+|immediate\s+)?(?:manager|lead|boss|supervisor|parent|mentor|referrer)\b|"
    r"\bwithout (?:a |any )?(?:manager|lead|boss|supervisor|parent|mentor)\b|\bdirect reports\b|\bpairs? of\b|"
    r"\b(?:\d+(?:st|nd|rd|th)|second|third|fourth|fifth)\s+(?:highest|lowest|largest|smallest|most|least|latest|newest|oldest)\b|"
    r"\btop \d+\b.{0,60}\b(?:in each|per|for each|in every)\b|\b(?:in each|per|for each|each)\b.{0,40}\btop \d+\b|"
    r"\baverage\b.{0,40}\b(?:than|above|below)\b.{0,20}\boverall\b|\boverall average\b|"
    r"\bpercent(?:age)?\b.{0,60}\b(?:per|each|of total|in each|by)\b|\b(?:each|per)\b.{0,60}\bpercent(?:age)?\b|"
    r"\brank(?:ed)?\b.{0,40}\bby\b|\bby\b.{0,40}\brank\b|\b(?:running|cumulative) (?:total|sum|count)\b|"
    r"\b(?:previous|next|prior)\s+[a-z_]+'s\b|"
    r"\b(?:set|update|change)\s+[a-z_ ]{1,30}\s+to\s+\S+.{0,60}\b(?:where|for|whose)\b|"
    r"\b(?:increase|decrease|raise|reduce)\b.{0,40}\bby \d|\d+\s*%\s+(?:increase|decrease)\b|"
    r"\b(?:delete|remove)\b.{0,40}\b(?:where|whose|duplicate)\b|\bduplicate\b.{0,20}\b(?:delete|remove)\b")
GENERIC_Q = re.compile(r"^\s*(?:how|why|what|when|which|can|is|does|do|should)\b|\b(?:kaise|kyun|kyu|kya|hote|hota|hoti|farak|fark|farq|kaun|kaunsa|kaunsi|kab|kitne (?:\w+ )?types|prakar|lagana|lagani|lagaye|lagayen)\b|(?:कैसे|क्यों|क्या|कब|कौन|प्रकार|फ़र्क|फर्क)|\?\s*$|\binstead of\b|\b(?:records|rows|entries)\b", re.I)
CONCRETE = re.compile(r"(?<!\w)'[^']+'|\"[^\"]+\"|`[^`]+`|\b(?!(?:row_number|dense_rank|percent_rank|cume_dist|group_concat|string_agg|date_format|date_trunc|"
                      r"date_add|date_sub|str_to_date|to_char|to_date|last_insert_id|found_rows|auto_increment|"
                      r"current_date|current_timestamp|json_extract|order_by|group_by|is_null|not_null|sql_mode|"
                      r"only_full_group_by|innodb_\w+|max_\w+|wp_\w+)\b)[a-z][a-z0-9]*_[a-z0-9_]+\b|\d{3,}|\w\s*(?:[<>]=?|!=|=)\s*[\'\"\w]|\b(?:more|less|greater|fewer) than\b|"
                      r"\bcreate\s+table\s+\S+\s*\(|\bnamed\b|\bcalled\b|\b(?:se|से)\s+(?:zyada|kam|ज़्यादा|ज्यादा|कम)", re.I)


def _lesson(text: str) -> bool:
    """'how to count rows per category', 'update query kaise likhe': a general how-to that a known lesson answers, with no
    names or values of their own in it, is a lesson; 'query for employees whose salary is more than 50000' is a job."""
    if CONCRETE.search(text):
        return False
    if re.search(r"\b(?:farak|fark|farq|antar|difference|differences|vs|versus)\b|अंतर|फ़र्क|फर्क", text, re.I):
        return True
    try:
        from app.products.dbaba.dbknow import concepts as K

        m = K.match(text)
    except Exception:  # noqa: BLE001
        return False
    if not (m and m.get("concept")):
        return False
    if GENERIC_Q.search(text):
        return True
    # no question word: a lesson only when the words hold nothing of the person's own (no table-like names), or name a
    # database engine ('delete in batches in mysql', 'two users reduce stock at the same time in sqlite')
    return bool(re.search(r"\b(?:mysql|mariadb|postgres|postgresql|sqlite|sql server|oracle|wordpress)\b", text, re.I))


# ------------------------------------------------------------------------------------------------ the schema
@dataclass
class Col:
    name: str
    kind: str = "text"       # num / date / text / id
    guessed: bool = False


@dataclass
class Table:
    name: str
    cols: dict = field(default_factory=dict)       # lower name -> Col
    pk: str | None = None
    fks: list = field(default_factory=list)       # (col, parent table, parent col)
    guessed: bool = False

    def forms(self) -> set:
        n = self.name.lower()
        out = {n}
        m = re.match(r"(?:[a-z]*\d+[a-z0-9]*|wp|tbl|tb|t)_(.+)$", n)
        if m:
            out.add(m.group(1))
        for x in list(out):
            out |= _plural_forms(x)
            out |= {y.replace("_", " ") for y in _plural_forms(x)}
        return out

    def col(self, name: str) -> Col | None:
        return self.cols.get((name or "").lower())


def _plural_forms(n: str) -> set:
    out = {n}
    if n.endswith("ves"):
        out |= {n[:-3] + "f", n[:-3] + "fe"}
    if n.endswith("ies"):
        out.add(n[:-3] + "y")
    elif n.endswith(("ses", "xes", "ches", "shes")):
        out.add(n[:-2])
    elif n.endswith("s") and not n.endswith("ss"):
        out.add(n[:-1])
    else:
        out.add(n + ("es" if n.endswith(("s", "x", "ch", "sh")) else "s"))
        if n.endswith("y") and n[-2:-1] not in "aeiou":
            out.add(n[:-1] + "ies")
    return out


def _kind(name: str, typ: str = "") -> str:
    t, n = (typ or "").lower(), name.lower()
    if re.search(r"date|time|year", t):
        return "date"
    if re.search(r"int|dec|num|float|double|real|money|serial", t):
        return "id" if n == "id" or n.endswith("_id") else "num"
    if t:
        return "text"
    if n == "id" or n.endswith("_id"):
        return "id"
    if re.search(r"date|_at$|_on$|^created|^updated|time|^dob$|birthday|joined|hired", n):
        return "date"
    if re.search(r"salary|price|amount|cost|total|qty|quantity|count|age|score|marks|fee|balance|stock|rating|pay|wage|"
                 r"income|revenue|discount|tax|weight|height|level|rank|number|num$|^no$", n):
        return "num"
    return "text"


def schema_from(sch: dict | None) -> dict:
    """{lower table name: Table} from app/agents/sqlproject's schema (a pasted CREATE TABLE or a connected database),
    or from tests/fuzz/db.make_schema's form."""
    out = {}
    if not sch:
        return out
    for key, t in (sch.get("tables") or {}).items():
        if "cols" in t and isinstance(t["cols"], dict):                       # sqlproject
            tb = Table(t.get("name") or key, pk=(t.get("pk") or [None])[0])
            for cn in t.get("order") or list(t["cols"]):
                c = t["cols"][cn]
                tb.cols[cn.lower()] = Col(c.get("name") or cn, _kind(c.get("name") or cn, c.get("type") or ""))
            for fk in t.get("fks") or []:
                if len(fk.get("cols") or []) == 1:
                    tb.fks.append((fk["cols"][0], fk["parent"], (fk.get("pcols") or [None])[0]))
        else:                                                                   # tests/fuzz/db.py
            tb = Table(t["name"], pk=t.get("pk"))
            for c in t["cols"]:
                k = {"pk": "id", "fk": "id", "money": "num", "int": "num", "date": "date", "datetime": "date"}.get(c["kind"], "text")
                tb.cols[c["name"].lower()] = Col(c["name"], k)
                if c["kind"] == "fk":
                    parent = sch["tables"][c["ref"]]
                    tb.fks.append((c["name"], parent["name"], parent["pk"]))
        out[tb.name.lower()] = tb
    return out


# ------------------------------------------------------------------------------------------------ reading the words
def _normalize(text: str, keep_case: bool = False) -> str:
    """The request in the shared words (Hinglish / Hindi phrases mapped to plain English). keep_case: the same text with
    the person's own capitals kept (values like 'Data' keep their case); its lower() is the normal form."""
    t = " " + (text or "").replace("’", "'").replace("`", "") + " "
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)                                  # 55,000 -> 55000
    kept = []

    def keep(m):                                               # values the person typed are never translated
        kept.append(m.group(0))
        return f"\x00{len(kept) - 1}\x00"
    t = re.sub(r"'[^']*'|\"[^\"]*\"", keep, t)
    t = re.sub(r"\b((?:starts?|starting|begins?|ends?|ending)\s+with|contains?|named|called)\s+(\S+)",
               lambda m: m.group(1) + " " + keep(_Lit(m.group(2))), t, flags=re.I)
    for pat, rep in _phrases():
        t = pat.sub(rep, t)
    t = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], t)
    t = re.sub(r"(\w)-(paid|earning|priced)\b", r"\1 \2", t, flags=re.I)      # lowest-paid -> lowest paid
    t = re.sub(r"\bnon[- ]?null\b", "not null", t, flags=re.I)
    t = re.sub(r"\s+", " ", t)
    return t if keep_case else t.lower()


@dataclass
class Spec:
    table: Table | None = None
    joins: list = field(default_factory=list)        # (kind, Table, on)  kind: inner / left
    select: list = field(default_factory=list)       # expressions (strings)
    where: list = field(default_factory=list)
    group: list = field(default_factory=list)
    having: list = field(default_factory=list)
    order: list = field(default_factory=list)
    limit: int | None = None
    distinct: bool = False
    guesses: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    cant: str | None = None


class CantBuild(Exception):
    pass


def _find_tables(low: str, schema: dict) -> list[tuple[int, Table]]:
    """(position, table) for each table the words name, first mention first."""
    found = []
    for tb in schema.values():
        best = None
        for f in sorted(tb.forms(), key=len, reverse=True):
            for m in re.finditer(rf"(?<![\w.]){re.escape(f)}(?![\w])", low):
                if re.match(r"[ _]?id\b", low[m.end():m.end() + 4]):
                    continue                                   # 'student id': a column that points at the table
                if best is None or m.start() < best:
                    best = m.start()
                break
        if best is not None:
            found.append((best, tb))
    return sorted(found, key=lambda x: x[0])


def _col_forms(tb: Table, c: Col) -> list[str]:
    n = c.name.lower()
    out = [n, n.replace("_", " ")]
    for f in sorted(tb.forms(), key=len, reverse=True):                    # emp_name / employee_name -> "name" too
        sing = f.rstrip("s")
        for pre in (f + "_", sing + "_"):
            if n.startswith(pre) and len(n) > len(pre) + 2:
                out.append(n[len(pre):].replace("_", " "))
    return list(dict.fromkeys(out))


class Reader:
    def __init__(self, text: str, schema: dict | None, engine: str | None):
        self.raw = text or ""
        self.cased = _normalize(text, keep_case=True)
        self.low = self.cased.lower()
        if len(self.low) != len(self.cased):                    # (a letter whose lower case is longer: no case kept)
            self.cased = self.low
        self.schema = dict(schema or {})
        self.engine = engine
        self.inferred = not self.schema
        self.spec = Spec()

    # ---- names
    def tables(self):
        if self.inferred:
            self._infer_tables()
        found = _find_tables(self.low, self.schema)
        if not found:
            raise CantBuild("which table to read: name it (for example 'from orders') or paste its CREATE TABLE")
        return found

    def _infer_tables(self):
        """No schema: the plural nouns the request reads from become tables, the snake_case words their columns."""
        stop = set("""rows records results entries values items details data columns fields times names numbers ones those
        them their these users' others duplicates pairs queries tables""".split())
        cands = []
        skip = stop | set(words()["money_words"]) | set(words()["not_tables"])

        def ok(w, at):
            return (w not in skip and not w.endswith(("ss", "ous", "us", "is")) and "_" not in w[:-1] and
                    not re.match(r"\s*(?:=|is|are)?\s*(?:'|\d)", self.low[at:at + 6]))
        # first: a plural noun right after from / of / in / no / without / each / every / their (what is read, or joined)
        for m in re.finditer(r"\b(?:from|of|in|no|without(?: any)?|each|every|all|their|any|per|for)\s+(?:the\s+|all\s+)?"
                             r"([a-z][a-z0-9_]{2,}s)\b(?!')", self.low):
            if ok(m.group(1), m.end()):
                cands.append(m.group(1))
        # then: the first plural noun of the request (its subject: "employees whose ...", "orders dikhao ...")
        for m in re.finditer(r"\b([a-z][a-z0-9_]{2,}s)\b(?!')", self.low):
            if ok(m.group(1), m.end()):
                if m.group(1) not in cands:
                    cands.insert(0, m.group(1))
                break
        for m in re.finditer(r"\b(?:its|their|with)\s+([a-z][a-z0-9_]{2,})\s+(?:count|total|number)\b", self.low):
            w = m.group(1)                                     # "and its employee count" -> employees
            w = w if w.endswith("s") else w + ("es" if w.endswith(("x", "ch", "sh")) else "s")
            if w not in skip:
                cands.append(w)
        for m in re.finditer(r"\b([a-z][a-z0-9_]{2,})'s\b", self.low):            # "each employee's name" -> employees
            cands.append(m.group(1) + ("es" if m.group(1).endswith(("s", "x", "ch", "sh")) else "s"))
        m = re.search(r"\b(?:table|tabel)\s+([a-z][a-z0-9_]*)|\b([a-z][a-z0-9_]*)\s+table\b", self.low)
        if m:
            cands.insert(0, m.group(1) or m.group(2))
        for w in dict.fromkeys(cands):
            if not any(w in t.forms() for t in self.schema.values()):
                self.schema[w] = Table(w, guessed=True)

    def col(self, word: str, tb: Table | None = None, kind: str | None = None) -> tuple[Table, Col] | None:
        """The column a word names: in the given table first, then any table of the request."""
        word = word.lower().strip()
        order = ([tb] if tb else []) + [t for t in self._in_play() if t is not tb]
        for t in order:
            for c in t.cols.values():
                if word in _col_forms(t, c) and (kind is None or c.kind == kind):
                    return t, c
        return None

    def _in_play(self) -> list[Table]:
        s = self.spec
        return [x for x in ([s.table] + [j[1] for j in s.joins]) if x]

    def mentions(self) -> list[tuple[int, int, Table, Col]]:
        """Every column mention (start, end, table, column), longest names first, no overlaps."""
        out, taken = [], []
        cands = []
        for t in self._in_play() + [t for t in self.schema.values() if t not in self._in_play()]:
            for c in t.cols.values():
                for f in _col_forms(t, c):
                    cands.append((f, t, c))
        for f, t, c in sorted(cands, key=lambda x: -len(x[0])):
            for m in re.finditer(rf"(?<![\w.]){re.escape(f)}(?![\w])", self.low):
                if any(a < m.end() and m.start() < b for a, b in taken):
                    continue
                taken.append((m.start(), m.end()))
                out.append((m.start(), m.end(), t, c))
        return sorted(out, key=lambda x: x[0])

    def guess_col(self, word: str, kind: str, tb: Table) -> Col:
        """A column the words need but the schema doesn't show: added to the table, marked for the person."""
        c = tb.cols.get(word.lower())
        if c is None:
            c = Col(word, kind, guessed=True)
            tb.cols[word.lower()] = c
            self.spec.guesses.append(f"{tb.name}.{word}")
        return c

    def money_col(self, tb: Table) -> Col:
        nums = [c for c in tb.cols.values() if c.kind == "num"]
        for w in words()["money_words"]:
            for c in nums:
                if w in c.name.lower():
                    return c
        if len(nums) == 1 and not self.inferred:
            return nums[0]
        if self.inferred or tb.guessed:
            name = ("salary" if re.search(r"\b(earn|paid|salary|pay)\w*", self.low) else
                    "price" if re.search(r"\b(expensive|cheap|cheapest|costliest|priced|price)\w*", self.low) else "amount")
            return self.guess_col(name, "num", tb)
        raise CantBuild(f"which column of {tb.name} holds the amount")

    def date_col(self, tb: Table) -> Col:
        dates = [c for c in tb.cols.values() if c.kind == "date"]
        if len(dates) == 1:
            return dates[0]
        for c in dates:
            if re.search(r"created|date|added|joined|hired", c.name.lower()):
                return c
        if dates:
            return dates[0]
        if self.inferred or tb.guessed:
            return self.guess_col("created_at", "date", tb)
        raise CantBuild(f"which date column of {tb.name} to sort by")


# ------------------------------------------------------------------------------------------------ building
def build(text: str, schema: dict | None = None, engine: str | None = None) -> dict:
    """{'ok', 'sql', 'guesses', 'steps', 'cant'} for a request in words. schema: app/agents/sqlproject's form or
    tests/fuzz/db.make_schema's; None = only the words."""
    r = Reader(text, schema_from(schema) if schema and not isinstance(next(iter(schema.values()), None), Table) else schema, engine)
    try:
        sql = _build(r)
    except CantBuild as exc:
        return {"ok": False, "cant": str(exc), "guesses": r.spec.guesses}
    ok, why = _self_check(sql, engine, r.schema)
    if not ok:
        return {"ok": False, "cant": why, "guesses": r.spec.guesses}
    return {"ok": True, "sql": sql, "guesses": r.spec.guesses, "steps": r.spec.steps}


def _build(r: Reader) -> str:
    from app.agents import sqlshapes

    sql = sqlshapes.build(r)                                 # night S2: self joins, Nth, top per group, %, ranks, changes
    if sql:
        return sql
    s = r.spec
    low = r.low
    for pat, what in words()["not_yet"]:                     # shapes the writer doesn't build yet: said, never faked
        if re.search(pat, low):
            raise CantBuild(f"more than I can do yet: this needs {what}")
    found = r.tables()
    s.table = _main_table(r, found)
    others = [t for _p, t in found if t is not s.table]
    if r.inferred:
        _infer_columns(r)
    anti = _anti_join(r, others)
    child_count = _child_count(r, others) if not anti else None
    for t in others:
        if t is anti or t is child_count:
            continue
        _join(r, t, "left" if _keep_all(low) else "inner")
    ments = r.mentions()
    for _a, _b, mt, _c in ments:                              # 'books with their shelf label': label lives in shelves
        if mt not in r._in_play() and mt is not anti and not mt.guessed:
            try:
                _join(r, mt, "left" if _keep_all(low) else "inner")
            except CantBuild:
                pass
    ments = [m for m in r.mentions() if m[2] in r._in_play()]
    used = set()
    _distinct_upper(r)
    _aggregates(r, ments, used)
    _filters(r, ments, used)
    _above_average(r, ments, used)
    _order_limit(r, ments, used)
    _select_rest(r, ments, used)
    if child_count is not None:
        _finish_child_count(r, child_count)
    return _render(r)


def _main_table(r: Reader, found) -> Table:
    low = r.low
    # "employees whose ..." / "list departments that have no employees": the first table named after the verb, unless
    # the words say "from X" / "in X table"
    if len(found) > 1 and re.search(r"\b(?:without|with no|have no|has no|having no|that have no|who have no)\b", low):
        return found[0][1]                                  # 'departments that have no projects': the subject
    m = re.search(r"\b(?:from|in)\s+(?:the\s+)?([a-z][a-z0-9_ ]*?)\s+table\b|\bfrom\s+(?:the\s+)?([a-z][a-z0-9_]*)", low)
    if m:
        w = (m.group(1) or m.group(2) or "").strip()
        for _p, t in found:
            if w in t.forms():
                return t
    return found[0][1]


def _infer_columns(r: Reader):
    """No schema: snake_case words (and money / date words) become columns of the table they most likely belong to."""
    tb = r.spec.table
    tabs = {f for t in r.schema.values() for f in t.forms()}
    for m in re.finditer(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", r.low):
        w = m.group(1)
        if w in tabs:
            continue
        owner = tb
        for t in r.schema.values():                                  # dept_name -> the departments table when it is named
            if t is not tb and any(w.startswith(f.rstrip("s")[:4]) for f in t.forms() if len(f) > 3) and not w.endswith("_id"):
                owner = t
        if w not in owner.cols:
            owner.cols[w] = Col(w, _kind(w), guessed=False)
    stop = set("""the a an all any each every and or of in on at to by for with from is are was be than who whose which that
    where only also not no top first last more less most least show list find get fetch display give write query sql""".split())
    for m in re.finditer(rf"\b([a-z][a-z0-9_]*)\s+(?:is\s+|are\s+|was\s+)?(?:not\s+)?(?:{CMP}|=|!=|<>|between|starts? with|"
                         rf"ends? with|contains?|like|is missing|is null|is not null)\b", r.low):
        w = m.group(1)
        if w in stop or w in tabs or w in words()["money_verbs"] or w in ("joined", "created", "added", "hired", "registered",
                                                                           "placed", "born", "made", "done", "paid"):
            continue
        if not any(w in x.cols for x in r.schema.values()):
            tb.cols[w] = Col(w, _kind(w))
    for m in re.finditer(r"\b(?:where|whose|and|with|having)\s+([a-z][a-z0-9_]*)\s+(?:is|are|=)\s+(?:not\s+)?"
                         r"(?!null\b|missing\b|empty\b|more\b|less\b|greater\b|between\b|above\b|below\b)", r.low):
        w = m.group(1)                                         # "whose department is Sales": department is a column
        if w not in stop and w not in tabs and not any(w in x.cols for x in r.schema.values()):
            tb.cols[w] = Col(w, _kind(w))
    for w in ("salary", "price", "amount", "email", "name", "status", "city", "age", "location", "title", "quantity"):
        if re.search(rf"\b{w}\b", r.low) and w not in tb.cols and not any(w in c for t in r.schema.values() for c in t.cols):
            tb.cols[w] = Col(w, _kind(w))


def _keep_all(low: str) -> bool:
    return bool(re.search(r"\b(?:including|include|even (?:if|those|when|the ones)|with (?:no|zero|0)\b|zero|count of 0|0 \w+s?\b|"
                          r"\bnone\b|no \w+ at all|empty ones)", low))


def _fk_between(r: Reader, a: Table, b: Table):
    """(child, child col, parent, parent col) for a foreign key between the two tables, guessed by name when no schema."""
    for child, parent in ((a, b), (b, a)):
        for col, ptab, pcol in child.fks:
            if ptab.lower() == parent.name.lower():
                return child, child.col(col), parent, parent.col(pcol) or Col(pcol, "id")
    for child, parent in ((a, b), (b, a)):                        # by name: orders.customer_id -> customers.(customer_)id
        for f in sorted(parent.forms(), key=len):
            sing = f.rstrip("s") if not f.endswith("ss") else f
            for c in child.cols.values():
                if c.name.lower() in (f"{sing}_id", f"{f}_id") or (c.name.lower().endswith("_id") and
                                                                   sing.startswith(c.name.lower()[:-3]) and len(c.name) > 5):
                    pc = parent.col(c.name) or parent.col("id") or (parent.col(parent.pk) if parent.pk else None)
                    if pc is None and (parent.guessed or r.inferred):
                        pc = parent.cols.setdefault(c.name.lower(), Col(c.name, "id"))
                    if pc is not None:
                        return child, c, parent, pc
    if r.inferred:                                                 # nothing to go on: the usual <parent>_id, marked
        # the table named in the singular ("their customer city") is the parent; otherwise the second table is the child
        # that points at the first ("departments that have no projects": projects.department_id)
        sing_b = any(re.search(rf"\b{re.escape(f)}\b", r.low) for f in b.forms() if not f.endswith("s"))
        parent, child = (b, a) if sing_b and not any(re.search(rf"\b{re.escape(f)}\b", r.low) for f in b.forms() if f.endswith("s")) \
            else (a, b)
        sing = sorted(parent.forms(), key=len)[0]
        cname = f"{sing}_id"
        c = child.cols.setdefault(cname, Col(cname, "id"))
        pc = parent.cols.setdefault(cname, Col(cname, "id"))
        r.spec.guesses.append(f"{child.name}.{cname} = {parent.name}.{cname}")
        return child, c, parent, pc
    raise CantBuild(f"how {a.name} and {b.name} are linked (no foreign key between them)")


def _alias(r: Reader, t: Table) -> str:
    taken = {}
    for x in r._in_play():
        base = re.sub(r"^[a-z]*\d+[a-z0-9]*_", "", x.name.lower())[:1] or "t"
        a = base
        k = 2
        while a in taken.values():
            a = f"{base}{k}"
            k += 1
        taken[x.name] = a
    return taken.get(t.name, "t")


def _join(r: Reader, t: Table, kind: str):
    child, cc, parent, pc = _fk_between(r, r.spec.table, t)
    r.spec.joins.append((kind, t, (child, cc, parent, pc)))
    r.spec.steps.append(f"{'LEFT JOIN' if kind == 'left' else 'JOIN'} {t.name} on {child.name}.{cc.name} = {parent.name}.{pc.name}"
                        + (" (every row of the first table stays, with NULLs where nothing matches)" if kind == "left" else ""))


def _anti_join(r: Reader, others) -> Table | None:
    for t in others:
        forms = "|".join(re.escape(f) for f in sorted(t.forms(), key=len, reverse=True))
        if re.search(rf"\b(?:without(?: any)?|with no|have no|has no|having no|no|never (?:\w+ )?(?:any )?|not having any|zero)\s+"
                     rf"(?:[a-z]+\s+){{0,2}}(?:{forms})\b", r.low):
            child, cc, parent, pc = _fk_between(r, r.spec.table, t)
            r.spec.steps.append(f"NOT EXISTS: keep a {r.spec.table.name} row only when no {t.name} row points to it "
                                f"(NOT EXISTS ignores NULLs, unlike NOT IN)")
            r.spec.where.append(("anti", t, (child, cc, parent, pc)))
            return t
    return None


def _child_count(r: Reader, others) -> Table | None:
    """'each department and its employee count' / 'number of orders per customer': the parent's rows with a count of the
    child's rows (LEFT JOIN when the words keep the ones with none)."""
    for t in others:
        forms = "|".join(re.escape(f) for f in sorted(t.forms(), key=len, reverse=True))
        if re.search(rf"\b(?:count of|number of|how many|count)\s+(?:the\s+)?(?:{forms})\b|\b(?:{forms})\s+count\b|"
                     rf"\bits\s+(?:{forms})\s+count\b", r.low):
            child, cc, parent, pc = _fk_between(r, r.spec.table, t)
            if parent is not r.spec.table:
                return None
            _join(r, t, "left" if _keep_all(r.low) or re.search(r"\b(?:each|every|all)\b", r.low) else "inner")
            return t
    return None


def _finish_child_count(r: Reader, t: Table):
    s = r.spec
    a = _alias(r, t)
    key = t.col(t.pk) if t.pk else None
    _kind_, _t, (child, cc, parent, pc) = next(j for j in s.joins if j[1] is t)
    target = f"{a}.{(key.name if key else cc.name)}"
    s.select = [x for x in s.select if not x.upper().startswith("COUNT(")]
    s.select.append(f"COUNT({target}) AS {_short(t)}_count")
    pa = _alias(r, s.table)
    keys = [f"{pa}.{(s.table.col(s.table.pk).name if s.table.pk and s.table.col(s.table.pk) else pc.name)}"]
    plain = [x for x in s.select if not re.match(r"(?i)(count|sum|avg|min|max)\(", x)]
    s.group = list(dict.fromkeys(keys + [re.sub(r"(?i)\s+as\s+\w+$", "", x) for x in plain]))
    s.steps.append(f"COUNT({target}) counts only matching {t.name} rows, so a {s.table.name} row with none shows 0")


def _short(t: Table) -> str:
    base = re.sub(r"^[a-z]*\d+[a-z0-9]*_", "", t.name.lower())
    return base[:-1] if base.endswith("s") and not base.endswith("ss") else base


def _ref(r: Reader, t: Table, c: Col) -> str:
    multi = bool(r.spec.joins) or any(w[0] == "anti" for w in r.spec.where if isinstance(w, tuple))
    return f"{_alias(r, t)}.{c.name}" if multi else c.name


def _distinct_upper(r: Reader):
    if re.search(r"\b(?:unique|distinct|different)\b", r.low):
        r.spec.distinct = True


AGG_WORDS = {"average": "AVG", "avg": "AVG", "mean": "AVG", "total": "SUM", "sum of": "SUM", "sum": "SUM",
             "highest": "MAX", "maximum": "MAX", "max": "MAX", "largest": "MAX", "lowest": "MIN", "minimum": "MIN",
             "min": "MIN", "smallest": "MIN"}


def _aggregates(r: Reader, ments, used):
    s, low = r.spec, r.low
    group_m = None
    for a, b, t, c in ments:
        before = low[max(0, a - 30):a]
        if re.search(r"\b(?:per|for each|for every|in each|each|every|by|grouped by|group by|wise)\s+(?:the\s+)?$", before) and \
                not re.search(r"\b(?:sorted|ordered|order|sort|sorting)\s+by\s+(?:the\s+)?$", before) and \
                not re.search(r"\bby\s+(?:the\s+)?$", before) or re.match(r"\s*-?wise\b", low[b:b + 6]):
            group_m = (a, b, t, c)
            break
        if re.search(r"\bby\s+(?:the\s+)?$", before) and not re.search(r"\b(?:sorted|ordered|order|sort|sorting)\s+by\s+(?:the\s+)?$", before) \
                and re.search(r"\b(?:count|total|average|avg|sum|number of|how many|mean)\b", low):
            group_m = (a, b, t, c)
            break
    dup = re.search(r"\bduplicate(?:d)?\s+(?:the\s+)?", low)
    if dup:
        target = next(((a, b, t, c) for a, b, t, c in ments if a >= dup.end() - 1 and a <= dup.end() + 25), None)
        if target is None:
            raise CantBuild("which column holds the duplicates")
        a, b, t, c = target
        s.select = [_ref(r, t, c), "COUNT(*) AS occurrences"]
        s.group = [_ref(r, t, c)]
        s.having.append("COUNT(*) > 1")
        used.add((a, b))
        s.steps.append(f"GROUP BY {c.name} makes one row per value; HAVING COUNT(*) > 1 keeps the values seen more than once")
        return
    aggs = []
    for a, b, t, c in ments:
        before = low[max(0, a - 22):a]
        m = re.search(r"\b(average|avg|mean|total|sum of|sum|highest|maximum|max|largest|lowest|minimum|min|smallest)\s+(?:of\s+)?(?:the\s+)?$", before)
        if not m:
            continue
        fn = AGG_WORDS[m.group(1)]
        if fn in ("MAX", "MIN") and group_m is None:
            continue                                           # 'highest salary' with no groups: a row, read by ordering
        if fn in ("SUM", "AVG") and c.kind not in ("num",):
            continue
        aggs.append((fn, a, b, t, c))
    counted = re.search(r"\b(?:how many|number of|count of|count)\b", low)
    if group_m is None and not aggs:
        if counted and re.search(r"\b(?:each|every|its|their)\b", low) and not s.joins:
            raise CantBuild("what the count is per (say 'per <column>' or name the related table)")
        if counted and not re.search(r"\b(?:count of|number of|how many)\s+(?:distinct|unique)\b", low) and \
                not re.search(r"\bcount\)", low) and not any(j for j in s.joins):
            s.select = ["COUNT(*) AS total_rows"]
            s.steps.append("COUNT(*) counts the rows that pass the conditions")
            s.__dict__["_count_only"] = True
        return
    if group_m is not None:
        a, b, t, c = group_m
        used.add((a, b))
        s.group.append(_ref(r, t, c))
        s.select.insert(0, _ref(r, t, c))
        s.steps.append(f"GROUP BY {c.name}: one result row per {c.name}")
    for fn, a, b, t, c in aggs:
        used.add((a, b))
        s.select.append(f"{fn}({_ref(r, t, c)}) AS {fn.lower()}_{c.name}")
    if counted or not aggs:
        s.select.append("COUNT(*) AS row_count")
    _having(r)


def _having(r: Reader):
    s, low = r.spec, r.low
    m = re.search(rf"\b(?:with|having|where|only (?:those |the ones )?with|that have|which have)\s+(?:a\s+)?(?:count\s+)?"
                  rf"(more than|over|above|greater than|at least|less than|fewer than|under|below|at most)\s+({NUM})\b", low)
    if not m or not s.group:
        return
    op = _op(m.group(1))
    agg = next((x for x in s.select if re.match(r"(AVG|SUM|MAX|MIN)\(", x)), None)
    after = low[m.end():m.end() + 30]
    expr = "COUNT(*)"
    if agg and not re.match(r"\s*(?:rows|records|entries|times|items|\w+s\b)", after):
        expr = re.sub(r"\s+AS\s+\w+$", "", agg)
    s.having.append(f"{expr} {op} {_num(m.group(2))}")


def _op(word: str) -> str:
    w = word.strip()
    if w in ("more than", "over", "above", "greater than", "higher than", "bigger than", "larger than", "after"):
        return ">"
    if w in ("at least", "minimum", "not less than", "min", "from", "since", "on or after"):
        return ">="
    if w in ("less than", "fewer than", "under", "below", "lower than", "smaller than", "before"):
        return "<"
    if w in ("at most", "maximum", "not more than", "up to", "upto", "max", "on or before"):
        return "<="
    return "="


def _num(v: str) -> str:
    v = v.replace(",", "")
    return v


CMP = (r"more than|greater than|higher than|bigger than|larger than|over|above|at least|minimum of|not less than|less than|"
       r"fewer than|lower than|smaller than|under|below|at most|up to|upto|not more than|maximum of|after|before|since|"
       r"on or after|on or before")


def _filters(r: Reader, ments, used):
    s, low = r.spec, r.low
    conds = []
    money_verb = re.search(r"\b(earn|earns|earning|earned|paid|makes?|get paid|gets paid)\b", low)
    for idx, (a, b, t, c) in enumerate(ments):
        if (a, b) in used:
            continue
        rest = low[b:b + 120]
        crest = r.cased[b:b + 120]
        before = low[max(0, a - 25):a]
        ref = _ref(r, t, c)
        lm = re.match(rf"\s*(?:is\s+|are\s+|of\s+|=\s*|equals?\s+(?:to\s+)?)?(?:not\s+)?(?:in|one of|any of)?\s*"
                      rf"((?:{NUM}|'[^']*'|\"[^\"]*\"|[a-z][\w-]*(?: (?!or\b|and\b)[a-z][\w-]*)?)(?:\s*(?:,|\bor\b)\s*(?:{NUM}|'[^']*'|\"[^\"]*\"|[a-z][\w-]*(?: (?!or\b|and\b)[a-z][\w-]*)?))+)", rest)
        if lm and c.kind != "date":
            vals = [v for v in re.split(r"\s*(?:,|\b[oO][rR]\b)\s*", crest[lm.start(1):lm.end(1)].strip()) if v]
            vals = [re.split(r"\s+(?:and|AND)\s+", v)[0] for v in vals]
            vals = [v for v in vals if (_is_value(v.lower(), c, r) or (v != v.lower() and c.kind == "text"))
                    and not any(_is_name(w, r) for w in v.lower().split())]
            if len(vals) >= 2:
                neg = bool(re.match(r"\s*(?:is\s+|are\s+)?not\s", rest))
                conds.append((a, f"{ref} {'NOT IN' if neg else 'IN'} ({', '.join(_val(v, c) for v in vals)})"))
                used.add((a, b))
                continue
        m = (re.match(r"\s*(?:is\s+|are\s+)?(?:not\s+(?:null|missing|empty|blank)|present|set|known|filled|available|not empty)\b", rest)
             or (re.search(r"\b(?:have|has|having|with)\s+(?:a\s+|an\s+)?(?:not null\s+)?$", before) and
                 re.search(r"\bnot null\s+$", before))
             or re.search(r"\bnot null\s+$", before))
        if m:
            conds.append((a, f"{ref} IS NOT NULL"))
            used.add((a, b))
            continue
        m = re.match(r"\s*(?:is\s+|are\s+)?(?:null|missing|empty|blank|not set|unknown|absent)\b", rest) or \
            re.search(r"\b(?:no|without|missing|null)\s+(?:a\s+|an\s+)?$", before) and not re.search(r"\bnot null\s+$", before)
        if m:
            conds.append((a, f"{ref} IS NULL"))
            used.add((a, b))
            continue
        m = re.match(rf"\s*(?:is\s+|are\s+)?between\s+(\d{{4}}-\d\d-\d\d|{NUM}|'[^']*')\s+and\s+(\d{{4}}-\d\d-\d\d|{NUM}|'[^']*')", rest)
        if m:
            conds.append((a, f"{ref} BETWEEN {_val(m.group(1), c)} AND {_val(m.group(2), c)}"))
            used.add((a, b))
            continue
        m = re.match(rf"\s*(?:is\s+|are\s+|of\s+|=\s*|equals?\s+(?:to\s+)?)?(?:not\s+)?(?:in|one of|any of)?\s*"
                     rf"((?:{NUM}|'[^']*'|\"[^\"]*\"|[a-z][\w-]*)(?:\s*(?:,|or|and|/)\s*(?:{NUM}|'[^']*'|\"[^\"]*\"|[a-z][\w-]*))+)", rest)
        if m and re.search(r"(,|\bor\b|/)", m.group(1)) and c.kind != "date":
            vals = [v for v in re.split(r"\s*(?:,|\b[oO][rR]\b|\b[aA][nN][dD]\b|/)\s*", crest[m.start(1):m.end(1)].strip()) if v]
            vals = [v for v in vals if _is_value(v.lower(), c, r) or (v != v.lower() and c.kind == "text")]   # 'A' is a value
            if len(vals) >= 2:
                neg = bool(re.match(r"\s*(?:is\s+|are\s+)?not\s", rest))
                conds.append((a, f"{ref} {'NOT IN' if neg else 'IN'} ({', '.join(_val(v, c) for v in vals)})"))
                used.add((a, b))
                continue
        m = re.match(rf"\s*(?:is\s+|are\s+|was\s+|value\s+)?(?:{CMP})\s+(?:than\s+)?(\d{{4}}-\d\d-\d\d|{NUM}|'[^']*')", rest)
        if m:
            opw = re.match(rf"\s*(?:is\s+|are\s+|was\s+|value\s+)?({CMP})", rest).group(1)
            conds.append((a, f"{ref} {_op(opw)} {_val(m.group(1), c)}"))
            used.add((a, b))
            continue
        m = re.match(r"\s*(?:is\s+|are\s+)?(?:starts?|starting|begins?|beginning)\s+with\s+('[^']*'|\"[^\"]*\"|[\w@.-]+)", rest)
        if m:
            conds.append((a, f"{ref} LIKE {_like(crest[m.start(1):m.end(1)], 'start')}"))
            used.add((a, b))
            continue
        m = re.match(r"\s*(?:is\s+|are\s+)?(?:ends?|ending)\s+with\s+('[^']*'|\"[^\"]*\"|[\w@.-]+)", rest)
        if m:
            conds.append((a, f"{ref} LIKE {_like(crest[m.start(1):m.end(1)], 'end')}"))
            used.add((a, b))
            continue
        m = re.match(r"\s*(?:contains?|containing|includes?|has|having)\s+('[^']*'|\"[^\"]*\"|[\w@.-]+)", rest)
        if m and not re.match(r"(?:a|an|the|no|any)$", m.group(1)):
            conds.append((a, f"{ref} LIKE {_like(crest[m.start(1):m.end(1)], 'contains')}"))
            used.add((a, b))
            continue
        m = re.match(rf"\s*(?:is\s+not|!=|<>|not\s+equal\s+to|other\s+than|except)\s+({NUM}|'[^']*'|\"[^\"]*\"|[a-z][\w-]*)", rest)
        if m and _is_value(m.group(1), c, r):
            conds.append((a, f"{ref} <> {_val(crest[m.start(1):m.end(1)], c)}"))
            used.add((a, b))
            continue
        m = re.match(rf"\s*(?:is\s+|=\s*|equals?\s+(?:to\s+)?|of\s+)?(\d{{4}}-\d\d-\d\d|{NUM}|'[^']*'|\"[^\"]*\")(?![\w-])", rest)
        if m and not re.match(r"\s*(?:or|and)?\s*(?:more|less|fewer|above|below|at)\b", rest[m.end():]):
            conds.append((a, f"{ref} = {_val(crest[m.start(1):m.end(1)], c)}"))
            used.add((a, b))
            continue
        m = re.match(r"\s*(?:is|=|equals|equal to|of)\s+([a-z][\w-]*)\b", rest) or \
            (re.search(r"\b(?:where|whose)\s+$", before) and
             re.match(r"\s+([a-z][\w-]*)(?=\s*(?:$|,|\.|\band\b|\bor\b|\bsorted\b|\border\b))", rest))
        if m and _is_value(m.group(1), c, r) and c.kind == "text":
            conds.append((a, f"{ref} = {_val(crest[m.start(1):m.end(1)], c)}"))
            used.add((a, b))
            continue
    # "who earn at least 55000": the verb names the money column
    if money_verb:
        rest = low[money_verb.end():money_verb.end() + 40]
        m = re.match(rf"\s*(?:{CMP})\s+(?:than\s+)?({NUM})", rest)
        if m:
            tb = r.spec.table
            c = r.money_col(tb)
            opw = re.match(rf"\s*({CMP})", rest).group(1)
            conds.append((money_verb.start(), f"{_ref(r, tb, c)} {_op(opw)} {_num(m.group(1))}"))
    dv = re.search(r"\b(?:joined|created|added|hired|registered|placed|ordered|born|signed up|made)\s+"
                   r"(after|before|since|on or after|on or before|on|in)\s+(\d{4}-\d\d-\d\d|\d{4})\b", low)
    if dv and not any(isinstance(x[1], str) and "date" in x[1].lower() for x in conds):
        tb = r.spec.table
        c = r.date_col(tb)
        word, val = dv.group(1), dv.group(2)
        ref = _ref(r, tb, c)
        if len(val) == 4:                                     # a year: the whole year, as a range an index can use
            y = int(val)
            cond = {"after": f"{ref} >= '{y + 1}-01-01'", "since": f"{ref} >= '{y}-01-01'", "before": f"{ref} < '{y}-01-01'",
                    "on or after": f"{ref} >= '{y}-01-01'", "on or before": f"{ref} < '{y + 1}-01-01'"}.get(
                word, f"{ref} >= '{y}-01-01' AND {ref} < '{y + 1}-01-01'")
        else:
            cond = f"{ref} {_op(word) if word not in ('on', 'in') else '='} '{val}'"
        conds.append((dv.start(), cond))
    # a value list said before the column ("in dept_id 10 or 30" is handled above; "10 or 30 dept" is rare)
    conds.sort(key=lambda x: x[0])
    for _p, cnd in conds:
        s.where.append(cnd)
    if conds:
        s.steps.append("WHERE keeps the rows that pass every condition (IN for one column's list of values, so the OR "
                       "stays inside it)")


def _is_value(v: str, c: Col, r: Reader) -> bool:
    v = v.strip()
    if re.fullmatch(NUM, v) or v[:1] in "'\"" or re.fullmatch(r"\d{4}-\d\d-\d\d", v):
        return True
    stop = set("""the a an and or of in on at to by for with from is are was be than more less who whose which that this these
    first last top newest oldest highest lowest sorted ordered order sort descending ascending desc asc where per each every
    all any some no not null missing empty only also their his her its show list fetch get find display along together
    plus include including name names second third fourth fifth sixth seventh eighth ninth tenth""".split())
    if v in stop or v in words()["desc_words"] or v in words()["asc_words"]:
        return False
    if any(v in t.forms() for t in r.schema.values()):
        return False
    if any(v == x.lower() or v == x.lower().replace("_", " ") for t in r.schema.values() for x in t.cols):
        return False
    return c.kind == "text"


def _is_name(w: str, r: Reader) -> bool:
    return any(w in t.forms() for t in r.schema.values()) or \
        any(w == x.lower() for t in r.schema.values() for x in t.cols)


def _val(v: str, c: Col) -> str:
    v = v.strip()
    if re.fullmatch(r"\d{4}-\d\d-\d\d", v):
        return f"'{v}'"
    if v[:1] in "'\"":
        return "'" + v[1:-1].replace("'", "''") + "'"
    if re.fullmatch(NUM, v) and c.kind != "text":
        return _num(v)
    if re.fullmatch(NUM, v):
        return "'" + _num(v) + "'" if c.kind == "text" and not c.guessed else _num(v)
    return "'" + v.replace("'", "''") + "'"


def _like(v: str, how: str) -> str:
    v = v.strip().strip("'\"").replace("'", "''")
    v = v.replace("%", "\\%").replace("_", "\\_") if re.search(r"[%_]", v) else v
    return {"start": f"'{v}%'", "end": f"'%{v}'", "contains": f"'%{v}%'"}[how]


def _above_average(r: Reader, ments, used):
    s, low = r.spec, r.low
    m = re.search(r"\b(above|more than|greater than|higher than|over|below|less than|lower than|under)\s+(?:the\s+)?"
                  r"(?:overall\s+|company\s+|total\s+|whole\s+)?(?:average|avg|mean)\b", low)
    if not m:
        return
    op = ">" if m.group(1) in ("above", "more than", "greater than", "higher than", "over") else "<"
    before = [(a, b, t, c) for a, b, t, c in ments if a < m.start() and c.kind == "num"]
    if before:
        a, b, t, c = before[-1]
    else:
        t, c = r.spec.table, r.money_col(r.spec.table)
    if s.group:                                               # groups whose average is above the overall average
        agg = next((x for x in s.select if x.startswith("AVG(")), None)
        if agg is None:
            agg = f"AVG({_ref(r, t, c)}) AS avg_{c.name}"
            s.select.append(agg)
        bare = re.sub(r" AS \w+$", "", agg)
        s.having.append(f"{bare} {op} (SELECT AVG({c.name}) FROM {t.name})")
        s.steps.append(f"HAVING compares each group's average with the average over the whole {t.name} table (a subquery)")
    else:
        s.where.append(f"{_ref(r, t, c)} {op} (SELECT AVG({c.name}) FROM {t.name})")
        s.steps.append(f"the subquery works out the average {c.name} once; each row is compared with it")
    if before:
        used.add((before[-1][0], before[-1][1]))


def _order_limit(r: Reader, ments, used):
    s, low = r.spec, r.low
    m = re.search(r"\b(?:top|first|limit(?: to)?|only|just)\s+(\d+)\b|\b(\d+)\s+(?:most|least|highest|lowest|newest|oldest|latest|"
                  r"cheapest|biggest|largest|smallest|earliest)\b|(?<!\bat )\b(?:most|least|highest|lowest|newest|oldest|latest|cheapest|"
                  r"biggest|largest|smallest|earliest)\s+(\d+)\b", low)
    if m:
        s.limit = int(m.group(1) or m.group(2) or m.group(3))
    desc_w = "|".join(re.escape(w) for w in sorted(words()["desc_words"], key=len, reverse=True))
    asc_w = "|".join(re.escape(w) for w in sorted(words()["asc_words"], key=len, reverse=True))
    sm = re.search(r"\b(?:sorted|ordered|order|sort|sorting|arranged|ranked)\s+(?:them\s+)?(?:by|on|in)\s+", low) or \
        re.search(r"\bsorted\s+(?=(?:in\s+)?(?:" + desc_w + "|" + asc_w + r")\b)", low) or \
        re.search(r"\b(?:by)\s+(?=[a-z_ ]+\s+(?:" + desc_w + "|" + asc_w + r")\b)", low)
    if sm:
        target = next(((a, b, t, c) for a, b, t, c in ments if sm.end() - 1 <= a <= sm.end() + 12), None)
        tail = low[(target[1] if target else sm.end()):(target[1] if target else sm.end()) + 40]
        direction = "DESC" if re.match(rf"\s*(?:in\s+)?(?:{desc_w})\b", tail) else "ASC"
        if target is None:                                    # 'salary ke hisaab se sort karo': the column came first
            target = next(((a, b, t, c) for a, b, t, c in reversed(ments) if b <= sm.start() and sm.start() - b <= 6), None)
            if target is not None:
                tail = low[sm.end():sm.end() + 40]
                direction = "DESC" if re.match(rf"\s*(?:in\s+)?(?:{desc_w})\b", tail) else "ASC"
        if target is None:
            if re.match(rf"\s*(?:{desc_w}|{asc_w})", low[sm.end():]):
                tb = r.spec.table
                c = r.date_col(tb) if re.search(r"newest|latest|recent|oldest|earliest", low[sm.end():sm.end() + 20]) else r.money_col(tb)
                s.order.append(f"{_ref(r, tb, c)} {direction}")
            return _limit_note(s)
        a, b, t, c = target
        used.add((a, b))
        s.order.append(f"{_ref(r, t, c)} {direction}")
        return _limit_note(s)
    # "lowest paid", "highest priced", "newest first", "most recent N": the superlative names the column
    sup = re.search(r"\b(lowest|highest|best|least|most|top|worst)[ -](paid|earning|earners?|salaried|priced|expensive|cheap)\b|"
                    r"\b(newest|latest|most recent|recent|oldest|earliest)\b|\b(cheapest|most expensive|costliest)\b", low)
    if sup:
        tb = r.spec.table
        if sup.group(3):
            c = r.date_col(tb)
            direction = "DESC" if sup.group(3) in ("newest", "latest", "most recent", "recent") else "ASC"
        else:
            c = r.money_col(tb)
            word = (sup.group(1) or sup.group(4) or "").strip()
            direction = "ASC" if word in ("lowest", "least", "worst", "cheapest") or (sup.group(2) or "") == "cheap" else "DESC"
        s.order.append(f"{_ref(r, tb, c)} {direction}")
        return _limit_note(s)
    if s.limit is not None and not s.order:
        for a, b, t, c in ments:                              # "top 5 orders by amount"
            before = low[max(0, a - 12):a]
            if re.search(r"\bby\s+(?:the\s+)?$", before) and (a, b) not in used:
                used.add((a, b))
                s.order.append(f"{_ref(r, t, c)} DESC")
                break
    if re.search(r"\b(?:with|has|having)\s+the\s+(?:highest|maximum|largest|lowest|minimum|smallest)\b", low) and not s.group:
        mm = re.search(r"\bthe\s+(highest|maximum|largest|lowest|minimum|smallest)\s+", low)
        tgt = next(((a, b, t, c) for a, b, t, c in ments if mm.end() - 1 <= a <= mm.end() + 3), None)
        if tgt:
            a, b, t, c = tgt
            used.add((a, b))
            fn = "MAX" if mm.group(1) in ("highest", "maximum", "largest") else "MIN"
            s.where.append(f"{_ref(r, t, c)} = (SELECT {fn}({c.name}) FROM {t.name})")
            s.steps.append(f"the subquery finds the {mm.group(1)} {c.name}; every row with that value is kept (ties too)")
    _limit_note(s)


def _limit_note(s: Spec):
    if s.limit is not None:
        s.steps.append(f"the top {s.limit} rows after sorting" if s.order else
                       f"{s.limit} rows (any {s.limit}: say how to sort for a fixed set)")


def _select_rest(r: Reader, ments, used):
    s, low = r.spec, r.low
    if s.__dict__.get("_count_only") or (s.select and re.search(r"COUNT\(\*\) AS occurrences", " ".join(s.select))):
        return
    if s.group:
        return
    picked = []
    upper = re.search(r"\b(?:in\s+)?(upper ?case|capital letters|caps|lower ?case|small letters)\b", low)
    for a, b, t, c in ments:
        if (a, b) in used:
            continue
        before = low[max(0, a - 40):a]
        if re.search(r"\b(?:sorted|ordered|order|sort)\s+by\s+(?:the\s+)?$", before):
            continue
        if re.search(r"\bby\s+(?:the\s+)?$", before) and any(o.split()[0] == _ref(r, t, c) for o in s.order):
            continue                                          # 'the newest 2 orders by order_date': the sort, not a column to show
        expr = _ref(r, t, c)
        if upper and abs(upper.start() - b) < 40:
            fn = "UPPER" if upper.group(1).startswith(("upper", "capital", "caps")) else "LOWER"
            expr = f"{fn}({expr}) AS {c.name}"
        if expr not in picked:
            picked.append(expr)
    if picked and s.joins and not any(p_.startswith(_alias(r, s.table) + ".") for p_ in picked) and \
            not re.search(r"\b(?:only|just)\b", low):              # 'orders with their customer name': the orders and the name
        picked = [f"{_alias(r, s.table)}.*"] + picked
    if not picked:
        t = s.table
        picked = [f"{_alias(r, t)}.*" if s.joins else "*"]
        for kind, jt, _on in s.joins:
            picked.append(f"{_alias(r, jt)}.*")
    s.select = picked + [x for x in s.select if x not in picked]
    if s.where and not any(w for w in s.where if isinstance(w, str)) and not any(isinstance(w, tuple) for w in s.where):
        pass


def _render(r: Reader) -> str:
    s = r.spec
    if not s.select:
        s.select = ["*"]
    s.select = list(dict.fromkeys(s.select))
    aggs = [x for x in s.select if re.match(r"(?i)(count|sum|avg|min|max)\(", x)]
    if aggs and not s.group and len(aggs) != len(s.select):
        raise CantBuild("whether you want one total for everything or one row per group (say 'per ...' / 'for each ...')")
    eng = (r.engine or "").lower()
    head = "SELECT " + ("DISTINCT " if s.distinct and not s.group else "")
    if s.limit is not None and eng in ("sqlserver",):
        head += f"TOP ({s.limit}) "
    lines = [head + ", ".join(s.select)]
    t = s.table
    multi = bool(s.joins) or any(isinstance(w, tuple) for w in s.where)
    lines.append(f"FROM {t.name}" + (f" {_alias(r, t)}" if multi else ""))
    for kind, jt, (child, cc, parent, pc) in s.joins:
        lines.append(f"{'LEFT JOIN' if kind == 'left' else 'JOIN'} {jt.name} {_alias(r, jt)} ON "
                     f"{_alias(r, child)}.{cc.name} = {_alias(r, parent)}.{pc.name}")
    conds = []
    for w in s.where:
        if isinstance(w, tuple) and w[0] == "anti":
            _k, jt, (child, cc, parent, pc) = w
            inner = "x"
            if parent is t:
                conds.append(f"NOT EXISTS (SELECT 1 FROM {jt.name} {inner} WHERE {inner}.{cc.name} = {_alias(r, t)}.{pc.name})")
            else:
                conds.append(f"NOT EXISTS (SELECT 1 FROM {jt.name} {inner} WHERE {inner}.{pc.name} = {_alias(r, t)}.{cc.name})")
        else:
            conds.append(w)
    if conds:
        lines.append("WHERE " + "\n  AND ".join(conds))
    if s.group:
        lines.append("GROUP BY " + ", ".join(s.group))
    if s.having:
        lines.append("HAVING " + " AND ".join(s.having))
    if s.order:
        lines.append("ORDER BY " + ", ".join(s.order))
    if s.limit is not None:
        if eng == "oracle":
            lines.append(f"FETCH FIRST {s.limit} ROWS ONLY")
        elif eng != "sqlserver":
            lines.append(f"LIMIT {s.limit}")
    sql = "\n".join(lines)
    if s.guesses:
        sql += "\n-- check: " + "; ".join(dict.fromkeys(s.guesses)) + " (names guessed from your words)"
    return sql


def _self_check(sql: str, engine: str | None, schema: dict | None = None) -> tuple[bool, str]:
    """The written query goes through DBaba's own checker: nothing it calls an error or wrong is ever shown. And it must
    really parse and name only real columns: it is run (no rows) on an empty SQLite copy of the tables it was built from."""
    from app.agents import sqlcorrect

    if schema and (engine or "") not in ("sqlserver", "oracle"):
        import sqlite3

        db = sqlite3.connect(":memory:")
        try:
            for tb in schema.values():
                cols = ", ".join(f'"{c.name}"' for c in tb.cols.values()) or '"id"'
                db.execute(f'CREATE TABLE "{tb.name}" ({cols})')
            body = re.sub(r"\n-- check:.*$", "", sql)
            if re.match(r"(?i)\s*(?:update|delete)\b", body):
                db.execute("EXPLAIN " + body)                     # compiled, never run
            else:
                db.execute("SELECT * FROM (" + body + ") LIMIT 0")
        except sqlite3.Error as exc:
            return False, f"the query I built doesn't hold together yet ({str(exc)[:60]})"
        finally:
            db.close()

    eng = engine if engine in ("mysql", "mariadb", "postgresql", "sqlite") else None
    try:
        res = sqlcorrect.check(re.sub(r"\n-- check:.*$", "", sql), engine=eng)
    except Exception:  # noqa: BLE001 - the checker is the safety net; a crash means: don't show it
        return False, "the query I built didn't pass my own check"
    bad = [f["title_en"] for f in res.get("findings") or [] if f["kind"] in ("error", "wrong")]
    if bad:
        return False, "the query I built didn't pass my own check (" + bad[0] + ")"
    return True, ""


# ------------------------------------------------------------------------------------------------ the reply
def reply(text: str, engine: str | None = None, hi: bool = False, schema: dict | None = None) -> tuple[str, dict]:
    """(reply text, result) for a request in words."""
    from app.agents import sqlproject

    sch = schema
    if sch is None and re.search(r"\bcreate\s+table\b", text or "", re.I):
        pasted = sqlproject.from_text(text)
        sch = pasted if pasted.get("tables") else None
        text = re.sub(r"(?is)create\s+table\b.*?\)\s*;", " ", text)
    res = build(text, sch, engine)
    if not res["ok"]:
        return ((f"Ye query abhi main nahi bana pa raha: {res['cant']}. Tables aur columns ke naam (ya CREATE TABLE) de do, "
                 f"main bana dunga.") if hi else
                (f"I can't build that one yet: I need {res['cant']}. Give me the table and column names (or paste the "
                 f"CREATE TABLE) and I'll write it.")), res
    from app.products.dbaba.dbknow import engines as E

    where = (E.label(engine) if engine else "MySQL, MariaDB, PostgreSQL and SQLite")
    head = ("Ye rahi query:" if hi else "Here's the query:")
    body = res["sql"].rstrip() + ";" if "-- check" not in res["sql"] else re.sub(r"\n-- check", ";\n-- check", res["sql"], count=1)
    how = ("\n\nKaise kaam karti hai:\n" if hi else "\n\nHow it works:\n") + "\n".join(f"- {x}" for x in res["steps"][:5]) \
        if res["steps"] else ""
    guess = ""
    if res["guesses"]:
        guess = ("\n\nIn naamon ka andaza lagaya hai, apni table se mila lo: " if hi else
                 "\n\nI guessed these names from your words; check them against your tables: ") + ", ".join(dict.fromkeys(res["guesses"])) + "."
    runs = (f"\n\n{where} pe chalti hai." if hi else f"\n\nRuns on {where}.")
    res["parts"] = {"head": head, "body": body, "how": how, "guess": guess, "runs": runs, "where": where, "hi": hi}
    return f"{head}\n\n{body}{how}{guess}{runs}", res


def card(title: str, res: dict) -> dict:
    """The answer in parts for the page: the explanation in the chat, the query itself in the side panel (the same
    form as a concept's worked example, app/dbknow/concepts.card)."""
    p = res["parts"]
    hi = p["hi"]
    explain = (p["how"] + p["guess"] + p["runs"]).strip()
    return {"id": "write", "lang": "hinglish" if hi else "en", "title": title, "answer": explain, "mistake": "",
            "examples": [{"label": "", "sql": [{"label": p["where"], "query": p["body"]}]}], "engines": [],
            "labels": {"example": "Query", "lab": "DBaba ke checker se jaancha" if hi else "checked by DBaba's own checker",
                       "result": "Result", "mistake": "", "engines": "", "preview": "", "change": ""}}
