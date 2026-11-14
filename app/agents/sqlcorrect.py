"""Is the query RIGHT? Read before any speed advice: tables, aliases, joins and columns are worked out from the query
itself (no database needed), then checked for what makes MySQL refuse it or return wrong rows. Each finding says how
sure it is: 'error' (MySQL refuses to run it), 'wrong' (runs, but the rows are wrong), 'check' (probably a typo; only
your table structure can say). A corrected query is written with every guessed name marked for the person to check.
"""

import contextvars
import difflib
import re
from dataclasses import dataclass, field

KW = set("""select from where group by having order limit offset union all distinct as on using join inner left right full
outer cross natural straight_join and or not null is in like between exists case when then else end asc desc true false
interval day days month months year years hour minute second week quarter current_date current_timestamp now with
recursive over partition rows range preceding following unbounded current row div mod xor regexp rlike escape collate
binary unsigned signed char varchar int integer decimal date datetime time timestamp any some if ifnull coalesce
nullif cast convert concat count sum avg min max lower upper trim substring round floor ceil abs length replace
group_concat separator straight values set update delete insert into ignore duplicate key for share lock nowait skip
locked window lateral""".split())
AGG = re.compile(r"\b(count|sum|avg|min|max|group_concat|string_agg|array_agg|bit_and|bit_or|std|stddev|variance)\s*\(", re.I)
JOIN_RE = re.compile(r"\b(?:(natural)\s+)?(?:(left|right|full)(?:\s+outer)?\s+|(inner)\s+|(cross)\s+)?(join|straight_join)\b", re.I)
CLAUSES = ["select", "from", "where", "group by", "having", "order by", "limit", "union", "for update", "window"]


@dataclass
class Source:
    kind: str            # from / join / left / right / full / cross / comma
    name: str            # table name ('' for a subquery)
    alias: str
    on: str = ""         # the ON condition (masked text)
    using: str = ""
    start: int = 0       # where this source starts in the query (its join keyword)
    end: int = 0
    on_start: int = 0
    on_end: int = 0
    refs: set = field(default_factory=set)
    lateral: bool = False   # JOIN LATERAL (SELECT ...): tied to the rows before it inside the subquery, ON may be just TRUE


@dataclass
class Finding:
    kind: str            # error / wrong / check
    topic: str           # join / column / null / group / having / order / alias / shape
    title_en: str
    title_hi: str
    evidence: str
    fix_en: str
    fix_hi: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _no_scalar_minmax(t: str) -> str:
    """max(a, b) / min(a, b) with two or more arguments is the bigger / smaller of those values in each row (SQLite; the
    others refuse it), never a total: renamed (same length) so the total checks don't read it as MAX() / MIN() of a column."""
    out = t
    for m in reversed(list(re.finditer(r"\b(max|min)\s*\(", t, re.I))):
        a = m.end() - 1
        b = _close(t, a)
        if b > a and any(d == 0 and ch == "," for d, ch in zip(_depths(t[a + 1:b]), t[a + 1:b])):
            out = out[:m.start()] + ("mx_" if m.group(1).lower() == "max" else "mn_") + out[m.start() + 3:]   # same length
    return out


def _mask(sql: str) -> str:
    """Same length as the query: strings keep their quotes but their insides become x, comments become spaces."""
    out, i, n = list(sql), 0, len(sql)
    while i < n:
        c = sql[i]
        if c in "'\"":
            j = i + 1
            while j < n and not (sql[j] == c and sql[j - 1] != "\\"):
                j += 1
            for k in range(i + 1, min(j, n)):
                out[k] = "x" if sql[k] != "\n" else "\n"
            i = j + 1
            continue
        if sql.startswith("--", i) or c == "#":
            j = sql.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                out[k] = " " if sql[k] != "\n" else "\n"
            i = j
            continue
        i += 1
    return "".join(out)


def _depths(s: str) -> list[int]:
    d, out = 0, []
    for c in s:
        if c == "(":
            d += 1
        out.append(d)
        if c == ")":
            d = max(0, d - 1)
    return out


def _top(m, dep) -> bool:
    return dep[m.start()] == 0


def _clauses(s: str, dep) -> dict:
    """Top-level clause -> (start, end) of its body."""
    marks = []
    for kw in CLAUSES:
        for m in re.finditer(r"\b" + kw.replace(" ", r"\s+") + r"\b", s, re.I):
            if _top(m, dep):
                marks.append((m.start(), m.end(), kw))
    marks.sort()
    out = {}
    for i, (a, b, kw) in enumerate(marks):
        if kw in out:
            continue
        out[kw] = (b, marks[i + 1][0] if i + 1 < len(marks) else len(s))
    return out


def _split_top(text: str, sep: str = ",") -> list[tuple[int, str]]:
    dep, parts, last = _depths(text), [], 0
    for i, c in enumerate(text):
        if c == sep and dep[i] == 0:
            parts.append((last, text[last:i]))
            last = i + 1
    parts.append((last, text[last:]))
    return parts


def _sources(s: str, dep, a: int, b: int) -> list[Source]:
    body = s[a:b]
    bdep = dep[a:b]
    cuts = [(m.start(), m.end(), m) for m in JOIN_RE.finditer(body) if bdep[m.start()] == 0]
    cuts += [(i, i + 1, None) for i, c in enumerate(body) if c == "," and bdep[i] == 0]
    cuts.sort(key=lambda x: x[0])
    pieces = []
    prev_end, prev_kind, prev_start = 0, "from", 0
    for st, en, m in cuts:
        pieces.append((prev_kind, prev_start, prev_end, st))
        if m is None:
            prev_kind = "comma"
        else:
            nat, side, inner, cross, _j = m.groups()
            prev_kind = (side or "").lower() or ("cross" if cross else "join")
            if nat:
                prev_kind = "natural"
        prev_start, prev_end = st, en
    pieces.append((prev_kind, prev_start, prev_end, len(body)))
    out = []
    for kind, st, head_end, en in pieces:
        text = body[head_end:en]
        lead = len(text) - len(text.lstrip())
        t = text.strip()
        if not t:
            continue
        on_m = re.search(r"\bon\b", t, re.I)
        using_m = re.search(r"\busing\s*\(([^)]*)\)", t, re.I)
        head = t[:on_m.start()] if on_m else (t[:using_m.start()] if using_m else t)
        lateral = re.match(r"lateral\s*(?=\()", head, re.I)
        if lateral:                                    # JOIN LATERAL (SELECT ...) t: a derived table that may read the rows before it
            head = head[lateral.end():]
        if head.startswith("("):
            close = _close(head, 0)
            name, rest = "", head[close + 1:]
        else:
            nm = re.match(r"[`\"]?([\w$]+(?:[`\"]?\.[`\"]?[\w$]+)?)[`\"]?", head)
            name, rest = (nm.group(1).replace("`", "").replace('"', ""), head[nm.end():]) if nm else ("", head)
        am = re.match(r"\s*(?:as\s+)?[`\"]?([A-Za-z_][\w$]*)[`\"]?", rest, re.I)
        alias = am.group(1) if am and am.group(1).lower() not in KW else (name.split(".")[-1] if name else "")
        src = Source(kind=kind, name=name, alias=alias, start=a + st, end=a + en, lateral=bool(lateral))
        if on_m:
            src.on_start = a + head_end + lead + on_m.end()
            src.on_end = a + en
            src.on = s[src.on_start:src.on_end]
        if using_m:
            src.using = using_m.group(1)
        out.append(src)
    return out


def _firm_refs(s: str, where, owners=None) -> list:
    """Column references the WHERE demands (in AND-parts with no OR, IS NULL or COALESCE): a NULL there drops the row."""
    wtext = s[where[0]:where[1]]
    plain_w = _no_subqueries(wtext)
    wd = _depths(plain_w)
    if any(wd[m.start()] == 0 for m in re.finditer(r"\bor\b", plain_w, re.I)):
        return []
    cuts = [0] + [m.end() for m in re.finditer(r"\band\b", plain_w, re.I) if wd[m.start()] == 0 and
                  not re.search(r"\bbetween\s+\S+\s*$", plain_w[:m.start()], re.I)] + [len(plain_w)]
    out = []
    for a1, b1 in zip(cuts, cuts[1:]):
        part = plain_w[a1:b1]
        if not re.search(r"\bor\b", part, re.I) and not re.search(r"\bis\s+null\b|\bcoalesce\s*\(|\bifnull\s*\(|(?:!=|<>|=)\s*null\b", part, re.I):
            out += _qrefs(part, where[0] + a1)
            for m in WORD.finditer(part):          # a column with no table: its owner, if the query names only one
                w = m.group(1).lower()
                if w not in KW and not re.match(r"\s*\.", part[m.end():]) and owners and len(owners.get(w, ())) == 1:
                    out.append((next(iter(owners[w])), m.group(1), where[0] + a1 + m.start()))
    return out


def _grain(srcs, alias_of, removed) -> str | None:
    """The table the result has one row per: every other table is reached one row each from it
    (users u JOIN orders o ON u.id = o.customer_id lists orders). None when no single table is."""
    def is_key(al, col):
        t = next((x for x in srcs if (x.alias or "").lower() == al), None)
        kb = _block_key(t, col)
        if kb is not None:
            return kb
        c = col.lower().replace("_", "")
        return c == "id" or bool(t and t.name and c in {x.replace("_", "") + "id" for x in _singulars(t.name)})

    links = [(alias_of(m.group(1)), m.group(2).lower(), alias_of(m.group(3)), m.group(4).lower())
             for x in srcs if x.on for m in EQ.finditer(x.on)]
    live = [(x.alias or "").lower() for x in srcs if x.alias and x.alias.lower() not in removed]
    found = []
    for g in live:
        got, more = {g}, True
        while more:
            more = False
            for l, lc, r, rc in links:
                for a1, c1, b1 in ((l, lc, r), (r, rc, l)):
                    if a1 not in got and b1 in got and is_key(a1, c1):
                        got.add(a1)
                        more = True
        if set(live) <= got:
            found.append(g)
    return found[0] if len(found) == 1 else None


# night 12: the keys the query's own blocks prove (a CTE grouped by customer_id has one row per customer_id), set by check()
BLOCK_KEYS = contextvars.ContextVar("dbaba_block_keys", default=None)
# night 15: the output columns each CTE / derived table plainly makes (sqlblocks.columns), set with BLOCK_KEYS
BLOCK_COLS = contextvars.ContextVar("dbaba_block_cols", default=None)


def _block_key(src, col: str):
    """True / False when the source is a CTE or derived table whose own GROUP BY / DISTINCT says whether col is unique in
    it (app/agents/sqlblocks.py); None when it isn't one (the name rules decide)."""
    bk = BLOCK_KEYS.get() or {}
    if src is None or not bk:
        return None
    nm = (src.name or src.alias or "").split(".")[-1].strip('`"').lower()
    if nm not in bk:
        return None
    return any(k <= {col.strip('`"').lower()} for k in bk[nm])


def _where_to_having(sql: str, flat: str, wm, rest: str, stop) -> list | None:
    """Night 15: SELECT ... WHERE <a total's condition> [AND <row conditions>] GROUP BY ...: the total's condition moves to
    HAVING (where it is checked once per group, after the totals exist), the row conditions stay in WHERE. Proven the same on
    MariaDB and PostgreSQL (tests/practice/test_night15.py): what the person wrote is refused by both, and HAVING is the one
    place a condition on a total can mean anything. None when it isn't that plain (an OR between the parts, no GROUP BY, a
    HAVING with an OR of its own)."""
    if not stop or not re.match(r"group\s+by\b", rest[stop.start():], re.I):
        return None
    b0, b1 = wm.end(), wm.end() + stop.start()
    body = flat[b0:b1]
    d = _depths(body)
    if any(d[m.start()] == 0 for m in re.finditer(r"\bor\b", body, re.I)):
        return None
    cuts = [0] + [m.end() for m in re.finditer(r"\band\b", body, re.I) if d[m.start()] == 0 and
                  not re.search(r"\bbetween\s+\S+\s*$", body[:m.start()], re.I)] + [len(body)]
    terms = []
    for x, y in zip(cuts, cuts[1:]):
        txt = re.sub(r"^\s*and\b|\band\s*$", "", sql[b0 + x:b0 + y], flags=re.I).strip()
        if txt:
            terms.append(txt)
    moved = [x for x in terms if AGG.search(_mask(x))]
    kept = [x for x in terms if x not in moved]
    if not moved:
        return None
    g0 = b1
    tail = flat[g0:]
    end = re.search(r"\bhaving\b|\border\s+by\b|\blimit\b|\bunion\b|;|$", tail[8:], re.I)
    at = g0 + 8 + end.start()
    edits = []
    if re.match(r"having\b", tail[8 + end.start():], re.I):
        h0 = at + len("having")
        hend = re.search(r"\border\s+by\b|\blimit\b|\bunion\b|;|$", flat[h0:], re.I)
        h1 = h0 + hend.start()
        if re.search(r"\bor\b", flat[h0:h1], re.I):
            return None
        h1 = h0 + len(sql[h0:h1].rstrip())
        edits.append((h1, h1, " AND " + " AND ".join(moved)))
    else:
        at = g0 + 8 + len(sql[g0 + 8:at].rstrip())
        edits.append((at, at, " HAVING " + " AND ".join(moved)))
    w0 = wm.start()
    edits.append((w0, b1, ("WHERE " + " AND ".join(kept) + " ") if kept else ""))
    return edits


def _fold_fanout(F: list) -> None:
    """Three or more totals that the same child table repeats (SUM(f.amount), SUM(f.paid), SUM(f.units) ... each once per
    order_items row) are one mistake with one cure (total the parent before the join): said once, naming the totals."""
    groups = {}
    for f in F:
        if f.topic == "join_fanout":
            m = re.search(r"counts each (.+) row once per (.+) row$", f.title_en)
            if m:
                groups.setdefault(m.groups(), []).append(f)
    for (parent, child), fs in groups.items():
        if len(fs) < 3:
            continue
        names = [re.match(r"(\S+\(.*?\))", f.title_en).group(1) for f in fs]
        first = fs[0]
        first.title_en = f"{', '.join(names[:2])} and {len(names) - 2} more count each {parent} row once per {child} row"
        first.title_hi = f"{', '.join(names[:2])} aur {len(names) - 2} aur: har {parent} row ko har {child} row ke liye ginte hain"
        first.evidence = "; ".join(names)
        for f in fs[1:]:
            F.remove(f)


def _no_subqueries(text: str) -> str:
    """Same length, with every (SELECT ...) blanked out: its names belong to its own scope."""
    out, i = list(text), 0
    while True:
        m = re.search(r"\(\s*select\b", text[i:], re.I)
        if not m:
            return "".join(out)
        a = i + m.start()
        b = _close(text, a)
        for k in range(a, b + 1):
            out[k] = " "
        i = b + 1


def _close(text: str, i: int) -> int:
    d = 0
    for j in range(i, len(text)):
        if text[j] == "(":
            d += 1
        elif text[j] == ")":
            d -= 1
            if d == 0:
                return j
    return len(text) - 1


EQ = re.compile(r"[`\"]?([A-Za-z_]\w*)[`\"]?\.[`\"]?(\w+)[`\"]?\s*=\s*[`\"]?([A-Za-z_]\w*)[`\"]?\.[`\"]?(\w+)[`\"]?")
QREF = re.compile(r"(?<![\w.`\"$])[`\"]?([A-Za-z_][\w$]*)[`\"]?\.[`\"]?([A-Za-z_][\w$]*|\*)[`\"]?(?![\w$`\"(])")
WORD = re.compile(r"(?<![\w.`\"'$@:])([A-Za-z_][\w$]*)(?![\w$`\".]|\s*\()")


def _qrefs(text: str, offset: int = 0) -> list[tuple[str, str, int]]:
    return [(m.group(1), m.group(2), offset + m.start()) for m in QREF.finditer(text)]


def _bare(text: str, skip: set) -> list[str]:
    out = []
    for m in WORD.finditer(text):
        w = m.group(1)
        before = text[:m.start()].rstrip()
        if w.lower() in KW or w.lower() in skip or before.lower().endswith(" as") or before.endswith("."):
            continue
        if re.match(r"\s*\.", text[m.end():]):
            continue
        out.append(w)
    return out


def _slip(a: str, b: str) -> bool:
    """One letter added, missing, wrong or swapped, in names long enough to be the same word (max_mark / max_marks)."""
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1 or a == b or any(ch.isdigit() for ch in a + b):
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    ja, jb = len(a), len(b)
    while ja > i and jb > i and a[ja - 1] == b[jb - 1]:
        ja, jb = ja - 1, jb - 1
    da, db = a[i:ja], b[i:jb]
    return (len(da) <= 1 and len(db) <= 1) or (len(da) == len(db) == 2 and da == db[::-1])


def _singulars(name: str) -> set:
    """Every likely singular: courses -> course, classes -> class, categories -> category, staff -> staff."""
    n = name.lower().split(".")[-1]
    out = {n}
    if "_" in n:                                   # wp_posts, fz11_students: the name after a prefix counts too
        out |= _singulars(n.split("_", 1)[1])
    if n.endswith("ies"):
        out.add(n[:-3] + "y")
    if n.endswith("es"):
        out.add(n[:-2])
    if n.endswith("s") and not n.endswith("ss"):
        out.add(n[:-1])
    return out


def _singular(name: str) -> str:
    n = name.lower().split(".")[-1]
    if n.endswith("ies"):
        return n[:-3] + "y"
    if n.endswith(("ses", "xes", "ches", "shes")):
        return n[:-2]
    if n.endswith("s") and not n.endswith("ss"):
        return n[:-1]
    return n


# words that are almost always a value, never a column, and columns that almost always hold a word value
VALUE_WORDS = set("""active inactive pending paid unpaid approved rejected completed complete cancelled canceled yes no male female
admin open closed draft published new done failed success successful enabled disabled blocked deleted expired valid
invalid present absent pass fail hold shipped delivered processing refunded verified unverified""".split())
VALUE_COLUMNS = set("""status state type role gender category level mode kind department dept grade method payment_method
currency plan tier priority stage city country speciality specialty designation source shift section title name label
season tag colour color size brand unit location place branch zone region area district""".split())
# after "col =", only these words can start a real expression; any other keyword-looking word (day, second, left, open) is a value
EXPR_WORDS = set("""null true false unknown default not case exists select interval binary any some all date time timestamp row
values current_date current_time current_timestamp localtime localtimestamp current_user session_user user""".split())
VALUE_COLUMN_END = re.compile(r"(_type|_status|_mode|_name|_category|_level|_kind|_group|_class|_code)$|"
                              r"[a-z](Type|Status|Mode|Name|Category|Level|Kind|Group|Class|Code)$")
_DATE = re.compile(r"(?<![\w'\".:-])(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?(?![\w'\".:-])")
_BARE_VALUE = re.compile(r"([`\"]?[A-Za-z_][\w$]*[`\"]?(?:\.[`\"]?[A-Za-z_][\w$]*[`\"]?)?)\s*(=|!=|<>)\s*([A-Za-z_][\w$]*)\b(?!\s*[.(])")


def _value_slips(sql: str, s: str, known: set) -> tuple[list, list]:
    """Values written without quotes, in any statement: a date (MySQL reads 2024-01-01 as 2024 minus 1 minus 1 = 2022)
    and a word (status = active: MySQL looks for a column called active). Checked on MariaDB and PostgreSQL."""
    F, edits = [], []
    for m in _DATE.finditer(s):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1000 <= y <= 9999 and 1 <= mo <= 12 and 1 <= d <= 31):
            continue
        lit = sql[m.start():m.end()]
        if m.group(4) is None:
            sum_ = y - mo - d
            F.append(Finding("wrong", "value", f"{lit} needs quotes: MySQL reads it as {y} - {mo} - {d} = {sum_}",
                             f"{lit} ko quotes chahiye: MySQL ise {y} - {mo} - {d} = {sum_} padhta hai", lit,
                             f"Without quotes it's arithmetic, not a date: MySQL compares the column with the number {sum_}, so a SELECT "
                             f"silently returns wrong rows (often none) and an UPDATE/DELETE stops with error 1292; PostgreSQL refuses it "
                             f"(operator does not exist: date < integer). Write '{lit}'.",
                             f"Bina quotes ke ye date nahi, hisaab hai: MySQL column ko number {sum_} se compare karta hai, isliye SELECT "
                             f"chupchaap galat rows deta hai (aksar koi nahi) aur UPDATE/DELETE error 1292 deta hai; PostgreSQL mana karta hai. "
                             f"'{lit}' likho."))
        else:
            F.append(Finding("error", "value", f"{lit} needs quotes", f"{lit} ko quotes chahiye", lit,
                             f"A date and time without quotes is a syntax error (MySQL 1064, PostgreSQL 42601). Write '{lit}'.",
                             f"Bina quotes ke date aur time syntax error hai (MySQL 1064, PostgreSQL 42601). '{lit}' likho."))
        edits.append((m.start(), m.end(), f"'{lit}'"))
    low = s.lower()
    for m in _BARE_VALUE.finditer(s):
        left, w = m.group(1).strip('`"'), m.group(3)
        wl, col = w.lower(), left.split(".")[-1].strip('`"').lower()
        if wl in EXPR_WORDS or wl in known or col in KW:
            continue
        raw_col = left.split(".")[-1].strip('`"')
        valueish_col = col in VALUE_COLUMNS or bool(VALUE_COLUMN_END.search(raw_col)) or bool(VALUE_COLUMN_END.search(col))
        if len(re.findall(rf"(?<![\w$.]){re.escape(wl)}(?![\w$])", low)) > 1 or re.search(rf"\.\s*[`\"]?{re.escape(wl)}\b", low):
            continue                                       # used elsewhere as a name: probably a real column
        plain_word = re.fullmatch(r"[A-Za-z]+", w) is not None   # no _ or digits: reads like a value, not a column name
        one_table = len(known) <= 1 and not re.search(r"\bjoin\b|\(\s*select\b", low)
        if wl in VALUE_WORDS or (valueish_col and plain_word):
            sure = True
        elif valueish_col or (one_table and plain_word):
            sure = False                                   # comparing two columns of the same row is rare, but possible
        else:
            continue
        ev = sql[m.start():m.end()]
        F.append(Finding("error" if sure else "check", "value",
                         f"{w} needs quotes: without them it's read as a column name" if sure else f"{w}: a value or a column?",
                         f"{w} ko quotes chahiye: bina quotes ke ise column ka naam samjha jata hai" if sure else f"{w}: value hai ya column?",
                         ev,
                         f"{left} = {w} makes the database look for a column called {w} (MySQL error 1054 Unknown column '{w}', "
                         f"PostgreSQL: column \"{w}\" does not exist). For the text value write {left} = '{w}'.",
                         f"{left} = {w} se database {w} naam ka column dhoondhta hai (MySQL error 1054, PostgreSQL: column does not exist). "
                         f"Text value ke liye {left} = '{w}' likho."))
        if sure:
            edits.append((m.start(3), m.end(3), f"'{w}'"))
    return F, edits


UNIQUE_COL = re.compile(r"^(id|\w+_id|uuid|guid|email|code|slug|username|user_name|sku|\w+_no|\w+_number|\w+_code|pk)$", re.I)


def _statement_slips(sql: str, s: str, is_select: bool, intent: str = "") -> tuple[list, list]:
    """Mistakes any statement can have (SELECT, UPDATE, DELETE), worked out from the query alone. Each one was reproduced on
    MariaDB and PostgreSQL: what MySQL does silently (wrong rows) is 'wrong', what a database refuses is 'error', and what
    depends on data or engine the query can't show is 'check'."""
    F, edits = [], []
    low = s.lower()
    dep = _depths(s)

    # 1. column = (SELECT ...) that can return several rows: error 1242 / 'more than one row returned by a subquery'
    for m in re.finditer(r"([`\"]?[A-Za-z_][\w$.]*[`\"]?)\s*(=|!=|<>|<=|>=|<|>)\s*\(\s*select\b", s, re.I):
        a = s.index("(", m.end(2))
        b = _close(s, a)
        sub = s[a + 1:b]
        subl = sub.lower()
        one = sub
        dm = re.match(r"\s*select\s+[`\"]?\w+[`\"]?\s+from\s*(\()\s*select\b", sub, re.I)
        if dm and re.fullmatch(r"\s*(as\s+)?[`\"]?\w+[`\"]?\s*", sub[_close(sub, dm.start(1)) + 1:], re.I):
            one = sub[dm.start(1) + 1:_close(sub, dm.start(1))]   # SELECT v FROM (SELECT MAX(x) AS v ...) AS copy: the inner one decides
        if AGG.search(_no_subqueries(one)) and not re.search(r"\bgroup\s+by\b", one.lower()):
            continue                                       # MAX(...) / COUNT(*) without GROUP BY: always one row
        if dm:                                             # ... GROUP BY g) AS copy WHERE copy.g = outer.g: one group, one row
            after = sub[_close(sub, dm.start(1)) + 1:]
            inner = sub[dm.start(1) + 1:_close(sub, dm.start(1))]
            wm = re.fullmatch(r"\s*(?:as\s+)?([`\"]?\w+[`\"]?)\s+where\s+\1\.([`\"]?\w+[`\"]?)\s*=\s*[`\"]?\w+[`\"]?\.[`\"]?\w+[`\"]?\s*",
                              after, re.I)
            gm = re.search(r"\bgroup\s+by\s+([`\"]?\w+[`\"]?)\s*$", inner, re.I)
            if wm and gm and wm.group(2).strip('`"').lower() == gm.group(1).strip('`"').lower() and AGG.search(inner):
                continue
        if re.search(r"\blimit\s+1\b|\bfetch\s+first\s+(1\s+)?rows?\s+only\b", subl):
            continue
        keyed = any(UNIQUE_COL.match(c.split(".")[-1].strip('`"')) for c in
                    re.findall(r"([`\"]?[A-Za-z_][\w$.]*[`\"]?)\s*=\s*(?:'|\d|:|\?|%s|@)", sub))
        col, op = m.group(1), m.group(2)
        ev = re.sub(r"\s+", " ", sql[m.start():b + 1])
        ev = ev if len(ev) <= 90 else ev[:87] + "..."
        fix_in = op == "="
        F.append(Finding("check" if keyed else "error", "subquery",
                         f"{col} {op} (SELECT ...) breaks when the subquery returns more than one row",
                         f"{col} {op} (SELECT ...) tab toot-ta hai jab subquery ek se zyada row de", ev,
                         ("It filters on a key, so it probably returns one row; if it can return more, " if keyed else
                          "Its WHERE can match several rows (for example several departments in one city), and then ")
                         + f"MySQL stops with error 1242 (Subquery returns more than 1 row) and PostgreSQL with 'more than one row returned "
                         f"by a subquery used as an expression'. "
                         + (f"Use {col} IN (SELECT ...): the same result for one row, and right for many."
                            if fix_in else f"Decide which row you mean: {op} ANY (...) / {op} ALL (...), or MIN()/MAX() inside the subquery."),
                         ("Ye key pe filter karti hai, to shayad ek hi row deti hai; zyada de sakti hai to " if keyed else
                          "Iska WHERE kai rows match kar sakta hai (jaise ek shehar mein kai departments), aur tab ")
                         + "MySQL error 1242 deta hai aur PostgreSQL 'more than one row returned by a subquery'. "
                         + (f"{col} IN (SELECT ...) likho: ek row pe same result, kai rows pe bhi sahi."
                            if fix_in else f"Batao kaunsi row chahiye: {op} ANY (...) / {op} ALL (...), ya subquery mein MIN()/MAX().")))
        if fix_in and not keyed:
            edits.append((m.start(2), m.end(2), "IN"))

    # 2. = NULL in UPDATE/DELETE conditions (SELECTs are read in depth below); SET x = NULL is an assignment, not a test
    if not is_select:
        wm = re.search(r"\bwhere\b", low)
        if wm:
            for m in re.finditer(r"(!=|<>|=)\s*null\b", s[wm.end():], re.I):
                op, at = m.group(1), wm.end() + m.start()
                right = "IS NOT NULL" if op in ("!=", "<>") else "IS NULL"
                F.append(Finding("wrong", "null", f"'{op} NULL' is never true", f"'{op} NULL' kabhi true nahi hota",
                                 re.sub(r"\s+", " ", sql[max(0, at - 30):at + len(m.group(0))]).strip(),
                                 f"Nothing equals NULL, not even NULL, so this condition matches no row and the statement changes nothing. "
                                 f"Write {right}.", f"NULL kisi ke barabar nahi hota, isliye ye kisi row ko nahi badlega. {right} likho."))
                edits.append((at, at + len(m.group(0)), right))

    # 3. UPDATE ... SET a = 1 AND b = 2: MySQL sets a = (1 AND b = 2), i.e. 0 or 1, and never touches b
    sm = re.search(r"\bset\b", low) if re.match(r"\s*update\b", low) else None
    if sm:
        end = re.search(r"\bwhere\b|\border\s+by\b|\blimit\b|;|$", low[sm.end():])
        set_end = sm.end() + (end.start() if end else len(low) - sm.end())
        for m in re.finditer(r"\band\s+([`\"]?[A-Za-z_][\w$.]*[`\"]?)\s*=(?!=)", s[sm.end():set_end], re.I):
            at = sm.end() + m.start()
            if dep[at] != dep[sm.start()]:
                continue                                   # inside brackets: a real condition
            ev = re.sub(r"\s+", " ", sql[sm.start():set_end]).strip()
            F.append(Finding("wrong", "set", "SET ... AND ...: the AND must be a comma", "SET ... AND ...: AND ki jagah comma",
                             ev[:90], "MySQL reads SET a = 50 AND b = 999 as a = (50 AND b = 999): a becomes 0 or 1 and b is never "
                             "changed, with no error. PostgreSQL refuses it (argument of AND must be type boolean). Separate the "
                             "columns with commas: SET a = 50, b = 999.",
                             "MySQL SET a = 50 AND b = 999 ko a = (50 AND b = 999) padhta hai: a 0 ya 1 ban jata hai aur b badalta hi "
                             "nahi, bina error. Columns ko comma se alag karo: SET a = 50, b = 999."))
            edits.append((at, at + 3, ","))
            pre = sql[at - 1:at]
            if pre == " ":
                edits[-1] = (at - 1, at + 3, ",")

    # 4. status = 'a' OR 'b': the OR side is just the text 'b', not status = 'b'
    for m in re.finditer(r"([`\"]?[A-Za-z_][\w$.]*[`\"]?)\s*=\s*('[^']*'|-?\d+(?:\.\d+)?)((?:\s+or\s+(?:'[^']*'|-?\d+(?:\.\d+)?)(?![\w.]|\s*[=<>!]))+)",
                         s, re.I):
        col = m.group(1)
        vals = [sql[m.start(2):m.end(2)]] + [sql[m.start(3) + x.start(1):m.start(3) + x.end(1)]
                                             for x in re.finditer(r"or\s+('[^']*'|-?\d+(?:\.\d+)?)", s[m.start(3):m.end(3)], re.I)]
        fixed = f"{col} IN ({', '.join(vals)})"
        F.append(Finding("wrong", "or", f"{sql[m.start():m.end()].strip()}: OR needs the column again",
                         f"{sql[m.start():m.end()].strip()}: OR ke baad column dobara chahiye", sql[m.start():m.end()].strip(),
                         f"OR {vals[1]} doesn't compare {col}: MySQL turns {vals[1]} into a number (0 for text) and silently keeps only "
                         f"{col} = {vals[0]}; PostgreSQL refuses it. Write {fixed}.",
                         f"OR {vals[1]} {col} se compare nahi karta: MySQL use number (text ke liye 0) bana deta hai aur chupchaap sirf "
                         f"{col} = {vals[0]} rakhta hai; PostgreSQL mana karta hai. {fixed} likho."))
        edits.append((m.start(), m.end(), fixed))

    # 5. BETWEEN high AND low: never true
    for m in re.finditer(r"\bbetween\s+(-?\d+(?:\.\d+)?|'[^']*')\s+and\s+(-?\d+(?:\.\d+)?|'[^']*')", s, re.I):
        lo, hi_ = sql[m.start(1):m.end(1)], sql[m.start(2):m.end(2)]
        try:
            bad = float(lo) > float(hi_)
        except ValueError:
            bad = lo.startswith("'") and hi_.startswith("'") and re.fullmatch(r"'\d{4}-\d{2}-\d{2}[^']*'", lo) is not None and \
                re.fullmatch(r"'\d{4}-\d{2}-\d{2}[^']*'", hi_) is not None and lo > hi_
        if bad:
            F.append(Finding("wrong", "between", f"BETWEEN {lo} AND {hi_}: the smaller value must come first",
                             f"BETWEEN {lo} AND {hi_}: chhoti value pehle aati hai", sql[m.start():m.end()],
                             f"BETWEEN a AND b means >= a AND <= b, so with {lo} first nothing can match: no rows, no error "
                             f"(MySQL and PostgreSQL). Write BETWEEN {hi_} AND {lo}.",
                             f"BETWEEN a AND b ka matlab >= a aur <= b, isliye {lo} pehle ho to kuch match nahi hota: koi row nahi, "
                             f"koi error nahi. BETWEEN {hi_} AND {lo} likho."))
            edits.append((m.start(1), m.end(1), hi_))
            edits.append((m.start(2), m.end(2), lo))

    # 6. an aggregate in WHERE (outside any subquery): error 1111 / 'aggregate functions are not allowed in WHERE'
    flat = _no_subqueries(s)
    wm = re.search(r"\bwhere\b", flat, re.I)
    if wm:
        rest = flat[wm.end():]
        stop = re.search(r"\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\blimit\b|\bunion\b|;", rest, re.I)
        wtext = rest[:stop.start()] if stop else rest
        am = AGG.search(_no_scalar_minmax(wtext))
        if am:
            fn = am.group(1).upper()
            F.append(Finding("error", "where", f"{fn}() can't be used in WHERE", f"WHERE mein {fn}() nahi chalta",
                             re.sub(r"\s+", " ", sql[wm.end() + am.start():wm.end() + am.start() + 60]).strip(),
                             f"WHERE looks at one row at a time, before any totals exist: MySQL stops with error 1111 (Invalid use of "
                             f"group function), PostgreSQL with 'aggregate functions are not allowed in WHERE'. A condition on a total goes "
                             f"in HAVING after GROUP BY; in UPDATE/DELETE, compare with a subquery, e.g. salary < (SELECT AVG(salary) FROM ...).",
                             f"WHERE ek-ek row dekhta hai, totals se pehle: MySQL error 1111, PostgreSQL 'aggregate functions are not allowed "
                             f"in WHERE'. Total pe condition GROUP BY ke baad HAVING mein; UPDATE/DELETE mein subquery se compare karo."))
            hv = _where_to_having(sql, flat, wm, rest, stop) if is_select else None
            if hv:
                edits.extend(hv)

    # portability (app/agents/sqlport.py): another database's words the chosen one refuses (rewritten in its own words), and
    # spellings that run but mean something else there (date - date, INTERVAL '2' WEEK, CAST AS CHAR, ^)
    from app.agents import sqlport

    for fnd, pedits in sqlport.port(sql, s, intent, Finding, is_select):
        if any(f.topic == fnd.topic and f.evidence == fnd.evidence for f in F):
            continue
        F.append(fnd)
        if fnd.kind == "check" and _SURE_ONLY.get():
            continue
        if pedits and not any(a < e[1] and e[0] < b or (a == b == e[0]) for e in pedits for a, b, _n in edits):
            edits += pedits

    # subquery reasoning (app/agents/sqlsubq.py): shape (one value / a set / EXISTS), scope (the inner FROM first), NULLs
    from app.agents import sqlsubq

    for fnd, sedits in sqlsubq.subqueries(sql, s, intent, Finding):
        F.append(fnd)
        if sedits and not any(a < e[1] and e[0] < b for e in sedits for a, b, _n in edits):
            edits += sedits

    # CASE, WITH and window functions (app/agents/sqlwin.py): first true WHEN wins, recursion must end, windows run last
    from app.agents import sqlwin

    for fnd, wedits in sqlwin.windows(sql, s, intent, Finding):
        if any(f.topic == fnd.topic and f.evidence == fnd.evidence for f in F):
            continue
        F.append(fnd)
        if fnd.kind == "check" and _SURE_ONLY.get() and fnd.topic in _meaning_checks():
            continue                                       # a definite mistake is fixed alone; the 'if you meant' rewrite waits
        if wedits and not any(a < e[1] and e[0] < b or (a == b == e[0]) for e in wedits for a, b, _n in edits):
            edits += wedits

    # indexes and speed (app/agents/sqlspeed.py): a condition on the bare column, OR across two tables, deep OFFSET -> keyset.
    # Each rewrite returns exactly the same rows (lab-proven); only the speed changes, so it is a 'check'
    from app.agents import sqlspeed

    for fnd, pedits in sqlspeed.speed(sql, s, intent, Finding, is_select):
        if any(f.topic == fnd.topic and f.evidence == fnd.evidence for f in F):
            continue
        F.append(fnd)
        if _SURE_ONLY.get():
            continue                                       # a definite mistake is fixed alone; the faster form comes after
        if pedits and not any(a < e[1] and e[0] < b or (a == b == e[0]) for e in pedits for a, b, _n in edits):
            edits += pedits

    # structure (app/agents/sqlstruct.py, night 13): correlation that never names the outer row or names the wrong one, the
    # same subquery written twice, UNION vs UNION ALL, a CTE read twice and filtered outside, SELECT * across a join
    from app.agents import sqlstruct

    for fnd, tedits in sqlstruct.structure(sql, s, intent, Finding, is_select):
        if any(f.topic == fnd.topic and f.evidence == fnd.evidence for f in F):
            continue
        F.append(fnd)
        if fnd.kind == "check" and _SURE_ONLY.get():
            continue
        if tedits and not any(a < e[1] and e[0] < b or (a == b == e[0]) for e in tedits for a, b, _n in edits):
            edits += tedits

    # safe rewrites with proof (app/agents/sqlproof.py, night 14): several correlated totals over one child table worked out
    # in one grouped pass, an expression repeated in SELECT and WHERE named once, and the form the person asked for (EXISTS,
    # IN, JOIN, CTE, keyset, UNION ALL) -- each one only when it returns the same rows, with what it keeps said
    from app.agents import sqlproof

    for fnd, redits in sqlproof.rewrites(sql, s, intent, Finding, is_select, {f.topic for f in F}):
        if any(f.topic == fnd.topic and f.evidence == fnd.evidence for f in F):
            continue
        F.append(fnd)
        if fnd.kind == "check" and _SURE_ONLY.get():
            continue
        if redits and fnd.topic.startswith("rw_") and not any(f.kind in ("error", "wrong") for f in F):
            edits[:] = redits                              # the form the person asked for wins over an 'if you meant' offer
        elif redits and not any(a < e[1] and e[0] < b or (a == b == e[0]) for e in redits for a, b, _n in edits):
            edits += redits

    # locks and big data (app/agents/sqllock.py): a huge change in batches, queue workers with SKIP LOCKED (offered, same rows)
    from app.agents import sqllock

    for fnd, ledits in sqllock.locks(sql, s, intent, Finding, is_select):
        if any(f.topic == fnd.topic and f.evidence == fnd.evidence for f in F):
            continue
        F.append(fnd)
        if _SURE_ONLY.get():
            continue
        if ledits and not any(a < e[1] and e[0] < b or (a == b == e[0]) for e in ledits for a, b, _n in edits):
            edits += ledits

    # changing data (app/agents/sqlchange.py): which rows, how often, what value, which column, engine syntax
    if not is_select:
        from app.agents import sqlchange

        for fnd, cedits in sqlchange.changes(sql, s, intent, Finding):
            if any(f.topic == fnd.topic and f.evidence == fnd.evidence and f.title_en == fnd.title_en for f in F):
                continue
            F.append(fnd)
            if cedits and not any(a < e[1] and e[0] < b or (a == b == e[0]) for e in cedits for a, b, _n in edits):
                edits += cedits

    # 9-13: traps from SQL practice (LEFT JOIN counts, NOT IN with NULLs, BETWEEN on timestamps, ties, top N per group)
    for fnd, edit in _workbook_traps(sql, s, is_select, intent):
        F.append(fnd)
        if edit:
            edits.append(edit)

    # 14. basic SELECT mistakes (app/agents/sqlbasics.py): text joining, NULL in sums, LIKE, AND/OR, division, sorted LIMIT
    from app.agents import sqlbasics

    for fnd, edit in sqlbasics.basics(sql, s, is_select, intent, Finding):
        if any(f.evidence == fnd.evidence and f.topic == fnd.topic for f in F):
            continue
        F.append(fnd)
        if edit and not any(a < edit[1] and edit[0] < b or (a == b == edit[0]) for a, b, _n in edits):
            edits.append(edit)

    # 8. what the person said they want: "the highest within their own category" vs MAX() over the whole table
    handled = set()
    for fnd, edit, span in _per_group(sql, s, intent):
        F.append(fnd)
        edits.append(edit)
        handled.add(span)

    # 7. UPDATE/DELETE t ... (SELECT ... FROM t): MySQL refuses (1093); MariaDB 10.3+ and PostgreSQL allow it
    tm = re.match(r"\s*(?:update\s+(?:low_priority\s+)?(?:ignore\s+)?|delete\s+from\s+)[`\"]?([\w$]+)[`\"]?", low)
    if tm:
        t = tm.group(1)
        for m in re.finditer(r"\(\s*select\b", s, re.I):
            a = m.start()
            b = _close(s, a)
            sub = s[a:b + 1]
            if re.search(r"\bfrom\s*$", s[:a], re.I) or any(x <= a <= y for x, y in handled):
                continue                                   # a derived table (built first, allowed), or already rewritten above
            if re.search(rf"\bfrom\s+[`\"]?{re.escape(t)}[`\"]?\b", sub, re.I) and \
                    not re.search(r"\bfrom\s*\(\s*select\b", sub, re.I):
                wrapped = _wrap_derived(sql, s, a, b, t)
                if wrapped:
                    edits.append((a, b + 1, wrapped))
                F.append(Finding("check", "same_table", f"The subquery reads {t}, the table being changed",
                                 f"Subquery wahi table {t} padhti hai jo badal rahi hai", re.sub(r"\s+", " ", sql[a:a + 70]).strip(),
                                 f"MySQL refuses this with error 1093 (You can't specify target table '{t}' for update in FROM clause); "
                                 f"MariaDB 10.3+ and PostgreSQL allow it. On MySQL, wrap the subquery once more: "
                                 f"... IN (SELECT x FROM (SELECT ... FROM {t} ...) AS t2), or use a multi-table UPDATE/DELETE with a JOIN.",
                                 f"MySQL ise error 1093 se mana karta hai; MariaDB 10.3+ aur PostgreSQL chala dete hain. MySQL pe subquery ko "
                                 f"ek aur baar lapeto: (SELECT x FROM (SELECT ... FROM {t} ...) AS t2), ya JOIN wala UPDATE/DELETE."))
                break
    return F, edits


_GROUP_SAID = [r"\b(?:within|in|for|inside|of)\s+(?:their|its|his|her|each|every|the\s+same|that|own)\s+(?:own\s+)?([a-z_]+)",
               r"\bper\s+([a-z_]+)", r"\beach\s+([a-z_]+)", r"\bevery\s+([a-z_]+)", r"\bhar\s+([a-z_]+)",
               r"\bapn[ieaā]\s+([a-z_]+)", r"\b([a-z_]+)[\s-]?wise\b", r"\bby\s+([a-z_]+)\b(?!\s*(?:\d|%))"]
_NOT_GROUPS = {"row", "rows", "record", "records", "one", "time", "case", "own", "the", "a", "an", "query", "table", "value", "day",
               "percent", "discount", "update", "change", "way"}


def _said_groups(intent: str) -> list[str]:
    """The groups the person said the totals belong to: 'within their own category' -> ['category']."""
    out = []
    for rx in _GROUP_SAID:
        for m in re.finditer(rx, (intent or "").lower()):
            w = m.group(1)
            if w not in _NOT_GROUPS and len(w) > 2:
                out.append(w)
    return list(dict.fromkeys(out))


def _group_column(noun: str, names: list[str]) -> str | None:
    stems = {noun, noun[:-3] + "y" if noun.endswith("ies") else noun.rstrip("s")}
    for n in names:
        nl = n.lower()
        if any(nl == st or nl in (st + "_id", st + "id", st + "_code", st + "_name") for st in stems):
            return n
    return None


def _per_group(sql: str, s: str, intent: str) -> list:
    """col op (SELECT MAX(x) FROM t) compares with ONE number for the whole table. When the person said the highest/lowest/
    average is meant within each group (category, class, department...), each row must be compared with its own group's
    value. Rewritten as (SELECT v FROM (SELECT g, MAX(x) AS v FROM t GROUP BY g) AS t_copy WHERE t_copy.g = outer.g): the
    same on MySQL, MariaDB and PostgreSQL, and MySQL builds the copy first (no error 1093). Checked on MariaDB and PostgreSQL."""
    groups = _said_groups(intent)
    if not groups:
        return []
    low = s.lower()
    tm = re.match(r"\s*(?:update\s+(?:low_priority\s+)?(?:ignore\s+)?|delete\s+from\s+)[`\"]?([\w$]+)[`\"]?(?:\s+(?:as\s+)?([a-z_]\w*))?", low) or \
        re.search(r"\bfrom\s+[`\"]?([\w$]+)[`\"]?(?:\s+(?:as\s+)?([a-z_]\w*))?", low)
    if not tm:
        return []
    table = tm.group(1)
    outer = tm.group(2) if tm.group(2) and tm.group(2) not in KW else table
    names = [w for w in re.findall(r"(?<![\w$])([A-Za-z_][\w$]*)", s) if w.lower() not in KW]
    out = []
    for m in re.finditer(r"([`\"]?[A-Za-z_][\w$.]*[`\"]?)\s*(=|!=|<>|<=|>=|<|>)\s*\(\s*select\b", s, re.I):
        a = s.index("(", m.end(2))
        b = _close(s, a)
        sub, sub_o = s[a + 1:b], sql[a + 1:b]
        sm = re.match(r"\s*select\s+((max|min|avg|sum|count)\s*\(([^()]*)\))\s+from\s+[`\"]?([\w$]+)[`\"]?(?:\s+(?:as\s+)?[a-z_]\w*)?"
                      r"(\s+where\s+(?P<w>[\s\S]*?))?\s*$", sub, re.I)
        if not sm or sm.group(4).lower() != table or re.search(r"\bgroup\s+by\b", sub, re.I):
            continue
        for noun in groups:
            g = _group_column(noun, names)
            guessed = g is None
            if guessed:                                    # not in the query: the usual name for it, in the query's own style
                base = noun[:-3] + "y" if noun.endswith("ies") else noun
                if any(re.match(r"[A-Z][a-z]+(?:[A-Z]|$)", n) for n in names):
                    g = base[:1].upper() + base[1:] + "Id"
                elif any(re.match(r"[a-z]+[A-Z]", n) for n in names):
                    g = base + "Id"
                else:
                    g = base + "_id"
            if re.search(rf"(?<![\w$]){re.escape(g)}(?![\w$])", sub, re.I):
                continue                                   # the subquery already uses it
            fn, x = sm.group(2).upper(), sub_o[sm.start(3):sm.end(3)].strip()
            where = f" WHERE {sub_o[sm.start('w'):sm.end('w')].strip()}" if sm.group("w") else ""
            new = (f"(SELECT v FROM (SELECT {g}, {fn}({x}) AS v FROM {table}{where} GROUP BY {g}) AS {table}_copy "
                   f"WHERE {table}_copy.{g} = {outer}.{g})" + (f" /* check: {g} is the column that holds the {noun} */" if guessed else ""))
            col = m.group(1)
            ev = re.sub(r"\s+", " ", sql[m.start():b + 1])
            out.append((Finding("wrong", "per_group", f"{fn}({x}) is taken over all of {table}, not within each {noun}",
                                f"{fn}({x}) poori {table} table ka hai, har {noun} ka alag nahi", ev[:90],
                                f"You want each row compared with its own {noun}'s {fn}, but this subquery has no link to the row, so it "
                                f"returns one {fn}({x}) for the whole table: only rows equal to that single value are touched (often none, "
                                f"if that value sits in a {noun} the rest of the WHERE excludes). Group it by {g} and match the row's {g}.",
                                f"Aap har row ko uske apne {noun} ke {fn} se compare karna chahte ho, par subquery row se judi nahi hai, isliye "
                                f"poori table ka ek hi {fn}({x}) aata hai. {g} se GROUP BY karo aur row ka {g} match karo."),
                        (a, b + 1, new), (a, b)))
            break
    return out


TIME_COL = re.compile(r"(date|_at$|at$|time|created|updated|_on$|timestamp|_dt$|^dt$)", re.I)


def _workbook_traps(sql: str, s: str, is_select: bool, intent: str) -> list:
    """Each one reproduced on MariaDB and PostgreSQL (tests/test_sql_correct.py, the lab test of each rewrite)."""
    import datetime as _dt

    out = []
    low = s.lower()
    said = (intent or "").lower()

    # 9. COUNT(*) with a LEFT JOIN: a row with no match is still one row, so 'no orders' counts as 1
    lj = list(re.finditer(r"\bleft\s+(?:outer\s+)?join\s+[`\"]?([\w$.]+)[`\"]?\s+(?:as\s+)?([a-z_]\w*)?\s*\bon\b", s, re.I))
    if is_select and len(lj) == 1 and re.search(r"\bgroup\s+by\b", low) and not re.search(r"\bjoin\b", low[:lj[0].start()] + low[lj[0].end():]):
        right = (lj[0].group(2) if lj[0].group(2) and lj[0].group(2).lower() not in KW else lj[0].group(1)).strip('`"')
        rname = lj[0].group(1).split(".")[-1].strip('`"')
        on = s[lj[0].end():]
        stop = re.search(r"\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\blimit\b|\bhaving\b|;", on, re.I)
        on = on[:stop.start()] if stop else on
        rc = None                                          # only a child table (orders of a customer), never a lookup (a student's class)
        for em in re.finditer(r"[`\"]?(\w+)[`\"]?\.[`\"]?(\w+)[`\"]?\s*=\s*[`\"]?(\w+)[`\"]?\.[`\"]?(\w+)[`\"]?", on):
            pairs = [(em.group(1), em.group(2), em.group(3), em.group(4)), (em.group(3), em.group(4), em.group(1), em.group(2))]
            for ra, rcol, la, lcol in pairs:
                if ra.lower() != right.lower() or rcol.lower() == "id":
                    continue
                fm = re.search(rf"\bfrom\s+[`\"]?([\w$.]+)[`\"]?\s+(?:as\s+)?{re.escape(la)}\b|\bfrom\s+[`\"]?({re.escape(la)})[`\"]?\b", s, re.I)
                ltab = (fm.group(1) or fm.group(2)).split(".")[-1].split("_", 1)[-1] if fm else la
                key = lcol.lower().replace("_", "")
                if key == "id" or key == _singular(ltab).lower().replace("_", "") + "id" or key == _singular(la).lower() + "id":
                    rc = re.match(r"(.*)", rcol)
                    break
            if rc:
                break
        for m in re.finditer(r"\bcount\s*\(\s*\*\s*\)", s, re.I):
            if not rc or _depths(s)[m.start()] != _depths(s)[lj[0].start()]:
                continue
            col = f"{right}.{rc.group(1)}"
            out.append((Finding("check", "count", f"COUNT(*) counts 1 for rows with no {rname} match",
                                f"COUNT(*) bina {rname} wali row ko bhi 1 ginta hai", sql[m.start():m.end()],
                                f"With a LEFT JOIN, a row that has no match is still one row (its {rname} columns are NULL), so COUNT(*) "
                                f"shows 1 instead of 0 for it. To count the matches, count a {rname} column: COUNT({col}).",
                                f"LEFT JOIN mein bina match wali row bhi ek row hai, isliye COUNT(*) 0 ki jagah 1 dikhata hai. Match ginne ke "
                                f"liye {right} ka column gino: COUNT({col})."), (m.start(), m.end(), f"COUNT({col})")))

    # 10. x NOT IN (SELECT col ...): one NULL in col and nothing is returned
    for m in re.finditer(r"([`\"]?[A-Za-z_][\w$.]*[`\"]?)\s+not\s+in\s*\(\s*select\s+([`\"]?[A-Za-z_][\w$.]*[`\"]?)\s+from\s+"
                         r"[`\"]?([\w$]+)[`\"]?(?:\s+(?:as\s+)?([a-z_]\w*))?\s*(?:where\s+(?P<w>[^()]*?))?\s*\)", s, re.I):
        x, col, t = m.group(1), m.group(2).split(".")[-1].strip('`"'), m.group(3)
        if col.lower() in ("id",) or (m.group(4) and m.group(4).lower() in KW):
            continue                                       # its own key can't be NULL
        ia = m.group(4) or "n1"
        outer_q = x
        if "." not in x:
            fm = re.search(r"\b(?:from|update|delete\s+from)\s+[`\"]?([\w$]+)[`\"]?(?:\s+(?:as\s+)?([a-z_]\w*))?", low)
            if fm:
                oa = fm.group(2) if fm.group(2) and fm.group(2) not in KW else fm.group(1)
                outer_q = f"{oa}.{x}"
        w = f" AND ({sql[m.start('w'):m.end('w')].strip()})" if m.group("w") else ""
        new = f"NOT EXISTS (SELECT 1 FROM {t} {ia} WHERE {ia}.{col} = {outer_q}{w})"
        out += _keep((Finding("check", "not_in", f"NOT IN (SELECT {col} ...) returns nothing if {col} has a NULL",
                            f"{col} mein ek bhi NULL ho to NOT IN (SELECT {col} ...) kuch nahi deta", sql[m.start():m.end()][:90],
                            f"x NOT IN (list) is true only when x differs from every value, and 'differs from NULL' is unknown, so one NULL "
                            f"in {t}.{col} makes the whole result empty, with no error (MySQL and PostgreSQL). NOT EXISTS ignores NULLs "
                            f"and returns the same rows otherwise: {new}.",
                            f"Ek NULL hone par NOT IN poora result khaali kar deta hai, bina error. NOT EXISTS NULL ko chhod deta hai: {new}."),
                    (m.start(), m.end(), new)), [("not_null", m.group(4) or t, col)])

    # 11. BETWEEN 'day1' AND 'day2' on a date-time column misses everything after 00:00 on day2
    for m in re.finditer(r"(?<![\w$.:`\"])([`\"]?[A-Za-z_][\w$.]*[`\"]?)\s+between\s+'(\d{4}-\d{2}-\d{2})'\s+and\s+'(\d{4}-\d{2}-\d{2})'",
                         sql, re.I):                       # (x::date BETWEEN ...: a day cast, not a time column)
        col = m.group(1).split(".")[-1].strip('`"')
        from app.agents.sqlverify import column_family

        if not TIME_COL.search(col) and column_family(m.group(1).split(".")[0].strip('`"') if "." in m.group(1) else None, col) != "time":
            continue                                       # the project's schema can say it holds a time even when the name doesn't
        try:
            nxt = (_dt.date.fromisoformat(m.group(3)) + _dt.timedelta(days=1)).isoformat()
        except ValueError:
            continue
        new = f"{m.group(1)} >= '{m.group(2)}' AND {m.group(1)} < '{nxt}'"
        qual = m.group(1).split(".")[0].strip('`"') if "." in m.group(1) else None
        out += _keep((Finding("check", "date_range", f"BETWEEN ... AND '{m.group(3)}' stops at 00:00 on {m.group(3)}",
                            f"BETWEEN ... AND '{m.group(3)}' {m.group(3)} ko 00:00 pe ruk jata hai", sql[m.start():m.end()],
                            f"If {col} stores a time too, '{m.group(3)}' means {m.group(3)} 00:00:00, so the rest of that day is left out. "
                            f"{new} covers the whole last day, and gives the same rows if {col} is a plain DATE.",
                            f"Agar {col} mein time bhi hai to aakhri din ka baaki hissa chhoot jata hai. {new} poora din leta hai."),
                    (m.start(), m.end(), new)), [("has_time", qual, col)])

    # 12. 'ties must share rank' but ROW_NUMBER() gives tied rows different numbers
    if re.search(r"\bties?\b|\btied\b|same rank|share (?:the )?rank|barabar", said):
        dense = re.search(r"\bdense\b|no gaps?|without gaps|consecutive", said)
        for m in re.finditer(r"\brow_number\s*\(\s*\)", s, re.I):
            fn = "DENSE_RANK()" if dense else "RANK()"
            out.append((Finding("wrong", "ties", f"ROW_NUMBER() splits ties; you asked for tied rows to share a rank",
                                f"ROW_NUMBER() barabar rows ko alag number deta hai; aapko same rank chahiye", sql[m.start():m.end()],
                                f"ROW_NUMBER() numbers every row 1, 2, 3... even when the values are equal, so a tie at the cut-off drops "
                                f"one of the tied rows at random. {fn} gives equal values the same rank"
                                + (" with no gaps (1, 1, 2)." if dense else " (1, 1, 3), so every tied row is kept."),
                                f"ROW_NUMBER() barabar values ko bhi alag number deta hai. {fn} barabar values ko same rank deta hai."),
                        (m.start(), m.end(), fn)))

    # 13. 'top N per category' written as ORDER BY ... LIMIT N: the top N overall
    tn = re.search(r"\b(?:top|first|best|highest|lowest|bottom|last)\s+(\d+)\b", said)
    lm = re.search(r"\border\s+by\s+([`\"]?\w+[`\"]?)(\s+(?:asc|desc))?\s+limit\s+(\d+)\s*;?\s*$", s, re.I)
    if is_select and tn and lm and "partition by" not in low and tn.group(1) == lm.group(3):
        names = [w for w in re.findall(r"(?<![\w$])([A-Za-z_][\w$]*)", s) if w.lower() not in KW]
        for noun in _said_groups(said):
            g = _group_column(noun, names)
            if not g or g.split(".")[-1].strip('`"').lower() == lm.group(1).strip('`"').lower():
                continue                                   # 'top 3 employees by commission': ranked BY it, not per it
            rank = "RANK()" if re.search(r"\bties?\b|same rank|share", said) else "ROW_NUMBER()"
            base = sql[:lm.start()].rstrip()
            key, direction = lm.group(1).strip('`"'), (lm.group(2) or "").strip().upper()
            g_name = g.split(".")[-1]
            # ROW_NUMBER over tied values numbers them in any order: end with the row's key so the same rows are kept every run
            tie = ", x.id" if rank == "ROW_NUMBER()" and key.lower() != "id" and re.search(r"(?<![\w.])(?:\w+\.)?id\b", base, re.I) else ""
            new = (f"SELECT * FROM (\n  SELECT x.*, {rank} OVER (PARTITION BY x.{g_name} ORDER BY x.{key}{' ' + direction if direction else ''}{tie}) "
                   f"AS rnk\n  FROM (\n{base}\n  ) AS x\n) AS ranked\nWHERE rnk <= {lm.group(3)}\nORDER BY {g_name}, rnk;")
            out.append((Finding("wrong", "top_n", f"LIMIT {lm.group(3)} gives the top {lm.group(3)} overall, not per {noun}",
                                f"LIMIT {lm.group(3)} poore result ke top {lm.group(3)} deta hai, har {noun} ke nahi", sql[lm.start():lm.end()].strip(),
                                f"LIMIT cuts the whole sorted result once, so most {noun}s get nothing. Number the rows inside each {g_name} "
                                f"with {rank} OVER (PARTITION BY {g_name} ORDER BY {key}{' ' + direction if direction else ''}) and keep "
                                f"rnk <= {lm.group(3)} (MySQL 8+, MariaDB 10.2+, PostgreSQL)."
                                + (" Ties: RANK() keeps every tied row." if rank == "RANK()" else " If tied rows must all be kept, use RANK()."),
                                f"LIMIT poore sorted result ko ek baar kaat-ta hai. Har {g_name} ke andar {rank} OVER (PARTITION BY {g_name} ...) "
                                f"se number do aur rnk <= {lm.group(3)} rakho."),
                        (0, len(sql), new)))
            break
    return out


def _wrap_derived(sql: str, s: str, a: int, b: int, table: str) -> str | None:
    """(SELECT x FROM t ...) -> (SELECT v FROM (SELECT x AS v FROM t ...) AS t_copy): the same rows on every database, and
    MySQL builds the inner copy first, so it no longer refuses an UPDATE/DELETE of t (error 1093)."""
    inner_m, inner_o = s[a + 1:b], sql[a + 1:b]
    sm = re.match(r"\s*select\s+(distinct\s+)?", inner_m, re.I)
    fm = None
    d = 0
    for k in range(sm.end(), len(inner_m)):
        ch = inner_m[k]
        d += ch == "("
        d -= ch == ")"
        if d == 0 and re.match(r"\bfrom\b", inner_m[k:], re.I) and (k == 0 or not (inner_m[k - 1].isalnum() or inner_m[k - 1] == "_")):
            fm = k
            break
        if d == 0 and ch == ",":
            return None                                    # several columns: there's no single safe rewrite
    if fm is None:
        return None
    item = inner_o[sm.end():fm].rstrip()
    am = re.search(r"\s+as\s+([`\"]?\w+[`\"]?)\s*$", item, re.I)
    name = am.group(1) if am else "v"
    body = inner_o if am else inner_o[:sm.end()] + item + " AS v " + inner_o[fm:].lstrip()
    lead = re.match(r"\s*", inner_o).group(0)
    return f"({lead}SELECT {name} FROM ({body.strip()}) AS {table}_copy{lead.rstrip(' ') if chr(10) in lead else ''})"


def _apply_plain(sql: str, edits: list) -> str | None:
    if not edits:
        return None
    out = sql
    for a, b, new in sorted(edits, reverse=True):
        out = out[:a] + new + out[b:]
    return out


def check(sql: str, _again: bool = False, intent: str = "", engine: str | None = None) -> dict:
    """Returns {'findings': [...], 'corrected': str|None, 'guesses': [...] }. Values without quotes are checked in any
    statement; plain SELECTs are read in depth. engine: the database the answer is for (rule A), when known."""
    from app.agents.sqlbasics import CHOSEN

    if BLOCK_KEYS.get() is None:                      # night 12: what the query's own blocks prove about their keys
        from app.agents import sqlblocks

        nested = bool(re.search(r"\(\s*select\b|^\s*with\b", _mask(sql), re.I))
        tok = BLOCK_KEYS.set(sqlblocks.keys(sql) if nested else {})
        tok2 = BLOCK_COLS.set(sqlblocks.columns(sql) if nested else {})
        try:
            return check(sql, _again, intent, engine)
        finally:
            BLOCK_KEYS.reset(tok)
            BLOCK_COLS.reset(tok2)
    if engine is not None:
        token = CHOSEN.set(engine)
        try:
            return check(sql, _again, intent)
        finally:
            CHOSEN.reset(token)
    if not _again and CHOSEN.get():
        return _in_engine_words(_check(sql, _again, intent), CHOSEN.get())
    return _check(sql, _again, intent)


def _meaning_checks() -> set:
    from app.agents.sql import MEANING_CHECKS

    return MEANING_CHECKS


# set while the corrected query is rebuilt for a query with a definite mistake: 'check' rewrites that change what the query
# means (AVG over NULLs as 0, ...) are not mixed into the fix of an 'error'/'wrong' one
_SURE_ONLY = contextvars.ContextVar("dbaba_sure_only", default=False)


def _check(sql: str, _again: bool = False, intent: str = "") -> dict:
    out = _check_once(sql, _again, intent)
    if not _again and re.search(r"\(\s*select\b|^\s*with\b", _mask(sql), re.I):
        from app.agents import sqlblocks

        try:
            out = sqlblocks.with_block_findings(sql, out)      # night 12: every CTE / derived table / UNION part checked too
        except Exception:  # noqa: BLE001 - the blocks are an extra; the plain check still answers
            pass
    fs = out.get("findings") or []
    if out.get("corrected") and any(f["kind"] in ("error", "wrong") for f in fs) and \
            any(f["kind"] == "check" and f["topic"] in _meaning_checks() for f in fs) and not _SURE_ONLY.get():
        token = _SURE_ONLY.set(True)
        try:
            again = _check_once(sql, _again, intent)
        finally:
            _SURE_ONLY.reset(token)
        if again.get("corrected"):
            out["corrected"] = again["corrected"]
    return out


def _keep(pair, needs: list) -> list:
    """The pair, unless the project's schema rules the finding out (app/agents/sqlverify.py, night 10B)."""
    from app.agents.sqlverify import keep

    return keep(pair, needs)


def _check_once(sql: str, _again: bool = False, intent: str = "") -> dict:
    from app.agents import sqllock, sqlroutine

    many = sqllock.n_plus_one(sql)
    if many:                                          # the same statement once per value (N+1): one statement does the same work
        one = many.pop("sql")
        return {"findings": [Finding(many["kind"], many["topic"], many["title_en"], many["title_hi"], many["evidence"], many["fix_en"],
                                     many["fix_hi"]).as_dict()], "corrected": one, "guesses": []}

    if sqlroutine.is_routine(sql):                    # CREATE PROCEDURE / FUNCTION / TRIGGER, DO: read as a routine (night 5)
        order = {"error": 0, "wrong": 1, "check": 2}
        found = sqlroutine.routines(sql, intent, Finding)
        edits = []
        for _f, e in found:
            if e and not any(a < x[1] and x[0] < b or (a == b == x[0]) for x in e for a, b, _n in edits):
                edits += e
        fs = sorted((f for f, _e in found), key=lambda f: order[f.kind])
        fixed = _apply_plain(sql, edits)
        if any(f.topic == "delimiter" for f in fs) and len(sqlroutine.split_script(sql, blocks=True)) == 1:
            fixed = sqlroutine.with_delimiter(fixed or sql)
        return {"findings": [f.as_dict() for f in fs], "corrected": fixed, "guesses": []}
    from app.agents import sqlschema

    if sqlschema.is_schema(sql):                     # CREATE TABLE: a table design, read for keys, types and facts (night 6)
        order = {"error": 0, "wrong": 1, "check": 2}
        found = sorted(sqlschema.schema(sql, intent, Finding), key=lambda fe: order[fe[0].kind])
        sure = any(f.kind in ("error", "wrong") for f, _e in found)
        edits = []
        for f, e in found:
            if sure and f.kind == "check":
                continue                             # a definite mistake is fixed alone; design advice is offered on its own
            if e and not any(a < x[1] and x[0] < b or (a == b == x[0]) for x in e for a, b, _n in edits):
                edits += e
        fixed = sqlschema.apply(sql, edits) if edits else None
        return {"findings": [f.as_dict() for f, _e in found], "corrected": fixed if fixed != sql else None, "guesses": []}
    s = _mask(sql)
    known = {w.lower() for w in re.findall(r"\b(?:from|join|update|into)\s+[`\"]?([\w$]+)", s, re.I)}
    known |= {w.lower() for w in re.findall(r"\b(?:from|join|update)\s+[`\"]?[\w$.]+[`\"]?\s+(?:as\s+)?([A-Za-z_]\w*)", s, re.I)
              if w.lower() not in KW}
    slips, slip_edits = _value_slips(sql, s, known)
    more, more_edits = _statement_slips(sql, s, bool(re.match(r"[\s(]*(select|with)\b", s.lower())), intent)
    slips, slip_edits = slips + more, slip_edits + more_edits
    from app.agents import sqlbasics, sqlscan

    for p in sqlscan.plain_param_slips(sql, sqlbasics.CHOSEN.get()):   # a placeholder inside quotes / as a name (night 10A)
        slips.append(Finding(p["kind"], p["topic"], p["title_en"], p["title_hi"], p["evidence"], p["fix_en"], p["fix_hi"]))
        if p.get("edit"):
            slip_edits.append(p["edit"])
    out = _check_select(sql, s, slip_edits, _again, intent)
    if out is None:                                   # not a plain SELECT: only the value checks apply
        order = {"error": 0, "wrong": 1, "check": 2}
        slips.sort(key=lambda f: order[f.kind])
        return {"findings": [f.as_dict() for f in slips], "corrected": _apply_plain(sql, slip_edits), "guesses": []}
    if slips:
        have = {f["evidence"] for f in out["findings"]}
        out["findings"] = [f.as_dict() for f in slips if f.evidence not in have] + out["findings"]
        order = {"error": 0, "wrong": 1, "check": 2}
        out["findings"].sort(key=lambda f: order[f["kind"]])
        if not out.get("corrected"):
            out["corrected"] = _apply_plain(sql, slip_edits)
    return out


def _check_select(sql: str, s: str, slip_edits: list, _again: bool, intent: str = "") -> dict | None:
    dep = _depths(s)
    low = s.lower()
    if not re.match(r"\s*(with\b|select\b|\()", low):
        return None
    cl = _clauses(s, dep)
    if "from" not in cl or "select" not in cl:
        return None
    srcs = _sources(s, dep, *cl["from"])
    if not srcs:
        return None
    F: list[Finding] = []
    edits: list[tuple[int, int, str]] = list(slip_edits)   # (start, end, new text) on the original query
    value_at = {a for a, _b, _n in slip_edits}
    notes: dict[int, str] = {}                       # line start -> '-- check: ...' note
    guesses: list[str] = []

    def add(kind, topic, ten, thi, ev, fen, fhi):
        F.append(Finding(kind, topic, ten, thi, re.sub(r"\s+", " ", ev.strip()), fen, fhi))

    def line_of(pos: int) -> str:
        a = sql.rfind("\n", 0, pos) + 1
        b = sql.find("\n", pos)
        return sql[a:b if b >= 0 else len(sql)].strip()

    def note(pos: int, text: str):
        notes[len(notes)] = text
        edits.append((pos, pos, f"\x00{len(notes) - 1}\x00"))

    by_alias = {x.alias.lower(): x for x in srcs if x.alias}
    names = {x.name.split(".")[-1].lower(): x for x in srcs if x.name}
    nested_aliases = {m.group(2).lower() for m in re.finditer(r"\b(from|join)\s+[`\"]?[\w$.]+[`\"]?\s+(?:as\s+)?([A-Za-z_]\w*)", s, re.I)
                      if m.group(2).lower() not in KW}
    nested_aliases |= {m.group(1).lower() for m in re.finditer(r"\)\s*(?:as\s+)?([A-Za-z_]\w*)", s, re.I) if m.group(1).lower() not in KW}
    has_sub = bool(re.search(r"\(\s*select\b", low))
    sel_a, sel_b = cl["select"]
    items = []
    for off, txt in _split_top(s[sel_a:sel_b]):
        t = txt.strip()
        if not t:
            continue
        am = re.search(r"(?:\bas\s+|\s)[`\"]?([A-Za-z_]\w*)[`\"]?\s*$", t, re.I)
        alias = am.group(1) if am and am.group(1).lower() not in KW and not t.endswith(")") or (am and re.search(r"\bas\s+", t[am.start():], re.I)) else ""
        if alias and re.fullmatch(r"[`\"]?\w+[`\"]?\.[`\"]?\w+[`\"]?", t):
            alias = ""
        items.append({"text": t, "alias": alias, "start": sel_a + off + (len(txt) - len(txt.lstrip())),
                      "end": sel_a + off + len(txt.rstrip()), "agg": bool(AGG.search(_no_scalar_minmax(_no_subqueries(t)))) and " over" not in _no_subqueries(t).lower()})
    sel_aliases = {i["alias"].lower(): i for i in items if i["alias"]}

    # ------------------------------------------------ 1. names: aliases that don't exist, tables used by name after an alias
    removed: set[str] = set()
    fixes_alias: dict[str, str] = {}
    all_refs = _qrefs(s)
    for q, col, pos in all_refs:
        ql = q.lower()
        if ql in by_alias or (ql in nested_aliases and has_sub):
            continue
        if col == "*" and ql in names:
            continue
        target = None
        if ql in names and names[ql].alias.lower() != ql:
            target, why = names[ql], "name"
        else:
            close = [x for x in srcs if x.name and (_singulars(x.name) & _singulars(ql) or
                                                    difflib.SequenceMatcher(None, ql, x.name.lower()).ratio() >= 0.8)]
            close = list({x.name.lower(): x for x in reversed(close)}.values())   # cities joined twice is still one table
            if len(close) == 1:
                target, why = close[0], "close"
        if target is not None:
            fixes_alias[ql] = target.alias
            edits.append((pos, pos + len(q), target.alias))
            if why == "name":
                add("error", "alias", f"'{q}.{col}': the table is called {target.alias} here",
                    f"'{q}.{col}': yahan table ka naam {target.alias} hai", line_of(pos),
                    f"Once a table has an alias, MySQL only knows the alias (error 1054 Unknown column '{q}.{col}'). Use {target.alias}.{col}.",
                    f"Table ko alias dene ke baad MySQL sirf alias pehchanta hai (error 1054). {target.alias}.{col} likho.")
            else:
                add("error", "alias", f"'{q}.{col}': there is no table or alias called {q}",
                    f"'{q}.{col}': {q} naam ki koi table ya alias nahi hai", line_of(pos),
                    f"MySQL stops with error 1054 (Unknown column '{q}.{col}'). You joined {target.name} as {target.alias}: "
                    f"use {target.alias}.{col}.",
                    f"MySQL error 1054 dega. {target.name} ko aapne {target.alias} naam diya hai: {target.alias}.{col} likho.")
        elif not has_sub:
            add("error", "alias", f"'{q}.{col}': there is no table or alias called {q}",
                f"'{q}.{col}': {q} naam ki koi table ya alias nahi hai", line_of(pos),
                f"MySQL stops with error 1054. Join the table that has {col}, or use one of: "
                + ", ".join(x.alias for x in srcs if x.alias) + ".",
                f"MySQL error 1054 dega. Jis table mein {col} hai use join karo, ya inme se ek: "
                + ", ".join(x.alias for x in srcs if x.alias) + ".")

    def alias_of(q: str) -> str:
        return fixes_alias.get(q.lower(), q).lower()

    # ------------------------------------------------ 2. joins
    where = cl.get("where")
    owners_of = {}
    for q, c, _p in all_refs:
        owners_of.setdefault(c.lower(), set()).add(q)
    firm = _firm_refs(s, where, owners_of) if where else []
    firm_aliases = {alias_of(q) for q, _c, _p in firm}
    grew = True
    while grew:                         # a required row needs the rows its ON points at: refunds r ON r.payment_id = p.id
        grew = False
        for x in srcs:
            if (x.alias or "").lower() in firm_aliases and x.on:
                for q, _c, _p in _qrefs(x.on):
                    if alias_of(q) not in firm_aliases and alias_of(q) in by_alias:
                        firm_aliases.add(alias_of(q))
                        grew = True
    seen_aliases: list[str] = []
    nullable: set[str] = set()          # can be NULL in the result (and nothing else already demands a row there)
    outer: set[str] = set()             # put on the outer side of some join
    for i, x in enumerate(srcs):
        a = (x.alias or "").lower()
        refs = [(alias_of(q), c, p) for q, c, p in _qrefs(x.on, x.on_start)] if x.on else []
        x.refs = {r[0] for r in refs}
        if x.kind == "full":
            add("error", "join", f"FULL OUTER JOIN {x.name} {x.alias}: MySQL doesn't have FULL OUTER JOIN",
                f"FULL OUTER JOIN {x.name} {x.alias}: MySQL mein FULL OUTER JOIN nahi hota", line_of(x.start),
                "MySQL stops with a syntax error (1064). If you need rows from both sides, write a LEFT JOIN query and a RIGHT JOIN "
                "query and put UNION between them.",
                "MySQL syntax error (1064) dega. Dono taraf ki rows chahiye to LEFT JOIN wali aur RIGHT JOIN wali query UNION se jodo.")
        if x.kind == "cross" and x.on:
            add("check", "join", f"CROSS JOIN {x.name} {x.alias} with an ON condition",
                f"CROSS JOIN {x.name} {x.alias} ke saath ON condition", line_of(x.start),
                "A CROSS JOIN pairs every row with every row and has no ON. MySQL quietly treats this as an INNER JOIN; PostgreSQL "
                "and SQL Server refuse it. Write INNER JOIN if you mean a matched join.",
                "CROSS JOIN mein ON nahi hota (har row har row se judti hai). MySQL ise INNER JOIN maan leta hai, PostgreSQL error deta hai. "
                "Matching chahiye to INNER JOIN likho.")
        if x.kind in ("join", "left", "right", "full", "cross", "inner") and not x.on and not x.using and x.kind != "cross":
            from app.agents import sqlstruct                 # night 13: what each engine does, and the link the names suggest

            fam, says = sqlstruct._says("join_without_on")
            link = sqlstruct.guess_link(x, srcs[:i]) if x.name else None
            add("error" if fam == "postgresql" else "wrong", "join", f"{x.name} {x.alias} is joined without ON",
                f"{x.name} {x.alias} bina ON ke joda hai", line_of(x.start),
                f"Without ON every row is paired with every row of this table (rows multiply). {says} Add ON with the matching "
                "columns" + (f": ON {link} (guessed from the table names: check it)." if link else "."),
                "ON ke bina har row is table ki har row se judti hai (rows guna ho jaati hain). Matching columns ke saath ON lagao"
                + (f": ON {link} (table ke naamon se andaza: check karo)." if link else "."))
            if link:
                pos = x.start + len(s[x.start:x.end].rstrip())
                edits.append((pos, pos, f" ON {link} /* check: guessed from the names */"))
                guesses.append(f"ON {link}")
        if x.on and a and a not in x.refs and i > 0 and not x.lateral:
            from app.agents import sqlstruct

            hint = {r[0] for r in refs} & {(y.alias or "").lower() for y in srcs[:i]}
            link = sqlstruct.guess_link(x, srcs[:i], hint) if x.name else None
            plain = re.fullmatch(r"\s*[`\"]?\w+[`\"]?\.[`\"]?\w+[`\"]?\s*=\s*[`\"]?\w+[`\"]?\.[`\"]?\w+[`\"]?\s*", x.on or "")
            add("wrong", "join", f"The ON of {x.alias} doesn't mention {x.alias}", f"{x.alias} ke ON mein {x.alias} hai hi nahi",
                line_of(x.on_start), f"An ON condition must connect {x.alias} to a table before it; this one doesn't, so every {x.alias} row "
                "is paired with every row (rows multiply)." + (f" The names suggest ON {link} (a guess: check it)." if link else ""),
                f"ON ko {x.alias} ko pichli table se jodna chahiye; ye nahi jodta, isliye rows guna ho jaati hain."
                + (f" Naamon se lagta hai: ON {link} (andaza: check karo)." if link else ""))
            if link and plain:
                end = x.on_start + len(sql[x.on_start:x.on_end].rstrip())
                edits.append((x.on_start, end, f" {link} /* check: guessed from the names */"))
                guesses.append(f"ON {link}")
        later = [q for q in x.refs if q != a and q in by_alias and q not in seen_aliases]
        for q in later:
            add("error", "join", f"The ON of {x.alias} uses {q} before {q} is joined", f"{x.alias} ke ON mein {q}, jo abhi joda nahi",
                line_of(x.on_start), f"MySQL reads joins top to bottom: {q} isn't known yet here (error 1054). Join {q} first or move this condition.",
                f"MySQL joins upar se neeche padhta hai: yahan {q} abhi pata nahi (error 1054). Pehle {q} jodo.")
        # an outer join undone by a later INNER JOIN on the nullable side
        if x.kind in ("join", "inner") and x.on and nullable & x.refs and i > 0:
            n = sorted(nullable & x.refs)[0]
            add("wrong", "join", f"INNER JOIN {x.alias} on {n}, which was LEFT JOINed", f"{n} LEFT JOIN tha, par {x.alias} INNER JOIN se uspe juda",
                line_of(x.start), f"Rows where {n} has no match have NULL there, and this INNER JOIN drops them, so the LEFT JOIN of {n} "
                f"no longer keeps them. Use LEFT JOIN for {x.alias} too.",
                f"Jahan {n} ka match nahi, wahan NULL hai, aur ye INNER JOIN un rows ko hata deta hai: {n} ka LEFT JOIN bekaar. "
                f"{x.alias} ko bhi LEFT JOIN karo.")
            edits.append(_join_word_edit(sql, s, x, "LEFT JOIN"))
            x.kind = "left"
        if x.kind == "left":
            outer.add(a)
            if a not in firm_aliases:     # WHERE demands a row there: it's an inner join really (said below)
                nullable.add(a)
        if x.kind == "right":
            outer |= set(seen_aliases)
            if not firm_aliases & set(seen_aliases):
                nullable |= set(seen_aliases)
        seen_aliases.append(a)              # (FULL OUTER JOIN is an error in MySQL anyway: it doesn't change what's nullable)

    # join and relationship reasoning (app/agents/sqljoins.py): cartesian links, wrong key columns, anti-joins, self-join
    # pairs, NATURAL JOIN / USING (id), DISTINCT hiding a join's copies
    from app.agents import sqljoins

    whole = None                                     # a rewrite of the whole query (DISTINCT over a join -> EXISTS)
    for fnd, edit in sqljoins.joins(sql, s, cl, srcs, alias_of, items, intent, Finding, all_refs):
        if any(f.title_en == fnd.title_en and f.evidence == fnd.evidence for f in F):
            continue
        F.append(fnd)
        if edit and edit[2] is not None and (edit[0], edit[1]) == (0, len(sql)):
            whole = edit[2]
        elif edit and edit[2] is not None and not any(a < edit[1] and edit[0] < b for a, b, _n in edits):
            edits.append(edit)

    # aggregation reasoning (app/agents/sqlaggs.py): COUNT of a comparison or of CASE ... ELSE 0, totals inside totals,
    # windows in a grouped query, SUM over a LEFT JOIN, AVG with NULLs, averages of averages, shares, what was asked to count
    from app.agents import sqlaggs

    groups = []                                      # a finding and its edits ((None, edit) = one more edit of the one before)
    for fnd, edit in sqlaggs.aggs(sql, s, cl, srcs, alias_of, items, intent, Finding):
        if fnd is None and groups:
            groups[-1][1].append(edit)
        elif fnd is not None:
            groups.append((fnd, [edit] if edit else []))
    agg_edits = []
    for fnd, ges in groups:
        if any(f.title_en == fnd.title_en and f.evidence == fnd.evidence for f in F):
            continue
        F.append(fnd)
        ges = [e for e in ges if e and e[2] is not None]
        if len(ges) == 1 and (ges[0][0], ges[0][1]) == (0, len(sql)):
            whole = whole or ges[0][2]
        elif fnd.kind == "check" and _SURE_ONLY.get() and fnd.topic in _meaning_checks():
            continue                                 # a definite mistake is being fixed: a rewrite that changes the meaning waits
        elif ges and not any(a < e[1] and e[0] < b for e in ges for a, b, _n in edits + agg_edits):
            agg_edits += ges                         # all of a finding's edits, or none of them
    edits += agg_edits

    # a JOIN used only to keep the parents that have a child (app/agents/sqlsubq.py): EXISTS keeps each parent once
    from app.agents import sqlsubq
    for fnd, sedits in sqlsubq.semi_join(sql, s, cl, srcs, items, intent, Finding):
        F.append(fnd)
        whole = whole or next((e[2] for e in sedits if (e[0], e[1]) == (0, len(sql))), None)

    # self joins: the same table twice, joined parent.id = child.fk -> the children, not the parent
    for i, x in enumerate(srcs):
        if not x.on or not x.name:
            continue
        twins = [y for y in srcs[:i] if y.name and y.name.lower() == x.name.lower()]
        if not twins:
            continue
        for m in EQ.finditer(x.on):
            l, lc, r, rc = alias_of(m.group(1)), m.group(2), alias_of(m.group(3)), m.group(4)
            pair = {(l, lc), (r, rc)}
            mine = next(((q, c) for q, c in pair if q == x.alias.lower()), None)
            other = next(((q, c) for q, c in pair if q != x.alias.lower() and q in {t.alias.lower() for t in twins}), None)
            if not mine or not other:
                continue
            if other[1].lower() == "id" and mine[1].lower().endswith("_id") and mine[1].lower() != "id":
                fk = mine[1]
                role = fk[:-3]
                o = other[0]
                add("wrong", "join", f"Self join on {x.name}: {x.alias}.{fk} = {o}.id finds the rows {o} is the {role} of",
                    f"{x.name} ka self join: {x.alias}.{fk} = {o}.id un rows ko laata hai jinka {role} {o} hai", line_of(x.on_start),
                    f"This finds every {x.alias} whose {fk} is {o} (e.g. the people {o} manages), one row each, so rows repeat. "
                    f"To get {o}'s own {role}, join the other way: {x.alias}.id = {o}.{fk}.",
                    f"Ye har wo {x.alias} laata hai jiska {fk} {o} hai (jaise jinhe {o} manage karta hai), isliye rows repeat hoti hain. "
                    f"{o} ka apna {role} chahiye to ulta jodo: {x.alias}.id = {o}.{fk}.")
                edits.append((x.on_start + m.start(), x.on_start + m.end(), f"{x.alias}.id = {o}.{fk}"))

    # the same table joined twice the same way, and tables nothing uses
    def used(alias: str, skip: Source) -> bool:
        al = alias.lower()
        for q, _c, p in all_refs:
            if alias_of(q) == al and not (skip.start <= p < skip.end):
                return True
        return False

    for i, x in enumerate(srcs):
        if i == 0 or not x.name:
            continue
        twin = next((y for y in srcs[:i] if y.name and y.name.lower() == x.name.lower() and y.alias.lower() not in removed), None)
        if twin is None:
            continue
        same = x.on and twin.on and _same_cond(x.on, x.alias, twin.on, twin.alias, alias_of)
        if same or (not used(x.alias, x) and x.kind in ("full", "cross", "left")):
            removed.add(x.alias.lower())
            add("wrong", "join", f"{x.name} is joined twice ({twin.alias} and {x.alias})", f"{x.name} do baar juda hai ({twin.alias} aur {x.alias})",
                line_of(x.start), f"The second join adds nothing{'' if used(x.alias, x) else ' (nothing uses ' + x.alias + ')'} and can repeat rows. "
                f"Remove it{'' if not used(x.alias, x) else ' and use ' + twin.alias + ' instead'}.",
                f"Doosra join kuch nahi deta aur rows repeat kar sakta hai. Hata do.")
            edits.append((_line_start(sql, x.start), _cut_end(sql, x.end), ""))
            for q, _c, p in all_refs:
                if alias_of(q) == x.alias.lower() and not (x.start <= p < x.end):
                    edits.append((p, p + len(q), twin.alias))

    # ------------------------------------------------ 3. conditions
    # a number in quotes compared with > < BETWEEN: on a text column '999' > '1000' (letter by letter); a plain number is
    # compared as a number whatever the column is. (= keeps its quotes: a bare number on a text id column skips its index)
    num_lit = re.compile(r"(>=|<=|(?<![<!])>|<(?![>=])|\bbetween\b|\band\b)\s*'(-?\d+(?:\.\d+)?)'", re.I)
    numeric_name = re.compile(r"(amount|price|qty|quantity|total|count|age|salary|rate|fee|fees|balance|marks|score|stock|"
                              r"cost|discount|tax|paid|due|weight|height|percent|percentage|level|rank|year|days|hours|minutes)s?$", re.I)
    for m in num_lit.finditer(sql):
        if dep[m.start()] < 0 or s[m.start(2) - 1] != "'":
            continue
        op = m.group(1).lower()
        if op == "and" and not re.search(r"\bbetween\s+'?-?[\d.]+'?\s*$", sql[max(0, m.start() - 40):m.start()], re.I):
            continue                                       # a plain AND, not BETWEEN x AND 'y'
        lhs = re.search(r"([`\"]?\w+[`\"]?(?:\.[`\"]?\w+[`\"]?)?)\s*(?:not\s+)?(?:between\s+'?-?[\d.]+'?\s*)?$", sql[:m.start()], re.I)
        col = lhs.group(1) if lhs else "the column"
        val = m.group(2)
        bare_col = col.split(".")[-1].strip("`\"")
        sure = bool(numeric_name.search(bare_col)) and not (len(val) > 1 and val.startswith("0"))
        add("check", "column", f"{col} {m.group(1).upper() if op != 'and' else 'BETWEEN'} '{val}': a number in quotes",
            f"{col} ke saath '{val}': number quotes mein", line_of(m.start()),
            f"If {col} is a text column, '{val}' is compared letter by letter ('999' counts as bigger than '1000'). Without quotes "
            f"it's compared as a number either way: {val}.",
            f"Agar {col} text column hai to '{val}' letter-by-letter compare hota hai ('999' > '1000'). Bina quotes ke number ki "
            f"tarah compare hoga: {val}.")
        if sure:                                           # a money/count column: compare as a number (PostgreSQL refuses
            q0 = m.start(2) - 1                            # text > number, so a column that may be text keeps its quotes)
            edits.append((q0, q0 + len(val) + 2, val))

    for m in re.finditer(r"(!=|<>|=)\s*null\b|\bnull\s*(!=|<>|=)", s, re.I):
        op = (m.group(1) or m.group(2))
        right = "IS NOT NULL" if op in ("!=", "<>") else "IS NULL"
        add("wrong", "null", f"'{op} NULL' is never true", f"'{op} NULL' kabhi true nahi hota", line_of(m.start()),
            f"Nothing equals NULL, not even NULL, so this condition removes every row. Write {right}.",
            f"NULL kisi ke barabar nahi hota, isliye ye condition saari rows hata deti hai. {right} likho.")
        if m.group(1):
            edits.append((m.start(), m.end(), right))
    where = cl.get("where")
    if where:
        wtext = _no_subqueries(s[where[0]:where[1]])     # a name inside a subquery is that subquery's own column
        from app.agents import sqlwin

        win_names = sqlwin.window_names(s)                 # a window's name in WHERE: sqlwin's win_alias_where (wrapped, not repeated)
        for m in WORD.finditer(wtext):
            w = m.group(1)
            if w.lower() in win_names:
                continue
            if w.lower() in sel_aliases and not any(w.lower() == (x.alias or "").lower() for x in srcs) and \
                    not re.match(r"\s*\.", wtext[m.end():]) and not any(w.lower() == c.lower() for _q, c, _p in all_refs) and \
                    not re.search(rf"(?<![\w.]){re.escape(w)}(?!\w)", re.sub(r"\s+as\s+\S+\s*$", "", sel_aliases[w.lower()]["text"], flags=re.I), re.I):
                # SUM(total) AS total: 'total' is also a real column, and WHERE total means that column (MySQL and PostgreSQL)
                add("error", "alias", f"WHERE uses {w}, a name made in SELECT", f"WHERE mein {w}, jo SELECT mein banaya naam hai",
                    line_of(where[0] + m.start()), "WHERE runs before SELECT, so it can't see SELECT's names (error 1054). Repeat the "
                    "expression itself in WHERE.", "WHERE, SELECT se pehle chalta hai, use SELECT ke naam nahi dikhte (error 1054). "
                    "Wahi expression WHERE mein likho.")
                edits.append((where[0] + m.start(), where[0] + m.end(), f"\x01{w.lower()}\x01"))   # filled in at the end
        for q, c, p in firm:
            qa = alias_of(q)
            if qa in removed:
                continue
            if qa in outer and not re.match(r"\s*is\s+null\b", s[p + len(q) + 1 + len(c):], re.I):
                src = by_alias.get(qa)
                kind = next((x.kind for x in srcs if (x.alias or "").lower() == qa), "")
                how = "LEFT JOIN" if kind == "left" else "an outer join"
                add("check", "join", f"WHERE on {q}.{c} cancels the {how} of {q}" if kind == "left" else
                    f"WHERE on {q}.{c} cancels the RIGHT JOIN",
                    f"WHERE mein {q}.{c} se {q} ka outer join bekaar ho jaata hai", line_of(p),
                    f"Rows kept only by the outer join have NULL in {q}.{c}, and this WHERE throws them away, so it works like an INNER "
                    "JOIN. If you want those rows, move this condition into the ON; if not, write INNER JOIN.",
                    f"Outer join wali extra rows mein {q}.{c} NULL hota hai, aur ye WHERE unhe hata deta hai: ye INNER JOIN jaisa hai. "
                    "Wo rows chahiye to condition ON mein le jao, warna INNER JOIN likho.")
                outer.discard(qa)
                for x in srcs:
                    if x.kind == "right":
                        edits.append(_join_word_edit(sql, s, x, "INNER JOIN"))
                        x.kind = "inner"
                    if (x.alias or "").lower() == qa and x.kind == "left":
                        edits.append(_join_word_edit(sql, s, x, "INNER JOIN"))
                        x.kind = "inner"
                if kind == "right":
                    outer -= {al for al in outer if by_alias.get(al) and by_alias[al].kind != "left"}

    # ------------------------------------------------ 4. likely wrong column names (the query's own naming says so)
    pk_style = sum(1 for _q, c, _p in all_refs if c.lower() == "id")
    for x in srcs:
        if not x.on:
            continue
        for m in EQ.finditer(x.on):
            sides = [(m.group(1), m.group(2), x.on_start + m.start(1)), (m.group(3), m.group(4), x.on_start + m.start(3))]
            for (q, c, p), (q2, c2, _p2) in (sides, sides[::-1]):
                t = by_alias.get(alias_of(q))
                t2 = by_alias.get(alias_of(q2))
                if not t or not t2 or not t.name or not t2.name:
                    continue
                # orders.customer = customers.id -> customer_id (the other foreign keys here end in _id)
                if c2.lower() == "id" and c.lower() in _singulars(t2.name) - {t2.name.lower()} and not c.lower().endswith("_id"):
                    new = c + "_id"
                    guesses.append(f"{q}.{c} -> {q}.{new}")
                    add("check", "column", f"{q}.{c} is probably {q}.{new}", f"{q}.{c} shayad {q}.{new} hai", line_of(p),
                        f"The other link columns in this query end in _id, so the column that points to {t2.name} is most likely "
                        f"{new}. Check your table: if it really is {c}, keep it.",
                        f"Is query ke baaki link columns _id pe khatam hote hain, isliye {t2.name} wala column shayad {new} hai. "
                        f"Table check karo; sach mein {c} hai to wahi rakho.")
                    cs = p + len(q) + 1
                    edits.append((cs, cs + len(c), new))
                    note(cs + len(c), f"check: {new} (you wrote {c})")
                # categories.category_id used as the key, while every other table's key is id
                if c.lower() in {x + "_id" for x in _singulars(t.name)} and pk_style >= 2 and c2.lower() == c.lower() and t is not t2:
                    add("check", "column", f"{q}.{c}: is the key of {t.name} called id?", f"{q}.{c}: kya {t.name} ki key id hai?", line_of(p),
                        f"Every other table here is matched on its id, and {t.name} is the table {c} points to, so its own key is "
                        f"most likely id. Check your table: if {t.name} really has {c} as its key, keep it.",
                        f"Baaki sab tables id se judti hain, aur {c} isi {t.name} ki taraf ishara karta hai, isliye iski key shayad id hai. "
                        "Table check karo.")
                    # a note only: some schemas key a table on class_id while others use id, and the query can't say which
    # one alias, two names for what looks like one column: oi.price and oi.unit_price
    cols_by = {}
    for q, c, p in all_refs:
        cols_by.setdefault(alias_of(q), {}).setdefault(c.lower(), []).append((q, c, p))
    shown = {re.sub(r"\s+", "", i["text"]).lower() for i in items}
    made = BLOCK_COLS.get() or {}
    for al, cols in cols_by.items():
        src = next((x for x in srcs if (x.alias or x.name or "").strip('`"').lower() == al), None)
        bname = (src.name or "").split(".")[-1].strip('`"').lower() if src is not None and src.name else al
        if made and bname not in made:                     # the alias of a table inside a CTE: its FROM / JOIN names it
            fm = re.search(rf"\b(?:from|join)\s+[`\"]?([A-Za-z_][\w$]*)[`\"]?\s+(?:as\s+)?{re.escape(al)}\b", s, re.I)
            bname = fm.group(1).lower() if fm else bname
        if bname in made and all(c in made[bname] for c in cols):
            continue                                       # a block of this query plainly makes every one of these columns
        done = set()
        for c in sorted(cols):
            for d in sorted(cols):
                if d <= c or (c, d) in done:
                    continue
                done.add((c, d))
                if _slip(c, d):                    # max_mark / max_marks: one typing slip, so one of them is a typo
                    pick = [x for x in (c, d) if any(q + "." + x == t for q in {r[0].lower() for r in cols[x]} for t in shown)]
                    if len(pick) != 1:
                        pick = [x for x in (c, d) if len(cols[x]) > len(cols[c if x == d else d])]
                    if len(pick) != 1:
                        add("check", "column", f"{al}.{c} and {al}.{d}: one is probably a typo", f"{al}.{c} aur {al}.{d}: ek shayad typo hai",
                            line_of(cols[c][0][2]), "They differ by one letter; use the name your table really has in both places.",
                            "Sirf ek letter ka farak hai; jo naam aapki table mein hai wahi dono jagah likho.")
                        continue
                    right = pick[0]
                    wrong = d if right == c else c
                    for q, cc, p in cols[wrong]:
                        guesses.append(f"{q}.{cc} -> {q}.{right}")
                        add("check", "column", f"{q}.{cc} is probably {q}.{right}", f"{q}.{cc} shayad {q}.{right} hai", line_of(p),
                            f"One letter away from {q}.{right}, which the query uses elsewhere; a table rarely has both. Check your table.",
                            f"{q}.{right} se sirf ek letter alag, jo query mein aur jagah hai. Table check karo.")
                        cs = p + len(q) + 1
                        edits.append((cs, cs + len(cc), right))
                        note(cs + len(cc), f"check: {right} (you wrote {cc})")
                elif len(c) >= 4 and not c.endswith("id") and not d.endswith("_id") and (d.endswith("_" + c) or c.endswith("_" + d)):
                    short, long_ = (c, d) if d.endswith("_" + c) else (d, c)
                    q = cols[short][0][0]
                    add("check", "column", f"{q}.{short} and {q}.{long_}: two columns, or one?", f"{q}.{short} aur {q}.{long_}: do column ya ek?",
                        line_of(cols[short][0][2]), f"If your table has only one of these, use it in both places (MySQL error 1054 otherwise). "
                        f"If it has both, this is fine.", f"Table mein inme se ek hi hai to dono jagah wahi likho (warna error 1054). "
                        "Dono hain to theek hai.")

    # ------------------------------------------------ 5. unqualified columns in a many-table query
    qualified = [c.lower() for _q, c, _p in all_refs]
    grain = _grain(srcs, alias_of, removed)

    def bare_to_grain(w: str, pos: int):
        if grain is None:                                     # no single table the rows belong to: only the structure can say
            add("check", "column", f"{w} has no table in front of it", f"{w} ke aage table ka naam nahi", line_of(pos),
                f"With {len(srcs)} tables, write which table it's from (alias.{w}); if two tables have it, MySQL stops with error 1052.",
                f"{len(srcs)} tables mein batao ye kis table ka hai (alias.{w}); warna error 1052 ho sakta hai.")
            return
        g = by_alias[grain]
        add("check", "column", f"{w} has no table in front of it: most likely {g.alias}.{w}",
            f"{w} ke aage table nahi: shayad {g.alias}.{w}", line_of(pos),
            f"Each result row is one {g.name} row (the other tables are looked up from it), so {w} most likely means "
            f"{g.alias}.{w}. Without the table name MySQL stops (error 1052) if two tables have {w}.",
            f"Har row ek {g.name} row hai, isliye {w} shayad {g.alias}.{w} hai. Table ke bina, do tables mein {w} ho to error 1052.")
        guesses.append(f"{w} -> {g.alias}.{w}")
        edits.append((pos, pos + len(w), f"{g.alias}.{w}"))
        note(pos + len(w), f"check: {g.alias}.{w} (you wrote {w})")
    bare_seen = set()
    if len([x for x in srcs if x.alias.lower() not in removed]) > 1:
        for clause in ("select", "where", "group by", "having", "order by"):
            if clause not in cl:
                continue
            a0, b0 = cl[clause]
            for m in WORD.finditer(_no_subqueries(s[a0:b0])):
                w = m.group(1)
                wl = w.lower()
                before = s[a0:a0 + m.start()].rstrip().lower()
                if wl in KW or before.endswith(" as") or before.endswith(".") or re.match(r"\s*\.", s[a0 + m.end():]):
                    continue
                if clause in ("order by", "having", "group by") and wl in sel_aliases:
                    continue
                if clause == "select" and any(i["alias"].lower() == wl for i in items):
                    continue
                if wl in by_alias or wl in names or wl in bare_seen or wl in nested_aliases or re.match(r"^\d", w):
                    continue
                if a0 + m.start() in value_at:                # a value written without quotes, already reported
                    continue
                bare_seen.add(wl)
                owners = sorted({q for q, c, _p in all_refs if c.lower() == wl})
                pos = a0 + m.start()
                if clause in ("order by", "group by", "having") and items and any(a.startswith(wl) or wl.startswith(a) for a in sel_aliases):
                    owners = []
                if len(owners) == 1:
                    o = owners[0]
                    add("check", "column", f"{w} has no table in front of it", f"{w} ke aage table ka naam nahi", line_of(pos),
                        f"With {len(srcs)} tables, MySQL stops (error 1052, ambiguous) if two of them have {w}. Elsewhere you wrote "
                        f"{o}.{w}, so use that.", f"{len(srcs)} tables mein agar do mein {w} hai to MySQL error 1052 dega. "
                        f"Baaki jagah {o}.{w} likha hai, wahi likho.")
                    edits.append((pos, pos + len(w), f"{o}.{w}"))
                elif clause in ("order by", "group by", "having") and items:
                    close = [a for a in sel_aliases if a.startswith(wl) or wl.startswith(a)] or \
                        difflib.get_close_matches(wl, list(sel_aliases), n=2, cutoff=0.5)
                    close = sorted(close, key=lambda a: (not a.endswith("name"), a))
                    if close:
                        c = sel_aliases[close[0]]["alias"]
                        other = f" (or {sel_aliases[close[1]]['alias']})" if len(close) > 1 else ""
                        guesses.append(f"{w} -> {c}")
                        add("error", "order" if clause == "order by" else "column", f"{clause.upper()} uses {w}, which isn't a column here",
                            f"{clause.upper()} mein {w}, jo yahan koi column nahi", line_of(pos),
                            f"No table alias and no SELECT name is called {w} (error 1054). You probably mean {c}{other}.",
                            f"{w} naam ka koi column ya SELECT naam nahi (error 1054). Shayad {c}{other} chahiye.")
                        edits.append((pos, pos + len(w), c))
                        note(pos + len(w), f"check: {c} (you wrote {w})")
                    else:
                        bare_to_grain(w, pos)
                elif clause in ("select", "where"):
                    bare_to_grain(w, pos)

    # ------------------------------------------------ 6. GROUP BY and totals
    group = cl.get("group by")
    aggs = [i for i in items if i["agg"]]
    plain = [i for i in items if not i["agg"]]
    drop_grouping = False
    if group or aggs:
        gtext = s[group[0]:group[1]] if group else ""
        gref = {(alias_of(q), c.lower()) for q, c, _p in _qrefs(gtext)}
        gbare = {w.lower() for w in _bare(gtext, set())}
        def is_key(al: str, col: str) -> bool:              # 'id', or students.student_id: the table's own key
            t = by_alias.get(al)
            kb = _block_key(t, col)
            if kb is not None:
                return kb
            c = col.lower().replace("_", "")
            return c == "id" or bool(t and t.name and c in {x.replace("_", "") + "id" for x in _singulars(t.name)})

        single = {q for q, c in gref if is_key(q, c)}       # grouped by its key: one row per group
        changed = True
        while changed:                                         # tables joined to-one from those are one row per group too
            changed = False
            for x in srcs:
                al = (x.alias or "").lower()
                if al in single or not x.on:
                    continue
                for m in EQ.finditer(x.on):
                    l, lc, r, rc = alias_of(m.group(1)), m.group(2).lower(), alias_of(m.group(3)), m.group(4).lower()
                    if (l == al and is_key(l, lc) and r in single and not is_key(r, rc)) or \
                            (r == al and is_key(r, rc) and l in single and not is_key(l, lc)):
                        single.add(al)
                        changed = True
        loose = []
        # GROUP BY 1, 2: positions in the SELECT list (every engine reads them so); that SELECT item is grouped as a whole
        ords = {int(x.strip()) for _p, x in _split_top(gtext) if x.strip().isdigit()}
        # ... and GROUP BY a SELECT name (u in SELECT UPPER(c) u ... GROUP BY u): MySQL, MariaDB and PostgreSQL group by that item
        named = {x.strip().strip('`"').lower() for _p, x in _split_top(gtext)} - {(x.alias or "").lower() for x in srcs}
        for i in plain:
            if items.index(i) + 1 in ords or ((i.get("alias") or "").lower() in named and i.get("alias")):
                continue
            if re.search(r"\bover\s*\(", i["text"], re.I):
                continue                                 # a window: judged with its own rule (app/agents/sqlaggs.py, window_agg)
            for q, c, _p in _qrefs(i["text"]):
                if (alias_of(q), c.lower()) not in gref and alias_of(q) not in single:
                    loose.append(f"{q}.{c}")
            for w in _bare(i["text"], set()):
                if w.lower() not in gbare and not any(w.lower() == c for _q, c in gref):
                    loose.append(w)
        loose = list(dict.fromkeys(loose))
        if aggs and loose and group:
            add("error", "group", "Columns in SELECT that aren't grouped", "SELECT mein aise columns jo group nahi hue",
                "GROUP BY " + re.sub(r"\s+", " ", sql[group[0]:group[1]].strip()),
                f"{', '.join(loose[:6])}{' …' if len(loose) > 6 else ''} can have many values in one group, so MySQL refuses the "
                "query (error 1055, ONLY_FULL_GROUP_BY is on by default) or, with it off, shows one random value. Either list "
                "rows without totals (drop COUNT and GROUP BY), or show totals with only the grouped columns.",
                f"{', '.join(loose[:6])} ek group mein kai value ho sakti hain, isliye MySQL error 1055 deta hai (ya random value "
                "dikhata hai). Ya to rows bina total ke dikhao (COUNT aur GROUP BY hatao), ya total sirf group wale columns ke saath.")
        elif aggs and loose and not group:
            add("error", "group", "A total (COUNT/SUM) next to plain columns, without GROUP BY",
                "Bina GROUP BY ke total (COUNT/SUM) aur plain columns saath", items[0]["text"][:60],
                "MySQL refuses this (error 1140) or squashes everything into one row. Add GROUP BY for the plain columns.",
                "MySQL error 1140 deta hai ya sab ek row mein daba deta hai. Plain columns ka GROUP BY lagao.")
        links = [(alias_of(m2.group(1)), m2.group(2).lower(), alias_of(m2.group(3)), m2.group(4).lower())
                 for x in srcs if x.on for m2 in EQ.finditer(x.on)]

        def to_one_from(start: set) -> set:                 # tables reached one row each from these (any join's ON)
            got, more = set(start), True
            while more:
                more = False
                for l, lc, r, rc in links:
                    for a1, c1, b1 in ((l, lc, r), (r, rc, l)):
                        if a1 not in got and b1 in got and is_key(a1, c1):
                            got.add(a1)
                            more = True
            return got

        live = [(x.alias or "").lower() for x in srcs if x.alias and x.alias.lower() not in removed]
        for i in aggs:                                       # SUM(f.amount) with marks joined too: each fee counted per mark
            fm = re.search(r"\b(sum|count|avg)\s*\(\s*(distinct\s+)?([A-Za-z_]\w*)\.(\w+)", i["text"], re.I)
            if not fm or fm.group(2):
                continue
            A = alias_of(fm.group(3))
            if A in single or A not in by_alias:
                continue                                     # (a total inside a subquery belongs to the subquery's own tables)
            mine = to_one_from({A})
            others = [y for y in live if y not in single and y not in mine and y != A and A not in to_one_from({y})]
            kids = [y for y in live if y not in single and y != A and A in to_one_from({y}) and y not in mine]
            if not others and len(kids) >= 2:
                others = kids          # two child tables of A (items AND payments): every A row repeats items x payments times
            if others:
                o = by_alias[others[0]]
                add("wrong", "group", f"{fm.group(1).upper()}({fm.group(3)}.{fm.group(4)}) is multiplied by the {o.name} rows",
                    f"{fm.group(1).upper()}({fm.group(3)}.{fm.group(4)}) {o.name} ki rows se guna ho jaata hai", i["text"],
                    f"{by_alias[A].name} and {o.name} are both many rows per group, so every {by_alias[A].name} row repeats once per "
                    f"{o.name} row and the {fm.group(1).upper()} comes out too big. Total {by_alias[A].name} in its own subquery "
                    f"(SELECT {fm.group(3)}.<key>, {fm.group(1).upper()}(...) ... GROUP BY ...) and join that.",
                    f"{by_alias[A].name} aur {o.name} dono ki ek group mein kai rows hain, isliye har row baar baar gini jaati hai aur "
                    f"{fm.group(1).upper()} bada aata hai. {by_alias[A].name} ka total alag subquery mein nikaal ke join karo.")
        told = {f.evidence for f in F if f.topic == "group"}
        for i in aggs:                                       # night 11: SUM(o.amount) with ONE child table joined (order_items)
            fm = re.search(r"\b(sum|count|avg)\s*\(\s*(distinct\s+)?([A-Za-z_]\w*)\.(\w+)", i["text"], re.I)
            if not fm or fm.group(2) or re.sub(r"\s+", " ", i["text"].strip()) in told:
                continue
            A = alias_of(fm.group(3))
            if A not in by_alias:
                continue
            mine = to_one_from({A})
            kids = [y for y in live if y != A and A in to_one_from({y}) and y not in mine]
            if len(kids) != 1 or any(y not in mine and y != A and y not in kids for y in live):
                continue
            from app.agents.sqlverify import holds

            k = by_alias[kids[0]]
            if group and kids[0] in single:
                continue                                     # grouped by the child's own key: one child row per group, nothing repeats
            if k.kind == "from" and (by_alias[A].name or by_alias[A].alias):
                # the child is the FROM table (FROM orders o JOIN spend s ON s.customer_id = o.customer_id): advice, no rewrite
                ka = by_alias[A]
                an = ka.name or ka.alias
                fnd = Finding("check", "join_fanout", f"{fm.group(1).upper()}({fm.group(3)}.{fm.group(4)}) counts each {an} row once "
                              f"per {k.name or k.alias} row", f"{fm.group(1).upper()}({fm.group(3)}.{fm.group(4)}) har {an} row ko har "
                              f"{k.name or k.alias} row ke liye ginta hai", i["text"],
                              f"Each {k.name or k.alias} row meets one {an} row, so a group with several {k.name or k.alias} rows of "
                              f"the same {an} row adds that row's {fm.group(4)} several times. Group by {k.name or k.alias}'s own key, "
                              f"or join {an} after totalling {k.name or k.alias} to {an}'s grain.",
                              f"Har {k.name or k.alias} row ek {an} row se milti hai, isliye group mein ek hi {an} ki kai "
                              f"{k.name or k.alias} rows hon to uska {fm.group(4)} kai baar judta hai. {k.name or k.alias} ki key se "
                              f"group karo, ya {k.name or k.alias} ka total {an} ke grain pe nikaal ke jodo.")
                if holds(fnd, []):
                    F.append(fnd)
                continue
            if k.kind not in ("join", "inner", "left") or not k.on or not k.name or not by_alias[A].name:
                continue
            from app.agents.sqlverify import holds

            fnd = Finding("check", "join_fanout", f"{fm.group(1).upper()}({fm.group(3)}.{fm.group(4)}) counts each {by_alias[A].name} row "
                          f"once per {k.name} row", f"{fm.group(1).upper()}({fm.group(3)}.{fm.group(4)}) har {by_alias[A].name} row ko "
                          f"har {k.name} row ke liye ginta hai", i["text"],
                          f"{k.name} is joined on its link to {by_alias[A].name} ({re.sub(chr(10), ' ', sql[k.on_start:k.on_end]).strip()}): a "
                          f"{by_alias[A].name} row with several {k.name} rows comes back once per {k.name} row, so the "
                          f"{fm.group(1).upper()} grows with them. Whether it happens depends on the data (one {k.name} row each "
                          f"is safe), so it is a risk, not proven. Total {by_alias[A].name} on its own before joining {k.name}, or "
                          f"use EXISTS when {k.name} only filters.",
                          f"{k.name} {by_alias[A].name} ke link pe juda hai: jis {by_alias[A].name} row ki kai {k.name} rows hain wo "
                          f"utni baar aati hai, isliye {fm.group(1).upper()} badh jaata hai. Ye data pe tika hai (har ek ki ek hi "
                          f"{k.name} row ho to theek), isliye risk hai, pakka nahi. {k.name} jodne se pehle {by_alias[A].name} ka "
                          f"total nikaalo, ya {k.name} sirf filter ho to EXISTS.")
            if not holds(fnd, [("to_one", k.alias or k.name, None)]):
                continue
            F.append(fnd)
            outside = s[:k.start] + " " * (k.end - k.start) + s[k.end:]
            used = re.search(rf"(?<![\w.]){re.escape(k.alias or k.name)}\.", outside, re.I)
            last = not any(y.start > k.start for y in srcs)
            if used or not last:
                continue                                     # the child is used elsewhere: only the advice, no rewrite
            cond = sql[k.on_start:k.on_end].strip()
            src_name = sql[k.start:k.end]
            nm = re.search(rf"(?:join)\s+([`\"]?{re.escape(k.name)}[`\"]?(?:\s+(?:as\s+)?[`\"]?{re.escape(k.alias or '')}[`\"]?)?)\s+on\b", src_name, re.I)
            if not nm:
                continue
            ex = f"EXISTS (SELECT 1 FROM {nm.group(1).strip()} WHERE {cond})"
            wcl = cl.get("where")
            if k.kind == "left":
                edits.append((k.start, k.end, ""))           # a LEFT JOIN used nowhere else: the parent rows stay, once each
            elif wcl:
                edits.append((k.start, k.end, ""))
                edits.append((wcl[0], wcl[0], f" {ex} AND ("))
                wend = wcl[0] + len(s[wcl[0]:wcl[1]].rstrip().rstrip(";").rstrip())
                edits.append((wend, wend, ")"))
            else:
                edits.append((k.start, k.end, f"WHERE {ex} "))
        _fold_fanout(F)                                      # night 15: one finding for many totals over the same join
        for i in aggs:
            m = re.search(r"\bcount\s*\(\s*(distinct\s+)?([A-Za-z_]\w*)\.(\w+)\s*\)", i["text"], re.I)
            if m and not m.group(1) and (alias_of(m.group(2)), m.group(3).lower()) in gref:
                many = [x.alias for x in srcs if (x.alias or "").lower() not in single and x.alias.lower() not in removed and x.alias]
                add("wrong", "group", f"{m.group(0)} is grouped by {m.group(2)}.{m.group(3)} itself",
                    f"{m.group(0)} usi {m.group(2)}.{m.group(3)} se group hai", i["text"],
                    f"Each group is one {m.group(2)}.{m.group(3)}, so this is 1"
                    + (f", or the number of joined {', '.join(many)} rows" if many else "")
                    + f", not a count of {m.group(2)}. To count them per "
                    + (re.sub(r"\s+", " ", sql[group[0]:group[1]].strip()).split(",")[0].strip()) + ", group by that alone and use "
                    f"COUNT(DISTINCT {m.group(2)}.{m.group(3)}).",
                    f"Har group ek hi {m.group(2)}.{m.group(3)} hai, isliye ye 1 (ya judi rows ki ginti) hai, {m.group(2)} ki ginti nahi. "
                    f"COUNT(DISTINCT {m.group(2)}.{m.group(3)}) aur sirf upar wale column ka GROUP BY.")
                if loose and group:
                    drop_grouping = True
    having = cl.get("having")
    if having:
        htext = s[having[0]:having[1]]
        hd = _depths(htext)
        cuts = [0] + [m.end() for m in re.finditer(r"\band\b", htext, re.I) if hd[m.start()] == 0] + [len(htext)]
        totals = {a for a, it in sel_aliases.items() if it["agg"]}
        per_row = []                     # AND-parts that use no total (SUM, COUNT, or a SELECT name for one)
        for a1, b1 in zip(cuts, cuts[1:]):
            part = htext[a1:b1]
            if re.search(r"\bover\b", part, re.I):
                continue                                  # a window function in HAVING: sqlwin's win_where
            if not AGG.search(part) and not any(w.lower() in totals for w in _bare(part, set())):
                per_row.append(re.sub(r"(?i)^\s*and\s+|\s+and\s*$", "", sql[having[0] + a1:having[0] + b1]).strip())
        # night 13: a condition on a GROUP BY column gives the same rows in WHERE (both engines, lab), only later: a speed risk
        gkeys = set()
        if group:
            for _o, g in _split_top(s[group[0]:group[1]]):
                g = g.strip()
                if re.fullmatch(r"(?:[`\"]?\w+[`\"]?\.)?[`\"]?\w+[`\"]?", g) and not (
                        g.strip('`"').lower() in sel_aliases and sel_aliases[g.strip('`"').lower()]["text"].strip('`"').lower() != g.strip('`"').lower()):
                    gkeys.add(re.sub(r"[`\"]", "", g).lower())
        def _on_key(part: str) -> bool:
            p = re.sub(r"'(?:[^']|'')*'", "''", part)
            cols = [re.sub(r"[`\"]", "", f"{q}.{c}").lower() for q, c, _x in _qrefs(p)] + \
                   [w.lower() for w in _bare(re.sub(r"[`\"]?\w+[`\"]?\.[`\"]?\w+[`\"]?", " ", p), set()) if not re.fullmatch(r"\d+", w)]
            return bool(cols) and all(c in gkeys or c.split(".")[-1] in {k.split(".")[-1] for k in gkeys} and
                                      (("." in c) == any("." in k for k in gkeys if k.split(".")[-1] == c.split(".")[-1]) or "." not in c)
                                      for c in cols) and not re.search(r"\(\s*select\b", part, re.I)
        per_row = [p.rstrip().rstrip(";").rstrip() for p in per_row]
        on_key = [p for p in per_row if gkeys and _on_key(p)]
        per_row = [p for p in per_row if p not in on_key]
        if on_key:
            hv = _short(" AND ".join(on_key))
            cond = " AND ".join(on_key)
            hk_edits = []
            if not per_row and len(on_key) == len([1 for a1, b1 in zip(cuts, cuts[1:])
                                                    if not re.search(r"\bover\b", htext[a1:b1], re.I)]) and "having" in cl:
                hkw = re.search(r"\bhaving\s*$", s[:having[0]], re.I).start()
                hend = having[0] + len(s[having[0]:having[1]].rstrip().rstrip(";").rstrip())
                hk_edits.append((hkw, hend, ""))
                if where:
                    wend = where[0] + len(s[where[0]:where[1]].rstrip().rstrip(";").rstrip())
                    hk_edits.append((where[0], where[0], " ("))
                    hk_edits.append((wend, wend, f") AND {cond}"))
                else:
                    gkw = re.search(r"\bgroup\s+by\s*$", s[:group[0]], re.I).start()
                    hk_edits.append((gkw, gkw, f"WHERE {cond} "))
            add("check", "having_key", f"HAVING {hv} filters on a GROUP BY column: WHERE does it before grouping",
                f"HAVING {hv} GROUP BY column pe filter hai: WHERE ise grouping se pehle karta hai",
                "HAVING " + re.sub(r"\s+", " ", cond),
                "This is valid and returns the right rows: every row of a group has the same value of a GROUP BY column. But "
                "HAVING runs after every group was built, so rows that are thrown away were still read and grouped (a speed "
                "risk). In WHERE the same condition removes them first and can use an index; the result is exactly the same "
                "(lab: one row either way on MariaDB and PostgreSQL).",
                "Ye sahi hai aur sahi rows deta hai: group ki har row mein GROUP BY column ki value ek hi hoti hai. Par HAVING "
                "saare groups banne ke baad chalta hai, isliye phenki jaane wali rows bhi padhi aur group hoti hain (speed ka "
                "risk). WHERE mein wahi condition unhe pehle hata deti hai aur index use kar sakti hai; result bilkul wahi.")
            if hk_edits and not any(a < e[1] and e[0] < b for e in hk_edits for a, b, _n in edits):
                edits += hk_edits
        if per_row:
            hv = _short(" AND ".join(per_row))
            add("wrong" if group else "check", "having", f"HAVING {hv} filters a per-row value",
                f"HAVING {hv} ek row ki value pe filter hai", "HAVING " + re.sub(r"\s+", " ", " AND ".join(per_row)),
                "HAVING is for totals (SUM, COUNT…). After grouping, a per-row value like this is taken from one random row of the "
                "group. Put the condition in WHERE, written with the columns themselves.",
                "HAVING totals (SUM, COUNT…) ke liye hai. Group ke baad aisi value group ki kisi ek row se aati hai. Condition WHERE "
                "mein columns ke saath likho.")
    # ---- unknown ORDER BY etc. are handled above; aggregate-alias in ORDER BY dropped with grouping below

    # ---- rules learned from the engines' own docs and proved on MySQL / MariaDB / PostgreSQL (tools/knowledge)
    have = {f.topic + f.evidence for f in F}
    for r in learned_rules():
        m = r["_rx"].search(sql)
        if not m:
            continue
        ev = line_of(m.start())
        if any(ev in h for h in have):
            continue                                          # already said, in our own words
        # the engine's own words are the reason (proved); the drafted title and fix are only a pointer, with the example
        from app.agents.sqlbasics import CHOSEN
        from app.dbknow import engines as _E

        mine = {"postgresql": ["postgres"], "mysql": [_x for _x in ("mariadb", "mysql") if CHOSEN.get() == _x] or ["mysql", "mariadb"]}.get(
            _E.family(CHOSEN.get()) or "", [])
        shown = {k: e for k, e in r["engines"].items() if k in mine} or r["engines"]   # the person's database speaks first
        if mine and not set(mine) & set(r["engines"]):
            continue                                          # proven on other databases only: not said for this one
        said = "; ".join(f"{_ENGINE_NAME[k]} error {e['code']}: {e.get('message', '')}".strip() for k, e in shown.items())
        everywhere = set(r["engines"]) >= {"mysql", "mariadb", "postgres"}
        head = next(iter(shown.values())).get("message") or r["title"]
        title = f"{_ENGINE_NAME[next(iter(shown))]} refuses this: {head[:90]}"
        example = f" Works: {r['fixed_sql'][:160]}" if r.get("fixed_sql") else ""
        add("error" if everywhere else "check", "learned", title, title, ev, f"{said}.{example}", f"{said}.{example}")
    F = [f for f in F if not (f.title_en.startswith("CROSS JOIN") and f.title_en.split()[3].lower() in removed)]
    corrected = _apply(sql, s, cl, items, edits, notes, drop_grouping, sel_aliases, removed, srcs)
    if whole and (not corrected or re.sub(r"\s+", " ", corrected).strip() == re.sub(r"\s+", " ", sql).strip()):
        corrected = whole
    order = {"error": 0, "wrong": 1, "check": 2}
    F.sort(key=lambda f: order[f.kind])
    left = []
    if corrected and not _again:
        left = [f for f in check(corrected, _again=True)["findings"] if f["kind"] in ("error", "wrong")]
    return {"findings": [f.as_dict() for f in _dedupe(F)], "corrected": corrected, "guesses": list(dict.fromkeys(guesses)),
            "left": [{"title_en": f["title_en"], "title_hi": f["title_hi"]} for f in left]}


def _short(text: str, n: int = 32) -> str:
    t = re.sub(r"\s+", " ", text.strip().rstrip(";"))
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " …"


_ENGINE_NAME = {"mysql": "MySQL", "mariadb": "MariaDB", "postgres": "PostgreSQL"}
_LEARNED = None


# an engine message about the lab itself (a full disk, a server limit, a table the drafted example made up) says nothing about
# the query shape a learned rule detects: such a rule is not proven and is never shown (night 7: a 'recursive query with a
# JOIN' rule claimed PostgreSQL refuses every recursive walk, from a disk-full error)
_LAB_CODES = re.compile(r"^(53|57|58|08|1146$|42P01$|1049$|1045$|2002$|2003$|1040$)")


def _lab_trouble(engines: dict) -> bool:
    return all(_LAB_CODES.match(str(e.get("code", ""))) for e in engines.values())


def learned_rules() -> list:
    """Verified rules with a verified pattern, from app/knowledge/sql_rules/learned.json (read once)."""
    global _LEARNED
    if _LEARNED is None:
        import json
        from pathlib import Path

        out = []
        try:
            for r in json.loads((Path(__file__).resolve().parents[1] / "knowledge" / "sql_rules" / "learned.json").read_text()):
                if r.get("detect") and r.get("engines") and not _lab_trouble(r["engines"]):
                    try:
                        out.append({**r, "_rx": re.compile(r["detect"], re.I | re.S)})
                    except re.error:
                        pass
        except (OSError, ValueError):
            pass
        _LEARNED = out
    return _LEARNED


def _dedupe(F):
    seen, out = set(), []
    for f in F:
        k = (f.title_en, f.evidence)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def _same_cond(a: str, aa: str, b: str, ba: str, alias_of) -> bool:
    def norm(t, al):
        refs = sorted(f"{'@' if alias_of(q) == al.lower() else alias_of(q)}.{c.lower()}" for q, c, _p in _qrefs(t))
        return refs
    return norm(a, aa) == norm(b, ba)


def _line_start(sql: str, pos: int) -> int:
    a = sql.rfind("\n", 0, pos) + 1
    return a if not sql[a:pos].strip() else pos


def _cut_end(sql: str, end: int) -> int:
    """Through the trailing blank lines of a removed join, so no hole is left."""
    j = end
    while j < len(sql) and sql[j] in " \t\r\n":
        j += 1
    k = sql.rfind("\n", end, j)
    return k + 1 if k >= 0 else end


def _join_word_edit(sql: str, s: str, x: Source, new: str):
    m = JOIN_RE.match(s, x.start)
    return (x.start, m.end() if m else x.start, new)


def _edited(sql: str, start: int, end: int, edits=()) -> str:
    """The text between start and end with the edits inside it applied."""
    inner = sorted((e for e in edits if start <= e[0] and e[1] <= end and not (e[0] == e[1] == end)), key=lambda e: (e[0], e[1]))
    t, last = "", start
    for a, b, new in inner:
        if a < last:
            continue
        t += sql[last:a] + new
        last = b
    return t + sql[last:end]


def _expr(sql: str, item: dict, edits=()) -> str:
    t = re.sub(r"\x00\d+\x00", "", _edited(sql, item["start"], item["end"], edits))
    return re.sub(r"(?:\s+as)?\s+[`\"]?" + re.escape(item["alias"]) + r"[`\"]?\s*$", "", t, flags=re.I).strip()


def _apply(sql, s, cl, items, edits, notes, drop_grouping, sel_aliases, removed, srcs):
    if not edits and not drop_grouping and not notes:
        return None
    edits = list(edits)
    if drop_grouping:
        for i in items:
            if i["agg"]:
                a = s.rfind(",", 0, i["start"])
                edits.append((a if a >= 0 and s[a:i["start"]].strip() == "," else i["start"], i["end"], ""))
        g = cl["group by"]
        gk = re.search(r"\bgroup\s+by\b", s[:g[0]][::-1][::-1][-20:], re.I)
        edits.append((s.rfind("GROUP", 0, g[0]) if "GROUP" in s[:g[0]] else s.lower().rfind("group", 0, g[0]), g[1], ""))
        h = cl.get("having")
        if h:
            cond = sql[h[0]:h[1]].strip()
            for al, it in sel_aliases.items():
                if not it["agg"]:
                    cond = re.sub(rf"\b{re.escape(it['alias'])}\b", "(" + _expr(sql, it, edits) + ")", cond)
            hs = s.lower().rfind("having", 0, h[0])
            edits.append((hs, h[1], ""))
            w = cl.get("where")
            if w:
                end = len(sql[:w[1]].rstrip())
                edits.append((end, end, f"\n    AND {cond}"))
        o = cl.get("order by")
        if o:
            parts = []
            for off, txt in _split_top(s[o[0]:o[1]]):
                word = re.match(r"\s*[`\"]?(\w+)", txt)
                if word and word.group(1).lower() in sel_aliases and sel_aliases[word.group(1).lower()]["agg"]:
                    continue
                parts.append(_edited(sql, o[0] + off, o[0] + off + len(txt), edits))
            edits.append((o[0], o[1], " " + ",".join(p.rstrip() if k < len(parts) - 1 else p for k, p in enumerate(parts)).strip() + "\n"
                          if parts else ""))
    edits = [(a, b, re.sub(r"\x01(\w+)\x01", lambda m: "(" + _expr(sql, sel_aliases[m.group(1)], edits) + ")", t)) for a, b, t in edits]
    edits.sort(key=lambda e: (e[0], e[1]))
    out, last = [], 0
    for a, b, t in edits:
        if a < last and not (a == b and a >= last - 0):
            continue
        if a < last:
            a = last
        out.append(sql[last:a])
        out.append(t)
        last = b
    out.append(sql[last:])
    text = "".join(out)
    text = re.sub(r"\bORDER\s+BY\s*(?=[;\x00]|$)", "", text.rstrip(), flags=re.I).rstrip()
    if sql.rstrip().endswith(";") and not re.sub(r"\x00\d+\x00", "", text).rstrip().endswith(";"):
        text = re.sub(r"((?:\x00\d+\x00)*)\s*$", r";\1", text, count=1)
    # notes ('-- check: ...') at the end of the line each guessed name ended up on (after any ';')
    lines = []
    for ln in text.split("\n"):
        ids = [int(k) for k in re.findall(r"\x00(\d+)\x00", ln)]
        ln = re.sub(r"\x00\d+\x00", "", ln)
        if ids:
            code = ln.rstrip()
            ln = code + "   -- " + "; ".join(notes[k] for k in ids)
        lines.append(ln)
    text = "\n".join(lines)
    text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)
    text = re.sub(r",(\s*\n\s*\n?\s*FROM\b)", r"\1", text, flags=re.I)
    text = re.sub(r"\bORDER\s+BY\s*;", ";", text, flags=re.I)
    if "order by" in cl and drop_grouping and not re.search(r"\border\s+by\s+\S", text, re.I):
        text = re.sub(r"\bORDER\s+BY\s*$", "", text, flags=re.I)
    return text.strip()


def _shares(a: str, b: str) -> bool:
    wa = set(re.findall(r"\w+", a.lower()))
    wb = set(re.findall(r"\w+", b.lower()))
    return len(wa & wb) >= max(1, min(len(wb), 3) - 1)


def _in_engine_words(out: dict, engine: str) -> dict:
    """Rule A: when the person's database is known, findings speak its language. A finding written with MySQL's error
    numbers gets that database's own (the dialect file's 'wording', lab-checked), and a finding that doesn't apply there
    at all ('not_here', e.g. 'MySQL has no FULL OUTER JOIN' on PostgreSQL) is left out."""
    import json
    from pathlib import Path

    from app.dbknow import engines as E

    fam = E.family(engine)
    if fam != "postgresql":
        return out
    d = json.loads((Path(__file__).resolve().parents[1] / "dbknow" / "dialects" / f"{fam}.json").read_text(encoding="utf-8"))
    words = [(re.compile(w["re"]), w["to"]) for w in d.get("wording", [])]
    drop = [re.compile(x) for x in d.get("not_here", [])]
    kept = []
    for f in out.get("findings", []):
        if any(rx.search(f.get("title_en", "")) for rx in drop):
            continue
        for k in ("title_en", "title_hi", "fix_en", "fix_hi"):
            for rx, to in words:
                if f.get(k):
                    f[k] = rx.sub(to, f[k])
        kept.append(f)
    if len(kept) != len(out.get("findings", [])):
        out = {**out, "findings": kept}
        if not kept:
            out["corrected"] = None               # nothing left to fix on this database
    return out
