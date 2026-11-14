"""Safe rewrites with a proof of equivalence (night 14 of the curriculum), shared by every database (fundamental
'equivalence' in app/dbknow/core/query_fundamentals.json; what each rewrite keeps, and on which conditions, in
app/dbknow/core/rewrites.json; what each engine does in the dialect files' query_rules, per standing rule B).

A rewrite is only 'the same query' when five things stay the same, and each one is checked, not assumed:
- matching : which outer rows find a partner (an equality never matches a NULL, in a join or in a subquery alike);
- NULLs    : what a row with nothing to total gets (COUNT gives 0, SUM / MIN / MAX / AVG give NULL);
- duplicates: how many times each row comes out (a join to a table with several matches repeats the outer row; IN and EXISTS
              never do);
- ordering : the ORDER BY decides the order, and nothing else; a page boundary needs an order with no ties;
- grain    : what one row of the result is (a derived table grouped by its join key has one row per key, so a LEFT JOIN to it
             never adds or loses outer rows).

Rewrites made here (each offered, never applied as a 'fix'; the original query is not wrong):
- preagg_subquery: a correlated total in SELECT / WHERE / ORDER BY (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id)
  is a small query run for each outer row. Totalling the child table once, grouped by its link column, and LEFT JOINing that
  gives the same values: COUNT wrapped in COALESCE(.., 0) because a missing group is NULL after a LEFT JOIN but 0 as a
  correlated COUNT (lab, both engines); other totals stay NULL both ways. Faster when most outer rows are read; when the
  outer WHERE keeps a few rows the per-row form can win, so it is a speed risk for EXPLAIN ANALYZE to decide.
- to_exists / to_in / to_join, when the person asks for that form: x IN (SELECT y ...) <-> EXISTS (... AND y = x) give the
  same rows in a WHERE that doesn't negate them (IN can be NULL where EXISTS is false, and only NOT tells them apart);
  IN -> JOIN only through SELECT DISTINCT, because a plain JOIN repeats an outer row for every match (lab: 8 rows vs 5).
- expr_once: the same calculation written in SELECT and again in ORDER BY: ORDER BY the SELECT name sorts by the very same
  value on both engines (WHERE can't use the name, lab 1054 / 42703, so no rewrite is offered there).
Returns [(Finding, [edits])], edit = (start, end, new text) on the original query; (0, len(sql), text) rewrites it whole.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "dbknow" / "core" / "rewrites.json"
# the engines each rewrite was proven on (tests/practice/test_night14.py, LAB and the stmtbugs generator) -- rule B
PROVEN = {k: ["mariadb", "postgresql"] for k in ("preagg_subquery", "to_exists", "to_in", "to_join", "expr_once")}
AGG1 = re.compile(r"^\s*(count|sum|avg|min|max)\s*\(([\s\S]*)\)\s*$", re.I)
NONDET = re.compile(r"\b(rand|random|uuid|uuid_short|gen_random_uuid|sysdate|clock_timestamp|nextval|newid|last_insert_id|"
                    r"row_count|found_rows|setval)\s*\(", re.I)
AGG_ANY = re.compile(r"\b(count|sum|avg|min|max|group_concat|string_agg|array_agg|bool_and|bool_or|stddev\w*|variance)\s*\(", re.I)
NAME = r"[`\"]?[A-Za-z_]\w*[`\"]?"
QCOL = rf"({NAME})\s*\.\s*({NAME})"
# the person asks for another form of the same query: a verb or a form word, and the form named
ASK_VERB = re.compile(r"\b(rewrite|re-write|write|written|make|convert|change|replace|turn|instead|use|using|with|into|as|form|version|"
                      r"equivalent|"
                      r"badl\w*|likh\w*|bana\w*|se|mein|me|ko)\b|बदल|लिख|बना|से|में", re.I)
SAYS_EXISTS = re.compile(r"\bexists\b|एग्जिस्ट्स", re.I)
SAYS_IN = re.compile(r"(?-i:\bIN\b)|\b(?:to|into|using|use|with|as|via)\s+in\b(?!\s+(?:the|a|an|this|that|my|our|your|which|"
                     r"order|case|sql|mysql|maria\w*|postgres\w*|production|code|php|python|english|hindi|hinglish)\b)|"
                     r"\bin\s+(?:clause|subquery|list|operator|ko|se|me|mein|form|version)\b|\bin\s*\(", re.I)
SAYS_JOIN = re.compile(r"\bjoin\b|जॉइन", re.I)


@lru_cache(maxsize=1)
def contracts() -> dict:
    return json.loads(CORE.read_text(encoding="utf-8"))


def contract(topic: str) -> dict | None:
    """What a rewrite keeps the same, and on which conditions (None for a topic with no same-rows rewrite)."""
    c = contracts()["rewrites"].get(topic)
    return dict(c, topic=topic) if c else None


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


def _says(key: str) -> str:
    from app.agents import sqlbasics

    eng = sqlbasics.engine_seen("")
    fam = "mysql" if eng == "mysql" else "postgresql" if eng == "postgresql" else None
    if fam:
        return sqlbasics.rules(fam).get(key, {}).get("says", "")
    return " ".join(x for x in (sqlbasics.rules("mysql").get(key, {}).get("says", ""),
                                sqlbasics.rules("postgresql").get(key, {}).get("says", "")) if x)


def rewrites(sql: str, s: str, intent: str, F, is_select: bool) -> list:
    """Night 14's rewrites for a statement: [(Finding, [edits])]."""
    out = []
    if not is_select or not re.match(r"\s*select\b", s, re.I):
        return out
    try:
        top = _top(sql, s)
        if top is None:
            return out
        out += _preagg(sql, s, top, F)
        out += _asked(sql, s, top, intent or "", F)
        out += _expr_once(sql, s, top, F)
    except Exception:  # noqa: BLE001 - a rewrite offer must never stop the main check
        return out
    for f, edits in out:                                 # each offer carries its own rewritten query, whatever else is fixed
        new = sql
        for a, b, t in sorted(edits, key=lambda e: e[0], reverse=True):
            new = new[:a] + t + new[b:]
        f.rewrite = new if edits else None
    return out


# ------------------------------------------------------------------------------------------------ the top-level query
def _top(sql: str, s: str) -> dict | None:
    C = _C()
    if re.search(r"\b(union|intersect|except)\b", C._no_subqueries(s), re.I):
        return None
    dep = C._depths(s)
    cl = C._clauses(s, dep)
    if "from" not in cl or "select" not in cl:
        return None
    srcs = C._sources(s, dep, *cl["from"])
    if not srcs or any(not x.name for x in srcs):
        return None
    fa, fb = cl["from"]
    ftext = C._no_subqueries(s[fa:fb])
    comma = "," in ftext
    return {"cl": cl, "dep": dep, "srcs": srcs, "comma": comma,
            "names": {_uq(x.alias or x.name.split(".")[-1]): x for x in srcs} | {_uq(x.name.split(".")[-1]): x for x in srcs}}


def _alias(x) -> str:
    """The alias written in the query ('' when the table has none: the reader fills in the table's own name)."""
    a = x.alias or ""
    return "" if _uq(a) == _uq(x.name.split(".")[-1]) else a


def _clause_of(pos: int, cl: dict) -> str | None:
    for k, (a, b) in cl.items():
        if a <= pos < b:
            return k
    return None


def _conjuncts(text: str, base: int) -> list | None:
    """Top-level AND parts of a condition: [(start, end)] in the query; None when an OR / NOT joins them at the top."""
    C = _C()
    d = C._depths(text)
    flat = C._no_subqueries(text)
    if re.search(r"\bor\b|\bxor\b|\|\|", flat, re.I):
        return None
    cuts = [m for m in re.finditer(r"\band\b", text, re.I) if d[m.start()] == 0]
    btw = [m for m in re.finditer(r"\bbetween\b", text, re.I) if d[m.start()] == 0]
    cuts = [m for m in cuts if not any(b.end() < m.start() and not any(b.end() < c.start() < m.start() for c in cuts) for b in btw)]
    out, last = [], 0
    for m in cuts:
        out.append((base + last, base + m.start()))
        last = m.end()
    out.append((base + last, base + len(text)))
    return out


def _negated(s: str, top: dict, pos: int) -> bool:
    """Is the position under a NOT (or inside a CASE / a SELECT item) where IN's NULL and EXISTS' false would differ?"""
    cl = top["cl"]
    if _clause_of(pos, cl) != "where":
        return True
    wa, _wb = cl["where"]
    before = s[wa:pos]
    return bool(re.search(r"\bnot\b(?!\s+(?:null|like|ilike|between|exists\b|in\b))|\bcase\b|\bif\s*\(|\bcoalesce\s*\(|\bifnull\s*\(|"
                          r"\bis\s+(?:not\s+)?(?:true|false|unknown|null)\b|[=<>]\s*$", before, re.I)) or \
        bool(re.search(r"^\s*\)?\s*(?:=|<>|!=|\bis\b)", s[pos:pos + 1], re.I))


# ------------------------------------------------------------------------------------------------ pre-aggregation
def _corr_shape(sql: str, s: str, blk, top: dict) -> dict | None:
    """A correlated total of one child table tied to the outer row by one equality: its parts, or None."""
    S, C = _S(), _C()
    if blk["nested"] or re.search(r"\(\s*select\b", blk["sub"][6:], re.I):
        return None
    cl = blk["cl"]
    if set(cl) - {"select", "from", "where"} or "from" not in cl:
        return None
    inner = blk["srcs"]
    if len(inner) != 1 or not inner[0].name or inner[0].kind != "from":
        return None
    items = S._items(blk)
    if len(items) != 1 or re.match(r"\s*distinct\b", blk["sub"][cl["select"][0]:cl["select"][1]], re.I):
        return None
    am = AGG1.match(items[0][1])
    if not am or AGG_ANY.search(am.group(2)) or " over" in items[0][1].lower() or NONDET.search(blk["sub"]):
        return None
    func = am.group(1).lower()
    ia = _uq(inner[0].alias or inner[0].name.split(".")[-1])
    itab = inner[0].name
    if "where" not in cl:
        return None
    wa, wb = cl["where"]
    parts = _conjuncts(blk["sub"][wa:wb], blk["o"] + wa)
    if not parts:
        return None
    link, rest = None, []
    for a, b in parts:
        txt = sql[a:b].strip()
        m = re.fullmatch(rf"{QCOL}\s*=\s*{QCOL}", _flat(txt))
        refs = [r for r in S._outer_refs(blk) if a <= r[2] < b]
        if m and refs:
            q1, c1, q2, c2 = (_uq(x) for x in m.groups())
            if q1 == ia and q2 in top["names"] and q2 not in (ia,):
                pair = (m.group(2), m.group(3), m.group(4))
            elif q2 == ia and q1 in top["names"] and q1 not in (ia,):
                pair = (m.group(4), m.group(1), m.group(2))
            else:
                return None
            if link:
                return None                                      # two links: a composite key, left as written
            link = {"inner_col": pair[0].strip('`"'), "outer": f"{pair[1]}.{pair[2]}", "outer_q": _uq(pair[1])}
        elif refs:
            return None                                          # the outer row is used beyond the link: no single grouping
        else:
            rest.append(txt)
    if not link:
        return None
    if _uq(link["inner_col"]) == "*":
        return None
    return {"func": func, "item": _flat(sql[items[0][0]:items[0][0] + len(items[0][1])]), "table": itab,
            "alias": _alias(inner[0]), "ia": ia, "rest": rest, **link}


def _preagg(sql: str, s: str, top: dict, F) -> list:
    S, C = _S(), _C()
    cl = top["cl"]
    if any(k in cl for k in ("group by", "having", "window")) or top["comma"]:
        return []
    if re.search(r"\bover\s*\(|\bfor\s+update\b|\blateral\b|\bnatural\b|\busing\s*\(", C._no_subqueries(s), re.I):
        return []
    if re.match(r"\s*distinct\b", s[cl["select"][0]:cl["select"][1]], re.I):
        return []
    from app.agents import sqlstruct

    if sqlstruct._correlation(sql, s, F):
        return []                                         # the link itself looks wrong (night 13): fix that before speeding it up
    groups: dict[str, dict] = {}
    for blk in S.blocks(s):
        if blk["nested"]:
            continue
        where = _clause_of(blk["a"], cl)
        if where not in ("select", "where", "order by"):
            if blk["ctx"] not in ("cte", "derived", "lateral"):
                return []                                         # a subquery elsewhere (ON, LIMIT ...): left as written
            continue
        shape = _corr_shape(sql, s, blk, top)
        if shape is None:
            if blk["ctx"] in ("scalar_select", "compare", "other") and S._outer_refs(blk):
                return []                                         # another correlated subquery we can't move: keep one form
            continue
        if blk["ctx"] in ("in", "not_in", "exists", "not_exists", "quant"):
            return []                                             # a set, not one value: the total can't stand in for it
        key = _flat(sql[blk["a"]:blk["b"] + 1]).lower()
        g = groups.setdefault(key, {"shape": shape, "spans": [], "where": set()})
        g["spans"].append((blk["a"], blk["b"] + 1))
        g["where"].add(where)
    if not groups:
        return []
    used = {w.lower() for w in re.findall(r"[A-Za-z_]\w*", s)}
    edits, joins, names, ev = [], [], [], []
    n = 0
    for key, g in groups.items():
        sh = g["shape"]
        n += 1
        alias = f"pre{n}"
        while alias in used:
            n += 1
            alias = f"pre{n}"
        used.add(alias)
        val = None
        for a, b in g["spans"]:                                  # the SELECT name of the subquery, when it has one
            if _clause_of(a, cl) == "select":
                after = re.match(rf"\s*(?:as\s+)?({NAME})\s*(?:,|$)", s[b:cl["select"][1]] + ",", re.I)
                if after and _uq(after.group(1)) not in C.KW:
                    val = _uq(after.group(1))
        val = val if val and val != _uq(sh["inner_col"]) else "total"
        q = sh["alias"] or sh["table"]
        item = re.sub(rf"(?<![\w.]){re.escape(q)}\s*\.", f"{q}.", sh["item"])
        where = (" WHERE " + " AND ".join(sh["rest"])) if sh["rest"] else ""
        frm = f"{sh['table']}{' ' + sh['alias'] if sh['alias'] else ''}"
        joins.append(f"LEFT JOIN (SELECT {q}.{sh['inner_col']}, {item} AS {val} FROM {frm}{where} GROUP BY {q}.{sh['inner_col']}) "
                     f"{alias} ON {alias}.{sh['inner_col']} = {sh['outer']}")
        repl = f"COALESCE({alias}.{val}, 0)" if sh["func"] == "count" else f"{alias}.{val}"
        for a, b in g["spans"]:
            edits.append((a, b, repl))
        names.append(sh["func"].upper())
        ev.append(_short(sql[g["spans"][0][0]:g["spans"][0][1]], 90))
    fa, fb = cl["from"]
    end = len(sql[:fb].rstrip())
    edits.append((end, end, "\n" + "\n".join(joins) if "\n" in sql else " " + " ".join(joins)))
    new = sql
    for a, b, t in sorted(edits, key=lambda e: e[0], reverse=True):
        new = new[:a] + t + new[b:]
    counts = any(g["shape"]["func"] == "count" for g in groups.values())
    says = _says("preaggregate")
    f = F("check", "preagg_subquery",
          "A correlated total runs once per outer row; it can be totalled once and joined",
          "Correlated total har bahar ki row ke liye chalta hai; ise ek baar total karke join kar sakte ho",
          "; ".join(ev),
          "Each copy of the subquery is a small query run for every outer row. Totalling the child table once, grouped by the "
          "link column, and joining that with LEFT JOIN gives the same values"
          + (": a COUNT is wrapped in COALESCE(..., 0), because a parent with no child rows gets NULL from the LEFT JOIN but 0 "
             "from the correlated COUNT" if counts else ": a parent with no child rows gets NULL both ways") +
          ". The derived table has one row per link value, so no outer row is repeated or lost. " + says +
          " It is a speed risk, not a promise: it helps when most outer rows are read; when the outer WHERE keeps only a few, the "
          "per-row form can be faster, so compare both with EXPLAIN ANALYZE (ANALYZE on MariaDB).",
          "Subquery ki har copy har bahar ki row ke liye chhoti query hai. Child table ko ek baar link column se group karke total "
          "karo aur LEFT JOIN karo: values wahi aati hain" + (" (COUNT ko COALESCE(..., 0) mein rakha hai, kyunki bina child wale "
          "parent ko LEFT JOIN se NULL milta hai par correlated COUNT se 0)" if counts else "") +
          ". Derived table mein har link value ki ek hi row hai, isliye koi row dohrati ya chhooti nahi. Ye speed ka risk hai, "
          "guarantee nahi: jab zyada rows padhi jaati hain tab fayda; bahar ka WHERE kam rows chhode to per-row form tez ho sakta "
          "hai, dono ko EXPLAIN ANALYZE (MariaDB pe ANALYZE) se compare karo.")
    return [(f, [(0, len(sql), new)])]


# ------------------------------------------------------------------------------------------------ the form the person asked for
def _in_parts(sql: str, s: str, blk, top: dict) -> dict | None:
    """x IN (SELECT y FROM t [WHERE ...]) in the top WHERE, not negated: its parts, or None."""
    S, C = _S(), _C()
    if blk["nested"] or blk["ctx"] != "in" or not blk["left"] or _negated(s, top, blk["left"][0]):
        return None
    cl = blk["cl"]
    if set(cl) - {"select", "from", "where"} or re.search(r"\(\s*select\b", blk["sub"][6:], re.I):
        return None
    inner = blk["srcs"]
    if len(inner) != 1 or not inner[0].name or inner[0].kind != "from":
        return None
    items = S._items(blk)
    if len(items) != 1:
        return None
    col = items[0][1].strip()
    if not re.fullmatch(rf"(?:{NAME}\s*\.\s*)?{NAME}", col) or AGG_ANY.search(col):
        return None
    left = sql[blk["left"][0]:blk["left"][1]]
    if left == ")" or not re.fullmatch(rf"(?:{NAME}\s*\.\s*)?{NAME}", left):
        return None
    if "." not in left:                                          # a bare outer name could mean the inner table inside EXISTS
        if len(top["srcs"]) != 1:
            return None
        x = top["srcs"][0]
        left = f"{x.alias or x.name}.{left}"
    q = inner[0].alias or inner[0].name
    if "." not in col:
        col = f"{q}.{col}"
    where = ""
    if "where" in cl:
        wa, wb = cl["where"]
        where = _flat(sql[blk["o"] + wa:blk["o"] + wb])
    if _uq(q) in top["names"] and not _alias(inner[0]):
        return None                                              # the same table inside and outside with no alias: ambiguous
    return {"left": left, "col": col, "table": inner[0].name, "alias": _alias(inner[0]), "where": where,
            "span": (blk["left"][0], blk["b"] + 1), "q": q}


def _exists_parts(sql: str, s: str, blk, top: dict) -> dict | None:
    """EXISTS (SELECT .. FROM t WHERE t.y = outer.x [AND inner-only ...]) in the top WHERE, not negated: its parts, or None."""
    S = _S()
    if blk["nested"] or blk["ctx"] != "exists":
        return None
    em = re.search(r"\bexists\s*$", s[:blk["a"]], re.I)
    if not em or _negated(s, top, em.start()):
        return None
    sh = _corr_shape_exists(sql, s, blk, top)
    if not sh:
        return None
    return dict(sh, span=(em.start(), blk["b"] + 1))


def _corr_shape_exists(sql: str, s: str, blk, top: dict) -> dict | None:
    S = _S()
    cl = blk["cl"]
    if set(cl) - {"select", "from", "where"} or "where" not in cl or re.search(r"\(\s*select\b", blk["sub"][6:], re.I):
        return None
    inner = blk["srcs"]
    if len(inner) != 1 or not inner[0].name or inner[0].kind != "from":
        return None
    ia = _uq(inner[0].alias or inner[0].name.split(".")[-1])
    wa, wb = cl["where"]
    parts = _conjuncts(blk["sub"][wa:wb], blk["o"] + wa)
    if not parts:
        return None
    link, rest = None, []
    for a, b in parts:
        txt = _flat(sql[a:b])
        refs = [r for r in S._outer_refs(blk) if a <= r[2] < b]
        m = re.fullmatch(rf"{QCOL}\s*=\s*{QCOL}", txt)
        if refs and m and not link:
            q1, q2 = _uq(m.group(1)), _uq(m.group(3))
            if q1 == ia and q2 in top["names"] and q2 != ia:
                link = (f"{m.group(1)}.{m.group(2)}", f"{m.group(3)}.{m.group(4)}")
            elif q2 == ia and q1 in top["names"] and q1 != ia:
                link = (f"{m.group(3)}.{m.group(4)}", f"{m.group(1)}.{m.group(2)}")
            else:
                return None
        elif refs:
            return None
        else:
            rest.append(txt)
    if not link:
        return None
    return {"inner": link[0], "outer": link[1], "table": inner[0].name, "alias": _alias(inner[0]), "rest": rest}


def _asked(sql: str, s: str, top: dict, intent: str, F) -> list:
    S = _S()
    if not intent.strip():
        return []
    if not ASK_VERB.search(intent):
        return []
    blks = S.blocks(s)
    has_in = any(b["ctx"] == "in" and not b["nested"] for b in blks)
    has_ex = any(b["ctx"] == "exists" and not b["nested"] for b in blks)
    want = None
    if has_in and SAYS_EXISTS.search(intent) and not has_ex:
        want = "exists"
    elif has_ex and SAYS_IN.search(re.sub(r"(?i)\bexists\b", "", intent)) and not has_in:
        want = "in"
    elif has_in and SAYS_JOIN.search(intent) and not has_ex:
        want = "join"
    if not want:
        return []
    out = []
    if want == "exists":
        ps = [p for p in (_in_parts(sql, s, b, top) for b in blks) if p]
        if not ps:
            return []
        edits = []
        for p in ps:
            frm = f"{p['table']}{' ' + p['alias'] if p['alias'] else ''}"
            cond = (f"({p['where']}) AND " if p["where"] else "") + f"{p['col']} = {p['left']}"
            edits.append((p["span"][0], p["span"][1], f"EXISTS (SELECT 1 FROM {frm} WHERE {cond})"))
        out.append((F("check", "to_exists", "The same query written with EXISTS", "Wahi query EXISTS ke saath",
                      "; ".join(_short(sql[p["span"][0]:p["span"][1]], 80) for p in ps),
                      "x IN (SELECT y ...) keeps an outer row when some inner row has y = x; EXISTS (... WHERE y = x) asks exactly "
                      "that, so in a WHERE that doesn't negate it the rows are the same (a NULL x or y matches in neither). Neither "
                      "form repeats an outer row. The only difference is under NOT: NOT IN with a NULL in the list keeps nothing, NOT "
                      "EXISTS ignores the NULL, so this rewrite is offered only where nothing negates it.",
                      "x IN (SELECT y ...) bahar ki row tab rakhta hai jab andar koi row y = x ho; EXISTS (... WHERE y = x) bhi wahi "
                      "poochta hai, isliye bina NOT wale WHERE mein rows same hain (NULL dono mein match nahi). Koi bhi form row dohrata "
                      "nahi. Farak sirf NOT ke saath: NOT IN list mein NULL ho to kuch nahi deta, NOT EXISTS NULL ko chhod deta hai."),
                    edits))
    elif want == "in":
        ps = [p for p in (_exists_parts(sql, s, b, top) for b in blks) if p]
        if not ps:
            return []
        edits = []
        for p in ps:
            frm = f"{p['table']}{' ' + p['alias'] if p['alias'] else ''}"
            where = (" WHERE " + " AND ".join(p["rest"])) if p["rest"] else ""
            edits.append((p["span"][0], p["span"][1], f"{p['outer']} IN (SELECT {p['inner']} FROM {frm}{where})"))
        out.append((F("check", "to_in", "The same query written with IN", "Wahi query IN ke saath",
                      "; ".join(_short(sql[p["span"][0]:p["span"][1]], 80) for p in ps),
                      "EXISTS with one link (inner.y = outer.x) and other conditions only on the inner table keeps the outer rows "
                      "whose x is among the inner y values that pass those conditions: that is x IN (SELECT y ... WHERE those "
                      "conditions). Same rows, no repeats either way (a NULL matches in neither). Keep it positive: NOT IN is not "
                      "the same as NOT EXISTS when the column can be NULL.",
                      "Ek link (inner.y = outer.x) aur baaki conditions sirf andar ki table pe hon to EXISTS wahi rows rakhta hai "
                      "jinka x un y values mein hai: yani x IN (SELECT y ... WHERE wahi conditions). Rows same, koi dohrav nahi. "
                      "NOT IN aur NOT EXISTS NULL hone pe same nahi hote."), edits))
    else:
        cl = top["cl"]
        if top["comma"] or "where" not in cl:
            return []
        wa, wb = cl["where"]
        parts = _conjuncts(s[wa:wb], wa)
        if not parts:
            return []
        ps = []
        for b in blks:
            p = _in_parts(sql, s, b, top)
            if p and any(_flat(sql[a:z]) == _flat(sql[p["span"][0]:p["span"][1]]) for a, z in parts):
                ps.append(p)
        if len(ps) != 1:
            return []
        p = ps[0]
        used = {w.lower() for w in re.findall(r"[A-Za-z_]\w*", s)}
        al = "inq" if "inq" not in used else "inq1"
        col = p["col"].split(".")[-1]
        frm = f"{p['table']}{' ' + p['alias'] if p['alias'] else ''}"
        where = f" WHERE {p['where']}" if p["where"] else ""
        join = f"JOIN (SELECT DISTINCT {p['col']} FROM {frm}{where}) {al} ON {al}.{col} = {p['left']}"
        conj = next((a, z) for a, z in parts if _flat(sql[a:z]) == _flat(sql[p["span"][0]:p["span"][1]]))
        keep = [_flat(sql[a:z]) for a, z in parts if (a, z) != conj]
        fa, fb = cl["from"]
        end = len(sql[:fb].rstrip())
        tail = sql[wb:]
        new = sql[:end] + " " + join + (" WHERE " + " AND ".join(keep) if keep else "") + \
            ((" " + tail.lstrip()) if tail.strip() else "")
        out.append((F("check", "to_join", "The same query written with a JOIN", "Wahi query JOIN ke saath",
                      _short(sql[p["span"][0]:p["span"][1]], 80),
                      "A plain JOIN to the inner table returns an outer row once for every matching inner row, so it can repeat rows "
                      "that IN returns once. Joining to SELECT DISTINCT of the inner column instead gives one inner row per value: "
                      "each outer row matches at most once, the same rows as IN (a NULL matches in neither).",
                      "Seedha JOIN har matching andar wali row ke liye bahar ki row dohra deta hai, jabki IN ek hi baar deta hai. Andar "
                      "wale column ka SELECT DISTINCT join karo: har value ki ek row, isliye har bahar ki row zyada se zyada ek baar, "
                      "IN jaisi hi rows."), [(0, len(sql), new)]))
    return out


# ------------------------------------------------------------------------------------------------ the same calculation twice
def _expr_once(sql: str, s: str, top: dict, F) -> list:
    C = _C()
    cl = top["cl"]
    if "order by" not in cl or any(k in cl for k in ("window",)):
        return []
    sa, sb = cl["select"]
    body = s[sa:sb]
    dm = re.match(r"\s*distinct\b", body, re.I)
    skip = dm.end() if dm else 0
    items = {}
    names = set()
    for off, t in C._split_top(body[skip:]):
        raw = sql[sa + skip + off:sa + skip + off + len(t)].strip()
        m = re.match(r"([\s\S]+?)\s+(?:as\s+)?([A-Za-z_]\w*|`[^`]+`|\"[^\"]+\")\s*$", raw, re.I)
        if not m or _uq(m.group(2)) in C.KW:
            names.add(_uq(re.sub(r"^.*\.", "", raw)))
            continue
        expr, name = m.group(1).strip(), m.group(2)
        names.add(_uq(name))
        if re.fullmatch(rf"(?:{NAME}\s*\.\s*)?{NAME}", expr) or re.search(r"\(\s*select\b|\bover\s*\(", expr, re.I) or \
                NONDET.search(expr) or not re.search(r"[-+*/%|]|\w\s*\(", C._mask(expr)):
            continue
        items[_flat(C._mask(expr)).lower()] = name
    if not items:
        return []
    oa, ob = cl["order by"]
    edits, ev = [], []
    for off, t in C._split_top(s[oa:ob]):
        tt = t.strip()
        dm2 = re.match(r"([\s\S]*?)(\s+(?:asc|desc)(?:\s+nulls\s+(?:first|last))?)?\s*$", tt, re.I)
        expr = _flat(dm2.group(1)).lower()
        if expr in items:
            name = items[expr]
            if sum(1 for n in names if n == _uq(name)) > 1:
                continue
            # the name must not also be a column of the tables (MariaDB and PostgreSQL both pick the SELECT name in ORDER BY,
            # but a reader can't tell which one is meant)
            bare_uses = re.findall(rf"(?<![\w.`\"]){re.escape(_uq(name))}(?![\w`\"])", C._mask(sql).replace('"', " ").replace("`", " "), re.I)
            if len(bare_uses) != 1 or re.search(rf"\.\s*[`\"]?{re.escape(_uq(name))}\b", s, re.I):
                continue
            a = oa + off + (len(t) - len(t.lstrip()))
            edits.append((a, a + len(dm2.group(1).rstrip()), name))
            ev.append(_short(dm2.group(1), 60))
    if not edits:
        return []
    return [(F("check", "expr_once", "The same calculation is written in SELECT and again in ORDER BY",
               "Wahi calculation SELECT mein aur phir ORDER BY mein likhi hai", "; ".join(ev),
               "ORDER BY can use the name given in SELECT, and both MariaDB and PostgreSQL then sort by that very value: the "
               "same rows in the same order, and one copy to change later instead of two that can drift apart. (WHERE can't use "
               "a SELECT name: MariaDB 1054, PostgreSQL 42703.)",
               "ORDER BY SELECT wala naam le sakta hai, aur MariaDB aur PostgreSQL dono usi value se sort karte hain: wahi rows, "
               "wahi order, aur badalne ke liye ek hi copy. (WHERE SELECT ka naam nahi le sakta.)"), edits)]


# ------------------------------------------------------------------------------------------------ the contract on the report
ASPECTS = ("matching", "nulls", "duplicates", "ordering", "grain")


def annotate(res: dict) -> dict:
    """Every finding with a same-rows rewrite gets 'same': what it keeps (matching, NULLs, duplicates, ordering, grain) and
    on which conditions; the result gets 'equivalence' for the rewrite it offers."""
    corr = res.get("correct") or {}
    fs = list(corr.get("findings") or []) + [f for f in res.get("findings") or [] if f.get("topic")]
    seen = []
    for f in fs:
        c = contract(f.get("topic") or "")
        if not c:
            continue
        f["same"] = {"keeps": {k: c["keeps"][k] for k in ASPECTS if k in c["keeps"]}, "holds_if": c.get("holds_if", []),
                     "proof": c["proof"], "proven": c.get("proven", [])}
        if f.get("topic") not in [x["topic"] for x in seen]:
            seen.append(c)
    offered = res.get("suggested") if res.get("suggested_kind") == "speed" else None
    if offered and seen:
        res["equivalence"] = {"topics": [c["topic"] for c in seen],
                              "keeps": {k: [c["keeps"][k] for c in seen if k in c["keeps"]] for k in ASPECTS},
                              "holds_if": [h for c in seen for h in c.get("holds_if", [])],
                              "proof": "query" if all(c["proof"] == "query" for c in seen) else "schema"}
    return res


def explain(res: dict, hi: bool = False) -> str:
    """'Why the rewrite is the same' in plain words, aspect by aspect, with the conditions it needs."""
    eq = res.get("equivalence")
    if not eq:
        return ""
    lab = contracts()["aspects"]
    L = ["Ye wahi rows kyun deti hai:" if hi else "Why the rewrite returns the same rows:"]
    for k in ASPECTS:
        for v in eq["keeps"].get(k) or []:
            L.append(f"- {lab[k]['hi' if hi else 'en']}: {v['hi' if hi else 'en']}")
    if eq["holds_if"]:
        L.append("Ye sharten pakki karni hongi:" if hi else "It holds only if:")
        L += [f"- {h['hi' if hi else 'en']}" for h in eq["holds_if"]]
    else:
        L.append("Ye sirf query se sabit hai, table ka koi fact nahi chahiye." if hi else
                 "This follows from the query alone; no table fact is needed.")
    return "\n".join(L)
