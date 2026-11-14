"""Subquery reasoning (night 4 of the curriculum), shared by every database (fundamentals 'subquery_shape', 'correlation',
'null_logic' and 'cardinality' in app/dbknow/core/query_fundamentals.json).

A subquery is a small query with its own FROM, and three facts decide what it gives:
- its SHAPE: where it stands says how much it may return. After = < > or in the SELECT list it must give one row and one
  column; after IN / NOT IN / ANY / ALL one column and any number of rows; after EXISTS only "is there at least one row"
  matters. An aggregate without GROUP BY always gives exactly one row (COUNT(*) gives 0, MAX gives NULL), even over nothing.
- its SCOPE: a name is looked up in the subquery's own FROM first and only then in the query around it. So a subquery that
  never mentions the outer row gives the same answer for every row, and `orders.customer_id` inside a subquery that reads
  orders again means the inner orders, not the outer one (x = x, always true).
- NULLs: x NOT IN (list with a NULL) is never true, x IN (..., NULL) never matches an empty x, a NULL x is dropped by NOT IN
  but kept by NOT EXISTS, > ALL (...) is never true when the list holds a NULL and always true when the list is empty, and a
  correlated SUM with no rows is NULL, not 0.
From these:
- sure from the text alone -> 'error' / 'wrong': EXISTS (SELECT COUNT(*) ...) is always true; x = x inside a correlated
  subquery; a subquery that returns the outer row's own column; x IN (SELECT MAX(y) ... GROUP BY g) loses which group the
  maximum belongs to; two columns where one is expected; NOT IN (..., NULL).
- depends on the data or on what was meant -> 'check' with the rewrite offered ("If that's what you meant"): EXISTS with no
  link to the outer row, a scalar subquery in SELECT that can return several rows, a correlated SUM that is NULL for nothing,
  LIMIT without ORDER BY inside a subquery, > ALL with NULLs or no rows, NOT IN on a column that may be NULL, IN (..., NULL),
  a JOIN used only to keep parents that have a child (each parent repeated once per child; EXISTS keeps it once).
- engine facts (standing rule B, from the dialect files' query_rules): LIMIT inside IN / ANY / ALL (MySQL and MariaDB refuse
  it, PostgreSQL runs it) and a subquery in FROM with no name (MySQL and MariaDB refuse it, PostgreSQL 16 runs it).
Every rule was reproduced on MariaDB 10.11 and PostgreSQL 16 (tests/practice/test_night4.py has the lab table).
Returns [(Finding, [edits])], edit = (start, end, new text) on the original query; (0, len(sql), text) rewrites it whole.
"""

import re

from app.agents.sqlverify import keep as schema_keep

# the engines each rule was reproduced on (tests/practice/test_night4.py, LAB) -- standing rule B
PROVEN = {k: ["mariadb", "postgresql"] for k in (
    "exists_agg", "exists_uncorr", "sub_outer_col", "corr_self", "in_grouped_agg", "sub_columns", "scalar_rows", "sub_sum_null",
    "sub_limit_order", "in_limit", "derived_alias", "all_null", "in_list_null", "not_in_outer", "semi_join", "sub_key")}

NAME = r"[`\"]?[A-Za-z_][\w$]*[`\"]?"
QNAME = rf"{NAME}(?:\.{NAME})?"
OPS = r"<>|!=|<=|>=|=|<|>"
AGG_ONE = re.compile(r"^\s*(count|sum|avg|min|max|group_concat|string_agg|array_agg|bool_and|bool_or|every)\s*\(", re.I)
ANY_AGG = re.compile(r"\b(count|sum|avg|min|max|group_concat|string_agg|array_agg|stddev|variance|bool_and|bool_or|every)\s*\(", re.I)
NULL_FN = re.compile(r"\b(coalesce|ifnull|nvl|isnull)\s*\(\s*$", re.I)


def _C():
    from app.agents import sqlcorrect as C

    return C


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _short(t: str, n: int = 110) -> str:
    t = _flat(t)
    return t if len(t) <= n else t[:n - 3] + "..."


def _uq(n: str) -> str:
    return (n or "").strip('`"').lower()


# ------------------------------------------------------------------------------------------------ reading the blocks
def _scope(text: str):
    """The FROM sources of one query level (text is masked, positions relative to it). UPDATE t / DELETE FROM t count too."""
    C = _C()
    dep = C._depths(text)
    cl = C._clauses(text, dep)
    srcs = C._sources(text, dep, *cl["from"]) if "from" in cl else []
    um = re.match(rf"\s*update\s+(?:low_priority\s+)?(?:ignore\s+)?({NAME})(?:\s+(?:as\s+)?({NAME}))?", text, re.I)
    if um:
        nm = _uq(um.group(1))
        al = _uq(um.group(2)) if um.group(2) and _uq(um.group(2)) not in C.KW and _uq(um.group(2)) != "set" else nm
        srcs = [C.Source(kind="from", name=nm, alias=al)] + srcs
    return srcs, cl, dep


def _own_names(sub: str) -> set:
    """Every table name and alias defined anywhere inside a block: its own scope (and its nested ones)."""
    C = _C()
    out = set()
    for m in re.finditer(rf"\b(?:from|join|update)\s+(?:lateral\s+)?({NAME}(?:\.{NAME})?)(?:\s+(?:as\s+)?({NAME}))?", sub, re.I):
        out.add(_uq(m.group(1).split(".")[-1]))
        if m.group(2) and _uq(m.group(2)) not in C.KW:
            out.add(_uq(m.group(2)))
    for m in re.finditer(rf"\)\s*(?:as\s+)?({NAME})", sub, re.I):
        if _uq(m.group(1)) not in C.KW:
            out.add(_uq(m.group(1)))
    return out


def blocks(s: str) -> list:
    """Every (SELECT ...) in the masked query: where it is, where it stands (its context), and the scope around it."""
    C = _C()
    spans = []
    for m in re.finditer(r"\(\s*select\b", s, re.I):
        a = m.start()
        spans.append((a, C._close(s, a)))
    out = []
    for a, b in spans:
        parents = [(x, y) for x, y in spans if x < a and b <= y]
        pa, pb = max(parents) if parents else (-1, len(s))
        ptext = s[pa + 1:pb]
        psrcs, pcl, _pd = _scope(ptext)
        rel = a - (pa + 1)
        before = s[:a]
        ctx, left, op, quant = "other", None, None, None
        if re.search(r"\bnot\s+exists\s*$", before, re.I):
            ctx = "not_exists"
        elif re.search(r"\bexists\s*$", before, re.I):
            ctx = "exists"
        elif (lm := re.search(rf"(\)|{QNAME})\s+(not\s+)?in\s*$", before, re.I)) and _uq(lm.group(1)) not in ("where", "and", "or", "on"):
            ctx, left = ("not_in" if lm.group(2) else "in"), (lm.start(1), lm.end(1))
        elif lm := re.search(rf"(\)|{QNAME})\s*({OPS})\s*(any|some|all)\s*$", before, re.I):
            ctx, left, op, quant = "quant", (lm.start(1), lm.end(1)), lm.group(2), lm.group(3).lower()
        elif lm := re.search(rf"(\)|{QNAME})\s*({OPS})\s*$", before, re.I):
            ctx, left, op = "compare", (lm.start(1), lm.end(1)), lm.group(2)
        elif re.search(r"\bas\s*$", before, re.I) and not parents:
            ctx = "cte"
        elif "from" in pcl and pcl["from"][0] - 5 <= rel < pcl["from"][1]:
            ctx = "lateral" if re.search(r"\blateral\s*$", before, re.I) else "derived"
        elif "select" in pcl and pcl["select"][0] <= rel < pcl["select"][1]:
            ctx = "scalar_select"
        outer_names = set()
        for x, y in parents + [(-1, len(s))]:
            ss, _c, _d = _scope(s[x + 1:y])
            for src in ss:
                outer_names |= {_uq(src.alias), _uq(src.name.split(".")[-1]) if src.name else ""}
        outer_names.discard("")
        sub = s[a + 1:b]
        own = _own_names(sub)
        ssrcs, scl, sdep = _scope(sub)
        out.append({"a": a, "b": b, "sub": sub, "o": a + 1, "ctx": ctx, "left": left, "op": op, "quant": quant,
                    "psrcs": psrcs, "poff": pa + 1, "outer": outer_names - own, "own": own, "srcs": ssrcs, "cl": scl,
                    "dep": sdep, "nested": bool(parents)})
    return out


def _items(blk) -> list:
    """The SELECT list of a block: [(start in the query, text)], with a leading DISTINCT left out."""
    C = _C()
    if "select" not in blk["cl"]:
        return []
    x, y = blk["cl"]["select"]
    body = blk["sub"][x:y]
    dm = re.match(r"\s*distinct\b", body, re.I)
    skip = dm.end() if dm else 0
    out = []
    for off, t in C._split_top(body[skip:]):
        if t.strip():
            out.append((blk["o"] + x + skip + off + (len(t) - len(t.lstrip())), t.strip()))
    return out


def _outer_refs(blk) -> list:
    """Qualified names in the block whose qualifier belongs to the query around it: the correlation."""
    C = _C()
    return [(q, c, p) for q, c, p in C._qrefs(blk["sub"], blk["o"]) if _uq(q) in blk["outer"]]


def _bare_cols(text: str) -> list:
    C = _C()
    t = re.sub(r"'[^']*'", "''", text)
    return [w for w in C._bare(t, set()) if not re.fullmatch(r"\d+", w)]


def _where_text(blk):
    cl = blk["cl"]
    if "where" not in cl:
        return None, None
    x, y = cl["where"]
    return blk["o"] + x, blk["sub"][x:y]


def _engine():
    from app.agents import sqlbasics

    return sqlbasics.engine_seen("")


def _refused(key: str):
    """For an engine-specific rule: (kind or None to stay quiet, text naming the engines). Engine facts from the dialect files."""
    from app.agents import sqlbasics

    eng = _engine()
    my, pg = sqlbasics.rules("mysql").get(key, {}), sqlbasics.rules("postgresql").get(key, {})
    if eng == "mysql":
        return (None if my.get("behaviour") == "works" else "error"), my.get("says", "")
    if eng == "postgresql":
        return (None if pg.get("behaviour") == "works" else "error"), pg.get("says", "")
    return "check", f"{my.get('says', '')} {pg.get('says', '')}".strip()


def _engine_of(sql: str):
    from app.agents import sqlbasics

    return sqlbasics.engine_seen(sql)


# ------------------------------------------------------------------------------------------------ the rules
def subqueries(sql: str, s: str, intent: str, F) -> list:
    """Every statement (SELECT, UPDATE, DELETE): the subquery rules that need only the text around each (SELECT ...)."""
    C = _C()
    out = []
    said = (intent or "").lower()
    eng = _engine_of(sql)

    for blk in blocks(s):
        sub, o, ctx = blk["sub"], blk["o"], blk["ctx"]
        cl = blk["cl"]
        items = _items(blk)
        ev = _short(sql[blk["a"]:blk["b"] + 1])
        grouped = "group by" in cl or "having" in cl
        has_limit = "limit" in cl or re.search(r"\bfetch\s+first\b", sub, re.I) is not None
        orefs = _outer_refs(blk)
        left_txt = sql[blk["left"][0]:blk["left"][1]] if blk["left"] else ""
        left_tuple = left_txt.endswith(")")

        # ---- S1. EXISTS (SELECT COUNT(*) ...): an aggregate without GROUP BY always gives one row -> always true / false
        if ctx in ("exists", "not_exists") and len(items) == 1 and AGG_ONE.match(items[0][1]) and not grouped \
                and not re.search(r"\bover\s*\(", items[0][1], re.I) and not has_limit:
            p, t = items[0]
            always = "true" if ctx == "exists" else "false"
            word = "EXISTS" if ctx == "exists" else "NOT EXISTS"
            fn = AGG_ONE.match(t).group(1).upper()
            out.append((F("wrong", "exists_agg", f"{word} (SELECT {fn}(...) ...) is always {always}",
                          f"{word} (SELECT {fn}(...) ...) hamesha {always} hai", ev,
                          f"A total without GROUP BY always gives exactly one row, even when nothing matches ({fn} of no rows is "
                          f"{'0' if fn == 'COUNT' else 'NULL'}, but it is still a row), and EXISTS only asks whether there is a row. "
                          f"So {word} is {always} for every row, on every database. To ask 'is there a matching row', select a "
                          f"constant: {word} (SELECT 1 FROM ... WHERE ...); to compare the count, write (SELECT COUNT(*) ...) > 0.",
                          f"GROUP BY ke bina total hamesha ek row deta hai, kuch match na ho tab bhi; EXISTS sirf row hai ya nahi dekhta. "
                          f"Isliye {word} har row ke liye {always} hai. Match hai ya nahi poochna ho to {word} (SELECT 1 FROM ... WHERE ...) "
                          f"likho; count compare karna ho to (SELECT COUNT(*) ...) > 0."),
                        [(p, p + len(t), "1")]))
            continue

        # ---- S2. EXISTS that never mentions the outer row: the same answer for every row
        if ctx in ("exists", "not_exists") and not orefs and blk["srcs"]:
            wa, wt = _where_text(blk)
            bare = _bare_cols(C._no_subqueries(wt)) if wt else []
            if not bare:
                inner = blk["srcs"][0]
                outer = blk["psrcs"][0] if blk["psrcs"] else None
                hint = ""
                if outer is not None and inner.name and outer.name:
                    from app.agents.sqljoins import _fk

                    hint = f" (for example {inner.alias}.{_fk(outer.name)} = {outer.alias}.id)"
                out.append((F("check", "exists_uncorr", f"The {'NOT ' if ctx == 'not_exists' else ''}EXISTS subquery never mentions the outer row",
                              f"{'NOT ' if ctx == 'not_exists' else ''}EXISTS wali subquery bahar ki row ka zikr hi nahi karti", ev,
                              f"Nothing inside it points at the row being checked, so it gives the same answer for every row: all rows "
                              f"pass when {inner.name or 'it'} has any matching row at all, none when it has none. To keep only the rows "
                              f"that have a match of their own, tie the subquery to the outer row in its WHERE{hint}.",
                              f"Iske andar kuch bhi us row ki taraf ishara nahi karta, isliye har row ka ek hi jawab: ya sab rows ya koi "
                              f"nahi. Har row ka apna match chahiye to subquery ke WHERE mein use bahar ki row se jodo{hint}."), []))

        # ---- S3. x IN (SELECT o.col FROM t ...) where o is the OUTER table: the subquery returns the row's own value
        if ctx in ("in", "not_in", "compare", "quant") and len(items) == 1 and blk["srcs"]:
            im = re.fullmatch(rf"({NAME})\.({NAME})", items[0][1])
            if im and _uq(im.group(1)) in blk["outer"]:
                q, c = im.group(1), im.group(2)
                out.append((F("wrong", "sub_outer_col", f"The subquery returns {q}.{c}, a column of the outer row",
                              f"Subquery {q}.{c} lautati hai, jo bahar ki row ka column hai", ev,
                              f"{q} is not in the subquery's own FROM, so {q}.{c} is the value of the row being checked, repeated once per "
                              f"row of {blk['srcs'][0].name or 'the subquery'}: the test compares the row with itself (true for "
                              f"every row whenever the subquery has rows). Return a column of the subquery's own table "
                              f"({blk['srcs'][0].alias}.<column>).",
                              f"{q} subquery ke FROM mein nahi hai, isliye {q}.{c} usi row ki value hai: row apne aap se compare hoti hai. "
                              f"Subquery ki apni table ka column lautao ({blk['srcs'][0].alias}.<column>)."), []))
                continue

        # ---- S3b. c.id IN (SELECT id FROM orders): the subquery's own id (an order id) compared with another table's id
        if ctx in ("in", "not_in") and len(items) == 1 and len(blk["srcs"]) == 1 and left_txt and not grouped and blk["psrcs"]:
            from app.agents.sqljoins import _fk

            im = re.fullmatch(rf"(?:({NAME})\.)?({NAME})", items[0][1])
            lm = re.fullmatch(rf"(?:({NAME})\.)?({NAME})", left_txt)
            inner = blk["srcs"][0]
            if im and lm and _uq(im.group(2)) == "id" and _uq(lm.group(2)) == "id" and inner.name:
                lq = _uq(lm.group(1)) if lm.group(1) else ""
                outer = next((x for x in blk["psrcs"] if not lq or lq in (_uq(x.alias), _uq(x.name))), None)
                from app.agents import sqlcorrect as _SC

                made = set(_SC.BLOCK_COLS.get() or {}) | set(_SC.BLOCK_KEYS.get() or {})
                if _uq(inner.name).lower() in made:
                    continue                                 # night 15: a CTE of this query: its id is whatever it chose to pass on
                if outer is not None and outer.name and _uq(outer.name) != _uq(inner.name):
                    fk = _fk(outer.name)
                    p = items[0][0]
                    qual = f"{im.group(1)}." if im.group(1) else ""
                    out.append((F("check", "sub_key", f"{left_txt} IN (SELECT id FROM {inner.name}): {outer.name} ids compared with {inner.name} ids",
                                  f"{left_txt} IN (SELECT id FROM {inner.name}): {outer.name} ki id {inner.name} ki id se compare", ev,
                                  f"The id of a {inner.name} row numbers that row, not the {outer.name} it belongs to, so this matches "
                                  f"{outer.name} whose number happens to be used by some {inner.name} row. If you meant the {outer.name} "
                                  f"that {inner.name} points at, select its link column: (SELECT {qual}{fk} FROM {inner.name} ...).",
                                  f"{inner.name} ki id us row ka number hai, {outer.name} ka nahi. {inner.name} jis {outer.name} se juda hai "
                                  f"wo chahiye to uska link column lo: (SELECT {qual}{fk} FROM {inner.name} ...)."),
                                [(p, p + len(items[0][1]), f"{qual}{fk}")]))
                    continue

        # ---- S4. x = x inside a correlated subquery: both sides are the inner table (an alias twice, or one table name twice)
        wa, wt = _where_text(blk)
        if wt and blk["srcs"]:
            mine = {_uq(x.alias): x for x in blk["srcs"] if x.alias}
            for m in re.finditer(rf"(?<![\w.`\"$])({QNAME})\s*=\s*({QNAME})(?![\w.(`\"])", C._no_subqueries(wt)):
                l, r = m.group(1), m.group(2)
                lq, lc = l.rsplit(".", 1) if "." in l else ("", l)
                rq, rc = r.rsplit(".", 1) if "." in r else ("", r)
                if _uq(lc) != _uq(rc) or not (lq or rq) or _uq(lc) in C.KW:
                    continue
                qs = {_uq(q) for q in (lq, rq) if q}
                if len(qs) != 1 or not qs <= set(mine) or (not (lq and rq) and len(mine) != 1):
                    continue                                   # one side is the outer table: a real correlation
                src = mine[next(iter(qs))]
                if not src.name:
                    continue
                twin = next((x for x in blk["psrcs"] if x.name and _uq(x.name) == _uq(src.name)), None)
                side = (wa + m.start(2), r) if rq else (wa + m.start(1), l)      # the side meant to be the outer row
                edits = []
                if twin is not None and _uq(twin.alias) != _uq(src.alias):
                    edits = [(side[0], side[0] + len(side[1]), f"{twin.alias}.{rc}")]
                elif twin is not None:                         # FROM orders ... (SELECT ... FROM orders WHERE x = orders.x)
                    head = re.search(rf"\b(?:from|join)\s+[`\"]?{re.escape(src.name)}[`\"]?(?![\w$])", sub, re.I)
                    if head:
                        edits = [(o + head.end(), o + head.end(), " AS sub")]
                        edits += [(p, p + len(q), "sub") for q, _c, p in C._qrefs(sub, o) if _uq(q) == _uq(src.name) and p != side[0]]
                outer_name = (twin.alias or twin.name) if twin is not None else None
                out.append((F("wrong", "corr_self", f"{_flat(m.group(0))} inside the subquery compares {src.name} with itself",
                              f"Subquery ke andar {_flat(m.group(0))} {src.name} ko khud se compare karta hai", ev,
                              f"Names inside a subquery are looked up in its own FROM first, so both sides are the subquery's own "
                              f"{src.name} row: the condition is true for every row that has a value, and the subquery is NOT tied to "
                              f"the outer row (it gives one answer for the whole table, e.g. the overall average). Point one side at the "
                              f"outer row" + (f" ({outer_name}.{rc})" if outer_name else "")
                              + (f", and give the inner {src.name} a name of its own (FROM {src.name} AS sub)"
                                 if twin is not None and _uq(twin.alias) == _uq(src.alias) else "") + ".",
                              f"Subquery ke andar naam pehle uske apne FROM mein dhoondhe jaate hain: dono taraf andar wali {src.name} "
                              f"row hai, isliye condition hamesha true hai aur subquery bahar ki row se judti hi nahi. Ek taraf bahar ki "
                              f"row ko do" + (f" ({outer_name}.{rc})" if outer_name else "") + "."),
                            edits))
                break

        # ---- S5. x IN (SELECT MAX(y) ... GROUP BY g): matches ANY group's maximum, not the row's own group's
        if ctx in ("in", "not_in") and not left_tuple and left_txt and len(items) == 1 and "group by" in cl and not orefs:
            am = re.fullmatch(r"(max|min)\s*\(\s*(" + QNAME + r")\s*\)(?:\s+(?:as\s+)?" + NAME + r")?", items[0][1], re.I)
            gx, gy = cl["group by"]
            gtext = sub[gx:gy].strip()
            if am and re.fullmatch(QNAME, gtext) and "having" not in cl:
                g = gtext
                gname = g.split(".")[-1]
                lq = left_txt.rsplit(".", 1)[0] + "." if "." in left_txt else ""
                p, t = items[0]
                fn = am.group(1).upper()
                new_left = f"({lq}{gname}, {left_txt})"
                out.append((F("wrong", "in_grouped_agg", f"{left_txt} IN (SELECT {fn}(...) ... GROUP BY {gname}) forgets which {gname} each {fn.lower()} belongs to",
                              f"{left_txt} IN (SELECT {fn}(...) ... GROUP BY {gname}) bhool jaata hai kaunsa {fn.lower()} kis {gname} ka hai", ev,
                              f"The subquery gives a list of {fn.lower()} values, one per {gname}, without saying whose they are, so a row "
                              f"passes when its value equals the {fn.lower()} of ANY {gname}, not of its own (on every database). Compare "
                              f"the pair: {new_left} IN (SELECT {g}, {fn}(...) ... GROUP BY {g}), or tie the subquery to the row "
                              f"(a correlated {fn}).",
                              f"Subquery har {gname} ka {fn.lower()} deti hai par ye nahi batati kiska, isliye row tab bhi aa jaati hai jab "
                              f"uski value KISI bhi {gname} ke {fn.lower()} ke barabar ho. Jodi compare karo: {new_left} IN "
                              f"(SELECT {g}, {fn}(...) ... GROUP BY {g})."),
                            [(blk["left"][0], blk["left"][1], new_left), (p, p, f"{g}, ")]))
                continue

        # ---- S6. two columns where one is expected: x IN (SELECT a, b ...), x = (SELECT a, b ...)
        if ctx in ("in", "not_in", "compare", "quant") and not left_tuple and len(items) >= 2:
            lname = _uq(left_txt.split(".")[-1])
            keep = next(((p, t) for p, t in items if _uq(re.split(r"\s+as\s+|\s+", t.split(".")[-1].strip(), flags=re.I)[0]) == lname), None)
            edits = []
            if keep:
                x, y = blk["cl"]["select"]
                body = sub[x:y]
                dm = re.match(r"\s*distinct\b", body, re.I)
                a0 = o + x + (dm.end() if dm else 0)
                edits = [(a0, o + y, " " + keep[1] + " ")]
            out.append((F("error", "sub_columns", f"{left_txt} {'IN' if ctx in ('in', 'not_in') else (blk['op'] or '=')} (SELECT ...) gets {len(items)} columns; it takes one",
                          f"{left_txt} ke saamne subquery {len(items)} columns deti hai; ek hi chahiye", ev,
                          f"One value is compared with each row of the subquery, so the subquery must return one column. MySQL/MariaDB "
                          f"stop with error 1241 (Operand should contain 1 column(s)), PostgreSQL with 42601 (subquery has too many "
                          f"columns). Keep only the column to compare"
                          + (f": {keep[1]}." if keep else ", or compare a pair: (a, b) IN (SELECT a, b ...)."),
                          f"Ek value subquery ki har row se compare hoti hai, isliye subquery ek hi column de. MySQL/MariaDB error 1241, "
                          f"PostgreSQL 42601. Sirf compare wala column rakho" + (f": {keep[1]}." if keep else ", ya jodi compare karo.")),
                        edits))
            continue

        # ---- S7. a scalar subquery in SELECT that can return several rows (no total, no LIMIT, tied on a non-key column)
        if ctx == "scalar_select" and len(items) == 1 and not AGG_ONE.match(items[0][1]) and not ANY_AGG.search(C._no_subqueries(items[0][1])) \
                and not grouped and not has_limit and orefs and len(blk["srcs"]) == 1:
            wa, wt = _where_text(blk)
            inner = blk["srcs"][0]
            ia = {_uq(inner.alias), _uq(inner.name)}
            keyed = False
            many = None
            from app.agents.sqljoins import key_names

            for m in re.finditer(rf"({QNAME})\s*=\s*({QNAME}|'x*'|\d+)", C._no_subqueries(wt or "")):
                for side in (m.group(1), m.group(2)):
                    if "." in side and _uq(side.split(".")[0]) in ia:
                        col = _uq(side.split(".")[-1])
                        if col in {k.lower() for k in key_names(inner.name)}:
                            keyed = True
                        else:
                            many = many or side
            if many and not keyed:
                p, t = items[0]
                edits = []
                tot = re.search(r"\btotal\b|\bsum\b|\bkitna\b|\bjod\b|\bkul\b", said)
                if tot and not re.fullmatch(r".*\*", t):
                    edits = [(p, p + len(t), f"SUM({t})")]
                out += schema_keep((F("check", "scalar_rows", f"The subquery in SELECT can return more than one row ({_flat(many)} is not a key)",
                              f"SELECT wali subquery ek se zyada row de sakti hai ({_flat(many)} key nahi hai)", ev,
                              f"A subquery in the SELECT list must give one value per row. It is tied on {_flat(many)}, which many "
                              f"{inner.name} rows can share, so as soon as one outer row has two of them MySQL/MariaDB stop with error "
                              f"1242 (Subquery returns more than 1 row) and PostgreSQL with 21000 (more than one row returned by a "
                              f"subquery used as an expression). Say which one value you want: a total (SUM / COUNT / MAX of it), or "
                              f"one chosen row (ORDER BY ... LIMIT 1).",
                              f"SELECT ki subquery har row ke liye ek hi value de. Ye {_flat(many)} se judi hai jo kai {inner.name} rows "
                              f"mein ho sakta hai, isliye do milte hi MySQL/MariaDB error 1242 aur PostgreSQL 21000. Batao kaunsi ek value "
                              f"chahiye: total (SUM / COUNT / MAX) ya ek chuni hui row (ORDER BY ... LIMIT 1)."), edits),
                                   [("unique", inner.alias or inner.name, [_uq(many.split(".")[-1])])])

        # ---- S8. a correlated SUM in SELECT: NULL (not 0) when nothing matches
        if ctx == "scalar_select" and len(items) == 1 and re.match(r"\s*sum\s*\(", items[0][1], re.I) and orefs \
                and C._close(items[0][1], items[0][1].index("(")) == len(re.split(r"\s+as\s+", items[0][1], flags=re.I)[0].rstrip()) - 1 \
                and not NULL_FN.search(s[:blk["a"]]) and not re.search(r"\bcoalesce|ifnull|nvl\b", items[0][1], re.I):
            whole = sql[blk["a"]:blk["b"] + 1]
            out.append((F("check", "sub_sum_null", "The SUM subquery gives NULL, not 0, for rows with nothing to add up",
                          "SUM wali subquery kuch na milne par 0 nahi, NULL deti hai", ev,
                          "SUM of no rows is NULL on every database (only COUNT gives 0), so a row with no matches shows an empty "
                          "value instead of 0, and adding it to something else gives NULL too. If 0 is what you mean, wrap it: "
                          f"COALESCE({_short(whole, 60)}, 0).",
                          "Kuch na mile to SUM NULL deta hai (sirf COUNT 0 deta hai), isliye bina match wali row khaali dikhti hai. "
                          f"0 chahiye to COALESCE({_short(whole, 60)}, 0) likho."),
                        [(blk["a"], blk["b"] + 1, f"COALESCE({whole}, 0)")]))

        # ---- S9. LIMIT without ORDER BY inside a subquery: which row(s) it keeps is up to the engine
        if ctx in ("scalar_select", "compare", "in", "not_in", "quant") and has_limit and "order by" not in cl \
                and not re.search(r"\blimit\s+0\b", sub, re.I):
            single = AGG_ONE.match(items[0][1]) if len(items) == 1 else None
            if not (single and not grouped):
                out.append((F("check", "sub_limit_order", "LIMIT without ORDER BY inside the subquery: which row it keeps is not defined",
                              "Subquery mein ORDER BY ke bina LIMIT: kaunsi row aayegi tay nahi", ev,
                              "Without ORDER BY a database may return the rows in any order (it can change after an insert, an index or "
                              "an upgrade), so LIMIT keeps whichever row it happened to read first. Add ORDER BY with a column that "
                              "decides which row you mean (and a key at the end to break ties).",
                              "ORDER BY ke bina rows kisi bhi kram mein aa sakti hain, isliye LIMIT jo pehle mili wahi rakhta hai. ORDER BY "
                              "lagao jo batata hai kaunsi row chahiye (ties ke liye aakhir mein key)."), []))

        # ---- S10. LIMIT inside IN / ANY / ALL: MySQL and MariaDB refuse it (1235), PostgreSQL runs it
        if ctx in ("in", "not_in", "quant") and has_limit and re.search(r"\blimit\s+\d+", sub, re.I):
            kind, says = _refused("limit_in_subquery")
            if kind:
                inner = sql[blk["o"]:blk["b"]]
                out.append((F(kind, "in_limit", "LIMIT inside an IN (SELECT ...) subquery",
                              "IN (SELECT ...) subquery ke andar LIMIT", ev,
                              f"{says} Put the limited query one level deeper, as a table of its own, and it works on every database: "
                              f"IN (SELECT * FROM ({_short(inner, 50)}) AS limited).",
                              f"{says} Limit wali query ko ek level andar, alag table ki tarah rakho, har database pe chalegi: "
                              f"IN (SELECT * FROM (...) AS limited)."),
                            [(blk["o"], blk["b"], f"SELECT * FROM ({inner.strip()}) AS limited")]))

        # ---- S11. a subquery in FROM with no name of its own
        if ctx == "derived":
            after = s[blk["b"] + 1:]
            nm = re.match(r"\s*(?:as\s+)?([A-Za-z_`\"][\w$`\"]*)", after, re.I)
            if not nm or _uq(nm.group(1)) in C.KW or _uq(nm.group(1)) in ("where", "group", "order", "limit", "join", "on", "union",
                                                                           "left", "right", "inner", "cross", "natural", "full", "having"):
                kind, says = _refused("derived_alias")
                if kind:
                    taken = blk["own"] | blk["outer"]
                    name = next(n for n in ("t", "t1", "sub", "derived1") if n not in taken)
                    out.append((F(kind, "derived_alias", "The subquery in FROM has no name",
                                  "FROM wali subquery ka koi naam nahi", ev,
                                  f"{says} Give it one after the closing bracket: ( ... ) AS {name}.",
                                  f"{says} Band bracket ke baad naam do: ( ... ) AS {name}."),
                                [(blk["b"] + 1, blk["b"] + 1, f" AS {name}")]))

        # ---- S12. > ALL (SELECT col ...): never true with a NULL in col, always true when the subquery is empty
        if ctx == "quant" and blk["quant"] == "all" and blk["op"] in (">", ">=", "<", "<=") and len(items) == 1 and not grouped \
                and not has_limit and not AGG_ONE.match(items[0][1]):
            p, t = items[0]
            col = t
            fn = "MAX" if blk["op"].startswith(">") else "MIN"
            am = re.search(r"\ball\s*$", s[:blk["a"]], re.I)
            edits = [(am.start(), am.end(), ""), (p, p + len(t), f"{fn}({col})")] if am else []
            out.append((F("check", "all_null", f"{blk['op']} ALL (SELECT {_short(col, 30)} ...): one NULL makes it never true, no rows make it always true",
                          f"{blk['op']} ALL (SELECT {_short(col, 30)} ...): ek NULL ho to kabhi true nahi, khaali ho to hamesha true", ev,
                          f"ALL must hold against every value: a NULL among them makes the answer unknown (the row is dropped, on "
                          f"every database), and an empty subquery makes it true for every row. If you mean 'more than the biggest "
                          f"one, ignoring empty values', compare with {fn}: {blk['op']} (SELECT {fn}({_short(col, 30)}) ...), which also "
                          f"keeps nothing when there is nothing to compare with.",
                          f"ALL har value ke saamne sach hona chahiye: ek NULL se jawab unknown (row hat jaati hai), aur khaali subquery "
                          f"pe har row true. Sabse badi value se compare karna ho (khaali values chhod kar) to {fn} lo: "
                          f"{blk['op']} (SELECT {fn}(...) ...)."), edits))

        # ---- S13. x NOT IN (SELECT id ...): the subquery can't hold a NULL, but a NULL x is dropped (NOT EXISTS keeps it)
        if ctx == "not_in" and len(items) == 1 and left_txt and not left_tuple and len(blk["srcs"]) == 1 and not grouped:
            im = re.fullmatch(rf"(?:({NAME})\.)?({NAME})", items[0][1])
            lm = re.fullmatch(rf"(?:({NAME})\.)?({NAME})", left_txt)
            inner = blk["srcs"][0]
            from app.agents.sqljoins import key_names

            keys = {k.replace("_", "") for k in key_names(inner.name)} if inner.name else set()
            if im and lm and _uq(im.group(2)).replace("_", "") in keys and _uq(lm.group(2)) != "id" and inner.name:
                ia = inner.alias if _uq(inner.alias) != _uq(inner.name) else "s1"
                outer_q = left_txt
                if not lm.group(1) and blk["psrcs"]:
                    po = blk["psrcs"][0]
                    outer_q = f"{po.alias or po.name}.{left_txt}"
                wa, wt = _where_text(blk)
                cond = ""
                if wt:
                    wsql = sql[wa:wa + len(wt)].strip()
                    if ia != inner.alias:
                        cond = None if re.search(rf"\b{re.escape(inner.name)}\.", wsql, re.I) else f" AND ({wsql})"
                    else:
                        cond = f" AND ({wsql})"
                if cond is not None and not ("order by" in cl or has_limit):
                    new = f"NOT EXISTS (SELECT 1 FROM {inner.name} {ia} WHERE {ia}.{im.group(2)} = {outer_q}{cond})"
                    start = blk["left"][0]
                    oq = lm.group(1) or (blk["psrcs"][0].alias or blk["psrcs"][0].name if blk["psrcs"] else None)
                    out += schema_keep((F("check", "not_in_outer", f"{left_txt} NOT IN (SELECT id ...) drops the rows where {left_txt} is empty",
                                  f"{left_txt} NOT IN (SELECT id ...) un rows ko hata deta hai jahan {left_txt} khaali hai", ev,
                                  f"For a row whose {lm.group(2)} is NULL, 'NULL is not in the list' is unknown, so NOT IN drops it with no "
                                  f"error, on every database (a row with no {inner.name} at all is left out). NOT EXISTS keeps such rows "
                                  f"and gives the same answer for the others: {new}.",
                                  f"Jis row ka {lm.group(2)} NULL hai uske liye NOT IN ka jawab unknown hai, isliye wo row chupchaap hat "
                                  f"jaati hai. NOT EXISTS aisi rows rakhta hai, baaki ka jawab wahi: {new}."),
                                [(start, blk["b"] + 1, new)]), [("not_null", oq, _uq(lm.group(2)))])

    # ---- S14. x NOT IN (1, NULL) / x IN ('a', NULL): a NULL in a written list
    for m in re.finditer(rf"({QNAME})\s+(not\s+)?in\s*\(([^()]*)\)", s, re.I):
        if re.match(r"\s*select\b", m.group(3), re.I):
            continue
        parts = [p.strip() for p in m.group(3).split(",")]
        if not any(p.lower() == "null" for p in parts):
            continue
        x = sql[m.start(1):m.end(1)]
        a0, a1 = m.start(3), m.end(3)
        rest = [sql[a0:a1].split(",")[i].strip() for i, p in enumerate(parts) if p.lower() != "null"]
        if m.group(2):
            new = f"{x} NOT IN ({', '.join(rest)})" if rest else f"{x} IS NOT NULL"
            out.append((F("wrong", "in_list_null", f"{x} NOT IN (..., NULL) is never true",
                          f"{x} NOT IN (..., NULL) kabhi true nahi hota", _short(sql[m.start():m.end()]),
                          f"NOT IN means 'different from every value in the list', and nothing is known to be different from NULL, so "
                          f"the condition is never true and no row passes (on every database, with no error). Leave the NULL out: "
                          f"{new} (that already keeps out the rows where {x} is empty).",
                          f"NOT IN ka matlab list ki har value se alag, aur NULL se alag hona kabhi pata nahi chalta, isliye koi row nahi "
                          f"aati. NULL hata do: {new}."), [(m.start(), m.end(), new)]))
        else:
            new = (f"({x} IN ({', '.join(rest)}) OR {x} IS NULL)" if rest else f"{x} IS NULL")
            out.append((F("check", "in_list_null", f"{x} IN (..., NULL) never matches an empty {x}",
                          f"{x} IN (..., NULL) khaali {x} ko kabhi match nahi karta", _short(sql[m.start():m.end()]),
                          f"IN compares with =, and NULL = NULL is not true, so the NULL in the list matches nothing (on every "
                          f"database). To keep the rows where {x} is empty too: {new}.",
                          f"IN = se compare karta hai aur NULL = NULL true nahi, isliye list ka NULL kuch match nahi karta. Khaali {x} "
                          f"wali rows bhi chahiye to: {new}."), [(m.start(), m.end(), new)]))
    return out


# ------------------------------------------------------------------------------------------------ JOIN used as a filter
REL = re.compile(r"\b(who|whose|that|which|with|having|jinhone|jinke|jinki|jinka|jo|jin|wale|waale|vale)\b")


def semi_join(sql: str, s: str, cl: dict, srcs: list, items: list, intent: str, F) -> list:
    """'customers who have a paid order' written as customers JOIN orders: each customer once per matching order."""
    from app.agents import sqlread
    from app.agents.sqljoins import _base, _one

    C = _C()
    said = (intent or "").lower()
    if len(srcs) != 2 or not said or not REL.search(said) or srcs[1].kind not in ("join", "inner") or not srcs[1].on:
        return []
    if any(k in cl for k in ("group by", "having", "union", "window")) or any(i["agg"] for i in items):
        return []
    sel = cl["select"]
    if re.match(r"\s*distinct\b", s[sel[0]:sel[1]], re.I) or re.search(r"\bover\s*\(", s[sel[0]:sel[1]], re.I):
        return []
    p, c = srcs
    pa, ca = _uq(p.alias), _uq(c.alias)
    if not p.name or not c.name or pa == ca:
        return []
    noun = _base(p.name)
    if not re.search(rf"\b({re.escape(noun)}|{re.escape(_one(noun))})\b", said):
        return []
    on = C._no_subqueries(c.on).rstrip().rstrip(";")
    m = re.fullmatch(rf"\s*({NAME})\.({NAME})\s*=\s*({NAME})\.({NAME})\s*", on)
    if not m:
        return []
    sides = {_uq(m.group(1)): _uq(m.group(2)), _uq(m.group(3)): _uq(m.group(4))}
    if set(sides) != {pa, ca} or not sqlread._is_key(p, sides[pa]) or sqlread._is_key(c, sides[ca]):
        return []
    # every SELECT item and ORDER BY name belongs to the parent
    for it in items:
        refs = {_uq(q) for q, _c, _p in C._qrefs(it["text"])}
        if refs - {pa} or (not refs and not re.fullmatch(r"\*|\d+|'x*'", it["text"])) or _bare_cols(re.sub(rf"\b{pa}\.\w+", "", it["text"], flags=re.I)):
            return []
        if it["text"] == "*":
            return []
    if "order by" in cl:
        ot = s[cl["order by"][0]:cl["order by"][1]]
        if {_uq(q) for q, _c, _p in C._qrefs(ot)} - {pa}:
            return []
    inner, outer = [], []
    if "where" in cl:
        wa, wb = cl["where"]
        wtext = s[wa:wb]
        wd = C._depths(wtext)
        if any(wd[x.start()] == 0 for x in re.finditer(r"\bor\b", wtext, re.I)):
            return []
        cuts = [x for x in re.finditer(r"\band\b", wtext, re.I) if wd[x.start()] == 0
                and not re.search(r"\bbetween\s+\S+\s*$", wtext[:x.start()], re.I)]
        last = 0
        for x in cuts + [None]:
            end = x.start() if x else len(wtext)
            part = wtext[last:end]
            refs = {_uq(q) for q, _c, _p in C._qrefs(C._no_subqueries(part))}
            if _bare_cols(C._no_subqueries(part)):
                return []
            text = sql[wa + last:wa + end].strip().rstrip(";").rstrip()
            if refs == {ca}:
                inner.append(text)
            elif refs <= {pa}:
                outer.append(text)
            else:
                return []
            last = x.end() if x else last
    head = sql[:sel[1]].rstrip()
    ptext = sql[p.start:p.end].strip()
    cond = " AND ".join([sql[c.on_start:c.on_end].strip().rstrip(";").rstrip()] + inner)
    rest_at = min([cl[k][0] for k in ("order by", "limit") if k in cl], default=None)
    tail = ""
    if rest_at is not None:
        dep = C._depths(s)
        kw = min(x.start() for x in re.finditer(r"\b(order\s+by|limit)\b", s, re.I) if dep[x.start()] == 0)
        tail = "\n" + sql[kw:].strip()
    where = f"\nWHERE EXISTS (\n  SELECT 1 FROM {c.name} {c.alias}\n  WHERE {cond}\n)"
    where += "".join(f"\n  AND {x}" for x in outer)
    new = f"{head}\nFROM {ptext}{where}{tail}"
    if not new.rstrip().endswith(";") and sql.rstrip().endswith(";"):
        new = new.rstrip() + ";"
    return schema_keep((F("check", "semi_join", f"JOIN {c.name} repeats each {_one(noun)} once per matching {_one(_base(c.name))}",
               f"JOIN {c.name} har {_one(noun)} ko har milti {_one(_base(c.name))} ke liye dohraata hai", _short(sql[c.start:c.end]),
               f"A {_one(noun)} with two matching {c.name} rows comes out twice, because a join makes one row per matching pair "
               f"(one {_one(noun)} to many {c.name}). You only read {p.name}'s columns, so if you mean 'the {p.name} that have one', "
               f"ask that with EXISTS: each {_one(noun)} comes once, and no DISTINCT is needed.",
               f"Do milti {c.name} rows wala {_one(noun)} do baar aata hai, kyunki join har jodi ki ek row banata hai. Aap sirf "
               f"{p.name} ke columns padhte ho, to 'jin {p.name} ke paas hai' EXISTS se poocho: har {_one(noun)} ek baar."),
             [(0, len(sql), new)]), [("to_one", c.alias or c.name, None)])
