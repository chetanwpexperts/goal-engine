"""Night S2 (X4): small worked puzzles about what SQL returns, answered by running them, not by memory.

"A column holds (10, NULL, 20): what do COUNT(*), COUNT(col) and AVG(col) give?"
"Table A has (1, 1, NULL) and B has (1, NULL): how many rows does each join return?"

The values are put in a throwaway in-memory SQLite table and the asked expressions / joins are run there; the answer gives
the numbers with the rule behind each one. NULL handling, COUNT and AVG, and join row counts are the same on MySQL, MariaDB
and PostgreSQL; where an engine differs (FULL OUTER JOIN) it is said.
"""

from __future__ import annotations

import re
import sqlite3

VAL = r"(?:NULL|null|-?\d+(?:\.\d+)?|'[^']*')"
LIST = rf"\(\s*{VAL}(?:\s*,\s*{VAL})+\s*\)"
AGG = re.compile(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(\s*(DISTINCT\s+)?(\*|[A-Za-z_][A-Za-z0-9_]*)\s*\)", re.I)
JOINS = [("INNER JOIN", r"\binner\s+join\b|\bplain join\b"), ("LEFT JOIN", r"\bleft\s+(?:outer\s+)?join\b"),
         ("RIGHT JOIN", r"\bright\s+(?:outer\s+)?join\b"), ("FULL OUTER JOIN", r"\bfull\s+(?:outer\s+)?join\b"),
         ("CROSS JOIN", r"\bcross\s+join\b")]


def _values(s: str) -> list:
    out = []
    for v in re.findall(VAL, s):
        if v.lower() == "null":
            out.append(None)
        elif v.startswith("'"):
            out.append(v[1:-1])
        else:
            out.append(float(v) if "." in v else int(v))
    return out


def _lists(text: str) -> list[tuple[str | None, list, int]]:
    """(table or column name said before it, values, position) for each list of values."""
    out = []
    for m in re.finditer(LIST, text):
        before = text[max(0, m.start() - 80):m.start()]
        before = before[before.rfind(")") + 1:]               # only the words since the previous list
        named = re.findall(r"\b(?:table|column|tabel)\s+([A-Za-z_][A-Za-z0-9_]*)", before, re.I)
        other = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s+(?:column\s+)?(?:has|holds|with|contains|values?)\b", before, re.I)
        stop = {"table", "column", "values", "value", "rows", "row", "with", "has", "and", "a", "the", "it", "that", "which", "tabel"}
        cands = [w for w in named + other if w.lower() not in stop]
        name = cands[0] if cands else None
        out.append((name, _values(m.group(0)), m.start()))
    return out


def is_puzzle(text: str) -> bool:
    t = text or ""
    if len(re.findall(LIST, t)) == 0:
        return False
    if re.search(r"(?is)\bselect\b.+\bfrom\b|\binsert\s+into\b|\bcreate\s+table\b", t):
        return False                                    # a real query or table: the checker's / writer's job
    asks = re.search(r"\b(?:what|how many|which|kitne|kya|return|returns|give|gives|result)\b|\?|क्या|कितने", t, re.I)
    return bool(asks and (AGG.search(t) or any(re.search(p, t, re.I) for _n, p in JOINS)))


def _fmt(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else f"{v:.4f}".rstrip("0")
    return str(v)


def solve(text: str) -> dict | None:
    """{'lines': [...], 'notes': [...]} or None when it isn't one of these puzzles."""
    if not is_puzzle(text):
        return None
    lists = _lists(text)
    db = sqlite3.connect(":memory:")
    try:
        joins = [(n, p) for n, p in JOINS if re.search(p, text, re.I)]
        if joins and len(lists) >= 2:
            return _joins(db, text, lists[:2], joins)
        if AGG.search(text) and lists:
            return _aggs(db, text, lists[0])
    except sqlite3.Error:
        return None
    finally:
        db.close()
    return None


def _aggs(db, text: str, lst) -> dict:
    name, vals, _pos = lst
    cols = [c for _f, _d, c in AGG.findall(text) if c != "*"]
    col = cols[0] if cols else (name or "x")
    db.execute(f'CREATE TABLE t ("{col}")')
    db.executemany("INSERT INTO t VALUES (?)", [(v,) for v in vals])
    lines, seen = [], set()
    for m in AGG.finditer(text):
        fn, dist, arg = m.group(1).upper(), (m.group(2) or "").strip().upper(), m.group(3)
        arg = "*" if arg == "*" else f'"{col}"'
        expr = f"{fn}({dist + ' ' if dist else ''}{arg})"
        shown = f"{fn}({dist + ' ' if dist else ''}{'*' if arg == '*' else col})"
        if shown in seen:
            continue
        seen.add(shown)
        v = db.execute(f"SELECT {expr} FROM t").fetchone()[0]
        lines.append((shown, _fmt(v)))
    sql = (f"-- t({col}) holds: {', '.join(_fmt(v) for v in vals)}\n"
           f"SELECT {', '.join(s for s, _v in lines)}\nFROM t")
    n, nulls = len(vals), sum(v is None for v in vals)
    notes = [f"{n} rows, {nulls} of them NULL."]
    if any(s.startswith("COUNT(*)") for s, _v in lines):
        notes.append("COUNT(*) counts rows, NULLs included.")
    if any(re.match(r"COUNT\((?!\*)", s) for s, _v in lines):
        notes.append(f"COUNT({col}) counts only the rows where {col} is not NULL.")
    if any(s.startswith(("AVG", "SUM", "MIN", "MAX")) for s, _v in lines):
        notes.append("SUM, AVG, MIN and MAX skip NULLs: AVG is the sum of the non-NULL values divided by how many there are, "
                     "not by all rows (to count NULL as 0: AVG(COALESCE(col, 0))).")
    notes.append("The same on MySQL, MariaDB, PostgreSQL and SQLite (AVG's decimals are shown differently).")
    return {"lines": lines, "notes": notes, "sql": sql}


def _joins(db, text: str, lists, joins) -> dict:
    (na, va, _p1), (nb, vb, _p2) = lists
    na, nb = (na or "a"), (nb or "b")
    if na.lower() == nb.lower():
        na, nb = "a", "b"
    db.execute("CREATE TABLE a (v)")
    db.execute("CREATE TABLE b (v)")
    db.executemany("INSERT INTO a VALUES (?)", [(v,) for v in va])
    db.executemany("INSERT INTO b VALUES (?)", [(v,) for v in vb])
    lines, queries = [], [f"-- {na}(v): {', '.join(_fmt(v) for v in va)}\n-- {nb}(v): {', '.join(_fmt(v) for v in vb)}"]
    for jn, _p in joins:
        on = "" if jn == "CROSS JOIN" else " ON a.v = b.v"
        n = db.execute(f"SELECT COUNT(*) FROM a {jn} b{on}").fetchone()[0]
        lines.append((f"{na} {jn} {nb}" + ("" if jn == "CROSS JOIN" else " ON the value"), str(n)))
        queries.append(f"SELECT COUNT(*) FROM {na} {jn} {nb}" + ("" if jn == "CROSS JOIN" else f" ON {na}.v = {nb}.v") + ";")
    notes = [f"{na}: {', '.join(_fmt(v) for v in va)}; {nb}: {', '.join(_fmt(v) for v in vb)}.",
             "Each matching pair is one row: a value seen 2 times on one side and 3 times on the other gives 2 x 3 = 6 rows.",
             "NULL never equals anything, not even another NULL, so NULL rows never match."]
    if any(j == "LEFT JOIN" for j, _p in joins):
        notes.append(f"LEFT JOIN adds each {na} row that matched nothing once, with NULLs for {nb}.")
    if any(j == "RIGHT JOIN" for j, _p in joins):
        notes.append(f"RIGHT JOIN adds each {nb} row that matched nothing once.")
    if any(j == "FULL OUTER JOIN" for j, _p in joins):
        notes.append("FULL OUTER JOIN = the matches + the unmatched rows of both sides. MySQL and MariaDB don't have FULL OUTER JOIN "
                     "(write LEFT JOIN ... UNION ALL ... RIGHT JOIN ... WHERE left side IS NULL); PostgreSQL and SQLite 3.39+ do.")
    if any(j == "CROSS JOIN" for j, _p in joins):
        notes.append(f"CROSS JOIN pairs every row with every row: {len(va)} x {len(vb)}.")
    return {"lines": lines, "notes": notes, "sql": "\n".join(queries)}


def card(title: str, res: dict, hi: bool = False) -> dict:
    """For the page: the reasons in the chat, the query that was run and what it returned in the side panel."""
    answer = "\n".join(f"- {a} = {b}" for a, b in res["lines"]) + ("\n\nKyun:\n" if hi else "\n\nWhy:\n") + \
        "\n".join(f"- {n}" for n in res["notes"])
    return {"id": "puzzle", "lang": "hinglish" if hi else "en", "title": title, "answer": answer, "mistake": "",
            "examples": [{"label": "", "sql": [{"label": "SQLite", "query": res["sql"]}],
                          "columns": ["expression" if not hi else "kya", "returns" if not hi else "nateeja"],
                          "rows": [[a, b] for a, b in res["lines"]]}],
            "engines": [], "labels": {"example": "Query", "lab": "SQLite pe chalaya" if hi else "run on SQLite", "result": "Result",
                                      "mistake": "", "engines": "", "preview": "", "change": ""}}


def reply(text: str, hi: bool = False) -> str | None:
    res = solve(text)
    if not res:
        return None
    head = "Ye milega (chala ke dekha):" if hi else "Here's what it returns (worked out by running it):"
    body = "\n".join(f"- {a} = {b}" for a, b in res["lines"])
    why = ("\n\nKyun:\n" if hi else "\n\nWhy:\n") + "\n".join(f"- {n}" for n in res["notes"])
    return f"{head}\n{body}{why}"
