"""Safe rewrites with proof of equivalence (night 14 of the curriculum), shared by every database (fundamentals 'grain',
'repeated_work', 'subquery_shape', 'null_logic', 'cardinality', 'set_operations', 'index_use', 'names_scope' and
'rewrite_equivalence' in app/dbknow/core/query_fundamentals.json; the contracts in app/dbknow/core/rewrites.json).

A rewrite is only "the same rows" when it keeps five things, so each one is checked and said, never assumed:
- MATCHING: which rows pass. IN compares the outer value with the inner column; EXISTS asks the same question with the '='
  inside the subquery. A pre-aggregated total counts the child rows whose link equals the outer row's, with the same extra
  conditions (a condition only one total had becomes CASE WHEN ... THEN ... END inside that total).
- NULLS: what an unknown value does. IN may give NULL where EXISTS gives FALSE: WHERE drops both, so they agree in WHERE, but
  not under NOT and not as a SELECT value (lab: category 1 shows NULL with IN, 0 with EXISTS). NOT IN is never TRUE once the
  inner column holds a NULL or the outer value is NULL (unless the list is empty), so NOT IN <-> NOT EXISTS is written out
  with those cases (lab: 0 vs 1 row, 4 vs 5 rows) -- the exact form keeps NOT IN's rows, the plain form is offered as what is
  usually meant. COUNT of nothing is 0 in a subquery but NULL after a LEFT JOIN, hence COALESCE(.., 0).
- DUPLICATES: how often a row comes. IN / EXISTS are semi-joins (once per outer row); a JOIN repeats the outer row per match
  unless the joined side is DISTINCT (lab: 8 rows vs 5); a derived table grouped by the link has one row per link value.
- ORDERING: the ORDER BY contract. Keyset paging needs the sort to end in a unique column and its columns free of NULLs (a
  comparison with NULL is never TRUE; MariaDB sorts NULLs first, PostgreSQL last); mixed ASC / DESC is spelled column by
  column, since (a, b) > (x, y) only fits one direction.
- GRAIN: what one output row stands for; LEFT JOIN to a grouped derived table keeps one output row per outer row.
And computing an expression once is only the same when the expression is deterministic: RAND() / random() written twice
draws twice (lab: 10 and 23 rows passed 'r < 0.5 AND r >= 0.5'; computed once in a derived table: 0 on both engines).

Two kinds of findings:
- found from the query alone (a 'check', the same rows offered as a faster or clearer form): several correlated totals over
  the same child table ('preagg'), an expression repeated in SELECT and WHERE ('compute_once'), a volatile expression
  repeated ('volatile_repeat': the copies differ, computing once changes the rows: offered as 'if that's what you meant').
- asked for in words around the query ("rewrite with EXISTS", "IN ki jagah JOIN", "use keyset", "CTE mein likho"): the
  rewrite asked for, with the proof, or the honest reason it would not be the same rows and the form that is.
Every rule and rewrite was reproduced on MariaDB 10.11 and PostgreSQL 16 (tests/practice/test_night14.py has the lab table;
the pf_* families of tests/fuzz/stmtbugs.py run both forms on random tables).
Returns [(Finding, [edits])], edit = (0, len(sql), new text): the whole statement rewritten.
"""

import json
import os
import re

# the engines each rewrite was reproduced on (tests/practice/test_night14.py, LAB) -- standing rule B
PROVEN = {k: ["mariadb", "postgresql"] for k in (
    "preagg", "compute_once", "volatile_repeat", "rw_exists", "rw_not_exists", "rw_in", "rw_not_in", "rw_join", "rw_cte",
    "rw_cte_derived", "rw_union_all", "rw_keyset", "rw_once", "rw_nothing")}
# rewrites that return exactly the rows of the query as written (offered as 'the same rows, another form')
SAME = {"preagg", "compute_once", "rw_exists", "rw_not_exists", "rw_in", "rw_not_in", "rw_join", "rw_cte", "rw_cte_derived",
        "rw_keyset", "rw_once"}
NAME = r"[`\"]?[A-Za-z_][\w$]*[`\"]?"
QNAME = rf"{NAME}(?:\.{NAME})?"
AGG = re.compile(r"(count|sum|avg|min|max)\s*\(\s*(distinct\s+)?([\s\S]*)\)", re.I)
ANY_AGG = re.compile(r"\b(count|sum|avg|min|max|group_concat|string_agg|array_agg|stddev|variance|bit_and|bit_or)\s*\(", re.I)
VOLATILE = re.compile(r"\b(rand|random|uuid|uuid_short|newid|gen_random_uuid|sysdate|clock_timestamp|timeofday|nextval|"
                      r"random_bytes)\s*\(", re.I)
PLAIN_WORDS = {"and", "or", "not", "null", "is", "case", "when", "then", "else", "end", "true", "false", "interval", "as", "in", "like",
               "between", "distinct", "day", "days", "month", "months", "year", "years", "hour", "minute", "second", "week",
               "current_date", "current_timestamp", "localtimestamp", "signed", "unsigned", "char", "date", "integer", "int",
               "decimal", "numeric", "text", "varchar"}
VERB = (r"(?:rewrite|re-?write|make|put|give|show|compute|calculate|evaluate|work\s+out|convert|change|turn|replace|use|using|switch|transform|instead|into|express|write|likho|likh|"
        r"badlo|badal|banao|bana|karo|kar\s+do|kardo|chahiye|chaiye|lagao|लिखो|लिख|बदलो|बनाओ|करो|चाहिए)")


def _C():
    from app.agents import sqlcorrect as C

    return C


def _S():
    from app.agents import sqlsubq as S

    return S


def _uq(n: str) -> str:
    return (n or "").strip('`"').lower()


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def _short(t: str, n: int = 100) -> str:
    t = _flat(t)
    return t if len(t) <= n else t[:n - 3] + "..."


_RW = None


def contracts() -> dict:
    """The shared contracts (app/dbknow/core/rewrites.json)."""
    global _RW
    if _RW is None:
        here = os.path.join(os.path.dirname(__file__), "..", "dbknow", "core", "rewrites.json")
        with open(here, encoding="utf-8") as fh:
            _RW = json.load(fh)
    return _RW


def family_of(topic: str) -> str | None:
    for fid, f in contracts()["families"].items():
        if topic in f["topics"]:
            return fid
    return None


def why(fid: str, hi: bool = False) -> str:
    """'Why it is the same rows': each of the five things the rewrite keeps, in one line each."""
    f = contracts()["families"][fid]
    dims = contracts()["dimensions"]
    k = "hi" if hi else "en"
    return "; ".join(f"{dims[d][k]}: {f['keeps'][d][k]}" for d in ("matching", "nulls", "duplicates", "ordering", "grain"))


def _holds(fid: str, hi: bool = False) -> str:
    f = contracts()["families"][fid]
    k = "hi" if hi else "en"
    return "; ".join(x[k] for x in f["holds_when"])


# ------------------------------------------------------------------------------------------------ what was asked for
CONSTRUCTS = [
    ("not_exists", r"\bnot\s+exists\b|नॉट\s*एग्जिस्ट"),
    ("exists", r"(?<!not )\bexists\b|एग्जिस्ट"),
    ("not_in", r"\bNOT\s+IN\b|\bnot\s+in\s+(?:clause|subquery|list|operator)\b"),
    ("in", r"\b(?:use|using|with|to|into|as|an|the|via|plain)\s+IN\b|(?<!NOT )\bIN(?=\s*(?:\(|clause|subquery|list|operator|se\b|mein\b|me\b|"
           r"से|में|ki\s+jagah|instead|use\b|lagao|likho))|(?<!not )\bin\s+(?:clause|subquery|list|operator)\b|\bwhere\s+in\b"),
    ("join", r"\b(?:use|using|with|into|to|as|via|a|an|se|mein|me)\s+(?:an?\s+)?(?:inner\s+|left\s+)?joins?\b|\bjoins?(?=\s+(?:se|mein|me|instead|ki\s+jagah|lagao|use|likho|banao)\b)|जॉइन|joins?(?=\s+(?:से|में))|\bwithout\s+(?:a\s+|the\s+|any\s+)?(?:sub-?quer(?:y|ies))\b|\bbina\s+subquery\b|"
             r"\bsubquery\s+(?:hatao|hata\s+do|ke\s+bina)\b"),
    ("union_all", r"\bunion\s+all\b"),
    ("keyset", r"\bkeyset\b|\bseek\s+method\b|\bcursor[-\s]+(?:based\s+)?pagination\b|\bwithout\s+(?:the\s+)?offset\b|\bno\s+offset\b|"
               r"\boffset\s+(?:ke\s+)?bina\b|\bbina\s+offset\b|\boffset\s+(?:hatao|hata\s+do)\b"),
    ("cte", r"\bctes?\b|\bwith\s+clause\b|\bcommon\s+table\b|\bwith\s+query\b|\bwith\s+se\b"),
    ("preagg", r"\bpre-?aggregat\w*|\baggregate\s+(?:it\s+)?(?:first|before)\b|\bgroup\s+(?:it\s+)?(?:first|before\s+(?:the\s+)?join)\b|"
               r"\bpehle\s+(?:total|group)\b"),
    ("once", r"\b(?:compute|calculate|work\s+out|write|evaluate)\s+(?:it\s+|this\s+|that\s+|the\s+\w+\s+)?once\b|\bek\s+(?:hi\s+)?baar\b|"
             r"\brepeat(?:ed|ing)?\s+(?:expression|calculation|formula)\b|\bएक\s+बार\b"),
]


def wanted(intent: str) -> list[str]:
    """The forms the person asked the query to be written in, in the order they were named (target first when clear)."""
    if not intent or not re.search(VERB, intent, re.I) and not re.search(r"\b(?:can|could|should)\s+i\b|\bkya\b|\bkar\s+sakte\b|"
                                                                         r"\bsame\b|\bequivalent\b", intent, re.I):
        return []
    found = []
    for cid, pat in CONSTRUCTS:
        flags = 0 if cid in ("in", "not_in") else re.I
        for m in re.finditer(pat, intent, flags):
            if cid == "exists" and any(c == "not_exists" and a <= m.start() < b for c, a, b in found):
                continue
            found.append((cid, m.start(), m.end()))
    if not found:
        return []
    found.sort(key=lambda x: x[1])
    names = []
    for cid, _a, _b in found:
        if cid not in names:
            names.append(cid)
    if len(names) < 2:
        return names
    # two forms named: "A to / into / se B" -> B; "A instead of B" / "B ki jagah A" -> A
    (c1, _a1, b1), (c2, a2, _b2) = found[0], next(x for x in found if x[0] != found[0][0])
    between = intent[b1:a2]
    if re.search(r"\binstead\s+of\b|\bin\s+place\s+of\b|\brather\s+than\b|\bover\b", between, re.I):
        return [c1, c2]
    if re.search(r"\bto\b|\binto\b|->|=>|\bse\b|\bki\s+jagah\b|\bke\s+badle\b|की\s+जगह|से", between, re.I):
        return [c2, c1]
    return names


# ------------------------------------------------------------------------------------------------ reading the statement
def _and_terms(text: str) -> list[tuple[int, str]]:
    """Top-level AND terms of a condition: [(offset, text)]; BETWEEN x AND y stays one term."""
    C = _C()
    dep = C._depths(text)
    cuts, last = [], 0
    for m in re.finditer(r"\band\b", text, re.I):
        if dep[m.start()] != 0:
            continue
        if re.search(r"\bbetween\s+(?:(?!\band\b)[\s\S])*$", text[last:m.start()], re.I):
            continue
        cuts.append((last, text[last:m.start()]))
        last = m.end()
    cuts.append((last, text[last:]))
    return cuts


def _parse(sql: str, s: str) -> dict | None:
    """The top-level SELECT: clauses, items (start, masked text, original text), sources; None for set operations."""
    C = _C()
    end = len(s.rstrip().rstrip(";").rstrip())
    s2 = s[:end]
    if re.search(r"\b(union|intersect|except)\b", C._no_subqueries(s2), re.I):
        return None
    dep = C._depths(s2)
    cl = C._clauses(s2, dep)
    if "select" not in cl or "from" not in cl:
        return None
    sa, sb = cl["select"]
    body = s2[sa:sb]
    dm = re.match(r"\s*distinct\b", body, re.I)
    skip = dm.end() if dm else 0
    items = []
    for off, t in C._split_top(body[skip:]):
        if not t.strip():
            continue
        a = sa + skip + off + (len(t) - len(t.lstrip()))
        b = a + len(t.strip())
        items.append({"a": a, "b": b, "m": s2[a:b], "o": sql[a:b]})
    srcs = C._sources(s2, dep, cl["from"][0], cl["from"][1])
    return {"end": end, "cl": cl, "dep": dep, "items": items, "srcs": srcs, "distinct": bool(dm),
            "with": bool(re.match(r"\s*with\b", s2, re.I)), "s": s2}


def _out_name(item: dict) -> tuple[str | None, str | None, str]:
    """(output name, explicit alias, the item without its alias)."""
    m = re.search(r"\s+as\s+(" + NAME + r")\s*$", item["m"], re.I)
    if m:
        alias = item["o"][m.start(1):m.end(1)]
        return alias, alias, item["o"][:m.start()].strip()
    cm = re.fullmatch(r"(?:" + NAME + r"\.)?(" + NAME + r")", item["m"].strip())
    if cm and _uq(cm.group(1)) not in _C().KW:
        return item["o"][cm.start(1):cm.end(1)], None, item["o"].strip()
    return None, None, item["o"].strip()


def _kw_start(s: str, body_start: int, kw: str) -> int:
    m = None
    for m in re.finditer(r"\b" + kw.replace(" ", r"\s+") + r"\b", s[:body_start], re.I):
        pass
    return m.start() if m else body_start


def _names(s: str) -> set:
    """Every table name and alias the statement reads."""
    out = {w.lower() for w in re.findall(r"\b(?:from|join)\s+[`\"]?([\w$]+)", s, re.I)}
    out |= {w.lower() for w in re.findall(r"\b(?:from|join)\s+[`\"]?[\w$.]+[`\"]?\s+(?:as\s+)?([A-Za-z_]\w*)", s, re.I)}
    out |= {w.lower() for w in re.findall(r"\)\s*(?:as\s+)?([A-Za-z_]\w*)", s, re.I)}
    return out


def _fresh(base: str, taken: set) -> str:
    base = re.sub(r"\W", "", base) or "t"
    name, i = base, 2
    while name.lower() in taken or name.lower() in _C().KW:
        name, i = f"{base}{i}", i + 1
    taken.add(name.lower())
    return name


def _where_term_blocks(sql: str, q: dict, ctxs: tuple) -> list:
    """Subqueries that are a whole top-level AND term of the main WHERE, in one of the given contexts:
    [(block, term start, term end)] (positions in the query)."""
    S = _S()
    cl = q["cl"]
    if "where" not in cl:
        return []
    wa, wb = cl["where"]
    out = []
    blks = [b for b in S.blocks(q["s"]) if not b["nested"] and b["ctx"] in ctxs and wa <= b["a"] < wb]
    for off, t in _and_terms(q["s"][wa:wb]):
        ta, tb = wa + off + (len(t) - len(t.lstrip())), wa + off + len(t.rstrip())
        for b in blks:
            if ta <= b["a"] and b["b"] + 1 == tb:
                head = q["s"][ta:b["a"]]
                if b["ctx"] in ("exists", "not_exists") and re.fullmatch(r"\s*(?:not\s+)?exists\s*", head, re.I):
                    out.append((b, ta, tb))
                elif b["ctx"] in ("in", "not_in") and re.fullmatch(r"\s*" + QNAME + r"\s+(?:not\s+)?in\s*", head, re.I):
                    out.append((b, ta, tb))
    return out


def _plain_sub(b: dict, allow_order: bool = False) -> bool:
    bad = ("group by", "having", "limit", "union", "window") + (() if allow_order else ("order by",))
    return "select" in b["cl"] and "from" in b["cl"] and not any(k in b["cl"] for k in bad) and \
        not re.search(r"\b(union|intersect|except|limit|fetch|offset)\b|\bover\s*\(", _C()._no_subqueries(b["sub"]), re.I)


def _sub_parts(sql: str, b: dict) -> tuple[str, str | None, list]:
    """(the FROM text, the WHERE text or None, the SELECT items' original text) of a subquery block."""
    o, cl = b["o"], b["cl"]
    fa, fb = cl["from"]
    frm = sql[o + fa:o + fb].strip()
    wt = None
    if "where" in cl:
        wa, wb = cl["where"]
        wt = sql[o + wa:o + wb].strip()
    items = [sql[a:a + len(t)].strip() for a, t in _S()._items(b)]
    return frm, wt, items


def _with_where(frm: str, conds: list) -> str:
    conds = [c for c in conds if c]
    return f"FROM {frm}" + (f" WHERE {' AND '.join(conds)}" if conds else "")


def _paren(c: str | None) -> str | None:
    if not c:
        return None
    return c if re.fullmatch(r"\s*" + QNAME + r"\s*(?:=|<>|!=|<=|>=|<|>)\s*[\w.'`\"]+\s*", c) else f"({c})"


def _qualify_left(sql: str, q: dict, left: str) -> str | None:
    """The outer value of an IN, qualified with its table so the subquery can't read it as its own column."""
    if "." in left:
        return left
    named = [x for x in q["srcs"] if x.name or x.alias]
    if len(q["srcs"]) != 1 or not named:
        return None
    x = named[0]
    return f"{x.alias or x.name}.{left}"


# ------------------------------------------------------------------------------------------------ entry
def rewrites(sql: str, s: str, intent: str, F, is_select: bool, have: set = frozenset()) -> list:
    """Night 14: [(Finding, [edits])] -- the same-rows rewrites found or asked for, each with its proof."""
    if not is_select or not re.search(r"\bselect\b", s, re.I):
        return []
    want = wanted(intent)
    out = []
    try:
        q = _parse(sql, s)
        if q is not None:
            out += _preagg(sql, q, F, want)
            out += _compute_once(sql, q, F, want)
        if want:
            out += _asked(sql, s, q, F, want, have, intent)
    except Exception:  # noqa: BLE001 - a rewrite is an extra; it must never stop the main check
        return out
    return out


# ------------------------------------------------------------------------------------------------ pre-aggregate
def _corr_totals(sql: str, q: dict) -> list:
    """Correlated totals that are a whole SELECT item: (SELECT AGG(..) FROM child c WHERE c.link = outer.col [AND ...]) AS name."""
    S, C = _S(), _C()
    outer_alias = {_uq(x.alias or x.name.split(".")[-1]) for x in q["srcs"] if x.name or x.alias}
    found = []
    for b in S.blocks(q["s"]):
        if b["nested"] or b["ctx"] != "scalar_select" or not _plain_sub(b) or "where" not in b["cl"]:
            continue
        item = next((it for it in q["items"] if it["a"] <= b["a"] and b["b"] < it["b"] + 1), None)
        if item is None:
            continue
        name, alias, core = _out_name(item)
        if not alias or _flat(core) != _flat(sql[b["a"]:b["b"] + 1]):
            continue
        inner = [x for x in b["srcs"]]
        if len(inner) != 1 or not inner[0].name or inner[0].kind != "from":
            continue
        T = inner[0]
        ia = _uq(T.alias or T.name.split(".")[-1])
        its = S._items(b)
        if len(its) != 1:
            continue
        im = AGG.fullmatch(its[0][1].strip())
        if not im or ANY_AGG.search(im.group(3)) or re.search(r"\bselect\b", im.group(3), re.I):
            continue
        arg_o = sql[its[0][0]:its[0][0] + len(its[0][1])].strip()
        arg_o = arg_o[arg_o.index("(") + 1:arg_o.rindex(")")].strip()
        if im.group(2):
            arg_o = re.sub(r"^\s*distinct\s+", "", arg_o, flags=re.I)
        wa, wb = b["cl"]["where"]
        link, rest = None, []
        for off, t in _and_terms(b["sub"][wa:wb]):
            refs = [r for r in C._qrefs(t) if _uq(r[0]) in b["outer"]]
            ot = sql[b["o"] + wa + off:b["o"] + wa + off + len(t)].strip()
            if not refs:
                if re.search(r"\bselect\b", t, re.I):
                    link = "bad"
                rest.append(ot)
                continue
            m = re.fullmatch(rf"\s*({QNAME})\s*=\s*({QNAME})\s*", t)
            if link is not None or not m:
                link = "bad"
                continue
            sides = [m.group(1), m.group(2)]
            outs = [x for x in sides if "." in x and _uq(x.split(".")[0]) in b["outer"]]
            ins = [x for x in sides if x not in outs]
            if len(outs) != 1 or len(ins) != 1 or ("." in ins[0] and _uq(ins[0].split(".")[0]) != ia):
                link = "bad"
                continue
            if _uq(outs[0].split(".")[0]) not in outer_alias:
                link = "bad"
                continue
            link = (_uq(ins[0].split(".")[-1]), ins[0].split(".")[-1].strip('`"'), _flat(outs[0]))
        if not isinstance(link, tuple):
            continue
        found.append({"blk": b, "item": item, "alias": alias, "fn": im.group(1).upper(), "distinct": bool(im.group(2)),
                      "arg": arg_o, "rest": rest, "table": T, "ia": ia, "link": link,
                      "key": (_uq(T.name.split(".")[-1]), link[0], link[2].lower(), ia)})
    return found


def _preagg(sql: str, q: dict, F, want: list) -> list:
    C = _C()
    cl = q["cl"]
    asked = any(w in want for w in ("join", "cte", "preagg"))
    if q["with"] or q["distinct"] or any(k in cl for k in ("group by", "having", "window", "for update")) or \
            re.search(r"\bover\s*\(", C._no_subqueries(q["s"]), re.I) or any(x.kind == "comma" for x in q["srcs"]):
        return []
    groups: dict = {}
    for t in _corr_totals(sql, q):
        groups.setdefault(t["key"], []).append(t)
    out = []
    for key, ts in groups.items():
        texts = {_flat(sql[t["blk"]["a"]:t["blk"]["b"] + 1]).lower() for t in ts}
        if not (asked and ts or len(ts) >= 2 and len(texts) >= 2):
            continue
        out.append(_preagg_one(sql, q, F, ts, cte="cte" in want and "join" not in want[:1], asked=asked))
        break                                          # one pre-aggregated table per answer; the rest after it is applied
    return [x for x in out if x]


def _preagg_one(sql: str, q: dict, F, ts: list, cte: bool, asked: bool):
    T, ia = ts[0]["table"], ts[0]["ia"]
    lc_raw, outer = ts[0]["link"][1], ts[0]["link"][2]
    common = [r for r in ts[0]["rest"] if all(_flat(r).lower() in {_flat(x).lower() for x in t["rest"]} for t in ts)]
    ckeys = {_flat(r).lower() for r in common}
    taken = _names(q["s"]) | {_uq(t["alias"]) for t in ts}
    d = _fresh(f"{ia}_totals", taken)
    qual = T.alias or T.name
    cols, outs = [], []
    used = {lc_raw.lower()}
    for t in ts:
        own = [r for r in t["rest"] if _flat(r).lower() not in ckeys]
        cond = " AND ".join(_paren(r) if len(own) > 1 else r for r in own)
        arg = t["arg"]
        if cond:
            arg = f"CASE WHEN {cond} THEN {'1' if arg == '*' else arg} END"
        agg = f"{t['fn']}({'DISTINCT ' if t['distinct'] else ''}{arg})"
        col = _uq(t["alias"])
        if col in used:
            col = _fresh(col + "_v", used)
        used.add(col)
        cols.append(f"{agg} AS {col}")
        val = f"COALESCE({d}.{col}, 0)" if t["fn"] == "COUNT" else f"{d}.{col}"
        outs.append((t["item"], f"{val} AS {t['alias']}"))
    frm = sql[ts[0]["blk"]["o"] + ts[0]["blk"]["cl"]["from"][0]:ts[0]["blk"]["o"] + ts[0]["blk"]["cl"]["from"][1]].strip()
    inner = (f"SELECT {qual}.{lc_raw}, " + ", ".join(cols) + f" FROM {frm}" +
             (f" WHERE {' AND '.join(common)}" if common else "") + f" GROUP BY {qual}.{lc_raw}")
    fa, fb = q["cl"]["from"]
    from_end = fa + len(sql[fa:fb].rstrip())
    edits = sorted([(it["a"], it["b"], new) for it, new in outs], key=lambda e: e[0])
    body = sql[:q["end"]]
    if cte:
        join = f"\nLEFT JOIN {d} ON {d}.{lc_raw} = {outer}"
    else:
        join = f"\nLEFT JOIN (\n  {inner}\n) {d} ON {d}.{lc_raw} = {outer}"
    pieces, last = [], 0
    for a, b, new in edits + [(from_end, from_end, join)]:
        pieces.append(body[last:a])
        pieces.append(new)
        last = b
    pieces.append(body[last:])
    new = "".join(pieces)
    if cte:
        new = f"WITH {d} AS (\n  {inner}\n)\n{new.lstrip()}"
    names = ", ".join(t["alias"] for t in ts)
    tname = T.name.split(".")[-1]
    n = len(ts)
    topic = "rw_cte" if asked and cte else "preagg"
    head_en = (f"{n} correlated totals over {tname} ({names}) can be worked out in one pass" if n > 1 else
               f"The correlated total over {tname} ({names}) as one grouped {'CTE' if cte else 'derived table'}")
    head_hi = (f"{tname} pe {n} correlated totals ({names}) ek hi baar mein nikal sakte hain" if n > 1 else
               f"{tname} ka correlated total ({names}) ek grouped {'CTE' if cte else 'derived table'} mein")
    fix_en = (f"Each subquery in SELECT is a small query run for every outer row ({'a speed risk: the plan decides' if not asked else 'as asked'}). "
              f"Grouping {tname} once by {lc_raw} gives every total in one pass, joined back with LEFT JOIN on "
              f"{d}.{lc_raw} = {outer}. Why it is the same rows: {why('preaggregate')}. "
              + (f"COUNT is wrapped in COALESCE(..., 0) because a LEFT JOIN that finds nothing gives NULL where the subquery gave 0. "
                 if any(t['fn'] == 'COUNT' for t in ts) else "")
              + "Proven on MariaDB and PostgreSQL: both forms return the same rows.")
    fix_hi = (f"SELECT ki har subquery har bahar ki row ke liye chalti hai. {tname} ko {lc_raw} se ek baar group karke saare total "
              f"ek saath milte hain, LEFT JOIN se jode ({d}.{lc_raw} = {outer}). Wahi rows kyun: {why('preaggregate', True)}. "
              + ("COUNT ko COALESCE(..., 0) mein rakha, kyunki LEFT JOIN kuch na mile to NULL deta hai jahan subquery 0 deti. "
                 if any(t['fn'] == 'COUNT' for t in ts) else "") + "MariaDB aur PostgreSQL pe dono roop wahi rows dete hain.")
    return (F("check", topic, head_en, head_hi, _short(sql[ts[0]['blk']['a']:ts[0]['blk']['b'] + 1], 90), fix_en, fix_hi),
            [(0, len(sql), new)])


# ------------------------------------------------------------------------------------------------ compute once
def _occurs(q: dict, sql: str, core_m: str, core_o: str, a: int, b: int) -> list[tuple[int, int]]:
    """Where the same expression appears again between a and b (masked text matched token by token, strings compared)."""
    toks = re.findall(r"\w+|[^\w\s]", core_m)
    if not toks:
        return []
    pat = r"\s*".join(re.escape(t) for t in toks)
    strs = re.findall(r"'[^']*'", core_o)
    out = []
    for m in re.finditer(rf"(?<![\w.$`\"]){pat}(?![\w$`\"(])", q["s"][a:b], re.I):
        x, y = a + m.start(), a + m.end()
        if re.findall(r"'[^']*'", sql[x:y]) == strs:
            out.append((x, y))
    return out


def _compute_once(sql: str, q: dict, F, want: list) -> list:
    C = _C()
    cl = q["cl"]
    asked = "once" in want
    if q["with"] or q["distinct"] or any(k in cl for k in ("group by", "having", "window", "for update")) or \
            re.search(r"\bover\s*\(", C._no_subqueries(q["s"]), re.I):
        return []
    for it in q["items"]:
        name, alias, core = _out_name(it)
        if not alias:
            continue
        core_m = re.sub(r"\s+as\s+" + NAME + r"\s*$", "", it["m"], flags=re.I).strip()
        if re.fullmatch(QNAME, core_m) or re.fullmatch(r"'x*'|\d+(?:\.\d+)?", core_m) or re.search(r"\bselect\b", core_m, re.I) or \
                ANY_AGG.search(core_m) or not re.search(r"[-+*/%|(]", core_m) or len(_flat(core_m)) < 6:
            continue
        words = [w for w in re.findall(r"(?<![\w.$])([A-Za-z_][\w$]*)(?![\w$]|\s*\()", re.sub(r"'x*'", "''", core_m))
                 if w.lower() not in PLAIN_WORDS]
        if not C._qrefs(core_m) and not words and not VOLATILE.search(core_m):
            continue                                   # no column at all: a constant
        where_hits = _occurs(q, sql, core_m, core, *cl["where"]) if "where" in cl else []
        order_hits = _occurs(q, sql, core_m, core, *cl["order by"]) if "order by" in cl else []
        if not where_hits and not (asked and order_hits):
            continue
        volatile = bool(VOLATILE.search(core_m))
        new = _once_rewrite(sql, q, it, alias, core, core_m, where_hits, order_hits)
        n = 1 + len(where_hits) + len(order_hits)
        ev = _short(core, 80)
        if volatile:
            fn = VOLATILE.search(core_m).group(1).upper()
            return [(F("check", "volatile_repeat", f"{fn}() is written {n} times: each copy draws a different value",
                       f"{fn}() {n} baar likha hai: har copy alag value nikalti hai", ev,
                       f"{fn}() gives a new value every time it is worked out, so the {alias} shown and the one WHERE tests are "
                       f"two different draws (lab: 'r < 0.5 AND r >= 0.5' with two draws kept 10 and 23 rows; with one value per "
                       f"row, 0, on MariaDB and PostgreSQL). If the same value was meant, compute it once in a derived table and "
                       f"filter on its name" + (": the form is below. It changes the rows (that is the point)." if new else "."),
                       f"{fn}() har baar nayi value deta hai, isliye dikhaya gaya {alias} aur WHERE wala do alag draw hain. Wahi "
                       f"value chahiye to derived table mein ek baar nikalo aur naam pe filter karo (rows badlengi, yahi matlab hai)."),
                     [(0, len(sql), new)] if new else [])]
        if not new:
            continue
        topic = "rw_once" if asked else "compute_once"
        return [(F("check", topic, f"{_short(core, 50)} is written {n} times; it can be worked out once",
                   f"{_short(core, 50)} {n} baar likha hai; ek baar nikal sakta hai", ev,
                   f"The same calculation is written in SELECT and again in " + ("WHERE" if where_hits else "ORDER BY") +
                   f" (WHERE can't see SELECT's names, so it was copied). Give it its name once: "
                   + ("ORDER BY can use the name directly; " if order_hits else "")
                   + ("the query is wrapped in a derived table and WHERE filters on the name. " if where_hits else "")
                   + f"One place to change it, and nothing to keep in step. Why it is the same rows: {why('compute_once')}. "
                   "It holds because the expression gives the same value each time (no RAND() / random() / UUID()). Proven on "
                   "MariaDB and PostgreSQL.",
                   f"Wahi hisaab SELECT mein aur phir " + ("WHERE" if where_hits else "ORDER BY") + " mein likha hai (WHERE SELECT "
                   f"ke naam nahi dekhta). Ek baar naam do: ORDER BY naam le sakta hai; WHERE ke liye query derived table mein "
                   f"rakh ke naam pe filter. Wahi rows kyun: {why('compute_once', True)}."),
                 [(0, len(sql), new)])]
    return []


def _once_rewrite(sql: str, q: dict, it: dict, alias: str, core: str, core_m: str, where_hits, order_hits) -> str | None:
    C = _C()
    cl = q["cl"]
    end = q["end"]
    a_name = alias
    if not where_hits:                                 # ORDER BY only: the name there, nothing else changes
        oa, ob = cl["order by"]
        pieces, last = [], 0
        for x, y in order_hits:
            term = [t for off, t in C._split_top(q["s"][oa:ob]) if oa + off <= x and y <= oa + off + len(t)]
            if not term or not re.fullmatch(r"\s*" + re.escape(q["s"][x:y]) + r"(?:\s+(?:asc|desc))?\s*", term[0], re.I):
                return None                            # inside a bigger ORDER BY expression: PostgreSQL can't see the name there
            pieces += [sql[last:x], a_name]
            last = y
        return "".join(pieces) + sql[last:end]
    # WHERE too: SELECT ... FROM (SELECT ..., expr AS name FROM ... WHERE <the rest>) x WHERE <on the name> ORDER BY ...
    outs = []
    for x in q["items"]:
        nm, _al, _core = _out_name(x)
        if not nm or x["m"].strip() == "*" or x["m"].strip().endswith(".*"):
            return None
        outs.append(nm)
    if len({_uq(n) for n in outs}) != len(outs):
        return None
    wa, wb = cl["where"]
    inner_terms, outer_terms = [], []
    for off, t in _and_terms(q["s"][wa:wb]):
        ta, tb = wa + off, wa + off + len(t)
        hits = [(x, y) for x, y in where_hits if ta <= x and y <= tb]
        orig = sql[ta:tb].strip()
        if not hits:
            inner_terms.append(orig)
            continue
        rest = q["s"][ta:tb]
        piece, last = [], ta
        for x, y in hits:
            piece += [sql[last:x], a_name]
            rest = rest[:x - ta] + " " * (y - x) + rest[y - ta:]
            last = y
        piece.append(sql[last:tb])
        if C._qrefs(rest) or [w for w in C._bare(re.sub(r"'x*'", "''", rest), set()) if not re.fullmatch(r"\d+", w)] or \
                re.search(r"\bselect\b", rest, re.I):
            return None                                # the condition also reads other columns: it can't move out alone
        outer_terms.append("".join(piece).strip())
    order = ""
    if "order by" in cl:
        oa, ob = cl["order by"]
        terms = []
        lower_outs = {_uq(n) for n in outs}
        for off, t in C._split_top(q["s"][oa:ob]):
            tm = re.fullmatch(r"\s*([\s\S]*?)(\s+(?:asc|desc))?(\s+nulls\s+(?:first|last))?\s*", t, re.I)
            expr_m = tm.group(1)
            orig = sql[oa + off:oa + off + len(t)]
            o_expr = orig[tm.start(1):tm.end(1)]
            suffix = (tm.group(2) or "") + (tm.group(3) or "")
            if any(oa + off <= x < oa + off + len(t) for x, _y in order_hits):
                if _flat(expr_m).lower() != _flat(core_m).lower():
                    return None
                terms.append(a_name + suffix)
            elif re.fullmatch(r"\d+", expr_m.strip()):
                terms.append(expr_m.strip() + suffix)
            elif re.fullmatch(QNAME, expr_m.strip()) and _uq(expr_m.strip().split(".")[-1]) in lower_outs and \
                    (("." not in expr_m) or any(_flat(x["m"]).lower() == _flat(expr_m).lower() for x in q["items"])):
                terms.append(o_expr.strip().split(".")[-1] + suffix)
            else:
                return None
        order = "\nORDER BY " + ", ".join(terms)
    tail = ""
    if "limit" in cl:
        tail = "\n" + sql[_kw_start(q["s"], cl["limit"][0], "limit"):end].strip()
    elif re.search(r"\b(offset|fetch)\b", C._no_subqueries(q["s"]), re.I):
        return None
    fa, fb = cl["from"]
    inner = f"SELECT {sql[cl['select'][0]:cl['select'][1]].strip()}\n  FROM {sql[fa:fb].strip()}"
    if inner_terms:
        inner += "\n  WHERE " + " AND ".join(inner_terms)
    x = _fresh("x", _names(q["s"]))
    return (f"SELECT {', '.join(outs)}\nFROM (\n  {inner}\n) {x}\nWHERE {' AND '.join(outer_terms)}{order}{tail}").rstrip()


# ------------------------------------------------------------------------------------------------ asked for in words
def _asked(sql: str, s: str, q: dict | None, F, want: list, have: set, intent: str) -> list:
    out = []
    target = want[0]
    if q is not None and target in ("exists", "not_exists"):
        out += _to_exists(sql, q, F)
    elif q is not None and target in ("in", "not_in"):
        out += _to_in(sql, q, F)
    elif q is not None and target == "join":
        out += _to_join(sql, q, F)
    elif target == "union_all":
        out += _to_union_all(sql, s, F, have)
    elif target == "keyset":
        out += _to_keyset(sql, F, have)
    elif q is not None and target in ("cte", "preagg"):
        if not any(f.topic in ("preagg", "rw_cte") for f, _e in out):
            out += _derived_to_cte(sql, q, F) if target == "cte" else []
    if not out and target in ("exists", "not_exists", "in", "not_in", "join", "union_all", "keyset") and \
            not have & {"semi_join", "union_all", "keyset", "preagg", "rw_cte"}:
        what = {"exists": "IN (SELECT ...)", "not_exists": "NOT IN (SELECT ...)", "in": "EXISTS (SELECT ...)",
                "not_in": "NOT EXISTS (SELECT ...)", "join": "IN (SELECT ...) or a total in SELECT",
                "union_all": "UNION", "keyset": "ORDER BY ... LIMIT ... OFFSET"}[target]
        out.append((F("check", "rw_nothing", f"Asked for {target.replace('_', ' ').upper()}: there is no {what} here that can be "
                      f"rewritten that way with the same rows", f"{target.replace('_', ' ').upper()} maanga: yahan aisa {what} nahi jo "
                      f"wahi rows dete hue badla ja sake", _short(intent, 80),
                      f"I only rewrite a query into another form when I can show it returns the same rows. Here the {what} that "
                      f"form replaces is missing, or it stands where the rewrite would change the result (under NOT or OR, in "
                      f"SELECT, or with GROUP BY / LIMIT inside the subquery). Your query stays as it is.",
                      f"Main query tabhi badalta hoon jab dikha sakun ki rows wahi rahengi. Yahan wo {what} nahi hai, ya aisi jagah "
                      f"hai jahan badalne se result badal jaata (NOT / OR ke andar, SELECT mein, ya subquery mein GROUP BY / LIMIT). "
                      f"Query waisi hi rahegi."), []))
    return out


def _replace_terms(sql: str, q: dict, repl: list) -> str:
    """The statement with whole WHERE terms replaced: repl = [(start, end, new text)]."""
    body, pieces, last = sql[:q["end"]], [], 0
    for a, b, new in sorted(repl):
        pieces += [body[last:a], new]
        last = b
    return "".join(pieces) + body[last:]


def _to_exists(sql: str, q: dict, F) -> list:
    S = _S()
    repl, plain, kinds = [], [], set()
    for b, ta, tb in _where_term_blocks(sql, q, ("in", "not_in")):
        if not _plain_sub(b, allow_order=False) or len(S._items(b)) != 1:
            continue
        left = q["s"][b["left"][0]:b["left"][1]]
        left_o = sql[b["left"][0]:b["left"][1]]
        lq = _qualify_left(sql, q, left_o.strip())
        frm, wt, items = _sub_parts(sql, b)
        item = re.sub(r"^\s*distinct\s+", "", items[0], flags=re.I)
        if not lq or item.strip() == "*" or re.search(r"\bselect\b", item, re.I) or \
                _uq(lq.split(".")[0]) in b["own"] or ANY_AGG.search(item):
            continue
        w = _paren(wt)
        if b["ctx"] == "in":
            new = f"EXISTS (SELECT 1 {_with_where(frm, [w, f'{item} = {lq}'])})"
            kinds.add("rw_exists")
        else:
            new = (f"(NOT EXISTS (SELECT 1 {_with_where(frm, [w, f'{item} = {lq}'])})\n"
                   f"     AND NOT EXISTS (SELECT 1 {_with_where(frm, [w, f'{item} IS NULL'])})\n"
                   f"     AND ({lq} IS NOT NULL OR NOT EXISTS (SELECT 1 {_with_where(frm, [w])})))")
            plain.append(f"NOT EXISTS (SELECT 1 {_with_where(frm, [w, f'{item} = {lq}'])})")
            kinds.add("rw_not_exists")
        repl.append((ta, tb, new))
    if not repl:
        return []
    new = _replace_terms(sql, q, repl)
    if "rw_not_exists" in kinds:
        return [(F("check", "rw_not_exists", "NOT IN -> NOT EXISTS: the same rows only with NOT IN's NULL cases written out",
                   "NOT IN -> NOT EXISTS: wahi rows tabhi jab NOT IN ke NULL wale case likhe jaayen", _short(sql[repl[0][0]:repl[0][1]], 90),
                   "NOT IN and NOT EXISTS differ on NULLs: once the inner column holds a NULL, NOT IN is never true (lab: 0 rows "
                   "where NOT EXISTS keeps 1), and a NULL outer value is dropped by NOT IN but kept by NOT EXISTS (lab: 4 vs 5 "
                   "rows), on MariaDB and PostgreSQL alike. The rewrite below keeps exactly NOT IN's rows: NOT EXISTS for the "
                   "match, plus 'no NULL inside' and 'the value is not NULL (or the list is empty)'. Why it is the same rows: "
                   f"{why('not_in_to_not_exists')}. If the NULL cases are not what you meant (usually they aren't), the plain "
                   f"form is {plain[0]}: {_holds('not_in_to_not_exists')}.",
                   "NOT IN aur NOT EXISTS NULL pe alag hain: andar ke column mein NULL ho to NOT IN kabhi true nahi (lab: 0 rows, "
                   "NOT EXISTS 1), aur NULL bahar ki value NOT IN hataata hai, NOT EXISTS rakhta hai (lab: 4 vs 5). Neeche wala roop "
                   "bilkul NOT IN ki rows rakhta hai: match ke liye NOT EXISTS, aur 'andar NULL nahi' aur 'value NULL nahi (ya list "
                   f"khaali)'. Agar NULL wala asar aapka matlab nahi (aksar nahi hota), seedha roop: {plain[0]}: "
                   f"{_holds('not_in_to_not_exists', True)}."),
                 [(0, len(sql), new)])]
    return [(F("check", "rw_exists", "IN (SELECT ...) as EXISTS: the same rows", "IN (SELECT ...) EXISTS mein: wahi rows",
               _short(sql[repl[0][0]:repl[0][1]], 90),
               "The IN's comparison moves inside the subquery as its link to the outer row. Why it is the same rows: "
               f"{why('in_to_exists')}. Proven on MariaDB and PostgreSQL. Both engines already run either form as a semi-join, so "
               "expect the same plan; the rewrite is for reading, or for adding more conditions on the inner row.",
               "IN ka '=' subquery ke andar bahar ki row se link ban jaata hai. Wahi rows kyun: "
               f"{why('in_to_exists', True)}. Dono engines dono roop semi-join se chalate hain, plan aksar wahi rahega."),
             [(0, len(sql), new)])]


def _exists_link(sql: str, b: dict, q: dict):
    """EXISTS (... WHERE inner = outer.col AND ...): (inner expression, outer column, other conditions) or None."""
    C = _C()
    if "where" not in b["cl"] or not _plain_sub(b, allow_order=False):
        return None
    outer_alias = {_uq(x.alias or x.name.split(".")[-1]) for x in q["srcs"] if x.name or x.alias}
    wa, wb = b["cl"]["where"]
    link, rest = None, []
    for off, t in _and_terms(b["sub"][wa:wb]):
        refs = [r for r in C._qrefs(t) if _uq(r[0]) in b["outer"]]
        ot = sql[b["o"] + wa + off:b["o"] + wa + off + len(t)].strip()
        if not refs:
            rest.append(ot)
            continue
        m = re.fullmatch(r"\s*([\s\S]+?)\s*=\s*([\s\S]+?)\s*", t)
        if link is not None or not m or re.search(r"\b(and|or|not|select)\b", t, re.I):
            return None
        g1 = (m.start(1), m.end(1))
        g2 = (m.start(2), m.end(2))
        sides = []
        for x, y in (g1, g2):
            mt, ot2 = t[x:y], sql[b["o"] + wa + off + x:b["o"] + wa + off + y]
            is_out = bool(re.fullmatch(QNAME, mt.strip())) and "." in mt and _uq(mt.split(".")[0]) in b["outer"]
            sides.append((mt, ot2.strip(), is_out))
        outs = [x for x in sides if x[2]]
        ins = [x for x in sides if not x[2]]
        if len(outs) != 1 or len(ins) != 1 or [r for r in C._qrefs(ins[0][0]) if _uq(r[0]) in b["outer"]] or \
                _uq(outs[0][0].split(".")[0]) not in outer_alias:
            return None
        link = (ins[0][1], outs[0][1])
    return (link[0], link[1], rest) if link else None


def _to_in(sql: str, q: dict, F) -> list:
    repl, kinds = [], set()
    for b, ta, tb in _where_term_blocks(sql, q, ("exists", "not_exists")):
        got = _exists_link(sql, b, q)
        if not got:
            continue
        inner, outer, rest = got
        frm = sql[b["o"] + b["cl"]["from"][0]:b["o"] + b["cl"]["from"][1]].strip()
        rest = [_paren(r) if len(rest) > 1 else r for r in rest]
        if b["ctx"] == "exists":
            new = f"{outer} IN (SELECT {inner} {_with_where(frm, rest)})"
            kinds.add("rw_in")
        else:
            new = f"({outer} NOT IN (SELECT {inner} {_with_where(frm, rest + [f'{inner} IS NOT NULL'])}) OR {outer} IS NULL)"
            kinds.add("rw_not_in")
        repl.append((ta, tb, new))
    if not repl:
        return []
    new = _replace_terms(sql, q, repl)
    if "rw_not_in" in kinds:
        return [(F("check", "rw_not_in", "NOT EXISTS -> NOT IN: the same rows only with the NULL cases written out",
                   "NOT EXISTS -> NOT IN: wahi rows tabhi jab NULL wale case likhe jaayen", _short(sql[repl[0][0]:repl[0][1]], 90),
                   "A plain NOT IN would keep nothing as soon as the inner column holds a NULL, and would drop rows whose own "
                   "value is NULL, which NOT EXISTS keeps (lab: 4 vs 5 rows on MariaDB and PostgreSQL). So the inner list leaves "
                   "NULLs out (IS NOT NULL) and a NULL outer value is kept on its own (OR ... IS NULL): exactly NOT EXISTS's rows. "
                   f"Why it is the same rows: {why('not_in_to_not_exists')}. NOT EXISTS is usually the clearer of the two.",
                   "Seedha NOT IN andar ke column mein ek NULL hote hi kuch nahi rakhta, aur NULL value wali rows hataata hai jo NOT "
                   "EXISTS rakhta hai (lab: 4 vs 5). Isliye andar ki list se NULL bahar (IS NOT NULL) aur NULL bahar ki value alag "
                   f"se rakhi (OR ... IS NULL): bilkul NOT EXISTS ki rows. Wahi rows kyun: {why('not_in_to_not_exists', True)}."),
                 [(0, len(sql), new)])]
    return [(F("check", "rw_in", "EXISTS as IN (SELECT ...): the same rows", "EXISTS IN (SELECT ...) mein: wahi rows",
               _short(sql[repl[0][0]:repl[0][1]], 90),
               "The link to the outer row (inner = outer) becomes the IN's comparison and the other conditions stay inside. Why "
               f"it is the same rows: {why('in_to_exists')}. Proven on MariaDB and PostgreSQL (MariaDB even makes this rewrite "
               "itself, the EXISTS-to-IN optimization).",
               "Bahar ki row ka link (andar = bahar) IN ka '=' ban jaata hai, baaki conditions andar rehti hain. Wahi rows kyun: "
               f"{why('in_to_exists', True)}."),
             [(0, len(sql), new)])]


def _to_join(sql: str, q: dict, F) -> list:
    S = _S()
    cl = q["cl"]
    pre = _preagg(sql, q, F, ["join"])
    if any(x.kind == "comma" for x in q["srcs"]) or q["with"]:
        return pre
    taken = _names(q["s"])
    joins, drop = [], []
    for b, ta, tb in _where_term_blocks(sql, q, ("in",)):
        if not _plain_sub(b, allow_order=False) or len(S._items(b)) != 1:
            continue
        left_o = sql[b["left"][0]:b["left"][1]].strip()
        lq = _qualify_left(sql, q, left_o)
        frm, wt, items = _sub_parts(sql, b)
        item = re.sub(r"^\s*distinct\s+", "", items[0], flags=re.I)
        if not lq or item.strip() == "*" or ANY_AGG.search(item) or re.search(r"\bselect\b", item, re.I) or S._outer_refs(b):
            continue
        base = (b["srcs"][0].alias or b["srcs"][0].name.split(".")[-1]) if b["srcs"] and (b["srcs"][0].alias or b["srcs"][0].name) else "m"
        d = _fresh(f"{_uq(base)}_keys", taken)
        joins.append(f"\nJOIN (SELECT DISTINCT {item} AS in_key {_with_where(frm, [wt] if wt else [])}) {d} ON {d}.in_key = {lq}")
        drop.append((ta, tb))
    if not joins:
        return pre
    wa, wb = cl["where"]
    keep = []
    for off, t in _and_terms(q["s"][wa:wb]):
        a, b2 = wa + off, wa + off + len(t)
        if not any(x <= a + (len(t) - len(t.lstrip())) and b2 - (len(t) - len(t.rstrip())) <= y for x, y in drop):
            keep.append(sql[a:b2].strip())
    fa, fb = cl["from"]
    from_end = fa + len(sql[fa:fb].rstrip())
    w_kw = _kw_start(q["s"], wa, "where")
    body = sql[:q["end"]]
    new = body[:from_end] + "".join(joins) + (f"\nWHERE {' AND '.join(keep)}" if keep else "") + "\n" + body[wb:].lstrip()
    new = new.rstrip()
    if w_kw < from_end:
        return pre
    return pre + [(F("check", "rw_join", "IN (SELECT ...) as a JOIN: joined to the distinct values, so no row repeats",
                     "IN (SELECT ...) JOIN mein: distinct values se joda, isliye koi row nahi dohrati", _short(sql[drop[0][0]:drop[0][1]], 90),
                     "A plain JOIN to the inner table would repeat each outer row once per matching inner row (lab: 8 rows instead "
                     "of 5 on MariaDB and PostgreSQL), so the join goes to SELECT DISTINCT of the inner values. Why it is the same "
                     f"rows: {why('in_to_join')}. Both engines already run IN as a semi-join, so this is about the shape you "
                     "prefer, not speed.",
                     "Andar ki table se seedha JOIN har bahar ki row ko har match pe dohrata (lab: 5 ki jagah 8), isliye JOIN andar "
                     f"ki values ke SELECT DISTINCT se hai. Wahi rows kyun: {why('in_to_join', True)}."),
                   [(0, len(sql), new)])]


def _to_union_all(sql: str, s: str, F, have: set) -> list:
    C = _C()
    if "union_all" in have:
        return []                                      # night 13 already offers UNION ALL with its condition
    flat = C._no_subqueries(s)
    if not re.search(r"\bunion\b(?!\s+all\b)", flat, re.I):
        return []
    parts = re.split(r"\bunion\b(?:\s+all\b)?", flat, flags=re.I)
    body = s.rstrip().rstrip(";").rstrip()
    as_all = re.sub(r"\bunion\b(?!\s+all\b)", "UNION ALL", sql[:len(body)], flags=re.I) if len(parts) == 2 else None
    checks = ""
    if as_all and not re.search(r"\border\s+by\b|\blimit\b", flat[flat.lower().rfind("union"):], re.I):
        checks = (f" To see whether today's data has any repeats, compare SELECT COUNT(*) FROM ({_flat(sql[:len(body)])}) u with "
                  f"SELECT COUNT(*) FROM ({_flat(as_all)}) u: equal counts mean no repeats today (not a promise for tomorrow).")
    return [(F("check", "rw_union_all", "UNION ALL here could return more rows than UNION",
               "Yahan UNION ALL UNION se zyada rows de sakta hai", _short(sql, 90),
               "UNION removes a repeated row between the parts and inside each part; UNION ALL keeps every row. They give the same "
               f"rows only when {_holds('union_to_union_all')}, and nothing in this query shows that (lab: parts that can share "
               "a value gave 8 rows with UNION and 16 with UNION ALL). So I'm not swapping it: UNION ALL is safe once the table "
               "structure shows each part lists a key and a constant column (or the conditions) keeps the parts apart." + checks,
               "UNION parts ke beech aur har part ke andar ke repeat hataata hai; UNION ALL sab rakhta hai. Dono tabhi barabar jab "
               f"{_holds('union_to_union_all', True)}, aur is query mein ye nahi dikhta (lab: 8 vs 16 rows). Isliye main nahi "
               "badal raha: har part mein key aur alag constant column (ya ulti conditions) ho tab UNION ALL safe hai."), [])]


def _to_keyset(sql: str, F, have: set) -> list:
    from app.agents import sqlbasics, sqlspeed

    if "keyset" in have:
        return []
    eng = sqlbasics.engine_seen(sql)
    got = sqlspeed.keyset_sql(sql, eng, min_offset=1, mixed=True)
    if not got:
        return [(F("check", "rw_keyset", "Keyset paging needs a sort that ends with a unique column, and an OFFSET to replace",
                   "Keyset paging ke liye sort unique column pe khatam ho, aur badalne ko OFFSET ho", _short(sql, 90),
                   "Keyset paging asks for 'the rows after the last one shown', which is only exactly the next page when "
                   f"{_holds('offset_to_keyset')}. Here the query has no LIMIT ... OFFSET over one table, or its ORDER BY does "
                   "not end with the table's key (add it at the end as a tie-breaker first: then OFFSET's pages are repeatable "
                   "too, and the keyset form gives the same pages).",
                   "Keyset paging 'aakhri dikhayi row ke baad' maangta hai, jo tabhi theek agla page hai jab "
                   f"{_holds('offset_to_keyset', True)}. Yahan ek table pe LIMIT ... OFFSET nahi, ya ORDER BY key pe khatam nahi "
                   "(pehle aakhir mein key jodo)."), [])]
    new, cols, n, m = got
    names = ", ".join(f":last_{c.split('.')[-1].strip(chr(96) + chr(34)).lower()}" for c in cols)
    return [(F("check", "rw_keyset", f"OFFSET {m} as keyset paging: the same page, read from where the last one ended",
               f"OFFSET {m} keyset paging mein: wahi page, pichle page ke aakhir se", _short(sql, 90),
               f"The page after OFFSET {m} is the {n} rows that sort after the last row of the page before: {names} are that "
               f"row's values, which the app keeps. Why it is the same page: {why('offset_to_keyset')}. It holds when "
               f"{_holds('offset_to_keyset')}. Proven on MariaDB and PostgreSQL: the same page as the OFFSET query.",
               f"OFFSET {m} ke baad wala page wo {n} rows hain jo pichle page ki aakhri row ke baad sort hoti hain: {names} us "
               f"row ki values hain (app yaad rakhti hai). Wahi page kyun: {why('offset_to_keyset', True)}."),
             [(0, len(sql), new)])]


def _derived_to_cte(sql: str, q: dict, F) -> list:
    S = _S()
    cl = q["cl"]
    taken = {n for n in re.findall(r"\b(?:from|join)\s+[`\"]?([\w$]+)", q["s"], re.I)}
    taken = {t.lower() for t in taken}
    ctes, repl = [], []
    for b in S.blocks(q["s"]):
        if b["nested"] or b["ctx"] != "derived":
            continue
        m = re.match(r"\s*(?:as\s+)?(" + NAME + r")(\s*\([^)]*\))?", q["s"][b["b"] + 1:], re.I)
        if not m or _uq(m.group(1)) in _C().KW or _uq(m.group(1)) in taken:
            continue
        name = sql[b["b"] + 1 + m.start(1):b["b"] + 1 + m.end(1)]
        colist = (m.group(2) or "").strip()
        ctes.append(f"{name}{(' ' + colist) if colist else ''} AS (\n  {sql[b['a'] + 1:b['b']].strip()}\n)")
        repl.append((b["a"], b["b"] + 1 + m.end(), name))
    if not ctes:
        return []
    body = _replace_terms(sql, q, repl)
    if q["with"]:
        wm = re.match(r"\s*with\s+(?:recursive\s+)?", q["s"], re.I)
        sa = _kw_start(q["s"], cl["select"][0], "select")
        shift = sum(len(n) - (b - a) for a, b, n in repl if a < sa)
        new = body[:sa + shift].rstrip() + ",\n" + ",\n".join(ctes) + "\n" + body[sa + shift:]
        if not wm:
            return []
    else:
        new = "WITH " + ",\n".join(ctes) + "\n" + body.lstrip()
    return [(F("check", "rw_cte_derived", "The derived table written as a CTE (WITH): the same rows",
               "Derived table CTE (WITH) mein: wahi rows", _short(sql[repl[0][0]:repl[0][1]], 90),
               f"The subquery in FROM gets its name up front in WITH and the FROM reads that name. Why it is the same rows: "
               f"{why('derived_to_cte')}. It holds because the name is not a table the query reads. Needs MySQL 8.0+ or "
               "MariaDB 10.2+ (PostgreSQL: any supported version).",
               f"FROM wali subquery ko WITH mein pehle naam milta hai aur FROM wo naam padhta hai. Wahi rows kyun: "
               f"{why('derived_to_cte', True)}. MySQL 8.0+ ya MariaDB 10.2+ chahiye."),
             [(0, len(sql), new)])]


# ------------------------------------------------------------------------------------------------ the proof on the result
VERDICT = {"same": SAME | {"repeat_subquery", "same_subquery", "having_key", "cte_filter_late", "sargable", "or_union"},
           "same_if": {"union_all", "keyset"},
           "differs": {"not_in_outer", "qo_not_in", "anti_join", "union_all_dups", "rw_union_all", "volatile_repeat", "rw_nothing"}}


def verdict(topic: str) -> str | None:
    for v, ts in VERDICT.items():
        if topic in ts:
            return v
    return None


def prove(out: dict) -> dict:
    """Every finding whose rewrite belongs to a family gets its proof: the family, the verdict (same / same if / differs),
    what it keeps, when it holds and when it breaks, and where it was proven. out['proof'] lists the ones behind the
    rewrite offered (or applied)."""
    corr = out.get("correct") or {}
    proofs = []
    for f in list(corr.get("findings") or []):
        fid = family_of(f.get("topic") or "")
        v = verdict(f.get("topic") or "")
        if not fid or not v:
            continue
        fam = contracts()["families"][fid]
        f["proof"] = {"family": fid, "verdict": v, "title": fam["title"], "keeps": fam["keeps"], "holds_when": fam["holds_when"],
                      "breaks_when": fam["breaks_when"], "proven": fam["proven"], "fundamental": fam["fundamental"]}
        if (out.get("suggested") or out.get("rewritten")) and v in ("same", "same_if") and \
                not any(p["family"] == fid for p in proofs):
            proofs.append({"topic": f["topic"], **f["proof"]})
    if proofs:
        out["proof"] = proofs
    return out
