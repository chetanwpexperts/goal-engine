"""Checking a query against the project's own schema (night 10B; fundamental 'schema_facts' in
app/dbknow/core/query_fundamentals.json): a finding that depends on a fact about the tables (can this column be NULL? is
this a key? does it hold a time of day? is there an index?) is only a risk while that fact is unknown. With the schema the
project creates (app/agents/sqlproject.py) the fact is looked up: the finding is either upgraded to verified, with the
fact and the file:line that says it, or dropped, with the reason. Nothing about a table that was only partly read is
decided. New findings only the schema can prove: a column the table doesn't have, two columns of different kinds compared
in a join, a text column compared with a number, and the index a filter would need (what exists, what is missing).

How a rule asks: it builds its Finding as before and calls holds(finding, needs) before keeping it. Without a schema
holds() only records the needs and returns True, so every rule works exactly as it did. With a schema, a need the schema
rules out returns False (the rule drops the finding and its rewrite, so the corrected query no longer carries it).

needs: [(fact, table-or-alias-or-None, column or [columns])]
  not_null  - the finding can only happen if the column can be NULL
  has_time  - only if the column holds a time of day (DATETIME / TIMESTAMP, not DATE)
  unique    - only if the columns are NOT a key (PRIMARY KEY / UNIQUE) of their table
  to_one    - only if the join to this alias can match several rows (its ON doesn't use one of its keys); column unused
  index     - a speed finding: the schema says which index there is (never drops; adds what exists or is missing)
"""

import contextvars
import difflib
import re

from app.agents import sqlproject as P

# the engines each schema fact was reproduced on (tests/practice/test_night10b.py, LAB) -- standing rule B; the engine-specific
# behaviour is in the dialect files' "schema_facts"
PROVEN = {k: ["mariadb", "postgresql"] for k in ("schema_column", "schema_table", "join_types", "implicit_cast", "index_missing",
                                                 "not_null", "has_time", "unique", "to_one", "index")}

SCHEMA: contextvars.ContextVar = contextvars.ContextVar("dbaba_schema", default=None)
ALIASES: contextvars.ContextVar = contextvars.ContextVar("dbaba_aliases", default=None)
DROPPED: contextvars.ContextVar = contextvars.ContextVar("dbaba_dropped", default=None)
QUERY: contextvars.ContextVar = contextvars.ContextVar("dbaba_query", default="")

NAME = r"(?:`[^`]+`|\"[^\"]+\"|[A-Za-z_][\w$]*)"
CLAUSE_END = r"(?=\b(?:where|group|order|limit|having|union|on|using|join|inner|left|right|full|cross|natural|window|for|returning|set|values|offset|fetch)\b|\)|;|$)"
NOT_TABLE = {"select", "lateral", "unnest", "dual", "json_table", "generate_series", "only"}
FAMILY_WORD = {"number": "a number", "text": "text", "date": "a date", "time": "a date-time", "other": "another type"}


# ------------------------------------------------------------------------------------------------ reading the query
def _mask(sql: str) -> str:
    from app.agents.sqlcorrect import _mask as m

    return m(sql)


def aliases(sql: str) -> dict:
    """{alias or table name (lower case): table name} for every FROM / JOIN / UPDATE / INTO source of the query, nested ones
    included; CTE names and derived tables map to None (they are not tables of the schema)."""
    s = _mask(sql)
    out = {}
    ctes = {m.group(1).lower() for m in re.finditer(r"(?:\bwith\s+(?:recursive\s+)?|,\s*)([A-Za-z_]\w*)\s*(?:\([^()]*\)\s*)?as\s*"
                                                     r"(?:not\s+)?(?:materialized\s+)?\(", s, re.I)}
    for m in re.finditer(r"\b(from|join|update|into|delete\s+from)\s+", s, re.I):
        if m.group(1).lower() == "from" and _in_call(s, m.start()):
            continue                                     # EXTRACT(YEAR FROM d), SUBSTRING(x FROM 2), TRIM(BOTH ' ' FROM x)
        if m.group(1).lower() == "update" and re.search(r"\b(key|do)\s+$", s[max(0, m.start() - 8):m.start()], re.I):
            continue                                     # ON DUPLICATE KEY UPDATE col = ..., ON CONFLICT ... DO UPDATE SET
        i = m.end()
        while True:
            seg = s[i:]
            sm = re.match(rf"\s*(?:only\s+)?((?:{NAME}\.)?{NAME})(?:\s+(?:as\s+)?(?!(?:where|group|order|limit|having|union|on|using|join|"
                          rf"inner|left|right|full|cross|natural|set|values|window|for|returning|offset|fetch|straight_join|"
                          rf"lateral|partition|use|force|ignore|tablesample)\b)({NAME}))?", seg, re.I)
            if not sm or seg.lstrip().startswith("("):
                dm = re.match(r"\s*\(", seg)
                if dm:                                           # a derived table: (SELECT ...) alias
                    from app.agents.sqlcorrect import _close

                    k = _close(s, i + dm.end() - 1)
                    am = re.match(rf"\s*(?:as\s+)?({NAME})", s[k + 1:], re.I)
                    if am:
                        out[P.uq(am.group(1))] = None
                break
            raw = sql[i + sm.start(1):i + sm.end(1)]
            name = P.uq(raw)
            if name in NOT_TABLE:
                break
            tbl = None if name in ctes else name
            out.setdefault(name, tbl)
            if sm.group(2):
                out[P.uq(sm.group(2))] = tbl
            i += sm.end()
            cm = re.match(r"\s*,", s[i:])
            if not cm or m.group(1).lower() not in ("from", "update", "delete from"):
                break
            i += cm.end()
    for c in ctes:
        out[c] = None
    return out


CALL_FROM = re.compile(r"(extract|substring|substr|trim|overlay|position)\s*\($", re.I)


def _in_call(s: str, pos: int) -> bool:
    """Is this FROM inside EXTRACT( / SUBSTRING( / TRIM( / OVERLAY( / POSITION( (a word of the function, not a table list)?"""
    d = 0
    for i in range(pos - 1, -1, -1):
        if s[i] == ")":
            d += 1
        elif s[i] == "(":
            if d == 0:
                return bool(CALL_FROM.search(s[max(0, i - 12):i + 1]))
            d -= 1
    return False


def _sources(amap: dict) -> set:
    return {t for t in amap.values() if t}


def resolve(q: str | None, col: str, amap: dict | None = None, sch: dict | None = None):
    """(table dict, column dict) for 'q.col' or a bare 'col', or (None, None) when it can't be said for sure."""
    sch = sch or SCHEMA.get()
    amap = amap if amap is not None else (ALIASES.get() or {})
    if not sch:
        return None, None
    if q:
        tn = amap.get(P.uq(q), P.uq(q) if P.uq(q) in sch["tables"] and P.uq(q) not in amap else None)
        if not tn:
            return None, None
        t = P.table(sch, tn)
        return (t, t["cols"].get(P.uq(col))) if t else (None, None)
    hits = [P.table(sch, tn) for tn in _sources(amap) if P.table(sch, tn) and P.uq(col) in P.table(sch, tn)["cols"]]
    if any(t is None for t in [P.table(sch, tn) for tn in _sources(amap)]) or any(v is None for v in amap.values()):
        return (hits[0], hits[0]["cols"][P.uq(col)]) if len(hits) == 1 and len(_sources(amap)) == 1 else (None, None)
    return (hits[0], hits[0]["cols"][P.uq(col)]) if len(hits) == 1 else (None, None)


def _where(t: dict, c: dict | None = None) -> str:
    return (c or {}).get("at") or t.get("at") or ""


def _join_on(sql: str, alias: str) -> str | None:
    """The ON condition of the join that brings in this alias."""
    s = _mask(sql)
    m = re.search(rf"\bjoin\s+(?:{NAME}\.)?{NAME}(?:\s+(?:as\s+)?)?(?:\s*{re.escape(alias)}|(?<=\b{re.escape(alias)}))\s+on\b", s, re.I)
    if not m:
        m = re.search(rf"\bjoin\s+[`\"]?{re.escape(alias)}[`\"]?\s+on\b", s, re.I)
    if not m:
        return None
    rest = s[m.end():]
    e = re.search(r"\b(?:join|inner|left|right|full|cross|natural|where|group|order|limit|having|union|window)\b|\)|;", rest, re.I)
    return sql[m.end():m.end() + (e.start() if e else len(rest))]


# ------------------------------------------------------------------------------------------------ the rules' question
def holds(fnd, needs: list) -> bool:
    """Keep this finding? Records its needs on it; False only when the project's schema rules it out."""
    fnd.needs = [list(n) for n in needs]
    sch = SCHEMA.get()
    if not sch:
        return True
    out = judge(fnd.needs, QUERY.get(), sch)
    if out["ruled_out"]:
        lst = DROPPED.get()
        if lst is not None:
            lst.append({"topic": fnd.topic, "title_en": fnd.title_en, "title_hi": fnd.title_hi, "why_en": out["en"], "why_hi": out["hi"]})
        return False
    return True


def column_family(q: str | None, col: str) -> str | None:
    """number / text / date / time / other for a column the query names, when the project's schema is known (else None)."""
    sch = SCHEMA.get()
    if not sch:
        return None
    t, c = resolve(q, col, aliases(QUERY.get()), sch)
    return P.family(c["type"]) if c and not t["partial"] else None


def keep(pair, needs: list) -> list:
    """[pair] when the finding holds (see holds), [] when the project's schema rules it out: out += keep((F(...), edit), needs)."""
    return [pair] if holds(pair[0], needs) else []


def judge(needs: list, sql: str, sch: dict) -> dict:
    """{'ruled_out': bool, 'verified': bool, 'en', 'hi'}: what the schema says about these needs."""
    amap = aliases(sql)
    say_en, say_hi, verified = [], [], False
    for fact, q, col in needs:
        if fact == "to_one":
            tn = amap.get(P.uq(q or ""))
            t = P.table(sch, tn) if tn else None
            on = _join_on(sql, q or "")
            if not t or t["partial"] or not on:
                continue
            mine = [P.uq(m.group(2)) for m in re.finditer(rf"({NAME})\s*\.\s*({NAME})\s*=", on) if P.uq(m.group(1)) == P.uq(q)]
            mine += [P.uq(m.group(2)) for m in re.finditer(rf"=\s*({NAME})\s*\.\s*({NAME})", on) if P.uq(m.group(1)) == P.uq(q)]
            if not mine:
                continue
            if P.is_unique(t, mine):
                return {"ruled_out": True, "verified": True,
                        "en": f"In the project's schema ({_where(t)}), {', '.join(mine)} is a key of {t['name']}, so each row matches at "
                              f"most one {t['name']} row: the join makes no copies.",
                        "hi": f"Project ke schema ({_where(t)}) mein {', '.join(mine)} {t['name']} ki key hai, isliye har row ko zyada se "
                              f"zyada ek {t['name']} row milti hai: join copies nahi banata."}
            verified = True
            say_en.append(f"{t['name']}.{', '.join(mine)} is not a key in the project's schema ({_where(t)}), so a row can match several "
                          f"{t['name']} rows.")
            say_hi.append(f"Project ke schema ({_where(t)}) mein {t['name']}.{', '.join(mine)} key nahi hai, isliye ek row kai "
                          f"{t['name']} rows se jud sakti hai.")
            continue
        cols = col if isinstance(col, list) else [col]
        if fact == "unique":
            tn = amap.get(P.uq(q)) if q else (next(iter(_sources(amap))) if len(_sources(amap)) == 1 else None)
            t = P.table(sch, tn) if tn else None
            if not t or t["partial"] or not all(P.uq(c) in t["cols"] for c in cols):
                continue
            if P.is_unique(t, cols):
                return {"ruled_out": True, "verified": True,
                        "en": f"In the project's schema ({_where(t)}), ({', '.join(cols)}) is a key of {t['name']}: no two rows share it, "
                              f"so there are no ties and nothing repeats.",
                        "hi": f"Project ke schema ({_where(t)}) mein ({', '.join(cols)}) {t['name']} ki key hai: do rows mein same nahi ho "
                              f"sakta, isliye barabari ya dohraav nahi."}
            verified = True
            say_en.append(f"({', '.join(cols)}) is not a key of {t['name']} in the project's schema ({_where(t)}): several rows can share it.")
            say_hi.append(f"Project ke schema ({_where(t)}) mein ({', '.join(cols)}) {t['name']} ki key nahi hai: kai rows mein same ho sakta hai.")
            continue
        for c in cols:
            t, cd = resolve(q, c, amap, sch)
            if not t or not cd or t["partial"]:
                continue
            where = _where(t, cd)
            if fact == "not_null":
                if cd["not_null"]:
                    return {"ruled_out": True, "verified": True,
                            "en": f"{t['name']}.{cd['raw']} is NOT NULL in the project's schema ({where}), so it is never NULL here.",
                            "hi": f"Project ke schema ({where}) mein {t['name']}.{cd['raw']} NOT NULL hai, isliye yahan kabhi NULL nahi."}
                verified = True
                say_en.append(f"{t['name']}.{cd['raw']} can be NULL in the project's schema ({where}).")
                say_hi.append(f"Project ke schema ({where}) mein {t['name']}.{cd['raw']} NULL ho sakta hai.")
            elif fact == "has_time":
                fam = P.family(cd["type"])
                if fam == "date":
                    return {"ruled_out": True, "verified": True,
                            "en": f"{t['name']}.{cd['raw']} is a {cd['type'].upper()} in the project's schema ({where}): no time of day, so "
                                  f"the last day is covered whole.",
                            "hi": f"Project ke schema ({where}) mein {t['name']}.{cd['raw']} {cd['type'].upper()} hai: time nahi hota, "
                                  f"isliye aakhri din poora aata hai."}
                if fam == "time":
                    verified = True
                    say_en.append(f"{t['name']}.{cd['raw']} is a {cd['type'].upper()} in the project's schema ({where}): it holds a time of day.")
                    say_hi.append(f"Project ke schema ({where}) mein {t['name']}.{cd['raw']} {cd['type'].upper()} hai: isme time bhi hota hai.")
            elif fact == "index":
                fam = _family()
                ixs = P.indexes_leading(t, cd["name"], fam)
                verified = True
                if ixs:
                    ix = ixs[0]
                    nm = ix["name"] or "an index"
                    say_en.append(f"The project's schema has {nm} ({', '.join(ix['cols'])}) on {t['name']}, which starts with {cd['raw']}: "
                                  f"with the condition on the bare column it can be used.")
                    say_hi.append(f"Project ke schema mein {t['name']} pe {nm} ({', '.join(ix['cols'])}) hai jo {cd['raw']} se shuru hota "
                                  f"hai: column seedha likhne par wo kaam aayega.")
                else:
                    from app.agents.sqlspeed import index_ddl

                    ddl = index_ddl(t["name"], f"ix_{t['name']}_{cd['name']}", [cd["name"]], fam)
                    say_en.append(f"No index in the project's schema starts with {t['name']}.{cd['raw']}, so even the rewrite reads the whole "
                                  f"table; on a big table add one: {ddl}")
                    say_hi.append(f"Project ke schema mein {t['name']}.{cd['raw']} se shuru hone wala koi index nahi, isliye rewrite ke baad "
                                  f"bhi poori table padhi jaayegi; badi table pe index lagao: {ddl}")
    return {"ruled_out": False, "verified": verified, "en": " ".join(dict.fromkeys(say_en)), "hi": " ".join(dict.fromkeys(say_hi))}


def _family():
    from app.agents.sqlbasics import CHOSEN
    from app.dbknow import engines as E

    return E.family(CHOSEN.get()) if CHOSEN.get() else None


# ------------------------------------------------------------------------------------------------ checking with a schema
def check(sql: str, sch: dict | None, intent: str = "", engine: str | None = None) -> dict:
    """sqlcorrect.check, with the project's schema deciding what depends on it (see the module text)."""
    from app.agents import sqlcorrect

    if not sch or not sch.get("tables") or not re.match(r"[\s(]*(select|with|update|delete|insert|replace)\b", _mask(sql), re.I):
        return sqlcorrect.check(sql, intent=intent, engine=engine)
    dropped: list = []
    toks = [SCHEMA.set(sch), DROPPED.set(dropped), QUERY.set(sql)]
    try:
        res = sqlcorrect.check(sql, intent=intent, engine=engine)
        if engine:
            from app.agents.sqlbasics import CHOSEN

            tok = CHOSEN.set(engine)
            try:
                return annotate(sql, res, sch, dropped)
            finally:
                CHOSEN.reset(tok)
        return annotate(sql, res, sch, dropped)
    finally:
        for v, t in zip((QUERY, DROPPED, SCHEMA), reversed(toks)):
            v.reset(t)


def annotate(sql: str, res: dict, sch: dict, dropped: list) -> dict:
    res = dict(res)
    seen = set()
    res["dropped"] = [d for d in dropped if (d["title_en"], d["why_en"]) not in seen and not seen.add((d["title_en"], d["why_en"]))]
    amap = aliases(sql)
    for f in res.get("findings", []):
        needs = f.get("needs")
        if not needs:
            continue
        j = judge(needs, sql, sch)
        if j["verified"] and j["en"]:
            f["schema"] = "verified"
            f["schema_en"], f["schema_hi"] = j["en"], j["hi"]
            f["fix_en"] = (f.get("fix_en") or "") + " Project schema: " + j["en"]
            f["fix_hi"] = (f.get("fix_hi") or "") + " Project schema: " + j["hi"]
    told = set()                                  # columns whose index is already spoken of (a speed finding's own note)
    for f in res.get("findings", []):
        for fact, q, col in f.get("needs") or []:
            if fact == "index":
                t, c = resolve(q, col, amap, sch)
                if t and c:
                    told.add((t["name"], c["name"]))
    new = names(sql, amap, sch) + join_types(sql, amap, sch) + _index_findings(sql, amap, sch, told)
    have = {(f["topic"], f["evidence"]) for f in res.get("findings", [])}
    new = [f for f in new if (f["topic"], f["evidence"]) not in have]
    renames = [f.pop("rename") for f in new if f.get("rename")]
    if renames:
        base = res.get("corrected") or sql
        for old, newname in renames:
            base = re.sub(rf"(?<![\w$.`\"]){re.escape(old)}(?![\w$`\"])", newname, base)
        res["corrected"] = base
    if new:
        order = {"error": 0, "wrong": 1, "check": 2}
        res["findings"] = sorted(list(res.get("findings", [])) + new, key=lambda f: order[f["kind"]])
    res["schema_summary"] = P.summary(sch)
    return _yours(res) if sch.get("origin") == "paste" else res


WORDS = [("In the project's schema", "In the tables you gave"), ("in the project's schema", "in the tables you gave"),
         ("the project's schema", "the tables you gave"), ("Project schema:", "Your tables:"), ("Project ke schema", "Aapki di hui tables"),
         ("project ke schema", "aapki di hui tables"), ("Project ke schema mein", "Aapki di hui tables mein")]


def _yours(res: dict) -> dict:
    """Tables pasted in the chat are 'the tables you gave', not a project's schema."""
    def fix(t):
        for a, b in WORDS:
            t = t.replace(a, b)
        return t
    for f in list(res.get("findings") or []) + list(res.get("dropped") or []):
        for k, v in list(f.items()):
            if isinstance(v, str):
                f[k] = fix(v)
    return res


def _finding(kind, topic, ten, thi, ev, fen, fhi) -> dict:
    return {"kind": kind, "topic": topic, "title_en": ten, "title_hi": thi, "evidence": ev, "fix_en": fen, "fix_hi": fhi,
            "schema": "verified"}


KEYWORDS = None


def _kw() -> set:
    global KEYWORDS
    if KEYWORDS is None:
        from app.agents.sqlcorrect import KW

        KEYWORDS = set(KW) | {"true", "false", "null", "unknown", "current_date", "current_timestamp", "current_time", "now", "localtime",
                              "localtimestamp", "interval", "day", "month", "year", "hour", "minute", "second", "week", "quarter",
                              "asc", "desc", "nulls", "first", "last", "distinct", "case", "when", "then", "else", "end", "and", "or",
                              "not", "in", "is", "like", "ilike", "between", "exists", "any", "some", "all", "escape", "rows", "range",
                              "unbounded", "preceding", "following", "current", "row", "over", "partition", "filter", "within", "date",
                              "time", "timestamp", "binary", "collate", "user", "rownum", "sysdate", "default", "separator", "using",
                              "with", "recursive", "lateral", "values", "returning", "set", "into", "only", "of", "nowait", "skip",
                              "locked", "share", "update", "for", "lock", "mode", "key", "rollup", "cube", "grouping", "sets",
                              "materialized", "ties", "fetch", "next", "offset", "percent", "dual", "public", "char", "varchar",
                              "int", "integer", "signed", "unsigned", "decimal", "numeric", "text", "boolean", "double", "float",
                              "real", "bigint", "json", "jsonb", "uuid", "precision", "zone", "at", "epoch", "dow", "doy",
                              "microsecond", "isodow", "century", "decade", "millennium", "both", "leading", "trailing", "from"}
    return KEYWORDS


def names(sql: str, amap: dict, sch: dict) -> list:
    """A column the query names that its table doesn't have (PROVEN against the project's schema), and a table the files
    never create (a check: it may come from somewhere the files don't show)."""
    out = []
    s = _mask(sql)
    tables = _sources(amap)
    for tn in sorted(tables):
        if not P.table(sch, tn):
            close = difflib.get_close_matches(tn, list(sch["tables"]), n=1, cutoff=0.75)
            if not close:
                continue                       # a table from elsewhere (a CMS's own tables, another service): nothing to say
            hint = f" Did you mean {close[0]}?"
            out.append(_finding("check", "schema_table", f"{tn} isn't created in the project's files",
                                f"{tn} project ki files mein kahin nahi banti", tn,
                                f"None of the schema files read ({P.summary(sch)}) creates a table called {tn}.{hint} If it comes from "
                                f"elsewhere (a package, another service, made by hand), it is fine; otherwise the database will refuse the "
                                f"query (MySQL/MariaDB error 1146, PostgreSQL 42P01).",
                                f"Padhi gayi schema files ({P.summary(sch)}) mein {tn} naam ki table nahi banti.{hint} Agar wo kahin aur se "
                                f"aati hai to theek; warna database query mana karega (1146 / 42P01)."))
    seen = set()
    for m in re.finditer(rf"(?<![\w$.`\"])({NAME})\s*\.\s*({NAME}|\*)(?![\w$`\"(])", s):
        q, c = P.uq(m.group(1)), P.uq(m.group(2))
        if c == "*" or (q, c) in seen or q not in amap:
            continue
        seen.add((q, c))
        t = P.table(sch, amap[q]) if amap[q] else None
        if not t or t["partial"] or c in t["cols"]:
            continue
        out.append(_missing(sql, m.group(0), t, c))
    if any(v is None for v in amap.values()) or not tables or not all(P.table(sch, tn) and not P.table(sch, tn)["partial"] for tn in tables):
        return out
    if re.search(r"\(\s*select\b", s, re.I) or re.search(r"\bwith\b", s, re.I) or re.search(r"\b(union|intersect|except)\b", s, re.I):
        return out
    out_aliases = {P.uq(m.group(1)) for m in re.finditer(rf"\bas\s+({NAME})", s, re.I)}
    out_aliases |= {P.uq(m.group(2)) for m in re.finditer(rf"([\w)'\"`]+)\s+({NAME})\s*(?:,|\bfrom\b)", s, re.I)
                    if m.group(1).lower() not in _kw()}
    have = set()
    for tn in tables:
        have |= set(P.table(sch, tn)["cols"])
    bare_spots = re.finditer(rf"(?<![\w$.`\"@:])({NAME})(?=\s*(?:=|<>|!=|<=|>=|<|>|\bis\b|\bin\b|\blike\b|\bilike\b|\bbetween\b|\bnot\b)"
                             rf"|\s*(?:,|$|\basc\b|\bdesc\b|\bfrom\b|;))", s, re.I)
    for m in bare_spots:
        w = P.uq(m.group(1))
        prev = s[:m.start()].rstrip()
        if w in _kw() or w in amap or w in out_aliases or w in have or (w, None) in seen or not re.match(r"[A-Za-z_`\"]", m.group(1)):
            continue
        if re.search(r"\b(as|interval|into|table|index|on\s+\w+|using)\s*$", prev, re.I) or prev.endswith((".", "::", ":", "@", "$")):
            continue
        if re.match(r"\s*\(", s[m.end():]):
            continue
        seen.add((w, None))
        owner = [P.table(sch, tn) for tn in tables]
        t = owner[0] if len(owner) == 1 else None
        out.append(_missing(sql, m.group(1), t, w, tables=[o["name"] for o in owner]))
    return out


def _missing(sql: str, ref: str, t: dict | None, c: str, tables: list | None = None) -> dict:
    sch = SCHEMA.get() or {}
    pool = list(t["cols"]) if t else sorted({x for tn in (tables or []) for x in (P.table(sch, tn) or {"cols": {}})["cols"]})
    close = difflib.get_close_matches(c, pool, n=1, cutoff=0.6)
    hint_en = f" Did you mean {close[0]}?" if close else ""
    hint_hi = f" Kya aapka matlab {close[0]} tha?" if close else ""
    where = f"{t['name']} ({t['at']})" if t else ", ".join(tables or [])
    pg, my = "PostgreSQL 42703", "MySQL/MariaDB 1054"
    f = _finding("error", "schema_column", f"{ref.strip()}: {t['name'] if t else 'these tables'} has no column {c}",
                    f"{ref.strip()}: {t['name'] if t else 'in tables'} mein {c} column nahi hai", ref.strip(),
                    f"In the project's schema, {where} has no column called {c} (columns: {', '.join(pool[:12])}"
                    f"{', ...' if len(pool) > 12 else ''}).{hint_en} The database refuses the query: {my} Unknown column, {pg}.",
                    f"Project ke schema mein {where} mein {c} naam ka column nahi hai.{hint_hi} Database query mana karega ({my}, {pg}).")
    if close:
        r = ref.strip()
        f["rename"] = (r, (r.rsplit(".", 1)[0] + "." if "." in r else "") + (sch["tables"][t["name"]]["cols"][close[0]]["raw"] if t else close[0]))
    return f


def join_types(sql: str, amap: dict, sch: dict) -> list:
    """ON a.x = b.y where one is a number and the other text: PostgreSQL refuses it (42883); MySQL/MariaDB convert every
    row to a number, so '01', ' 1' and '1abc' all match 1 (lab) and the index on the text side can't be used."""
    out = []
    s = _mask(sql)
    fam = _family()
    for m in re.finditer(rf"({NAME})\s*\.\s*({NAME})\s*=\s*({NAME})\s*\.\s*({NAME})", s):
        t1, c1 = resolve(m.group(1), m.group(2), amap, sch)
        t2, c2 = resolve(m.group(3), m.group(4), amap, sch)
        if not (c1 and c2) or t1["partial"] or t2["partial"]:
            continue
        f1, f2 = P.family(c1["type"]), P.family(c2["type"])
        if {f1, f2} != {"number", "text"}:
            continue
        num, txt = (c1, c2) if f1 == "number" else (c2, c1)
        tn, tt = (t1, t2) if f1 == "number" else (t2, t1)
        ev = sql[m.start():m.end()]
        pg = fam == "postgresql"
        kind = "error" if pg else "check"
        cast = (f"{tt['name']}.{txt['raw']} = CAST({tn['name']}.{num['raw']} AS CHAR)" if fam in ("mysql", None) else
                f"{tt['name']}.{txt['raw']} = {tn['name']}.{num['raw']}::text")
        out.append(_finding(kind, "join_types", f"{ev}: a number compared with text",
                            f"{ev}: number aur text ka mel", ev,
                            f"In the project's schema {tn['name']}.{num['raw']} is {num['type'].upper()} ({num['at']}) and "
                            f"{tt['name']}.{txt['raw']} is {txt['type'].upper()} ({txt['at']}). PostgreSQL refuses this join (42883 "
                            f"operator does not exist). MySQL/MariaDB turn every text value into a number first, so '01', ' 1' and '1abc' "
                            f"all match 1 (with warning 1292), and no index on the text column can be used. Best: give both columns the "
                            f"same type (the key's type); until then compare as text on both sides: {cast}.",
                            f"Project ke schema mein {tn['name']}.{num['raw']} {num['type'].upper()} hai aur {tt['name']}.{txt['raw']} "
                            f"{txt['type'].upper()}. PostgreSQL ye join mana karta hai (42883). MySQL/MariaDB har text ko number bana dete "
                            f"hain, isliye '01', ' 1', '1abc' sab 1 se mil jaate hain, aur text column ka index use nahi hota. Dono "
                            f"columns ka type ek karo; tab tak dono taraf text: {cast}."))
    return out


def _index_findings(sql: str, amap: dict, sch: dict, told: set = frozenset()) -> list:
    """What exists and what is missing for this query's filters, from the schema: the existing index reader (a text column
    compared with a number, an index whose first column isn't filtered, a range before '=') plus a filter on a column no
    index starts with."""
    from app.agents.sqlspeed import index_ddl, index_fit

    fam = _family()
    out = []
    fitted = set()
    for d in index_fit(sql, P.ddl(sch), fam):
        fitted.add(d["evidence"].split(":")[0].strip().lower())
        fitted.add(d["title_en"].split()[0].lower())
        kind = "error" if d["topic"] == "implicit_cast" and fam == "postgresql" else "check"
        f = _finding(kind, d["topic"], d["title_en"], d["title_hi"], d["evidence"], d["fix_en"], d["fix_hi"])
        if d["topic"] == "implicit_cast":
            nm = re.match(r"(.*?)(-?\d+(?:\.\d+)?)$", d["evidence"])
            if nm and not d["evidence"].endswith("..."):
                f["rename"] = (d["evidence"], f"{nm.group(1)}'{nm.group(2)}'")
        out.append(f)
    s = _mask(sql)
    if not re.match(r"\s*(select|with|update|delete)\b", s, re.I):
        return out
    wm = re.search(r"\bwhere\b(.*?)(?=\b(?:group\s+by|order\s+by|limit|having|union|returning|for\s+update|window)\b|;|$)", s, re.I | re.S)
    if not wm or re.search(r"\(\s*select\b", wm.group(1), re.I):
        return out
    seen_t = set()
    for m in re.finditer(rf"(?<![\w$.`\"])(?:({NAME})\s*\.\s*)?({NAME})\s*(=|\bin\s*\(|\bbetween\b|>=|<=|>|<|\blike\s+'(?!%))", wm.group(1), re.I):
        if re.search(r"\bor\b", wm.group(1), re.I):
            break
        t, c = resolve(m.group(1), m.group(2), amap, sch)
        if not t or not c or t["partial"] or t["name"] in seen_t:
            continue
        if (t["name"], c["name"]) in told or t["name"] in fitted or c["name"] in fitted:
            seen_t.add(t["name"])
            continue
        rhs = wm.group(1)[m.end():].lstrip()
        if re.match(rf"(?:{NAME})\s*\.\s*{NAME}", rhs) and not re.match(r"\d", rhs):
            continue                                                     # a join condition written in WHERE
        seen_t.add(t["name"])
        if P.indexes_leading(t, c["name"], fam) or any(c["name"] in ix["cols"][:1] for ix in t["indexes"]):
            continue
        ddl = index_ddl(t["name"], f"ix_{t['name']}_{c['name']}", [c["name"]], fam)
        others = [f"{ix['name'] or 'unnamed'} ({', '.join(ix['cols'])})" for ix in t["indexes"]] + ([f"PRIMARY KEY ({', '.join(t['pk'])})"]
                                                                                                    if t["pk"] and not any(ix["kind"] == "primary" for ix in t["indexes"]) else [])
        have = f" It has: {'; '.join(others)}." if others else " It has no index at all."
        out.append(_finding("check", "index_missing", f"No index in the project's schema starts with {t['name']}.{c['raw']}",
                            f"Project ke schema mein {t['name']}.{c['raw']} se shuru hone wala index nahi", sql[wm.start(1) + m.start():
                                                                                                          wm.start(1) + m.end()].strip(),
                            f"The filter on {c['raw']} can only be answered by reading every row of {t['name']} ({t['at']}).{have} On a "
                            f"small table that's fine; once it grows, an index that starts with {c['raw']} lets the database jump to the "
                            f"matching rows: {ddl} (A speed risk, not a mistake: EXPLAIN on the real data decides.)",
                            f"{c['raw']} pe filter ke liye {t['name']} ki har row padhni padti hai.{have} Chhoti table pe theek; badi hone par "
                            f"{c['raw']} se shuru hone wala index seedha sahi rows tak le jaata hai: {ddl} (Ye speed ka risk hai, galti "
                            f"nahi: asli data pe EXPLAIN batayega.)"))
    return out


def split_paste(text: str) -> tuple[str, str] | None:
    """CREATE TABLE statements (with their ALTER / CREATE INDEX) and ONE query pasted together: (the schema part, the query).
    None for anything else (a design alone is checked as a design; several queries are code or a script)."""
    from app.agents.sqlroutine import split_script

    if not re.search(r"\bcreate\s+(?:temporary\s+)?table\b", text or "", re.I):
        return None
    ddl, queries = [], []
    for stmt in split_script(text):
        low = _mask(stmt).lower()
        low = re.sub(r"^(\s|--[^\n]*\n|#[^\n]*\n)*", "", low)
        if re.match(r"(create\s+(?:temporary\s+)?table|alter\s+table|create\s+(?:unique\s+)?index|drop\s+table|set\s|use\s)", low):
            ddl.append(stmt.strip())
        elif re.match(r"[\s(]*(select|with|update|delete|insert|replace)\b", low):
            queries.append(stmt.strip())
        elif low.strip():
            return None
    if not ddl or len(queries) != 1 or not any(re.match(r"\s*create\s+(?:temporary\s+)?table", _mask(d), re.I) for d in ddl):
        return None
    return ";\n".join(ddl) + ";", queries[0]


def schema_text(sch: dict) -> str:
    """The schema as DBaba read it, for 'dbaba schema <folder>'."""
    lines = []
    if not sch["tables"]:
        return "No tables found in the project's files (CREATE TABLE scripts, schema dumps, SQL or Laravel migrations)."
    lines.append(f"Schema read: {P.summary(sch)}. Nothing was changed.")
    for t in sch["tables"].values():
        lines.append("")
        lines.append(f"{t['name']}  ({t['at']}){'  [only partly read]' if t['partial'] else ''}")
        for k in t["order"]:
            c = t["cols"][k]
            extra = [x for x in ("PK" if k in t["pk"] else "", "auto" if c["auto"] else "", f"default {c['default']}" if c["default"] else "") if x]
            lines.append(f"  {c['raw']:<24} {c['type'].upper():<20} {'NOT NULL' if c['not_null'] else 'NULL':<9}{'  ' + ', '.join(extra) if extra else ''}")
        for ix in t["indexes"]:
            if ix["kind"] == "primary":
                continue
            lines.append(f"  {'UNIQUE ' if ix['unique'] else ''}{'INDEX' if ix['kind'] != 'fulltext' else 'FULLTEXT'} {ix['name'] or ''} "
                         f"({', '.join(ix['cols'])})")
        for fk in t["fks"]:
            lines.append(f"  FOREIGN KEY ({', '.join(fk['cols'])}) -> {fk['parent']} ({', '.join(fk['pcols'] or ['?'])})")
    if sch["unread"]:
        lines.append("")
        lines.append("Couldn't read completely (not guessed):")
        lines += [f"  {u['at']}  {u['why']}" for u in sch["unread"]]
    return "\n".join(lines)
