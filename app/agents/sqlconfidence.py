"""How sure is each finding, and what does the query alone tell about the tables? (night 11; fundamental 'proof_classes' in
app/dbknow/core/query_fundamentals.json, classes in app/dbknow/core/confidence.json)

With only the pasted query there is no schema, no index list, no data and no plan. DBaba never invents those facts:
- every finding gets one class: PROVEN (the SQL text alone shows it), LIKELY RISK (the shape suggests it; whether it bites
  depends on the data or the intent), NEEDS SCHEMA (a fact about the tables decides it: NULLs, keys, a time of day, types)
  or NEEDS EXECUTION (a speed risk: indexes, row counts and the plan decide it). A finding the project's or pasted schema has
  checked (night 10B) is upgraded to verified; a speed finding stays a risk until EXPLAIN shows the plan.
- an inferred schema is read from the query with verified=false on every fact: the tables and their aliases, the columns
  each one is seen with and the role they play (join key, filter, grouping key, measure, output, sort), a rough type only
  where a literal or a function shows it, and the links between tables from the ON conditions, with a guessed direction
  that is marked as a guess. With a schema, each fact it confirms is marked verified.
- the report lists what can't be proven from the query alone, each with the read-only query that checks it in the user's
  database (the engine's own words come from the dialect files' 'verify', standing rule B).
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.agents import sqlcorrect as C

CORE = Path(__file__).resolve().parent.parent / "dbknow" / "core" / "confidence.json"
PROVEN_ON = ["mariadb", "postgresql"]          # every class rule and every check query below: tests/practice/test_night11.py LAB

NAME = r"(?:`[^`]+`|\"[^\"]+\"|[A-Za-z_][\w$]*)"
CMP = r"(=|<>|!=|<=|>=|<|>)"
NUM = r"-?\d+(?:\.\d+)?"
STR = r"'(?:[^']|'')*'"
DATE_LIT = re.compile(r"^'\d{4}-\d{2}-\d{2}'$")
DT_LIT = re.compile(r"^'\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}(:\d{2})?'$")
ROLE_EN = {"join_key": "join key", "filter": "filter", "group_key": "grouping key", "measure": "measure (inside a total)",
           "output": "shown", "sort": "sort", "changed": "changed", "window": "window key"}
ROLE_HI = {"join_key": "join key", "filter": "filter", "group_key": "group key", "measure": "measure (total ke andar)",
           "output": "dikhaya", "sort": "sort", "changed": "badla", "window": "window key"}
TYPE_EN = {"number": "a number", "text": "text", "date": "a date", "datetime": "a date-time", "boolean": "true/false"}
TYPE_HI = {"number": "number", "text": "text", "date": "date", "datetime": "date-time", "boolean": "true/false"}
SPEED_KINDS = {"or_union", "keyset", "sargable"}


@lru_cache(maxsize=1)
def rules() -> dict:
    return json.loads(CORE.read_text(encoding="utf-8"))


def _uq(name: str) -> str:
    return name.strip('`"').lower()


# ------------------------------------------------------------------------------------------------ the class of a finding
def classify(f: dict) -> str:
    """proven / likely_risk / needs_schema / needs_execution for one finding (a correctness finding or a speed one)."""
    R = rules()
    topic = f.get("topic") or ""
    if "kind" not in f or not f.get("kind"):                       # a speed finding of the index reader (no kind)
        return "needs_execution"
    facts = [n[0] for n in f.get("needs") or []]
    if f.get("schema") == "verified":                               # the tables were read: the fact is known, not guessed
        return "needs_execution" if "index" in facts or topic in R["topics"]["needs_execution"] else "proven"
    if f["kind"] == "error":
        return "proven"
    for want in ("needs_schema", "likely_risk", "needs_execution"):   # a fact the finding depends on decides first
        if any(R["needs"].get(x) == want for x in facts):
            return want
    if f["kind"] == "wrong":
        return "proven"
    if topic in R["topics"]["needs_execution"]:
        return "needs_execution"
    if topic in R["topics"]["needs_schema"]:
        return "needs_schema"
    return "likely_risk"


def _engine() -> str | None:
    from app.agents.sqlbasics import CHOSEN

    return CHOSEN.get() or None


def _verify(eid: str | None) -> list[tuple[str, dict]]:
    """[(engine label, verify templates)]: the chosen engine's, or MySQL/MariaDB and PostgreSQL both when none is chosen."""
    from app.dbknow import engines as E

    if eid in ("mysql", "mariadb", "postgresql"):
        return [(E.label(eid), E.dialect(eid)["verify"])]
    if eid in E.ORDER:                                              # notes-only engines: general SQL, said as such
        return []
    return [("MySQL/MariaDB", E.dialect("mariadb")["verify"]), ("PostgreSQL", E.dialect("postgresql")["verify"])]


def _check_sql(fact: str, table: str, cols: list[str], eid: str | None) -> list[dict]:
    """The read-only queries that settle one fact in the user's database."""
    out = []
    for lab, v in _verify(eid):
        if fact in ("not_null", "has_time"):
            q = v["nullable"].format(table=table, cols=", ".join(f"'{c}'" for c in cols))
            out.append({"engine": lab, "sql": q})
        elif fact in ("unique", "to_one"):
            out.append({"engine": lab, "sql": v["dupes"].format(table=table, cols=", ".join(cols))})
        elif fact == "index":
            out.append({"engine": lab, "sql": v["indexes"].format(table=table)})
    seen, uniq = set(), []
    for x in out:                                                   # the same text for both engines: said once
        if x["sql"] not in seen:
            seen.add(x["sql"])
            uniq.append(x)
    if len(uniq) == 1 and len(out) > 1:
        uniq[0]["engine"] = " / ".join(x["engine"] for x in out)
    return uniq


def _plan_sql(query: str, eid: str | None, change: bool) -> list[dict]:
    out = []
    q = re.sub(r";\s*$", "", query.strip())
    for lab, v in _verify(eid):
        out.append({"engine": lab, "sql": (v["plan"] if change else v["plan_run"]).format(query=q), "note": v["plan_run_note"]})
    return out


def _fact_tables(fact: str, q, col, amap: dict, single: str | None) -> tuple[str | None, list[str]]:
    cols = col if isinstance(col, list) else ([col] if col else [])
    cols = [_uq(c) for c in cols if c]
    if fact == "to_one":
        return amap.get(_uq(q or "")) or None, []
    t = amap.get(_uq(q)) if q else single
    return t, cols


def _depends(f: dict, sql: str, amap: dict, single: str | None, eid: str | None) -> list[dict]:
    """What the finding depends on, in words, with the query that checks it."""
    R = rules()["facts"]
    out = []
    for fact, q, col in f.get("needs") or []:
        if fact not in R:
            continue
        table, cols = _fact_tables(fact, q, col, amap, single)
        if fact == "to_one":
            on = _on_cols(sql, q or "")
            cols = on
        if not table:
            continue
        shown = ", ".join(f"{table}.{c}" for c in cols) if fact != "unique" else ", ".join(cols)
        out.append({"fact": fact, "table": table, "cols": cols,
                    "en": R[fact]["en"].format(col=shown or table, table=table),
                    "hi": R[fact]["hi"].format(col=shown or table, table=table),
                    "check": _check_sql(fact, table, cols, eid) if (cols or fact == "index") else []})
    return out


def _on_cols(sql: str, alias: str) -> list[str]:
    """The columns of this alias its join's ON compares (the columns that decide one match or many)."""
    from app.agents.sqlverify import _join_on

    on = _join_on(sql, alias) or ""
    got = [m.group(2) for m in C.QREF.finditer(C._mask(on)) if _uq(m.group(1)) == _uq(alias)]
    return list(dict.fromkeys(_uq(c) for c in got if c != "*"))


def label(cls: str, hi: bool = False) -> str:
    return rules()["classes"][cls]["hi" if hi else "en"]


def annotate_findings(findings: list, sql: str, amap: dict | None = None) -> None:
    """Adds 'confidence' to each finding: {class, label_en, label_hi, why_en, why_hi, verified, depends: [...]}."""
    amap = amap if amap is not None else _amap(sql)
    real = sorted({t for t in amap.values() if t})
    single = real[0] if len(real) == 1 else None
    eid = _engine()
    K = rules()["classes"]
    for f in findings or []:
        if not isinstance(f, dict) or "confidence" in f:
            continue
        cls = classify(f)
        dep = _depends(f, sql, amap, single, eid) if cls != "proven" or f.get("schema") == "verified" else []
        why_en, why_hi = K[cls]["why_en"], K[cls]["why_hi"]
        if dep and cls in ("needs_schema", "likely_risk"):
            why_en = "It depends on " + "; ".join(d["en"] for d in dep) + ": the query text doesn't show it."
            why_hi = "Ye is pe tika hai: " + "; ".join(d["hi"] for d in dep) + " (query text mein nahi dikhta)."
        elif cls == "needs_execution" and dep:
            why_en = K[cls]["why_en"] + " Worth checking " + "; ".join(d["en"] for d in dep) + "."
            why_hi = K[cls]["why_hi"] + " Dekh lo " + "; ".join(d["hi"] for d in dep) + "."
        f["confidence"] = {"class": cls, "label_en": K[cls]["en"], "label_hi": K[cls]["hi"], "why_en": why_en, "why_hi": why_hi,
                           "verified": f.get("schema") == "verified", "depends": dep}


def _amap(sql: str) -> dict:
    from app.agents.sqlverify import aliases

    try:
        return aliases(sql)
    except Exception:  # noqa: BLE001 - an odd query: no aliases known
        return {}


# ------------------------------------------------------------------------------------------------ the inferred schema
def _marks(s: str, dep: list[int]) -> list[tuple[int, int, str]]:
    """(position, depth, clause) for every clause word in the masked query."""
    out = []
    for m in re.finditer(r"\b(select|from|join|on|using|where|group\s+by|having|order\s+by|limit|set|values|partition\s+by|"
                         r"returning|union|window|over)\b", s, re.I):
        out.append((m.start(), dep[m.start()], re.sub(r"\s+", " ", m.group(1).lower())))
    return out


def _clause_at(pos: int, s: str, dep: list[int], marks) -> str:
    """The clause a position is in: the last clause word before it inside its own brackets, else around those brackets."""
    d, p = dep[pos], pos
    if s[pos] == "(":
        d -= 1
    while True:
        start = 0
        if d > 0:
            start = next((i for i in range(p - 1, -1, -1) if s[i] == "(" and dep[i] == d), 0)
        best = None
        for mp, md, cl in marks:
            if start <= mp < p and md == d:
                best = cl
        if best or d <= 0:
            return best or ""
        p, d = start, d - 1


def _scope_table(pos: int, s: str, dep: list[int], marks, amap: dict) -> tuple[str | None, bool]:
    """(table, nested) for a bare column: the one real table its own SELECT (or UPDATE / DELETE) reads, when it reads exactly
    one; None when that block reads several, or a CTE / derived table (then the owner can't be told from the text).
    nested: the block sits inside another one, so an outer table could own the column (SQL looks inside first)."""
    d, p = dep[pos], pos
    while True:                                       # out of function-call / expression brackets, up to the query block
        start = 0 if d == 0 else next((i for i in range(p - 1, -1, -1) if s[i] == "(" and dep[i] == d), 0)
        if d == 0 or re.match(r"\(\s*(select|with)\b", s[start:], re.I):
            break
        p, d = start, d - 1
    end = len(s) if d == 0 else C._close(s, start)
    # night 13: a UNION / INTERSECT / EXCEPT part is its own block: a bare column belongs to its part's FROM, not the first one
    cuts = [m for m in re.finditer(r"\b(?:union|intersect|except)\b(?:\s+(?:all|distinct)\b)?", s[start:end], re.I)
            if dep[start + m.start()] == d]
    if cuts:
        lo, hi = start, end
        for m in cuts:
            if start + m.end() <= pos:
                lo = start + m.end()
            elif start + m.start() > pos:
                hi = start + m.start()
                break
        start, end = lo, hi
    src = None
    for mp, md, cl in marks:
        if start <= mp < end and md == d and cl in ("from",):
            src = mp
            break
    head = re.match(rf"[\s(]*(update|delete\s+from)\s+(?:only\s+)?(?:{NAME}\.)?({NAME})", s[start + (1 if d else 0):], re.I)
    if head and (src is not None or re.search(r"\bjoin\b|\busing\b", s[start:end], re.I)):
        # UPDATE t ... FROM / JOIN other (or DELETE ... USING): only a SET target is surely t's column
        if head.group(1).lower() == "update" and _clause_at(pos, s, dep, marks) == "set" and \
                re.match(r"\s*=(?!=)", s[pos + len(re.match(r"[\w$]+", s[pos:]).group(0)):]):
            tbl = amap.get(_uq(head.group(2)))
            return (tbl, d > 0) if tbl else (None, False)
        return None, False
    if src is None:
        um = re.match(r"[\s(]*(?:update|delete\s+from)\s+", s[start + (1 if d else 0):], re.I)
        if not um:
            return None, False
        src = start + (1 if d else 0) + um.start()
        body_start = start + (1 if d else 0) + um.end()
    else:
        body_start = src + 4
    stop = end
    for mp, md, cl in marks:
        if body_start <= mp < end and md == d and cl in ("where", "group by", "having", "order by", "limit", "union", "window",
                                                           "set", "returning"):
            stop = mp
            break
    body = s[body_start:stop]
    bd = C._depths(body)
    if any(c == "," and bd[i] == 0 for i, c in enumerate(body)) or \
            any(bd[m.start()] == 0 for m in re.finditer(r"\bjoin\b", body, re.I)) or re.match(r"\s*\(", body):
        return None, False
    nm = re.match(rf"\s*(?:only\s+)?(?:{NAME}\.)?({NAME})", body)
    if not nm:
        return None, False
    tbl = amap.get(_uq(nm.group(1)))
    return (tbl, d > 0) if tbl else (None, False)


def _in_agg(pos: int, s: str, dep: list[int]) -> bool:
    for m in C.AGG.finditer(s):
        if m.end() - 1 < pos < C._close(s, m.end() - 1):
            return True
    return False


def _lit_type(text: str) -> str | None:
    t = text.strip()
    if re.fullmatch(NUM, t):
        return "number"
    if DT_LIT.match(t):
        return "datetime"
    if DATE_LIT.match(t):
        return "date"
    if re.fullmatch(STR, t):
        return "text"
    if t.lower() in ("true", "false"):
        return "boolean"
    return None


def infer_schema(sql: str, sch: dict | None = None) -> dict:
    """{'tables': [...], 'links': [...], 'unassigned': [...], 'verified': bool} read from the query only (see the module text).
    Nothing here is a fact about the database until 'verified' says so."""
    s = C._mask(sql)
    dep = C._depths(s)
    amap = _amap(sql)
    real = {a: t for a, t in amap.items() if t}
    derived = sorted({a for a, t in amap.items() if not t})
    tables: dict[str, dict] = {}
    for a, t in real.items():
        tb = tables.setdefault(t, {"name": t, "aliases": [], "columns": {}, "kind": "table", "verified": False})
        if a != t and a not in tb["aliases"]:
            tb["aliases"].append(a)
    marks = _marks(s, dep)
    sel_alias = {_uq(m.group(1)) for m in re.finditer(rf"\bas\s+({NAME})", s, re.I)}

    def col(t, c):
        return tables[t]["columns"].setdefault(c, {"roles": [], "type": None, "type_from": "", "null_checked": False, "verified": False})

    def role_of(pos):
        cl = _clause_at(pos, s, dep, marks)
        r = {"on": "join_key", "using": "join_key", "where": "filter", "having": "filter", "group by": "group_key",
             "order by": "sort", "select": "output", "set": "changed", "partition by": "window", "returning": "output"}.get(cl)
        if _in_agg(pos, s, dep) and cl in ("select", "having", "order by", ""):
            r = "measure"
        return r

    refs = []                                                        # (table, column, start, end)
    for m in C.QREF.finditer(s):
        q, c = _uq(m.group(1)), m.group(2)
        if c == "*" or q not in real:
            continue
        if re.match(r"\s*\(", s[m.end():]):
            continue
        refs.append((real[q], _uq(c), m.start(), m.end()))
    unassigned, guessed = [], set()
    skip = set(C.KW) | {x.lower() for x in amap} | sel_alias | {"nulls", "first", "last", "ilike", "similar", "to", "filter",
                                                                 "within", "rollup", "ties", "fetch", "next", "only", "rows",
                                                                 "returning", "conflict", "do", "nothing", "excluded", "of",
                                                                 "epoch", "dow", "doy", "isodow", "microsecond", "zone", "at",
                                                                 "local", "boolean", "numeric", "text", "bigint", "smallint",
                                                                 "real", "double", "precision", "float", "json", "jsonb",
                                                                 "uuid", "bit", "varying", "without", "array"}
    words_ok = not re.search(r"\bnatural\s+join\b|\busing\s*\(", s, re.I)
    for m in C.WORD.finditer(s) if words_ok else []:
        w = m.group(1)
        if w.lower() in skip or re.fullmatch(r"\d.*", w):
            continue
        before = s[max(0, m.start() - 12):m.start()].lower()
        if re.search(r"\b(as|from|join|update|into|table|interval|with|recursive)\s+$", before) or re.search(r"[:@]$|\.\s*$", before):
            continue
        if re.match(r"\s*(\(|\bas\s*\()", s[m.end():], re.I):
            continue
        cl = _clause_at(m.start(), s, dep, marks)
        if cl in ("from", "join", "limit", "values", "window", "union", "using", ""):
            continue
        tbl, nested = _scope_table(m.start(), s, dep, marks, amap)
        if not tbl:
            continue
        refs.append((tbl, _uq(w), m.start(), m.end()))
        if nested:
            guessed.add((tbl, _uq(w)))
    for t, c, a, b in refs:
        info = col(t, c)
        r = role_of(a)
        if r and r not in info["roles"]:
            info["roles"].append(r)
        after, before = sql[b:b + 80], sql[max(0, a - 60):a]
        lit = None
        mm = re.match(rf"\s*{CMP}\s*({NUM}|{STR}|true|false)(?![\w.])", after, re.I)
        if mm:
            lit = mm.group(2)
        mb = re.search(rf"(?<![\w.])({NUM}|{STR})\s*{CMP}\s*$", before) if not lit else None
        if mb and not lit:
            lit = mb.group(1)
        mi = re.match(rf"\s*(?:not\s+)?(?:in\s*\(\s*({NUM}|{STR})|between\s+({NUM}|{STR}))", after, re.I) if not lit else None
        if mi:
            lit = mi.group(1) or mi.group(2)
        guess, src = (_lit_type(lit), f"compared with {lit.strip()[:30]}") if lit else (None, "")
        if not guess and re.match(r"\s*(?:not\s+)?i?like\b", after, re.I):
            guess, src = "text", "used with LIKE"
        if not guess and re.search(r"\b(sum|avg)\s*\(\s*(?:distinct\s+)?$", before, re.I):
            guess, src = "number", "added up with SUM/AVG"
        if not guess and re.match(r"\s*is\s+(not\s+)?(true|false)\b", after, re.I):
            guess, src = "boolean", "tested with IS TRUE/FALSE"
        if guess and not info["type"]:
            info["type"], info["type_from"] = guess, src
        elif guess and info["type"] and guess != info["type"] and {guess, info["type"]} != {"date", "datetime"}:
            info["type_from"] += f"; also {src}"
            info["mixed"] = True
        if re.match(r"\s*is\s+(not\s+)?null\b", after, re.I) or re.search(r"\b(coalesce|ifnull|nvl|isnull)\s*\(\s*$", before, re.I):
            info["null_checked"] = True
    links = []
    for m in re.finditer(rf"({NAME})\s*\.\s*({NAME})\s*=\s*({NAME})\s*\.\s*({NAME})", s):
        la, lc, ra, rc = (_uq(m.group(i)) for i in (1, 2, 3, 4))
        if la not in amap or ra not in amap or la == ra:
            continue
        if _clause_at(m.start(), s, dep, marks) not in ("on", "where"):
            continue
        lt, rt = amap.get(la) or la, amap.get(ra) or ra
        guess = _direction(lt, lc, rt, rc)
        links.append({"left": f"{lt}.{lc}", "right": f"{rt}.{rc}", "left_alias": la, "right_alias": ra,
                      "where": _clause_at(m.start(), s, dep, marks), "guess": guess, "verified": False})
    for tb, c in guessed:
        if c in tables[tb]["columns"]:
            tables[tb]["columns"][c]["owner_guess"] = True
    out = {"tables": list(tables.values()), "derived": derived, "links": links, "unassigned": unassigned, "verified": False}
    if sch:
        _verify_with(out, sch)
    return out


def _direction(lt: str, lc: str, rt: str, rc: str) -> str:
    """A guess from the names only: '<many> -> <one>' when one side is an id and the other looks like a reference to it."""
    sing = lambda t: {x.replace("_", "") for x in C._singulars(t)}  # noqa: E731

    def is_id(t, c):
        return c == "id" or c.replace("_", "") in {x + "id" for x in sing(t)}

    def refers(c, t):
        return c.endswith("_id") and c[:-3].replace("_", "") in sing(t) or c.replace("_", "") in {x + "id" for x in sing(t)}

    l_id, r_id = is_id(lt, lc), is_id(rt, rc)
    if l_id and r_id and lt != rt:
        return "one-to-one"
    if r_id and (refers(lc, rt) or not l_id):
        return f"many {lt} -> one {rt}"
    if l_id and (refers(rc, lt) or not r_id):
        return f"many {rt} -> one {lt}"
    return "unknown"


def _verify_with(inf: dict, sch: dict) -> None:
    from app.agents import sqlproject as P

    for t in inf["tables"]:
        td = P.table(sch, t["name"])
        if not td or td["partial"]:
            continue
        t["verified"] = True
        for c, info in t["columns"].items():
            cd = td["cols"].get(P.uq(c))
            if cd:
                info["verified"] = True
                info["real_type"] = cd["type"]
                info["not_null"] = bool(cd["not_null"])
                info["key"] = P.is_unique(td, [cd["name"]])
            else:
                info["missing"] = True
    for k in inf["links"]:
        lt, lc = k["left"].rsplit(".", 1)
        rt, rc = k["right"].rsplit(".", 1)
        a, b = P.table(sch, lt), P.table(sch, rt)
        if not a or not b or a["partial"] or b["partial"]:
            continue
        lu, ru = P.is_unique(a, [lc]), P.is_unique(b, [rc])
        k["verified"] = True
        k["shape"] = ("one-to-one" if lu and ru else f"many {lt} -> one {rt}" if ru else f"many {rt} -> one {lt}" if lu else
                      "many-to-many (neither side is a key)")
    inf["verified"] = any(t["verified"] for t in inf["tables"])


# ------------------------------------------------------------------------------------------------ what the text can't prove
def cant_prove(sql: str, inf: dict, findings: list, is_change: bool = False) -> list[dict]:
    """What can't be proven from the query alone, most important first, each with the query that checks it."""
    eid = _engine()
    out, seen = [], set()

    def add(key, en, hi, check):
        if key in seen:
            return
        seen.add(key)
        out.append({"en": en, "hi": hi, "check": check})
    for f in findings or []:
        for d in (f.get("confidence") or {}).get("depends") or []:
            if f.get("schema") == "verified" and d["fact"] != "index":
                continue
            add((d["fact"], d["table"], tuple(d["cols"])), d["en"][0].upper() + d["en"][1:], d["hi"][0].upper() + d["hi"][1:], d["check"])
    for k in inf.get("links") or []:
        if k.get("verified"):
            continue
        rt, rc = k["right"].rsplit(".", 1)
        lt, lc = k["left"].rsplit(".", 1)
        one_t, one_c = (lt, lc) if k["guess"].endswith(f"one {lt}") else (rt, rc)
        if not any(t["name"] == one_t for t in inf["tables"]):
            continue
        add(("unique", one_t, (one_c,)), f"Whether {one_t}.{one_c} is unique, so the join {k['left']} = {k['right']} matches one row "
            f"and copies nothing (from the names it looks like {k['guess']}, a guess)",
            f"{one_t}.{one_c} unique hai ya nahi, taaki join {k['left']} = {k['right']} ek hi row mile aur kuch copy na ho (naam se "
            f"{k['guess']} lagta hai, andaza)", _check_sql("unique", one_t, [one_c], eid))
    for t in inf.get("tables") or []:
        if t.get("verified"):
            continue
        for c, info in t["columns"].items():
            if info.get("type") and len(out) < 8:
                add(("type", t["name"], c), f"The real type of {t['name']}.{c} (the query only shows it {info['type_from']})",
                    f"{t['name']}.{c} ka asli type (query mein bas {info['type_from']} dikhta hai)",
                    _check_sql("has_time", t["name"], [c], eid))
    idx = [(t["name"], c) for t in inf.get("tables") or [] for c, info in t["columns"].items()
           if {"filter", "join_key", "sort"} & set(info["roles"])]
    for tn, c in idx[:4]:
        add(("index", tn, (c,)), f"Whether an index exists on {tn}({c}) (worth checking; the query can't show indexes)",
            f"{tn}({c}) pe index hai ya nahi (dekhne layak; query index nahi dikhati)", _check_sql("index", tn, [c], eid))
    add(("plan",), "How many rows each table has, which plan the database picks and how long it takes (only EXPLAIN or a run shows it)",
        "Har table mein kitni rows hain, database kaunsa plan chunta hai aur kitna time lagta hai (sirf EXPLAIN ya run dikhata hai)",
        _plan_sql(sql, eid, is_change))
    return out


# ------------------------------------------------------------------------------------------------ the report
def _schema_lines(inf: dict, hi: bool) -> list[str]:
    R, T = (ROLE_HI, TYPE_HI) if hi else (ROLE_EN, TYPE_EN)
    L = []
    for t in inf["tables"]:
        head = t["name"] + (f" ({', '.join(t['aliases'])})" if t["aliases"] else "")
        cols = []
        for c, info in t["columns"].items():
            bits = [R[r] for r in info["roles"] if r in R]
            if info.get("real_type"):
                bits.append(info["real_type"].upper() + (" NOT NULL" if info.get("not_null") else ""))
            elif info.get("type"):
                bits.append(("shayad " if hi else "probably ") + T[info["type"]])
            if info.get("missing"):
                bits.append("table mein nahi!" if hi else "not in the table!")
            cols.append(f"{c} [{', '.join(bits)}]" if bits else c)
        mark = (" ✓ (aapki tables se pakka)" if hi else " ✓ (checked against your tables)") if t["verified"] else \
            (" (andaza, verified=false)" if hi else " (inferred, verified=false)")
        L.append(f"{head}{mark}: " + ("; ".join(cols) if cols else ("koi column naam se nahi dikha" if hi else "no columns named")))
    for d in inf.get("derived") or []:
        L.append(f"{d}: " + ("query ke andar bana (CTE / derived), asli table nahi" if hi else "built inside the query (CTE / derived table), not a table"))
    for k in inf["links"]:
        if k.get("verified"):
            L.append(f"{k['left']} = {k['right']}: {k['shape']}" + (" ✓" if True else ""))
        else:
            L.append(f"{k['left']} = {k['right']}: " + (f"naam se andaza {k['guess']} (verified=false)" if hi else
                                                         f"guessed from the names: {k['guess']} (verified=false)"))
    return L


def _equivalence(res: dict, hi: bool) -> str:
    corr = res.get("correct") or {}
    fs = corr.get("findings") or []
    broken = [f for f in fs if f["kind"] in ("error", "wrong")]
    if res.get("rewritten") and broken:
        names = "; ".join(f["title_hi" if hi else "title_en"] for f in broken[:3])
        return ("Ye jaan-boojh ke wahi rows nahi deti: pakki galtiyan theek karti hai (" + names + "). Baaki sab (tables, filters, "
                "grouping, order) waisa hi rakha hai." if hi else
                "It is not meant to return the same rows: it fixes the proven mistakes (" + names + "). Everything else (tables, "
                "filters, grouping, order) is kept as written.")
    kinds = {f["topic"] for f in fs if f["topic"] in SPEED_KINDS}
    if res.get("suggested") and res.get("suggested_kind") == "speed" or kinds and res.get("rewritten"):
        bits = []
        if "sargable" in kinds:
            bits.append("condition column se hata ke values pe gayi: har value ke liye wahi rows rehti hain (NULL dono mein bahar)" if hi else
                        "the condition moved off the column onto the values: for every value of the column the same rows pass "
                        "(a NULL fails both forms)")
        if "or_union" in kinds:
            bits.append("har UNION ALL hissa OR ki ek taraf ki rows deta hai, aur doosra hissa pehle wale ki rows chhodta hai: koi row na "
                        "chhooti na dohrayi" if hi else
                        "each UNION ALL branch keeps one side of the OR, and the later branch leaves out rows the earlier one "
                        "returned: no row lost, none repeated")
        if "keyset" in kinds:
            bits.append("wahi ORDER BY, aakhri column unique, isliye 'aakhri dekhi row ke baad' theek agla page hai" if hi else
                        "the same ORDER BY ending in a unique column, so 'after the last row seen' is exactly the next page")
        from app.agents import sqlequiv

        ex = sqlequiv.explain(res, hi)                # night 14: aspect by aspect, with the facts it needs
        if bits:
            return ("Wahi rows kyun: " if hi else "Why it returns the same rows: ") + "; ".join(bits) + "." + ("\n\n" + ex if ex else "")
        if ex:
            return ex
    if res.get("suggested"):
        names = "; ".join(f["title_hi" if hi else "title_en"] for f in fs if f["kind"] == "check")[:300]
        return ("Ye wahi rows nahi deti: ye tab ke liye hai jab aapka matlab ye tha (" + names + "). Agar abhi wala result hi chahiye "
                "to apni query rakho." if hi else
                "It does not return the same rows: it is the query for what you may have meant (" + names + "). If the current "
                "result is what you want, keep yours.")
    return ""


def report(sql: str, res: dict, hi: bool = False) -> str:
    """The query-only report, in the order a careful reviewer gives it."""
    rep = res.get("query_only") or {}
    if not rep:
        return ""
    fs = _all_findings(res)
    by = {k: [f for f in fs if (f.get("confidence") or {}).get("class") == k] for k in rules()["order"]}
    P = []
    reading = (res.get("reading") or {}).get("hi" if hi else "en") or ""
    if reading:
        P.append(("Ye kya karti lagti hai:\n" if hi else "What it appears to do:\n") + reading)
    heads = {"proven": ("Query se pakki galtiyan:", "Proven issues (from the query alone):"),
             "likely_risk": ("Shayad risk (data ya matlab pe tika):", "Likely risks (depend on the data or what you meant):"),
             "needs_schema": ("Table structure se tay hoga:", "Depends on your table structure:"),
             "needs_execution": ("Speed ke risk (EXPLAIN se pakka hoga):", "Performance risks (EXPLAIN decides):")}
    for k in rules()["order"]:
        if by[k]:
            lines = [f"- {f['title_hi' if hi else 'title_en']}" + (" ✓" if f["confidence"]["verified"] else "") for f in by[k][:8]]
            P.append(heads[k][0 if hi else 1] + "\n" + "\n".join(lines))
    if not fs:
        P.append("Query text mein koi galti ya risk nahi mila." if hi else "No mistake or risk found in the query text.")
    inf = rep.get("inferred_schema") or {}
    if inf.get("tables") or inf.get("links"):
        P.append(("Query se andaza lagaya schema (sab verified=false jab tak tables na mile):\n" if hi else
                  "Inferred schema (from the query; verified=false until your tables say otherwise):\n")
                 + "\n".join(f"- {x}" for x in _schema_lines(inf, hi)))
    cp = rep.get("cant_prove") or []
    if cp:
        P.append(("Sirf query se ye pakka nahi ho sakta (assumptions, check karo):\n" if hi else
                  "What can't be proven from the query alone (assumptions to verify):\n")
                 + "\n".join(f"- {x['hi' if hi else 'en']}" for x in cp))
    fix = (res.get("correct") or {}).get("corrected") or res.get("rewritten") or res.get("suggested")
    if fix:
        P.append(("Sujhaayi query:\n\n" if hi else "Suggested rewrite:\n\n") + fix)
        eq = _equivalence(res, hi)
        if eq:
            P.append(eq)
    checks = []
    for x in cp:
        for c in x.get("check") or []:
            line = f"-- {c['engine']}\n{c['sql']};"
            if line not in checks:
                checks.append(line)
    if checks:
        P.append(("Schema / EXPLAIN milne pe ye chalao (sirf padhti hain), phir paste karo, main findings pakki ya khatam kar dunga:\n\n"
                  if hi else "What to check once you can reach the database (read-only); paste the output and I'll confirm or drop "
                  "each finding:\n\n") + "\n\n".join(checks[:8]))
    return "\n\n".join(P)


def _all_findings(res: dict) -> list:
    corr = (res.get("correct") or {}).get("findings") or []
    seen = {id(f) for f in corr}
    speed = [f for f in (res.get("speed_later") or {}).get("findings") or []] + [f for f in res.get("findings") or []
                                                                                  if "kind" not in f or not f.get("kind")]
    return list(corr) + [f for f in speed if id(f) not in seen]


def annotate(sql: str, res: dict, sch: dict | None = None) -> dict:
    """Classes on every finding, the inferred schema, what can't be proven, and the report text (en / hi) on the result."""
    if not res.get("ok", True) or not sql.strip():
        return res
    from app.agents import sqlschema

    corr = (res.get("correct") or {})
    try:
        from app.agents import sqlequiv

        sqlequiv.annotate(res)                        # night 14: what each same-rows rewrite keeps, and on which facts
    except Exception:  # noqa: BLE001 - the contract is an extra
        pass
    amap = _amap(sql)
    annotate_findings(corr.get("findings"), sql, amap)
    annotate_findings((res.get("speed_later") or {}).get("findings"), sql, amap)
    shown = res.get("findings") or []
    by_title = {f["title_en"]: f.get("confidence") for f in corr.get("findings") or []}
    for f in shown:                                   # the card's copies of the correctness findings carry the same class
        if isinstance(f, dict) and "confidence" not in f and by_title.get(f.get("title_en")):
            f["confidence"] = by_title[f["title_en"]]
    annotate_findings(shown, sql, amap)
    if sqlschema.is_schema(sql) or re.match(r"\s*(create|delimiter|do)\b", C._mask(sql), re.I):
        return res
    try:
        inf = infer_schema(sql, sch)
    except Exception:  # noqa: BLE001 - the inferred schema is an extra; never let it break the check
        return res
    change = bool(re.match(r"[\s(]*(update|delete|insert|replace)\b", C._mask(sql), re.I))
    sure = [f for f in corr.get("findings") or [] if f.get("kind") in ("error", "wrong")]
    plan_of = corr.get("corrected") if sure and corr.get("corrected") and not re.search(r"/\* check|-- check", corr["corrected"]) else sql
    res["query_only"] = {"inferred_schema": inf, "cant_prove": cant_prove(plan_of, inf, _all_findings(res), change),
                         "counts": {k: sum((f.get("confidence") or {}).get("class") == k for f in _all_findings(res)) for k in rules()["order"]}}
    res["query_only"]["en"] = report(sql, res, False)
    res["query_only"]["hi"] = report(sql, res, True)
    return res


def counts_line(res: dict, hi: bool) -> str:
    """One short line: how many findings are sure from the text, and how many wait on the data, the tables or a plan."""
    c = (res.get("query_only") or {}).get("counts") or {}
    if not any(c.values()):
        return ""
    names = [("proven", "pakki", "proven from the query"), ("likely_risk", "shayad risk", "likely risk"),
             ("needs_schema", "table structure pe tiki", "need your table structure"), ("needs_execution", "EXPLAIN pe tiki", "need EXPLAIN")]
    bits = [f"{c[k]} {h if hi else e}" for k, h, e in names if c.get(k)]
    return ("Kitna pakka: " if hi else "How sure: ") + ", ".join(bits) + "."
