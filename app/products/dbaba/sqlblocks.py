"""A long query read as blocks and their dependency tree, with the grain at every stage (night 12; fundamental 'grain' in
app/dbknow/core/query_fundamentals.json).

A query is a tree of SELECT blocks: the CTEs of its WITH, derived tables in a FROM (SELECT ...) alias, subqueries in WHERE /
SELECT (IN, EXISTS, a scalar value) and the branches of UNION / INTERSECT / EXCEPT. A block depends on the CTEs and derived
tables it reads. For every block DBaba states:
- its input grain: what one row stands for after its FROM and joins (one row per order item, ...),
- what each join does to that grain: keeps it (the joined side matches at most one row), moves it down to the joined table
  (a parent joined to its children), or multiplies both ways (neither side is a key),
- its output grain: GROUP BY columns, one row for a total without GROUP BY, the distinct output rows for DISTINCT, otherwise
  the input grain,
- its filters, totals, windows, final cut, and the assumptions the grain rests on.
Which side of a join is 'one' is PROVEN when it is a block whose own GROUP BY (or DISTINCT, or a total without GROUP BY) makes
the ON columns unique, and only GUESSED when it comes from a column name (id, <table>_id). A guessed key is listed as an
assumption to verify; nothing is invented. keys(sql) gives the proven keys of every block, so the checks of the main query and
of every other block know that a CTE grouped by customer_id has one row per customer_id.
Every grain fact was reproduced on MariaDB and PostgreSQL (tests/practice/test_night12.py, LAB).
"""

import re

from app.agents import sqlcorrect as C

PROVEN_ON = ["mariadb", "postgresql"]
NAME = r"(?:`[^`]+`|\"[^\"]+\"|[A-Za-z_][\w$]*)"
SETOP = re.compile(r"\b(union(?:\s+all|\s+distinct)?|intersect(?:\s+all)?|except(?:\s+all)?|minus)\b", re.I)


def _uq(n: str) -> str:
    return (n or "").strip('`"').lower()


# ------------------------------------------------------------------------------------------------ the blocks
def _ctes(s: str) -> tuple[list[dict], int]:
    """[(name, cols, body_start, body_end)] of a top-level WITH, and where the main statement starts."""
    m = re.match(r"\s*with\s+(recursive\s+)?", s, re.I)
    if not m:
        return [], 0
    out, i = [], m.end()
    while True:
        cm = re.match(rf"\s*({NAME})\s*(\(([^()]*)\))?\s*as\s*(?:not\s+)?(?:materialized\s+)?\(", s[i:], re.I)
        if not cm:
            break
        op = i + cm.end() - 1
        cl = C._close(s, op)
        cols = [_uq(x) for x in (cm.group(3) or "").split(",") if x.strip()]
        head = i + cm.start(1)
        out.append({"name": _uq(cm.group(1)), "cols": cols, "start": op + 1, "end": cl, "recursive": bool(m.group(1)), "head": head})
        i = cl + 1
        nm = re.match(r"\s*,", s[i:])
        if not nm:
            break
        i += nm.end()
    return out, i


def _branches(s: str, a: int, b: int) -> list[tuple[int, int]]:
    """Split a block's text at its own top-level UNION / INTERSECT / EXCEPT."""
    dep = C._depths(s[a:b])
    cuts = [(m.start(), m.end()) for m in SETOP.finditer(s[a:b]) if dep[m.start()] == 0]
    if not cuts:
        return [(a, b)]
    out, last = [], 0
    for x, y in cuts:
        out.append((a + last, a + x))
        last = y
    out.append((a + last, b))
    return out


def blocks(sql: str) -> list[dict]:
    """Every SELECT block of the query, parents before children:
    {id, kind (main / cte / derived / subquery / branch), name, start, end, parent, depends, recursive}."""
    s = C._mask(sql)
    out: list[dict] = []
    ctes, main_at = _ctes(s)
    names = {c["name"] for c in ctes}

    def add(kind, name, a, b, parent, recursive=False):
        a2 = a + len(s[a:b]) - len(s[a:b].lstrip())
        b2 = a + len(s[a:b].rstrip().rstrip(";").rstrip())
        while s[a2:a2 + 1] == "(" and C._close(s, a2) == b2 - 1:   # (SELECT ...) UNION (SELECT ...): the brackets of a branch
            a2, b2 = a2 + 1, b2 - 1
        bid = name if kind == "cte" else "main" if kind == "main" else \
            (f"{kind} {name}" if name else f"{kind} {len([x for x in out if x['kind'] == kind]) + 1}")
        blk = {"id": bid, "kind": kind, "name": name, "start": a2, "end": b2, "parent": parent, "depends": [], "recursive": recursive}
        parts = _branches(s, a2, b2)
        if len(parts) > 1:
            blk["branches"] = []
            out.append(blk)
            for k, (x, y) in enumerate(parts, 1):
                sub = add("branch", f"{bid} #{k}", x, y, bid, recursive)
                blk["branches"].append(sub["id"])
                blk["depends"] += [d for d in sub["depends"] if d not in blk["depends"]]
            return blk
        out.append(blk)
        _inner(blk)
        return blk

    def _inner(blk):
        a, b = blk["start"], blk["end"]
        text = s[a:b]
        d = C._depths(text)
        own0 = _mask_children(s, a, b)
        fr = C._clauses(own0, C._depths(own0)).get("from")
        for m in re.finditer(r"\(\s*(select|with)\b", text, re.I):
            if d[m.start()] != 1:
                continue                                         # only the brackets directly inside this block
            op = a + m.start()
            cl = C._close(s, op)
            before = s[max(a, op - 40):op]
            after = s[cl + 1:cl + 60]
            am = re.match(rf"\s*(?:as\s+)?({NAME})", after, re.I)
            in_from = bool(fr) and fr[0] <= m.start() < fr[1]
            if in_from and re.search(r"(\bfrom|\bjoin|,|\blateral)\s*$", before, re.I) and am and _uq(am.group(1)) not in C.KW:
                child = add("derived", _uq(am.group(1)), op + 1, cl, blk["id"])
                blk["depends"].append(child["id"])
            else:
                child = add("subquery", "", op + 1, cl, blk["id"])
                blk["depends"].append(child["id"])
        own = _mask_children(s, a, b)
        for m in re.finditer(rf"\b(from|join)\s+({NAME})", own, re.I):
            n = _uq(m.group(2))
            if n in names and n != blk["name"] and n not in blk["depends"]:
                blk["depends"].append(n)
            elif n in names and n == blk["name"] and blk["kind"] in ("cte", "branch"):
                blk["recursive"] = True

    for c in ctes:
        add("cte", c["name"], c["start"], c["end"], "main", c["recursive"])
    main = add("main", "", main_at, len(s), None)
    main["depends"] = [c["name"] for c in ctes if _reads(s, main, c["name"], out)] + [x for x in main["depends"] if x not in names]
    ordered = [b for b in out if b["kind"] == "main"] + [b for b in out if b["kind"] != "main"]
    return ordered


def _mask_children(s: str, a: int, b: int) -> str:
    """The block's own text: nested brackets blanked (their FROM / JOIN belong to the inner blocks)."""
    text = list(s[a:b])
    d = C._depths(s[a:b])
    for i, ch in enumerate(text):
        if d[i] >= 1 and not (ch in "()" and d[i] == 1):
            text[i] = " " if ch != "\n" else "\n"
    return "".join(text)


def _reads(s: str, blk: dict, name: str, out: list) -> bool:
    own = _mask_children(s, blk["start"], blk["end"])
    if re.search(rf"\b(from|join)\s+[`\"]?{re.escape(name)}[`\"]?\b", own, re.I):
        return True
    kids = [x for x in out if x["parent"] == blk["id"]]
    return any(name in x["depends"] or _reads(s, x, name, out) for x in kids if x["kind"] != "cte")


# ------------------------------------------------------------------------------------------------ the grain of a block
def _select_items(sql: str, s: str, a: int, b: int) -> tuple[list[dict], dict]:
    own = _mask_children(s, a, b)
    dep = C._depths(own)
    cl = C._clauses(own, dep)
    items = []
    if "select" in cl:
        x, y = cl["select"]
        for p, t in C._split_top(own[x:y]):
            raw = sql[a + x + p:a + x + p + len(t)].strip()
            if not raw:
                continue
            am = re.search(rf"\s+(?:as\s+)?({NAME})\s*$", raw, re.I)
            expr = raw[:am.start()].strip() if am and not re.fullmatch(rf"(?:{NAME}\.)?{NAME}", raw) else raw
            name = _uq(am.group(1)) if am and expr != raw else _uq(raw.split(".")[-1]) if re.fullmatch(rf"(?:{NAME}\.)?{NAME}", raw) else None
            own_t = own[x + p:x + p + len(t)]               # the item's own text: a total inside its (SELECT ...) is the subquery's
            items.append({"text": re.sub(r"\s+", " ", raw), "expr": re.sub(r"\s+", " ", expr), "name": name,
                          "agg": bool(C.AGG.search(C._mask(own_t))) and not re.search(r"\bover\s*\(", own_t, re.I)})
    return items, cl


def keys(sql: str) -> dict:
    """{block name: [set of output column names that are unique in it]} for CTEs and derived tables, PROVEN by the block's
    own text: a GROUP BY of plain columns that are all in the SELECT, DISTINCT over its whole output, or a total with no
    GROUP BY (one row: every set of columns is unique, including the empty one)."""
    out = {}
    try:
        bl = blocks(sql)
    except Exception:  # noqa: BLE001 - an odd query: no block keys
        return {}
    s = C._mask(sql)
    todo = [b for b in bl if b["kind"] in ("cte", "derived") and not b.get("branches") and not b.get("recursive")]
    for _round in range(len(todo) + 1):                  # a block that only passes another block's rows on keeps its key
        changed = False
        for blk in todo:
            if blk["name"] in out:
                continue
            k = _block_key(sql, s, blk, out)
            if k is not None:
                out[blk["name"]] = k if isinstance(k, list) else [k]
                changed = True
        if not changed:
            break
    return out


def columns(sql: str) -> dict:
    """{block name: set of its output column names} for CTEs and derived tables whose SELECT names every column (night 15):
    what the query itself proves a block has, so 'x.units and x.returned_units: two columns, or one?' is never asked of a
    block that plainly makes both. A block with * or an unnamed expression is left out (its columns aren't all known)."""
    out = {}
    try:
        bl = blocks(sql)
    except Exception:  # noqa: BLE001 - an odd query: nothing known
        return {}
    s = C._mask(sql)
    ctes = {c["name"]: c["cols"] for c in _ctes(s)[0]}
    for blk in bl:
        if blk["kind"] not in ("cte", "derived"):
            continue
        if ctes.get(blk["name"]):
            out[blk["name"]] = set(ctes[blk["name"]])
            continue
        first = blk["id"]
        if blk.get("branches"):
            first = blk["branches"][0]
            blk = next(b for b in bl if b["id"] == first)
        items, _cl = _select_items(sql, s, blk["start"], blk["end"])
        names = [i["name"] for i in items]
        if items and all(names) and not any(i["expr"].endswith("*") for i in items):
            out[_uq(blk["name"] or "")] = set(names)
    return out


def _block_key(sql: str, s: str, blk: dict, known: dict | None = None):
    items, cl = _select_items(sql, s, blk["start"], blk["end"])
    if not items or "from" not in cl:
        return None
    own = _mask_children(s, blk["start"], blk["end"])
    if known and "group by" not in cl and not re.match(r"\s*distinct\b", own[cl["select"][0]:cl["select"][1]], re.I):
        srcs = C._sources(own, C._depths(own), *cl["from"])        # SELECT k, ... FROM <block with key k> (filters, windows only)
        if len(srcs) == 1 and srcs[0].name and _uq(srcs[0].name) in known:
            shown = {i["name"] for i in items if i["name"] and re.fullmatch(rf"(?:{NAME}\.)?{NAME}", i["expr"]) and
                     _uq(i["expr"].split(".")[-1]) == i["name"]}
            kept = [k for k in known[_uq(srcs[0].name)] if k <= shown]
            if kept:
                return kept
    sel = own[cl["select"][0]:cl["select"][1]]
    if "group by" in cl:
        g = own[cl["group by"][0]:cl["group by"][1]]
        if re.search(r"\b(rollup|cube|grouping\s+sets)\b", g, re.I):
            return None
        gcols = []
        g0 = blk["start"] + cl["group by"][0]
        for _p, t in C._split_top(g):
            t = sql[g0 + _p:g0 + _p + len(t)].strip()        # the real text: a grouped expression's brackets aren't blanked
            if t.isdigit() and 0 < int(t) <= len(items):
                nm = items[int(t) - 1]["name"]
            else:
                nm = next((i["name"] for i in items if i["name"] and (re.sub(r"\s+", "", i["expr"].lower()) == re.sub(r"\s+", "", t.lower())
                                                                     or i["name"] == _uq(t.split(".")[-1]) and "." not in i["expr"].replace(t, ""))), None)
                if nm is None:
                    nm = next((i["name"] for i in items if i["name"] == _uq(t)), None)
            if nm is None:
                return None                                       # grouped by something not shown: no named key
            gcols.append(nm)
        return set(gcols)
    if re.match(r"\s*distinct\b", sel, re.I):
        if all(i["name"] for i in items):
            return {i["name"] for i in items}
        return None
    if all(i["agg"] for i in items) and "group by" not in cl and not re.search(r"\bover\s*\(", sel, re.I):
        return set()
    return None


def _named_key(table: str, col: str, bkeys: dict) -> tuple[bool, str]:
    """(is it a key, how we know): 'block' when a block's own GROUP BY / DISTINCT proves it, 'name' when only the name says so."""
    t, c = _uq(table), _uq(col)
    if t in bkeys:
        return (any(k <= {c} for k in bkeys[t]), "block")
    flat = c.replace("_", "")
    if flat == "id" or flat in {x.replace("_", "") + "id" for x in C._singulars(t)}:
        return True, "name"
    return False, "name"


def grain(sql: str) -> dict:
    """{'blocks': [per-block reading], 'tree': text, 'lines': n} -- see the module text."""
    s = C._mask(sql)
    bl = blocks(sql)
    bkeys = keys(sql)
    out = []
    by_id = {b["id"]: b for b in bl}
    for blk in bl:
        if blk.get("branches"):
            out.append({**_public(blk), "input": "the rows of its branches together", "output": "the rows of its branches together"
                        + (" (UNION removes repeats)" if re.search(r"\bunion\b(?!\s+all)", s[blk["start"]:blk["end"]], re.I) else ""),
                        "joins": [], "filters": [], "totals": [], "windows": [], "multiplies": [], "assumptions": []})
            continue
        out.append({**_public(blk), **_read_block(sql, s, blk, bkeys, by_id)})
    return {"blocks": out, "lines": len(sql.strip().splitlines())}


def _public(blk: dict) -> dict:
    return {k: blk[k] for k in ("id", "kind", "name", "parent", "depends", "start", "end", "recursive", "branches") if k in blk}


def _read_block(sql: str, s: str, blk: dict, bkeys: dict, by_id: dict) -> dict:
    a, b = blk["start"], blk["end"]
    items, cl = _select_items(sql, s, a, b)
    own = _mask_children(s, a, b)
    dep = C._depths(own)
    res = {"input": "", "output": "", "joins": [], "filters": [], "totals": [], "windows": [], "multiplies": [], "assumptions": [],
           "final_cut": []}
    if "from" not in cl:
        res["input"] = res["output"] = "one row (no FROM)"
        return res
    srcs = C._sources(own, dep, *cl["from"])
    alias_of = {}
    for x in srcs:
        nm = _uq(x.name) if x.name else None
        if not nm:                                                  # a derived table: its block, by alias
            nm = _uq(x.alias)
        alias_of[_uq(x.alias or x.name or "")] = nm
        if x.name:
            alias_of.setdefault(_uq(x.name), nm)

    def label(nm):
        if nm in bkeys or any(v["kind"] in ("cte", "derived") and v["name"] == nm for v in by_id.values()):
            blkk = next((v for v in by_id.values() if v["kind"] in ("cte", "derived") and v["name"] == nm), None)
            return f"{nm} ({'CTE' if blkk and blkk['kind'] == 'cte' else 'derived table'})"
        return nm

    unit = [alias_of.get(_uq(srcs[0].alias or srcs[0].name or ""), "?")] if srcs else []
    for x in srcs[1:]:
        me = _uq(x.alias or x.name or "")
        tn = alias_of.get(me, me)
        if x.kind in ("comma", "cross") and not x.on:
            unit.append(tn)
            res["joins"].append({"table": label(tn), "effect": "every row meets every row (no condition)", "how": "proven"})
            res["multiplies"].append({"by": tn, "how": "proven"})
            continue
        eqs = [(_uq(m.group(1)), _uq(m.group(2)), _uq(m.group(3)), _uq(m.group(4))) for m in C.EQ.finditer(x.on or "")]
        mine_cols, other_side = [], []
        for l1, c1, r1, c2 in eqs:
            if l1 == me and r1 != me:
                mine_cols.append(c1)
                other_side.append((r1, c2))
            elif r1 == me and l1 != me:
                mine_cols.append(c2)
                other_side.append((l1, c1))
        new_one = [(_named_key(tn, c, bkeys)) for c in mine_cols]
        old_one = [(_named_key(alias_of.get(o, o), oc, bkeys)) for o, oc in other_side]
        to_one = any(k for k, _h in new_one)
        from_one = any(k for k, _h in old_one)
        how_new = "proven" if any(k and h == "block" for k, h in new_one) else "guessed from the column name"
        how_old = "proven" if any(k and h == "block" for k, h in old_one) else "guessed from the column name"
        on_txt = re.sub(r"\s+", " ", sql[a + x.on_start:a + x.on_end].strip()) if x.on else (x.using or "")
        if to_one:
            res["joins"].append({"table": label(tn), "on": on_txt, "effect": f"keeps the grain (at most one {tn} row each)", "how": how_new})
            if how_new != "proven":
                res["assumptions"].append(f"{tn}.{mine_cols[0]} is unique (only the name says so)")
        elif from_one:
            res["joins"].append({"table": label(tn), "on": on_txt, "effect": f"moves the grain down to {tn} (each earlier row repeats once "
                                                                          f"per matching {tn} row)", "how": how_old})
            res["multiplies"].append({"by": tn, "how": "likely"})
            unit = [tn]
            if how_old != "proven":
                res["assumptions"].append(f"{other_side[0][0]}.{other_side[0][1]} is unique (only the name says so)")
        else:
            res["joins"].append({"table": label(tn), "on": on_txt, "effect": "can multiply rows both ways (neither side is a known key)",
                                 "how": "likely"})
            res["multiplies"].append({"by": tn, "how": "likely"})
            unit.append(tn)
    res["input"] = ("one row per " + " x ".join(f"{label(u)} row" for u in unit)) if unit else ""
    if "where" in cl:
        res["filters"] = [re.sub(r"\s+", " ", p) for p in _parts(sql, own, cl["where"])]
    res["totals"] = [i["expr"] for i in items if i["agg"]]
    res["windows"] = [i["expr"] for i in items if re.search(r"\bover\s*\(", i["expr"], re.I)]
    sel = own[cl["select"][0]:cl["select"][1]]
    if "group by" in cl:
        g = re.sub(r"\s+", " ", sql[a + cl["group by"][0]:a + cl["group by"][1]].strip().rstrip(";"))
        res["output"] = f"one row per {g}"
    elif res["totals"] and not res["windows"]:
        res["output"] = "one row (a total of every row)"
    elif re.match(r"\s*distinct\b", sel, re.I):
        res["output"] = "one row per distinct output row (DISTINCT)"
    else:
        res["output"] = res["input"]
    if "having" in cl:
        res["filters"].append("HAVING " + re.sub(r"\s+", " ", sql[a + cl["having"][0]:a + cl["having"][1]].strip()))
    if "limit" in cl:
        res["final_cut"].append("LIMIT " + re.sub(r"\s+", " ", sql[a + cl["limit"][0]:a + cl["limit"][1]].strip().rstrip(";")))
    return res


def _parts(sql: str, own: str, w) -> list[str]:
    text = own[w[0]:w[1]]
    d = C._depths(text)
    cuts = [0] + [m.end() for m in re.finditer(r"\band\b", text, re.I) if d[m.start()] == 0 and
                  not re.search(r"\bbetween\s+\S+\s*$", text[:m.start()], re.I)] + [len(text)]
    return [p for p in (re.sub(r"^\s*and\b|\band\s*$", "", text[x:y], flags=re.I).strip() for x, y in zip(cuts, cuts[1:])) if p][:8]


# ------------------------------------------------------------------------------------------------ the tree, in words
def tree(sql: str, hi: bool = False) -> str:
    g = grain(sql)
    by = {b["id"]: b for b in g["blocks"]}
    L = []

    def show(bid, depth, seen):
        b = by[bid]
        head = ("main query" if b["kind"] == "main" else f"CTE {b['name']}" if b["kind"] == "cte" else
                f"derived table {b['name']}" if b["kind"] == "derived" else
                (f"part {b['name'].rsplit('#', 1)[1]} of the UNION" if b["kind"] == "branch" else b["id"]))
        if b.get("recursive"):
            head += " (recursive)"
        pad = "  " * depth
        L.append(f"{pad}- {head}: " + (f"input {b['input']} -> output {b['output']}" if not hi else
                                       f"andar {b['input']} -> bahar {b['output']}"))
        for j in b["joins"]:
            if "keeps" not in j["effect"]:
                L.append(f"{pad}    ! join {j['table']}: {j['effect']}" + ("" if j["how"] == "proven" else f" ({j['how']})"))
        if b["totals"]:
            L.append(f"{pad}    totals: " + ", ".join(b["totals"][:4]))
        if bid in seen:
            return
        for d in (b.get("branches") or b["depends"]):
            if d in by:
                show(d, depth + 1, seen | {bid})
    if "main" in by:
        show("main", 0, set())
    for name in unused_ctes(sql):                            # night 15: said in the tree too, where a reader looks for it
        L.append(f"- CTE {name}: " + ("koi ise nahi padhta (result tak nahi pahunchta)" if hi else "nothing reads it (it never reaches the result)"))
    return "\n".join(L)


# ------------------------------------------------------------------------------------------------ checking every block
def _label(blk: dict) -> str:
    return ("CTE " + blk["name"] if blk["kind"] == "cte" else "derived table " + blk["name"] if blk["kind"] == "derived" else
            "part " + blk["name"].rsplit("#", 1)[1] + " of the UNION" if blk["kind"] == "branch" else blk["id"])


def cte_order(sql: str):
    """(finding fields, reordered query) when a CTE reads a CTE defined after it: without RECURSIVE a WITH sees only the
    CTEs above it (MariaDB 1146, PostgreSQL 42P01, lab). The fix puts each CTE after the ones it reads; None if it's fine."""
    s = C._mask(sql)
    ctes, main_at = _ctes(s)
    if len(ctes) < 2 or any(c["recursive"] for c in ctes):
        return None
    names = [c["name"] for c in ctes]
    reads = {}
    for c in ctes:
        body = s[c["start"]:c["end"]]
        reads[c["name"]] = [n for n in names if n != c["name"] and re.search(rf"\b(from|join)\s+[`\"]?{re.escape(n)}[`\"]?(?![\w$])", body, re.I)]
    late = [(c["name"], n) for k, c in enumerate(ctes) for n in reads[c["name"]] if names.index(n) > k]
    if not late:
        return None
    order, left = [], list(names)
    while left:
        ready = [n for n in left if all(r in order for r in reads[n])]
        if not ready:
            return late, None                                     # they read each other: no order works
        order.append(ready[0])
        left.remove(ready[0])
    by = {c["name"]: c for c in ctes}
    defs = [sql[by[n]["head"]:by[n]["end"] + 1].strip() for n in order]
    lead = sql[:re.match(r"\s*with\s+", s, re.I).end()]        # a comment above the WITH stays where it was
    return late, lead + ",\n".join(defs) + "\n" + sql[main_at:].lstrip()


def with_block_findings(sql: str, out: dict) -> dict:
    """The checker run on each CTE, derived table and UNION part the main check doesn't read as a query of its own (a total
    inside a CTE that a join multiplies, = NULL inside a derived table ...). Each finding names its block; the fix of a block
    is put back where the block is. Correlated subqueries (WHERE x IN (SELECT ... outer.col)) and LATERAL / recursive blocks
    are not checked alone: they read names from outside."""
    s = C._mask(sql)
    bl = blocks(sql)
    if len(bl) < 2:
        return out
    have = {(f["topic"], f["title_en"]) for f in out.get("findings") or []}
    fixed = out.get("corrected")
    mine = []
    for blk in bl:
        if blk["kind"] not in ("cte", "derived", "branch") or blk.get("recursive") or blk.get("branches"):
            continue
        parent = next((x for x in bl if x["id"] == blk["parent"]), None)
        if blk["kind"] == "derived" and parent and parent["kind"] not in ("main", "branch"):
            continue                                       # a derived table inside a CTE: that CTE's own check reads it
        if blk["kind"] == "branch" and parent and parent["kind"] != "main":
            continue
        if re.search(r"\blateral\s*$", s[max(0, blk["start"] - 20):blk["start"] - 1], re.I):
            continue
        text = sql[blk["start"]:blk["end"]]
        if not re.match(r"\s*(select|with)\b", C._mask(text), re.I):
            continue
        res = C.check(text, True)
        new = [f for f in res.get("findings") or [] if (f["topic"], f["title_en"]) not in have and f["topic"] != "learned"
               and f["topic"] != "star_join"]                # a block's * only feeds the query around it (star_dup says the rest)
        if not new:
            continue
        for f in new:
            have.add((f["topic"], f["title_en"]))
            f = dict(f)
            f["block"] = blk["id"]
            f["evidence"] = f"[{_label(blk)}] " + (f.get("evidence") or "")
            f["title_en"] = f"In {_label(blk)}: {f['title_en']}"
            f["title_hi"] = f"{_label(blk)} mein: {f['title_hi']}"
            mine.append(f)
        inner = res.get("corrected")
        if inner and inner.strip().rstrip(";") != text.strip().rstrip(";"):
            base = fixed or sql
            if base.count(text) == 1:
                fixed = base.replace(text, inner.strip().rstrip(";"))
    for name in unused_ctes(sql):                            # night 15: a CTE nothing reads (often a join that was forgotten)
        if ("cte_unused", f"CTE {name} is never read") in have:
            continue
        mine.append({"kind": "check", "topic": "cte_unused", "title_en": f"CTE {name} is never read",
                     "title_hi": f"CTE {name} kahin padha nahi jaata", "evidence": f"WITH {name} AS (...)", "block": name,
                     "fix_en": f"Nothing in the query reads {name}: neither the main query nor another CTE uses it, so its rows never "
                               f"reach the result (MariaDB and PostgreSQL still check its table and column names, but don't run it). "
                               f"If its numbers were meant to be in the report, join it where they belong; if not, delete it.",
                     "fix_hi": f"Query mein {name} ko koi nahi padhta: na main query, na koi aur CTE, isliye uski rows result tak nahi "
                               f"pahunchti (MariaDB aur PostgreSQL naam check karte hain, par chalate nahi). Uske numbers report mein "
                               f"chahiye the to use sahi jagah join karo; nahi to hata do.", "needs": []})
    co = cte_order(sql)
    if co:
        late, fixed_order = co
        a, b = late[0]
        mine.append({"kind": "error", "topic": "cte_order", "title_en": f"CTE {a} reads {b}, which is defined after it",
                     "title_hi": f"CTE {a} {b} ko padhta hai, jo uske baad bana hai",
                     "evidence": "; ".join(f"{x} reads {y}" for x, y in late),
                     "fix_en": f"Without RECURSIVE, a WITH only sees the CTEs written above it, so {b} doesn't exist yet when {a} runs "
                               f"(MySQL/MariaDB 1146, PostgreSQL 42P01). Write {b} first."
                               + ("" if fixed_order else " They read each other, so no order works: merge them or use WITH RECURSIVE."),
                     "fix_hi": f"RECURSIVE ke bina WITH sirf upar likhe CTE dekhta hai, isliye {a} chalte waqt {b} hota hi nahi "
                               f"(MySQL/MariaDB 1146, PostgreSQL 42P01). {b} ko pehle likho.", "needs": []})
        if fixed_order and not fixed:
            fixed = fixed_order
        elif fixed_order and fixed:
            fixed = cte_order(fixed)[1] if cte_order(fixed) and cte_order(fixed)[1] else fixed
    if not mine:
        return out
    order = {"error": 0, "wrong": 1, "check": 2}
    out = dict(out)
    out["findings"] = sorted(list(out.get("findings") or []) + mine, key=lambda f: order[f["kind"]])
    if fixed:
        out["corrected"] = fixed
    return out


def locate(sql: str, findings: list) -> None:
    """Night 15: a finding the whole-query check made (no block named) gets the block its evidence is written in, when that
    text is in exactly one innermost block that isn't the main query: 'In CTE paid_orders' tells a reader of a 200-line query
    where to look. The block goes in f['block'] and in front of the evidence; the title stays as it was."""
    try:
        bl = blocks(sql)
    except Exception:  # noqa: BLE001 - an odd query: nothing located
        return
    if len(bl) < 2:
        return
    flat = lambda x: re.sub(r"\s+", " ", x or "").strip().lower()  # noqa: E731
    for f in findings or []:
        if not isinstance(f, dict) or f.get("block") or not f.get("kind"):
            continue
        ev = flat(re.sub(r"^\s*(and|or|where|on)\s+", "", f.get("evidence") or "", flags=re.I))
        if len(ev) < 6:
            continue
        where = []
        for b in bl:
            if b.get("branches"):
                continue
            own = list(sql[b["start"]:b["end"]])
            for k in bl:                                     # the blocks inside it are their own: blanked out
                if k is not b and b["start"] <= k["start"] and k["end"] <= b["end"] and (k["start"], k["end"]) != (b["start"], b["end"]):
                    own[k["start"] - b["start"]:k["end"] - b["start"]] = " " * (k["end"] - k["start"])
            own = "".join(own)
            if ev in flat(own):
                where.append(b)
        if len(where) == 1 and where[0]["kind"] in ("cte", "derived", "branch"):
            f["block"] = where[0]["id"]
            if not (f.get("evidence") or "").startswith("["):
                f["evidence"] = f"[{_label(where[0])}] " + (f.get("evidence") or "")


def unused_ctes(sql: str) -> list[str]:
    """The CTEs nothing reads: not the main query, not a CTE the main query reaches (directly or through others). A CTE
    that changes data (PostgreSQL's WITH d AS (DELETE ... RETURNING ...)) runs anyway and is left out."""
    try:
        bl = blocks(sql)
    except Exception:  # noqa: BLE001
        return []
    s = C._mask(sql)
    by = {b["id"]: b for b in bl}
    ctes = [b for b in bl if b["kind"] == "cte"]
    if not ctes or "main" not in by:
        return []
    seen, todo = set(), ["main"]
    while todo:
        bid = todo.pop()
        if bid in seen or bid not in by:
            continue
        seen.add(bid)
        b = by[bid]
        todo += list(b.get("branches") or []) + list(b["depends"])
        todo += [k["id"] for k in bl if k["parent"] == bid and k["kind"] != "cte"]
    return [c["name"] for c in ctes if c["id"] not in seen and
            not re.match(r"\s*(insert|update|delete|merge)\b", s[c["start"]:c["end"]], re.I)]
