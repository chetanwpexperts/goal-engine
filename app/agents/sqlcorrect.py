"""Is the query RIGHT? Read before any speed advice: tables, aliases, joins and columns are worked out from the query
itself (no database needed), then checked for what makes MySQL refuse it or return wrong rows. Each finding says how
sure it is: 'error' (MySQL refuses to run it), 'wrong' (runs, but the rows are wrong), 'check' (probably a typo; only
your table structure can say). A corrected query is written with every guessed name marked for the person to check.
"""

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
        if head.startswith("("):
            close = _close(head, 0)
            name, rest = "", head[close + 1:]
        else:
            nm = re.match(r"[`\"]?([\w$]+(?:[`\"]?\.[`\"]?[\w$]+)?)[`\"]?", head)
            name, rest = (nm.group(1).replace("`", "").replace('"', ""), head[nm.end():]) if nm else ("", head)
        am = re.match(r"\s*(?:as\s+)?[`\"]?([A-Za-z_][\w$]*)[`\"]?", rest, re.I)
        alias = am.group(1) if am and am.group(1).lower() not in KW else (name.split(".")[-1] if name else "")
        src = Source(kind=kind, name=name, alias=alias, start=a + st, end=a + en)
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


def check(sql: str, _again: bool = False) -> dict:
    """Returns {'findings': [...], 'corrected': str|None, 'guesses': [...] }. Only plain SELECTs are read in depth."""
    s = _mask(sql)
    dep = _depths(s)
    low = s.lower()
    if not re.match(r"\s*(with\b|select\b|\()", low):
        return {"findings": [], "corrected": None, "guesses": []}
    cl = _clauses(s, dep)
    if "from" not in cl or "select" not in cl:
        return {"findings": [], "corrected": None, "guesses": []}
    srcs = _sources(s, dep, *cl["from"])
    if not srcs:
        return {"findings": [], "corrected": None, "guesses": []}
    F: list[Finding] = []
    edits: list[tuple[int, int, str]] = []          # (start, end, new text) on the original query
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
                      "end": sel_a + off + len(txt.rstrip()), "agg": bool(AGG.search(t)) and " over" not in t.lower()})
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
            add("wrong", "join", f"{x.name} {x.alias} is joined without ON", f"{x.name} {x.alias} bina ON ke joda hai",
                line_of(x.start), "Without ON every row is paired with every row of this table (rows multiply). Add ON with the matching columns.",
                "ON ke bina har row is table ki har row se judti hai (rows guna ho jaati hain). Matching columns ke saath ON lagao.")
        if x.on and a and a not in x.refs and i > 0:
            add("wrong", "join", f"The ON of {x.alias} doesn't mention {x.alias}", f"{x.alias} ke ON mein {x.alias} hai hi nahi",
                line_of(x.on_start), f"An ON condition must connect {x.alias} to a table before it; this one doesn't, so every {x.alias} row "
                "is paired with every row (rows multiply).",
                f"ON ko {x.alias} ko pichli table se jodna chahiye; ye nahi jodta, isliye rows guna ho jaati hain.")
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
        wtext = s[where[0]:where[1]]
        for m in WORD.finditer(wtext):
            w = m.group(1)
            if w.lower() in sel_aliases and not any(w.lower() == (x.alias or "").lower() for x in srcs) and \
                    not re.match(r"\s*\.", wtext[m.end():]) and not any(w.lower() == c.lower() for _q, c, _p in all_refs):
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
                    guesses.append(f"{q}.{c} -> {q}.id")
                    add("check", "column", f"{q}.{c} is probably {q}.id", f"{q}.{c} shayad {q}.id hai", line_of(p),
                        f"Every other table here is matched on its id, and {t.name} is the table {c} points to, so its own key is "
                        f"most likely id. Check your table: if {t.name} really has {c} as its key, keep it.",
                        f"Baaki sab tables id se judti hain, aur {c} isi {t.name} ki taraf ishara karta hai, isliye iski key shayad id hai. "
                        "Table check karo.")
                    cs = p + len(q) + 1
                    edits.append((cs, cs + len(c), "id"))
                    note(cs + len(c), f"check: id (you wrote {c})")
    # one alias, two names for what looks like one column: oi.price and oi.unit_price
    cols_by = {}
    for q, c, p in all_refs:
        cols_by.setdefault(alias_of(q), {}).setdefault(c.lower(), []).append((q, c, p))
    shown = {re.sub(r"\s+", "", i["text"]).lower() for i in items}
    for al, cols in cols_by.items():
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
                        add("check", "column", f"{w} has no table in front of it", f"{w} ke aage table ka naam nahi", line_of(pos),
                            f"With {len(srcs)} tables, write which table it's from (alias.{w}); if two tables have it, MySQL stops with error 1052.",
                            f"{len(srcs)} tables mein batao ye kis table ka hai (alias.{w}); warna error 1052 ho sakta hai.")
                elif clause in ("select", "where"):
                    add("check", "column", f"{w} has no table in front of it", f"{w} ke aage table ka naam nahi", line_of(pos),
                        f"With {len(srcs)} tables, write which table it's from (alias.{w}); if two tables have it, MySQL stops with error 1052.",
                        f"{len(srcs)} tables mein batao ye kis table ka hai (alias.{w}); warna error 1052 ho sakta hai.")

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
        for i in plain:
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
            if A in single:
                continue
            mine = to_one_from({A})
            others = [y for y in live if y not in single and y not in mine and y != A and A not in to_one_from({y})]
            if others:
                o = by_alias[others[0]]
                add("wrong", "group", f"{fm.group(1).upper()}({fm.group(3)}.{fm.group(4)}) is multiplied by the {o.name} rows",
                    f"{fm.group(1).upper()}({fm.group(3)}.{fm.group(4)}) {o.name} ki rows se guna ho jaata hai", i["text"],
                    f"{by_alias[A].name} and {o.name} are both many rows per group, so every {by_alias[A].name} row repeats once per "
                    f"{o.name} row and the {fm.group(1).upper()} comes out too big. Total {by_alias[A].name} in its own subquery "
                    f"(SELECT {fm.group(3)}.<key>, {fm.group(1).upper()}(...) ... GROUP BY ...) and join that.",
                    f"{by_alias[A].name} aur {o.name} dono ki ek group mein kai rows hain, isliye har row baar baar gini jaati hai aur "
                    f"{fm.group(1).upper()} bada aata hai. {by_alias[A].name} ka total alag subquery mein nikaal ke join karo.")
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
            if not AGG.search(part) and not any(w.lower() in totals for w in _bare(part, set())):
                per_row.append(re.sub(r"(?i)^\s*and\s+|\s+and\s*$", "", sql[having[0] + a1:having[0] + b1]).strip())
        if per_row:
            hv = _short(" AND ".join(per_row))
            add("wrong" if group else "check", "having", f"HAVING {hv} filters a per-row value",
                f"HAVING {hv} ek row ki value pe filter hai", "HAVING " + re.sub(r"\s+", " ", " AND ".join(per_row)),
                "HAVING is for totals (SUM, COUNT…). After grouping, a per-row value like this is taken from one random row of the "
                "group. Put the condition in WHERE, written with the columns themselves.",
                "HAVING totals (SUM, COUNT…) ke liye hai. Group ke baad aisi value group ki kisi ek row se aati hai. Condition WHERE "
                "mein columns ke saath likho.")
    # ---- unknown ORDER BY etc. are handled above; aggregate-alias in ORDER BY dropped with grouping below

    F = [f for f in F if not (f.title_en.startswith("CROSS JOIN") and f.title_en.split()[3].lower() in removed)]
    corrected = _apply(sql, s, cl, items, edits, notes, drop_grouping, sel_aliases, removed, srcs)
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
