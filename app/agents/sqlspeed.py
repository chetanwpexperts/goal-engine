"""Indexes and speed (night 8 of the curriculum), shared by every database (fundamental 'index_use' in
app/dbknow/core/query_fundamentals.json; what each engine's EXPLAIN shows is read by app/agents/sqlexplain.py).

An index is a sorted copy of some columns with a pointer to each row. It can find rows only where the condition compares the
column AS IT IS STORED with a value, and only from its first column onwards (an index on (a, b) is sorted by a, then by b
inside each a). So:
- a function or arithmetic around the column (YEAR(d) = 2024, DATE(t) = '...', d::date, LEFT(x, 3) = 'abc', qty + 1 = 5,
  COALESCE(c, 0) = 5) turns every row into a new value that no index holds: both engines read the whole table (MariaDB
  'type: ALL' or a full 'index' scan, PostgreSQL 'Seq Scan'). Where the same rows can be described on the bare column (a date
  range, the other side of the equation, a LIKE prefix, c = 5 [OR c IS NULL]) the rewrite is exact and is applied as the
  faster form -> 'check' (the rows don't change; the speed depends on the index and the data).
- OR between columns of two different tables of a join can't be answered by any one index: both engines read the tables in
  full (MariaDB a full index scan, PostgreSQL a hash join over two Seq Scans). UNION ALL of the two cases, the second one
  excluding rows the first already returned ((A) IS NOT TRUE), gives exactly the same rows and lets each side use its own
  index -> 'check', offered. (OR between two indexed columns of ONE table is fine: MariaDB 'index_merge', PostgreSQL
  'BitmapOr'; lab-checked, so nothing is said there.)
- LIMIT n OFFSET m still reads and throws away m rows. Paging by the last row seen (keyset: WHERE key > :last ORDER BY key
  LIMIT n) gives the same page when the ORDER BY ends with a unique column and the app passes the last row's values ->
  'check', offered with the placeholders said in words.
With the table's structure (CREATE TABLE / indexes pasted), index_fit() also says why an index that exists is not used: a
composite index whose first column the query doesn't filter, a range on the first column of (a, b) that leaves b unused for
the search, and a text column compared with a bare number (MySQL/MariaDB can't use the index and match '098' and '98 ';
PostgreSQL refuses, 42883).
Every rule was reproduced on MariaDB 10.11 and PostgreSQL 16 with 20,000 rows (tests/practice/test_night8.py has the lab table).
Returns [(Finding, [edits])], edit = (start, end, new text) on the original query.
"""

import datetime as _dt
import re
from decimal import Decimal, InvalidOperation

# the engines each rule was reproduced on (tests/practice/test_night8.py, LAB) -- standing rule B
PROVEN = {k: ["mariadb", "postgresql"] for k in ("sargable", "or_union", "keyset", "index_prefix", "index_range_first",
                                                  "implicit_cast")}

COL = r"((?:[`\"]?[A-Za-z_][\w$]*[`\"]?\.)?[`\"]?[A-Za-z_][\w$]*[`\"]?)"
NUM = r"(-?\d+(?:\.\d+)?)"
OPS = r"(=|<>|!=|<=|>=|<|>)"
_STARTS = re.compile(r"(?:\bwhere|\band|\bor|\bnot|\(|\bon)\s*$", re.I)
_ENDS = re.compile(r"^\s*(?:$|\)|;|\b(?:and|or|order|group|limit|having|union|offset|fetch|for|window|returning)\b)", re.I)
NOT_COLUMN = {"null", "true", "false", "and", "or", "not", "case", "when", "then", "else", "end", "select", "from", "where", "date",
              "interval", "current_date", "current_timestamp", "now", "exists", "in", "is", "like", "between", "distinct"}


def _C():
    from app.agents import sqlcorrect as C

    return C


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _short(t: str, n: int = 90) -> str:
    t = _flat(t)
    return t if len(t) <= n else t[:n - 3] + "..."


def _bare(col: str) -> str:
    return col.split(".")[-1].strip('`"')


def _engine(sql: str):
    from app.agents import sqlbasics

    return sqlbasics.engine_seen(sql)


def _rule(eng: str, key: str) -> dict:
    from app.agents import sqlbasics

    return sqlbasics.rules(eng).get(key, {}) if eng else {}


def _says(key: str, eng) -> str:
    """The engine fact from the dialect files (rule B): for the known database only, else for each."""
    if eng in ("mysql", "postgresql"):
        return _rule(eng, key).get("says", "")
    return f"{_rule('mysql', key).get('says', '')} {_rule('postgresql', key).get('says', '')}".strip()


# ------------------------------------------------------------------------------------------------ where the WHEREs are
def _where_spans(s: str) -> list[tuple[int, int]]:
    """(start, end) of the body of every WHERE, at every level (the statement and each subquery)."""
    from app.agents import sqlwin

    C = _C()
    out = []
    for a, b in sqlwin._levels(s):
        text = s[a:b]
        cl = C._clauses(text, C._depths(text))
        if "where" in cl:
            x, y = cl["where"]
            out.append((a + x, a + y))
    return out


def _in_where(spans, pos: int, end: int) -> bool:
    best = None
    for a, b in spans:
        if a <= pos and end <= b and (best is None or b - a < best[1] - best[0]):
            best = (a, b)
    return best is not None


def _starts_condition(s: str, pos: int) -> bool:
    return bool(_STARTS.search(s[max(0, pos - 12):pos]))


def _is_column(c: str) -> bool:
    b = _bare(c).lower()
    return not re.match(r"^\d", b) and b not in NOT_COLUMN


# ------------------------------------------------------------------------------------------------ date bounds
def _day(text: str):
    try:
        return _dt.date.fromisoformat(text)
    except ValueError:
        return None


def _next(d: _dt.date, unit: str) -> _dt.date:
    if unit == "day":
        return d + _dt.timedelta(days=1)
    if unit == "month":
        return _dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return _dt.date(d.year + 1, 1, 1)


def _bounds(col: str, unit: str, op: str | None, d1, d2=None) -> str | None:
    """The same rows as unit(col) op d1 (or BETWEEN d1 AND d2), said on the bare column. None: not exact (<>)."""
    q = lambda d: f"'{d.isoformat()}'"  # noqa: E731
    if d2 is not None:
        return f"{col} >= {q(d1)} AND {col} < {q(_next(d2, unit))}" if d1 <= d2 else None
    return {"=": f"{col} >= {q(d1)} AND {col} < {q(_next(d1, unit))}", ">=": f"{col} >= {q(d1)}", ">": f"{col} >= {q(_next(d1, unit))}",
            "<": f"{col} < {q(d1)}", "<=": f"{col} < {q(_next(d1, unit))}"}.get(op)


def _year(y: str):
    return _dt.date(int(y), 1, 1) if 1 <= int(y) <= 9998 else None


DAY_FN = (r"(?:\b(date)\s*\(\s*" + COL + r"\s*\)|\b(cast)\s*\(\s*" + COL + r"\s+as\s+date\s*\)|" + COL + r"\s*(::)\s*date\b)")
YEAR_FN = (r"(?:\b(year)\s*\(\s*" + COL + r"\s*\)|\b(extract)\s*\(\s*year\s+from\s+" + COL + r"\s*\)|"
           r"\b(date_part)\s*\(\s*'year'\s*,\s*" + COL + r"\s*\))")
MONTH_FN = (r"(?:\bmonth\s*\(\s*" + COL + r"\s*\)|\bextract\s*\(\s*month\s+from\s+" + COL + r"\s*\)|"
            r"\bdate_part\s*\(\s*'month'\s*,\s*" + COL + r"\s*\))")
FMT_FN = (r"(?:\b(date_format)\s*\(\s*" + COL + r"\s*,\s*'([^']{2,10})'\s*\)|\b(to_char)\s*\(\s*" + COL +
          r"\s*,\s*'([^']{4,10})'\s*\))")
TODAY = r"(curdate\s*\(\s*\)|current_date\b(?:\s*\(\s*\))?)"


def _first(*xs):
    return next((x for x in xs if x), None)


def _fn_title(fn: str) -> tuple[str, str]:
    fn = fn.upper()
    return (f"{fn}() on a column in WHERE blocks the index", f"WHERE mein column pe {fn}() index ko rok deta hai")


def _sargable(sql: str, s: str, F, eng) -> list:
    """Conditions that wrap the column, rewritten on the bare column with exactly the same rows."""
    spans = _where_spans(s)
    if not spans:
        return []
    found = []                                                   # (start, end, new, fn label, column, kind of rewrite)

    def take(m, new, fn, col, how):
        a, b = m.start(), m.end()
        if new is None or not _in_where(spans, a, b) or not _starts_condition(s, a) or not _ENDS.match(s[b:]):
            return
        if not _is_column(col) or any(x < b and a < y for x, y, *_r in found):
            return
        if " OR " in new or (" AND " in new and re.search(r"\bnot\s*$", s[max(0, a - 5):a], re.I)):
            new = f"({new})"                                  # AND binds tighter than OR: a range stands as one condition
        found.append((a, b, new, fn, _bare(col), how))

    # ---- year + month of one column side by side: one month
    for m in re.finditer(YEAR_FN + r"\s*=\s*(\d{4})\s+and\s+" + MONTH_FN + r"\s*=\s*(\d{1,2})\b", s, re.I):
        g = m.groups()
        yc, y = _first(g[1], g[3], g[5]), g[6]
        mc, mo = _first(g[7], g[8], g[9]), g[10]
        if _bare(yc).lower() == _bare(mc).lower() and 1 <= int(mo) <= 12 and _year(y):
            col = sql[m.start(2 if g[1] else 4 if g[3] else 6):m.end(2 if g[1] else 4 if g[3] else 6)]
            take(m, _bounds(col, "month", "=", _dt.date(int(y), int(mo), 1)), (g[0] or g[2] or g[4]).upper(), col, "month")
    for m in re.finditer(MONTH_FN + r"\s*=\s*(\d{1,2})\s+and\s+" + YEAR_FN + r"\s*=\s*(\d{4})\b", s, re.I):
        g = m.groups()
        mc, mo = _first(g[0], g[1], g[2]), g[3]
        yc, y = _first(g[5], g[7], g[9]), g[10]
        if _bare(yc).lower() == _bare(mc).lower() and 1 <= int(mo) <= 12 and _year(y):
            k = 6 if g[5] else 8 if g[7] else 10
            col = sql[m.start(k):m.end(k)]
            take(m, _bounds(col, "month", "=", _dt.date(int(y), int(mo), 1)), (g[4] or g[6] or g[8]).upper(), col, "month")
    # ---- a whole year
    for m in re.finditer(YEAR_FN + r"\s*(?:(between)\s+(\d{4})\s+and\s+(\d{4})|" + OPS + r"\s*(\d{4}))\b", s, re.I):
        g = m.groups()
        k = 2 if g[1] else 4 if g[3] else 6
        col = sql[m.start(k):m.end(k)]
        fn = (g[0] or g[2] or g[4]).upper()
        if g[6]:
            y1, y2 = _year(g[7]), _year(g[8])
            new = _bounds(col, "year", None, y1, y2) if y1 and y2 else None
        else:
            y = _year(g[10])
            new = _bounds(col, "year", g[9], y) if y else None
        take(m, new, fn, col, "year")
    # ---- one day: DATE(c), CAST(c AS DATE), c::date
    for m in re.finditer(DAY_FN + r"\s*(?:(between)\s+'([^']{10})'\s+and\s+'([^']{10})'|" + OPS +
                         r"\s*(?:'([^']{10})'|" + TODAY + "))", s, re.I):
        g = m.groups()
        k = 2 if g[1] else 4 if g[3] else 5
        col = sql[m.start(k):m.end(k)]
        fn = "DATE" if g[0] else "CAST" if g[2] else "::date"
        if g[6]:
            d1, d2 = _day(sql[m.start(8):m.end(8)]), _day(sql[m.start(9):m.end(9)])
            new = _bounds(col, "day", None, d1, d2) if d1 and d2 else None
        elif g[10]:
            d = _day(sql[m.start(11):m.end(11)])
            new = _bounds(col, "day", g[9], d) if d else None
        else:                                                        # = CURDATE() / CURRENT_DATE: today, on both engines
            nxt = "CURRENT_DATE + INTERVAL '1' DAY"
            new = {"=": f"{col} >= CURRENT_DATE AND {col} < {nxt}", ">=": f"{col} >= CURRENT_DATE", ">": f"{col} >= {nxt}",
                   "<": f"{col} < CURRENT_DATE", "<=": f"{col} < {nxt}"}.get(g[9])
        take(m, new, fn, col, "day")
    # ---- DATE_FORMAT(c, '%Y-%m') = '2024-03' / TO_CHAR(c, 'YYYY-MM') = '2024-03'
    for m in re.finditer(FMT_FN + r"\s*" + OPS + r"\s*'([^']*)'", s, re.I):
        g = m.groups()
        fn = (g[0] or g[3]).upper()
        k = 2 if g[0] else 5
        col = sql[m.start(k):m.end(k)]
        fk = 3 if g[0] else 6
        raw = sql[m.start(fk):m.end(fk)]
        fmt = {"%Y-%m-%d": "YYYY-MM-DD", "%Y-%m": "YYYY-MM", "%Y": "YYYY"}.get(raw, raw) if g[0] else raw.upper()
        lit = sql[m.start(8):m.end(8)]
        unit = {"YYYY-MM-DD": "day", "YYYY-MM": "month", "YYYY": "year"}.get(fmt)
        if unit is None or (g[0] and raw not in ("%Y-%m-%d", "%Y-%m", "%Y")):
            continue
        shape = {"day": r"(\d{4})-(\d{2})-(\d{2})", "month": r"(\d{4})-(\d{2})", "year": r"(\d{4})"}[unit]
        mm = re.fullmatch(shape, lit)
        new = None
        if mm:
            try:
                parts = [int(x) for x in mm.groups()] + [1, 1]
                d = _dt.date(parts[0], parts[1], parts[2]) if unit != "year" else _dt.date(parts[0], 1, 1)
                if unit == "month":
                    d = _dt.date(parts[0], parts[1], 1)
                new = _bounds(col, unit, g[6], d)
            except ValueError:
                new = None
        take(m, new, fn, col, unit)
    # ---- arithmetic on the column: c + 5 = 20 -> c = 15
    for m in re.finditer(r"(?<![\w.`\"')])" + COL + r"\s*([+\-*])\s*" + NUM + r"\s*" + OPS + r"\s*" + NUM + r"(?![\w.(])", s):
        col, sign, k, op, v = m.groups()
        try:
            k, v = Decimal(sql[m.start(3):m.end(3)]), Decimal(sql[m.start(5):m.end(5)])
        except InvalidOperation:
            continue
        if sign == "*":
            if k <= 0:
                continue
            r = v / k
            if r * k != v or abs(r.as_tuple().exponent) > 6:
                continue
        else:
            r = v - k if sign == "+" else v + k
        val = format(r.normalize(), "f") if r != r.to_integral_value() else str(int(r))
        take(m, f"{sql[m.start(1):m.end(1)]} {op} {val}", "arithmetic", col, "arith")
    for m in re.finditer(r"(?<![\w.`\"')*/%-])" + NUM + r"\s*\+\s*" + COL + r"\s*" + OPS + r"\s*" + NUM + r"(?![\w.(])", s):
        try:
            k, v = Decimal(sql[m.start(1):m.end(1)]), Decimal(sql[m.start(4):m.end(4)])
        except InvalidOperation:
            continue
        r = v - k
        val = format(r.normalize(), "f") if r != r.to_integral_value() else str(int(r))
        take(m, f"{sql[m.start(2):m.end(2)]} {m.group(3)} {val}", "arithmetic", m.group(2), "arith")
    # ---- COALESCE(c, d) op v / IFNULL(c, d) op v
    for m in re.finditer(r"\b(coalesce|ifnull)\s*\(\s*" + COL + r"\s*,\s*(" + NUM[1:-1] + r"|'[^']*')\s*\)\s*" + OPS +
                         r"\s*(" + NUM[1:-1] + r"|'[^']*')(?![\w.(])", s, re.I):
        fn, col, d, op, v = m.group(1), sql[m.start(2):m.end(2)], sql[m.start(3):m.end(3)], m.group(4), sql[m.start(5):m.end(5)]
        holds = _const_cmp(d, op, v)
        if holds is None or not holds and re.search(r"\bnot\s*$", s[max(0, m.start() - 5):m.start()], re.I):
            continue                                          # NOT (c = v) is unknown for a NULL c, NOT (COALESCE(..) = v) is true
        new = f"{col} {op} {v}" if not holds else f"{col} {op} {v} OR {col} IS NULL"
        take(m, new, fn.upper(), col, "null")
    # ---- LEFT(c, n) = 'abc' / SUBSTRING(c, 1, n) = 'abc' -> c LIKE 'abc%'
    for m in re.finditer(r"\b(left)\s*\(\s*" + COL + r"\s*,\s*(\d+)\s*\)\s*=\s*'([^']*)'|\b(substring|substr)\s*\(\s*" + COL +
                         r"\s*(?:,\s*1\s*,|\s+from\s+1\s+for)\s*(\d+)\s*\)\s*=\s*'([^']*)'", s, re.I):
        g = m.groups()
        fn = (g[0] or g[4]).upper()
        k = 2 if g[0] else 6
        col = sql[m.start(k):m.end(k)]
        n = int(g[2] or g[6])
        lit = sql[m.start(4):m.end(4)] if g[0] else sql[m.start(8):m.end(8)]
        if len(lit) != n or not re.fullmatch(r"[A-Za-z0-9@.\-:/+# ]+", lit) or lit.endswith(" ") or lit.startswith(" "):
            continue
        take(m, f"{col} LIKE '{lit}%'", fn, col, "prefix")

    out = []
    for a, b, new, fn, col, how in sorted(found):
        ev = _short(sql[a:b], 80)
        if fn == "arithmetic":
            ten, thi = "Arithmetic on a column in WHERE blocks the index", "WHERE mein column pe hisaab (arithmetic) index ko rok deta hai"
        elif fn == "::date":
            ten, thi = "::date on a column in WHERE blocks the index", "WHERE mein column pe ::date index ko rok deta hai"
        else:
            ten, thi = _fn_title(fn)
        why_en = {"year": "the whole year is one range of dates", "month": "the whole month is one range of dates",
                  "day": "the whole day is one range of times, from its midnight up to (not including) the next midnight",
                  "arith": "moving the number to the other side gives the same rows",
                  "null": "the rows the default stands for are the rows where the column IS NULL",
                  "prefix": "a fixed first part is a LIKE that starts with it"}[how]
        why_hi = {"year": "poora saal dates ki ek range hai", "month": "poora mahina dates ki ek range hai",
                  "day": "poora din ek range hai: us din ki aadhi raat se agle din ki aadhi raat tak (usse pehle)",
                  "arith": "number ko doosri taraf le jaane se wahi rows aati hain",
                  "null": "default wali rows wahi hain jinka column NULL hai", "prefix": "shuru ka hissa LIKE 'abc%' ban jaata hai"}[how]
        extra_en = ""
        if how == "prefix":
            extra_en = " " + _says("like_prefix_index", eng)
        if fn in ("DATE", "YEAR", "LEFT") and eng != "postgresql":
            extra_en += " " + _rule("mysql", "optimizer_sargable").get("says", "")
        out.append((F("check", "sargable", ten, thi, ev,
                      f"An index holds {col} as it is stored, so a condition on {_short(sql[a:b], 40)} has to work out a new value for "
                      f"every row and can't use it (MariaDB shows type ALL or a full 'index' scan, PostgreSQL a Seq Scan). The same rows, "
                      f"said on the bare column ({why_en}): {new}. This returns exactly the same rows on MySQL, MariaDB and PostgreSQL, "
                      f"and an index on {col} can now be used.{extra_en}",
                      f"Index mein {col} waisa hi rakha hota hai jaisa store hai, isliye {_short(sql[a:b], 40)} har row ke liye nayi "
                      f"value banata hai aur index use nahi hota (poori table padhi jaati hai). Wahi rows seedhe column pe ({why_hi}): "
                      f"{new}. Result bilkul wahi rahega, aur ab {col} ka index kaam aayega."), [(a, b, new)]))
    return out


def _const_cmp(d: str, op: str, v: str):
    """Is the default d op v true (the NULL rows match), false, or None when it can't be said the same on every engine."""
    if d.startswith("'") != v.startswith("'"):
        return None
    if d.startswith("'"):
        a, b = d[1:-1], v[1:-1]
        if op not in ("=", "<>", "!=") or a != a.strip() or b != b.strip():
            return None
        if a != b and a.lower() == b.lower():
            return None                                   # equal on a case-blind collation (MySQL), not on PostgreSQL
        return (a == b) == (op == "=")
    x, y = Decimal(d), Decimal(v)
    return {"=": x == y, "<>": x != y, "!=": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]


# ------------------------------------------------------------------------------------------------ OR across two tables
def _top_split(text: str, word: str) -> list[tuple[int, int]]:
    """Spans of text split on a top-level AND / OR (BETWEEN x AND y is one piece)."""
    C = _C()
    dep = C._depths(text)
    cuts, between = [], 0
    for m in re.finditer(r"\b(between|and|or)\b", text, re.I):
        if dep[m.start()] != 0:
            continue
        w = m.group(1).lower()
        if w == "between":
            between += 1
        elif w == "and" and between:
            between -= 1
        elif w == word:
            cuts.append((m.start(), m.end()))
    out, last = [], 0
    for a, b in cuts:
        out.append((last, a))
        last = b
    out.append((last, len(text)))
    return out


def _strip_parens(text: str, a: int, b: int) -> tuple[int, int]:
    C = _C()
    while True:
        t = text[a:b]
        lead = len(t) - len(t.lstrip())
        trail = len(t) - len(t.rstrip())
        a2, b2 = a + lead, b - trail
        if a2 < b2 and text[a2] == "(" and C._close(text, a2) == b2 - 1:
            a, b = a2 + 1, b2 - 1
            continue
        return a2, b2


def _or_union(sql: str, s: str, F, eng) -> list:
    C = _C()
    end = len(s.rstrip().rstrip(";").rstrip())
    sql, s = sql[:end], s[:end]                                # the closing ';' is not part of the last condition
    flat = C._no_subqueries(s)
    if not re.match(r"\s*select\b", s, re.I) or re.search(r"\b(union|intersect|except|group\s+by|having|distinct|over|for\s+update|"
                                                        r"into|window)\b", flat, re.I):
        return []
    dep = C._depths(s)
    cl = C._clauses(s, dep)
    if "where" not in cl or "from" not in cl or not re.search(r"\bwhere\s*$", s[:cl["where"][0]], re.I):
        return []
    sel = s[cl["select"][0]:cl["select"][1]]
    if C.AGG.search(C._no_subqueries(sel)):
        return []
    srcs = C._sources(s, dep, cl["from"][0], cl["from"][1])
    if len(srcs) < 2 or any(not x.name for x in srcs):
        return []
    aliases = {x.alias.lower(): x for x in srcs if x.alias}
    wa, wb = cl["where"]
    wtext = s[wa:wb]
    ors = _top_split(wtext, "or")
    if len(ors) >= 2:                                         # WHERE A OR B (A may be x AND y: AND binds first)
        parts, others = ors, []
    else:                                                     # WHERE p AND (A OR B)
        conj = _top_split(wtext, "and")
        parts, others = None, []
        for a, b in conj:
            a2, b2 = _strip_parens(wtext, a, b)
            sub = _top_split(wtext[a2:b2], "or")
            if len(sub) >= 2 and (a2, b2) != (a, b) or len(sub) >= 2 and len(conj) == 1:
                if parts is not None:
                    return []                                 # two ORs: left as it is
                parts = [(a2 + x, a2 + y) for x, y in sub]
            else:
                others.append(_flat(sql[wa + a:wa + b]))
        if parts is None:
            return []
    owners = []
    for x, y in parts:
        piece = wtext[x:y]
        if re.search(r"\(\s*select\b", piece, re.I) or not piece.strip():
            return []
        refs = {q.lower() for q, _c, _p in C._qrefs(piece)}
        bare = [w for w in C._bare(piece, set()) if w.lower() not in NOT_COLUMN]
        if bare or len(refs) != 1 or not refs <= set(aliases):
            return []                                         # a side that reads two tables, or names a column without its table
        owners.append(next(iter(refs)))
    if len(set(owners)) < 2:
        return []
    head = sql[:wa - 5].rstrip()                              # SELECT ... FROM ... JOIN ..., without 'WHERE'
    sides = [_flat(sql[wa + x:wa + y]) for x, y in parts]
    branches = []
    for i, side in enumerate(sides):
        cond = others + [f"({side})" if others or i else side] + [f"({p}) IS NOT TRUE" for p in sides[:i]]
        branches.append(f"{head}\nWHERE {' AND '.join(cond)}")
    body = "\nUNION ALL\n".join(branches)
    order, limit = cl.get("order by"), cl.get("limit")
    if order or limit:
        if not order:
            return []                                         # LIMIT with no order: any rows; nothing to keep the same
        names = []
        for _o, it in C._split_top(sql[cl["select"][0]:cl["select"][1]]):
            it = it.strip()
            if it.endswith("*"):
                return []
            am = re.search(r"\s(?:as\s+)?[`\"]?([A-Za-z_]\w*)[`\"]?\s*$", it, re.I)
            plain = re.fullmatch(r"(?:[`\"]?\w+[`\"]?\.)?[`\"]?\w+[`\"]?", it)
            names.append((_bare(it) if plain or not am else am.group(1)).lower())
        if len(set(names)) != len(names):
            return []                                         # two output columns with one name: no outer ORDER BY can name them
        terms = []
        for _o, t in C._split_top(sql[order[0]:order[1]]):
            tm = re.fullmatch(r"\s*" + COL + r"(\s+(?:asc|desc))?\s*", t, re.I)
            if not tm or _bare(tm.group(1)).lower() not in names:
                return []
            terms.append(_bare(tm.group(1)) + (tm.group(2) or ""))
        lim = sql[limit[0]:limit[1]].strip().rstrip(";").strip() if limit else ""
        new = f"SELECT *\nFROM (\n{body}\n) AS u\nORDER BY {', '.join(terms)}" + (f"\nLIMIT {lim}" if lim else "")
    else:
        if sql[wb:].strip().rstrip(";").strip():
            return []
        new = body
    ev = _short(sql[wa + parts[0][0]:wa + parts[-1][1]], 90)
    tables = " and ".join(dict.fromkeys(aliases[o].name for o in owners))
    return [(F("check", "or_union", f"OR across two tables ({tables}) can't use an index",
               f"Do tables ({tables}) ke beech OR index use nahi kar sakta", ev,
               f"Each side of this OR filters a different table, so no single index can find the rows: MariaDB reads one table in "
               f"full and PostgreSQL hash-joins two full table scans (lab-checked with 20,000 rows). Written as UNION ALL, each part "
               f"finds its rows through its own index; the second part leaves out rows the first already returned "
               f"((...) IS NOT TRUE), so every row comes back exactly as often as before, on MySQL, MariaDB and PostgreSQL. It helps "
               f"when both columns have an index: compare the two with EXPLAIN.",
               f"OR ki har taraf alag table ko filter karti hai, isliye koi ek index rows nahi dhoondh sakta: dono databases tables "
               f"poori padhte hain. UNION ALL mein har hissa apne index se rows dhoondhta hai; doosra hissa pehle wali rows chhod deta "
               f"hai ((...) IS NOT TRUE), isliye result bilkul wahi rehta hai. Dono columns pe index ho tab faayda hai: EXPLAIN se "
               f"milao."), [(0, len(sql.rstrip().rstrip(';').rstrip()), new)])]


# ------------------------------------------------------------------------------------------------ deep OFFSET -> keyset
_LIMIT = re.compile(r"\blimit\s+(\d+)\s*,\s*(\d+)\s*$|\blimit\s+(\d+)\s+offset\s+(\d+)\s*$|\boffset\s+(\d+)\s+rows?\s+fetch\s+"
                    r"(?:first|next)\s+(\d+)\s+rows?\s+only\s*$|\boffset\s+(\d+)\s+limit\s+(\d+)\s*$", re.I)


def _key_like(col: str, table: str) -> bool:
    """'id', or the table's own <singular>_id (students.student_id, wp_users.user_id, staff.staffId): the row's key."""
    from app.agents.sql import _likely_primary

    parts = table.lower().split("_")
    return any(_likely_primary("_".join(parts[i:]), _bare(col)) for i in range(len(parts)))


def keyset_sql(sql: str, eng=None, min_offset: int = 1000):
    """(new query, the order columns, n, m) for a single-table SELECT ... ORDER BY <cols ending in the key> LIMIT n OFFSET m,
    paged by the last row seen (named placeholders :last_<col>), or None."""
    C = _C()
    s = C._mask(sql)
    body = s.rstrip().rstrip(";").rstrip()
    lm = _LIMIT.search(body)
    if not lm or not re.match(r"\s*select\b", s, re.I):
        return None
    g = lm.groups()
    m, n = ((int(g[0]), int(g[1])) if g[0] else (int(g[3]), int(g[2])) if g[2] else (int(g[4]), int(g[5])) if g[4]
            else (int(g[6]), int(g[7])))
    if m < min_offset or re.search(r"\b(union|intersect|except|group\s+by|having|distinct|over|window|for\s+update)\b",
                                   C._no_subqueries(s), re.I):
        return None
    dep = C._depths(s)
    cl = C._clauses(s, dep)
    if "order by" not in cl or "from" not in cl:
        return None
    srcs = C._sources(s, dep, cl["from"][0], cl["from"][1])
    if len(srcs) != 1 or not srcs[0].name:
        return None
    table = srcs[0].name.split(".")[-1]
    oa, ob = cl["order by"][0], lm.start()
    if ob <= oa:
        return None
    terms = []
    for _o, t in C._split_top(sql[oa:ob]):
        tm = re.fullmatch(r"\s*" + COL + r"(?:\s+(asc|desc))?\s*", t, re.I)
        if not tm or not _is_column(tm.group(1)):
            return None
        terms.append((tm.group(1), (tm.group(2) or "asc").lower()))
    if not terms or len({d for _c, d in terms}) != 1 or not _key_like(terms[-1][0], table):
        return None
    op = ">" if terms[0][1] == "asc" else "<"
    ph = [f":last_{_bare(c).lower()}" for c, _d in terms]
    cols = [c for c, _d in terms]
    if len(cols) == 1:
        cond = f"{cols[0]} {op} {ph[0]}"
    elif eng == "postgresql":
        cond = f"({', '.join(cols)}) {op} ({', '.join(ph)})"
    else:
        ors = []
        for i in range(len(cols)):
            ors.append(" AND ".join([f"{cols[j]} = {ph[j]}" for j in range(i)] + [f"{cols[i]} {op} {ph[i]}"]))
        cond = "(" + " OR ".join(f"({x})" if " AND " in x else x for x in ors) + ")"
    om = re.search(r"\border\s+by\s*$", s[:oa], re.I)
    if not om:
        return None
    if "where" in cl:
        wa, wb = cl["where"]
        head = sql[:wa].rstrip() + " (" + sql[wa:wb].strip() + f") AND {cond}\n" + sql[wb:om.start()].strip()
    else:
        head = sql[:om.start()].rstrip() + f"\nWHERE {cond}"
    new = f"{head.rstrip()}\nORDER BY {_flat(sql[oa:ob])}\nLIMIT {n}"
    return new, cols, n, m


def _keyset(sql: str, s: str, F, eng) -> list:
    C = _C()
    body = s.rstrip().rstrip(";").rstrip()
    lm = _LIMIT.search(body)
    if not lm or not re.match(r"\s*select\b", s, re.I):
        return []
    g = lm.groups()
    m = int(g[0] or g[3] or g[4] or g[6])
    if m < 1000 or not re.search(r"\border\s+by\b", s, re.I):
        return []
    got = keyset_sql(sql, eng)
    ev = _short(sql[lm.start():lm.end()], 60)
    if got is None:
        return [(F("check", "keyset", f"OFFSET {m} reads {m:,} rows and throws them away", f"OFFSET {m} {m:,} rows padh ke phenk deta hai",
                   ev, f"To return the page after row {m:,} the database still finds and sorts every row before it (MariaDB's EXPLAIN shows "
                       f"rows = OFFSET + LIMIT, PostgreSQL's Limit node reads them all), so each page is slower than the one before. Page by "
                       f"the last row seen instead (keyset): sort by columns that end with a unique one (the table's key, as a tie-breaker), "
                       f"remember the last row's values, and ask for WHERE (sort columns) > (those values) ... LIMIT n. It returns the same "
                       f"page and reads only that page.",
                   f"Row {m:,} ke baad wala page dene ke liye database pehle ki saari rows dhoondh ke sort karta hai, isliye har agla page "
                   f"aur slow hota hai. Pichli row ke hisaab se page karo (keyset): sort ke aakhir mein unique column (key) rakho, aakhri "
                   f"row ki values yaad rakho, aur WHERE (sort columns) > (wo values) ... LIMIT n maango. Wahi page aata hai."), [])]
    new, cols, n, _m = got
    names = ", ".join(f":last_{_bare(c).lower()}" for c in cols)
    return [(F("check", "keyset", f"OFFSET {m} reads {m:,} rows and throws them away", f"OFFSET {m} {m:,} rows padh ke phenk deta hai",
               ev, f"To return rows {m + 1:,}-{m + n:,} the database still finds and sorts the {m:,} rows before them (MariaDB's EXPLAIN shows "
                   f"rows = OFFSET + LIMIT, PostgreSQL's Limit node reads them all), so each page is slower than the one before. Keyset "
                   f"paging gives the same page and reads only it: keep the sort, and ask for the rows after the last one already shown "
                   f"({names} = that row's values, which your app remembers from the previous page). It needs the sort to end with a "
                   f"unique column (it does: {cols[-1]}) and those columns to have no NULLs; "
                   + (f"the primary key's own index makes each page one short index read. " if len(cols) == 1 else
                      f"an index on ({', '.join(_bare(c) for c in cols)}) makes each page one short index read. ")
                   + "Proven on MariaDB and PostgreSQL: the same page as the OFFSET query.",
               f"Rows {m + 1:,}-{m + n:,} dene ke liye database pehle ki {m:,} rows dhoondh ke sort karta hai, isliye har agla page aur slow. "
               f"Keyset paging wahi page deta hai aur sirf wahi padhta hai: sort wahi rakho aur pichle page ki aakhri row ke baad wali rows "
               f"maango ({names} = us row ki values, jo app yaad rakhti hai). Sort ke aakhir mein unique column ({cols[-1]}) hona chahiye "
               f"aur un columns mein NULL nahi; ({', '.join(_bare(c) for c in cols)}) pe index ho to har page ek chhota index read hai."),
             [(0, len(sql.rstrip().rstrip(';').rstrip()), new)])]


# ------------------------------------------------------------------------------------------------ entry (query alone)
def speed(sql: str, s: str, intent: str, F, is_select: bool) -> list:
    """Speed findings with a rewrite that returns the same rows: [(Finding, [edits])]."""
    eng = _engine(sql)
    out = _sargable(sql, s, F, eng)
    if is_select:
        out += _or_union(sql, s, F, eng)
        out += _keyset(sql, s, F, eng)
    return out


# ------------------------------------------------------------------------------------------------ with the table structure
_TN = r"[`\"]?(?:\w+[`\"]?\.[`\"]?)?(\w+)[`\"]?"


def structure(extra: str) -> dict:
    """{table: {"cols": {name: type}, "indexes": [[col, ...], ...]}} from pasted CREATE TABLE / CREATE INDEX / ALTER TABLE /
    pg_dump text (names in lower case)."""
    from app.agents.sql import _strip_comments

    text = _strip_comments(extra or "")
    out: dict = {}
    for m in re.finditer(rf"create\s+(?:temporary\s+)?table\s+(?:if\s+not\s+exists\s+)?{_TN}\s*\(", text, re.I):
        C = _C()
        a = m.end() - 1
        try:
            b = C._close(text, a)
        except Exception:  # noqa: BLE001
            continue
        t = out.setdefault(m.group(1).lower(), {"cols": {}, "indexes": []})
        for _o, part in C._split_top(text[a + 1:b]):
            p = part.strip()
            km = re.match(r"(?:constraint\s+[`\"\w]+\s+)?(primary\s+key|unique(?:\s+(?:key|index))?|key|index)\s*[`\"\w]*\s*"
                          r"(?:using\s+\w+\s*)?\(([^)]*)\)", p, re.I)
            if km:
                t["indexes"].append([re.sub(r"\(\d+\)|\s+(asc|desc)$", "", c.strip(), flags=re.I).strip('`" ').lower()
                                     for c in km.group(2).split(",")])
                continue
            cm = re.match(r"[`\"]?(\w+)[`\"]?\s+([A-Za-z]+(?:\s+(?:varying|precision))?)", p)
            if cm and cm.group(1).lower() not in ("constraint", "foreign", "check", "fulltext", "spatial", "exclude"):
                t["cols"][cm.group(1).lower()] = cm.group(2).lower()
                if re.search(r"\bprimary\s+key\b|\bunique\b", p, re.I):
                    t["indexes"].append([cm.group(1).lower()])
    for m in re.finditer(rf"create\s+(?:unique\s+)?index\s+(?:concurrently\s+)?(?:if\s+not\s+exists\s+)?[`\"\w]*\s*on\s+(?:only\s+)?"
                         rf"{_TN}\s*(?:using\s+\w+\s*)?\(([^)]*)\)", text, re.I):
        out.setdefault(m.group(1).lower(), {"cols": {}, "indexes": []})["indexes"].append(
            [re.sub(r"\(\d+\)|\s+(asc|desc)$|\s+\w+_ops$", "", c.strip(), flags=re.I).strip('`" ').lower() for c in m.group(2).split(",")])
    for m in re.finditer(rf"alter\s+table\s+(?:only\s+)?{_TN}\s+add\s+(?:constraint\s+[`\"\w]+\s+)?(primary\s+key|unique(?:\s+key)?|"
                         rf"index|key)\s*[`\"\w]*\s*\(([^)]*)\)", text, re.I):
        out.setdefault(m.group(1).lower(), {"cols": {}, "indexes": []})["indexes"].append(
            [c.strip().strip('`" ').lower() for c in m.group(3).split(",")])
    return out


TEXT_TYPES = re.compile(r"^(char|varchar|character|text|tinytext|mediumtext|longtext|nchar|nvarchar|citext)", re.I)


def index_fit(query: str, extra: str, eng=None) -> list[dict]:
    """With the structure pasted: why an index that exists can't serve this query. Speed findings (dicts like SqlFinding)."""
    from app.agents.sql import _clause, _mask_strings, _strip_comments, _tables

    st = structure(extra)
    if not st:
        return []
    sql = _strip_comments(query)
    masked = _mask_strings(sql)
    tables = _tables(masked)
    where = _clause(masked, "where", ("group by", "order by", "limit", "having", "union", "returning"))
    raw_where = _clause(sql, "where", ("group by", "order by", "limit", "having", "union", "returning"))
    out = []
    eq, rng = {}, {}

    def owner(col):
        if "." in col:
            al, c = col.split(".", 1)
            t = next((x for x in tables if x.alias.lower() == al.strip('`"').lower() or x.name.split(".")[-1].lower() == al.lower()), None)
            return (t.name.split(".")[-1].lower() if t else None), c.strip('`"').lower()
        if len(tables) == 1:
            return tables[0].name.split(".")[-1].lower(), col.strip('`"').lower()
        hits = [t.name.split(".")[-1].lower() for t in tables if col.strip('`"').lower() in st.get(t.name.split(".")[-1].lower(), {}).get("cols", {})]
        return (hits[0] if len(hits) == 1 else None), col.strip('`"').lower()

    for piece in re.split(r"\band\b", re.sub(r"\bbetween\s+(\S+)\s+and\s+", r"between \1 _and_ ", where, flags=re.I), flags=re.I):
        m = re.match(r"\s*\(?\s*" + COL + r"\s*(=|<=>|\bin\b|<=|>=|<|>|\bbetween\b|\blike\s+'(?!%))", piece, re.I)
        if not m or re.search(r"\bor\b", piece, re.I):
            continue
        t, c = owner(m.group(1))
        if not t:
            continue
        (eq if m.group(2).strip().lower() in ("=", "<=>", "in") else rng).setdefault(t, []).append(c)
    # a text column compared with a bare number
    for m in re.finditer(r"(?<![\w.'])" + COL + r"\s*(=|<>|!=|\bin\s*\()\s*(-?\d+(?:\.\d+)?)(?![\w.'])", raw_where, re.I):
        t, c = owner(m.group(1))
        ty = st.get(t or "", {}).get("cols", {}).get(c, "")
        if ty and TEXT_TYPES.match(ty):
            pg = eng == "postgresql"
            out.append({"severity": 3, "topic": "implicit_cast", "title_en": f"{c} is text but is compared with a number",
                        "title_hi": f"{c} text hai par number se compare ho raha hai", "evidence": _short(m.group(0), 60),
                        "fix_en": (f"{c} is {ty.upper()} in your table. " + _says("text_vs_number", eng) +
                                   f" Put the value in quotes: {m.group(1)} {m.group(2).strip()} '{m.group(3)}'." +
                                   ("" if pg else " Then the index on it can be used, and only the exact text matches.")),
                        "fix_hi": (f"Aapki table mein {c} {ty.upper()} hai. MySQL/MariaDB har row ko number banake compare karte hain: "
                                   f"index use nahi hota aur '098' ya '98 ' jaisi values bhi match ho jaati hain; PostgreSQL error deta hai "
                                   f"(42883). Value quotes mein likho: {m.group(1)} {m.group(2).strip()} '{m.group(3)}'.")})
    for t, info in st.items():
        used = set(eq.get(t, [])) | set(rng.get(t, []))
        if not used or t not in {x.name.split(".")[-1].lower() for x in tables}:
            continue
        idx = [ix for ix in info["indexes"] if ix]
        if any(ix[0] in used for ix in idx):
            for ix in idx:
                if len(ix) >= 2 and ix[0] in rng.get(t, []) and ix[0] not in eq.get(t, []) and any(c in eq.get(t, []) for c in ix[1:]):
                    later = [c for c in ix[1:] if c in eq.get(t, [])]
                    better = later + [ix[0]] + [c for c in ix[1:] if c not in later]
                    out.append({"severity": 2, "topic": "index_range_first",
                                "title_en": f"Only {ix[0]} of the index ({', '.join(ix)}) narrows the search",
                                "title_hi": f"Index ({', '.join(ix)}) ka sirf {ix[0]} search chhota karta hai",
                                "evidence": f"{ix[0]}: a range; {', '.join(later)}: equal to a value",
                                "fix_en": (f"An index is sorted by its first column, then by the next one inside each value. A range on {ix[0]} "
                                           f"covers many values of {ix[0]}, and inside that stretch {', '.join(later)} is not in order, so the "
                                           f"search can't jump to it: it is checked entry by entry (MariaDB's EXPLAIN says 'Using index "
                                           f"condition', PostgreSQL keeps it in the Index Cond but reads the whole range). Put the columns "
                                           f"compared with '=' first: an index on ({', '.join(better)}) finds exactly the rows."),
                                "fix_hi": (f"Index pehle column se sort hota hai, phir har value ke andar agle se. {ix[0]} pe range bahut "
                                           f"values leti hai, aur unke andar {', '.join(later)} kram mein nahi hota, isliye search seedha wahan "
                                           f"nahi jaa sakta. '=' wale columns pehle rakho: ({', '.join(better)}) pe index sahi rows seedha "
                                           f"dhoondhta hai.")})
                    break
            continue
        for ix in idx:
            if len(ix) >= 2 and ix[0] not in used and any(c in used for c in ix[1:]):
                later = [c for c in ix[1:] if c in used]
                out.append({"severity": 2, "topic": "index_prefix",
                            "title_en": f"The index ({', '.join(ix)}) can't find rows by {', '.join(later)} alone",
                            "title_hi": f"Index ({', '.join(ix)}) sirf {', '.join(later)} se rows nahi dhoondh sakta",
                            "evidence": f"{t}: WHERE uses {', '.join(sorted(used))}, not {ix[0]}",
                            "fix_en": (f"An index on ({', '.join(ix)}) is sorted by {ix[0]} first, like a phone book sorted by surname: it "
                                       f"can't jump to a first name. Without a condition on {ix[0]} the database reads the whole index or "
                                       f"the whole table (MariaDB: type 'index' or ALL; PostgreSQL 16: a scan of the entire index or a Seq "
                                       f"Scan; lab-checked). Add an index that starts with {later[0]} (below), or add the {ix[0]} condition "
                                       f"if the query really means one {ix[0]}."),
                            "fix_hi": (f"({', '.join(ix)}) wala index pehle {ix[0]} se sort hai, jaise phone book surname se: pehle naam se "
                                       f"seedha nahi mil sakta. {ix[0]} pe condition na ho to poora index ya poori table padhi jaati hai. "
                                       f"{later[0]} se shuru hone wala index lagao (neeche).")})
                break
    return out


def index_ddl(table: str, name: str, cols: list[str], eng) -> str:
    """The index command in the database's own words: CREATE INDEX on PostgreSQL, ALTER TABLE ... ADD INDEX on MySQL/MariaDB."""
    if eng == "postgresql":
        return f"CREATE INDEX {name} ON {table} ({', '.join(cols)});"
    return f"ALTER TABLE {table} ADD INDEX {name} ({', '.join(cols)});"


def check_query(names: list[str], eng) -> str:
    """Which indexes these tables already have, read-only, in the database's own catalogue."""
    q = ", ".join(f"'{n}'" for n in names)
    if eng == "postgresql":
        return ("SELECT tablename, indexname, indexdef\nFROM pg_indexes\n"
                f"WHERE schemaname = current_schema() AND tablename IN ({q.lower()})\nORDER BY tablename, indexname;")
    return ("SELECT TABLE_NAME, INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns\n"
            "FROM information_schema.STATISTICS\n"
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({q})\n"
            "GROUP BY TABLE_NAME, INDEX_NAME;")
