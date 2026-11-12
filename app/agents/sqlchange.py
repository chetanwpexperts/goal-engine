"""Changing data (night 5 of the curriculum): UPDATE, DELETE, INSERT, upserts, shared by every database (fundamentals
'changes_safety', 'change_target', 'engine_syntax' in app/dbknow/core/query_fundamentals.json); what one engine accepts and
another refuses comes from the dialect files' query_rules (standing rule B).

What decides whether a change does what was meant:
- WHICH ROWS: the target rows are the ones the WHERE (and the joined tables) let through. A link to another table that never
  ties it to the target row (UPDATE t ... FROM other WHERE other.x = 1) lets every target row through; reading the target
  table a second time in PostgreSQL's FROM is a second, unlinked copy, so again every row changes. Preview the same rows
  with a SELECT before running the change.
- HOW OFTEN: each target row is changed at most once per statement, on every engine. When the joined table has several
  matching rows (one order, two payments), SET x = x + p.amount adds only one of them, and SET x = p.amount keeps one of
  them, which one is not defined. Total the child rows first (a derived table grouped by the link), then join.
- WHAT VALUE: SET x = (SELECT ...) with no WHERE gives NULL to every row that has no match in the subquery.
- WHICH COLUMN: INSERT ... SELECT fills the listed columns by POSITION, never by name: a SELECT that lists the same columns in
  another order swaps the values silently (when the types allow it). Too few or too many values is an error.
- DUPLICATES: deleting duplicates through a self-join needs a rule for which copy stays (t1.id > t2.id keeps the lowest id);
  without it every row matches itself and the whole table goes; with t1.id <> t2.id both copies of each pair go.
- ENGINE SYNTAX: MySQL/MariaDB write UPDATE a JOIN b ON ... SET and DELETE a FROM a JOIN b; PostgreSQL writes UPDATE a SET
  ... FROM b WHERE and DELETE FROM a USING b WHERE, does not let SET name the table (SET a.x), has no LIMIT on UPDATE/DELETE,
  and upserts with ON CONFLICT (key) DO UPDATE SET x = EXCLUDED.x where MySQL/MariaDB use ON DUPLICATE KEY UPDATE x = VALUES(x).
Every rule was reproduced on MariaDB 10.11 and PostgreSQL 16 (tests/practice/test_night5.py has the lab table).
Returns [(Finding, [edits])], edit = (start, end, new text) on the original query; (0, len(sql), text) rewrites it whole.
"""

import re

# the engines each rule was reproduced on (tests/practice/test_night5.py, LAB) -- standing rule B
PROVEN = {k: ["mariadb", "postgresql"] for k in (
    "update_join_syntax", "delete_join_syntax", "set_qualified", "update_from_self", "change_unlinked", "join_change_many",
    "set_subquery_null", "upsert_syntax", "conflict_ambiguous", "conflict_no_target", "insert_count", "insert_order",
    "insert_no_columns", "change_limit", "dedupe_self", "delete_no_target", "insert_ignore", "replace_into")}

NAME = r"[`\"]?[A-Za-z_][\w$]*[`\"]?"
QNAME = rf"{NAME}(?:\.{NAME})?"
AGG = re.compile(r"^\s*(count|sum|avg|min|max)\s*\(", re.I)


def _C():
    from app.agents import sqlcorrect as C

    return C


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def _short(t: str, n: int = 110) -> str:
    t = _flat(t)
    return t if len(t) <= n else t[:n - 3] + "..."


def _uq(n: str) -> str:
    return (n or "").strip('`"').lower()


def _engine(sql: str):
    from app.agents import sqlbasics

    return sqlbasics.engine_seen(sql)


def _said(key: str, fam: str) -> str:
    from app.agents import sqlbasics

    return sqlbasics.rules(fam).get(key, {}).get("says", "")


def _verdict(key: str, eng, works_on: str):
    """An engine-syntax rule: works_on = the family whose syntax this is. (kind or None, text naming the engines)."""
    other = "postgresql" if works_on == "mysql" else "mysql"
    if eng == works_on:
        return None, ""
    if eng == other:
        return "error", _said(key, other)
    return "check", f"{_said(key, works_on)} {_said(key, other)}".strip()


def _cut(s: str, start: int, end: int, words: list[str]) -> dict:
    """Top-level clauses of s[start:end]: {word: (body_start, body_end)}; the first top-level match of each word."""
    C = _C()
    dep = C._depths(s)
    marks = []
    for w in words:
        for m in re.finditer(r"\b" + w.replace(" ", r"\s+") + r"\b", s[start:end], re.I):
            if dep[start + m.start()] == 0:
                marks.append((start + m.start(), start + m.end(), w))
                break
    marks.sort()
    out = {}
    for i, (a, b, w) in enumerate(marks):
        out[w] = (b, marks[i + 1][0] if i + 1 < len(marks) else end)
        out["@" + w] = a
    return out


def _end_of(s: str) -> int:
    m = re.search(r";\s*$", s)
    return m.start() if m else len(s.rstrip())


def _sources(s: str, a: int, b: int):
    C = _C()
    return C._sources(s, C._depths(s), a, b)


def _assignments(sql: str, s: str, a: int, b: int) -> list:
    """SET a = x, b = y -> [(col_text, col_start, col_end, expr_text, expr_start, expr_end)] (top-level commas only)."""
    C = _C()
    out = []
    for off, part in C._split_top(s[a:b]):
        m = re.match(rf"\s*({QNAME})\s*=(?!=)", part)
        if not m:
            continue
        cs, ce = a + off + m.start(1), a + off + m.end(1)
        es = a + off + m.end()
        ee = a + off + len(part)
        while es < ee and sql[es].isspace():
            es += 1
        while ee > es and sql[ee - 1].isspace():
            ee -= 1
        out.append((sql[cs:ce], cs, ce, sql[es:ee], es, ee))
    return out


def _refs(text: str) -> set:
    """Aliases named in a piece of (masked) text: x.col -> x."""
    return {_uq(m.group(1)) for m in re.finditer(rf"({NAME})\.{NAME}", text)}


def _fk(name: str) -> str:
    from app.agents.sqljoins import _fk as fk

    return fk(name)


def _link_hint(target, other) -> str:
    if not target or not other or not target.name or not other.name:
        return ""
    return f" (for example {other.alias}.{_fk(target.name)} = {target.alias}.id)"


# ------------------------------------------------------------------------------------------------ reading the statement
def read_update(sql: str, s: str):
    """{'target': Source, 'section': (a, b) of the target part (with its joins), 'joined': [Source], 'set': (a, b),
    'from': (a, b) or None, 'where': (a, b) or None, 'order', 'limit', 'form': 'join' / 'from' / 'plain'}"""
    um = re.match(r"\s*update\s+(?:low_priority\s+)?(?:ignore\s+)?", s, re.I)
    if not um:
        return None
    end = _end_of(s)
    cl = _cut(s, um.end(), end, ["set", "from", "where", "order by", "limit", "returning"])
    if "set" not in cl:
        return None
    sec = (um.end(), cl["@set"])
    srcs = _sources(s, *sec)
    if not srcs or not srcs[0].name:
        return None
    frm = cl.get("from")
    if frm and cl["@from"] < cl["@set"]:
        frm = None
    out = {"target": srcs[0], "section": sec, "joined": srcs[1:], "set": cl["set"], "from": frm, "where": cl.get("where"),
           "order": cl.get("order by"), "limit": cl.get("limit"), "returning": cl.get("returning"), "cl": cl, "end": end}
    out["from_srcs"] = _sources(s, *frm) if frm else []
    out["form"] = "join" if srcs[1:] else ("from" if frm else "plain")
    return out


def read_delete(sql: str, s: str):
    """DELETE [targets] FROM section [USING section] [WHERE] [ORDER BY] [LIMIT]."""
    dm = re.match(r"\s*delete\s+(?:low_priority\s+)?(?:quick\s+)?(?:ignore\s+)?", s, re.I)
    if not dm:
        return None
    end = _end_of(s)
    cl = _cut(s, dm.end(), end, ["from", "using", "where", "order by", "limit", "returning"])
    if "from" not in cl:
        return None
    targets = [_uq(t) for t in re.findall(rf"({NAME})(?:\.\*)?", s[dm.end():cl["@from"]])]
    stop = min([cl[k] for k in ("@using", "@where", "@order by", "@limit", "@returning") if k in cl] + [end])
    sec = (cl["from"][0], stop)
    srcs = _sources(s, *sec)
    if not srcs or not srcs[0].name:
        return None
    using = (cl["using"][0], min([cl[k] for k in ("@where", "@order by", "@limit", "@returning") if k in cl] + [end])) \
        if "using" in cl else None
    return {"targets": targets, "section": sec, "srcs": srcs, "using": using, "using_srcs": _sources(s, *using) if using else [],
            "where": cl.get("where"), "order": cl.get("order by"), "limit": cl.get("limit"), "cl": cl, "end": end,
            "form": "multi" if targets else ("using" if using else ("join" if len(srcs) > 1 else "plain"))}


def read_insert(sql: str, s: str):
    """INSERT/REPLACE [IGNORE] INTO t [(cols)] VALUES (...)/SELECT ... [ON DUPLICATE KEY UPDATE ... | ON CONFLICT ...]."""
    C = _C()
    im = re.match(rf"\s*(insert|replace)\s+(?:low_priority\s+|delayed\s+|high_priority\s+)?(ignore\s+)?into\s+({QNAME})(?:\s+(?:as\s+)?({NAME}))?\s*",
                  s, re.I)
    if not im:
        return None
    end = _end_of(s)
    pos = im.end()
    alias = im.group(4) if im.group(4) and _uq(im.group(4)) not in ("values", "value", "select", "with", "set", "default") else None
    if not alias:
        pos = im.end(3)
        while pos < end and s[pos].isspace():
            pos += 1
    cols, cols_span = None, None
    if pos < end and s[pos] == "(" and not re.match(r"\(\s*select\b", s[pos:], re.I):
        b = C._close(s, pos)
        cols = [(_uq(c.strip()), sql[pos + 1 + o:pos + 1 + o + len(c)].strip()) for o, c in C._split_top(s[pos + 1:b])]
        cols_span = (pos, b + 1)
        pos = b + 1
    cl = _cut(s, pos, end, ["values", "value", "select", "on duplicate key update", "on conflict", "returning"])
    body_kind = "values" if ("values" in cl or "value" in cl) else ("select" if "select" in cl else None)
    if body_kind is None and re.match(r"\s*\(\s*select\b|\s*with\b", s[pos:end], re.I):
        body_kind = "select"
    rows = []
    if body_kind == "values":
        a, b = cl.get("values") or cl.get("value")
        stop = min([cl[k] for k in ("@on duplicate key update", "@on conflict", "@returning") if k in cl] + [end])
        k = a
        while True:
            m = re.compile(r"\s*\(").match(s, k)
            if not m or m.end() > stop:
                break
            o = m.end() - 1
            c = C._close(s, o)
            rows.append([sql[o + 1 + x:o + 1 + x + len(t)].strip() for x, t in C._split_top(s[o + 1:c])])
            k = c + 1
            n = re.compile(r"\s*,").match(s, k)
            if not n:
                break
            k = n.end()
    sel = None
    if body_kind == "select":
        a = cl["@select"] if "@select" in cl else pos
        stop = min([cl[k] for k in ("@on duplicate key update", "@on conflict", "@returning") if k in cl] + [end])
        sel = (a, stop)
    return {"verb": im.group(1).lower(), "ignore": bool(im.group(2)), "table": _uq(im.group(3).split(".")[-1]),
            "table_text": sql[im.start(3):im.end(3)], "alias": alias, "cols": cols, "cols_span": cols_span, "kind": body_kind,
            "rows": rows, "select": sel, "dup": cl.get("on duplicate key update"), "dup_at": cl.get("@on duplicate key update"),
            "conflict": cl.get("on conflict"), "conflict_at": cl.get("@on conflict"), "returning": cl.get("returning"), "end": end}


def _select_items(sql: str, s: str, a: int, b: int):
    """The output items of the (top-level) SELECT in s[a:b]: [(start, end, text, name)]."""
    C = _C()
    m = re.compile(r"\s*\(?\s*select\s+(?:distinct\s+|all\s+)?", re.I).match(s, a)
    if not m:
        return None
    sub = s[m.end():b]
    cl = _cut(sub, 0, len(sub), ["from", "where", "group by", "having", "order by", "limit", "union"])
    stop = cl["@from"] if "@from" in cl else (min(v for k, v in cl.items() if k.startswith("@")) if cl else len(sub))
    items = []
    for off, part in C._split_top(sub[:stop]):
        st = m.end() + off + (len(part) - len(part.lstrip()))
        en = m.end() + off + len(part.rstrip())
        text = sql[st:en]
        am = re.search(rf"(?:\bas\s+|\s)({NAME})\s*$", s[st:en], re.I)
        if am and not re.search(r"[.(]\s*$", s[st:st + am.start(1)]) and _uq(am.group(1)) not in _C().KW:
            name = _uq(am.group(1))
        else:
            cm = re.fullmatch(rf"\s*(?:{NAME}\.)?({NAME})\s*", s[st:en])
            name = _uq(cm.group(1)) if cm else None
        items.append((st, en, text, name))
    return items


# ------------------------------------------------------------------------------------------------ the rules
def changes(sql: str, s: str, intent: str, F) -> list:
    """UPDATE / DELETE / INSERT / REPLACE: the rules above. F is sqlcorrect.Finding."""
    low = s.lower().lstrip()
    try:
        if low.startswith("update"):
            return _update(sql, s, intent, F)
        if low.startswith("delete"):
            return _delete(sql, s, intent, F)
        if low.startswith(("insert", "replace")):
            return _insert(sql, s, intent, F)
    except Exception:  # noqa: BLE001 - a statement shape this reader doesn't know: say nothing rather than something wrong
        return []
    return []


def _strip_target(sql: str, s: str, a: int, b: int, names: set) -> str:
    """SET o.amount = ... -> SET amount = ... (only the column being set loses its table name)."""
    out, last = [], a
    for col, cs, ce, _e, _es, _ee in _assignments(sql, s, a, b):
        m = re.match(rf"({NAME})\.({NAME})$", col)
        if m and _uq(m.group(1)) in names:
            out.append(sql[last:cs] + m.group(2))
            last = ce
    out.append(sql[last:b])
    return "".join(out)


def _qualify_target(sql: str, s: str, a: int, b: int, alias: str) -> str:
    out, last = [], a
    for col, cs, ce, _e, _es, _ee in _assignments(sql, s, a, b):
        if "." not in col:
            out.append(sql[last:cs] + f"{alias}.{col}")
            last = ce
    out.append(sql[last:b])
    return "".join(out)


def _update(sql: str, s: str, intent: str, F) -> list:
    out = []
    u = read_update(sql, s)
    if u is None:
        return out
    eng = _engine(sql)
    t = u["target"]
    names = {_uq(t.alias), _uq(t.name.split(".")[-1])}
    where = u["where"]
    wtext = s[where[0]:where[1]] if where else ""
    set_a, set_b = u["set"]
    head_ev = _short(sql[:u["end"]])

    # ---- U1. MySQL's UPDATE a JOIN b ON ... SET: PostgreSQL wants UPDATE a SET ... FROM b WHERE ...
    if u["form"] == "join":
        kind, says = _verdict("update_join", eng, "mysql")
        inner = all(j.kind in ("join", "comma") for j in u["joined"]) and all(j.name for j in u["joined"])
        edits = []
        if kind and inner:
            conds = [_flat(sql[j.on_start:j.on_end]) for j in u["joined"] if j.on]
            if where:
                conds.append(_flat(sql[where[0]:where[1]]) if not re.search(r"\bor\b", wtext, re.I) else f"({_flat(sql[where[0]:where[1]])})")
            frm = ", ".join(re.sub(r"^(?:inner\s+)?join\s+|^,\s*", "", _flat(sql[j.start:(j.on_start - 2) if j.on else j.end]).strip(), flags=re.I)
                            .strip() for j in u["joined"])
            tail = sql[min(u["cl"][k] for k in ("@order by", "@limit") if k in u["cl"]):u["end"]] \
                if any(k in u["cl"] for k in ("@order by", "@limit")) else ""
            set_end = (u["cl"]["@where"] if "@where" in u["cl"] else set_b)
            new = (f"UPDATE {_flat(sql[t.start:u['joined'][0].start])} SET {_strip_target(sql, s, set_a, set_end, names).strip()} "
                   f"FROM {frm}" + (f" WHERE {' AND '.join(conds)}" if conds else ""))
            if kind == "error" and not tail.strip():
                edits = [(0, len(sql.rstrip().rstrip(';').rstrip()), new)]
        if kind:
            out.append((F(kind, "update_join_syntax", "UPDATE ... JOIN ... SET is MySQL/MariaDB syntax",
                          "UPDATE ... JOIN ... SET MySQL/MariaDB ka syntax hai", head_ev,
                          f"{says} Each target row is still changed once. "
                          + ("" if inner else "A LEFT JOIN in an UPDATE has no direct PostgreSQL form: use UPDATE ... SET x = (SELECT ...) "
                             "for the rows without a match. "),
                          f"{says} PostgreSQL mein: UPDATE a SET col = ... FROM b WHERE a.x = b.y (SET mein table ka naam nahi)."), edits))

    # ---- U2. PostgreSQL's UPDATE a SET ... FROM b WHERE: MySQL/MariaDB want UPDATE a JOIN b ON ... SET / UPDATE a, b SET
    if u["form"] == "from":
        kind, says = _verdict("update_from", eng, "postgresql")
        edits = []
        if kind == "error" and not any(_uq(x.name.split(".")[-1]) == _uq(t.name.split(".")[-1]) for x in u["from_srcs"]):
            set_end = u["cl"]["@from"]
            new = (f"UPDATE {_flat(sql[t.start:t.end])}, {_flat(sql[u['from'][0]:u['from'][1]])} "
                   f"SET {_qualify_target(sql, s, set_a, set_end, t.alias).strip()}"
                   + (f" WHERE {_flat(sql[where[0]:where[1]])}" if where else ""))
            if not u["returning"] and not u["order"] and not u["limit"]:
                edits = [(0, len(sql.rstrip().rstrip(';').rstrip()), new)]
        if kind:
            out.append((F(kind, "update_join_syntax", "UPDATE ... SET ... FROM is PostgreSQL syntax",
                          "UPDATE ... SET ... FROM PostgreSQL ka syntax hai", head_ev,
                          f"{says} On MySQL/MariaDB name both tables before SET: UPDATE a, b SET a.col = ... WHERE a.x = b.y "
                          f"(or UPDATE a JOIN b ON a.x = b.y SET a.col = ...).",
                          f"{says} MySQL/MariaDB mein dono tables SET se pehle: UPDATE a JOIN b ON a.x = b.y SET a.col = ..."), edits))

    # ---- U3. SET o.amount = ... : PostgreSQL never lets SET name the table (42703 column "o" of relation ... does not exist)
    if u["form"] in ("plain", "from"):
        quals = [(col, cs, ce) for col, cs, ce, *_r in _assignments(sql, s, set_a, u["cl"].get("@from", set_b) if u["form"] == "from" else set_b)
                 if "." in col and _uq(col.split(".")[0]) in names]
        if quals:
            kind, says = _verdict("set_qualified", eng, "mysql")
            if u["form"] == "from" and kind is None and eng != "mysql":
                kind, says = "error", _said("set_qualified", "postgresql")
            if kind:
                edits = [(cs, ce, col.split(".", 1)[1]) for col, cs, ce in quals] if kind == "error" else []
                out.append((F(kind, "set_qualified", f"SET {quals[0][0]} = ...: PostgreSQL doesn't allow the table name in SET",
                              f"SET {quals[0][0]} = ...: PostgreSQL SET mein table ka naam nahi maanta", _short(sql[quals[0][1]:quals[0][2] + 30]),
                              f"{says} Write the bare column: SET {quals[0][0].split('.', 1)[1]} = ... (the right side may still use the alias).",
                              f"{says} Sirf column likho: SET {quals[0][0].split('.', 1)[1]} = ... (right side pe alias chal jaata hai)."), edits))

    # ---- U4. PostgreSQL FROM that reads the target table again: a second copy, not linked to the row being changed
    if u["form"] == "from":
        again = [x for x in u["from_srcs"] if _uq(x.name.split(".")[-1]) == _uq(t.name.split(".")[-1])]
        if again:
            x = again[0]
            edits = []
            rest = [y for y in u["from_srcs"] if y is not x]
            if len(u["from_srcs"]) == 2 and rest and rest[0].kind in ("join",) and rest[0].on:
                y = rest[0]
                conds = [_flat(sql[y.on_start:y.on_end])] + ([_flat(sql[where[0]:where[1]])] if where else [])
                set_end = u["cl"]["@from"]
                y_head = re.sub(r"^(?:inner\s+)?join\s+", "", _flat(sql[y.start:y.on_start - 2]), flags=re.I).strip()
                new = (f"UPDATE {t.name} {x.alias} SET {_strip_target(sql, s, set_a, set_end, names | {_uq(x.alias)}).strip()} "
                       f"FROM {y_head} WHERE {' AND '.join(conds)}")
                edits = [(0, len(sql.rstrip().rstrip(';').rstrip()), new)]
            out.append((F("wrong", "update_from_self", f"FROM reads {t.name} again: every row of {t.name} is changed",
                          f"FROM mein {t.name} dobara: {t.name} ki har row badal jaayegi", head_ev,
                          f"In PostgreSQL the table after UPDATE is not part of FROM: '{t.name} {x.alias}' in FROM is a second copy, and "
                          f"nothing ties it to the row being changed. So every row of {t.name} passes as soon as the copy has one matching "
                          f"row (the lab changed all 8 orders, not 2). Name the target once, with its alias, and keep only the other "
                          f"tables in FROM: UPDATE {t.name} {x.alias} SET ... FROM <other tables> WHERE <links> AND <filters>.",
                          f"PostgreSQL mein UPDATE wali table FROM ka hissa nahi hoti: FROM mein '{t.name} {x.alias}' ek doosri copy hai, jo "
                          f"badalti row se judi nahi. Isliye {t.name} ki har row badal jaati hai. Target ek hi baar likho: UPDATE {t.name} "
                          f"{x.alias} SET ... FROM <baaki tables> WHERE <links>."), edits))

    # ---- U5. a joined table never tied to the target row: every target row changes
    joined = u["joined"] + [x for x in u["from_srcs"] if _uq(x.name.split(".")[-1]) != _uq(t.name.split(".")[-1])]
    if joined and not any(o[0].topic == "update_from_self" for o in out):
        txt = " ".join([s[j.on_start:j.on_end] for j in u["joined"] + u["from_srcs"] if j.on] + [wtext])
        linked = _linked(txt, names, joined)
        for j in joined:
            if not linked.get(_uq(j.alias)) and j.name:
                out.append((F("wrong", "change_unlinked", f"{j.name} is never tied to the {t.name} row being changed",
                              f"{j.name} badalti {t.name} row se kabhi juda nahi", head_ev,
                              f"No condition links {j.alias} to {t.alias}, so the filter on {j.alias} doesn't pick {t.name} rows: when "
                              f"any {j.name} row matches, every {t.name} row is changed (the lab changed all 8). Add the link"
                              f"{_link_hint(t, j)}, and preview with SELECT first.",
                              f"{j.alias} ko {t.alias} se jodne wali koi condition nahi, isliye {j.alias} ka filter {t.name} ki rows nahi "
                              f"chunta: koi bhi {j.name} row match ho to {t.name} ki har row badal jaati hai. Link jodo{_link_hint(t, j)}."), []))
                break

    # ---- U6. one target row, several joined rows: changed once, with one of them
    for j in joined:
        if not j.name:
            continue
        txt = (s[j.on_start:j.on_end] if j.on else wtext)
        many = _child_link(txt, t, j)
        if not many:
            continue
        used = [(col, cs, ce, e, es, ee) for col, cs, ce, e, es, ee in _assignments(sql, s, set_a, u["cl"].get("@from", u["cl"].get("@where", set_b))
                                                                                      if u["form"] == "from" else u["cl"].get("@where", set_b))
                if _uq(j.alias) in _refs(s[es:ee]) and not re.search(r"\(\s*select\b", s[es:ee], re.I)]
        if not used:
            continue
        col, cs, ce, e, es, ee = used[0]
        bare = col.split(".")[-1]
        adds = re.search(rf"(?:^|[^\w.]){re.escape(_uq(bare))}\b", _uq(s[es:ee])) is not None or \
            re.search(rf"\b{re.escape(_uq(t.alias))}\.{re.escape(_uq(bare))}\b", _uq(s[es:ee])) is not None
        child_cols = sorted({m.group(1) for m in re.finditer(rf"\b{re.escape(j.alias)}\.({NAME})", sql[es:ee])})
        edits = []
        if adds and len(child_cols) == 1 and j.kind in ("join", "comma", "from"):
            fkcol = many
            cc = child_cols[0]
            if j.on:
                ss, se = j.start, j.on_start - 2
                pre = re.match(r"((?:inner\s+)?join|,)?\s*", sql[ss:se], re.I).group(0)
            else:
                ss, se = j.start, j.end
                pre = re.match(r"(,)?\s*", sql[ss:se]).group(0)
            derived = f"(SELECT {fkcol}, SUM({cc}) AS {cc} FROM {j.name} GROUP BY {fkcol}) {j.alias}"
            src_end = (j.on_start - 2) if j.on else j.end
            while src_end > ss and sql[src_end - 1].isspace():
                src_end -= 1
            edits = [(ss + len(pre), src_end, derived)]
        verb = "adds only one of them" if adds else "keeps one of them, and which one is not defined"
        out.append((F("wrong" if adds else "check", "join_change_many",
                      f"Each {t.name} row can match several {j.name} rows, but it is changed only once",
                      f"Ek {t.name} row ke kai {j.name} rows ho sakte hain, par wo ek hi baar badalti hai", _short(sql[cs:ee]),
                      f"An UPDATE changes each target row at most once, on every engine. {j.name} is joined on its link column "
                      f"({many}), so a {t.name} row can have several matching rows, and SET {col} = {_flat(e)} {verb} "
                      f"(the lab: an order with payments 40000 and 39000 got 40000). "
                      + ("Total the child rows first: join (SELECT link, SUM(x) ... GROUP BY link) instead of the table."
                         if adds else "If one value per row is meant, say which: pick it with a subquery (MAX, or ORDER BY ... LIMIT 1)."),
                      f"UPDATE har target row ko ek hi baar badalta hai. Ek {t.name} row ke kai {j.name} rows ho sakte hain, aur SET "
                      f"{col} = {_flat(e)} unme se " + ("sirf ek jodta hai. Pehle child rows ka total banao: (SELECT link, SUM(x) ... "
                                                        "GROUP BY link) join karo." if adds else
                                                        "ek rakhta hai, kaunsa tay nahi. Batao kaunsa: MAX ya ORDER BY ... LIMIT 1.")),
                    edits))
        break

    # ---- U7. SET col = (SELECT ... correlated) with no WHERE: NULL for every row without a match
    if not where and u["form"] == "plain":
        for col, cs, ce, e, es, ee in _assignments(sql, s, set_a, set_b):
            m = re.fullmatch(r"\(\s*select\s+([\s\S]+)\)", s[es:ee].strip(), re.I)
            if not m:
                continue
            inner_items = s[es:ee]
            if re.match(r"\(\s*select\s+count\s*\(", inner_items, re.I):
                continue                                   # COUNT of nothing is 0, not NULL
            if not _refs(inner_items) & names and not re.search(rf"\b{re.escape(t.name)}\.", inner_items, re.I):
                continue                                   # not tied to the row: one value for every row
            fm = re.search(r"\bfrom\b([\s\S]*)\)\s*$", sql[es:ee], re.I)
            if not fm:
                continue
            body = fm.group(1).strip()
            body = re.sub(r"\border\s+by\b[\s\S]*$|\blimit\b[\s\S]*$", "", body, flags=re.I).strip()
            new_where = f" WHERE EXISTS (SELECT 1 FROM {body})"
            out.append((F("check", "set_subquery_null", f"SET {col} = (SELECT ...) gives NULL to rows with no match",
                          f"SET {col} = (SELECT ...) bina match wali rows ko NULL deta hai", _short(sql[cs:ee]),
                          f"With no WHERE, every {t.name} row is changed; a row the subquery finds nothing for gets NULL "
                          f"(a total like SUM/MAX of no rows is NULL too), so its old value is lost. To change only the rows that have a "
                          f"match, add WHERE EXISTS (the same subquery); to store 0 instead, wrap it in COALESCE(..., 0).",
                          f"WHERE ke bina {t.name} ki har row badalti hai; jis row ke liye subquery ko kuch nahi milta use NULL milta hai "
                          f"aur purani value chali jaati hai. Sirf match wali rows badalni hon to WHERE EXISTS (wahi subquery) lagao; "
                          f"0 chahiye to COALESCE(..., 0)."),
                        [(u["end"], u["end"], new_where)]))
            break

    # ---- U8. UPDATE ... LIMIT: PostgreSQL has no LIMIT on UPDATE
    out += _limit_rule(sql, s, eng, u, t, "UPDATE", F)
    return out


def _linked(txt: str, names: set, joined) -> dict:
    """{alias: True} for each joined table that some equality ties (directly or through another joined table) to the target."""
    pairs = []
    for m in re.finditer(rf"({NAME})\.({NAME})\s*=\s*({NAME})\.({NAME})", txt):
        pairs.append((_uq(m.group(1)), _uq(m.group(3))))
    have = set(names)
    changed = True
    while changed:
        changed = False
        for a, b in pairs:
            if a in have and b not in have:
                have.add(b)
                changed = True
            elif b in have and a not in have:
                have.add(a)
                changed = True
    return {_uq(j.alias): _uq(j.alias) in have or bool(j.using) for j in joined}


def _child_link(txt: str, t, j) -> str | None:
    """j.<fk> = t.id (the target's key on one side, a non-key column of j on the other): the link column of j, else None."""
    ta, ja = _uq(t.alias), _uq(j.alias)
    for m in re.finditer(rf"({NAME})\.({NAME})\s*=\s*({NAME})\.({NAME})", txt):
        a1, c1, a2, c2 = _uq(m.group(1)), _uq(m.group(2)), _uq(m.group(3)), _uq(m.group(4))
        if a1 == ja and a2 == ta:
            a1, c1, a2, c2 = a2, c2, a1, c1
        if a1 == ta and a2 == ja and _is_key(t.name, c1) and not _is_key(j.name, c2):
            return c2
    return None


def _is_key(table: str, col: str) -> bool:
    """id, or <table in the singular>_id (customers -> customer_id; app_users -> user_id)."""
    C = _C()
    c = col.lower().replace("_", "")
    name = _uq(table.split(".")[-1])
    names = {name, name.rsplit("_", 1)[-1]}
    return c == "id" or any(c in {x.replace("_", "") + "id" for x in C._singulars(n)} for n in names)


def _limit_rule(sql, s, eng, st, t, verb, F) -> list:
    if not st.get("limit"):
        return []
    kind, says = _verdict("change_limit", eng, "mysql")
    if not kind:
        return []
    edits = []
    lim = _flat(sql[st["limit"][0]:st["limit"][1]])
    if kind == "error" and re.fullmatch(r"\d+", lim):
        cl = st["cl"]
        where = st["where"]
        cond = _flat(sql[where[0]:where[1]]) if where else ""
        order = _flat(sql[st["order"][0]:st["order"][1]]) if st.get("order") else ""
        cut = min(v for k, v in cl.items() if k.startswith("@") and k in ("@where", "@order by", "@limit"))
        head = sql[:cut].rstrip()
        sub = f"SELECT ctid FROM {t.name}" + (f" WHERE {cond}" if cond else "") + (f" ORDER BY {order}" if order else "") + f" LIMIT {lim}"
        new = f"{head} WHERE ctid IN ({sub})"
        if verb == "UPDATE" and st["form"] == "plain" or verb == "DELETE" and st["form"] == "plain":
            edits = [(0, len(sql.rstrip().rstrip(';').rstrip()), new)]
    return [(F(kind, "change_limit", f"{verb} ... LIMIT: PostgreSQL has no LIMIT on {verb}",
               f"{verb} ... LIMIT: PostgreSQL mein {verb} pe LIMIT nahi hota", _short(sql[:st["end"]]),
               f"{says} In PostgreSQL pick the rows in a subquery: {verb} ... WHERE ctid IN (SELECT ctid FROM t WHERE ... ORDER BY ... "
               f"LIMIT n) (ctid is the row's physical address; the table's key works too). Without ORDER BY, which rows are taken is up "
               f"to the engine, on every database: fine for batches, wrong when you mean the oldest or newest.",
               f"{says} PostgreSQL mein rows subquery se chuno: {verb} ... WHERE ctid IN (SELECT ctid FROM t WHERE ... ORDER BY ... "
               f"LIMIT n). ORDER BY ke bina kaunsi rows aayengi engine pe hai: batches ke liye theek, 'sabse purani' ke liye galat."),
             edits)]


def _delete(sql: str, s: str, intent: str, F) -> list:
    out = []
    d = read_delete(sql, s)
    if d is None:
        return out
    eng = _engine(sql)
    srcs = d["srcs"]
    t = srcs[0]
    where = d["where"]
    wtext = s[where[0]:where[1]] if where else ""
    ev = _short(sql[:d["end"]])

    # ---- D1. DELETE FROM a JOIN b ...: no engine knows which table to delete from
    if d["form"] == "join":
        mysql_form = f"DELETE {t.alias} FROM {_flat(sql[d['section'][0]:d['end']])}"
        edits = [(0, len(sql.rstrip().rstrip(';').rstrip()), mysql_form)] if eng != "postgresql" else []
        if eng == "postgresql":
            pg = _pg_delete(sql, s, d, t)
            edits = [(0, len(sql.rstrip().rstrip(';').rstrip()), pg)] if pg else []
        out.append((F("error", "delete_no_target", "DELETE FROM a JOIN b: say which table the rows are deleted from",
                      "DELETE FROM a JOIN b: batao rows kis table se hatani hain", ev,
                      "Both engines refuse it (MariaDB 1064, PostgreSQL 'syntax error at or near JOIN'). MySQL/MariaDB name the table "
                      f"before FROM: DELETE {t.alias} FROM {t.name} {t.alias} JOIN ...; PostgreSQL uses DELETE FROM {t.name} {t.alias} "
                      f"USING <other table> WHERE <link> AND <filter>.",
                      f"Dono engines mana karte hain. MySQL/MariaDB: DELETE {t.alias} FROM {t.name} {t.alias} JOIN ...; PostgreSQL: "
                      f"DELETE FROM {t.name} {t.alias} USING <doosri table> WHERE <link>."), edits))
        return out

    # ---- D2. MySQL's DELETE a FROM a JOIN b / PostgreSQL's DELETE FROM a USING b
    if d["form"] == "multi" and (len(srcs) > 1 or d["using"]):
        kind, says = _verdict("delete_join", eng, "mysql")
        edits = []
        if kind == "error" and len(d["targets"]) == 1 and not d["using"]:
            pg = _pg_delete(sql, s, d, next((x for x in srcs if _uq(x.alias) == d["targets"][0]), t))
            edits = [(0, len(sql.rstrip().rstrip(';').rstrip()), pg)] if pg else []
        if kind:
            out.append((F(kind, "delete_join_syntax", "DELETE a FROM a JOIN b is MySQL/MariaDB syntax",
                          "DELETE a FROM a JOIN b MySQL/MariaDB ka syntax hai", ev,
                          f"{says} PostgreSQL: DELETE FROM a USING b WHERE a.x = b.y AND ...",
                          f"{says} PostgreSQL: DELETE FROM a USING b WHERE a.x = b.y AND ..."), edits))
    if d["form"] == "using":
        kind, says = _verdict("delete_using", eng, "postgresql")
        edits = []
        if kind == "error":
            new = (f"DELETE {t.alias} FROM {_flat(sql[d['section'][0]:d['section'][1]])}, {_flat(sql[d['using'][0]:d['using'][1]])}"
                   + (f" WHERE {_flat(sql[where[0]:where[1]])}" if where else ""))
            if not d["order"] and not d["limit"] and not d["cl"].get("returning"):
                edits = [(0, len(sql.rstrip().rstrip(';').rstrip()), new)]
        if kind:
            out.append((F(kind, "delete_join_syntax", "DELETE FROM a USING b is PostgreSQL syntax",
                          "DELETE FROM a USING b PostgreSQL ka syntax hai", ev,
                          f"{says} MySQL/MariaDB: DELETE a FROM a JOIN b ON a.x = b.y WHERE ... (or DELETE a FROM a, b WHERE ...).",
                          f"{says} MySQL/MariaDB: DELETE a FROM a JOIN b ON a.x = b.y WHERE ..."), edits))

    # ---- D3. deleting duplicates through a self-join: which copy stays?
    tabs = [(x, s[x.on_start:x.on_end] if x.on else "") for x in srcs + d["using_srcs"]]
    same = [x for x, _o in tabs if x.name and _uq(x.name) == _uq(t.name)]
    if len(same) >= 2:
        a1, a2 = _uq(same[0].alias), _uq(same[1].alias)
        txt = " ".join(o for _x, o in tabs) + " " + wtext
        eqs = [(m.group(2), m.group(4)) for m in re.finditer(rf"({NAME})\.({NAME})\s*=\s*({NAME})\.({NAME})", txt)
               if {_uq(m.group(1)), _uq(m.group(3))} == {a1, a2}]
        order_cmp = re.search(rf"\b(?:{re.escape(a1)}|{re.escape(a2)})\.({NAME})\s*(<>|!=|<|>|<=|>=)\s*(?:{re.escape(a1)}|{re.escape(a2)})\.\1\b", txt, re.I)
        if eqs and not any(_is_key(t.name, _uq(c)) for c, _c2 in eqs):
            if not order_cmp or order_cmp.group(2) in ("<>", "!="):
                k = order_cmp.group(1) if order_cmp else "id"
                deleted = _uq(d["targets"][0]) if d["targets"] else a1
                keep = a2 if deleted == a1 else a1
                fix_cond = f"{deleted}.{k} > {keep}.{k}"
                edits = []
                if order_cmp:
                    k0 = s.find(order_cmp.group(0))
                    edits = [(k0, k0 + len(order_cmp.group(0)), fix_cond)] if k0 >= 0 else []
                elif where:
                    w1 = _trim(sql, where[1])
                    edits = [(w1, w1, f" AND {fix_cond}")] if not re.search(r"\bor\b", wtext, re.I) else \
                        [(where[0] + len(wtext) - len(wtext.lstrip()), where[0] + len(wtext) - len(wtext.lstrip()), "("),
                         (w1, w1, f") AND {fix_cond}")]
                else:
                    on_src = next((x for x, o in tabs if o), None)
                    if on_src is not None:
                        e_ = _trim(sql, on_src.on_end)
                        edits = [(e_, e_, f" AND {fix_cond}")]
                    elif d["using"]:
                        e_ = _trim(sql, d["end"])
                        edits = [(e_, e_, f" WHERE {fix_cond}")]
                what = ("both copies of every pair" if order_cmp else "every row that has a match, and each row matches itself, so the "
                        "whole table")
                out.append((F("wrong", "dedupe_self", "This self-join deletes " + ("both copies" if order_cmp else "every copy, not only the extras"),
                              "Ye self-join " + ("dono copies" if order_cmp else "har copy") + " hata dega, sirf extra nahi", ev,
                              f"Joining {t.name} to itself on {', '.join(c for c, _x in eqs)} matches each row with every row of the same "
                              f"value{'' if order_cmp else ', itself included'}, so it deletes {what} (the lab: 8 of 8 customers gone). Say "
                              f"which copy stays: {fix_cond} deletes every copy except the one with the lowest {k}. Preview it first: "
                              f"SELECT {deleted}.* with the same FROM and WHERE.",
                              f"{t.name} ko khud se {', '.join(c for c, _x in eqs)} pe jodne se har row apni jaisi har row se (khud se bhi) "
                              f"match hoti hai, isliye ye {('har pair ki dono copies' if order_cmp else 'poori table')} hata deta hai. Batao "
                              f"kaunsi copy rahe: {fix_cond} sabse chhoti {k} wali ko chhod ke baaki hatata hai. Pehle SELECT se dekho."),
                            edits))

    # ---- D4. DELETE ... LIMIT: PostgreSQL has no LIMIT on DELETE
    out += _limit_rule(sql, s, eng, d, t, "DELETE", F)
    return out


def _trim(sql: str, pos: int) -> int:
    while pos > 0 and sql[pos - 1].isspace():
        pos -= 1
    return pos


def _pg_delete(sql: str, s: str, d, t) -> str | None:
    """DELETE t FROM t JOIN b ON cond WHERE w -> DELETE FROM t USING b WHERE cond AND w (inner joins only)."""
    srcs = d["srcs"]
    if srcs[0] is not t or any(x.kind not in ("join", "comma") for x in srcs[1:]):
        return None
    using, conds = [], []
    for x in srcs[1:]:
        if x.on:
            using.append(re.sub(r"^(?:inner\s+)?join\s+|^,\s*", "", _flat(sql[x.start:x.on_start - 2]), flags=re.I).strip())
            conds.append(_flat(sql[x.on_start:x.on_end]))
        else:
            using.append(re.sub(r"^(?:inner\s+)?join\s+|^,\s*", "", _flat(sql[x.start:x.end]), flags=re.I).strip())
    if d["where"]:
        w = _flat(sql[d["where"][0]:d["where"][1]])
        conds.append(f"({w})" if re.search(r"\bor\b", w, re.I) else w)
    if d["order"] or d["limit"]:
        return None
    return f"DELETE FROM {_flat(sql[t.start:t.end])} USING {', '.join(using)}" + (f" WHERE {' AND '.join(conds)}" if conds else "")


# ------------------------------------------------------------------------------------------------ INSERT / REPLACE / upserts
def _insert(sql: str, s: str, intent: str, F) -> list:
    out = []
    ins = read_insert(sql, s)
    if ins is None:
        return out
    eng = _engine(sql)
    ev = _short(sql[:ins["end"]])
    cols = ins["cols"]

    # ---- I1. how many values for how many columns
    if cols:
        counts = [len(r) for r in ins["rows"]]
        items = _select_items(sql, s, *ins["select"]) if ins["select"] else None
        if items is not None and not any(re.fullmatch(r"(?:\w+\.)?\*", it[2].strip()) for it in items):
            counts.append(len(items))
        bad = [n for n in counts if n != len(cols)]
        if bad:
            out.append((F("error", "insert_count", f"{len(cols)} columns listed, {bad[0]} values given",
                          f"{len(cols)} columns likhe, {bad[0]} values di", ev,
                          "Each listed column takes the value in the same position, so the counts must match: MySQL/MariaDB stop with "
                          "error 1136 (Column count doesn't match value count), PostgreSQL with 'INSERT has more target columns than "
                          "expressions' (or more expressions than target columns). Add the missing value, or drop the column from the list.",
                          "Har column ko usi position ki value milti hai, isliye ginti barabar honi chahiye: MySQL/MariaDB error 1136, "
                          "PostgreSQL 'INSERT has more target columns than expressions'. Missing value jodo, ya column list se hatao."), []))
            return out

    # ---- I2. INSERT ... SELECT fills by position, not by name
    if cols and ins["select"]:
        items = _select_items(sql, s, *ins["select"])
        if items and len(items) == len(cols):
            want = [c for c, _t in cols]
            got = [it[3] for it in items]
            common = [g for g in got if g and g in want]
            moved = [k for k, g in enumerate(got) if g and g in want and want[k] != g]
            places = [k for k, g in enumerate(got) if g and g in want]
            swapped = {got[k] for k in places} == {want[k] for k in places}     # the same names, only in other places
            if len(common) >= 2 and moved and len(set(common)) == len(common) and swapped:
                # reorder the SELECT items so each lands in the column of its own name; items without a name keep their slot
                slot = {}
                free = [k for k in range(len(want)) if want[k] not in common]
                rest = [it for it in items if not (it[3] and it[3] in want)]
                for it in items:
                    if it[3] and it[3] in want:
                        slot[want.index(it[3])] = it[2]
                for k, it in zip(free, rest):
                    slot[k] = it[2]
                new = ", ".join(slot[k] for k in range(len(want)))
                a, b = items[0][0], items[-1][1]
                names = ", ".join(f"{items[k][2]} -> {cols[k][1]}" for k in moved[:3])
                out.append((F("wrong", "insert_order", "INSERT ... SELECT fills columns by position, not by name",
                              "INSERT ... SELECT columns ko position se bharta hai, naam se nahi", ev,
                              f"The SELECT lists the same columns in another order, so the values land in the wrong columns ({names}), "
                              f"with no error whenever the types allow it (the lab stored a country in name). List the SELECT items in the "
                              f"order of the INSERT's column list.",
                              f"SELECT wahi columns alag order mein deta hai, isliye values galat columns mein jaati hain ({names}), bina "
                              f"error. SELECT ke items INSERT ki column list ke order mein likho."), [(a, b, new)]))

    # ---- I3. no column list: depends on the table's column order today
    if cols is None and ins["kind"] in ("values", "select"):
        out.append((F("check", "insert_no_columns", f"INSERT INTO {ins['table_text']} without a column list",
                      f"INSERT INTO {ins['table_text']} bina column list ke", ev,
                      "Without a column list the values go into the table's columns in their current order, all of them: one value "
                      "too few is an error on MariaDB (1136), while PostgreSQL fills the missing last columns with their defaults; and "
                      "the day a column is added or moved, the same INSERT breaks or fills the wrong columns. Name the columns: "
                      "INSERT INTO t (a, b, c) VALUES (...).",
                      "Column list ke bina values table ke columns mein unke abhi ke order mein jaati hain: ek value kam ho to MariaDB "
                      "error 1136, PostgreSQL baaki columns ko default de deta hai; aur column judte hi wahi INSERT toot jaata hai ya "
                      "galat column bharta hai. Columns ke naam likho."), []))

    # ---- I4. MySQL's ON DUPLICATE KEY UPDATE / PostgreSQL's ON CONFLICT
    if ins["dup"]:
        kind, says = _verdict("on_duplicate_key", eng, "mysql")
        edits = []
        if kind == "error":
            new = _to_conflict(sql, s, ins)
            if new:
                edits = [(ins["dup_at"], _trim(sql, ins["end"]), new)]
        if kind:
            out.append((F(kind, "upsert_syntax", "ON DUPLICATE KEY UPDATE is MySQL/MariaDB syntax",
                          "ON DUPLICATE KEY UPDATE MySQL/MariaDB ka syntax hai", ev,
                          f"{says} PostgreSQL: ON CONFLICT (key column) DO UPDATE SET col = EXCLUDED.col, where EXCLUDED is the row "
                          f"that was being inserted and the key column must have a PRIMARY KEY or UNIQUE constraint.",
                          f"{says} PostgreSQL: ON CONFLICT (key column) DO UPDATE SET col = EXCLUDED.col (EXCLUDED = jo row insert ho "
                          f"rahi thi; key column pe PRIMARY KEY ya UNIQUE hona chahiye)."), edits))
    if ins["conflict"]:
        ctext = s[ins["conflict"][0]:ins["conflict"][1]]
        kind, says = _verdict("on_conflict", eng, "postgresql")
        edits = []
        if kind == "error":
            new = _to_dup(sql, s, ins)
            if new:
                edits = [(ins["conflict_at"], _trim(sql, ins["end"]), new)]
        if kind:
            out.append((F(kind, "upsert_syntax", "ON CONFLICT is PostgreSQL syntax", "ON CONFLICT PostgreSQL ka syntax hai", ev,
                          f"{says} MySQL/MariaDB: ON DUPLICATE KEY UPDATE col = VALUES(col) (MySQL 8.0.19+ also: INSERT ... AS new "
                          f"... UPDATE col = new.col); it reacts to any PRIMARY KEY or UNIQUE clash, there is no target to name.",
                          f"{says} MySQL/MariaDB: ON DUPLICATE KEY UPDATE col = VALUES(col); ye kisi bhi PRIMARY KEY/UNIQUE clash pe "
                          f"chalta hai."), edits))
        if kind != "error":
            # ---- I5. DO UPDATE without a conflict target
            if re.match(r"\s*do\s+update\b", ctext, re.I):
                key = _key_guess(cols, ins["table"])
                k = ins["conflict"][0]
                out.append((F("error", "conflict_no_target", "ON CONFLICT DO UPDATE needs the key column: ON CONFLICT (col)",
                              "ON CONFLICT DO UPDATE ko key column chahiye: ON CONFLICT (col)", ev,
                              "PostgreSQL refuses it ('ON CONFLICT DO UPDATE requires inference specification or constraint name'): "
                              "it must know which unique key's clash turns the insert into an update. Name the PRIMARY KEY or UNIQUE "
                              f"column(s): ON CONFLICT ({key or 'key_column'}) DO UPDATE ... (only DO NOTHING may leave it out).",
                              "PostgreSQL mana karta hai: use pata hona chahiye kaunsi unique key ka clash update banega. ON CONFLICT "
                              f"({key or 'key_column'}) DO UPDATE ... likho."), [(k, k, f" ({key})")] if key else []))
            # ---- I6. DO UPDATE SET x = x + EXCLUDED.x: the bare x is ambiguous
            dm = re.search(r"\bdo\s+update\s+set\b", ctext, re.I)
            if dm:
                a = ins["conflict"][0] + dm.end()
                wm = re.search(r"\bwhere\b|\breturning\b", s[a:ins["conflict"][1]], re.I)
                b = a + wm.start() if wm else ins["conflict"][1]
                tname = ins["alias"] or ins["table_text"]
                edits, bare = [], []
                for col, cs, ce, e, es, ee in _assignments(sql, s, a, b):
                    for m in re.finditer(rf"(?<![\w.$`\"])({NAME})(?![\w$]*\s*[.(])", s[es:ee]):
                        w = _uq(m.group(1))
                        if w in _C().KW or w in ("excluded", "null", "true", "false") or re.match(r"\d", w) or \
                                (cols and w not in {c for c, _t in cols} and w != _uq(col.split(".")[-1])):
                            continue
                        bare.append(m.group(1))
                        edits.append((es + m.start(1), es + m.end(1), f"{tname}.{m.group(1)}"))
                if bare:
                    out.append((F("error", "conflict_ambiguous", f"'{bare[0]}' in DO UPDATE SET is ambiguous",
                                  f"DO UPDATE SET mein '{bare[0]}' ambiguous hai", ev,
                                  f"Inside DO UPDATE two rows are in scope: the row already in the table and EXCLUDED (the one being "
                                  f"inserted), so PostgreSQL refuses a bare column on the right side (42702 column reference \"{bare[0]}\" "
                                  f"is ambiguous). Say which: {tname}.{bare[0]} for the stored value, EXCLUDED.{bare[0]} for the new one.",
                                  f"DO UPDATE ke andar do rows hain: table wali aur EXCLUDED (nayi). Isliye bina naam ka column PostgreSQL "
                                  f"mana karta hai (42702). Batao kaunsa: {tname}.{bare[0]} purani value, EXCLUDED.{bare[0]} nayi."), edits))

    # ---- I7. INSERT IGNORE / REPLACE INTO: MySQL-only, and each hides more than it seems
    if ins["ignore"]:
        kind, says = _verdict("insert_ignore", eng, "mysql")
        kind = kind or "check"
        key = _key_guess(cols, ins["table"])
        if kind == "error":
            k = re.search(r"\bignore\s+", s[:ins["end"]], re.I)
            edits = [(k.start(), k.end(), "")] + [(_trim(sql, ins["end"]), _trim(sql, ins["end"]), " ON CONFLICT DO NOTHING")]
        else:
            edits = []
        out.append((F(kind, "insert_ignore", "INSERT IGNORE skips more than duplicate rows",
                      "INSERT IGNORE duplicate ke alawa bhi bahut kuch chhod deta hai", ev,
                      (f"{says} " if kind == "error" else "") +
                      "IGNORE turns every error into a warning: a duplicate key row is skipped, but so is a row breaking a foreign key, and "
                      "a value too long or of the wrong type is cut or changed to fit, all without an error. To skip only duplicates: "
                      + (f"ON DUPLICATE KEY UPDATE {key} = {key} (MySQL/MariaDB) or " if key else "")
                      + "ON CONFLICT DO NOTHING (PostgreSQL).",
                      "IGNORE har error ko warning bana deta hai: duplicate row chhoot-ti hai, par foreign key todne wali row bhi, aur "
                      "lambi/galat type ki value kaat ke fit kar di jaati hai, bina error. Sirf duplicate chhodne hain to "
                      + (f"ON DUPLICATE KEY UPDATE {key} = {key} (MySQL) ya " if key else "") + "ON CONFLICT DO NOTHING (PostgreSQL)."), edits))
    if ins["verb"] == "replace":
        kind, says = _verdict("replace_into", eng, "mysql")
        kind = kind or "check"
        listed = [c for c, _t in cols] if cols else []
        edits = []
        key = _key_guess(cols, ins["table"])
        if key and listed and ins["rows"] and not ins["dup"]:
            upd = [c for c in listed if c != key]
            k = re.match(r"\s*replace\b", s, re.I)
            if kind == "error":
                tail = f" ON CONFLICT ({key}) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in upd)
            else:
                tail = " ON DUPLICATE KEY UPDATE " + ", ".join(f"{c} = VALUES({c})" for c in upd)
            if upd:
                edits = [(k.start(), k.end(), sql[k.start():k.end()].replace("REPLACE", "INSERT").replace("replace", "insert")
                          if "REPLACE" in sql[k.start():k.end()] or "replace" in sql[k.start():k.end()] else "INSERT"),
                         (_trim(sql, ins["end"]), _trim(sql, ins["end"]), tail)]
        out.append((F(kind, "replace_into", "REPLACE deletes the old row, then inserts a new one",
                      "REPLACE purani row hata ke nayi daalta hai", ev,
                      (f"{says} " if kind == "error" else "") +
                      "When the key exists, REPLACE is a DELETE plus an INSERT: every column not listed goes back to its default (the "
                      "lab's product lost its price and stock), ON DELETE CASCADE removes the row's children, delete triggers fire, and an "
                      "auto-increment key gets a new number. To change only the listed columns, use INSERT ... ON DUPLICATE KEY UPDATE "
                      "(MySQL/MariaDB) or ON CONFLICT (key) DO UPDATE (PostgreSQL).",
                      "Key pehle se ho to REPLACE = DELETE + INSERT: jo columns list mein nahi wo default ho jaate hain, ON DELETE CASCADE "
                      "children hata deta hai, delete triggers chalte hain, auto-increment ko naya number milta hai. Sirf listed columns "
                      "badalne hain to INSERT ... ON DUPLICATE KEY UPDATE (MySQL) ya ON CONFLICT (key) DO UPDATE (PostgreSQL)."), edits))
    return out


def _key_guess(cols, table: str = "") -> str | None:
    """The column of the list that is the table's key by its name (id, or customer_id in customers); None when none is."""
    if not cols:
        return None
    names = [c for c, _t in cols]
    if "id" in names:
        return "id"
    texts = {c: tx for c, tx in cols}
    return next((texts[c] for c in names if table and _is_key(table, c)), None)


def _to_conflict(sql: str, s: str, ins) -> str | None:
    """ON DUPLICATE KEY UPDATE a = VALUES(a), b = b + VALUES(b) -> ON CONFLICT (id) DO UPDATE SET a = EXCLUDED.a, b = t.b + EXCLUDED.b"""
    key = _key_guess(ins["cols"], ins["table"])
    if not key:
        return None
    tname = ins["alias"] or ins["table_text"]
    a, b = ins["dup"]
    parts = []
    for col, cs, ce, e, es, ee in _assignments(sql, s, a, b):
        expr = re.sub(rf"\bvalues?\s*\(\s*({NAME})\s*\)", r"EXCLUDED.\1", e, flags=re.I)
        expr = re.sub(rf"(?<![\w.$`\"])({NAME})(?![\w$]*\s*[.(])",
                      lambda m: m.group(1) if _uq(m.group(1)) in _C().KW or _uq(m.group(1)) in ("excluded", "null", "true", "false")
                      or (ins["cols"] and _uq(m.group(1)) not in {c for c, _t in ins["cols"]} and _uq(m.group(1)) != _uq(col))
                      else f"{tname}.{m.group(1)}", expr)
        parts.append(f"{col.split('.')[-1]} = {expr}")
    if not parts:
        return None
    return f"ON CONFLICT ({key}) DO UPDATE SET " + ", ".join(parts)


def _to_dup(sql: str, s: str, ins) -> str | None:
    """ON CONFLICT (k) DO UPDATE SET a = EXCLUDED.a, b = t.b + EXCLUDED.b -> ON DUPLICATE KEY UPDATE a = VALUES(a), b = b + VALUES(b)"""
    ctext = s[ins["conflict"][0]:ins["conflict"][1]]
    if re.search(r"\bdo\s+nothing\b", ctext, re.I):
        key = _key_guess(ins["cols"], ins["table"]) or (ins["cols"][0][1] if ins["cols"] else None)
        return f"ON DUPLICATE KEY UPDATE {key} = {key}" if key else None
    dm = re.search(r"\bdo\s+update\s+set\b", ctext, re.I)
    if not dm or re.search(r"\bwhere\b", ctext[dm.end():], re.I):
        return None
    a = ins["conflict"][0] + dm.end()
    tname = _uq(ins["alias"] or ins["table"])
    parts = []
    for col, cs, ce, e, es, ee in _assignments(sql, s, a, ins["conflict"][1]):
        expr = re.sub(rf"\bexcluded\.({NAME})", r"VALUES(\1)", e, flags=re.I)
        expr = re.sub(rf"\b{re.escape(tname)}\.({NAME})", r"\1", expr, flags=re.I)
        parts.append(f"{col.split('.')[-1]} = {expr}")
    return ("ON DUPLICATE KEY UPDATE " + ", ".join(parts)) if parts else None


# ------------------------------------------------------------------------------------------------ preview before change
def preview(sql: str) -> str | None:
    """The SELECT that shows the rows an UPDATE / DELETE would change (and, for UPDATE, the new values), read-only."""
    C = _C()
    s = C._mask(sql)
    low = s.lower().lstrip()
    try:
        if low.startswith("update"):
            u = read_update(sql, s)
            if u is None:
                return None
            t = u["target"]
            set_end = u["cl"].get("@from", u["cl"].get("@where", u["cl"].get("@order by", u["cl"].get("@limit", u["set"][1]))))
            news = []
            for col, cs, ce, e, es, ee in _assignments(sql, s, u["set"][0], set_end):
                news.append(f"{_flat(e)} AS new_{_uq(col.split('.')[-1])}")
            frm = _flat(sql[u["section"][0]:u["section"][1]])
            if u["form"] == "from":
                frm = f"{frm}, {_flat(sql[u['from'][0]:u['from'][1]])}" if not any(
                    _uq(x.name.split('.')[-1]) == _uq(t.name.split('.')[-1]) for x in u["from_srcs"]) else frm
            tail = _flat(sql[u["cl"]["@where"]:u["end"]]) if "@where" in u["cl"] else \
                _flat(sql[min(v for k, v in u["cl"].items() if k in ("@order by", "@limit")):u["end"]]) if any(
                    k in u["cl"] for k in ("@order by", "@limit")) else ""
            tail = re.sub(r"\breturning\b[\s\S]*$", "", tail, flags=re.I).strip()
            return f"SELECT {t.alias}.*{', ' + ', '.join(news) if news else ''} FROM {frm}" + (f" {tail}" if tail else "")
        if low.startswith("delete"):
            d = read_delete(sql, s)
            if d is None:
                return None
            t = d["srcs"][0]
            who = d["targets"][0] if d["targets"] else t.alias
            frm = _flat(sql[d["section"][0]:d["section"][1]])
            if d["using"]:
                frm += ", " + _flat(sql[d["using"][0]:d["using"][1]])
            rest = [k for k in ("@where", "@order by", "@limit") if k in d["cl"]]
            tail = _flat(sql[min(d["cl"][k] for k in rest):d["end"]]) if rest else ""
            tail = re.sub(r"\breturning\b[\s\S]*$", "", tail, flags=re.I).strip()
            return f"SELECT {who}.* FROM {frm}" + (f" {tail}" if tail else "")
    except Exception:  # noqa: BLE001 - the preview is an extra
        return None
    return None
