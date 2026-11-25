"""Night X1: when a mistake has more than one honest repair, each one written out, runnable, with what it means.

A checker that only says "move this condition" leaves the work to the person. The repairs below are the general rewrites of
two mistake families whose right answer depends on what the result is FOR (the person's words decide; otherwise every
reading is shown, each under "if that's what you meant"):

  outer join undone by WHERE   LEFT JOIN b ... WHERE b.x > 5: rows kept only by the LEFT JOIN have NULL in b.x and the WHERE
                               drops them. Reading 1, every row of the left table with only the matching b rows: the
                               condition moves into the ON of b's join (it then chooses which b rows match, not which result
                               rows stay). Reading 2, only rows that have a match: INNER JOIN (the checker's own fix).
  columns that aren't grouped  SELECT g, c, MAX(x) ... GROUP BY g: c has many values per group. Reading 1, one row per g as the
                               GROUP BY says: c leaves the SELECT. Reading 2, one row per g and c: c joins the GROUP BY.
                               Reading 3, c of the row that holds each group's MAX(x)/MIN(x): the rows whose (g, x) is in the
                               grouped result (every tied row is shown; a row whose g is NULL can't match, as NULL equals
                               nothing).

Each rewrite is checked again by the checker and kept only when nothing is left that a database refuses or answers wrongly.
Lab-proven on MariaDB and PostgreSQL by tests/fuzz/stmtbugs.py (families dx_*) against queries written independently.
"""

from __future__ import annotations

import re

# words that say every row of the first table must stay (with zero / NULL where nothing matches)
KEEP_ALL = re.compile(r"\b(all|every|each|empty|zero|none|without|includ\w*|even|missing|fail\w*\s+to\s+(?:return|show)|"
                      r"no\s+\w+|0|sab|sabhi|saare|har|khali|bina|bhi)\b|सभी|सब|हर|खाली|बिना|भी", re.I)
# words that ask for the row holding the highest / lowest value (who earns the most in each department)
ROW_OF_EXTREME = re.compile(r"\b(who|which|whose|name\s+of|highest|lowest|top|most|least|max\w*|min\w*|kaun|kis|sabse)\b|"
                            r"कौन|किस|सबसे", re.I)


def alternatives(sql: str, out: dict, intent: str = "") -> list[dict]:
    """The other honest repairs of this statement's mistakes, each {'key', 'en', 'hi', 'sql'}; may also make one of them the fix
    (out['corrected']) when the person's words say which reading they meant."""
    from app.agents.sql import _split_tail

    sql = _split_tail(sql)[0].strip().rstrip(";").rstrip()     # the statement only, never the words written after it
    fs = out.get("findings") or []
    alts = []
    if any(f["topic"] == "join" and "cancels the" in f["title_en"] for f in fs):
        on = _checked(where_to_on(sql))
        if on:
            inner = out.get("corrected")
            if KEEP_ALL.search(intent or ""):            # their words: every row of the first table, zero where nothing matches
                for f in fs:
                    if f["topic"] == "join" and "cancels the" in f["title_en"]:
                        f["kind"] = "wrong"
                out["corrected"] = on
                if inner and inner != on:
                    alts.append({"key": "inner", "sql": inner, "en": "If you only want rows that have a match (INNER JOIN):",
                                 "hi": "Agar sirf match wali rows chahiye (INNER JOIN):"})
            else:
                alts.append({"key": "on", "sql": on,
                             "en": "If you want every row of the first table, with only the matching rows joined (the condition "
                                   "moves into the ON):",
                             "hi": "Agar pehli table ki har row chahiye, sirf match wali rows jud kar (condition ON mein):"})
    if any(f["topic"] == "group" and f["kind"] == "error" and f["title_en"].startswith("Columns in SELECT") for f in fs):
        g = group_rewrites(sql)
        if g:
            drop, add, extreme = (_checked(g.get(k)) for k in ("drop", "add", "extreme"))
            loose = ", ".join(g["loose"])
            want_row = bool(extreme) and bool(ROW_OF_EXTREME.search(intent or ""))
            first = extreme if want_row else drop or add
            if first and not out.get("corrected"):
                out["corrected"] = first
            for key, q, en, hi in (
                    ("drop", drop, f"One row per {g['group']} (as your GROUP BY says): {loose} leaves the SELECT:",
                     f"Har {g['group']} ki ek row (jaisa GROUP BY kehta hai): {loose} SELECT se hata:"),
                    ("add", add, f"One row per {g['group']} and {loose}: add it to GROUP BY:",
                     f"Har {g['group']} aur {loose} ki ek row: use GROUP BY mein jodo:"),
                    ("extreme", extreme, f"The {loose} of the row with the {g['fn_en']} {g['arg']} in each {g['group']} "
                                         f"(ties all shown):",
                     f"Har {g['group']} mein sabse {g['fn_hi']} {g['arg']} wali row ka {loose} (barabar wale sab):")):
                if q and q != out.get("corrected"):
                    alts.append({"key": key, "sql": q, "en": "If you meant: " + en[0].lower() + en[1:], "hi": "Agar matlab: " + hi})
    return alts


def _checked(q: str | None) -> str | None:
    """The rewrite, after the checker's own sure fixes; None if a database would still refuse it or answer wrongly."""
    if not q:
        return None
    from app.agents import sqlcorrect

    try:
        r = sqlcorrect.check(q, _again=True)
        if any(f["kind"] in ("error", "wrong") for f in r["findings"]):
            q = r.get("corrected")
            if not q or any(f["kind"] in ("error", "wrong") for f in sqlcorrect.check(q, _again=True)["findings"]):
                return None
    except Exception:  # noqa: BLE001 - an alternative is an extra; never let it break the answer
        return None
    return q


# ------------------------------------------------------------------------------------------------ outer join: WHERE -> ON
def where_to_on(sql: str) -> str | None:
    """Each AND-part of the WHERE that tests only a LEFT JOINed table (and isn't its IS NULL anti-join test) moved into that
    table's ON. None when the WHERE has a top-level OR (the parts don't stand alone) or nothing can move."""
    from app.agents.sqlcorrect import _clauses, _depths, _mask, _qrefs, _sources

    s = _mask(sql)
    dep = _depths(s)
    cl = _clauses(s, dep)
    if "from" not in cl or "where" not in cl or not re.match(r"\s*select\b", s, re.I):
        return None
    srcs = _sources(s, dep, *cl["from"])
    by = {(x.alias or x.name).lower(): x for x in srcs if x.alias or x.name}
    left = {k for k, x in by.items() if x.kind == "left" and x.on_end}
    if not left:
        return None
    wa, wb = cl["where"]
    wtext = s[wa:wb]
    wdep = dep[wa:wb]
    if any(wdep[m.start()] == 0 for m in re.finditer(r"\bor\b", wtext, re.I)):
        return None
    cuts = [m for m in re.finditer(r"\band\b", wtext, re.I) if wdep[m.start()] == 0 and
            not re.search(r"\bbetween\s+\S+\s*$", wtext[:m.start()], re.I)]
    bounds, prev = [], 0
    for m in cuts:
        bounds.append((prev, m.start()))
        prev = m.end()
    bounds.append((prev, len(wtext)))
    moves: dict[str, list[str]] = {}
    keep = []
    for a, b in bounds:
        part_s, part = s[wa + a:wa + b], sql[wa + a:wa + b].strip()
        if not part:
            continue
        refs = {q.lower() for q, _c, _p in _qrefs(part_s)}
        bare = _bare_cols(part_s)
        if len(refs) == 1 and not bare and next(iter(refs)) in left and not re.search(r"\(\s*select\b", part_s, re.I) and \
                not re.search(r"\bis\s+null\b|\bcoalesce\s*\(|\bifnull\s*\(|\bisnull\s*\(|\bnvl\s*\(", part_s, re.I):
            moves.setdefault(next(iter(refs)), []).append(part)
        else:
            keep.append(part)
    if not moves:
        return None
    edits = []
    for al, conds in moves.items():
        x = by[al]
        end = x.on_end
        while end > x.on_start and sql[end - 1].isspace():
            end -= 1
        edits.append((end, end, " AND " + " AND ".join(conds)))
    kw = [m for m in re.finditer(r"\bwhere\b", s[:wa], re.I) if dep[m.start()] == 0][-1]
    tail_ws = re.search(r"\s*$", sql[wa:wb]).group(0)
    edits.append((kw.start(), wb, ("WHERE " + " AND ".join(keep) + (tail_ws or " ")) if keep else ""))
    out = sql
    for a, b, new in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:a] + new + out[b:]
    return re.sub(r"[ \t]+\n", "\n", out).strip() if out.strip() != sql.strip() else None


def _bare_cols(text: str) -> list[str]:
    from app.agents.sqlcorrect import _bare

    clean = re.sub(r"'[^']*'", "''", text)
    return [w for w in _bare(clean, set()) if not re.fullmatch(r"\d+", w)]


# ------------------------------------------------------------------------------------------------ columns that aren't grouped
def group_rewrites(sql: str) -> dict | None:
    from app.agents.sqlcorrect import AGG, _clauses, _depths, _mask, _qrefs, _split_top

    s = _mask(sql)
    dep = _depths(s)
    cl = _clauses(s, dep)
    if not re.match(r"\s*select\b", s, re.I) or "group by" not in cl or "from" not in cl or "union" in cl:
        return None
    sa, sb = cl["select"]
    if re.match(r"\s*distinct\b", s[sa:sb], re.I):
        return None
    items = []
    for p, txt in _split_top(s[sa:sb]):
        lead = len(txt) - len(txt.lstrip())
        st = sa + p + lead
        items.append((st, st + len(txt.strip()), txt.strip()))
    ga, gb = cl["group by"]
    groups = [x.strip() for _p, x in _split_top(sql[ga:gb]) if x.strip()]
    gnorm = {_norm(x) for x in groups}
    if any(re.fullmatch(r"\d+", g) for g in groups):
        return None
    gcols = {c.lower() for g in groups for _q, c, _p in _qrefs(_mask(g))}
    loose, aggs = [], []
    for a, b, txt in items:
        expr = _strip_alias(sql[a:b].strip())
        if AGG.search(txt):
            aggs.append((a, b, sql[a:b]))
            continue
        if _norm(expr) in gnorm or _norm(sql[a:b]) in gnorm:
            continue
        refs = _qrefs(_mask(expr))
        if refs and all(c.lower() in gcols for _q, c, _p in refs) and not re.fullmatch(r"\S+\.\S+", expr.strip()):
            continue                                       # UPPER(d.name) with d.name grouped
        if re.fullmatch(r"'[^']*'|\d+(\.\d+)?|null", expr.strip(), re.I):
            continue                                       # a constant: the same in every group
        loose.append((a, b, sql[a:b].strip(), expr.strip()))
    if not loose or not aggs:
        return None
    gname = groups[0] if len(groups) == 1 else "(" + ", ".join(groups) + ")"
    out = {"loose": [x[3] for x in loose], "group": gname}
    keep_items = [sql[a:b].strip() for a, b, _t in items if not any(a == x[0] for x in loose)]
    if keep_items:
        out["drop"] = sql[:sa] + " " + ", ".join(keep_items) + " " + sql[sb:].lstrip()
    out["add"] = sql[:gb].rstrip() + ", " + ", ".join(x[3] for x in loose) + (" " + sql[gb:].lstrip() if sql[gb:].strip() else sql[gb:])
    out["drop"] = _tidy(out.get("drop"))
    out["add"] = _tidy(out["add"])
    # the row holding each group's MAX/MIN: exactly one MIN()/MAX() of a plain column, nothing else totalled, no HAVING
    if len(aggs) == 1 and "having" not in cl:
        m = re.fullmatch(r"(?is)(max|min)\s*\(\s*((?:[`\"]?\w+[`\"]?\.)?[`\"]?\w+[`\"]?)\s*\)(\s+(?:as\s+)?[`\"]?\w+[`\"]?)?", aggs[0][2].strip())
        if m:
            fn, arg, alias = m.group(1).upper(), m.group(2), m.group(3) or ""
            body_from = sql[cl["from"][0] - 4:cl["where"][1] if "where" in cl else cl["from"][1]].strip()   # 'FROM ... [WHERE ...]'
            if not re.match(r"(?i)from\b", body_from):
                return out
            cut = cl["group by"][1]
            rest = sql[cut:].strip()
            outer_items = []
            for a, b, _t in items:
                if a == aggs[0][0]:
                    outer_items.append(arg + (alias if alias.strip() else f" AS {fn.lower()}_{arg.split('.')[-1].strip('`\"')}"))
                else:
                    outer_items.append(sql[a:b].strip())
            key = ", ".join(groups + [arg])
            inner = f"SELECT {', '.join(groups)}, {fn}({arg}) {body_from} GROUP BY {', '.join(groups)}"
            cond = f"({key}) IN ({inner})"
            if "where" in cl:
                wa, wb = cl["where"]
                outer_from = sql[cl["from"][0] - 4:cl["from"][1]].strip()
                where = f"WHERE ({sql[wa:wb].strip()}) AND {cond}"
            else:
                outer_from, where = body_from, f"WHERE {cond}"
            out["extreme"] = _tidy(f"SELECT {', '.join(outer_items)} {outer_from} {where}" + (f" {rest}" if rest else ""))
            out.update(fn_en="highest" if fn == "MAX" else "lowest", fn_hi="zyada" if fn == "MAX" else "kam", arg=arg)
    return out


def _strip_alias(t: str) -> str:
    """The expression without its SELECT name: 'SUM(x) AS total' -> 'SUM(x)', 'e.name n' -> 'e.name'; CASE ... END keeps END."""
    from app.agents.sqlcorrect import KW

    m = re.fullmatch(r"(?is)(.*?[\w`\")\]])(\s+as)?\s+[`\"]?([A-Za-z_]\w*)[`\"]?\s*", t)
    if m and (m.group(2) or m.group(3).lower() not in KW) and m.group(3).lower() not in KW:
        return m.group(1).strip()
    return t


def _norm(t: str) -> str:
    return re.sub(r"[`\"\s]", "", t).lower()


def _tidy(q: str | None) -> str | None:
    if not q:
        return q
    return re.sub(r"[ \t]{2,}", " ", q).strip()
