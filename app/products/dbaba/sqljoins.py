"""Join and relationship reasoning (night 2 of the curriculum), shared by every database (fundamentals 'join_condition',
'outer_join', 'cardinality' in app/dbknow/core/query_fundamentals.json).

A join is right when each joined table is tied to one already in the query by matching rows that belong together
(child.foreign_key = parent.primary_key). The checks below read that from the query text alone, and say how sure they are:
- a table with no condition tying it to the rest (comma join, or an ON that only names one side): every row meets every
  row. Sure -> 'wrong'.
- two primary keys compared (o.id = c.id), a key used the wrong way round, a key compared with the wrong parent, a name
  instead of a key, NATURAL JOIN / USING (id): the rows paired may not belong together. Depends on the schema -> 'check'.
- an anti-join that tests a column which can be NULL in a matched row, a self join that pairs each row with itself (and
  every pair twice), DISTINCT used to remove the copies a join made: depends on what was meant -> 'check', with a
  rewrite offered ("If that's what you meant").
Every rule was reproduced on MariaDB and PostgreSQL (tests/practice/test_night2.py has the lab table).
Returns [(Finding, edit or None)], edit = (start, end, new text) on the original query.
"""

import re

from app.agents.sqlverify import keep as schema_keep

# the engines each rule was reproduced on (tests/practice/test_night2.py, LAB) -- standing rule B
PROVEN = {"cartesian": ["mariadb", "postgresql"], "join_key": ["mariadb", "postgresql"], "natural_join": ["mariadb", "postgresql"],
          "anti_join": ["mariadb", "postgresql"], "self_join": ["mariadb", "postgresql"], "distinct_join": ["mariadb", "postgresql"]}

# tables that are usually the parent side of a link (earlier = more often the parent); used only to guess which side holds
# the foreign key when the query itself doesn't show it, and the guess is always offered as a 'check', never applied
PARENTS = ["categories", "departments", "suppliers", "companies", "customers", "users", "clients", "accounts", "students",
           "authors", "vendors", "teachers", "employees", "products", "courses", "orders", "posts", "invoices", "order_items"]
NAME_COL = re.compile(r"(^|_)(name|full_name|title|email)$", re.I)
EQ_ANY = re.compile(r"[`\"]?([A-Za-z_]\w*)[`\"]?\.[`\"]?(\w+)[`\"]?\s*=\s*[`\"]?([A-Za-z_]\w*)[`\"]?\.[`\"]?(\w+)[`\"]?")


def _bare(name: str) -> str:
    return (name or "").split(".")[-1].strip('`"').lower()


PREFIX = re.compile(r"^(?:[a-z]{1,4}\d*_|tbl_?|tb_)(?=[a-z])", re.I)   # wp_posts, tbl_orders, app1_users: the part before the name


def _base(table: str) -> str:
    return PREFIX.sub("", _bare(table))


def _one(word: str) -> str:
    """courses -> course, classes -> class, categories -> category, batches -> batch, statuses -> status, warehouses -> warehouse."""
    w = word.lower()
    if re.search(r"ies$", w):
        return w[:-3] + "y"
    if re.search(r"(sses|xes|ches|shes|zes)$", w) or re.search(r"(status|bus|campus|virus|bonus|census|address)es$", w):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _fk(table: str) -> str:
    """The usual name of a foreign key pointing at this table: customers -> customer_id (wp_posts -> post_id)."""
    return _one(_base(table)) + "_id"


def key_names(table: str) -> set:
    """Names a table's own key may have: id, customer_id for customers, payment_id for fee_payments."""
    base = _base(table)
    return {"id", _fk(table), _one(base.split("_")[-1]) + "_id"}


def _key(src, refs_cols: set) -> str:
    """The table's own key as this query names it: customer_id when the query uses c.customer_id on customers, else id."""
    own = {_norm(k) for k in key_names(src.name)} - {"id"}
    return next((c for q, c in sorted(refs_cols) if q == (src.alias or "").lower() and _norm(c) in own), "id")


def _rank(table: str) -> int:
    t = _base(table)
    return PARENTS.index(t) if t in PARENTS else 999


def _norm(c: str) -> str:
    return c.lower().replace("_", "")


def _guess_key(a, b, refs_cols: set) -> tuple | None:
    """(child, parent, link) for two sources: link = 'c.customer_id = p.id', spelled the way the query spells its columns
    (customer_id / customerId). The query's own column names decide first, then the usual parent tables."""
    def link(child, parent):
        fk = next((c for q, c in refs_cols if q == (child.alias or "").lower() and _norm(c) == _norm(_fk(parent.name))), _fk(parent.name))
        pk = next((c for q, c in refs_cols if q == (parent.alias or "").lower() and _norm(c) in ("id", _norm(_fk(parent.name)))), "id")
        return f"{child.alias}.{fk} = {parent.alias}.{pk}"

    for child, parent in ((a, b), (b, a)):
        if any(q == (child.alias or "").lower() and _norm(c) == _norm(_fk(parent.name)) for q, c in refs_cols):
            return child, parent, link(child, parent)
    ra, rb = _rank(a.name), _rank(b.name)
    if ra == rb:
        return None
    child, parent = (b, a) if ra < rb else (a, b)
    return child, parent, link(child, parent)


def _trim(s: str, a: int, b: int) -> int:
    """The end of a clause without the spaces and ';' after it."""
    while b > a and (s[b - 1].isspace() or s[b - 1] == ";"):
        b -= 1
    return b


def _top_and(text: str) -> list[tuple[int, int]]:
    """Spans of the top-level AND parts of a condition (None-safe for OR: an OR at top level gives one part)."""
    from app.agents.sqlcorrect import _depths

    d = _depths(text)
    if any(d[m.start()] == 0 for m in re.finditer(r"\bor\b", text, re.I)):
        return [(0, len(text))]
    cuts = [m for m in re.finditer(r"\band\b", text, re.I) if d[m.start()] == 0 and
            not re.search(r"\bbetween\s+\S+\s*$", text[:m.start()], re.I)]
    out, prev = [], 0
    for m in cuts:
        out.append((prev, m.start()))
        prev = m.end()
    out.append((prev, len(text)))
    return out


def joins(sql: str, s: str, cl: dict, srcs: list, alias_of, items: list, intent: str, F, all_refs: list) -> list:
    out = []
    if len(srcs) < 2 or not all(x.name or x.kind == "from" for x in srcs[:1]):
        return out
    by_alias = {(x.alias or "").lower(): x for x in srcs if x.alias}
    refs_cols = {(alias_of(q), c) for q, c, _p in all_refs}
    where = cl.get("where")
    wend = _trim(s, where[0], where[1]) if where else 0
    wtext = s[where[0]:wend] if where else ""
    low = s.lower()
    said = (intent or "").lower()

    def line_of(pos: int) -> str:
        a = sql.rfind("\n", 0, pos) + 1
        b = sql.find("\n", pos)
        return re.sub(r"\s+", " ", sql[a:b if b >= 0 else len(sql)].strip())

    # links: (alias, alias) pairs tied by an equality in ON or in WHERE
    links = set()
    for x in srcs:
        for m in EQ_ANY.finditer(x.on or ""):
            links.add(frozenset((alias_of(m.group(1)), alias_of(m.group(3)))))
        if x.using:
            prev = [y for y in srcs if y.start < x.start]
            if prev:
                links.add(frozenset(((x.alias or "").lower(), (prev[-1].alias or "").lower())))
        if x.kind == "natural":
            prev = [y for y in srcs if y.start < x.start]
            if prev:
                links.add(frozenset(((x.alias or "").lower(), (prev[-1].alias or "").lower())))
    for a, b in _top_and(wtext):
        for m in EQ_ANY.finditer(wtext[a:b]):
            links.add(frozenset((alias_of(m.group(1)), alias_of(m.group(3)))))
    # sub-selects in WHERE may tie tables too (WHERE b.x IN (SELECT ...)): then the comma check stays quiet
    where_sub = bool(re.search(r"\(\s*select\b", wtext, re.I))

    # ---- J1. comma join with nothing tying the table to the others: a cartesian product
    group = {(srcs[0].alias or "").lower()}
    changed = True
    while changed:
        changed = False
        for pair in links:
            p = set(pair)
            if len(p) == 2 and p & group and not p <= group:
                group |= p
                changed = True
    for x in srcs[1:]:
        a = (x.alias or "").lower()
        if x.kind != "comma" or a in group or not x.name or where_sub or "lateral" in low:
            continue
        others = ", ".join(y.alias for y in srcs if y is not x and y.alias)
        key = _guess_key(srcs[0], x, refs_cols) if srcs[0].name else None
        hint = (f" (for example {key[2]})" if key else "")
        out.append((F("wrong", "cartesian", f"{x.name} {x.alias} is listed with a comma but never tied to {others}",
                      f"{x.name} {x.alias} comma se likha hai par {others} se joda hi nahi",
                      line_of(x.start),
                      f"FROM a, b with no condition linking them pairs every row of one with every row of the other (a cartesian "
                      f"product: 8 customers x 8 orders = 64 rows), on every database. Add the link in WHERE{hint}, or better, write "
                      f"it as JOIN {x.name} {x.alias} ON ... so the link sits next to the table.",
                      f"FROM a, b bina jodne wali condition ke har row ko har row se jodta hai (cartesian product: 8 x 8 = 64 rows). "
                      f"WHERE mein link lagao{hint}, ya behtar, JOIN {x.name} {x.alias} ON ... likho."), None))
        group.add(a)

    # ---- J2. an ON that names only the joined table (or only literals beside it): it never reaches an earlier table
    for i, x in enumerate(srcs):
        if i == 0 or not x.on or x.kind in ("cross", "natural") or "lateral" in low:
            continue
        a = (x.alias or "").lower()
        refs = {alias_of(q) for q, _c in re.findall(r"[`\"]?([A-Za-z_]\w*)[`\"]?\.[`\"]?(\w+|\*)", x.on)}
        refs = {r for r in refs if r in by_alias}
        earlier = {(y.alias or "").lower() for y in srcs[:i]}
        if a in refs and not (refs & earlier) and not re.search(r"\(\s*select\b", x.on, re.I):
            out.append((F("wrong", "cartesian", f"The ON of {x.alias} only talks about {x.alias}",
                          f"{x.alias} ka ON sirf {x.alias} ki baat karta hai", line_of(x.on_start),
                          f"This ON filters {x.alias}'s own rows but never ties them to {', '.join(sorted(earlier))}, so every kept "
                          f"{x.alias} row is paired with every earlier row (rows multiply). Add the link, e.g. "
                          f"{x.alias}.<key> = <earlier table>.id, and keep the filter with AND.",
                          f"Ye ON {x.alias} ki rows chhaanta hai par unhe {', '.join(sorted(earlier))} se jodta nahi, isliye rows "
                          f"guna ho jaati hain. Link jodo (jaise {x.alias}.<key> = <pichli table>.id) aur filter AND se rakho."), None))

    # ---- J3/J4/J8. the columns compared in ON: two primary keys, a key the wrong way round, a name instead of a key
    names = {(y.alias or "").lower(): y for y in srcs if y.name}
    table_fk = {_fk(y.name): y for y in srcs if y.name}
    for i, x in enumerate(srcs):
        if i == 0 or not x.on:
            continue
        for m in EQ_ANY.finditer(x.on):
            la, lc, ra, rc = alias_of(m.group(1)), m.group(2).lower(), alias_of(m.group(3)), m.group(4).lower()
            L, R = names.get(la), names.get(ra)
            if not L or not R or la == ra or _bare(L.name) == _bare(R.name):
                continue
            span = (x.on_start + m.start(), x.on_start + m.end())
            ev = line_of(x.on_start)
            if lc == "id" and rc == "id":                                         # J3
                key = _guess_key(L, R, refs_cols)
                new = key[2] if key else None
                out.append((F("check", "join_key", f"{m.group(0).strip()} compares two primary keys",
                              f"{m.group(0).strip()} do primary keys ko milata hai", ev,
                              f"{L.name}.id and {R.name}.id are each table's own row number, so this pairs order 5 with customer 5 "
                              f"just because the numbers match. Unless one table shares the other's key (a one-to-one extension "
                              f"table), join the foreign key to the primary key" + (f": {new}." if new else "."),
                              f"{L.name}.id aur {R.name}.id dono apni-apni table ke number hain; ye sirf number barabar hone pe "
                              f"jodta hai. Foreign key ko primary key se jodo" + (f": {new}." if new else ".")),
                            (span[0], span[1], new) if new else None))
                continue
            for (ca, cc, C), (pa, pc, P) in (((la, lc, L), (ra, rc, R)), ((ra, rc, R), (la, lc, L))):
                if pc != "id" or not cc.endswith("_id"):
                    continue
                if cc == _fk(C.name) and _fk(P.name) != cc:                     # J4: c.customer_id = o.id on customers c
                    new = f"{P.alias}.{cc} = {C.alias}.id"
                    out.append((F("check", "join_key", f"{C.alias}.{cc} = {P.alias}.id looks the wrong way round",
                                  f"{C.alias}.{cc} = {P.alias}.id ulta lagta hai", ev,
                                  f"{cc} is named after {C.name} itself, so it's most likely the key {P.name} uses to point at "
                                  f"{C.name}, not a link from {C.name} to {P.name}. The usual link is {new}.",
                                  f"{cc} ka naam {C.name} pe hai, to ye shayad wo key hai jisse {P.name} {C.name} ko point karti hai. "
                                  f"Aam link: {new}."), (span[0], span[1], new)))
                elif cc != _fk(P.name) and cc in table_fk and table_fk[cc] is not P and table_fk[cc] is not C:   # wrong parent
                    T = table_fk[cc]
                    out.append((F("check", "join_key", f"{C.alias}.{cc} points at {T.name}, but it's compared with {P.name}.id",
                                  f"{C.alias}.{cc} {T.name} ki key hai, par {P.name}.id se milayi", ev,
                                  f"{cc} is the link to {T.name} ({T.alias}), yet here it's matched with {P.alias}.id, so rows pair "
                                  f"up by coincidence of numbers. Check which column links {C.name} to {P.name} (usually "
                                  f"{C.alias}.{_fk(P.name)}).",
                                  f"{cc} {T.name} ({T.alias}) ka link hai, par yahan {P.alias}.id se milaya hai: rows sirf number "
                                  f"milne se judti hain. Dekho {C.name} ko {P.name} se kaunsi column jodti hai (aam taur pe "
                                  f"{C.alias}.{_fk(P.name)})."), (span[0], span[1], f"{C.alias}.{_fk(P.name)} = {P.alias}.id")))
            if NAME_COL.search(lc) and NAME_COL.search(rc) and not (lc == rc == "email"):  # J8
                key = _guess_key(L, R, refs_cols)
                new = key[2] if key else None
                out.append((F("check", "join_key", f"Joined on names ({m.group(0).strip()}), not on a key",
                              f"Naam se joda ({m.group(0).strip()}), key se nahi", ev,
                              "Names aren't unique: two customers called 'Aarav Shah' both match every order of either, so rows "
                              "repeat and totals double; a renamed customer loses their orders. Join on the key" +
                              (f": {new}." if new else "."),
                              "Naam unique nahi hote: do 'Aarav Shah' ho to dono har order se jud jaate hain, rows repeat hoti hain. "
                              "Key se jodo" + (f": {new}." if new else ".")), (span[0], span[1], new) if new else None))

    # ---- J9. NATURAL JOIN / USING (id): the columns are picked by name
    for i, x in enumerate(srcs):
        prev = srcs[i - 1] if i else None
        if prev is None or not x.name or not prev.name:
            continue
        if x.kind == "natural" or (x.using and re.fullmatch(r"\s*[`\"]?id[`\"]?\s*", x.using, re.I) and _bare(x.name) != _bare(prev.name)):
            key = _guess_key(prev, x, refs_cols)
            new_on = key[2] if key else None
            what = "NATURAL JOIN" if x.kind == "natural" else "USING (id)"
            out.append((F("check", "natural_join", f"{what} {x.name}: the join columns are picked by their names",
                          f"{what} {x.name}: join ki columns naam se chuni jaati hain", line_of(x.start),
                          (f"NATURAL JOIN joins on every column the two tables share by name. {prev.name} and {x.name} both have "
                           "id (and often created_at, status, name), so it matches order 5 with customer 5, and adding a column "
                           "later silently changes the join. " if x.kind == "natural" else
                           f"USING (id) means {prev.name}.id = {x.name}.id: two primary keys, matched only by coincidence of "
                           "numbers. ") + "Write the link out: JOIN ... ON " + (new_on if new_on else "child.<parent>_id = parent.id") + ".",
                          ("NATURAL JOIN har us column pe jodta hai jo dono tables mein same naam ki hai (id, created_at ...), isliye "
                           "order 5 customer 5 se jud jaata hai. " if x.kind == "natural" else
                           f"USING (id) ka matlab {prev.name}.id = {x.name}.id: do primary keys. ") +
                          "Link saaf likho: JOIN ... ON " + (new_on if new_on else "child.<parent>_id = parent.id") + "."), None))

    # ---- J5. anti-join that tests a column which may be NULL in a matched row
    for x in srcs:
        if x.kind != "left" or not x.on or not where:
            continue
        a = (x.alias or "").lower()
        mine = None
        for m in EQ_ANY.finditer(x.on):
            for q, c, oq in ((m.group(1), m.group(2), m.group(3)), (m.group(3), m.group(4), m.group(1))):
                if alias_of(q) == a and alias_of(oq) != a:
                    mine = mine or c
        if not mine:
            continue
        for pa, pb in _top_and(wtext):
            part = wtext[pa:pb]
            m = re.fullmatch(rf"\s*\(?\s*[`\"]?{re.escape(x.alias)}[`\"]?\.[`\"]?(\w+)[`\"]?\s+is\s+null\s*\)?\s*", part, re.I)
            if not m or len(_top_and(wtext)) == 1 and re.search(r"\bor\b", wtext, re.I):
                continue
            col = m.group(1)
            if col.lower() in ("id", mine.lower(), _key(x, refs_cols).lower()):
                continue
            pos = where[0] + pa + m.start(1)
            out += schema_keep((F("check", "anti_join", f"{x.alias}.{col} IS NULL is also true for matched rows with an empty {col}",
                          f"{x.alias}.{col} IS NULL match wali un rows pe bhi sach hai jinka {col} khali hai", line_of(pos),
                          f"To keep only the rows with NO {x.name} match, test a column that can't be NULL when a row matched: the "
                          f"join column {x.alias}.{mine} (it equals a real value whenever there is a match) or {x.alias}.id. "
                          f"{x.alias}.{col} can be empty in a real {x.name} row, so those rows pass too. If you meant 'no "
                          f"{x.name} OR an empty {col}', keep it.",
                          f"Sirf bina match wali rows ke liye aisi column dekho jo match hone pe NULL na ho: join column "
                          f"{x.alias}.{mine} ya {x.alias}.id. {x.alias}.{col} asli row mein bhi khali ho sakti hai."),
                        (pos - len(x.alias) - 1, pos + len(col), f"{x.alias}.{mine}")), [("not_null", x.alias, col)])

    # ---- J6. self join on a shared value: each row meets itself, and every pair comes twice
    for i, x in enumerate(srcs):
        if not x.on or not x.name:
            continue
        twin = next((y for y in srcs[:i] if y.name and _bare(y.name) == _bare(x.name)), None)
        if twin is None:
            continue
        a, b = (twin.alias or "").lower(), (x.alias or "").lower()
        same = [m for m in EQ_ANY.finditer(x.on) if {alias_of(m.group(1)), alias_of(m.group(3))} == {a, b}
                and m.group(2).lower() == m.group(4).lower() and m.group(2).lower() != "id"]
        if not same:
            continue
        both = (x.on or "") + " " + wtext
        k = _key(x, refs_cols) if _key(x, refs_cols) != "id" else _key(twin, refs_cols)
        if same[0].group(2).lower() == k.lower():
            continue                                                  # joined on its own key: each row with itself, on purpose
        from app.agents.sqlcorrect import _block_key

        if _block_key(x, same[0].group(2)):
            continue                                                  # night 13: a CTE's own GROUP BY makes it the key
        idcmp = re.search(rf"[`\"]?({re.escape(twin.alias)}|{re.escape(x.alias)})[`\"]?\.[`\"]?{k}[`\"]?\s*(<>|!=|<|>|<=|>=)\s*"
                          rf"[`\"]?({re.escape(twin.alias)}|{re.escape(x.alias)})[`\"]?\.[`\"]?{k}\b", both, re.I)
        col = same[0].group(2)
        if idcmp and idcmp.group(2) in ("<", ">"):
            continue
        if idcmp:                                                     # a.id <> b.id: each pair twice
            if not re.search(r"\b(pairs?|jodi|jodiyan|jode|combinations?|both ways|dono)\b", said):
                continue                                              # listing 'others in my department' wants both ways
            inon = idcmp.start() < len(x.on or "")
            start = x.on_start + idcmp.start(2)
            out.append((F("check", "self_join", f"{twin.alias}.{k} {idcmp.group(2)} {x.alias}.{k} lists every pair twice",
                          f"{twin.alias}.{k} {idcmp.group(2)} {x.alias}.{k} har jodi do baar laata hai", line_of(x.on_start),
                          f"With <> both (A, B) and (B, A) are kept. For each pair once, keep one side smaller: "
                          f"{twin.alias}.{k} < {x.alias}.{k}.",
                          f"<> se (A, B) aur (B, A) dono aate hain. Har jodi ek baar ke liye: {twin.alias}.{k} < {x.alias}.{k}."),
                        (start, start + len(idcmp.group(2)), "<") if inon else None))
            continue
        end = _trim(s, x.on_start, x.on_end)
        out.append((F("check", "self_join", f"Self join on {col}: each {x.name[:-1] if x.name.endswith('s') else x.name} row also pairs with itself",
                      f"{col} pe self join: har row khud se bhi judti hai", line_of(x.on_start),
                      f"Every row has the same {col} as itself, so each one comes back paired with itself, and every real pair "
                      f"appears twice ((A, B) and (B, A)). For pairs of different rows, each once, add AND {twin.alias}.{k} < "
                      f"{x.alias}.{k} (use <> to keep both directions).",
                      f"Har row ka {col} khud jaisa hai, isliye har row khud se judti hai aur har jodi do baar aati hai. Alag rows "
                      f"ki jodi ek baar: AND {twin.alias}.{k} < {x.alias}.{k} (dono taraf chahiye to <>)."),
                    (end, end, f" AND {twin.alias}.{k} < {x.alias}.{k}")))

    # ---- J7. DISTINCT used to remove the copies a join made
    sel = cl.get("select")
    if sel and re.match(r"\s*distinct\b", s[sel[0]:sel[1]], re.I) and "group by" not in cl and not any(i["agg"] for i in items):
        cols = [re.fullmatch(r"(?:distinct\s+)?[`\"]?([A-Za-z_]\w*)[`\"]?\.[`\"]?(\w+|\*)[`\"]?"
                             r"(?:\s+(?:as\s+)?[`\"]?[A-Za-z_]\w*[`\"]?)?", i["text"].strip(), re.I) for i in items]   # a renamed column too
        if cols and all(cols) and len({alias_of(c.group(1)) for c in cols}) == 1 and len(srcs) == 2:
            keep = alias_of(cols[0].group(1))
            first, other = srcs[0], srcs[1]
            if (first.alias or "").lower() == keep and other.kind in ("join", "inner") and other.on and other.name and first.name:
                ob = cl.get("order by")
                ob_text = s[ob[0]:ob[1]] if ob else ""
                if not re.search(rf"\b{re.escape(other.alias)}\.", ob_text, re.I):
                    new = _semi_join(sql, s, cl, first, other, where, wend, keep, cols)
                    out += schema_keep((F("check", "distinct_join", f"DISTINCT is removing the copies the join with {other.name} made",
                                  f"DISTINCT {other.name} ke join se bani copies hata raha hai", line_of(sel[0]),
                                  f"Joining {other.name} repeats each {first.name} row once per matching {other.name} row, and "
                                  f"DISTINCT then throws the copies away (after building and sorting them). If the question is "
                                  f"'{first.name} that have a matching {other.name}', EXISTS says that directly and never makes "
                                  f"the copies. The rewrite returns the same rows.",
                                  f"{other.name} jodne se har {first.name} row har match pe repeat hoti hai, aur DISTINCT copies "
                                  f"phenk deta hai. Sawaal '{first.name} jinka {other.name} hai' ho to EXISTS seedha yahi kehta hai."),
                                (0, len(sql), new) if new else None), [("to_one", other.alias or other.name, None)])
    return out


def _semi_join(sql, s, cl, first, other, where, wend, keep, cols) -> str | None:
    """SELECT DISTINCT a.x FROM a JOIN b ON cond WHERE w  ->  SELECT DISTINCT a.x FROM a WHERE EXISTS (SELECT 1 FROM b WHERE cond AND w).
    DISTINCT stays unless a's own id is selected (then a's rows are already distinct). The WHERE moves into EXISTS whole:
    its conditions on a are still visible there (correlated), so the rows kept are the same."""
    sel = cl["select"]
    sel_text = sql[sel[0]:sel[1]].strip()
    has_id = any(c.group(2).lower() == "id" for c in cols)
    if has_id:
        sel_text = re.sub(r"^\s*distinct\s+", "", sel_text, flags=re.I)
    head = sql[first.start:first.end].strip()
    head = re.sub(r"^\s*from\s+", "", head, flags=re.I)
    tail_at = wend if where else other.end
    tail = sql[tail_at:].strip().rstrip(";")
    if tail and not re.match(r"(order\s+by|limit)\b", tail, re.I):
        return None
    cond = sql[other.on_start:_trim(s, other.on_start, other.on_end)].strip()
    w = sql[where[0]:wend].strip() if where else ""
    inner = f"SELECT 1 FROM {other.name} {other.alias} WHERE {cond}" + (f" AND ({w})" if w else "")
    return f"SELECT {sel_text} FROM {head} WHERE EXISTS ({inner})" + (f" {tail}" if tail else "")
