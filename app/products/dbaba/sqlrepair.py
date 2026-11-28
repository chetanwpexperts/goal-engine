"""Night X1: a statement the database can't even read, put right so it CAN be read, before any deeper check.

Two families, both refused by every database the lab runs (MariaDB, PostgreSQL; SQLite has one exception, from its dialect
file), and both with only one possible meaning, so the repair is sure ('error'), never a guess:

  clause order   A statement's clauses have one fixed order (SELECT: FROM, JOINs, WHERE, GROUP BY, HAVING, ORDER BY, LIMIT;
                 UPDATE: SET, WHERE, ORDER BY, LIMIT; DELETE: FROM, WHERE, ORDER BY, LIMIT). A clause written out of place is
                 a syntax error ('near "WHERE"'). Each clause keeps its own text; moving it back where it belongs is the only
                 reading, so the result is exactly what the person wrote, in the order the parser needs.
  == / =< / =>   SQL compares with one '=' and writes the bigger/smaller sign first (<=, >=). '==' (a habit from other
                 languages), '=<' and '=>' are refused; the comparison meant is plain.

Each repair returns the statement rebuilt and the findings that say what was moved or changed. The checker
(app/agents/sqlcorrect.py) then reads the repaired statement for everything else (meaning, NULLs, joins, groups).
"""

from __future__ import annotations

import re

# rank of each clause in the order the parser reads it; LIMIT and OFFSET share a rank (their own order differs by engine)
ORDER = {
    "select": {"from": 1, "join": 2, "where": 3, "group by": 4, "having": 5, "order by": 6, "limit": 7, "offset": 7},
    "update": {"join": 2, "set": 3, "where": 4, "order by": 6, "limit": 7},
    "delete": {"from": 1, "join": 2, "using": 2, "where": 3, "order by": 6, "limit": 7},
}
WORDS = {"select": "FROM, JOIN, WHERE, GROUP BY, HAVING, ORDER BY, LIMIT", "update": "UPDATE ... SET, WHERE, ORDER BY, LIMIT",
         "delete": "DELETE FROM, WHERE, ORDER BY, LIMIT"}
CLAUSE_RE = re.compile(r"\b(from|where|group\s+by|having|order\s+by|limit|offset|set|using)\b|" +
                       r"\b(?:natural\s+)?(?:(?:left|right|full)(?:\s+outer)?\s+|inner\s+|cross\s+)?join\b", re.I)
# a statement with any of these at its top level is left alone: its shape is more than one plain statement
OTHER_TOP = re.compile(r"\b(union|intersect|except|window|fetch|for\s+update|for\s+share|lock\s+in|into|returning|"
                       r"on\s+duplicate|values)\b", re.I)


def repair(sql: str, finding_cls, engine: str | None = None) -> tuple[str | None, list]:
    """(the statement repaired, the findings) or (None, []) when there's nothing a parser would refuse here."""
    from app.agents.sqlcorrect import _depths, _mask

    out, found = sql, []
    fixed, fs = _operators(out, finding_cls, engine)
    if fixed is not None:
        out, found = fixed, found + fs
    s = _mask(out)
    fixed, fs = _clause_order(out, s, _depths(s), finding_cls)
    if fixed is not None:
        out, found = fixed, found + fs
    return (out, found) if found else (None, [])


# ------------------------------------------------------------------------------------------------ == / =< / =>
def _operators(sql: str, F, engine: str | None) -> tuple[str | None, list]:
    from app.agents.sqlbasics import behaviour, says
    from app.agents.sqlcorrect import _depths, _mask

    s = _mask(sql)
    dep = _depths(s)
    edits, seen = [], []
    for m in re.finditer(r"(?<![<>!=:])(={2,3}|=<|=>)(?![=<>])", s):
        op = m.group(1)
        if op == "=>" and _named_argument(s, dep, m.start()):
            continue                                       # f(a => 1): PostgreSQL's named argument, not a comparison
        if op.startswith("==") and behaviour("double_equals", engine) == "works":
            continue                                       # SQLite reads == as = (its dialect file, lab-proven)
        new = {"=<": "<=", "=>": ">="}.get(op, "=")
        edits.append((m.start(), m.end(), new))
        seen.append((op, new, m.start()))
    if not edits:
        return None, []
    out = sql
    for a, b, new in sorted(edits, reverse=True):
        out = out[:a] + new + out[b:]
    F_out = []
    for op, new in dict.fromkeys((o, n) for o, n, _p in seen):
        at = next(p for o, n, p in seen if o == op)
        ev = re.sub(r"\s+", " ", sql[max(0, at - 25):at + 25]).strip()
        why = (f"SQL compares with a single '=': '{op}' is not an operator there" if op.startswith("==") else
               f"SQL writes the sign first: {new}, never {op}")
        F_out.append(F("error", "operator", f"'{op}' is not SQL: write {new}", f"'{op}' SQL nahi hai: {new} likho", ev,
                       f"{why}, so the database stops before reading any row (MySQL/MariaDB error 1064; PostgreSQL 'operator "
                       f"does not exist' or a syntax error). {says('double_equals', engine) if op.startswith('==') else ''}".strip()
                       + f" Write {new}: the comparison you meant, nothing else changes.",
                       f"SQL mein {'barabar ke liye ek hi =' if op.startswith('==') else 'nishaan pehle aata hai: ' + new}, isliye "
                       f"database query padhte hi rok deta hai. {new} likho: matlab wahi rehta hai."))
    return out, F_out


def _named_argument(s: str, dep: list, at: int) -> bool:
    """Inside f( ... ) right after a bare name (not a condition's brackets): name => value is an argument."""
    d = dep[at]
    if d == 0:
        return False
    i = at
    while i > 0 and not (s[i] == "(" and dep[i] == d):
        i -= 1
    m = re.search(r"([A-Za-z_][\w$]*)\s*$", s[:i])
    head = re.search(r"([A-Za-z_][\w$]*)\s*$", s[i + 1:at])
    return bool(m and head and m.group(1).lower() not in {"in", "and", "or", "not", "where", "on", "when", "having", "exists"})


# ------------------------------------------------------------------------------------------------ clause order
def _clause_order(sql: str, s: str, dep: list, F) -> tuple[str | None, list]:
    body_end = len(s.rstrip().rstrip(";").rstrip())
    lead = re.match(r"\s*(select|update|delete)\b", s, re.I)
    if not lead:
        return None, []
    kind = lead.group(1).lower()
    ranks = ORDER[kind]
    if any(dep[m.start()] == 0 for m in OTHER_TOP.finditer(s[:body_end])):
        return None, []
    marks = []
    for m in CLAUSE_RE.finditer(s[:body_end]):
        if dep[m.start()] != 0:
            continue
        word = "join" if m.group(1) is None else re.sub(r"\s+", " ", m.group(1).lower())
        if word not in ranks:
            continue                                       # JOIN ... USING (col), UPDATE ... FROM: part of the clause before it
        if word == "from" and kind == "delete" and marks:
            return None, []                                # DELETE t FROM t JOIN ...: the multi-table form, left as written
        if kind == "update" and word == "join" and any(w == "set" for _p, w in marks):
            return None, []                                # a JOIN after SET: not a shape to reorder
        marks.append((m.start(), word))
    if not marks:
        return None, []
    words = [w for _p, w in marks]
    for w in set(words) - {"join"}:
        if words.count(w) > 1:
            return None, []                                # two WHEREs: which one was meant is not for the checker to pick
    if kind == "select" and "from" not in words and "join" in words:
        return None, []
    if kind == "select" and "join" in words and words.index("join") < words.index("from"):
        return None, []
    order = [ranks[w] for w in words]
    if order == sorted(order):
        return None, []
    head = sql[:marks[0][0]]
    segs = []
    for k, (p, w) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else body_end
        segs.append((ranks[w], k, w, sql[p:end]))
    ordered = sorted(segs, key=lambda x: (x[0], x[1]))
    has_comment = s != _strip_strings_only(sql)
    sep = "\n" if "\n" in sql[:body_end] or has_comment else " "
    tail = sql[body_end:]
    if has_comment and tail.strip():
        tail = "\n" + tail.lstrip()                       # a line comment at the end of the last clause must not swallow the ';'
    rebuilt = head.rstrip() + sep + sep.join(x[3].strip() for x in ordered) + tail
    # the first clause that came too early / too late, said in the person's own words
    late = next(segs[k] for k in range(1, len(segs)) if segs[k][0] < max(x[0] for x in segs[:k]))
    early = next(x for x in segs[:late[1]] if x[0] > late[0])
    a, b = _label(late[2]), _label(early[2])
    ev = re.sub(r"\s+", " ", f"{early[3].strip()[:40]} ... {late[3].strip()[:40]}")
    finding = F("error", "clause_order", f"{a} is written after {b}: clauses have a fixed order",
                f"{a}, {b} ke baad likha hai: clauses ka order tay hai", ev,
                f"Every database reads a {kind.upper()}'s clauses in one order ({WORDS[kind]}), so a clause out of place stops the "
                f"statement before it runs (MySQL/MariaDB error 1064 near '{a}', PostgreSQL 'syntax error at or near \"{a}\"'). "
                f"Moving {a} back before {b} keeps every condition exactly as you wrote it.",
                f"Har database {kind.upper()} ke clauses ek hi order mein padhta hai ({WORDS[kind]}), isliye galat jagah wala clause "
                f"query ko chalne se pehle hi rok deta hai. {a} ko {b} se pehle le jao: conditions wahi rehti hain.")
    return rebuilt, [finding]


def _label(word: str) -> str:
    return {"join": "JOIN"}.get(word, word.upper())


def _strip_strings_only(sql: str) -> str:
    """The query with only its strings masked (comments kept): differs from the full mask exactly where comments are."""
    out, i, n = list(sql), 0, len(sql)
    while i < n:
        c = sql[i]
        if c in "'\"":
            j = i + 1
            while j < n and not (sql[j] == c and sql[j - 1] != "\\"):
                j += 1
            for k in range(i + 1, min(j, n)):
                out[k] = "x" if sql[k] != "\n" else "\n"
            i = j + 1
            continue
        i += 1
    return "".join(out)
