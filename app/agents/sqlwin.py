"""CASE, WITH (CTEs, recursive CTEs) and window functions (night 7 of the curriculum), shared by every database
(fundamentals 'case_logic', 'recursion' and 'window_frames' in app/dbknow/core/query_fundamentals.json).

CASE is read top to bottom and stops at the first WHEN that is true; with no ELSE, a row that matches no WHEN gets NULL. So:
- a WHEN whose condition is narrower than an earlier one (amount > 10000 after amount > 100, x AND y after x) can never be
  reached: sure -> 'wrong', and the WHENs are put narrowest first.
- CASE x WHEN NULL compares x = NULL, which is never true: sure -> 'wrong', rewritten as CASE WHEN x IS NULL ...
- no ELSE: unmatched rows get NULL. In ORDER BY that NULL sorts first on MySQL/MariaDB and last on PostgreSQL; as a label it
  is an empty value. Depends on what was meant -> 'check'.
- every THEN/ELSE must give one type: text next to a number makes PostgreSQL stop (22P02) and makes MySQL/MariaDB turn the
  whole column into text (1000 then sorts before 50). Engine facts from the dialect files (rule B).
WITH names a query; WITH RECURSIVE lets it read itself. A recursive query is an anchor (run once) UNION a recursive part
(run again on the rows the last round made) and it stops only when a round makes no rows. So:
- a CTE that reads its own name without RECURSIVE: refused everywhere (1146 / 42P01) -> 'error', RECURSIVE added.
- a recursive part with nothing that can end it (no WHERE, no join): PostgreSQL runs until it fails, MariaDB stops silently
  after max_recursive_iterations (1000) rounds, MySQL stops with 3636 -> 'wrong'; the bound is added when the words say it.
- a walk through a table (parent -> child) with UNION ALL and no depth limit never ends if the data has a loop -> 'check'.
- the anchor fixes each column's type: MySQL/MariaDB size text from the anchor ('x' is 1 character: longer paths stop with
  1406), PostgreSQL refuses a VARCHAR(n) anchor that the recursive part makes longer (42804). Engine facts (rule B).
- MariaDB's 1000-round limit cuts a longer series short with no error; aggregates, ORDER BY and LIMIT are refused in the
  recursive part; a column list must match the anchor; two CTEs can't share a name.
A window function is worked out after WHERE, GROUP BY and HAVING, on the rows that are left, so:
- it can't be used in WHERE / HAVING / GROUP BY, and its SELECT name can't be either: refused everywhere -> 'error', the
  query is wrapped (numbered inside, filtered outside).
- with ORDER BY and no frame, the frame is RANGE UNBOUNDED PRECEDING .. CURRENT ROW: rows that tie are one step (a running
  total gives them the same value) and LAST_VALUE gives the current row's own value -> LAST_VALUE 'wrong', running totals
  'check'; ROW_NUMBER over a sort with ties numbers the tied rows in any order (the engines differ) -> 'check'.
- LAG / LEAD / RANK ... need an ORDER BY: MariaDB refuses them without one (4018), PostgreSQL gives arbitrary rows.
- 'per customer' said in words with no PARTITION BY: one window over everything -> 'wrong', PARTITION BY added.
Every rule was reproduced on MariaDB 10.11 and PostgreSQL 16 (tests/practice/test_night7.py has the lab table).
Returns [(Finding, [edits])], edit = (start, end, new text) on the original query.
"""

import re

# the engines each rule was reproduced on (tests/practice/test_night7.py, LAB) -- standing rule B
PROVEN = {k: ["mariadb", "postgresql"] for k in (
    "case_unreachable", "case_null", "case_no_else", "case_types",
    "cte_recursive_kw", "cte_no_stop", "cte_cycle", "cte_width", "cte_depth", "cte_recursive_term", "cte_columns", "cte_dup_name",
    "win_where", "win_alias_where", "win_nested", "win_no_order", "win_last_value", "win_running_ties", "win_rank_ties",
    "win_partition_said", "win_version")}

WINDOW_FN = re.compile(r"\b(row_number|rank|dense_rank|percent_rank|cume_dist|ntile|lag|lead|first_value|last_value|nth_value|"
                       r"count|sum|avg|min|max|stddev|variance|group_concat|string_agg|array_agg|bit_and|bit_or)\s*$", re.I)
NEEDS_ORDER = {"lag", "lead", "rank", "dense_rank", "percent_rank", "cume_dist"}
ARBITRARY = {"ntile", "first_value", "last_value", "nth_value"}
AGG_NAMES = {"count", "sum", "avg", "min", "max", "group_concat", "string_agg", "array_agg", "stddev", "variance", "bit_and", "bit_or"}
NUMERIC_NAME = re.compile(r"(^|_)(amount|price|salary|qty|quantity|total|cost|discount|stock|score|marks|age|balance|fee|fees|"
                          r"commission|tax|weight|height|count|units|value|paid|due|rate|percent|points|rating)$", re.I)
TIES_SAID = re.compile(r"\bties?\b|\btied\b|\bbarabar\b|\bequal\b|\bsame\s+(?:salary|score|marks|amount|price|value|rank)\b|"
                       r"\bboth\b|\bsab(?:hi)?\b|बराबर", re.I)
NTH_SAID = re.compile(r"\b(2nd|second|3rd|third|\d+(?:st|nd|rd|th)|nth|doosr[ia]|teesr[ia])\s+(?:highest|largest|lowest|smallest|"
                      r"max(?:imum)?|min(?:imum)?|sabse\s+(?:zyada|kam|bad[ia]))", re.I)
OLD_VERSION = re.compile(r"\bmysql\s*(?:version\s*)?5(?:\.\d+)?\b|\bmariadb\s*(?:version\s*)?10\.[01]\b|\bmysql\s*v?5\.[5-7]\b", re.I)


def _C():
    from app.agents import sqlcorrect as C

    return C


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _short(t: str, n: int = 100) -> str:
    t = _flat(t)
    return t if len(t) <= n else t[:n - 3] + "..."


def _uq(n: str) -> str:
    return (n or "").strip('`"').lower()


def _engine(sql: str):
    """('mysql' | 'postgresql' | None, the exact database chosen if any)."""
    from app.agents import sqlbasics

    return sqlbasics.engine_seen(sql), (sqlbasics.CHOSEN.get() or "")


def _rule(eng: str, key: str) -> dict:
    from app.agents import sqlbasics

    return sqlbasics.rules(eng).get(key, {}) if eng else {}


def _says(key: str, eng) -> str:
    """The engine fact from the dialect files (rule B): for the known database only, else for each."""
    if eng == "mysql":
        return _rule("mysql", key).get("says", "")
    if eng == "postgresql":
        return _rule("postgresql", key).get("says", "")
    return f"{_rule('mysql', key).get('says', '')} {_rule('postgresql', key).get('says', '')}".strip()


# ------------------------------------------------------------------------------------------------ reading the query's parts
def _levels(s: str) -> list[tuple[int, int]]:
    """Every SELECT level: the whole statement and each (SELECT ...) inside it, as (start, end) spans of s."""
    C = _C()
    out = [(0, len(s))]
    for m in re.finditer(r"\(\s*(?=select\b|with\b)", s, re.I):
        out.append((m.end(), C._close(s, m.start())))
    return out


def _level_of(levels, pos: int) -> tuple[int, int]:
    best = levels[0]
    for a, b in levels:
        if a <= pos < b and (b - a) < (best[1] - best[0]):
            best = (a, b)
    return best


def _clause_at(s: str, level, pos: int) -> str:
    """The clause of its own level a position is in: select / where / group by / having / order by / from / ..."""
    C = _C()
    a, b = level
    text = s[a:b]
    cl = C._clauses(text, C._depths(text))
    rel = pos - a
    best, at = "", -1
    for k, (x, _y) in cl.items():
        if x <= rel and x > at:
            best, at = k, x
    return best


def _ctes(s: str) -> dict | None:
    """WITH [RECURSIVE] name [(cols)] AS (body), ...: {'recursive', 'with_end', 'items': [...], 'main': start of the main query}."""
    C = _C()
    m = re.match(r"\s*with\s+(recursive\s+)?", s, re.I)
    if not m:
        return None
    pos, items = m.end(), []
    while True:
        nm = re.compile(r"[`\"]?([A-Za-z_]\w*)[`\"]?\s*(\(([^()]*)\))?\s*as\s*(?:not\s+)?(?:materialized\s+)?\(", re.I).match(s, pos)
        if not nm:
            return None
        a = nm.end() - 1
        b = C._close(s, a)
        cols = [c.strip().strip('`"').lower() for c in nm.group(3).split(",")] if nm.group(3) else None
        items.append({"name": nm.group(1).lower(), "name_at": nm.start(1), "cols": cols, "a": a + 1, "b": b})
        rest = re.compile(r"\s*,\s*").match(s, b + 1)
        if not rest:
            return {"recursive": bool(m.group(1)), "with_end": m.end(), "items": items, "main": b + 1}
        pos = rest.end()


def _members(s: str, a: int, b: int) -> list[tuple[int, int, str]]:
    """The parts of a UNION inside s[a:b]: (start, end, the UNION word before it)."""
    C = _C()
    text = s[a:b]
    d = C._depths(text)
    cuts = [(m.start(), m.end(), _flat(m.group(0)).lower()) for m in re.finditer(r"\bunion(?:\s+all|\s+distinct)?\b", text, re.I)
            if d[m.start()] == 0]
    out, last, word = [], 0, ""
    for x, y, w in cuts:
        out.append((a + last, a + x, word))
        last, word = y, w
    out.append((a + last, b, word))
    return out


def _items(s: str, sql: str, a: int, b: int) -> list[dict]:
    """The SELECT list of the query in s[a:b]: [{'text', 'expr', 'alias', 'start', 'end'}] (texts from the original query)."""
    C = _C()
    text = s[a:b]
    cl = C._clauses(text, C._depths(text))
    if "select" not in cl:
        return []
    x, y = cl["select"]
    out = []
    for off, part in C._split_top(text[x:y]):
        t = part.strip()
        if not t:
            continue
        st = a + x + off + (len(part) - len(part.lstrip()))
        en = st + len(t)
        orig = sql[st:en]
        am = re.search(r"(?:\bas\s+|(?<=[\w)'\"`])\s+)[`\"]?([A-Za-z_]\w*)[`\"]?\s*$", t, re.I)
        alias = ""
        if am and am.group(1).lower() not in C.KW and not re.fullmatch(r"[`\"]?\w+[`\"]?(\.[`\"]?\w+[`\"]?)?", t):
            alias = am.group(1)
        expr = orig[:am.start()].rstrip() if alias else orig
        if re.match(r"(?i)distinct\s+", expr):
            expr = expr[re.match(r"(?i)distinct\s+", expr).end():]
        out.append({"text": orig, "expr": expr, "alias": alias, "start": st, "end": en,
                    "name": (alias or (re.fullmatch(r"(?:[`\"]?\w+[`\"]?\.)?[`\"]?(\w+)[`\"]?", expr.strip()) or [None, ""])[1]).lower()})
    return out


def _cases(s: str) -> list[dict]:
    """Every CASE ... END: {'start', 'end' (after END), 'operand' (a, b) or None, 'whens': [(ca, cb, ra, rb)], 'else': (a, b)}."""
    C = _C()
    dep = C._depths(s)
    out, stack = [], []
    for m in re.finditer(r"\b(case|when|then|else|end)\b", s, re.I):
        w = m.group(1).lower()
        if w == "case":
            stack.append({"start": m.start(), "depth": dep[m.start()], "marks": [("case", m.start(), m.end())]})
        elif stack:
            top = stack[-1]
            if dep[m.start()] != top["depth"]:
                continue
            top["marks"].append((w, m.start(), m.end()))
            if w == "end":
                stack.pop()
                out.append(top)
    res = []
    for c in out:
        mk = c["marks"]
        if not mk or mk[-1][0] != "end":
            continue
        item = {"start": c["start"], "end": mk[-1][2], "end_word": mk[-1][1], "operand": None, "whens": [], "else": None}
        if len(mk) > 1 and mk[1][0] == "when" and s[mk[0][2]:mk[1][1]].strip():
            item["operand"] = (mk[0][2], mk[1][1])
        i = 1
        ok = True
        while i < len(mk) - 1:
            w, a, b = mk[i]
            if w == "when" and i + 1 < len(mk) and mk[i + 1][0] == "then":
                nxt = mk[i + 2][1] if i + 2 < len(mk) else mk[-1][1]
                item["whens"].append((b, mk[i + 1][1], mk[i + 1][2], nxt))
                i += 2
            elif w == "else":
                item["else"] = (b, mk[i + 1][1])
                i += 1
            else:
                ok = False
                break
        if ok and item["whens"]:
            res.append(item)
    return res


def _enclosing_fn(s: str, pos: int) -> str:
    """The function a position sits inside ('sum', 'count', ...), 'select' when inside a subquery, '' at the top."""
    d = 0
    for k in range(pos - 1, -1, -1):
        ch = s[k]
        if ch == ")":
            d += 1
        elif ch == "(":
            if d == 0:
                if re.match(r"\s*(select|with)\b", s[k + 1:], re.I):
                    return "select"
                wm = re.search(r"([A-Za-z_]\w*)\s*$", s[:k])
                return wm.group(1).lower() if wm else "("
            d -= 1
    return ""


def _windows(s: str) -> list[dict]:
    """Every window call: fn(args) OVER (spec) / OVER name, with its parts."""
    C = _C()
    named = {}
    for m in re.finditer(r"\bwindow\s+", s, re.I):
        for nm in re.finditer(r"([A-Za-z_]\w*)\s+as\s*\(", s[m.end():], re.I):
            a = m.end() + nm.end() - 1
            named[nm.group(1).lower()] = (a + 1, C._close(s, a))
    out = []
    for m in re.finditer(r"\bover\s*(\(|([A-Za-z_]\w*))", s, re.I):
        k = m.start() - 1
        while k >= 0 and s[k].isspace():
            k -= 1
        if k < 0 or s[k] != ")":
            continue
        d, j = 0, k
        while j >= 0:
            if s[j] == ")":
                d += 1
            elif s[j] == "(":
                d -= 1
                if d == 0:
                    break
            j -= 1
        fm = WINDOW_FN.search(s[:j])
        if not fm:
            continue
        w = {"fn": fm.group(1).lower(), "fn_start": fm.start(), "args": (j + 1, k), "over": m.start(), "named": None}
        if m.group(1) == "(":
            a = m.end() - 1
            b = C._close(s, a)
            w["spec"], w["end"] = (a + 1, b), b + 1
        else:
            w["named"] = m.group(2).lower()
            w["spec"], w["end"] = named.get(w["named"]), m.end()
            if w["spec"] is None:
                continue
        sa, sb = w["spec"]
        spec = s[sa:sb]
        sd = C._depths(spec)
        pm = next((x for x in re.finditer(r"\bpartition\s+by\b", spec, re.I) if sd[x.start()] == 0), None)
        om = next((x for x in re.finditer(r"\border\s+by\b", spec, re.I) if sd[x.start()] == 0), None)
        fr = next((x for x in re.finditer(r"\b(rows|range|groups)\b", spec, re.I) if sd[x.start()] == 0), None)
        w["partition"] = (sa + pm.end(), sa + (om.start() if om else fr.start() if fr else len(spec))) if pm else None
        w["order"] = (sa + om.end(), sa + (fr.start() if fr else len(spec))) if om else None
        w["frame"] = bool(fr)
        out.append(w)
    return out


def _sources_of(s: str) -> list[tuple[str, str]]:
    """(table, alias) of every FROM / JOIN table in the text."""
    C = _C()
    out = []
    for m in re.finditer(r"\b(?:from|join)\s+[`\"]?([A-Za-z_][\w$.]*)[`\"]?(?:\s+(?:as\s+)?([A-Za-z_]\w*))?", s, re.I):
        alias = m.group(2) if m.group(2) and m.group(2).lower() not in C.KW else m.group(1).split(".")[-1]
        out.append((m.group(1).split(".")[-1].lower(), alias.lower()))
    return out


def _key_of(s: str, level) -> str | None:
    """A column that names one row of the level's main table, written the way the query writes it (o.id / id), when the query
    itself uses it; None when it doesn't (no guessing a key the query never shows)."""
    C = _C()
    a, b = level
    text = s[a:b]
    srcs = _sources_of(C._no_subqueries(text))
    if not srcs:
        return None
    table, alias = srcs[0]
    keys = ["id"] + sorted(re.escape(s) + "_?id" for s in C._singulars(table) if s != table)
    for k in keys:
        m = re.search(rf"(?<![\w$]){re.escape(alias)}\.[`\"]?({k})\b", text, re.I)
        if m:
            return f"{alias}.{m.group(1)}"
    if len(srcs) == 1:
        for k in keys:
            m = re.search(rf"(?<![\w$.]){k}\b(?!\s*\()", text, re.I)
            if m:
                return m.group(0)
    return None


def _is_key(expr: str, s: str, level) -> bool:
    """Does an ORDER BY key name one row (id / order_id of orders)?"""
    C = _C()
    e = re.sub(r"(?i)\s+(asc|desc)\b.*$|\s+nulls\s+(first|last)\b.*$", "", expr.strip())
    m = re.fullmatch(r"(?:[`\"]?(\w+)[`\"]?\.)?[`\"]?(\w+)[`\"]?", e)
    if not m:
        return False
    col = m.group(2).lower()
    srcs = _sources_of(C._no_subqueries(s[level[0]:level[1]]))
    owners = [t for t, al in srcs if not m.group(1) or al == m.group(1).lower()]
    if not owners:
        owners = [t for t, _al in srcs]
    return col == "id" and (m.group(1) or len(srcs) == 1) or any(col.replace("_", "") == x.replace("_", "") + "id"
                                                                  for t in owners[:1] for x in C._singulars(t) if x != t)


# ------------------------------------------------------------------------------------------------ CASE
def _pred(cond: str):
    """A simple condition as (column, ('range', lo, lo_in, hi, hi_in) | ('set', frozenset)) or None."""
    c = cond.strip()
    while c.startswith("(") and c.endswith(")") and _C()._close(c, 0) == len(c) - 1:
        c = c[1:-1].strip()
    col = r"((?:[`\"]?\w+[`\"]?\.)?[`\"]?[A-Za-z_]\w*[`\"]?)"
    num = r"(-?\d+(?:\.\d+)?)"
    m = re.fullmatch(col + r"\s*(>=|<=|>|<|=)\s*" + num, c)
    if m:
        v, op = float(m.group(3)), m.group(2)
        rng = {">": (v, False, None, False), ">=": (v, True, None, False), "<": (None, False, v, False), "<=": (None, False, v, True),
               "=": (v, True, v, True)}[op]
        return _uq(m.group(1).split(".")[-1]), ("range",) + rng
    m = re.fullmatch(num + r"\s*(>=|<=|>|<)\s*" + col, c)
    if m:
        v, op = float(m.group(1)), {">": "<", ">=": "<=", "<": ">", "<=": ">="}[m.group(2)]
        rng = {">": (v, False, None, False), ">=": (v, True, None, False), "<": (None, False, v, False), "<=": (None, False, v, True)}[op]
        return _uq(m.group(3).split(".")[-1]), ("range",) + rng
    m = re.fullmatch(col + r"\s+between\s+" + num + r"\s+and\s+" + num, c, re.I)
    if m:
        return _uq(m.group(1).split(".")[-1]), ("range", float(m.group(2)), True, float(m.group(3)), True)
    m = re.fullmatch(col + r"\s*=\s*('[^']*')", c)
    if m:
        return _uq(m.group(1).split(".")[-1]), ("set", frozenset([m.group(2)]))
    m = re.fullmatch(col + r"\s+in\s*\(\s*('[^']*'(?:\s*,\s*'[^']*')*)\s*\)", c, re.I)
    if m:
        return _uq(m.group(1).split(".")[-1]), ("set", frozenset(x.strip() for x in m.group(2).split(",")))
    return None


def _within(p, q) -> bool:
    """Is every row p accepts also accepted by q (same column)?"""
    if p is None or q is None or p[0] != q[0] or p[1][0] != q[1][0]:
        return False
    if p[1][0] == "set":
        return p[1][1] <= q[1][1]
    _r, plo, plin, phi, phin = p[1]
    _r, qlo, qlin, qhi, qhin = q[1]
    lo_ok = qlo is None or (plo is not None and (plo > qlo or (plo == qlo and (qlin or not plin))))
    hi_ok = qhi is None or (phi is not None and (phi < qhi or (phi == qhi and (qhin or not phin))))
    return lo_ok and hi_ok


def _conjuncts(text: str) -> list[str]:
    C = _C()
    d = C._depths(text)
    cuts = [0]
    for m in re.finditer(r"\band\b", text, re.I):
        if d[m.start()] == 0 and not re.search(r"\bbetween\s+\S+\s*$", text[:m.start()], re.I):
            cuts += [m.start(), m.end()]
    cuts.append(len(text))
    parts = [text[cuts[i]:cuts[i + 1]] for i in range(0, len(cuts), 2)]
    return [p.strip() for p in parts if p.strip()]


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip().lower().replace("`", "").replace('"', ""))


def _implies(later: str, earlier: str) -> bool:
    """Is every row 'later' accepts also accepted by 'earlier' (so a WHEN later than 'earlier' can't be reached)?"""
    if re.search(r"\bor\b", earlier, re.I) or re.search(r"\bor\b", later, re.I):
        return _norm(later) == _norm(earlier)
    lc, ec = _conjuncts(later), _conjuncts(earlier)
    for e in ec:
        pe = _pred(e)
        if not any(_norm(l) == _norm(e) or (pe is not None and _within(_pred(l), pe)) for l in lc):
            return False
    return True


def _case_rules(sql: str, s: str, intent: str, F, eng, levels) -> list:
    out = []
    for c in _cases(s):
        ws = c["whens"]
        ctx = _enclosing_fn(s, c["start"])
        clause = _clause_at(s, _level_of(levels, c["start"]), c["start"])
        ev = _short(sql[c["start"]:c["end"]], 110)
        # ---- 1. CASE x WHEN NULL: x = NULL is never true
        if c["operand"]:
            op = sql[c["operand"][0]:c["operand"][1]].strip()
            nulls = [w for w in ws if s[w[0]:w[1]].strip().lower() == "null"]
            if nulls:
                parts = []
                for ca, cb, ra, rb in ws:
                    v = sql[ca:cb].strip()
                    cond = f"{op} IS NULL" if v.lower() == "null" else f"{op} = {v}"
                    parts.append(f"WHEN {cond} THEN {sql[ra:rb].strip()}")
                if c["else"]:
                    parts.append(f"ELSE {sql[c['else'][0]:c['else'][1]].strip()}")
                new = "CASE " + " ".join(parts) + " END"
                out.append((F("wrong", "case_null", f"CASE {op} WHEN NULL never matches", f"CASE {op} WHEN NULL kabhi match nahi hota", ev,
                              f"CASE {op} WHEN v compares {op} = v, and nothing is equal to NULL (not even NULL), so the rows where {op} "
                              f"is empty skip this WHEN and fall through to the next one or the ELSE (the same on MySQL, MariaDB and "
                              f"PostgreSQL). Write the searched form: {_short(new, 120)}",
                              f"CASE {op} WHEN v ka matlab {op} = v hai, aur NULL kisi ke barabar nahi hota, isliye khaali {op} wali rows "
                              f"ye WHEN chhod deti hain. Searched CASE likho: {_short(new, 120)}"), [(c["start"], c["end"], new)]))
                continue
        # ---- 2. a WHEN that an earlier WHEN already takes: it can never be reached
        conds = [(sql[ca:cb].strip() if not c["operand"] else f"{sql[c['operand'][0]:c['operand'][1]].strip()} = {sql[ca:cb].strip()}")
                 for ca, cb, _ra, _rb in ws]
        dead = [(i, j) for i in range(len(ws)) for j in range(i) if _implies(conds[i], conds[j])]
        dead_i = sorted({i for i, _j in dead})
        if dead_i:
            i, j = next((i, j) for i, j in dead if i == dead_i[0])
            dup = _norm(conds[i]) == _norm(conds[j]) or _implies(conds[j], conds[i])
            order = []
            if not dup and all(not _implies(conds[j2], conds[i2]) for i2, j2 in dead):
                for k in range(len(ws)):
                    at = next((n for n, q in enumerate(order) if _implies(conds[k], conds[q]) and not _implies(conds[q], conds[k])), None)
                    if at is None:
                        order.append(k)
                    else:
                        order.insert(at, k)
            edits = []
            if order and order != list(range(len(ws))):
                pairs = [f"WHEN {sql[ws[k][0]:ws[k][1]].strip()} THEN {sql[ws[k][2]:ws[k][3]].strip()}" for k in order]
                head = "CASE " + (sql[c["operand"][0]:c["operand"][1]].strip() + " " if c["operand"] else "")
                tail = f" ELSE {sql[c['else'][0]:c['else'][1]].strip()}" if c["else"] else ""
                edits = [(c["start"], c["end"], head + " ".join(pairs) + tail + " END")]
            later, first = _short(conds[i], 50), _short(conds[j], 50)
            out.append((F("wrong", "case_unreachable", f"WHEN {later} can never be reached", f"WHEN {later} tak kabhi pahuncha nahi jaata",
                          ev, f"CASE stops at the first WHEN that is true. Every row with {later} already matches the earlier WHEN "
                          f"{first}, so it gets that result and this WHEN never runs (no error, on MySQL, MariaDB and PostgreSQL). "
                          + ("Put the narrower conditions first." if edits else "Two WHENs say the same thing: decide which result you "
                                                                                 "meant and keep one."),
                          f"CASE pehle true WHEN par ruk jaata hai. {later} wali har row pehle wale WHEN {first} se hi match ho jaati "
                          f"hai, isliye ye WHEN kabhi nahi chalta. " + ("Chhoti (narrow) conditions pehle likho." if edits else
                                                                         "Do WHEN ek hi baat kehte hain: ek rakho.")), edits))
        # ---- 3. results of different types
        if ctx not in AGG_NAMES:
            res = [(ra, rb) for _ca, _cb, ra, rb in ws] + ([c["else"]] if c["else"] else [])
            texts = [sql[a:b].strip() for a, b in res]
            words = [t for t in texts if re.fullmatch(r"'[^']*'", t) and not re.fullmatch(r"'\s*-?\d+(\.\d+)?\s*'", t)]
            sure = [i for i, t in enumerate(texts) if re.fullmatch(r"-?\d+(\.\d+)?", t) or re.match(r"(?i)(count|sum|avg)\s*\(", t)]
            named = [i for i, t in enumerate(texts) if re.fullmatch(r"(?:[`\"]?\w+[`\"]?\.)?[`\"]?(\w+)[`\"]?", t) and
                     NUMERIC_NAME.search(re.sub(r"[`\"]", "", t.split(".")[-1])) and t.lower() not in ("null", "true", "false")]
            nums = sure + [i for i in named if i not in sure]
            if words and nums:
                kind = ("error" if sure else "check") if eng == "postgresql" else "check"
                cast = {"postgresql": "TEXT", "mysql": "CHAR"}.get(eng or "")
                edits = []
                if cast:
                    for i in nums:
                        a, b = res[i]
                        lead = len(sql[a:b]) - len(sql[a:b].lstrip())
                        edits.append((a + lead, a + lead + len(texts[i]), f"CAST({texts[i]} AS {cast})"))
                num_t = texts[nums[0]]
                out.append((F(kind, "case_types", f"CASE mixes text ({words[0]}) and a number ({_short(num_t, 30)})",
                              f"CASE text ({words[0]}) aur number ({_short(num_t, 30)}) mila raha hai", ev,
                              f"All the results of one CASE must be one type. {_says('case_mixed_types', eng)} "
                              + (f"Make them all text: CAST({num_t} AS {cast}), or use NULL instead of {words[0]} to keep a number."
                                 if cast else "Make them one type: CAST the number to text, or use NULL instead of the word to keep a "
                                              "number."),
                              f"Ek CASE ke saare results ek hi type ke hone chahiye. PostgreSQL error deta hai, MySQL/MariaDB poora "
                              f"column text bana dete hain (phir 1000, 50 se pehle sort hota hai). Sab text karo (CAST) ya word ki "
                              f"jagah NULL rakho."), edits))
        # ---- 4. no ELSE: the rows that match no WHEN get NULL
        if not c["else"] and ctx not in AGG_NAMES and not any(re.search(r"\bis\s+(not\s+)?null\b", cnd, re.I) for cnd in conds):
            res = [sql[ra:rb].strip() for _ca, _cb, ra, rb in ws]
            if clause == "order by" and ctx in ("", "select"):
                ints = [int(r) for r in res if re.fullmatch(r"\d+", r)]
                edits = [(c["end_word"], c["end_word"], f"ELSE {max(ints) + 1} ")] if len(ints) == len(res) else []
                out.append((F("check", "case_no_else", "CASE in ORDER BY has no ELSE: the other rows sort as NULL",
                              "ORDER BY ke CASE mein ELSE nahi: baaki rows NULL ki tarah sort hoti hain", ev,
                              "Rows that match no WHEN get NULL, and NULL goes first on MySQL/MariaDB but last on PostgreSQL, so the "
                              "same query puts them at the top on one database and at the bottom on another. "
                              + (f"Give them a place: ELSE {max(ints) + 1} (after the others)." if edits else "Add an ELSE with the "
                                                                                                             "place they should take."),
                              "Kisi WHEN se match na hone wali rows ko NULL milta hai; MySQL/MariaDB NULL ko upar aur PostgreSQL neeche "
                              "rakhta hai. ELSE se unki jagah tay karo."), edits))
            elif clause == "select" and ctx == "" and res and all(re.fullmatch(r"'[^']*'", r) for r in res):
                out.append((F("check", "case_no_else", "CASE has no ELSE: rows that match no WHEN show NULL",
                              "CASE mein ELSE nahi: jo row kisi WHEN se match nahi hoti wahan NULL aata hai", ev,
                              "With no ELSE, every row outside the WHENs gets NULL (an empty label), on every database. If that is "
                              "not what you want, add ELSE 'other' (or the label those rows should have).",
                              "ELSE ke bina WHEN se bahar wali har row ko NULL milta hai. Agar ye nahi chahiye to ELSE 'other' jaisa "
                              "label do."), []))
    return out


# ------------------------------------------------------------------------------------------------ WITH / recursive CTEs
def _cte_rules(sql: str, s: str, intent: str, F, eng, chosen) -> list:
    C = _C()
    w = _ctes(s)
    if not w:
        return []
    out = []
    names = [x["name"] for x in w["items"]]
    for n in sorted({n for n in names if names.count(n) > 1}):
        out.append((F("error", "cte_dup_name", f"Two WITH queries are called {n}", f"Do WITH queries ka naam {n} hai",
                      _short(sql[:w["main"]], 90), "Each name in one WITH must be different: MySQL/MariaDB stop with error 4004 (Duplicate "
                      "query name), PostgreSQL with 42712 (WITH query name specified more than once). Rename one of them.",
                      "Ek WITH mein har naam alag hona chahiye (MySQL/MariaDB 4004, PostgreSQL 42712). Ek ka naam badlo."), []))
    main_text = s[w["main"]:]
    outer_limit = re.search(r"\blimit\s+\d+|\bfetch\s+first\b", C._no_subqueries(main_text), re.I) is not None
    for it in w["items"]:
        a, b, n = it["a"], it["b"], it["name"]
        body = s[a:b]
        self_ref = re.search(rf"\b(?:from|join)\s+[`\"]?{re.escape(n)}[`\"]?(?![\w$])|,\s*[`\"]?{re.escape(n)}[`\"]?\s+(?:as\s+)?\w+\s*(?:where|$)",
                             body, re.I)
        members = _members(s, a, b)
        anchor = next((m for m in members if not re.search(rf"\b(?:from|join)\s+[`\"]?{re.escape(n)}[`\"]?(?![\w$])", s[m[0]:m[1]], re.I)),
                      None)
        a_items = _items(s, sql, anchor[0], anchor[1]) if anchor else []
        # ---- column list that doesn't match
        if it["cols"] and a_items and not any(x["expr"].strip().endswith("*") for x in a_items) and len(it["cols"]) != len(a_items):
            out.append((F("error", "cte_columns", f"{n}({', '.join(it['cols'])}) names {len(it['cols'])} columns but its query gives "
                          f"{len(a_items)}", f"{n} ke {len(it['cols'])} column naam par query {len(a_items)} columns deti hai",
                          _short(sql[it["name_at"]:b + 1], 90), "The names in brackets after a WITH name are given to the columns "
                          "in order, so their count must match: MySQL/MariaDB stop with error 4002, PostgreSQL with 42P10 (has 1 columns "
                          "available but 2 columns specified). Name exactly the columns the query returns.",
                          "WITH naam ke bracket wale naam columns ko kram se milte hain, ginti barabar honi chahiye (4002 / 42P10)."), []))
        if not self_ref:
            continue
        # ---- reads itself without RECURSIVE
        if not w["recursive"]:
            at = w["with_end"]
            out.append((F("error", "cte_recursive_kw", f"{n} reads itself, so the WITH needs RECURSIVE",
                          f"{n} khud ko padhta hai, isliye WITH RECURSIVE chahiye", _short(sql[it["name_at"]:b + 1], 90),
                          f"Without RECURSIVE, a WITH query can only read tables and the WITH queries before it, so {n} inside its own "
                          f"definition is an unknown table: MySQL/MariaDB stop with error 1146 (Table '{n}' doesn't exist), PostgreSQL "
                          f"with 42P01 (relation \"{n}\" does not exist). Write WITH RECURSIVE (once, at the start: it covers every "
                          f"query in the WITH).",
                          f"RECURSIVE ke bina WITH query sirf tables aur pehle wali WITH queries padh sakti hai, isliye {n} apni hi "
                          f"definition mein anjaan table hai (1146 / 42P01). WITH RECURSIVE likho."),
                        [(at, at, "RECURSIVE ")]))
        rec = [m for m in members if m is not anchor]
        if not rec or not anchor:
            continue
        ra, rb, word = rec[-1]
        rtext = s[ra:rb]
        rflat = C._no_subqueries(rtext)
        rcl = C._clauses(rtext, C._depths(rtext))
        # ---- what the recursive part can't have
        bad = []
        if "select" in rcl and C.AGG.search(rflat[rcl["select"][0]:rcl["select"][1]]) and " over" not in rflat.lower():
            bad.append("an aggregate (MAX, COUNT ...)")
        if "group by" in rcl:
            bad.append("GROUP BY")
        if "order by" in rcl or "limit" in rcl:
            bad.append("ORDER BY / LIMIT")
        if bad:
            out.append((F("error", "cte_recursive_term", f"The recursive part of {n} can't have {bad[0]}",
                          f"{n} ke recursive hisse mein {bad[0]} nahi chal sakta", _short(sql[ra:rb], 100),
                          "The recursive part runs once per round on the rows the last round made, so it can't total, group or sort "
                          "the whole result: MariaDB stops with error 4008 / 1235, PostgreSQL with 42P19 / 0A000 (aggregate functions "
                          "are not allowed in a recursive query's recursive term; ORDER BY in a recursive query is not implemented). "
                          "Do the total or the sort in the main query after the WITH.",
                          "Recursive hissa har round mein pichle round ki rows par chalta hai, isliye wo poore result ka total, group "
                          "ya sort nahi kar sakta (4008 / 1235, 42P19 / 0A000). Total ya sort WITH ke baad main query mein karo."), []))
        cols = it["cols"] or [x["name"] for x in a_items]
        r_items = _items(s, sql, ra, rb)
        has_where = "where" in rcl
        others = re.search(r"\bjoin\b", rflat, re.I) or ("from" in rcl and "," in rflat[rcl["from"][0]:rcl["from"][1]])
        # ---- nothing can end it
        if not has_where and not others and not outer_limit and not re.search(r"\bcycle\b", s[b:w["main"]], re.I):
            edits, bound = [], None
            nm = (re.search(r"\b(\d+)\s*(?:to|-|se|tak|through|till|until|upto|up\s+to)\s*(\d+)\b", intent or "", re.I) or
                  re.search(r"\b(?:up\s*to|upto|till|until|tak)\s+(\d+)\b", intent or "", re.I) or
                  re.search(r"\b(\d+)\s+tak\b", intent or "", re.I))
            if nm:
                bound = int(nm.groups()[-1])
            step = None
            for k, ri in enumerate(r_items):
                sm = re.fullmatch(r"(?:[`\"]?\w+[`\"]?\.)?[`\"]?(\w+)[`\"]?\s*\+\s*(\d+)", ri["expr"].strip())
                if sm and k < len(cols) and sm.group(1).lower() == cols[k]:
                    step = (sm.group(1), int(sm.group(2)))
                    break
            if bound is not None and step:
                cond = f"{step[0]} < {bound}" if step[1] == 1 else f"{step[0]} + {step[1]} <= {bound}"
                end = ra + len(sql[ra:rb].rstrip())
                edits = [(end, end, f" WHERE {cond}")]
            out.append((F("wrong", "cte_no_stop", f"The recursive part of {n} has nothing that ends it",
                          f"{n} ke recursive hisse ko rokne wala kuch nahi hai", _short(sql[ra:rb], 100),
                          f"A recursive query stops only when a round makes no rows, and this part makes a new row from every row, "
                          f"with no WHERE: {_says('recursion_limit', eng)} "
                          + (f"Stop it where you said: WHERE {edits[0][2].split('WHERE ', 1)[1]}." if edits else
                             "Add the condition that ends it in the recursive part, e.g. WHERE n < 100 (or WHERE d < '2024-12-31')."),
                          f"Recursive query tabhi rukti hai jab kisi round mein koi row na bane, aur ye hissa har row se nayi row banata "
                          f"hai, bina WHERE ke: PostgreSQL ruke bina chalta rehta hai, MariaDB 1000 round par chupchaap ruk jaata hai, "
                          f"MySQL error 3636 deta hai. Recursive hisse mein rokne ki condition likho, jaise WHERE n < 100."), edits))
        # ---- a walk through a table with UNION ALL and no depth limit: a loop in the data never ends
        if others and word == "union all" and not re.search(r"\bcycle\b", s[b:w["main"]], re.I):
            depth_limit = re.search(r"(?:[\w.]+)\s*(<|<=)\s*\d+", rflat[rcl["where"][0]:rcl["where"][1]], re.I) if has_where else None
            guard = re.search(r"\bnot\s+like\b|\bposition\s*\(|\bfind_in_set\s*\(|\binstr\s*\(|<>\s*all\b|\bnot\s+in\b|\bany\s*\(",
                              rflat, re.I)
            if not depth_limit and not guard:
                out.append((F("check", "cte_cycle", f"{n} walks the table with no depth limit: a loop in the data never ends",
                              f"{n} bina depth limit ke table mein chalta hai: data mein loop ho to kabhi nahi rukega",
                              _short(sql[ra:rb], 100),
                              "Each round follows the links one step further and stops when a step finds nothing. If the data has a "
                              "loop (A's parent is B and B's parent is A, or a row that is its own parent), UNION ALL keeps going round "
                              "it: PostgreSQL never stops, MariaDB stops after 1000 rounds with repeated rows. Carry a depth column and "
                              "stop it (WHERE depth < 50), or keep the path and skip ids already on it (PostgreSQL 14+ and MariaDB "
                              "10.5+ also have a CYCLE clause).",
                              "Har round link ko ek kadam aage le jaata hai. Data mein loop ho (A ka parent B, B ka parent A) to UNION "
                              "ALL ghoomta rehta hai. Depth column rakho aur WHERE depth < 50 se roko, ya path rakh kar dobara aane wali "
                              "id chhodo."), []))
        # ---- the anchor fixes each column's type: text that grows in the recursive part
        for k, ri in enumerate(r_items):
            if k >= len(a_items):
                break
            grow = re.search(r"\bconcat\s*\(|\|\|", ri["expr"], re.I)
            if not grow:
                continue
            ax = a_items[k]["expr"].strip()
            lit = re.fullmatch(r"'[^']*'", ax)
            colm = re.fullmatch(r"(?:[`\"]?\w+[`\"]?\.)?[`\"]?[A-Za-z_]\w*[`\"]?", ax)
            if not (lit or colm) or ax.lower() in ("null",):
                continue
            if lit and eng == "postgresql":
                continue
            kind = "error" if lit and eng == "mysql" else "check"
            cast = {"mysql": "CHAR(1000)", "postgresql": "TEXT"}.get(eng or "")
            st = a_items[k]["start"] + (len(a_items[k]["text"]) - len(a_items[k]["text"].lstrip()))
            edits = [(st, st + len(ax), f"CAST({ax} AS {cast})")] if cast else []
            out.append((F(kind, "cte_width", f"{n}.{cols[k] if k < len(cols) else ax} grows each round, but its type comes from {ax}",
                          f"{n} ka text har round badhta hai, par uska type {ax} se tay hota hai", _short(ax + " ... " + ri["expr"], 90),
                          f"The anchor (the part before UNION) decides each column's type and size, and the longer text the recursive "
                          f"part builds must fit it. {_says('recursive_text_width', eng)} "
                          + (f"Give the anchor room: CAST({ax} AS {cast})." if cast else
                             "Give the anchor room: CAST(... AS CHAR(1000)) on MySQL/MariaDB, CAST(... AS TEXT) on PostgreSQL."),
                          f"Anchor (UNION se pehle wala hissa) har column ka type aur size tay karta hai; recursive hissa jo lamba text "
                          f"banata hai wo usme fit hona chahiye. Anchor ko jagah do: MySQL/MariaDB par CAST(... AS CHAR(1000)), "
                          f"PostgreSQL par CAST(... AS TEXT)."), edits))
        # ---- MariaDB's / MySQL's 1000-round limit
        if has_where and eng != "postgresql":
            for bm in re.finditer(r"([\w.]+)\s*(<|<=)\s*(\d+)\b", rtext[rcl["where"][0]:rcl["where"][1]]):
                lim = int(bm.group(3))
                if lim <= 1001 or bm.group(1).split(".")[-1].lower() not in cols:
                    continue
                exact = chosen if chosen in ("mysql", "mariadb") else ""
                kind = "wrong" if eng == "mysql" else "check"
                out.append((F(kind, "cte_depth", f"{n} needs about {lim} rounds; MySQL/MariaDB allow 1000 by default",
                              f"{n} ko lagbhag {lim} round chahiye; MySQL/MariaDB default 1000 dete hain", _short(bm.group(0), 60),
                              f"{_rule('mariadb' if exact != 'mysql' else 'mysql', 'recursion_limit').get('says', '') if eng else _says('recursion_limit', None)} "
                              f"Raise the limit for the session first: SET SESSION max_recursive_iterations = {lim + 10} (MariaDB) / "
                              f"SET SESSION cte_max_recursion_depth = {lim + 10} (MySQL 8).",
                              f"MariaDB 1000 round ke baad bina error ruk jaata hai (aadha result), MySQL 8 error 3636 deta hai. Pehle "
                              f"session ki limit badhao: SET SESSION max_recursive_iterations = {lim + 10} (MariaDB) / "
                              f"cte_max_recursion_depth (MySQL)."), []))
                break
    return out


# ------------------------------------------------------------------------------------------------ window functions
def _wrap(sql: str, s: str, w_pos: dict, alias_words: set) -> str | None:
    """SELECT ... WHERE <a window or its name> -> SELECT <names> FROM (SELECT ..., <window> AS wN FROM ... WHERE <the rest>) AS
    ranked WHERE <the window part>: numbered inside, filtered outside (the same on every database). None when the outer names
    can't be written plainly."""
    C = _C()
    w = _ctes(s)
    a = w["main"] if w else 0
    head = sql[:a]
    text, otext = s[a:], sql[a:]
    end = len(text.rstrip().rstrip(";").rstrip())
    text, otext = text[:end], otext[:end]
    if re.search(r"\bunion\b", C._no_subqueries(text), re.I):
        return None
    lead = len(text) - len(text.lstrip())
    d = C._depths(text)
    cl = C._clauses(text, d)
    if not all(k in cl for k in ("select", "from")):
        return None
    clause = "where" if "where" in cl and re.search(r"\bover\b|" + "|".join(rf"\b{re.escape(x)}\b" for x in alias_words or {"\x00"}),
                                                     C._no_subqueries(text[cl["where"][0]:cl["where"][1]]), re.I) else "having"
    if clause not in cl:
        return None
    items = _items(text, otext, 0, len(text))
    names = [it["name"] for it in items]
    if any(not n for n in names) or len(set(names)) != len(names):
        return None
    ca, cb = cl[clause]
    ctext, cotext = text[ca:cb], otext[ca:cb]
    keep, move = [], []
    for part in _conjuncts(ctext):
        off = ctext.index(part)
        orig = cotext[off:off + len(part)]
        if re.search(r"\bover\b", part, re.I) or any(re.search(rf"(?<![\w.]){re.escape(x)}\b", part, re.I) for x in alias_words):
            move.append((part, orig))
        else:
            keep.append(orig)
    if not move or (re.search(r"\bor\b", C._no_subqueries(ctext), re.I) and len(_conjuncts(ctext)) > 1):
        return None
    extra, outer_conds, n = [], [], 0
    for part, orig in move:
        new = orig
        for m in reversed(list(re.finditer(r"\bover\s*\(", part, re.I))):
            ws = [x for x in _windows(part) if x["over"] == m.start()]
            if not ws:
                return None
            x = ws[0]
            n += 1
            extra.append(f"{orig[x['fn_start']:x['end']]} AS w{n}")
            new = new[:x["fn_start"]] + f"w{n}" + new[x["end"]:]
        for qm in re.finditer(r"(?<![\w$])[`\"]?\w+[`\"]?\.[`\"]?(\w+)[`\"]?", new):
            if qm.group(1).lower() not in names:
                return None
        new = re.sub(r"(?<![\w$])[`\"]?\w+[`\"]?\.([`\"]?\w+[`\"]?)", r"\1", new)
        outer_conds.append(new.strip())
    sel_a, sel_b = cl["select"]
    inner_sel = otext[sel_a:sel_b].rstrip() + (", " + ", ".join(extra) if extra else "")
    kw_start = lambda k: re.search(r"\b" + k.replace(" ", r"\s+") + r"\b\s*$", text[:cl[k][0]], re.I).start()  # noqa: E731
    body_end = min([kw_start(k) for k in ("order by", "limit") if k in cl and cl[k][0] > cb - 1] or [len(text)])
    inner = otext[lead:sel_a].strip() + " " + inner_sel.strip() + " " + otext[sel_b:kw_start(clause)].strip()
    if keep:
        inner += f" {clause.upper()} " + " AND ".join(k.strip() for k in keep)
    rest_start = cb
    tail = otext[rest_start:body_end].strip()
    if tail:
        inner += " " + tail
    out = f"SELECT {', '.join(n2 for n2 in names)}\nFROM ({inner}) AS ranked\nWHERE {' AND '.join(outer_conds)}"
    if "order by" in cl and cl["order by"][0] >= cb:
        ob = otext[cl["order by"][0]:cl["limit"][0] if "limit" in cl and cl["limit"][0] > cl["order by"][0] else len(otext)]
        ob = re.sub(r"\blimit\b[\s\S]*$", "", ob, flags=re.I).strip()
        for qm in re.finditer(r"(?<![\w$])[`\"]?\w+[`\"]?\.[`\"]?(\w+)[`\"]?", ob):
            if qm.group(1).lower() not in names:
                return None
        out += "\nORDER BY " + re.sub(r"(?<![\w$])[`\"]?\w+[`\"]?\.([`\"]?\w+[`\"]?)", r"\1", ob)
    lm = re.search(r"\blimit\b[\s\S]*$", otext[cb:], re.I)
    if lm:
        out += "\n" + lm.group(0).strip()
    return head + out


def _window_rules(sql: str, s: str, intent: str, F, eng, levels) -> list:
    C = _C()
    out = []
    wins = _windows(s)
    said = (intent or "").lower()
    if (wins or re.match(r"\s*with\b", s, re.I)) and (OLD_VERSION.search(said) or OLD_VERSION.search(sql)):
        what = "window functions (OVER)" if wins else "WITH"
        out.append((F("error", "win_version", f"{what} need MySQL 8.0+ / MariaDB 10.2+", f"{what} ke liye MySQL 8.0+ / MariaDB 10.2+ chahiye",
                      _short(sql, 90), f"MySQL 5.x and MariaDB 10.0/10.1 don't have {what}: the query stops with a syntax error (1064). "
                      "Upgrade, or write it the old way: a correlated subquery for 'latest per group' (WHERE d = (SELECT MAX(d) ... "
                      "WHERE same group)), a derived table for a WITH, a self-join or a variable for a running total.",
                      f"MySQL 5.x aur MariaDB 10.0/10.1 mein {what} nahi hai (error 1064). Upgrade karo, ya purane tareeke se likho: "
                      "correlated subquery, derived table, self-join."), []))
    main = _ctes(s)
    main_level = (main["main"] if main else 0, len(s))
    wrapped = False
    # ---- in WHERE / HAVING / GROUP BY
    for x in wins:
        lv = _level_of(levels, x["over"])
        clause = _clause_at(s, lv, x["over"])
        if clause in ("where", "having", "group by", "on"):
            edits = []
            if not wrapped and lv[0] in (0, main_level[0]) and clause in ("where", "having"):
                new = _wrap(sql, s, x, set())
                if new:
                    edits, wrapped = [(0, len(sql.rstrip().rstrip(";").rstrip()), new)], True
            fn = x["fn"].upper()
            out.append((F("error", "win_where", f"{fn}() OVER can't be used in {clause.upper()}", f"{clause.upper()} mein {fn}() OVER nahi chalta",
                          _short(sql[x["fn_start"]:x["end"]], 90),
                          f"A window function is worked out after WHERE, GROUP BY and HAVING, on the rows that are left, so it doesn't "
                          f"exist yet there: MariaDB stops with error 4015 (allowed only in SELECT list and ORDER BY clause), "
                          f"PostgreSQL with 42P20 (window functions are not allowed in {clause.upper()}). Number the rows in a "
                          f"subquery (or a WITH) and filter outside it.",
                          f"Window function WHERE, GROUP BY aur HAVING ke baad bachi rows par banta hai, isliye wahan wo hota hi nahi "
                          f"(4015 / 42P20). Subquery (ya WITH) mein number karo aur bahar filter karo."), edits))
    # ---- a window's SELECT name in WHERE of the same query
    for lv in levels:
        text = s[lv[0]:lv[1]]
        cl = C._clauses(text, C._depths(text))
        if "where" not in cl:
            continue
        its = _items(s, sql, lv[0], lv[1])
        wnames = {it["alias"].lower(): it for it in its if it["alias"] and re.search(r"\bover\b", C._no_subqueries(s[it["start"]:it["end"]]), re.I)}
        if not wnames:
            continue
        wtext = C._no_subqueries(text[cl["where"][0]:cl["where"][1]])
        used = sorted({m.group(1).lower() for m in re.finditer(r"(?<![\w.$`\"])([A-Za-z_]\w*)(?![\w$.(])", wtext) if m.group(1).lower() in wnames})
        if not used:
            continue
        edits = []
        if not wrapped and lv[0] in (0, main_level[0]):
            new = _wrap(sql, s, None, set(used))
            if new:
                edits, wrapped = [(0, len(sql.rstrip().rstrip(";").rstrip()), new)], True
        u = used[0]
        out.append((F("error", "win_alias_where", f"WHERE uses {u}, a window function's name from SELECT",
                      f"WHERE mein {u}, jo SELECT ke window function ka naam hai", _short("WHERE " + sql[lv[0] + cl["where"][0]:lv[0] + cl["where"][1]], 80),
                      f"WHERE runs before SELECT, so it can't see SELECT's names, and the window behind {u} doesn't exist yet "
                      f"either (repeating it in WHERE is refused too): MySQL/MariaDB stop with error 1054 (Unknown column '{u}'), "
                      f"PostgreSQL with 42703. Number the rows in a subquery (or a WITH) and filter on {u} outside it.",
                      f"WHERE SELECT se pehle chalta hai, use SELECT ke naam nahi dikhte, aur {u} wala window bhi tab tak nahi bana "
                      f"(1054 / 42703). Subquery (ya WITH) mein number karo aur bahar {u} par filter karo."), edits))
    for x in wins:
        fn = x["fn"]
        F_ = fn.upper()
        args = C._no_subqueries(s[x["args"][0]:x["args"][1]])
        spec_o = sql[x["spec"][0]:x["spec"][1]]
        ev = _short(sql[x["fn_start"]:x["end"]], 90)
        lv = _level_of(levels, x["over"])
        # ---- a window inside a window / an aggregate
        if re.search(r"\bover\b", args, re.I):
            out.append((F("error", "win_nested", f"{F_}() has a window function inside it", f"{F_}() ke andar window function hai", ev,
                          "Window functions are worked out last and can't be nested in another window or an aggregate: MariaDB stops "
                          "with error 4074, PostgreSQL with 42P20 (window function calls cannot be nested). Compute the inner one in a "
                          "subquery (or a WITH) and use its column outside.",
                          "Window function sabse aakhir mein banta hai aur doosre window/aggregate ke andar nahi chalta (4074 / 42P20). "
                          "Andar wala subquery mein nikaalo."), []))
            continue
        has_order = x["order"] is not None
        # ---- a function that needs an order, with none
        if not has_order and fn in NEEDS_ORDER | ARBITRARY | {"row_number"}:
            if fn == "row_number":
                kind = "check"
            elif fn in NEEDS_ORDER:
                kind = "wrong" if eng == "postgresql" else "error"
            else:
                kind = "wrong"
            what = {"lag": "the row before", "lead": "the row after", "first_value": "the first row", "last_value": "the last row",
                    "nth_value": "the n-th row", "ntile": "the groups", "row_number": "the numbers"}.get(fn, "the ranks")
            out.append((F(kind, "win_no_order", f"{F_}() OVER ({_short(spec_o, 30)}) has no ORDER BY",
                          f"{F_}() OVER ({_short(spec_o, 30)}) mein ORDER BY nahi", ev,
                          f"Rows have no order unless the window gives one, so {what} can be any row. "
                          + (_says("window_needs_order", eng) + " " if fn in NEEDS_ORDER else
                             "Each database (and each run) may read the rows in another order: in the lab FIRST_VALUE() OVER () gave "
                             "a different row on MariaDB and on PostgreSQL. " if fn in ARBITRARY else "")
                          + "Add the ORDER BY that says which row comes first, e.g. OVER (PARTITION BY ... ORDER BY order_date, id).",
                          f"Window ORDER BY na de to rows ka koi kram nahi, isliye {what} koi bhi row ho sakti hai. MariaDB LAG/LEAD/RANK "
                          f"ko ORDER BY ke bina mana karta hai (4018), PostgreSQL koi bhi row deta hai. ORDER BY likho jo batata hai "
                          f"kaunsi row pehle aati hai."), []))
            continue
        # ---- LAST_VALUE / NTH_VALUE with the default frame
        if fn in ("last_value", "nth_value") and has_order and not x["frame"]:
            edits = [] if x["named"] else [(x["spec"][1], x["spec"][1], " ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING")]
            kind = "wrong" if fn == "last_value" else "check"
            out.append((F(kind, "win_last_value", f"{F_}() stops at the current row (the default frame)",
                          f"{F_}() current row par ruk jaata hai (default frame)", ev,
                          f"With ORDER BY and no frame, a window only sees the rows from the start up to the current row (and the rows "
                          f"that tie with it): RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW. So {F_}() gives "
                          + ("the current row's own value, not the last of its group" if fn == "last_value" else
                             "NULL until the n-th row is reached") + " (MariaDB and PostgreSQL alike). Widen the frame: ... ORDER BY "
                          "... ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING.",
                          f"ORDER BY ho aur frame na ho to window shuru se current row tak hi dekhta hai, isliye {F_}() group ki aakhri "
                          f"value nahi, current row ki value deta hai. Frame badhao: ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED "
                          f"FOLLOWING."), edits))
            continue
        order_keys = [k.strip() for _o, k in C._split_top(sql[x["order"][0]:x["order"][1]])] if has_order else []
        last_key = order_keys[-1] if order_keys else ""
        unique = bool(last_key) and _is_key(re.sub(r"[`\"]", "", last_key), s, lv)
        key = _key_of(s, lv)
        # ---- said 'per X' with no PARTITION BY
        if not x["partition"] and not x["named"]:
            text = s[lv[0]:lv[1]]
            if "group by" not in C._clauses(text, C._depths(text)):
                names = [m.group(1) for m in re.finditer(r"(?<![\w$])([A-Za-z_][\w$]*)", text) if m.group(1).lower() not in C.KW]
                done = False
                for noun in _said_partitions(intent):
                    g = C._group_column(noun, names)
                    if not g or any(_flat(k).lower().split(".")[-1].split()[0] == g.lower() for k in order_keys):
                        continue
                    q = next((m.group(1) for m in re.finditer(rf"([A-Za-z_]\w*)\.{re.escape(g)}\b", text)), None)
                    gref = f"{q}.{g}" if q else g
                    at = x["spec"][0] + (len(spec_o) - len(spec_o.lstrip()))
                    out.append((F("wrong", "win_partition_said", f"{F_}() runs over all rows, not per {noun}",
                                  f"{F_}() saari rows par chalta hai, har {noun} ka alag nahi", ev,
                                  f"You asked for it per {noun}, but the window has no PARTITION BY, so it runs over every row as one "
                                  f"group (one running total / one numbering for the whole table). PARTITION BY {gref} restarts it for "
                                  f"each {noun}.",
                                  f"Aapne har {noun} ka alag maanga, par window mein PARTITION BY nahi, isliye ye poori table ko ek group "
                                  f"maanta hai. PARTITION BY {gref} har {noun} par naye sire se shuru karta hai."),
                                [(at, at, f"PARTITION BY {gref} ")]))
                    done = True
                    break
                if done:
                    continue
        # ---- ROW_NUMBER where ties matter
        if fn == "row_number" and has_order:
            nth = NTH_SAID.search(said)
            if TIES_SAID.search(said) and not nth:
                continue                                   # 'keep the ties': the ranking rule (sqlcorrect, topic 'ties') says it
            if nth:
                new = "DENSE_RANK" if nth else "RANK"
                out.append((F("wrong", "win_rank_ties", f"ROW_NUMBER() gives tied rows different numbers; you meant {new}()",
                              f"ROW_NUMBER() barabar rows ko alag number deta hai; {new}() chahiye", ev,
                              ("ROW_NUMBER() never repeats a number, so with equal values one row gets 1 and the other 2 (which one is "
                               "arbitrary). " + (f"For the '{nth.group(0)}' value, equal values must share a place: DENSE_RANK() gives "
                                                 "1, 1, 2 and the 2nd highest is the next different value." if nth else
                                                 "RANK() gives every tied row the same number (1, 1, 3), so all of them are kept."))
                              + " The same on MariaDB and PostgreSQL.",
                              "ROW_NUMBER() kabhi number repeat nahi karta, isliye barabar values mein ek ko 1 aur doosri ko 2 milta hai. "
                              + ("DENSE_RANK() barabar values ko ek hi jagah deta hai (1, 1, 2)." if nth else
                                 "RANK() barabar rows ko ek hi number deta hai (1, 1, 3), sab rakhi jaati hain.")),
                            [(x["fn_start"], x["fn_start"] + len("row_number"), new)]))
                continue
            if not unique:
                edits = [(x["order"][1] - (len(sql[x["order"][0]:x["order"][1]]) - len(sql[x["order"][0]:x["order"][1]].rstrip())),
                          x["order"][1] - (len(sql[x["order"][0]:x["order"][1]]) - len(sql[x["order"][0]:x["order"][1]].rstrip())),
                          f", {key}")] if key and not x["named"] else []
                out.append((F("check", "win_rank_ties", f"ROW_NUMBER() ORDER BY {_short(last_key, 30)}: tied rows are numbered in any order",
                              f"ROW_NUMBER() ORDER BY {_short(last_key, 30)}: barabar rows ka number kisi bhi kram mein", ev,
                              f"When two rows have the same {_short(last_key, 30)}, ROW_NUMBER() still gives them 1 and 2, in whatever "
                              f"order the database reads them: in the lab MariaDB and PostgreSQL numbered the same two tied orders the "
                              f"other way round, so 'rn = 1' can pick a different row on each. "
                              + (f"Add a tie-breaker that names one row: ORDER BY {_short(', '.join(order_keys), 40)}, {key}; or use "
                                 f"RANK() to keep all tied rows." if key else "Add a tie-breaker that names one row (the id), or use "
                                                                             "RANK() to keep all tied rows."),
                              f"Do rows ki {_short(last_key, 30)} barabar ho to bhi ROW_NUMBER() unhe 1 aur 2 deta hai, kisi bhi kram "
                              f"mein; MariaDB aur PostgreSQL ne lab mein ulta number diya. Ek row batane wala tie-breaker (id) jodo, "
                              f"ya sab barabar rows ke liye RANK()."), edits))
            continue
        # ---- a running total over a sort with ties (the default RANGE frame)
        if fn in AGG_NAMES and has_order and not x["frame"] and not unique:
            sp = sql[x["order"][0]:x["order"][1]]
            end = x["order"][1] - (len(sp) - len(sp.rstrip()))
            edits = [(end, end, f", {key} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW")] if key and not x["named"] else []
            out.append((F("check", "win_running_ties", f"{F_}() OVER (ORDER BY {_short(last_key, 30)}): tied rows get the same running value",
                          f"{F_}() OVER (ORDER BY {_short(last_key, 30)}): barabar rows ko ek hi running value milti hai", ev,
                          f"With ORDER BY and no frame the window is RANGE ... CURRENT ROW, which counts every row that ties with the "
                          f"current one: rows with the same {_short(last_key, 30)} all get the total up to the last of them (in the lab, "
                          f"three items of order 1 all showed 4 instead of 1, 2, 4, on MariaDB and PostgreSQL). If you want a "
                          f"row-by-row running total, "
                          + (f"add a tie-breaker and a ROWS frame: ORDER BY {_short(', '.join(order_keys), 40)}, {key} ROWS BETWEEN "
                             f"UNBOUNDED PRECEDING AND CURRENT ROW." if key else "add a tie-breaker (the id) and ROWS BETWEEN UNBOUNDED "
                                                                                "PRECEDING AND CURRENT ROW.")
                          + " If one total per value is what you want, it is right as it is.",
                          f"ORDER BY ho aur frame na ho to window RANGE ... CURRENT ROW hota hai, jo current row se barabar wali saari "
                          f"rows gin leta hai: same {_short(last_key, 30)} wali sab rows ko ek hi total milta hai. Row-by-row total ke "
                          f"liye tie-breaker (id) aur ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW jodo."), edits))
    return out


_PARTITION_SAID = [r"\b(?:per|within\s+(?:each|every|its|their|his|her|the\s+same)|for\s+(?:each|every)|in\s+(?:each|every)|har|"
                   r"partitioned\s+by|separately\s+for\s+each)\s+(?:own\s+)?([a-z_]+)",
                   r"\b([a-z_]+)[\s-]?wise\b", r"\b(?:apn[ieaā]|its|their)\s+(?:own\s+)?([a-z_]+)\s+(?:mein|me|ke\s+andar|ke\s+liye)\b"]
_NOT_PARTS = {"row", "rows", "record", "records", "one", "time", "case", "own", "the", "a", "an", "query", "table", "value", "day",
              "percent", "way", "line", "page", "step", "round", "result", "number", "running", "date", "month", "year", "week"}


def _said_partitions(intent: str) -> list[str]:
    out = []
    for rx in _PARTITION_SAID:
        for m in re.finditer(rx, (intent or "").lower()):
            w = m.group(1)
            if w not in _NOT_PARTS and len(w) > 2:
                out.append(w)
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------------------------------------ entry
def windows(sql: str, s: str, intent: str, F) -> list:
    """CASE, WITH and window findings for any statement: [(Finding, [edits])]."""
    if not re.search(r"\bcase\b|\bwith\b|\bover\b", s, re.I):
        return []
    eng, chosen = _engine(sql)
    levels = _levels(s)
    out = []
    for part in (_case_rules(sql, s, intent, F, eng, levels), _cte_rules(sql, s, intent, F, eng, chosen),
                 _window_rules(sql, s, intent, F, eng, levels)):
        out += part
    return out


def window_names(s: str) -> set:
    """SELECT names of window functions at any level (the plain 'name made in SELECT' rule leaves them to win_alias_where)."""
    C = _C()
    out = set()
    for lv in _levels(s):
        for it in _items(s, s, lv[0], lv[1]):
            if it["alias"] and re.search(r"\bover\b", C._no_subqueries(it["text"]), re.I):
                out.add(it["alias"].lower())
    return out
