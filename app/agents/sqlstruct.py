"""The remaining structural checks (night 13 of the curriculum), shared by every database (fundamentals 'correlation',
'join_condition', 'set_operations', 'grain' and 'index_use' in app/dbknow/core/query_fundamentals.json). What each engine
really does with them lives in the dialect files (query_rules: join_without_on, star_dup_derived, union_collation,
repeated_subquery, same_subquery, cte_read_twice), per standing rule B.

A query's structure says a lot before any data is seen:
- a correlated subquery is a small query run for each outer row; it must name the outer row. A scalar total in SELECT that
  never names it gives the same number on every row (a grand total), and a condition that compares the inner table with
  itself (o.customer_id = o.id) or links a key to the wrong outer table (p.order_id = c.id) pairs rows by coincidence of
  numbers. Both depend on what was meant -> 'check', the corrected link offered (its column name a guess from the names).
- the same subquery written twice is worked out twice: PostgreSQL makes one SubPlan / InitPlan per copy (a copy in ORDER BY
  that is exactly a SELECT item shares that item's plan), MariaDB shows one DEPENDENT SUBQUERY / SUBQUERY per copy. Computing
  it once (a derived table for a correlated one, a one-row CROSS JOIN for an uncorrelated total) returns the same rows ->
  a speed risk, the faster form offered.
- UNION removes duplicate rows, which costs a sort or a hash, and it removes them within each part too. When a constant
  column proves the parts can never share a row, UNION ALL returns the same rows exactly when each part has no repeats of
  its own (it lists a key, or has DISTINCT / GROUP BY on its columns) -> 'check', needs the table structure. UNION ALL when
  the person asked for each value once keeps the repeats -> 'check', UNION offered.
- a filter on a GROUP BY column in HAVING returns the same rows as in WHERE, but only after every group was built
  (both engines) -> a speed risk, WHERE offered. A CTE read twice is built once and kept by PostgreSQL, so a filter on one of
  its uses is not pushed in (lab: CTE Scan with the filter on top); AS NOT MATERIALIZED (PostgreSQL 12+) lets it in, MariaDB
  pushes it into both copies by itself (and refuses that keyword).
- SELECT * across a join returns the columns of every table, so a name both tables have (id) comes twice: MariaDB refuses
  that inside a derived table or a CTE (1060), PostgreSQL runs it until the name is used (42702); at the top level the app
  that reads columns by name gets only one of them.
Speed findings are risks (needs EXPLAIN or a run), never promised bottlenecks.
Every rule was reproduced on MariaDB 10.11 and PostgreSQL 16 (tests/practice/test_night13.py has the lab table).
Returns [(Finding, [edits])], edit = (start, end, new text) on the original query; (0, len(sql), text) rewrites it whole.
"""

import re

from app.agents.sqljoins import _fk

# the engines each rule was reproduced on (tests/practice/test_night13.py, LAB) -- standing rule B
PROVEN = {k: ["mariadb", "postgresql"] for k in (
    "scalar_uncorr", "corr_inner", "corr_key", "repeat_subquery", "same_subquery", "union_all", "union_all_dups", "having_key",
    "cte_filter_late", "star_dup", "star_join", "join_no_on")}
# what the person may say when they want each row once
ONCE_SAID = re.compile(r"\b(unique|distinct|no\s+duplicates?|without\s+(?:any\s+)?(?:duplicates?|repeats?)|de-?dup\w*|"
                       r"bina\s+duplicate|duplicate\s+(?:na|nahi|nahin|mat)|ek\s+(?:hi\s+)?baar|each\s+\w+\s+(?:only\s+)?once|"
                       r"only\s+once|without\s+repeating)\b", re.I)
AGG_ONE = re.compile(r"^\s*(count|sum|avg|min|max)\s*\(", re.I)
NAME = r"[`\"]?[A-Za-z_][\w$]*[`\"]?"


def _C():
    from app.agents import sqlcorrect as C

    return C


def _S():
    from app.agents import sqlsubq as S

    return S


def _uq(n: str) -> str:
    return (n or "").strip('`"').lower()


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _short(t: str, n: int = 100) -> str:
    t = _flat(t)
    return t if len(t) <= n else t[:n - 3] + "..."


def _says(key: str) -> tuple[str | None, str]:
    """(engine family or None, what it does) for an engine fact in the dialect files."""
    from app.agents import sqlbasics

    eng = sqlbasics.engine_seen("")
    my, pg = sqlbasics.rules("mysql").get(key, {}), sqlbasics.rules("postgresql").get(key, {})
    if eng == "mysql":
        return eng, my.get("says", "")
    if eng == "postgresql":
        return eng, pg.get("says", "")
    return None, f"{my.get('says', '')} {pg.get('says', '')}".strip()


def _engine(sql: str):
    from app.agents import sqlbasics

    return sqlbasics.engine_seen(sql)


def structure(sql: str, s: str, intent: str, F, is_select: bool) -> list:
    """Night 13's structural checks for a statement: [(Finding, [edits])]."""
    out = []
    if not re.search(r"\bselect\b", s, re.I):
        return out
    try:
        out += _correlation(sql, s, F)
        out += _repeats(sql, s, F)
        if is_select:
            out += _unions(sql, s, intent, F)
            out += _cte_twice(sql, s, F)
            out += _stars(sql, s, F)
    except Exception:  # noqa: BLE001 - a structural extra must never stop the main check
        return out
    return out


def guess_link(x, earlier: list, hint: set = frozenset()) -> str | None:
    """The usual link between a joined table and one before it, from the names alone (child.parent_id = parent.id); the
    earlier table named in a broken ON is preferred, else the one just before. Always offered as a guess."""
    from app.agents.sqljoins import _rank

    named = [y for y in earlier if y.name and y.name.split(".")[-1].lower() != x.name.split(".")[-1].lower()]
    if not named:
        return None
    pick = [y for y in named if (y.alias or "").lower() in hint] or named[-1:]
    if len(pick) != 1:
        return None
    P = pick[0]
    xa, pa = x.alias or x.name, P.alias or P.name
    if _rank(x.name) < _rank(P.name):
        return f"{pa}.{_fk(x.name)} = {xa}.id"
    return f"{xa}.{_fk(P.name)} = {pa}.id"


# ------------------------------------------------------------------------------------------------ correlation
def _outer_sources(blk) -> list:
    """The FROM sources around a subquery (its parent level), with names."""
    return [x for x in blk["psrcs"] if x.name]


def _correlation(sql: str, s: str, F) -> list:
    S, C = _S(), _C()
    out = []
    for blk in S.blocks(s):
        if blk["ctx"] in ("cte", "derived", "lateral"):
            continue
        sub, o = blk["sub"], blk["o"]
        inner = [x for x in blk["srcs"] if x.name]
        if len(inner) != 1 or "from" not in blk["cl"]:
            continue
        T = inner[0]
        ta = _uq(T.alias or T.name)
        outer = [x for x in _outer_sources(blk) if _uq(x.name.split(".")[-1]) != _uq(T.name.split(".")[-1])]
        if not outer:
            continue
        refs_out = S._outer_refs(blk)
        wpos, wtext = S._where_text(blk)
        ev = _short(sql[blk["a"]:blk["b"] + 1])
        # ---- a scalar total in SELECT that never names the outer row: the same number on every row
        if blk["ctx"] == "scalar_select" and not refs_out and not blk["nested"] and "group by" not in blk["cl"]:
            items = S._items(blk)
            pair = re.search(rf"{NAME}(?:\.{NAME})?\s*=\s*{NAME}\.{NAME}|\b(?!\d){NAME}\s*=\s*(?!\d|'){NAME}\b", wtext or "")
            whole = re.fullmatch(r"\(\s*select\b[\s\S]*\)\s*(?:as\s+)?[`\"]?\w*[`\"]?", _item_around(s, blk), re.I)
            if len(items) == 1 and AGG_ONE.match(items[0][1]) and not pair and whole and \
                    not re.search(r"\blimit\b|\bover\s*\(", sub, re.I):
                P = outer[0]
                pa = P.alias or P.name
                link = f"{ta}.{_fk(P.name)} = {pa}.id"
                if wtext is None:
                    at = o + len(sub.rstrip())
                    edits = [(at, at, f" WHERE {link}")]
                else:
                    edits = [(wpos, wpos + len(wtext.rstrip()), f" {link} AND ({_flat(sql[wpos:wpos + len(wtext.rstrip())])})")]
                fn = AGG_ONE.match(items[0][1]).group(1).upper()
                out.append((F("check", "scalar_uncorr", f"The {fn} subquery in SELECT never mentions the outer row: every row gets the same number",
                              f"SELECT wali {fn} subquery bahar ki row ka naam nahi leti: har row ko wahi number milta hai", ev,
                              f"A subquery is worked out on its own unless it names a column of the outer row. This one reads all of "
                              f"{T.name}, so each {P.name} row shows the same {fn.lower()} (lab: one distinct value over 8 customers on "
                              f"MariaDB and PostgreSQL). If you wanted the total of all {T.name} next to each row, that is fine. If you "
                              f"wanted each {_one(P.name)}'s own {fn.lower()}, tie it to the outer row: WHERE {link} (the column name "
                              f"{_fk(P.name)} is a guess from the table names: check it).",
                              f"Subquery tab tak akeli chalti hai jab tak wo bahar ki row ka column na le. Ye poori {T.name} padhti hai, "
                              f"isliye har {P.name} row pe wahi {fn.lower()} aata hai. Sabka total chahiye tha to theek hai. Har "
                              f"{_one(P.name)} ka apna chahiye to bahar ki row se jodo: WHERE {link} ({_fk(P.name)} naam ka andaza "
                              f"hai, check karo)."), edits))
                continue
        # ---- the condition compares the inner table with itself, where an outer table was meant (o.customer_id = o.id)
        if not refs_out and wtext:
            for m in re.finditer(rf"(?:({NAME})\.)?({NAME})\s*=\s*(?:({NAME})\.)?({NAME})(?![\w.(])", wtext):
                q1, c1, q2, c2 = _uq(m.group(1)), _uq(m.group(2)), _uq(m.group(3)), _uq(m.group(4))
                if q1 and q1 != ta or q2 and q2 != ta or c1 == c2 or c1 in C.KW or c2 in C.KW or re.fullmatch(r"\d+", c2):
                    continue
                for (kc, ks), (ic, istart, iend) in (((c1, 1), (c2, m.start(3) if m.group(3) else m.start(4), m.end(4))),
                                                     ((c2, 3), (c1, m.start(1) if m.group(1) else m.start(2), m.end(2)))):
                    P = next((x for x in outer if _fk(x.name) == kc), None)
                    if P is None or ic != "id":
                        continue
                    new = f"{P.alias or P.name}.id"
                    a, b = wpos + istart, wpos + iend
                    out.append((F("check", "corr_inner", f"{_flat(m.group(0))} compares {T.name} with itself; {kc} points at {P.name}",
                                  f"{_flat(m.group(0))} {T.name} ko khud se milata hai; {kc} {P.name} ki taraf hai", ev,
                                  f"Inside the subquery both sides belong to {T.name} (a name is looked up in the subquery's own FROM "
                                  f"first), so each {T.name} row is compared with itself and the outer {P.name} row is never used: the "
                                  f"answer is the same for every row (lab: one value for all customers). {kc} is the link to {P.name}: "
                                  f"compare it with the outer row, {kc} = {new}.",
                                  f"Subquery ke andar dono taraf {T.name} hai (naam pehle subquery ki apni FROM mein dhoonda jaata hai), "
                                  f"isliye har {T.name} row khud se milti hai aur bahar ki {P.name} row kabhi use nahi hoti. {kc} "
                                  f"{P.name} ka link hai: bahar ki row se milao, {kc} = {new}."), [(a, b, new)]))
                    break
        # ---- a link column compared with the wrong outer table (p.order_id = c.id while orders o is outside too)
        if refs_out and wtext:
            by_alias = {_uq(x.alias or x.name): x for x in outer}
            for m in re.finditer(rf"({NAME})\.({NAME})\s*=\s*({NAME})\.({NAME})", wtext):
                q1, c1, q2, c2 = (_uq(g) for g in m.groups())
                for (iq, ic), (oq, oc, span) in (((q1, c1), (q2, c2, (m.start(3), m.end(4)))), ((q2, c2), (q1, c1, (m.start(1), m.end(2))))):
                    if iq != ta or oq not in by_alias:
                        continue
                    target = [x for x in outer if _fk(x.name) == ic]
                    if len(target) != 1 or ic == oc:
                        continue
                    P = target[0]
                    if by_alias[oq] is P and oc == "id":
                        continue
                    new = f"{P.alias or P.name}.id"
                    out.append((F("check", "corr_key", f"{iq}.{ic} points at {P.name}, but the subquery compares it with {oq}.{oc}",
                                  f"{iq}.{ic} {P.name} ki key hai, par subquery use {oq}.{oc} se milati hai", ev,
                                  f"{ic} is the link from {T.name} to {P.name} ({P.alias or P.name} is in the outer query), yet it is "
                                  f"matched with {oq}.{oc}, so rows pair up only when the numbers happen to be equal. The usual "
                                  f"correlation is {iq}.{ic} = {new}.",
                                  f"{ic} {T.name} se {P.name} ka link hai ({P.alias or P.name} bahar ki query mein hai), par yahan "
                                  f"{oq}.{oc} se milaya hai: rows sirf number barabar hone pe judti hain. Aam link: {iq}.{ic} = {new}."),
                                [(wpos + span[0], wpos + span[1], new)]))
                    break
    return out


def _one(word: str) -> str:
    from app.agents.sqljoins import _one as one, _base

    return one(_base(word))


def _item_around(s: str, blk) -> str:
    """The SELECT item that holds this subquery (in the masked text)."""
    C = _C()
    po = blk["poff"]
    ptext = s[po:] if po >= 0 else s
    dep = C._depths(ptext)
    cl = C._clauses(ptext, dep)
    if "select" not in cl:
        return ""
    x, y = cl["select"]
    for off, t in C._split_top(ptext[x:y]):
        a = po + x + off
        if a <= blk["a"] <= a + len(t):
            return t.strip()
    return ""


# ------------------------------------------------------------------------------------------------ repeated subqueries
def _top_items(s: str):
    C = _C()
    dep = C._depths(s)
    cl = C._clauses(s, dep)
    return dep, cl


def _repeats(sql: str, s: str, F) -> list:
    S, C = _S(), _C()
    dep, cl = _top_items(s)
    if re.search(r"^\s*with\b", s, re.I) or any(re.match(r"union|intersect|except", k) for k in cl) or \
            re.search(r"\b(union|intersect|except)\b", _C()._no_subqueries(s), re.I) or "from" not in cl:
        return []
    blks = [b for b in S.blocks(s) if not b["nested"] and b["ctx"] not in ("cte", "derived", "lateral")]
    groups: dict[str, list] = {}
    for b in blks:
        groups.setdefault(_flat(sql[b["a"]:b["b"] + 1]).lower(), []).append(b)
    out = []
    for key, bs in groups.items():
        if len(bs) < 2:
            continue
        corr = bool(S._outer_refs(bs[0]))
        ev = _short(sql[bs[0]["a"]:bs[0]["b"] + 1], 90)
        where_at = [b for b in bs if "where" in cl and cl["where"][0] <= b["a"] < cl["where"][1]]
        sel_at = [b for b in bs if cl["select"][0] <= b["a"] < cl["select"][1]]
        ord_at = [b for b in bs if "order by" in cl and cl["order by"][0] <= b["a"] < cl["order by"][1]]
        if corr:
            fam, says = _says("repeated_subquery")
            new = _once_derived(sql, s, cl, bs, sel_at, where_at, ord_at)
            out.append((F("check", "repeat_subquery", f"The same correlated subquery is written {len(bs)} times",
                          f"Wahi correlated subquery {len(bs)} baar likhi hai", ev,
                          f"Each copy is a small query run for every outer row, and the database doesn't know they are the same "
                          f"(a speed risk: how much it costs depends on the rows and the plan). {says} Work it out once: give it a "
                          f"name in SELECT and use that name" + (" (ORDER BY can use the name directly; WHERE can't, so the query "
                          "is wrapped in a derived table and filtered outside). The rows stay exactly the same." if new else
                          ". Rewriting it here would need a choice only you can make, so it is left as it is."),
                          f"Har copy har bahar ki row ke liye chalne wali chhoti query hai, aur database nahi jaanta ki ye ek hi hai "
                          f"(speed ka risk). Ek baar nikalo: SELECT mein naam do aur wahi naam use karo (ORDER BY naam le sakta hai; "
                          f"WHERE nahi, isliye query ko derived table mein rakh ke bahar filter). Rows bilkul wahi rahengi."),
                        [(0, len(sql), new)] if new else []))
        else:
            fam, says = _says("same_subquery")
            new = _once_cross(sql, s, cl, bs)
            out.append((F("check", "same_subquery", f"The same subquery is written {len(bs)} times",
                          f"Wahi subquery {len(bs)} baar likhi hai", ev,
                          f"It doesn't depend on the outer row, so it gives one value, but each copy is still worked out on its own "
                          f"(a speed risk). {says}" + (" Work it out once as a one-row derived table joined with CROSS JOIN and use "
                          "its column in each place: a total without GROUP BY always gives exactly one row, so no row is added or "
                          "lost." if new else " Compute it once (a CTE or a derived table) and use it in each place."),
                          "Ye bahar ki row pe nahi tiki, isliye ek hi value deti hai, phir bhi har copy alag se nikalti hai (speed ka "
                          "risk). Ek baar nikalo: ek row ki derived table CROSS JOIN se jodo aur uska column har jagah use karo; "
                          "bina GROUP BY ka total hamesha ek hi row deta hai, isliye koi row badhti ya ghatti nahi."),
                        [(0, len(sql), new)] if new else []))
    return out


def _sel_alias(sql: str, item_text: str) -> str | None:
    m = re.search(r"\)\s*(?:as\s+)?[`\"]?([A-Za-z_]\w*)[`\"]?\s*$", item_text, re.I)
    return m.group(1) if m and m.group(1).lower() not in _C().KW else None


def _once_derived(sql, s, cl, bs, sel_at, where_at, ord_at) -> str | None:
    """SELECT ..., (sub) AS t FROM ... [WHERE ... (sub) ...] [ORDER BY (sub)]: the subquery worked out once."""
    C = _C()
    if not sel_at or any(k in cl for k in ("group by", "having", "limit", "window", "for update")) or \
            re.match(r"\s*distinct\b", s[cl["select"][0]:cl["select"][1]], re.I):
        return None
    sa, sb = cl["select"]
    items = [(sa + off, t) for off, t in C._split_top(s[sa:sb]) if t.strip()]
    owner = next(((a, t) for a, t in items if a <= sel_at[0]["a"] < a + len(t)), None)
    if owner is None:
        return None
    alias = _sel_alias(sql, sql[owner[0]:owner[0] + len(owner[1])].strip())
    if not alias or _flat(sql[owner[0]:owner[0] + len(owner[1])]).lower().rstrip() == "":
        return None
    sub_text = sql[sel_at[0]["a"]:sel_at[0]["b"] + 1]
    item_core = _flat(re.sub(r"(?:\s+as)?\s+[`\"]?\w+[`\"]?\s*$", "", sql[owner[0]:owner[0] + len(owner[1])].strip(), flags=re.I))
    if item_core.lower() != _flat(sub_text).lower():
        return None                                         # the subquery is part of a bigger expression there
    names = []
    for a, t in items:
        txt = sql[a:a + len(t)].strip()
        if txt == "*" or txt.endswith(".*"):
            return None
        al = _sel_alias(sql, txt) if txt.endswith(")") or re.search(r"\s(?:as\s+)?\w+\s*$", txt, re.I) else None
        nm = (al or re.sub(r"^.*\.", "", txt)).strip('`"').lower()
        if not re.fullmatch(r"[a-z_]\w*", nm):
            m2 = re.search(r"\s(?:as\s+)?[`\"]?(\w+)[`\"]?\s*$", txt, re.I)
            if not m2:
                return None
            nm = m2.group(1).lower()
        names.append(nm)
    if len(set(names)) != len(names):
        return None
    # the inner query: everything but the WHERE parts that use the subquery and the ORDER BY
    body_end = cl["order by"][0] if "order by" in cl else len(sql)
    order_kw = re.search(r"\border\s+by\s*$", s[:cl["order by"][0]], re.I).start() if "order by" in cl else None
    outer_where = []
    inner_sql = sql
    cuts = []
    if where_at:
        wa, wb = cl["where"]
        wtext = s[wa:wb]
        wd = C._depths(wtext)
        bounds = [0] + [m.end() for m in re.finditer(r"\band\b", wtext, re.I) if wd[m.start()] == 0] + [len(wtext)]
        if re.search(r"\bor\b", C._no_subqueries(wtext), re.I):
            return None
        keep = []
        for x, y in zip(bounds, bounds[1:]):
            part = re.sub(r"(?i)^\s*and\s+|\s+and\s*$", "", sql[wa + x:wa + y]).strip()
            if any(wa + x <= b["a"] < wa + y for b in where_at):
                outer_where.append(re.sub(re.escape(sub_text), alias, part))
            else:
                keep.append(part)
        if any(_flat(sub_text).lower() in _flat(p).lower() for p in outer_where):
            return None
        fixed_parts = []
        for part in outer_where:                       # outside the derived table only its output names exist
            ok = True

            def bare(m):
                nonlocal ok
                if m.group(2).strip('`"').lower() not in names:
                    ok = False
                return m.group(2)
            part = re.sub(r"(?<![\w.])([`\"]?[A-Za-z_]\w*[`\"]?)\.([`\"]?\w+[`\"]?)", bare, part)
            words = [w for w in re.findall(r"(?<![\w.'])([A-Za-z_]\w*)(?![\w'(])", re.sub(r"'(?:[^']|'')*'", "''", part))
                     if w.lower() not in C.KW]
            if not ok or any(w.lower() not in names and w != alias for w in words):
                return None
            fixed_parts.append(part)
        outer_where = fixed_parts
        wkw = re.search(r"\bwhere\s*$", s[:wa], re.I).start()
        cuts.append((wkw, len(sql.rstrip().rstrip(";")), ("WHERE " + " AND ".join(keep)) if keep else ""))
    elif order_kw is not None:
        cuts.append((order_kw, len(sql.rstrip().rstrip(";")), ""))
    for a, b, new in sorted(cuts, reverse=True):
        inner_sql = inner_sql[:a] + new + inner_sql[b:]
    inner_sql = inner_sql.rstrip().rstrip(";").rstrip()
    order = ""
    if "order by" in cl:
        oa, ob = cl["order by"]
        parts = []
        for off, t in C._split_top(sql[oa:ob]):
            t = t.strip().rstrip(";").strip()
            dm = re.match(r"([\s\S]*?)(\s+(?:asc|desc))?\s*$", t, re.I)
            expr, dirn = dm.group(1).strip(), dm.group(2) or ""
            if _flat(expr).lower() == _flat(sub_text).lower():
                parts.append(alias + dirn)
            elif re.sub(r"^\w+\.", "", expr).strip('`"').lower() in names:
                parts.append(re.sub(r"^\w+\.", "", expr) + dirn)
            else:
                return None
        order = " ORDER BY " + ", ".join(parts)
    where = (" WHERE " + " AND ".join(outer_where)) if outer_where else ""
    if not where and not ord_at:
        return None
    return f"SELECT * FROM ({inner_sql}) AS once_t{where}{order}"


def _once_cross(sql, s, cl, bs) -> str | None:
    """An uncorrelated total repeated: CROSS JOIN (SELECT ...) AS once1 and once1.v1 in each place."""
    C, S = _C(), _S()
    blk = bs[0]
    items = S._items(blk)
    if len(items) != 1 or not AGG_ONE.match(items[0][1]) or re.search(r"\bgroup\s+by\b|\blimit\b|\bhaving\b", blk["sub"], re.I):
        return None
    if any(k in cl for k in ("group by", "having", "window")) or re.match(r"\s*distinct\b", s[cl["select"][0]:cl["select"][1]], re.I):
        return None
    if re.search(r"\bonce1\b", s, re.I):
        return None
    fa, fb = cl["from"]
    sub_text = sql[blk["a"]:blk["b"] + 1]
    inner = re.sub(r"^\(\s*select\s+", "", sub_text, flags=re.I)
    m = re.match(r"([\s\S]*?)(\s+from\b[\s\S]*)\)$", inner, re.I)
    if not m:
        return None
    one_row = f"(SELECT {_flat(m.group(1))} AS v1 {_flat(m.group(2))})"
    edits = [(b["a"], b["b"] + 1, "once1.v1") for b in bs]
    edits.append((len(sql[:fb].rstrip()), len(sql[:fb].rstrip()), f" CROSS JOIN {one_row} AS once1"))
    out = sql
    for a, b, new in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:a] + new + out[b:]
    return out


# ------------------------------------------------------------------------------------------------ UNION / UNION ALL
def _set_parts(sql: str, s: str):
    """The parts of a top-level UNION chain: (ops, [(start, end)]) or None."""
    C = _C()
    if re.match(r"\s*with\b", s, re.I):
        return None
    dep = C._depths(s)
    ops = [m for m in re.finditer(r"\b(union(?:\s+all|\s+distinct)?|intersect|except)\b", s, re.I) if dep[m.start()] == 0]
    if not ops:
        return None
    bounds, last = [], 0
    for m in ops:
        bounds.append((last, m.start()))
        last = m.end()
    tail = s[last:]
    om = None
    for m in re.finditer(r"\b(order\s+by|limit)\b", tail, re.I):
        if C._depths(tail)[m.start()] == 0:
            om = m
            break
    bounds.append((last, last + (om.start() if om else len(tail.rstrip().rstrip(";")))))
    return [m for m in ops], bounds


def _literal_items(s: str, a: int, b: int):
    C = _C()
    part = s[a:b].strip()
    while part.startswith("(") and part.endswith(")"):
        part = part[1:-1].strip()
    dep = C._depths(part)
    cl = C._clauses(part, dep)
    if "select" not in cl:
        return None, None, part, cl
    x, y = cl["select"]
    body = part[x:y]
    items = [t.strip() for _o, t in C._split_top(body) if t.strip()]
    return items, body, part, cl


def _unions(sql: str, s: str, intent: str, F) -> list:
    got = _set_parts(sql, s)
    if not got:
        return []
    ops, bounds = got
    kinds = {re.sub(r"\s+", " ", m.group(1).lower()) for m in ops}
    out = []
    ev = _short(sql, 90)
    if kinds == {"union"}:
        parts = [_literal_items(sql, a, b) for a, b in bounds]
        masked = [_literal_items(s, a, b) for a, b in bounds]
        if any(p[0] is None for p in parts) or len({len(p[0]) for p in parts}) != 1:
            return []
        tag = None
        for i in range(len(parts[0][0])):
            vals = []
            for items, _b, _p, _c in parts:
                v = re.sub(r"(?:\s+as)?\s+[`\"]?\w+[`\"]?\s*$", "", items[i], flags=re.I) if not re.fullmatch(r"'(?:[^']|'')*'|-?\d+(?:\.\d+)?", items[i]) else items[i]
                if not re.fullmatch(r"'(?:[^']|'')*'|-?\d+(?:\.\d+)?", v.strip()):
                    break
                vals.append(v.strip().lower())
            else:
                if len(set(vals)) == len(vals):
                    tag = i
                    break
        if tag is None:
            return []
        own = all(re.match(r"\s*distinct\b", (m[1] or ""), re.I) or ("group by" in m[3]) for m in masked)
        fam, says = _says("union_collation")
        edits = [(m.start(), m.end(), "UNION ALL") for m in ops]
        out.append((F("check", "union_all", "UNION removes repeats the parts can't have between them: UNION ALL may be enough",
                      "UNION wo repeats hatata hai jo parts ke beech ho hi nahi sakte: UNION ALL kaafi ho sakta hai", ev,
                      f"Column {tag + 1} is a different constant in each part, so no row of one part can equal a row of another. "
                      f"UNION still sorts or hashes every row to find repeats, and it also removes repeats INSIDE each part. So "
                      f"UNION ALL returns exactly the same rows when each part has no repeated rows of its own: "
                      + ("each part has DISTINCT or GROUP BY, so that holds here." if own else
                         "that holds when each part lists a key (such as id) or has DISTINCT / GROUP BY on its columns; check it "
                         "(lab: city + 'c' from customers gave 7 rows with UNION and 11 with UNION ALL, id + 'c' gave 11 both ways).")
                      + (f" {says}" if says else ""),
                      f"Column {tag + 1} har part mein alag constant hai, isliye do parts ki rows kabhi barabar nahi ho sakti. Phir bhi "
                      f"UNION har row ko sort/hash karta hai, aur har part ke ANDAR ke repeats bhi hatata hai. Isliye UNION ALL bilkul "
                      f"wahi rows deta hai jab har part mein apni repeat rows na hon (key jaise id, ya DISTINCT / GROUP BY)."),
                    edits))
    elif kinds == {"union all"} and ONCE_SAID.search(intent or ""):
        edits = [(m.start(), m.end(), "UNION") for m in ops]
        out.append((F("check", "union_all_dups", "UNION ALL keeps repeated rows, and you asked for each one once",
                      "UNION ALL repeat rows rakhta hai, aur aapne har ek ek baar maanga", ev,
                      "UNION ALL just stacks the parts, so a row that both parts return (or one part returns twice) comes out twice "
                      "(lab: 11 e-mails with UNION ALL, 9-10 with UNION). UNION removes the repeats. Note that it compares rows with "
                      "the column's collation: MariaDB's usual _ci collation treats 'A@x' and 'a@x' as the same row, PostgreSQL "
                      "doesn't.",
                      "UNION ALL parts ko bas jod deta hai, isliye jo row dono parts dete hain wo do baar aati hai. UNION repeats hata "
                      "deta hai (MariaDB ka _ci collation 'A@x' aur 'a@x' ko ek maanta hai, PostgreSQL nahi)."), edits))
    return out


# ------------------------------------------------------------------------------------------------ a CTE read twice
def _cte_twice(sql: str, s: str, F) -> list:
    C = _C()
    m = re.match(r"\s*with\s+(?!recursive\b)", s, re.I)
    if not m:
        return []
    ctes, i = [], m.end()
    while True:
        nm = re.match(rf"\s*({NAME})\s*(?:\([^)]*\)\s*)?as\s*(not\s+materialized\s*|materialized\s*)?\(", s[i:], re.I)
        if not nm:
            break
        a = i + nm.end() - 1
        b = C._close(s, a)
        ctes.append((_uq(nm.group(1)), a, b, nm.group(2), i + nm.start(1)))
        i = b + 1
        cm = re.match(r"\s*,", s[i:])
        if not cm:
            break
        i += cm.end()
    main = s[i:]
    out = []
    for name, a, b, mat, _ns in ctes:
        if mat:
            continue
        uses = [u for u in re.finditer(rf"\b(?:from|join)\s+{re.escape(name)}\b(?:\s+(?:as\s+)?({NAME}))?", s[i:], re.I)]
        body = s[a + 1:b]
        gm = re.search(r"\bgroup\s+by\s+([\s\S]*?)(?:\bhaving\b|\border\b|\blimit\b|$)", body, re.I)
        if len(uses) < 2 or not gm or re.search(r"\blimit\b|\bover\s*\(", body, re.I):
            continue
        keys = {_uq(k.strip().split(".")[-1]) for k in gm.group(1).split(",")}
        aliases = {_uq(u.group(1)) if u.group(1) and _uq(u.group(1)) not in C.KW else name for u in uses}
        wm = re.search(r"\bwhere\b([\s\S]*?)(?:\bgroup\b|\border\b|\blimit\b|$)", main, re.I)
        if not wm:
            continue
        hit = next((f for f in re.finditer(rf"({NAME})\.({NAME})\s*(=|<|>|<=|>=|in)\s*(?:'|\d|\()", wm.group(1), re.I)
                    if _uq(f.group(1)) in aliases and _uq(f.group(2)) in keys), None)
        if not hit:
            continue
        eng = _engine(sql)
        edits = []
        if eng == "postgresql":
            edits = [(a, a, "NOT MATERIALIZED ")]
        fam, says = _says("cte_read_twice")
        out.append((F("check", "cte_filter_late", f"The CTE {name} is read {len(uses)} times and filtered only outside",
                      f"CTE {name} {len(uses)} baar padha gaya aur filter sirf bahar hai", _short(hit.group(0)),
                      f"{_flat(hit.group(0))}... is a condition on {name}'s GROUP BY column, so it could be applied before the "
                      f"grouping (a speed risk). {says} "
                      + ("Here (PostgreSQL) AS NOT MATERIALIZED lets the filter in; the rows stay the same." if edits else
                         "On PostgreSQL write AS NOT MATERIALIZED (12+); on any engine, put the condition inside the CTE when "
                         "every use wants only those rows."),
                      f"Ye {name} ke GROUP BY column pe condition hai, isliye grouping se pehle lag sakti hai (speed ka risk). "
                      f"PostgreSQL do baar padhe CTE ko ek baar bana ke rakhta hai aur filter andar nahi jaata: AS NOT MATERIALIZED "
                      f"(12+) likho; MariaDB khud dono copies mein filter le jaata hai."), edits))
    return out


# ------------------------------------------------------------------------------------------------ SELECT * across a join
def _stars(sql: str, s: str, F) -> list:
    C, S = _C(), _S()
    refs = C._qrefs(s)
    out = []
    levels = [(0, len(s), "main", None)]
    for blk in S.blocks(s):
        if blk["ctx"] in ("derived", "cte"):
            levels.append((blk["a"] + 1, blk["b"], blk["ctx"], blk))
    for a, b, ctx, blk in levels:
        text = s[a:b]
        if ctx == "main":
            text = C._no_subqueries(text)
            dep = C._depths(s)
            cl = C._clauses(s, dep)
            if "select" not in cl or "from" not in cl:
                continue
            srcs = C._sources(s, dep, *cl["from"])
            sel = s[cl["select"][0]:cl["select"][1]]
        else:
            srcs, cl = blk["srcs"], blk["cl"]
            if "select" not in cl or "from" not in cl:
                continue
            sel = blk["sub"][cl["select"][0]:cl["select"][1]]
        named = [x for x in srcs if x.name]
        if len(srcs) < 2 or not re.search(r"(?:^|,)\s*(?:distinct\s+)?\*\s*(?:,|$)", sel.strip(), re.I):
            continue
        if any(x.kind == "natural" or not x.name for x in srcs):
            continue
        merged = {_uq(c) for x in srcs for c in re.findall(r"\w+", x.using or "")}
        cols: dict[str, set] = {}
        mine = {_uq(x.alias or x.name): x for x in named}
        for q, c, _p in refs:
            if _uq(q) in mine and c != "*":
                cols.setdefault(_uq(c), set()).add(_uq(mine[_uq(q)].name))
        dup = sorted(c for c, ts in cols.items() if len(ts) >= 2 and c not in merged)
        names = ", ".join(x.name for x in named)
        if ctx in ("derived", "cte"):
            if not dup:
                continue
            fam, says = _says("star_dup_derived")
            kind = "error" if fam == "mysql" else "check"
            where = "derived table" if ctx == "derived" else "CTE"
            out.append((F(kind, "star_dup", f"SELECT * inside a {where} over {names} gives two columns named {dup[0]}",
                          f"{where} ke andar SELECT * ({names}) mein {dup[0]} naam ke do columns", _short(sql[blk["a"]:blk["b"] + 1]),
                          f"The query itself shows {dup[0]} in more than one of these tables, and * keeps both. {says} List the "
                          f"columns you need and rename the second one (for example {named[1].alias or named[1].name}.{dup[0]} AS "
                          f"{_one(named[1].name)}_{dup[0]}).",
                          f"Query khud dikhati hai ki {dup[0]} ek se zyada tables mein hai, aur * dono rakhta hai. MariaDB/MySQL "
                          f"derived table / CTE mein ise mana karte hain (1060); PostgreSQL chala deta hai par naam use karte hi "
                          f"42702. Jo columns chahiye wo likho aur doosre ka naam badlo."), []))
        else:
            out.append((F("check", "star_join", f"SELECT * across a join returns every column of {names}",
                          f"Join ke saath SELECT * {names} ke saare columns deta hai", "SELECT * ... JOIN",
                          "All columns of every joined table are read and sent (a speed risk on wide tables: more data, and no "
                          "index alone can answer the query)"
                          + (f", and {', '.join(dup)} comes out twice (the query shows it in more than one table): code that reads "
                             f"columns by name gets only one of them" if dup else
                             ", and a name that two tables share (often id) comes out twice; the query alone can't show which")
                          + ". Name the columns you use.",
                          "Har judi table ke saare columns padhe aur bheje jaate hain (speed ka risk), aur dono tables mein jo naam hai "
                          "(aksar id) wo do baar aata hai. Jo columns use karte ho wahi likho."), []))
    return out
