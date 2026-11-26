"""Night S2: the query shapes the plain writer (app/agents/sqlwrite.py) doesn't build on its own.

Each shape reads the same Reader (the request in shared words + the schema) and returns the finished SQL, or None when the
request isn't that shape. Shared by every database (rule B); only syntax that really differs is chosen per engine (rule A).

  self      a row compared with its own manager / parent row      (self join on the column that points at the same table)
  pairs     pairs of rows sharing values, each pair once           (self join with a.key < b.key)
  nth       the Nth highest / lowest value, overall or per group   (correlated COUNT(DISTINCT ...), no window functions)
  top_per   the top N rows of each group                           (correlated COUNT, ties kept)
  group_avg groups whose average is above / below the overall one  (HAVING with a subquery)
  percent   each group's share of a total, or of the rows          (100.0 * ... / NULLIF(total, 0), rounded)
  window    ranks, running totals, the previous / next row         (window functions; ranks also without them)
  change    UPDATE / DELETE with a WHERE, deleting duplicates      (never without a WHERE; a preview to run first)
"""

from __future__ import annotations

import re

from app.agents import sqlwrite as W
from app.agents.sqlwrite import CantBuild, Col, Reader, Table

ROLE = r"(manager|boss|supervisor|parent|mentor|referrer|team lead|lead|reporting manager)"
ENTITY = set("""department dept team class section branch store warehouse category project company group division batch course
customer product supplier vendor city country region manager doctor teacher student shop office""".split())
ORD = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
HIGH = ("highest", "largest", "biggest", "most", "top", "maximum", "max", "greatest", "best", "latest", "newest", "most expensive",
        "costliest")
LOW = ("lowest", "smallest", "least", "minimum", "min", "earliest", "oldest", "cheapest", "worst")
GROUP_RE = r"\b(?:in each|for each|per|in every|for every|of each|of every|within each|within every|within|each|every)\s+(?:the\s+)?([a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)?)"
GROUP_BY_RE = GROUP_RE.replace("|every)", "|every|by|grouped by|wise)")


def build(r: Reader) -> str | None:
    import copy

    low = r.low
    for shape in (_change, _self, _pairs, _nth, _top_per, _group_avg, _percent, _window):
        saved = copy.deepcopy((r.schema, r.spec))
        sql = shape(r, low)
        if sql:
            return sql
        r.schema, r.spec = saved                      # a shape that didn't fit leaves nothing behind (no stray guesses)
    return None


# ------------------------------------------------------------------------------------------------ helpers
def _table(r: Reader, want: str | None = None) -> Table:
    """The table the request reads. want: a column phrase; when the named table doesn't have it and another one does
    ('the 3rd highest pay in each team': pay lives in the staff table, not in teams), that one."""
    found = r.tables()
    t = W._main_table(r, found)
    r.spec.table = t
    if r.inferred:
        W._infer_columns(r)
    elif want:
        want = _clean(want)
        def has(x, w=None):
            hit = r.col(w or want, x)
            return bool(hit and hit[0] is x)

        def has_any(w):
            return any(has(x, w) for x in r.schema.values())
        if want and not has(t) and " " in want and has_any(want.split()[0]):
            want = want.split()[0]
        if want and not has(t):
            owners = [x for _p, x in found if x is not t and has(x)] or [x for x in r.schema.values() if x is not t and has(x)]
            if len(owners) == 1:
                t = owners[0]
                r.spec.table = t
    return t


def _sing(t: Table) -> str:
    return W._short(t)


def _guess(r: Reader, t: Table, name: str, kind: str) -> Col:
    if t.col(name):
        return t.col(name)
    if r.inferred or t.guessed:
        return r.guess_col(name, kind, t)
    raise CantBuild(f"which column of {t.name} holds the {name.replace('_id', '').replace('_', ' ')}")


def _pk(r: Reader, t: Table) -> Col:
    if t.pk and t.col(t.pk):
        return t.col(t.pk)
    for n in ("id", f"{_sing(t)}_id"):
        if t.col(n):
            return t.col(n)
    return _guess(r, t, f"{_sing(t)}_id", "id")


def _name_col(r: Reader, t: Table, need: bool = False) -> Col | None:
    texts = [c for c in t.cols.values() if c.kind == "text"]
    for test in (lambda n: n == "name", lambda n: n == f"{_sing(t)}_name", lambda n: n in ("full_name", "title"),
                 lambda n: n.endswith("_name") and not n.startswith(("first", "last", "middle", "user", "nick")),
                 lambda n: n == "first_name", lambda n: "name" in n and "middle" not in n):
        for c in texts:
            if test(c.name.lower()):
                return c
    if r.inferred or t.guessed:
        return r.guess_col("name", "text", t)
    if need:
        raise CantBuild(f"which column of {t.name} holds the name")
    return None


def _clean(phrase: str) -> str:
    p = re.sub(r"\b(?:the|exact|exactly|same|their|his|her|its|a|an|of|is|are|was|were|have|has|having|with|who|that)\b", " ", phrase)
    return re.sub(r"\s+", " ", p).strip()


def _word_col(r: Reader, t: Table, phrase: str, kind: str | None = None) -> Col | None:
    """The column of t a word names: the column itself, or the column that points at the table the word names
    ('same department' -> department_id)."""
    phrase = _clean(phrase or "")
    if not phrase:
        return None
    words = phrase.split()
    tries = [" ".join(words[:k]) for k in range(len(words), 0, -1)] + [" ".join(words[-k:]) for k in range(len(words) - 1, 0, -1)]
    for p in tries:
        hit = r.col(p, t, kind)
        if hit and hit[0] is t:
            return hit[1]
    for p in tries:
        for other in r.schema.values():
            if other is t or p not in other.forms():
                continue
            for col, ptab, _pc in t.fks:
                if ptab.lower() == other.name.lower():
                    return t.col(col)
            sing = W._short(other)
            for n in (f"{sing}_id", f"{p}_id"):
                if t.col(n):
                    return t.col(n)
    for p in tries:
        for n in (f"{p.replace(' ', '_')}_id", p.replace(" ", "_")):
            if t.col(n) and (kind is None or t.col(n).kind == kind or t.col(n).kind == "id"):
                return t.col(n)
    if r.inferred or t.guessed:
        p = words[-1] if len(words) > 1 and words[-1] in ENTITY else words[0] if words[0] in ENTITY else "_".join(words)
        if p in ("salary", "pay", "earning", "earnings", "wage", "income"):
            return r.money_col(t)
        if re.fullmatch(r"[a-z][a-z0-9_]*", p) and p not in W.words()["not_tables"]:
            return r.guess_col(f"{p}_id" if p in ENTITY else p, "id" if p in ENTITY else W._kind(p), t)
    return None


def _num_col(r: Reader, t: Table, near: str) -> Col:
    """The amount a phrase names: a column it says, else the money column (earn / paid / salary ...)."""
    c = _word_col(r, t, near, "num") if near and not re.search(r"\b(?:paid|earning|earners?|priced|expensive|costliest|cheap\w*)\b",
                                                                near) else None
    if c is not None and c.kind in ("num", "id"):
        return c
    for a, _b, mt, mc in r.mentions():
        if mt is t and mc.kind == "num" and r.low[a:a + len(mc.name)] and mc.name.lower() in near:
            return mc
    return r.money_col(t)


def _date_col(r: Reader, t: Table, hint: str = "") -> Col:
    dates = [c for c in t.cols.values() if c.kind == "date"]
    for want in (r"hire|hired|join", r"birth|dob", r"order|placed", r"created|added|date"):
        if re.search(want, hint):
            for c in dates:
                if re.search(want, c.name.lower()):
                    return c
    if dates:
        return r.date_col(t)
    if r.inferred or t.guessed:
        name = "hire_date" if re.search(r"hire|hired|join", hint) else "created_at"
        return r.guess_col(name, "date", t)
    return r.date_col(t)


def _group(r: Reader, t: Table, low: str, skip: tuple = (), by: bool = False, not_col: Col | None = None) -> Col | None:
    """'in each department' / 'per category' / 'har city': the column the rows are grouped by ('by region' too when by)."""
    for m in re.finditer(GROUP_BY_RE if by else GROUP_RE, low):
        word = m.group(1)
        if word.split()[0] in ("row", "rows", "one", "pair", "the", "time", "other", "of") or word in skip:
            continue
        if word in t.forms() or (word.split()[0] in t.forms() and not _word_col_soft(r, t, word)):
            continue                                   # 'each employee': the rows themselves, not a group
        c = _word_col(r, t, word)
        if c is not None and c is not not_col:
            return c
    return None


def _extra_where(r: Reader, t: Table, alias: str, skip_spans=()) -> list[str]:
    """Plain conditions said alongside the shape ('... in department 3', '... whose status is active'), on t's alias."""
    r.spec.table = t
    keep_where, keep_steps = list(r.spec.where), list(r.spec.steps)
    r.spec.where = []
    ments = [m for m in r.mentions() if m[2] is t and not any(a <= m[0] < b for a, b in skip_spans)]
    used = set()
    try:
        W._filters(r, ments, used)
    except CantBuild:
        pass
    conds = [w for w in r.spec.where if isinstance(w, str)]
    r.spec.where = keep_where
    r.spec.steps = keep_steps + (["WHERE keeps only the rows that pass the other conditions you gave"] if conds else [])
    out = []
    for cnd in conds:
        parts = re.split(r"('(?:[^']|'')*')", cnd)
        for i in range(0, len(parts), 2):
            for c in sorted(t.cols.values(), key=lambda x: -len(x.name)):
                if alias:
                    parts[i] = re.sub(rf"(?<![\w.]){re.escape(c.name)}(?![\w])", f"{alias}.{c.name}", parts[i])
        out.append("".join(parts))
    return out


def _lines(*parts) -> str:
    return "\n".join(p for p in parts if p)


def _where(conds: list[str]) -> str:
    conds = [c for c in conds if c]
    return ("WHERE " + "\n  AND ".join(conds)) if conds else ""


def _top(r: Reader, n: int) -> tuple[str, str]:
    """(head after SELECT, tail) for 'the first n rows' in the person's engine."""
    eng = (r.engine or "").lower()
    if eng == "sqlserver":
        return f"TOP ({n}) ", ""
    if eng == "oracle":
        return "", f"FETCH FIRST {n} ROWS ONLY"
    return "", f"LIMIT {n}"


def _finish(r: Reader, sql: str) -> str:
    if r.spec.guesses:
        sql += "\n-- check: " + "; ".join(dict.fromkeys(r.spec.guesses)) + " (names guessed from your words)"
    return sql


# ------------------------------------------------------------------------------------------------ a row and its manager row
def _self_ref(r: Reader, t: Table, role: str) -> Col:
    for col, ptab, _pc in t.fks:
        if ptab.lower() == t.name.lower():
            return t.col(col)
    role_w = role.replace(" ", "_")
    names = [f"{role_w}_id", f"{role_w[:3]}_id", "mgr_id", "manager_id", "parent_id", "boss_id", "supervisor_id", "mentor_id",
             "reports_to", "referred_by", "referrer_id", f"parent_{_sing(t)}_id", f"{role_w}"]
    for n in names:
        if t.col(n):
            return t.col(n)
    return _guess(r, t, f"{role_w}_id", "id")


def _self(r: Reader, low: str) -> str | None:
    m = re.search(rf"\b(?:their|his|her|its|the|own)\s+(?:direct\s+|immediate\s+)?{ROLE}s?\b|\b{ROLE}'s\b|\breports? to\b|"
                  rf"\b(?:who|that)\s+(?:are|is)\s+(?:a\s+)?(?:manager|boss|supervisor)s?\b|\bwho manage\b|\bdirect reports\b|"
                  rf"\b(?:no|without(?: a| any)?)\s+{ROLE}\b|\b{ROLE}\s+(?:of|name|naam)\b", low)
    if not m:
        return None
    role = next((g for g in m.groups() if g), "manager")
    role = re.sub(r"\W+", "_", role)
    t = _table(r)
    s = r.spec
    ref = _self_ref(r, t, role)
    pk = _pk(r, t)
    e, mm = ("e", "m") if role in ("manager", "boss", "supervisor") else ("c", "p")
    spans = [(m.start(), m.end())]

    # ---- the ones without a manager / the managers themselves / how many report to each
    if re.search(rf"\b(?:no|without(?: a| any)?|not having a)\s+(?:direct\s+)?{ROLE}\b|\btop[- ]level\b", low) and \
            not re.search(r"\b(?:including|include|even|also|as well)\b", low):
        conds = [f"{e}.{ref.name} IS NULL"] + _extra_where(r, t, e, spans)
        s.steps.append(f"{ref.name} IS NULL: the rows that point at no {role}")
        return _finish(r, _lines(f"SELECT {e}.*", f"FROM {t.name} {e}", _where(conds)))
    count_m = re.search(r"\b(?:how many|number of|count of|count)\b[^.]{0,40}\b(?:report|reports|reporting|direct reports|team|"
                        r"subordinates|juniors|children|employees under)\b|\b(?:team size|reportee)", low)
    if count_m:
        name = _name_col(r, t)
        keep = W._keep_all(low)
        cols = [f"{mm}.{pk.name}"] + ([f"{mm}.{name.name}"] if name else [])
        s.steps.append(f"each {role} row joined to the rows whose {ref.name} points at it; COUNT counts them per {role}")
        if keep:
            s.steps.append("LEFT JOIN keeps the rows with nobody under them, with a count of 0")
        if keep:                                       # every row, 0 for the ones nobody reports to
            return _finish(r, _lines(f"SELECT {', '.join(cols)}, (SELECT COUNT(*) FROM {t.name} {e} WHERE {e}.{ref.name} = "
                                     f"{mm}.{pk.name}) AS direct_reports", f"FROM {t.name} {mm}", "ORDER BY direct_reports DESC"))
        return _finish(r, _lines(f"SELECT {', '.join(cols)}, COUNT(*) AS direct_reports",
                                 f"FROM {t.name} {e}",
                                 f"JOIN {t.name} {mm} ON {e}.{ref.name} = {mm}.{pk.name}",
                                 "GROUP BY " + ", ".join(cols),
                                 "ORDER BY direct_reports DESC"))
    if re.search(r"\b(?:who|that)\s+(?:are|is)\s+(?:a\s+)?(?:manager|boss|supervisor)s?\b|\bwho manage\b|\bhave (?:any )?(?:direct )?reports\b", low):
        conds = [f"EXISTS (SELECT 1 FROM {t.name} x WHERE x.{ref.name} = {mm}.{pk.name})"] + _extra_where(r, t, mm, spans)
        s.steps.append(f"EXISTS keeps a row only when some other row's {ref.name} points at it")
        return _finish(r, _lines(f"SELECT {mm}.*", f"FROM {t.name} {mm}", _where(conds)))

    # ---- comparisons with the manager row
    comps = []
    cmp_re = (rf"(?:\b([a-z][a-z0-9_ ]{{0,30}}?)\s+)?(?:is\s+|are\s+|was\s+|were\s+)?"
              rf"(more|less|higher|lower|greater|bigger|smaller|earlier|later|before|after|above|below)\s+(?:than\s+)?"
              rf"(?:that of\s+)?(?:their|his|her|its|the)\s+(?:own\s+)?(?:direct\s+|immediate\s+)?{ROLE}(?:'s)?(?:\s+([a-z_]+))?")
    for cm in re.finditer(cmp_re, low):
        word, op_w = (cm.group(1) or "").strip(), cm.group(2)
        spans.append((cm.start(), cm.end()))
        before = word.split()[-3:] if word else []
        phrase = " ".join(before)
        exact = next((c for k in (1, 2, 3) if len(before) >= k for c in [_word_col_soft(r, t, " ".join(before[-k:]))] if c), None)
        op = ">" if op_w in ("more", "higher", "greater", "bigger", "later", "after", "above") else "<"
        if exact is not None:
            col = exact
        elif op_w in ("before", "after", "earlier", "later") or re.search(r"\b(?:hired|joined|started|born|created|registered)\b", phrase):
            col = _date_col(r, t, phrase)
        elif re.search(r"\b(?:earn\w*|paid|make|makes|get paid|salary|salaries)\b", phrase):
            col = r.money_col(t)
        else:
            col = _word_col(r, t, phrase) or _word_col(r, t, cm.group(4) or "") or r.money_col(t)
        comps.append((col, op, op_w))
    for cm in re.finditer(rf"\b(?:the\s+)?(same|different)\s+([a-z][a-z0-9_ ]{{0,30}}?)\s+(?:as|from|than)\s+(?:their|his|her|its|the)\s+"
                          rf"(?:direct\s+)?{ROLE}\b", low):
        col = _word_col(r, t, cm.group(2))
        if col is None:
            raise CantBuild(f"which column '{cm.group(2)}' is")
        comps.append((col, "=" if cm.group(1) == "same" else "<>", cm.group(1)))
        spans.append((cm.start(), cm.end()))
    if comps:
        conds = [f"{e}.{c.name} {op} {mm}.{c.name}" for c, op, _w in comps] + _extra_where(r, t, e, spans)
        shown = [f"{e}.*"] + [f"{mm}.{c.name} AS {role}_{c.name}" for c, _o, _w in comps]
        name = _name_col(r, t)
        if name:
            shown.insert(1, f"{mm}.{name.name} AS {role}_{name.name}")
        s.steps.append(f"the table joined to itself: {e} is each row, {mm} the row its {ref.name} points to (its {role})")
        s.steps.append(f"WHERE compares each row with its own {role}: " +
                       ", ".join(f"{e}.{c.name} {op} {mm}.{c.name}" for c, op, _w in comps))
        s.steps.append(f"rows with no {role} drop out, because there is nothing to compare with")
        return _finish(r, _lines("SELECT " + ", ".join(dict.fromkeys(shown)), f"FROM {t.name} {e}",
                                 f"JOIN {t.name} {mm} ON {e}.{ref.name} = {mm}.{pk.name}", _where(conds)))

    # ---- each row with (some column of) its manager row
    after = low[m.end():m.end() + 40]
    role_col = None
    am = re.match(r"\s*(?:'s\s+)?(?:of\s+)?([a-z][a-z0-9_ ]{0,25})", after)
    if am:
        role_col = _word_col(r, t, " ".join(am.group(1).split()[:2]))
        if role_col is not None and role_col.kind == "id" and role_col is ref:
            role_col = None
    role_col = role_col or _name_col(r, t, need=True)
    mine = []
    for a, _b, mt, mc in r.mentions():
        if mt is t and a < m.start() and mc is not ref and mc is not role_col:
            mine.append(f"{e}.{mc.name}")
    if not mine:
        name = _name_col(r, t)
        mine = [f"{e}.{name.name}"] if name else [f"{e}.*"]
    if role_col is not None and f"{e}.{role_col.name}" not in mine and role_col.kind == "text":
        mine.insert(0, f"{e}.{role_col.name}")
    keep = W._keep_all(low) or re.search(r"\b(?:each|every|all)\b", low) or re.search(r"\b(?:null|no " + role + r")\b", low)
    s.steps.append(f"the table joined to itself: {e} is each row, {mm} the row its {ref.name} points to (its {role})")
    s.steps.append(f"LEFT JOIN keeps the rows with no {role} too ({role} columns NULL)" if keep else
                   f"JOIN keeps only rows that have a {role}; say 'including those without one' to keep them all")
    conds = _extra_where(r, t, e, spans + [(m.start(), m.end() + 40)])
    return _finish(r, _lines("SELECT " + ", ".join(dict.fromkeys(mine + [f"{mm}.{role_col.name} AS {role}_{role_col.name}"])),
                             f"FROM {t.name} {e}",
                             f"{'LEFT JOIN' if keep else 'JOIN'} {t.name} {mm} ON {e}.{ref.name} = {mm}.{pk.name}", _where(conds)))


# ------------------------------------------------------------------------------------------------ pairs of rows
def _pairs(r: Reader, low: str) -> str | None:
    if not re.search(r"\bpairs?\b|\beach other\b|\bjode\b|\bjodi\b", low):
        return None
    sames = list(re.finditer(r"\b(?:the\s+)?(?:exact\s+|exactly\s+the\s+)?same\s+([a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)?)", low))
    if not sames:
        return None
    t = _table(r)
    s = r.spec
    cols = []
    for sm in sames:
        words = sm.group(1).split()
        if len(words) > 1 and words[1] in ("as", "and", "who", "with", "in", "of", "from", "without", "but", "or"):
            words = words[:1]
        c = _word_col(r, t, " ".join(words))
        if c is None:
            raise CantBuild(f"which column '{' '.join(words)}' is")
        if c not in cols:
            cols.append(c)
    pk = _pk(r, t)
    name = _name_col(r, t)
    show = name or pk
    on = [f"a.{c.name} = b.{c.name}" for c in cols] + [f"a.{pk.name} < b.{pk.name}"]
    sel = [f"a.{show.name} AS {show.name}_1", f"b.{show.name} AS {show.name}_2"] + [f"a.{c.name}" for c in cols]
    s.steps.append("the table joined to itself (a and b): two rows pair up when " + " and ".join(f"{c.name} matches" for c in cols))
    s.steps.append(f"a.{pk.name} < b.{pk.name} keeps each pair once (no (B, A) after (A, B)) and never pairs a row with itself")
    s.steps.append("rows with NULL in those columns never pair (NULL = NULL is not true)")
    conds = _extra_where(r, t, "a", [(x.start(), x.end()) for x in sames])
    return _finish(r, _lines("SELECT " + ", ".join(dict.fromkeys(sel)), f"FROM {t.name} a",
                             f"JOIN {t.name} b ON " + "\n  AND ".join(on), _where(conds),
                             f"ORDER BY a.{show.name}, b.{show.name}"))


# ------------------------------------------------------------------------------------------------ the Nth highest
NTH_RE = (r"\b(?:(\d+)\s*(?:st|nd|rd|th)|(second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth))\s+"
          r"(?:most expensive|highest|largest|biggest|most|top|maximum|max|greatest|best|latest|newest|lowest|smallest|least|minimum|"
          r"min|earliest|oldest|cheapest)\b")


def _nth(r: Reader, low: str) -> str | None:
    m = re.search(NTH_RE, low)
    if not m:
        return None
    n = int(m.group(1)) if m.group(1) else ORD[m.group(2)]
    word = re.search(r"(most expensive|highest|largest|biggest|most|top|maximum|max|greatest|best|latest|newest|lowest|smallest|"
                     r"least|minimum|min|earliest|oldest|cheapest)\b", low[m.start():]).group(1)
    high = word in HIGH
    after = low[m.end():m.end() + 40]
    t = _table(r, re.sub(r"^\s*(?:distinct|unique|different)\s+", "", after).split(" in ")[0].split(" per ")[0])
    s = r.spec
    after = re.sub(r"^\s*(?:distinct|unique|different)\s+", " ", after)
    if word in ("latest", "newest", "earliest", "oldest"):
        v = _date_col(r, t, after)
    else:
        am = re.match(r"\s*([a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)?)", after)
        phrase = am.group(1) if am else ""
        v = None
        if phrase and not re.match(r"(?:paid|earning|earners?|priced)\b", phrase) and not any(phrase.split()[0] in x.forms()
                                                                                            for x in r.schema.values()):
            v = _word_col(r, t, phrase)
        v = v if v is not None and v.kind in ("num", "date") else r.money_col(t)
    g = _group(r, t, low, not_col=v)
    rows = bool(re.search(r"\b(?:who|whose|that|which|with|having|earn\w*|paid|gets?|makes?|has|have)\s+(?:the\s+|a\s+)?$",
                          low[max(0, m.start() - 25):m.start()])) or \
        _rows_after(t, after)
    op = ">" if high else "<"
    inner = [f"t2.{v.name} {op} t.{v.name}"] + ([f"t2.{g.name} = t.{g.name}"] if g else [])
    sub = f"(SELECT COUNT(DISTINCT t2.{v.name}) FROM {t.name} t2 WHERE {' AND '.join(inner)}) = {n - 1}"
    conds = [f"t.{v.name} IS NOT NULL"] + ([f"t.{g.name} IS NOT NULL"] if g else []) + [sub]
    conds += _extra_where(r, t, "t", [(m.start(), m.end() + 40)])
    which = "highest" if high else "lowest"
    s.steps.append(f"for each row, the subquery counts the distinct {v.name} values {'above' if high else 'below'} it"
                   + (f" in the same {g.name}" if g else "") + f"; exactly {n - 1} of them means it is the {n}th {which}")
    s.steps.append("no window functions and no LIMIT/OFFSET, so it runs on every database; ties share the place")
    if rows:
        sel = "t.*"
        order = f"ORDER BY {'t.' + g.name + ', ' if g else ''}t.{v.name}"
    else:
        sel = "DISTINCT " + (f"t.{g.name}, " if g else "") + f"t.{v.name}"
        order = f"ORDER BY t.{g.name}" if g else ""
    return _finish(r, _lines(f"SELECT {sel}", f"FROM {t.name} t", _where(conds), order))


# ------------------------------------------------------------------------------------------------ the top N of each group
def _rows_after(t: Table, after: str) -> bool:
    """'the 3rd lowest room no classes': the table's name follows the value (not 'of / from / in classes')."""
    toks = re.findall(r"[a-z_]+", after)[:5]
    for i, w in enumerate(toks):
        if w in t.forms() and (i == 0 or toks[i - 1] not in ("of", "from", "in", "among")):
            return True
        if w in ("of", "from", "in", "for", "per", "each", "among"):
            return False
    return False


def _top_per(r: Reader, low: str) -> str | None:
    m = re.search(r"\b(?:top|first|best|bottom|last)\s+(\d+)\b|\b(\d+)\s+(?:most expensive|highest|lowest|most|least|largest|smallest|"
                  r"biggest|best|newest|latest|oldest|earliest|top|cheapest|costliest)\b", low)
    if not m:
        return None
    n = int(m.group(1) or m.group(2))
    bm = re.search(r"\b(?:by|on|according to|in terms of)\s+(?:the\s+)?(?:highest\s+|lowest\s+)?([a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)?)", low)
    if bm and re.match(r"(?:each|every|the|per)\b", bm.group(1)):
        bm = None
    t = _table(r, bm.group(1).split(" in ")[0] if bm else None)
    s = r.spec
    v = None
    if bm:
        v = _word_col(r, t, bm.group(1).split(" in ")[0])
        v = v if v is not None and v.kind in ("num", "date") else None
    g = _group(r, t, low, not_col=v)
    if g is None:
        return None
    tail = low[m.start():m.start() + 60]
    sup = re.search(r"\b(most expensive|costliest|highest|lowest|most|least|largest|smallest|biggest|best|newest|latest|oldest|"
                    r"earliest|cheapest|top|bottom|first|last)\b", tail)
    word = sup.group(1) if sup else "top"
    if v is None:
        if word in ("newest", "latest", "oldest", "earliest"):
            v = _date_col(r, t, tail)
        else:
            nm = re.match(rf"\s*(?:{word})\s+(?:\d+\s+)?(?:paid\s+|earning\s+|priced\s+)?([a-z][a-z0-9_]*)", low[sup.start():] if sup else "")
            cand = _word_col(r, t, nm.group(1)) if nm and nm.group(1) not in t.forms() else None
            v = cand if cand is not None and cand.kind in ("num", "date") else r.money_col(t)
    desc = word not in ("lowest", "least", "smallest", "oldest", "earliest", "cheapest", "bottom")
    if re.search(r"\b(?:ascending|asc|low to high|lowest first)\b", low):
        desc = False
    op = ">" if desc else "<"
    sub = (f"(SELECT COUNT(*) FROM {t.name} t2 WHERE t2.{g.name} = t.{g.name} AND t2.{v.name} {op} t.{v.name}) < {n}")
    conds = [f"t.{g.name} IS NOT NULL", f"t.{v.name} IS NOT NULL", sub] + _extra_where(r, t, "t", [(m.start(), m.end() + 60)])
    s.steps.append(f"for each row, the subquery counts the rows of the same {g.name} with a {'bigger' if desc else 'smaller'} "
                   f"{v.name}; fewer than {n} means it is in that {g.name}'s top {n}")
    s.steps.append("rows tied on the last place are all kept; no window functions, so it runs on every database")
    return _finish(r, _lines("SELECT t.*", f"FROM {t.name} t", _where(conds),
                             f"ORDER BY t.{g.name}, t.{v.name} {'DESC' if desc else 'ASC'}"))


# ------------------------------------------------------------------------------------------------ groups vs the overall average
def _group_avg(r: Reader, low: str) -> str | None:
    m = re.search(r"\b([a-z][a-z0-9_]*)(?:\s+(?:show|list|find|get|display))?\s+(?:whose|where|with|having|that have|which have|where the)\s+(?:an?\s+|the\s+|their\s+)?"
                  r"(average|avg|mean)\s+(?:of\s+)?([a-z][a-z0-9_]*(?: (?!overall\b|company\b|is\b|are\b)[a-z][a-z0-9_]*)?)\s+(?:is\s+|are\s+)?(?:overall\s+|company\s+)?"
                  r"(higher|more|greater|above|bigger|over|lower|less|below|smaller|under)\s+(?:than\s+)?(?:the\s+)?"
                  r"(?:overall|company|company-wide|whole|total|entire|all|global|full)?[\w -]{0,20}?(?:average|avg|mean)\b", low)
    if not m:
        return None
    s = r.spec
    found = r.tables()
    word, vw = m.group(1), m.group(3)
    op = ">" if m.group(4) in ("higher", "more", "greater", "above", "bigger", "over") else "<"
    grp_tab = next((t for _p, t in found if word in t.forms()), None)
    if r.inferred:
        r.spec.table = W._main_table(r, found)
        W._infer_columns(r)
    if grp_tab is not None:
        # the groups are rows of another table ('departments whose average salary ...'): the amount lives in a child table
        owner = next((t for t in r.schema.values() if t is not grp_tab and _word_col_soft(r, t, vw)), None)
        if owner is None:
            if _word_col_soft(r, grp_tab, vw):
                raise CantBuild(f"what the groups of {grp_tab.name} are (say 'per ...')")
            raise CantBuild(f"which table holds the {vw} (paste the CREATE TABLE of both tables)")
        v = _word_col_soft(r, owner, vw)
        r.spec.table = grp_tab
        child, cc, parent, pc = W._fk_between(r, grp_tab, owner)
        if parent is not grp_tab:
            raise CantBuild(f"how {owner.name} points at {grp_tab.name}")
        name = _name_col(r, grp_tab)
        keys = [f"g.{pc.name}"] + ([f"g.{name.name}"] if name else [])
        s.steps.append(f"JOIN {owner.name} to {grp_tab.name}; GROUP BY makes one row per {W._short(grp_tab)}")
        s.steps.append(f"HAVING keeps the groups whose AVG({v.name}) is {'above' if op == '>' else 'below'} the average over the "
                       f"whole {owner.name} table (the subquery)")
        return _finish(r, _lines(f"SELECT {', '.join(keys)}, AVG(x.{v.name}) AS avg_{v.name}", f"FROM {grp_tab.name} g",
                                 f"JOIN {owner.name} x ON x.{cc.name} = g.{pc.name}", "GROUP BY " + ", ".join(keys),
                                 f"HAVING AVG(x.{v.name}) {op} (SELECT AVG({v.name}) FROM {owner.name})"))
    t = r.spec.table or W._main_table(r, found)
    r.spec.table = t
    g = _word_col(r, t, word)
    v = _word_col(r, t, vw)
    if g is None or v is None:
        return None
    if v.kind not in ("num",):
        v = r.money_col(t)
    s.steps.append(f"GROUP BY {g.name}: one row per {g.name}; HAVING compares each group's average {v.name} with the average "
                   f"over the whole table (the subquery)")
    return _finish(r, _lines(f"SELECT {g.name}, AVG({v.name}) AS avg_{v.name}", f"FROM {t.name}", f"GROUP BY {g.name}",
                             f"HAVING AVG({v.name}) {op} (SELECT AVG({v.name}) FROM {t.name})"))


def _word_col_soft(r: Reader, t: Table, phrase: str) -> Col | None:
    hit = r.col(_clean(phrase), t)
    if hit and hit[0] is t:
        return hit[1]
    if re.search(r"\b(?:salary|pay|earning|amount|price|cost|wage|income)\b", phrase):
        for w in W.words()["money_words"]:
            for c in t.cols.values():
                if c.kind == "num" and w in c.name.lower():
                    return c
    return None


# ------------------------------------------------------------------------------------------------ percentages
def _pct(r: Reader, expr: str, total: str, places: int) -> str:
    return f"ROUND(100.0 * CAST({expr} AS DECIMAL(20,6)) / NULLIF(CAST({total} AS DECIMAL(20,6)), 0), {places})"


def _percent(r: Reader, low: str) -> str | None:
    if not re.search(r"\b(?:percent(?:age)?s?|share|proportion|ratio)\b|%", low):
        return None
    am_ = re.search(r"\b(?:total|of|share of)\s+(?:the\s+)?(?:total\s+)?([a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)?)", low)
    t = _table(r, am_.group(1) if am_ else None)
    s = r.spec
    pm = re.search(r"\b(?:rounded|round(?:ed)?\s+(?:off\s+)?)\s+to\s+(\d+)\s+decimal", low)
    places = int(pm.group(1)) if pm else 2
    # what the total is: a sum of an amount ('total payroll', 'of total sales', 'share of revenue') or the rows
    amount = None
    am = re.search(r"\b(?:of\s+(?:the\s+)?(?:total|overall|all|whole|company)?\s*|share of\s+(?:the\s+)?(?:total\s+)?|total\s+)"
                   r"([a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)?)", low)
    if am:
        phrase = am.group(1)
        if re.match(r"(?:payroll|salar|pay|wage|revenue|sales|spend|spending|amount|cost|earnings|income|value|money)", phrase):
            amount = r.money_col(t)
        else:
            c = _word_col_soft(r, t, phrase) or _word_col_soft(r, t, phrase.split()[0])
            if c is not None and c.kind == "num":
                amount = c
    g = _group(r, t, low, by=True, not_col=amount)
    if g is None:
        cm = re.search(r"\bconsumed by\s+(?:each\s+)?([a-z][a-z0-9_]*)|\b(?:of|for)\s+each\s+([a-z][a-z0-9_]*)", low)
        if cm:
            g = _word_col(r, t, cm.group(1) or cm.group(2))
    total = f"(SELECT SUM({amount.name}) FROM {t.name})" if amount else f"(SELECT COUNT(*) FROM {t.name})"
    part = f"SUM({amount.name})" if amount else "COUNT(*)"
    if g is not None:
        s.steps.append(f"GROUP BY {g.name}: one row per {g.name}; {part} is that group's part")
        s.steps.append(f"the subquery is the whole table's {'SUM(' + amount.name + ')' if amount else 'row count'}; 100.0 (not 100) "
                       f"keeps the division decimal, so PostgreSQL and SQLite don't cut it to a whole number")
        s.steps.append(f"CAST(... AS DECIMAL) keeps it exact on every database (PostgreSQL's ROUND needs a decimal); NULLIF(..., 0) "
                       f"avoids dividing by zero; ROUND gives {places} decimal places")
        return _finish(r, _lines(f"SELECT {g.name}, {part} AS part, {_pct(r, part, total, places)} AS pct_of_total",
                                 f"FROM {t.name}", f"GROUP BY {g.name}", "ORDER BY pct_of_total DESC"))
    conds = _extra_where(r, t, "t", [])
    if not conds:
        raise CantBuild("a percentage of what: say 'per <column>' for each group's share, or the condition to measure")
    cond = " AND ".join(conds)
    s.steps.append("SUM(CASE WHEN ... THEN 1 ELSE 0 END) counts the rows that match; COUNT(*) counts them all")
    s.steps.append("100.0 keeps the division decimal; NULLIF avoids dividing by zero")
    return _finish(r, _lines(f"SELECT {_pct(r, f'SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)', 'COUNT(*)', places)} AS pct",
                             f"FROM {t.name} t"))


# ------------------------------------------------------------------------------------------------ ranks, running totals, previous row
def _window(r: Reader, low: str) -> str | None:
    rank = re.search(r"\b(dense[_ ]rank|row[_ ]number|rank(?:ed|ing|s)?|number (?:the|each) rows?)\b", low)
    run = re.search(r"\b(?:running|cumulative)\s+(?:total|sum|count)\b", low)
    prev = re.search(r"\b(previous|prior|preceding|next|following|last)\s+(?:row|order|value|one|month|day|record|entry|"
                     r"[a-z_]+'s)(?![\w'])", low)
    if not (rank or run or prev):
        return None
    if re.search(r"\b(?:median|moving average|rolling)\b", low):
        return None
    vm_ = re.search(r"\b(?:by|on|according to|based on|of)\s+(?:the\s+|their\s+)?(?:highest\s+|lowest\s+)?"
                    r"([a-z][a-z0-9_]*(?: (?!within\b|in\b|for\b|per\b|without\b)[a-z][a-z0-9_]*)?)", low)
    t = _table(r, vm_.group(1) if vm_ else None)
    s = r.spec
    g = _group(r, t, low)
    no_window = bool(re.search(r"\bwithout (?:using )?(?:any )?window", low))
    if rank:
        kind = rank.group(1)
        dense = "dense" in kind or re.search(r"\bno gaps|without gaps|consecutive\b", low)
        rown = "row" in kind or "number" in kind
        bm = re.search(r"\b(?:by|on|according to|based on)\s+(?:the\s+|their\s+)?(?:highest\s+|lowest\s+)?"
                       r"([a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)?)", low)
        v = None
        if bm:
            v = _word_col(r, t, bm.group(1))
        if v is None or v.kind not in ("num", "date"):
            v = r.money_col(t)
        desc = not re.search(r"\b(?:lowest|smallest|ascending|asc|low to high|oldest|earliest|cheapest)\b", low)
        fn = "ROW_NUMBER" if rown else "DENSE_RANK" if dense else "RANK"
        op = ">" if desc else "<"
        pk = _pk(r, t)
        if no_window:
            if rown:
                raise CantBuild("a row number without window functions (ties need a tie-breaker)")
            cnt = f"COUNT(DISTINCT t2.{v.name})" if dense else "COUNT(*)"
            inner = [f"t2.{v.name} {op} t.{v.name}"] + ([f"t2.{g.name} = t.{g.name}"] if g else [])
            expr = f"(SELECT {cnt} + 1 FROM {t.name} t2 WHERE {' AND '.join(inner)})"
            s.steps.append(f"the rank is 1 + how many {'different ' if dense else ''}{v.name} values are "
                           f"{'above' if desc else 'below'} this row's" + (f" in the same {g.name}" if g else "")
                           + ": the same numbers RANK()" + (" / DENSE_RANK()" if dense else "") + " gives, without window functions")
            conds = [f"t.{v.name} IS NOT NULL"] + ([f"t.{g.name} IS NOT NULL"] if g else []) + \
                _extra_where(r, t, "t", [(rank.start(), rank.end())])
        else:
            over = (f"PARTITION BY t.{g.name} " if g else "") + f"ORDER BY t.{v.name} {'DESC' if desc else 'ASC'}" + \
                (f", t.{pk.name}" if rown else "")
            expr = f"{fn}() OVER ({over})"
            s.steps.append(f"{fn}() numbers the rows by {v.name} {'highest' if desc else 'lowest'} first"
                           + (f", starting again at 1 for each {g.name}" if g else ""))
            s.steps.append({"RANK": "ties share a rank and the next rank is skipped (1, 1, 3)",
                            "DENSE_RANK": "ties share a rank and no rank is skipped (1, 1, 2)",
                            "ROW_NUMBER": f"every row gets its own number; {pk.name} breaks ties so the order is fixed"}[fn])
            s.steps.append("window functions need MySQL 8+, MariaDB 10.2+, PostgreSQL or SQLite 3.25+; rows with no "
                           f"{v.name} are left out (each database would put NULLs at a different end)")
            conds = [f"t.{v.name} IS NOT NULL"] + ([f"t.{g.name} IS NOT NULL"] if g else []) + \
                _extra_where(r, t, "t", [(rank.start(), rank.end())])
        if g:
            s.steps.append(f"rows with no {g.name} belong to no group, so they are not ranked")
        return _finish(r, _lines(f"SELECT t.*, {expr} AS rank_no", f"FROM {t.name} t", _where(conds),
                                 "ORDER BY " + (f"t.{g.name}, " if g else "") + "rank_no"))
    if no_window:
        return None
    if run:
        am0 = re.search(r"\b(?:running|cumulative)\s+(?:total|sum)\s+(?:of\s+)?(?:the\s+)?([a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)?)", low)
        if am0:
            t = _table(r, am0.group(1))
            g = _group(r, t, low)
    pk = _pk(r, t)
    if run:
        am = re.search(r"\b(?:running|cumulative)\s+(?:total|sum)\s+(?:of\s+)?(?:the\s+)?([a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)?)", low)
        counting = run.group(0).endswith("count")
        v = None
        if am and not counting:
            v = _word_col_soft(r, t, am.group(1)) or _word_col_soft(r, t, am.group(1).split()[0])
        if v is None and not counting:
            v = r.money_col(t)
        dm = re.search(r"\b(?:by|over|ordered by|order by|sorted by|across)\s+(?:the\s+)?([a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)?)", low)
        d = _word_col(r, t, dm.group(1)) if dm else None
        d = d if d is not None and d.kind in ("date", "num", "id") and d is not v else _date_col(r, t, low)
        over = (f"PARTITION BY t.{g.name} " if g else "") + f"ORDER BY t.{d.name}, t.{pk.name} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
        expr = f"COUNT(*) OVER ({over})" if counting else f"SUM(t.{v.name}) OVER ({over})"
        s.steps.append(f"{'COUNT(*)' if counting else 'SUM(' + v.name + ')'} OVER (... ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) "
                       f"adds up every row so far, in {d.name} order" + (f", starting again for each {g.name}" if g else ""))
        s.steps.append(f"{pk.name} breaks ties on the same {d.name}, so the running total grows one row at a time")
        s.steps.append("window functions need MySQL 8+, MariaDB 10.2+, PostgreSQL or SQLite 3.25+")
        conds = _extra_where(r, t, "t", [(run.start(), run.end() + 30)])
        return _finish(r, _lines(f"SELECT t.*, {expr} AS running_{'count' if counting else 'total'}", f"FROM {t.name} t",
                                 _where(conds), "ORDER BY " + (f"t.{g.name}, " if g else "") + f"t.{d.name}, t.{pk.name}"))
    # the previous / next row's value
    after = low[prev.end():prev.end() + 30]
    vm = re.match(r"\s*(?:'s\s+)?([a-z][a-z0-9_]*)", after)
    word = prev.group(0).split()[-1]
    v = None
    if word.endswith("'s"):
        vm = re.match(r"\s*([a-z][a-z0-9_]*)", after)
    if vm:
        v = _word_col_soft(r, t, vm.group(1))
    if v is None:
        for a, _b, mt, mc in r.mentions():
            if mt is t and mc.kind == "num" and abs(a - prev.start()) < 50:
                v = mc
                break
    v = v or r.money_col(t)
    dm = re.search(r"\b(?:ordered by|order by|sorted by|in order of|by)\s+(?:the\s+)?([a-z][a-z0-9_]*)", low)
    d = _word_col_soft(r, t, dm.group(1)) if dm else None
    d = d if d is not None and d.kind in ("date", "num", "id") and d is not v else _date_col(r, t, low)
    fn = "LEAD" if prev.group(1) in ("next", "following") else "LAG"
    over = (f"PARTITION BY t.{g.name} " if g else "") + f"ORDER BY t.{d.name}, t.{pk.name}"
    sel = [f"t.*", f"{fn}(t.{v.name}) OVER ({over}) AS {'next' if fn == 'LEAD' else 'previous'}_{v.name}"]
    if re.search(r"\b(?:difference|change|diff|growth|increase|compared)\b", low):
        sel.append(f"t.{v.name} - {fn}(t.{v.name}) OVER ({over}) AS change_in_{v.name}" if fn == "LAG" else
                   f"{fn}(t.{v.name}) OVER ({over}) - t.{v.name} AS change_in_{v.name}")
    s.steps.append(f"{fn}() reads the {'next' if fn == 'LEAD' else 'previous'} row's {v.name} in {d.name} order"
                   + (f", within the same {g.name}" if g else "") + "; the first row has none (NULL)")
    s.steps.append("window functions need MySQL 8+, MariaDB 10.2+, PostgreSQL or SQLite 3.25+")
    return _finish(r, _lines("SELECT " + ", ".join(sel), f"FROM {t.name} t", _where(_extra_where(r, t, "t", [(prev.start(), prev.end() + 30)])),
                             "ORDER BY " + (f"t.{g.name}, " if g else "") + f"t.{d.name}, t.{pk.name}"))


# ------------------------------------------------------------------------------------------------ changing data
def _change(r: Reader, low: str) -> str | None:
    up = re.search(r"\b\d+(?:\.\d+)?\s*(?:%|percent)?\s+(?:from\s+)?(increase|raise|hike|decrease|reduce|cut|lower)\b", low) or \
        re.search(r"^\s*(?:please\s+)?(?:(?:write|give|need|want|make)\s+(?:me\s+)?(?:an?\s+)?(?:query|sql|statement|update)\s+"
                   r"(?:query\s+)?(?:to|that|which|for)\s+)?(increase|raise|hike|decrease|reduce|cut|lower|set|update|change|mark)\b",
                   low) or re.search(r"\b(increase|raise|hike|decrease|reduce|cut)\s+(?:the\s+)?[a-z_ ]{2,30}?\s+by\s+\d", low)
    de = re.search(r"\b(delete|remove|purge)\b", low)
    if not up and not de:
        return None
    if re.search(r"\bswap\b|\binsert\b|\badd (?:a |new )?rows?\b", low):
        return None
    t = _table(r)
    s = r.spec
    if de and (not up or de.start() < up.start()):
        return _delete(r, t, low, de)
    verb = up.group(1)
    spans = [(up.start(), up.end())]
    if verb in ("increase", "raise", "hike", "decrease", "reduce", "cut", "lower") or (verb in ("update", "change") and
                                                                                        re.search(r"\bby\s+\d", low)):
        m = re.search(r"\b(?:increase|raise|hike|decrease|reduce|cut|lower|update|change)\s+(?:the\s+)?(?:of\s+)?"
                      r"([a-z][a-z0-9_ ]{0,40}?)\s+(?:of\s+[a-z_ ]+?\s+)?by\s+(\d+(?:\.\d+)?)\s*(%|percent)?", low)
        if not m:                                       # Hinglish order: 'salary 10% increase', 'salary ko 500 se decrease'
            m = re.search(r"\b([a-z][a-z0-9_ ]{0,40}?)\s+(?:ko\s+|of\s+)?(?:by\s+)?(\d+(?:\.\d+)?)\s*(%|percent)?\s+(?:from\s+)?"
                          r"(?:increase|raise|hike|decrease|reduce|cut|lower)\b", low)
        if not m:
            raise CantBuild("which column to change and by how much (for example 'increase salary by 10%')")
        phrase = m.group(1)
        ws = phrase.split()
        c = next((x for k in (len(ws), 2, 1) if 0 < k <= len(ws) for x in [_word_col_soft(r, t, " ".join(ws[-k:]))] if x), None) or (r.money_col(t) if re.search(r"\b(?:salar\w*|pay|wage|price|amount|fee|cost)\b", phrase)
                                              else _word_col(r, t, phrase, "num"))
        if c is None or c.kind not in ("num",):
            raise CantBuild(f"which number column '{phrase}' is")
        k = m.group(2)
        down = verb in ("decrease", "reduce", "cut", "lower")
        if m.group(3):
            factor = (1 - float(k) / 100) if down else (1 + float(k) / 100)
            f = f"{factor:.6f}".rstrip("0").rstrip(".")
            new = f"{c.name} * {f}"
            s.steps.append(f"{c.name} * {f}: {'down' if down else 'up'} by {k}%")
        else:
            new = f"{c.name} {'-' if down else '+'} {k}"
            s.steps.append(f"{c.name} {'-' if down else '+'} {k}")
        spans.append((m.start(), m.end()))
        target = c
    else:
        m = re.search(r"\b(?:set|update|change)\s+(?:the\s+)?([a-z][a-z0-9_ ]{0,30}?)\s+(?:to|=|as)\s+('[^']*'|\"[^\"]*\"|"
                      rf"{W.NUM}|\d{{4}}-\d\d-\d\d|null|[a-z][\w-]*)", r.low) or \
            re.search(r"\bmark\s+(?:the\s+|all\s+|those\s+)?(?:[a-z_]+\s+)?as\s+('[^']*'|\"[^\"]*\"|[a-z][\w-]*)", r.low)
        if not m:
            raise CantBuild("which column to set and to what (for example \"set status to 'shipped' where ...\")")
        if m.re.pattern.startswith(r"\bmark"):
            target = next((c for c in t.cols.values() if re.search(r"status|state|flag|stage", c.name.lower())), None) or \
                _guess(r, t, "status", "text")
            raw = r.cased[m.start(1):m.end(1)]
        else:
            target = _word_col(r, t, m.group(1))
            if target is None:
                raise CantBuild(f"which column '{m.group(1)}' is")
            raw = r.cased[m.start(2):m.end(2)]
        new = "NULL" if raw.lower() == "null" else W._val(raw, target)
        spans.append((m.start(), m.end()))
        s.steps.append(f"SET {target.name} = {new}")
    conds = _extra_where(r, t, "", spans)
    if not conds:
        if not re.search(r"\b(?:all|every|everyone|everybody|whole table|all rows)\b", low):
            raise CantBuild(f"which {t.name} rows to change (an UPDATE without WHERE changes every row); say 'all {t.name}' if "
                            f"that is what you want")
        s.steps.append(f"no WHERE: every row of {t.name} changes, as you asked")
    preview = f"SELECT COUNT(*) FROM {t.name}" + (" WHERE " + " AND ".join(conds) if conds else "")
    s.steps.append(f"run it inside a transaction: BEGIN, the UPDATE, check the rows, then COMMIT (or ROLLBACK to undo). "
                   f"Preview first: {preview};")
    return _finish(r, _lines(f"UPDATE {t.name}", f"SET {target.name} = {new}", _where(conds)))


def _delete(r: Reader, t: Table, low: str, de) -> str:
    s = r.spec
    if re.search(r"\bduplicates?\b|\bduplicate (?:rows|records|entries)\b|\brepeated\b", low):
        sm = re.search(r"\b(?:on|by|based on|with the same|same|of|in)\s+(?:the\s+)?(?:same\s+)?([a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)?)",
                       low[de.end():])
        cols = []
        pk0 = _pk(r, t)
        for m in re.finditer(r"\b(?:same|on|by|based on|duplicate|with the same)\s+(?:the\s+)?([a-z][a-z0-9_]*(?:\s*(?:,|and)\s*[a-z][a-z0-9_]*)*)", low):
            for w in re.split(r"\s*(?:,|\band\b)\s*", m.group(1)):
                c = _word_col_soft(r, t, w) or t.col(w)
                if c is not None and c not in cols and c is not pk0:
                    cols.append(c)
        if not cols and sm:
            c = _word_col(r, t, sm.group(1))
            if c is not None and c is not pk0:
                cols.append(c)
        if not cols:
            raise CantBuild("which column makes two rows duplicates (for example 'duplicate emails')")
        pk = _pk(r, t)
        keep_new = bool(re.search(r"\bkeep(?:ing)?\s+(?:the\s+)?(?:latest|newest|last|highest|most recent|max)", low))
        fn = "MAX" if keep_new else "MIN"
        key = ", ".join(c.name for c in cols)
        notnull = " AND ".join(f"{c.name} IS NOT NULL" for c in cols)
        s.steps.append(f"the inner query keeps one {pk.name} per {key} ({'the highest' if keep_new else 'the lowest'}); every other "
                       f"row with the same {key} is deleted")
        s.steps.append("the extra SELECT around it (AS keep_ids) lets MySQL / MariaDB read the table it deletes from; rows with "
                       "a NULL there are never called duplicates")
        s.steps.append(f"run it inside a transaction and preview first: SELECT {key}, COUNT(*) FROM {t.name} GROUP BY {key} "
                       f"HAVING COUNT(*) > 1;")
        return _finish(r, _lines(f"DELETE FROM {t.name}", f"WHERE {notnull}",
                                 f"  AND {pk.name} NOT IN (SELECT keep_id FROM (SELECT {fn}({pk.name}) AS keep_id FROM {t.name} "
                                 f"WHERE {notnull} GROUP BY {key}) AS keep_ids)"))
    conds = _extra_where(r, t, "", [(de.start(), de.end())])
    if not conds:
        raise CantBuild(f"which {t.name} rows to delete (a DELETE without WHERE empties the table)")
    preview = f"SELECT * FROM {t.name} WHERE " + " AND ".join(conds)
    s.steps.append("DELETE removes only the rows the WHERE matches")
    s.steps.append(f"run it inside a transaction (BEGIN ... COMMIT, or ROLLBACK to undo) and preview first: {preview};")
    return _finish(r, _lines(f"DELETE FROM {t.name}", _where(conds)))
