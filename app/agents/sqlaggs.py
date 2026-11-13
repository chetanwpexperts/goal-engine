"""Aggregation reasoning (night 3 of the curriculum), shared by every database (fundamental 'aggregation' in
app/dbknow/core/query_fundamentals.json, plus 'grain' and 'names_scope').

What a total sees: the rows that reach it (after WHERE and the joins), and of those only the values that are not NULL
(COUNT(*) is the one exception: it counts rows). From that one idea:
- COUNT(x) counts values that are not NULL, never values that are true: COUNT(CASE ... ELSE 0 END) and COUNT(status = 'paid')
  count every row. Sure -> 'wrong'.
- a total can't contain another total at the same level (MAX(COUNT(*))): refused everywhere. A window function in a grouped
  query sees one row per group, so its own argument must be a total too (SUM(SUM(x)) OVER ()): PostgreSQL refuses a plain
  column there, MariaDB silently takes it from one row of each group. Sure -> 'error'.
- SUM over a LEFT JOIN with no match is NULL, not 0; AVG skips NULLs instead of counting them as 0; SUM(DISTINCT x) drops equal
  amounts of different rows; an average of averages weights every group the same; a share whose total ignores the WHERE the
  rows passed doesn't add up to 100%. Each depends on what was meant -> 'check', with the rewrite offered as "If that's what
  you meant".
- what the person asked to count ('how many customers') against what the query counts (order rows) -> 'wrong', since they
  said it.
Engine differences (SUM of a comparison, a SELECT name in HAVING, COUNT(DISTINCT a, b), a plain column in a window of a grouped
query) come from the dialect files' query_rules (standing rule B): on the database the person named or the query shows they
are said for that database only; with none known, as a 'check' that names each engine.
Every rule was reproduced on MariaDB 10.11 and PostgreSQL 16 (tests/practice/test_night3.py has the lab table).
Returns [(Finding, edit or None)], edit = (start, end, new text) on the original query; (0, len(sql), text) rewrites it whole.
"""

import re

# the engines each rule was reproduced on (tests/practice/test_night3.py, LAB) -- standing rule B
PROVEN = {k: ["mariadb", "postgresql"] for k in (
    "count_case", "count_bool", "sum_bool", "nested_agg", "window_agg", "having_alias", "sum_null", "sum_distinct",
    "avg_null", "avg_avg", "count_said", "pct_total", "count_multi")}

AGG_CALL = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.I)
ANY_AGG = re.compile(r"\b(count|sum|avg|min|max|group_concat|string_agg|array_agg|stddev|variance|bit_and|bit_or)\s*\(", re.I)
COMPARE = re.compile(r"<>|!=|<=|>=|(?<![<>!])=|<|>|\bis\b|\blike\b|\bin\b|\bbetween\b|\bregexp\b", re.I)
NOT_COLS = set("""and or not null is in like between case when then else end as distinct true false interval asc desc over
partition by order rows range groups unbounded preceding following current row filter where nulls first last exclude ties others
no select from all""".split())


def _helpers():
    from app.agents import sqlcorrect as C

    return C


def _top_compare(arg: str) -> bool:
    """Does the argument hold a comparison at its own top level (status = 'paid', amount > 100, x IS NULL)?"""
    C = _helpers()
    d = C._depths(arg)
    a = arg.strip().lower()
    if a.startswith("case") or a.startswith("distinct") or a.startswith("if(") or a.startswith("if ("):
        return False
    return any(d[m.start()] == 0 for m in COMPARE.finditer(arg))


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _engine(sql: str):
    """('mysql' | 'postgresql' | None, its query_rules)."""
    from app.agents import sqlbasics

    eng = sqlbasics.engine_seen(sql)
    return eng, (sqlbasics.rules(eng) if eng else {})


def _db_says(rule: dict, fallback: str = "") -> str:
    if not isinstance(rule, dict):
        return fallback
    return rule.get("says", fallback)


def _refused(key: str, eng, sql: str):
    """For an engine-specific rule: (kind or None to stay quiet, text naming the engines). Engine facts from the dialect files."""
    from app.agents import sqlbasics

    my, pg = sqlbasics.rules("mysql").get(key, {}), sqlbasics.rules("postgresql").get(key, {})
    if eng == "mysql":
        return (None if my.get("behaviour") == "works" else "error"), _db_says(my)
    if eng == "postgresql":
        return (None if pg.get("behaviour") == "works" else "error"), _db_says(pg)
    return "check", f"{_db_says(my)} {_db_says(pg)}".strip()


def aggs(sql: str, s: str, cl: dict, srcs: list, alias_of, items: list, intent: str, F) -> list:
    C = _helpers()
    out = []
    dep = C._depths(s)
    said = (intent or "").lower()
    group = cl.get("group by")
    eng, _r = _engine(sql)

    # ---- 1. COUNT(CASE WHEN c THEN 1 ELSE 0 END) / COUNT(IF(c, 1, 0)): 0 is a value, so every row is counted
    for m in re.finditer(r"\bcount\s*\(\s*(case\b|if\s*\()", s, re.I):
        a = s.index("(", m.start())
        b = C._close(s, a)
        inner = s[a + 1:b]
        if m.group(1).lower().startswith("case"):
            idep = C._depths(inner)
            elses = [x for x in re.finditer(r"\belse\b", inner, re.I) if idep[x.start()] == 0]
            endm = re.search(r"\bend\s*$", inner, re.I)
            if not elses or not endm or not re.match(r"\s*case\b", inner, re.I):
                continue
            e = elses[-1]
            val = inner[e.end():endm.start()].strip()
            if not re.fullmatch(r"-?\d+(?:\.\d+)?|'[^']*'|true|false", val, re.I) or val.lower() == "null":
                continue
            edit = (a + 1 + len(inner[:e.start()].rstrip()), a + 1 + endm.start(), " ")
        else:
            ia = s.index("(", m.start(1))
            ib = C._close(s, ia)
            args = C._split_top(s[ia + 1:ib])
            if len(args) != 3 or C._close(s, ia) + 1 > b:
                continue
            off, val = args[2]
            v = val.strip()
            if not re.fullmatch(r"-?\d+(?:\.\d+)?|'[^']*'|true|false", v, re.I):
                continue
            k = ia + 1 + off + (len(val) - len(val.lstrip()))
            edit = (k, k + len(v), "NULL")
        call = _flat(sql[m.start():b + 1])
        fixed = _flat(C._edited(sql, m.start(), b + 1, [edit]))
        out.append((F("wrong", "count_case", f"COUNT(... ELSE {val}) counts every row",
                      f"COUNT(... ELSE {val}) har row ginta hai", call[:120],
                      f"COUNT counts the values that are not NULL, and {val} is a value just like 1, so every row is counted, "
                      f"matching or not (the same on MySQL, MariaDB and PostgreSQL). Let the rows that don't match give NULL: "
                      f"{fixed}, or add them up with SUM(CASE WHEN ... THEN 1 ELSE 0 END).",
                      f"COUNT NULL ke alawa har value ginta hai, aur {val} bhi value hai, isliye har row gini jaati hai. Na milne "
                      f"wali rows ko NULL do: {fixed}, ya SUM(CASE WHEN ... THEN 1 ELSE 0 END) se jodo."), edit))

    # ---- 2. COUNT(status = 'paid'): a comparison is 1/0 (true/false), never NULL for a filled column -> every row
    for m in re.finditer(r"\bcount\s*\(", s, re.I):
        a = m.end() - 1
        b = C._close(s, a)
        arg = s[a + 1:b]
        if not _top_compare(arg):
            continue
        cond = _flat(sql[a + 1:b])
        new = f"COUNT(CASE WHEN {cond} THEN 1 END)"
        out.append((F("wrong", "count_bool", f"COUNT({cond[:40]}) counts every row, not the matching ones",
                      f"COUNT({cond[:40]}) har row ginta hai, sirf match wali nahi", _flat(sql[m.start():b + 1])[:120],
                      f"A comparison gives true or false (1 or 0 on MySQL/MariaDB), and COUNT counts every value that isn't NULL, so "
                      f"false is counted too: the result is the number of rows where {cond.split()[0]} is filled, on every "
                      f"database. Count only the matches: {new} (or SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)).",
                      f"Comparison true/false (1/0) deta hai aur COUNT har non-NULL value ginta hai, isliye false bhi gina jaata "
                      f"hai. Sirf match gino: {new}."), (m.start(), b + 1, new)))

    # ---- 3. SUM(status = 'paid') / AVG(...): works on MySQL/MariaDB (true = 1), refused on PostgreSQL
    for m in re.finditer(r"\b(sum|avg)\s*\(", s, re.I):
        a = m.end() - 1
        b = C._close(s, a)
        if not _top_compare(s[a + 1:b]):
            continue
        kind, says = _refused("sum_boolean", eng, sql)
        if not kind:
            continue
        fn = m.group(1).upper()
        cond = _flat(sql[a + 1:b])
        new = f"{fn}(CASE WHEN {cond} THEN 1 ELSE 0 END)"
        out.append((F(kind, "sum_bool", f"{fn}({cond[:40]}) adds up true/false, which PostgreSQL refuses",
                      f"{fn}({cond[:40]}) true/false jodta hai, jo PostgreSQL nahi maanta", _flat(sql[m.start():b + 1])[:120],
                      f"{says} {new} counts the matches the same way on every database.",
                      f"{says} {new} har database pe ek jaisa ginta hai."), (m.start(), b + 1, new)))

    nested = []
    # ---- 4. a total inside a total (MAX(COUNT(*))): refused everywhere unless the outer one is a window (... OVER)
    for m in ANY_AGG.finditer(s):
        a = m.end() - 1
        b = C._close(s, a)
        arg = C._no_subqueries(s[a + 1:b])
        im = ANY_AGG.search(arg)
        if not im or re.match(r"\s*over\b", s[b + 1:], re.I) or any(x0 <= m.start() < x1 for x0, x1 in nested):
            continue
        outer, inner = m.group(1).upper(), _flat(sql[a + 1:b])
        edit = None
        top_items = [i for i in items if i["start"] <= m.start() < i["end"]]
        if len(items) == 1 and top_items and dep[m.start()] == 0 and "from" in cl and not cl.get("having") and not cl.get("order by"):
            it = items[0]
            name = it["alias"] or "result"
            fa = s.lower().rfind("from", 0, cl["from"][0] + 1)
            rest = sql[fa:].rstrip().rstrip(";").rstrip()
            new = f"SELECT {outer}(v) AS {name}\nFROM (\n  SELECT {inner} AS v\n  {rest}\n) AS per_group"
            if sql.rstrip().endswith(";"):
                new += ";"
            edit = (0, len(sql), new)
        f = F("error", "nested_agg", f"{outer}({inner[:40]}): a total inside a total", f"{outer}({inner[:40]}): total ke andar total",
              _flat(sql[m.start():b + 1])[:120],
              f"Totals are made once per group: the inner {inner.split('(')[0].upper()}() needs the groups, and {outer}() would need "
              f"them again, in the same SELECT. MySQL/MariaDB refuse it (error 1111, Invalid use of group function), PostgreSQL too "
              f"(42803, aggregate function calls cannot be nested). Make the per-group totals first, then take the {outer} of "
              f"them: SELECT {outer}(v) FROM (SELECT {inner} AS v FROM ... GROUP BY ...) AS per_group."
              + (" To see WHICH group it is, sort the groups instead: ORDER BY " + inner + " DESC LIMIT 1 (or RANK() if ties count)."
                 if len(items) > 1 else ""),
              f"Total ek group par ek baar banta hai; {outer}() ko groups dobara chahiye. MySQL error 1111, PostgreSQL 42803. Pehle "
              f"per-group total banao, phir uska {outer}: SELECT {outer}(v) FROM (SELECT {inner} AS v ... GROUP BY ...) AS per_group.")
        nested.append((m.start(), b + 1))
        out.append((f, edit))

    # ---- 5. a window in a grouped query sees one row per group: a plain column in it is refused / taken from one row
    if group:
        gtext = s[group[0]:group[1]]
        gnames = {w.lower() for w in re.findall(r"[A-Za-z_]\w*", gtext)}
        gkeys = set()                                      # tables grouped by their own key: their other columns are one per group
        for q, c in re.findall(r"([A-Za-z_]\w*)\.(\w+)", gtext):
            x = next((y for y in srcs if (y.alias or y.name or "").lower() == alias_of(q)), None)
            if c.lower() == "id" or (x and x.name and c.lower().replace("_", "") in {n.replace("_", "") + "id" for n in C._singulars(x.name)}):
                gkeys.add(alias_of(q))
        if len(srcs) == 1 and re.fullmatch(r"\s*(?:\w+\.)?id\s*", gtext, re.I):
            gkeys.add((srcs[0].alias or srcs[0].name or "").lower())
        aliases = {i["alias"].lower() for i in items if i["alias"]}
        for it in items:
            low = s[it["start"]:it["end"]]
            om = re.search(r"\bover\s*\(", low, re.I)
            if not om:
                continue
            fm = re.match(r"\s*(\w+)\s*\(", low)
            if not fm:
                continue
            fa = it["start"] + fm.end() - 1
            fb = C._close(s, fa)
            oa = it["start"] + om.end() - 1
            ob = C._close(s, oa)
            loose = []
            for part_a, part_b in ((fa + 1, fb), (oa + 1, ob)):
                text = list(C._no_subqueries(s[part_a:part_b]))
                for am in ANY_AGG.finditer("".join(text)):            # totals inside the window are per group: fine
                    k = am.end() - 1
                    e = C._close("".join(text), k)
                    for z in range(am.start(), e + 1):
                        text[z] = " "
                text = "".join(text)
                for q, c in re.findall(r"(?<![\w.])([A-Za-z_]\w*)\.([A-Za-z_]\w*)", text):
                    if c.lower() not in gnames and alias_of(q) not in gkeys:
                        loose.append(f"{q}.{c}")
                bare = re.sub(r"[A-Za-z_]\w*\.[A-Za-z_]\w*", " ", text)
                for w in re.findall(r"(?<![\w.'])([A-Za-z_]\w*)(?![\w(]|\s*\(|\s*')", bare):
                    wl = w.lower()                         # function names are followed by '(' and typed literals by a quote
                    if wl in NOT_COLS or wl in gnames or wl in aliases or (len(srcs) == 1 and gkeys):
                        continue
                    loose.append(w)
            loose = list(dict.fromkeys(loose))
            if not loose:
                continue
            fn = fm.group(1).upper()
            kind = "wrong" if eng == "mysql" else "error"
            edit = None
            arg_loose = any(x.split(".")[-1].lower() in s[fa + 1:fb].lower() for x in loose)
            over_loose = any(re.search(rf"\b{re.escape(x.split('.')[-1])}\b", C._no_subqueries(s[oa + 1:ob]), re.I) for x in loose)
            wrap = {"SUM": "SUM", "COUNT": "SUM", "MIN": "MIN", "MAX": "MAX"}.get(fn)
            if wrap and arg_loose and not over_loose:
                start = it["start"] + fm.start(1)
                edit = (start, fb + 1, f"{wrap}({sql[start:fb + 1]})")
            fixed = f"{wrap}({_flat(sql[it['start'] + fm.start(1):fb + 1])}) OVER (...)" if wrap else f"{fn}(<a total such as SUM(...)>) OVER (...)"
            _k, says = _refused("window_over_ungrouped", eng, sql)
            out.append((F(kind, "window_agg", f"{fn}() OVER uses {', '.join(loose[:3])}, which isn't grouped",
                          f"{fn}() OVER mein {', '.join(loose[:3])}, jo group nahi hua", _flat(it["text"])[:120],
                          f"In a GROUP BY query the window runs after grouping, on one row per group, so a plain column like "
                          f"{loose[0]} has many values there. {says} Use a total inside the window: {fixed} (the total over all "
                          f"the groups).",
                          f"GROUP BY query mein window grouping ke baad chalta hai, har group ki ek row par, isliye {loose[0]} jaisa plain "
                          f"column wahan kai value rakhta hai. {says} Window ke andar total rakho: {fixed}."), edit))

    # ---- 6. a SELECT name used in HAVING: MySQL/MariaDB allow it, PostgreSQL doesn't
    having = cl.get("having")
    if having:
        agg_alias = {i["alias"].lower(): i for i in items if i["alias"] and i["agg"]}
        htext = s[having[0]:having[1]]
        hits = [x for x in re.finditer(r"(?<![\w.`\"])[`\"]?([A-Za-z_]\w*)[`\"]?(?![\w.(])", htext)
                if x.group(1).lower() in agg_alias and not _inside_agg(htext, x.start())]
        if hits:
            kind, says = _refused("having_alias", eng, sql)
            if kind:
                edits = []
                for x in hits:
                    it = agg_alias[x.group(1).lower()]
                    edits.append((having[0] + x.start(), having[0] + x.end(), C._expr(sql, it)))
                name = hits[0].group(1)
                expr = C._expr(sql, agg_alias[name.lower()])
                f = F(kind, "having_alias", f"HAVING uses {name}, a name made in SELECT",
                      f"HAVING mein {name}, jo SELECT mein banaya naam hai", "HAVING " + _flat(sql[having[0]:having[1]])[:100],
                      f"{says} Writing the total itself (HAVING {expr} ...) works on every database and gives the same groups.",
                      f"{says} Total khud likho (HAVING {expr} ...): har database pe chalta hai, groups wahi.")
                out.append((f, edits[0]))
                for e in edits[1:]:
                    out.append((None, e))

    # ---- 7. COUNT(DISTINCT a, b): MySQL/MariaDB only
    for m in re.finditer(r"\bcount\s*\(\s*distinct\b", s, re.I):
        a = s.index("(", m.start())
        b = C._close(s, a)
        parts = C._split_top(s[m.end():b])
        if len(parts) < 2:
            continue
        kind, says = _refused("count_distinct_multi", eng, sql)
        if not kind:
            continue
        cols = [_flat(sql[m.end() + off:m.end() + off + len(p)]) for off, p in parts]
        edit = None
        whole = re.fullmatch(r"\s*select\s+count\s*\(\s*distinct\b[^()]*\)\s*(?:as\s+(\w+)\s*)?from\s+([\w$.`\"]+)(?:\s+(?:as\s+)?(?!where\b)(\w+))?"
                             r"\s*(?:where\s+(?P<w>.*?))?\s*;?\s*", s, re.I | re.S)
        if whole and len(items) == 1:
            w = f"({_flat(sql[whole.start('w'):whole.end('w')])}) AND " if whole.group("w") else ""
            notnull = " AND ".join(f"{c} IS NOT NULL" for c in cols)
            t = _flat(sql[whole.start(2):whole.end(3) if whole.group(3) else whole.end(2)])
            new = (f"SELECT COUNT(*){' AS ' + whole.group(1) if whole.group(1) else ''}\nFROM (SELECT DISTINCT {', '.join(cols)} "
                   f"FROM {t} WHERE {w}{notnull}) AS d" + (";" if sql.rstrip().endswith(";") else ""))
            edit = (0, len(sql), new)
        out.append((F(kind, "count_multi", f"COUNT(DISTINCT {', '.join(cols)[:50]}): several columns in one COUNT",
                      f"COUNT(DISTINCT {', '.join(cols)[:50]}): ek COUNT mein kai columns", _flat(sql[m.start():b + 1])[:120],
                      f"{says} The same count on every database: SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(cols)} FROM ... "
                      f"WHERE {' AND '.join(c + ' IS NOT NULL' for c in cols)}) AS d (MySQL skips a combination with a NULL in it; "
                      f"PostgreSQL's COUNT(DISTINCT (a, b)) would count it, so the NULL filter keeps the MySQL meaning).",
                      f"{says} Har database pe wahi ginti: SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(cols)} ... WHERE ... IS NOT "
                      f"NULL) AS d."), edit))

    # ---- 8. SUM of the outer side of a LEFT JOIN: NULL, not 0, for rows with no match
    lefts = {(x.alias or x.name or "").lower() for x in srcs if x.kind == "left"}
    where = cl.get("where")
    wtext = s[where[0]:where[1]].lower() if where else ""
    if group and lefts:
        from app.agents.sqlbasics import _inside_null_fn

        for it in items:
            for m in re.finditer(r"\bsum\s*\(\s*([A-Za-z_]\w*)\.(\w+)\s*\)", s[it["start"]:it["end"]], re.I):
                q = alias_of(m.group(1))
                p = it["start"] + m.start()
                if q not in lefts or _inside_null_fn(s, p) or re.search(rf"\b{re.escape(m.group(1).lower())}\.", wtext) \
                        or re.match(r"\s*over\b", s[it["start"] + m.end():], re.I):
                    continue
                call = sql[p:it["start"] + m.end()]
                new = f"COALESCE({call}, 0)"
                tn = next((x.name for x in srcs if (x.alias or x.name or "").lower() == q and x.name), m.group(1)).split(".")[-1].strip('`"')
                out.append((F("check", "sum_null", f"{call} is NULL, not 0, when there is no {tn} row",
                              f"{tn} ki row na ho to {call} NULL hai, 0 nahi", _flat(it["text"])[:120],
                              f"A row kept by the LEFT JOIN with no match has NULL in every {tn} column, and SUM of nothing but "
                              f"NULLs is NULL (COUNT gives 0), on every database. If 'none' should show as 0: {new}.",
                              f"LEFT JOIN mein bina match wali row ke {m.group(1)} columns NULL hain, aur sirf NULL ka SUM NULL hai. "
                              f"'Kuch nahi' ko 0 dikhana ho to: {new}."), (p, it["start"] + m.end(), new)))

    # ---- 9. SUM(DISTINCT x) / AVG(DISTINCT x): equal amounts of different rows count once
    for m in re.finditer(r"\b(sum|avg)\s*\(\s*distinct\b", s, re.I):
        a = s.index("(", m.start())
        b = C._close(s, a)
        call = _flat(sql[m.start():b + 1])
        joined = len(srcs) > 1
        out.append((F("check", "sum_distinct", f"{call[:50]} drops equal amounts of different rows",
                      f"{call[:50]} alag rows ki barabar raqam ek hi baar jodta hai", call[:120],
                      f"DISTINCT works on the values, not the rows: two orders of 500 add up to 500, not 1000 (the same on every "
                      f"database). " + ("If DISTINCT is there to undo the copies a join made, total the child table first in its own "
                                        "subquery (GROUP BY its key) and join that, then use a plain SUM."
                                        if joined else "If every row should count, use a plain SUM."),
                      f"DISTINCT values pe chalta hai, rows pe nahi: 500 ke do orders ka jod 500 aata hai, 1000 nahi. "
                      + ("Agar DISTINCT join ki copies hataane ke liye hai, to child table ka total pehle alag subquery mein nikaalo "
                         "aur use join karo." if joined else "Har row ginni hai to plain SUM likho.")), None))

    avg_defs = []
    # ---- 10. AVG of a per-group average: every group weighs the same, whatever its size
    for m in re.finditer(r"\bavg\s*\(\s*(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)\s*\)", s, re.I):
        name = m.group(2)
        d_use = dep[m.start()]
        defn = None
        for dm in re.finditer(rf"\bavg\s*\((?P<e>(?:[^()]|\([^()]*\))*)\)\s+(?:as\s+)?[`\"]?{re.escape(name)}[`\"]?(?![\w])", s, re.I):
            if dep[dm.start()] > d_use or dm.start() < m.start() and re.match(r"\s*with\b", s, re.I):
                defn = dm
                break
        if not defn or re.search(r"average of (?:the )?averages|averages ka average|each .* (?:counts?|weighs?) the same|equal weight", said):
            continue
        inner_e = _flat(sql[defn.start("e"):defn.end("e")])
        new_def = f"{sql[defn.start():defn.end()]}, SUM({inner_e}) AS {name}_sum, COUNT({inner_e}) AS {name}_n"
        q = f"{m.group(1)}." if m.group(1) else ""
        new_use = f"1.0 * SUM({q}{name}_sum) / SUM({q}{name}_n)"
        f = F("check", "avg_avg", f"AVG({name}) is an average of averages",
              f"AVG({name}) averages ka average hai", _flat(sql[m.start():m.end()]),
              f"{name} is already an average per group, so AVG({name}) gives a group of 1 row the same weight as a group of 100 "
              f"(the same on every database). The average over all the rows is the total divided by the count: carry "
              f"SUM({inner_e}) and COUNT({inner_e}) out of the inner query and use {new_use}.",
              f"{name} pehle se har group ka average hai, isliye AVG({name}) 1 row wale group ko 100 row wale jitna wazan deta hai. "
              f"Saari rows ka average = total / ginti: andar se SUM aur COUNT bahar laao aur {new_use} lo.")
        avg_defs.append((defn.start(), defn.end()))
        out.append((f, (defn.start(), defn.end(), new_def)))
        out.append((None, (m.start(), m.end(), new_use)))
        break

    # ---- 11. AVG of a column that is often empty (discount, commission): NULLs are skipped, not counted as 0
    from app.agents.sqlbasics import OPTIONAL_NUM, _inside_null_fn

    for m in re.finditer(r"\bavg\s*\(\s*(?:([A-Za-z_]\w*)\.)?(\w+)\s*\)", s, re.I):
        col = m.group(2).lower()
        if any(x0 <= m.start() < x1 for x0, x1 in avg_defs):
            continue                                       # it is the inner average of an average of averages (said above)
        if col not in OPTIONAL_NUM or _inside_null_fn(s, m.start() + 4) or re.search(r"\bonly\b|\bamong\b|\bwho (?:got|have|has|had)\b|jinko|jinhe", said):
            continue
        call = sql[m.start():m.end()]
        ref = sql[s.index("(", m.start()) + 1:m.end() - 1].strip()
        new = f"AVG(COALESCE({ref}, 0))"
        out.append((F("check", "avg_null", f"{call} leaves out the rows with no {col}",
                      f"{call} bina {col} wali rows ko chhod deta hai", call,
                      f"AVG adds the values that are there and divides by how many are there, so rows where {col} is NULL are left "
                      f"out entirely (not counted as 0), on every database: the average of those who have a {col}. If 'none' means 0 "
                      f"and every row should count: {new}.",
                      f"AVG sirf bhari values ka average leta hai; jahan {col} NULL hai wo rows gini hi nahi jaati (0 nahi maani "
                      f"jaati). Agar khaali ka matlab 0 hai aur har row ginni hai: {new}."), (m.start(), m.end(), new)))

    # ---- 12. what was asked to be counted: 'how many customers' while the query counts order rows
    out += _count_said(sql, s, cl, srcs, alias_of, items, said, F)

    # ---- 13. a share of a total that ignores the outer WHERE: the shares don't add up to 100%
    if where:
        for m in re.finditer(r"/\s*\(\s*select\s+(sum|count)\s*\(([^()]*)\)\s+from\s+[`\"]?([\w$]+)[`\"]?(?:\s+(?:as\s+)?(?!where\b)([A-Za-z_]\w*))?\s*\)",
                             s, re.I):
            t = m.group(3).lower()
            if len(srcs) != 1 or (srcs[0].name or "").split(".")[-1].strip('`"').lower() != t:
                continue
            wsql = _flat(sql[where[0]:where[1]]).rstrip(";").strip()
            if not wsql:
                continue
            oal = srcs[0].alias if srcs[0].alias and srcs[0].alias.lower() != t else ""
            close = m.end() - 1
            ins = (f" {oal}" if oal and not m.group(4) else "") + f" WHERE {wsql}"
            pos = close
            while pos > 0 and sql[pos - 1].isspace():
                pos -= 1
            out.append((F("check", "pct_total", f"The total in the share ignores WHERE {wsql[:40]}",
                          f"Share ka total WHERE {wsql[:40]} ko nahi maanta", _flat(sql[m.start():m.end()])[:120],
                          f"The rows are filtered by WHERE {wsql}, but (SELECT {m.group(1).upper()}(...) FROM {m.group(3)}) adds up every "
                          f"row of {m.group(3)}, so the shares are parts of a bigger total and don't add up to 100% (same on every "
                          f"database). For shares of the filtered rows, give the subquery the same WHERE, or divide by "
                          f"SUM({m.group(1).upper()}(...)) OVER () (MySQL 8+, MariaDB 10.2+, PostgreSQL).",
                          f"Rows WHERE {wsql} se chhanti hain, par subquery {m.group(3)} ki saari rows jodti hai, isliye shares 100% tak "
                          f"nahi pahunchte. Subquery mein bhi wahi WHERE do."), (pos, pos, ins)))
    return out


_SAID_COUNT = [r"\bhow many ((?:[a-z_]+ ){0,2}[a-z_]+)", r"\bnumber of ((?:[a-z_]+ ){0,2}[a-z_]+)", r"\bcount (?:of )?(?:the |all )?((?:[a-z_]+ ){0,2}[a-z_]+)",
               r"\bkitn[eia] ((?:[a-z_]+ ){0,2}[a-z_]+)", r"\b([a-z_]+) ki (?:ginti|sankhya|count)\b"]
_NOT_NOUNS = set("""unique different distinct separate total all the a an of active new old paid unpaid open closed rows row records record
times time entries entry results result lines line ones one values value of with have has had in on for per each every""".split())


def _inside_agg(text: str, pos: int) -> bool:
    """Is this spot inside the brackets of a total (SUM(total)): there the name is the table's column, not the SELECT name."""
    depth = 0
    for i in range(pos - 1, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            if depth == 0:
                return bool(ANY_AGG.search(text[max(0, i - 20):i + 1]) and re.search(r"\w+\s*\($", text[:i + 1]) and
                            ANY_AGG.match(re.search(r"(\w+)\s*\($", text[:i + 1]).group(0)))
            depth -= 1
    return False


def _norm(c: str) -> str:
    return c.lower().replace("_", "")


def _count_said(sql, s, cl, srcs, alias_of, items, said, F) -> list:
    """'how many customers ...' while the rows counted belong to another table (orders): each customer is counted once per
    order. The noun the person said is matched to a table of the query, or to a '<noun>_id' column in it."""
    from app.agents import sqljoins as J

    if not said or not srcs:
        return []
    names = {J._one(J._base(x.name)): x for x in srcs if x.name}
    for rx in _SAID_COUNT:
        for nm in re.finditer(rx, said):
            words = [w for w in nm.group(1).split() if w not in _NOT_NOUNS]
            if not words:
                continue
            noun = J._one(words[0])
            if len(noun) < 3 or noun in _NOT_NOUNS:
                continue
            for it in items:
                cm = re.match(r"\s*count\s*\(\s*(\*|(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*))\s*\)\s*(?:(?:as\s+)?\w+\s*)?$",
                              s[it["start"]:it["end"]], re.I)
                if not cm:
                    continue
                q, col = cm.group(2), (cm.group(3) or "")
                fk = noun + "_id"
                target = names.get(noun)
                counted, guessed = None, False
                if target is not None:                       # the noun's own table is joined: are its rows repeated by a child?
                    tal = (target.alias or target.name).lower()
                    kids = [x for x in srcs if x is not target and x.name and x.on and
                            re.search(rf"\b{re.escape((x.alias or x.name).lower())}\.[`\"]?{fk}\b", x.on.lower().replace("`", ""))]
                    if kids and (cm.group(1) == "*" or (q and alias_of(q) != tal)):
                        key = J._key(target, {(alias_of(a), c) for a, c in re.findall(r"([A-Za-z_]\w*)\.(\w+)", s)})
                        counted = f"{target.alias or target.name}.{key}"
                elif len(srcs) == 1 and srcs[0].name and J._one(J._base(srcs[0].name)) != noun and \
                        not re.search(rf"\b{re.escape(srcs[0].name.strip('`\"'))}\s*(\([^()]*\))?\s+as\s*\(", s, re.I):
                    # (a CTE's rows are whatever its body makes them: night 12 reads them as a block, not as a table name)
                    x = srcs[0]
                    pre = f"{x.alias}." if x.alias and x.alias.lower() != x.name.split(".")[-1].strip('`"').lower() else ""
                    if col and _norm(col) == _norm(fk):
                        counted = f"{pre}{col}"
                    elif cm.group(1) == "*":
                        shown = next((c for c in re.findall(r"(?<![\w.])([A-Za-z_]\w*)", s) if _norm(c) == _norm(fk)), None)
                        counted, guessed = f"{pre}{shown or fk}", not shown
                if not counted:
                    continue
                a = it["start"] + re.match(r"\s*", s[it["start"]:]).end()
                b = _helpers()._close(s, s.index("(", a)) + 1
                new = f"COUNT(DISTINCT {counted})" + (f" /* check: the column that points at the {noun} */" if guessed else "")
                what = J._base(next((y.name for y in srcs if y.name and y is not target), "joined")).replace("_", " ")
                out = [(F("wrong", "count_said", f"You asked for the number of {words[0]}; this counts {what} rows",
                          f"Aapne {words[0]} ki ginti maangi; ye {what} ki rows ginta hai", _flat(sql[a:b]),
                          f"One {noun} can have many {what} rows here, so {_flat(sql[a:b])} counts those rows, and a {noun} with 3 of "
                          f"them is counted 3 times (the same on every database). Count each {noun} once: {new}"
                          + (f" (if the column is called {fk})." if guessed else "."),
                          f"Ek {noun} ki yahan kai {what} rows ho sakti hain, isliye ye rows gini jaati hain. Har {noun} ek baar gino: "
                          f"{new}."), (a, b, new))]
                return out
    return []
