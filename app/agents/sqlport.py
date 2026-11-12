"""Portability (night 10 of the curriculum): one SQL language, each database's own words for some things.

The fundamental ('portability'): SQL is a standard, but every database spells some ideas its own way (the first N rows, today's
date, adding days to a date, the position of a text, joining text from many rows, a cast), and some spellings run on several
databases while meaning different things there (date minus date, an interval in weeks, ^, CHAR without a length). A
translation must keep the MEANING (which rows, which value), not just make the text run.

What one engine has, lacks and does differently lives only in its dialect file (app/dbknow/dialects/<engine>.json, 'port'):
the calls it refuses with their rewrite templates, its interval words, its date-format codes and its cast type names. This
file only reasons over them, for the database the person named, chose or that was detected (standing rule A), so each engine
can later become its own sub-agent. Every rule was reproduced on MariaDB 10.11 and PostgreSQL 16
(tests/practice/test_night10.py has the lab table).

Two uses:
- port(): findings for the statement checker. Syntax the chosen database refuses -> 'error' (topic 'dialect') with the
  rewrite in its own words; spellings that run but give other values -> 'wrong' (date_math, interval_unit, cast_char);
  '^' on MySQL/MariaDB -> 'check' (it is XOR there; maybe meant).
- translate(): 'convert this query to PostgreSQL': every rewrite applied, plus the meaning notes a translation must not hide
  (case-sensitive text comparison, integer division, NULL in CONCAT / GREATEST, LENGTH in bytes), and an honest list of what
  couldn't be translated automatically. SQL Server / Oracle / SQLite targets get notes only (no lab-tested rules yet).
Returns [(Finding, [edits])], edit = (start, end, new text) on the original statement.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

DIALECTS = Path(__file__).resolve().parent.parent / "dbknow" / "dialects"

# the date parts people write (SQL Server's short names included), in the standard words both engines read
UNITS = {"year": "YEAR", "years": "YEAR", "yy": "YEAR", "yyyy": "YEAR", "quarter": "QUARTER", "quarters": "QUARTER", "qq": "QUARTER",
         "q": "QUARTER", "month": "MONTH", "months": "MONTH", "mm": "MONTH", "m": "MONTH", "week": "WEEK", "weeks": "WEEK",
         "wk": "WEEK", "ww": "WEEK", "day": "DAY", "days": "DAY", "dd": "DAY", "d": "DAY", "hour": "HOUR", "hours": "HOUR",
         "hh": "HOUR", "minute": "MINUTE", "minutes": "MINUTE", "mi": "MINUTE", "n": "MINUTE", "second": "SECOND",
         "seconds": "SECOND", "ss": "SECOND", "s": "SECOND"}
UNIT_WORD = r"(year|quarter|month|week|day|hour|minute|second)s?"
# a column whose name says it holds a date (no time): order_date, hired_on, dob ...
DATE_NAME = re.compile(r"(?:^|_)(?:date|dob|birthday|birth_date)$|_on$|^(?:day|dt)$|date$", re.I)
TIME_NAME = re.compile(r"_at$|_time$|^(?:created|updated|modified|timestamp|ts)$|_ts$|datetime$", re.I)
OPER = (r"(?:(?:date|timestamp)\s*'[^']*'|'(?:[^'\\]|\\.|'')*'|[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?(?:\s*\((?:[^()]|\([^()]*\))*\))?|\d+(?:\.\d+)?)")


@lru_cache(maxsize=8)
def dialect(fam: str) -> dict:
    return json.loads((DIALECTS / f"{fam}.json").read_text(encoding="utf-8"))


def spec(fam: str) -> dict:
    return dialect(fam).get("port", {})


def _close(s: str, i: int) -> int:
    from app.agents.sqlcorrect import _close as c

    return c(s, i)


def _args(text: str) -> list[str]:
    from app.agents.sqlbasics import _split_args

    return _split_args(text)


def _mask(sql: str) -> str:
    from app.agents.sqlcorrect import _mask as m

    return m(sql)


def _unit(arg: str) -> str | None:
    return UNITS.get(arg.strip().strip("'\"").lower())


def _int(text: str) -> int | None:
    t = text.strip()
    while t.startswith("(") and t.endswith(")") and _close(t, 0) == len(t) - 1:
        t = t[1:-1].strip()
    return int(t) if re.fullmatch(r"-?\d+", t) else None


def interval(fam: str, amount: str, unit: str) -> str:
    """An interval of `amount` (a number or an expression) `unit`s in this database's own words. PostgreSQL's
    INTERVAL '2' WEEK is 2 SECONDS (lab): its qualifier knows no WEEK or QUARTER, so they are written in days / months."""
    iv = spec(fam).get("interval", {})
    to, k = (iv.get("units", {}).get(unit) or [unit, 1])
    n = _int(amount)
    if n is not None:
        return iv["literal"].format(n=n * k, UNIT=to, unit=to.lower())
    return iv["expr"].format(n=amount.strip(), UNIT=to, unit=to.lower(), k=k)


# ---------------------------------------------------------------- date formats: each engine's codes <-> neutral names
def _fmt_tokens(fam: str) -> list[tuple[str, str]]:
    codes = spec(fam).get("date_codes", {})
    return sorted(codes.items(), key=lambda kv: -len(kv[0]))


def convert_format(fmt: str, src: str, dst: str) -> str | None:
    """A date-format string from one engine's codes to another's ('%d/%m/%Y' -> 'DD/MM/YYYY'), or None when a code has
    no exact twin (then nothing is rewritten: a near miss would print other text)."""
    if not (fmt.startswith("'") and fmt.endswith("'")):
        return None
    body = fmt[1:-1]
    src_codes = _fmt_tokens(src)
    back = {v: k for k, v in spec(dst).get("date_codes", {}).items()}
    out, i = [], 0
    pct = src == "mysql"
    while i < len(body):
        hit = next(((c, n) for c, n in src_codes if body.startswith(c, i)), None)
        if hit:
            if hit[1] not in back:
                return None
            out.append(back[hit[1]])
            i += len(hit[0])
            continue
        ch = body[i]
        if pct and ch == "%":
            return None                                   # a MySQL code we don't know
        if not pct and ch.isalpha():
            return None                                   # a PostgreSQL code (or a word) we don't know
        out.append("%%" if (dst == "mysql" and ch == "%") else ch)
        i += 1
    return "'" + "".join(out) + "'"


# ---------------------------------------------------------------- the calls a database refuses, and their rewrites
def _calls(fam: str) -> dict:
    out = {}
    for r in spec(fam).get("calls", []):
        out.setdefault(r["fn"].lower(), []).append(r)
    return out


def _render(tpl: str, args: list[str], fam: str, rule: dict) -> str | None:
    """Fill a template: {0} {1} args, {U0} the date part named by arg 0, {IV:a:u} an interval of arg a in the unit of arg u,
    {IVARG1} arg 1's MySQL 'INTERVAL n UNIT' in this database's words, {F1} arg 1's date format in this database's codes."""
    def unit_of(k: int) -> str | None:
        return _unit(args[k]) if k < len(args) else None

    out = tpl
    for m in list(re.finditer(r"\{IV:(\d+):U(\d+)\}", tpl)):
        u = unit_of(int(m.group(2)))
        if not u:
            return None
        out = out.replace(m.group(0), interval(fam, args[int(m.group(1))], u))
    for m in list(re.finditer(r"\{IVARG(\d+)\}", out)):
        im = re.fullmatch(r"\s*interval\s+(.+?)\s+" + UNIT_WORD + r"\s*", args[int(m.group(1))], re.I | re.S)
        if not im:
            return None
        amount = im.group(1).strip()
        if amount.startswith("'") and amount.endswith("'"):
            amount = amount[1:-1]
        out = out.replace(m.group(0), interval(fam, amount, UNITS[im.group(2).lower()]))
    for m in list(re.finditer(r"\{U(\d+)\}", out)):
        u = unit_of(int(m.group(1)))
        if not u:
            return None
        out = out.replace(m.group(0), u)
    for m in list(re.finditer(r"\{F(\d+)\}", out)):
        f = convert_format(args[int(m.group(1))].strip(), rule["fmt_from"], fam)
        if f is None:
            return None
        out = out.replace(m.group(0), f)
    try:
        return re.sub(r"\{(\d+)\}", lambda mm: args[int(mm.group(1))], out)
    except IndexError:
        return None


def _pick(rule: dict, args: list[str]) -> str | None:
    """The template for these arguments: 'by' picks one by an argument's value (a unit, a literal), '*' for the rest."""
    if "by" in rule:
        k = rule.get("by_arg", 0)
        key = args[k].strip().lower() if k < len(args) else ""
        if rule.get("by_unit"):
            key = (_unit(args[k]) or "") if k < len(args) else ""
        return rule["by"].get(key, rule["by"].get("*"))
    return rule.get("to")


def _special(fn: str, args: list[str], fam: str, inner: str) -> tuple[bool, str | None]:
    """Calls whose rewrite moves words around: text aggregation (ORDER BY / SEPARATOR inside) and CONVERT (two orders)."""
    if fn == "group_concat" and fam == "postgresql":
        m = re.fullmatch(r"\s*(distinct\s+)?([\s\S]+?)(?:\s+order\s+by\s+([\s\S]+?))?(?:\s+separator\s+('(?:[^'\\]|\\.|'')*'))?\s*", inner, re.I)
        if not m or len(_args(m.group(2))) != 1:
            return True, None                              # several columns joined: written by hand
        sep = m.group(4) or "','"
        dist = "DISTINCT " if m.group(1) else ""
        if dist and m.group(3) and m.group(3).strip().split()[0].lower() != m.group(2).strip().lower():
            return True, None                              # PostgreSQL wants DISTINCT's ORDER BY on the joined value itself
        order = f" ORDER BY {m.group(3).strip()}" if m.group(3) else ""
        return True, f"string_agg({dist}CAST({m.group(2).strip()} AS TEXT), {sep}{order})"
    if fn == "string_agg" and fam == "mysql":
        m = re.fullmatch(r"\s*(distinct\s+)?([\s\S]+?)\s*,\s*('(?:[^'\\]|\\.|'')*')(?:\s+order\s+by\s+([\s\S]+?))?\s*", inner, re.I)
        if not m:
            return True, None
        val = re.sub(r"\s*::\s*(?:text|varchar)\s*$|^\s*cast\s*\(\s*([\s\S]+?)\s+as\s+(?:text|varchar)\s*\)\s*$", lambda x: x.group(1) or "",
                     m.group(2).strip(), flags=re.I)
        order = f" ORDER BY {m.group(4).strip()}" if m.group(4) else ""
        sep = "" if m.group(3) == "','" else f" SEPARATOR {m.group(3)}"
        return True, f"GROUP_CONCAT({'DISTINCT ' if m.group(1) else ''}{val}{order}{sep})"
    if fn == "convert" and len(args) == 2:
        casts = spec(fam).get("cast_types", {})
        t0, t1 = args[0].strip(), args[1].strip()
        ty = re.fullmatch(r"(n?varchar|n?char|int|integer|bigint|smallint|decimal|numeric|date|datetime|datetime2|time|float|real|"
                          r"signed|unsigned|text|double)(\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?(\s+integer)?", t0, re.I)
        if ty:                                             # SQL Server: CONVERT(type, value)
            return True, _cast(t1, ty.group(0), fam, casts)
        ty = re.fullmatch(r"(char|signed|unsigned|decimal|date|datetime|time|binary|double|float|integer|int)(\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?(\s+integer)?",
                          t1, re.I)
        if ty and fam == "postgresql":                    # MySQL: CONVERT(value, type)
            return True, _cast(t0, ty.group(0), fam, casts)
        return False, None
    return False, None


def _cast(value: str, typ: str, fam: str, casts: dict) -> str | None:
    base = re.sub(r"\s+", " ", typ.strip().lower())
    name = re.sub(r"\s*\(.*$", "", base)
    size = re.search(r"\(.*\)", base)
    to = casts.get(base, casts.get(name, name.upper() + (size.group(0) if size else "")))
    if to is None:
        return None
    if "{size}" in to:
        to = to.replace("{size}", size.group(0) if size else "")
    return f"CAST({value} AS {to})"


def rewrite_calls(sql: str, fam: str) -> tuple[str, list[dict]]:
    """Every call this database refuses, rewritten in its own words, innermost first (DATEADD(day, 1, GETDATE()) works out
    GETDATE() before DATEADD). Returns (new text, [{'fn', 'from', 'error', 'evidence', 'new', 'rule'}]) for the outermost
    calls; a call with no safe rewrite keeps its text and has new = None."""
    s = _mask(sql)
    calls = _calls(fam)
    names = "|".join(sorted(set(calls) | {"group_concat", "string_agg", "convert"}, key=len, reverse=True))
    out, pos, found = [], 0, []
    for m in re.finditer(rf"(?<![\w.$])({names})\s*\(", s, re.I):
        if m.start() < pos:
            continue
        a = m.end() - 1
        b = _close(s, a)
        if b <= a:
            continue
        fn = m.group(1).lower()
        inner_raw = sql[a + 1:b]
        inner, sub = rewrite_calls(inner_raw, fam)           # the arguments first
        inner = _regex_rewrites(inner, fam)
        args = _args(inner) if inner.strip() else []
        done, new = _special(fn, args, fam, inner)
        rule = None
        if not done:
            rule = next((r for r in calls.get(fn, []) if len(args) in (r["args"] if isinstance(r["args"], list) else [r["args"]])), None)
            if rule is None:
                continue                                    # this database has it with this many arguments
            tpl = _pick(rule, args)
            new = _render(tpl, args, fam, rule) if tpl else None
        else:
            rule = spec(fam).get("special", {}).get(fn)
            if rule is None:
                continue
        out.append(sql[pos:m.start()])
        out.append(new if new is not None else sql[m.start():a + 1] + inner + ")")
        pos = b + 1
        found.append({"fn": fn, "rule": rule, "evidence": sql[m.start():b + 1], "new": new, "start": m.start(), "end": b + 1})
        found += [{**x, "nested": True} for x in sub]
    out.append(sql[pos:])
    return "".join(out), found


def _regex_rewrites(text: str, fam: str) -> str:
    """The plain word swaps this database's 'refuses' list rewrites (GETDATE() -> NOW(), CURDATE() -> CURRENT_DATE ...),
    applied inside a call's arguments so the outer rewrite is whole."""
    from app.agents.sqlbasics import _bare_ident, _refuses

    s = _mask(text)
    edits = []
    for r in _refuses(fam):
        if r.get("to") is None:
            continue
        for m in r["_re"].finditer(s):
            new = _bare_ident(m.group(1)) if r["to"] == "@bare" else m.expand(r["to"])
            if not any(a < m.end() and m.start() < b for a, b, _n in edits):
                edits.append((m.start(), m.end(), new))
    for a, b, new in sorted(edits, reverse=True):
        text = text[:a] + new + text[b:]
    return text


# ---------------------------------------------------------------- what the checker reports
def port(sql: str, s: str, intent: str, F, is_select: bool) -> list:
    from app.agents.sqlbasics import CHOSEN
    from app.dbknow import engines as E

    chosen = CHOSEN.get()
    fam = E.family(chosen)
    out = []
    if fam:
        out += _foreign_calls(sql, fam, E.label(chosen), F)
        out += _top_rows(sql, s, fam, E.label(chosen), F)
        out += _intervals(sql, s, fam, E.label(chosen), F)
        out += _casts(sql, s, fam, E.label(chosen), F)
    out += _date_math(sql, s, fam, E.label(chosen) if fam else "", F)
    out += _power(sql, s, fam, F)
    merged: dict = {}                                     # YEAR(d) in SELECT and in GROUP BY: one finding, both rewritten
    for f, edits in out:
        key = (f.kind, f.topic, f.evidence)
        if key in merged:
            merged[key][1].extend(e for e in edits if e not in merged[key][1])
        else:
            merged[key] = (f, list(edits))
    return list(merged.values())


def _foreign_calls(sql: str, fam: str, name: str, F) -> list:
    _new, found = rewrite_calls(sql, fam)
    out = []
    for c in found:
        r = c["rule"]
        fn = c["fn"].upper()
        ev = re.sub(r"\s+", " ", c["evidence"]).strip()[:90]
        where = r.get("from", "another database")
        if c["new"] is not None:
            fen = f"{r.get('fix_en', 'Write it in ' + name + ' words')} Here: {ev} -> {c['new']}."
            fhi = f"{r.get('fix_hi', name + ' ke shabdon mein likho.')} Yahan: {ev} -> {c['new']}."
        else:
            fen, fhi = r.get("manual_en", r.get("fix_en", "")), r.get("manual_hi", r.get("fix_hi", ""))
        out.append((F("error", "dialect", f"{name} has no {fn}() (that's {where})", f"{name} mein {fn}() nahi hota (wo {where} ka hai)", ev,
                      f"{name} refuses it ({r['error']}). " + fen, f"{name} ise mana karta hai ({r['error']}). " + fhi),
                    [(c["start"], c["end"], c["new"])] if c["new"] is not None and not c.get("nested") else []))
    return out


def _stmt_end(sql: str, s: str, a: int) -> int:
    """Where the statement (or the parenthesised subquery) that starts at a ends: before its closing ')' or a trailing ';'."""
    depth = 0
    for k in range(a, len(s)):
        ch = s[k]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                return k
            depth -= 1
        elif ch == ";" and depth == 0:
            return k
    return len(sql.rstrip())


def _top_rows(sql: str, s: str, fam: str, name: str, F) -> list:
    """SQL Server's TOP n and Oracle's ROWNUM <= n: in MySQL/MariaDB/PostgreSQL words, LIMIT n at the end of that SELECT."""
    sp = spec(fam).get("rows", {})
    out = []
    for m in re.finditer(r"\bselect\s+(distinct\s+)?(top\s*(?:\(\s*(\d+)\s*\)|(\d+))(\s+percent)?(\s+with\s+ties)?\s+)", s, re.I):
        n = m.group(3) or m.group(4)
        end = _stmt_end(sql, s, m.end())
        body = s[m.end():end]
        can = not (m.group(5) or m.group(6)) and not re.search(r"\blimit\b|\bfetch\s+(first|next)\b|\bunion\b|\bintersect\b|\bexcept\b",
                                                                re.sub(r"\([^()]*\)", "", body), re.I)
        ev = re.sub(r"\s+", " ", sql[m.start():m.end()]).strip()
        tail = sql[:end].rstrip()
        edits = [(m.start(2), m.end(2), ""), (len(tail), len(tail), f"\nLIMIT {n}")] if can else []
        new = (re.sub(r"\s+", " ", (sql[:m.start(2)] + sql[m.end(2):end]).strip())[:60] + f" ... LIMIT {n}") if can else ""
        out.append((F("error", "dialect", f"{name} has no TOP n (that's SQL Server)", f"{name} mein TOP n nahi hota (wo SQL Server ka hai)", ev,
                      f"{name} refuses it ({sp.get('top_error', 'a syntax error')}). " + (
                          f"Take TOP {n} out and end that SELECT with LIMIT {n} (after its ORDER BY): same rows. " if can else
                          "Take TOP out and end the query with ORDER BY ... LIMIT n" + (
                              " (TOP n PERCENT / WITH TIES have no LIMIT twin: count the rows first, or rank with RANK() and keep "
                              "rank <= n for ties)." if (m.group(5) or m.group(6)) else ".")),
                      f"{name} ise mana karta hai. TOP {n} hatao aur us SELECT ke aakhir mein (ORDER BY ke baad) LIMIT {n} likho."),
                    edits))
        del new
    w = re.search(r"\bwhere\s+(rownum\s*(<=|<|=)\s*(\d+))\s*(and\b\s*)?|\band\s+(rownum\s*(<=|<|=)\s*(\d+))\b", s, re.I)
    if w:
        op, k = (w.group(2), w.group(3)) if w.group(1) else (w.group(6), w.group(7))
        n = int(k) - 1 if op == "<" else (1 if op == "=" and k == "1" else int(k) if op == "<=" else None)
        end = _stmt_end(sql, s, w.start())
        flat = re.sub(r"\([^()]*\)", "", s[w.start():end])
        can = n is not None and not re.search(r"\blimit\b|\bunion\b", flat, re.I)
        if w.group(1):
            cut = (w.start(1), w.end()) if w.group(4) else (w.start(), w.end(1))
        else:
            cut = (w.start(), w.end())
        a0 = cut[0]
        while not w.group(4) and a0 > 0 and sql[a0 - 1] in " \t":
            a0 -= 1                                        # no space left hanging where the condition was
        cut = (a0, cut[1])
        tail = sql[:end].rstrip()
        edits = [(cut[0], cut[1], ""), (len(tail), len(tail), f"\nLIMIT {n}")] if can else []
        ordered = bool(re.search(r"\border\s+by\b", flat, re.I))
        out.append((F("error", "dialect", f"{name} has no ROWNUM (that's Oracle)", f"{name} mein ROWNUM nahi hota (wo Oracle ka hai)",
                      re.sub(r"\s+", " ", sql[w.start():w.end()]).strip(),
                      f"{name} refuses it ({sp.get('rownum_error', 'unknown column rownum')}). "
                      + (f"End the SELECT with LIMIT {n} instead. " if can else "End the SELECT with LIMIT n instead. ")
                      + ("Note: Oracle numbers rows BEFORE its ORDER BY, so ROWNUM <= n there took n arbitrary rows and then sorted "
                         "them; LIMIT takes the first n after the sort, which is what a 'top n' query means." if ordered else ""),
                      f"{name} ise mana karta hai. Iski jagah SELECT ke aakhir mein LIMIT {n if n is not None else 'n'} likho."),
                    edits))
    return out


def _intervals(sql: str, s: str, fam: str, name: str, F) -> list:
    """An interval in the other engine's words: MySQL's INTERVAL 7 DAY on PostgreSQL (refused), PostgreSQL's INTERVAL '7 days'
    on MySQL/MariaDB (refused), and PostgreSQL's INTERVAL '2' WEEK / QUARTER, which it reads as 2 SECONDS (silently)."""
    iv = spec(fam).get("interval", {})
    out = []
    if fam == "postgresql":
        calls = [(c.start(), _close(s, c.end() - 1)) for c in re.finditer(r"\b(?:date_add|date_sub|adddate|subdate)\s*\(", s, re.I)]
        for m in re.finditer(r"\binterval\s+(\d+|\([^()]*\))\s+" + UNIT_WORD + r"\b", s, re.I):
            if any(a < m.start() < b for a, b in calls):
                continue                                   # inside DATE_ADD(...): rewritten with its call
            new = interval(fam, sql[m.start(1):m.end(1)], UNITS[m.group(2).lower()])
            out.append((F("error", "dialect", f"{name} has no INTERVAL n {m.group(2).upper()} (that's MySQL/MariaDB)",
                          f"{name} mein INTERVAL n {m.group(2).upper()} nahi hota", sql[m.start():m.end()],
                          f"{name} refuses it ({iv.get('error', 'a syntax error')}): the amount must be in quotes. Here: "
                          f"{sql[m.start():m.end()]} -> {new} (INTERVAL '7' DAY runs on MySQL/MariaDB too).",
                          f"{name} ise mana karta hai: amount quotes mein chahiye. Yahan: {sql[m.start():m.end()]} -> {new}."),
                        [(m.start(), m.end(), new)]))
        for m in re.finditer(r"\binterval\s+'\s*(\d+)\s*'\s+(week|quarter)s?\b", sql, re.I):
            new = interval(fam, m.group(1), m.group(2).upper())
            unit = m.group(2).upper()
            # in a SELECT list the word after '2' is taken as the column's NAME: INTERVAL '2' alone is 2 seconds (silently);
            # anywhere else (WHERE, inside CAST, before AS) it is a syntax error
            alias = bool(re.match(r"\s*(,|from\b|$)", sql[m.end():], re.I)) and not re.search(r"\bas\s*$", sql[:m.start()], re.I)
            kind = "wrong" if alias else "error"
            out.append((F(kind, "interval_unit",
                          f"INTERVAL '{m.group(1)}' {unit} is {m.group(1)} SECONDS on {name} (and {unit} becomes the column's name)" if alias
                          else f"{name} has no INTERVAL '<n>' {unit}",
                          f"{name} pe INTERVAL '{m.group(1)}' {unit} sirf {m.group(1)} SECOND hai" if alias else
                          f"{name} mein INTERVAL '<n>' {unit} nahi hota", sql[m.start():m.end()],
                          f"{name}'s INTERVAL '<n>' <unit> form knows YEAR, MONTH, DAY, HOUR, MINUTE and SECOND, not {unit}. "
                          + (f"In a SELECT list it reads INTERVAL '{m.group(1)}' as {m.group(1)} seconds and takes {unit} as the column's name "
                             f"(lab: 2024-01-31 + INTERVAL '2' WEEK gave 2024-01-31 00:00:02, in a column called week), with no error. "
                             if alias else "Anywhere else it stops with 42601 (syntax error at or near the unit). ")
                          + f"Write it in days or months: {new} (this runs on MySQL/MariaDB too).",
                          f"{name} {unit} nahi samajhta" + (": SELECT mein '2' ko 2 second aur unit ko column ka naam maan leta hai, bina error. "
                                                             if alias else ": syntax error. ") + f"Din ya mahine mein likho: {new}."),
                        [(m.start(), m.end(), new)]))
    else:
        for m in re.finditer(r"\binterval\s+'\s*(\d+)\s*" + UNIT_WORD + r"\s*'", sql, re.I):
            new = interval(fam, m.group(1), UNITS[m.group(2).lower()])
            out.append((F("error", "dialect", f"{name} has no INTERVAL '{m.group(1)} {m.group(2)}s' (that's PostgreSQL)",
                          f"{name} mein INTERVAL '{m.group(1)} {m.group(2)}s' nahi hota", sql[m.start():m.end()],
                          f"{name} refuses it ({iv.get('error', 'a syntax error')}): the number and the unit are separate words there. "
                          f"Here: {sql[m.start():m.end()]} -> {new}.",
                          f"{name} ise mana karta hai: number aur unit alag likhe jaate hain. Yahan: {sql[m.start():m.end()]} -> {new}."),
                        [(m.start(), m.end(), new)]))
    return out


def _casts(sql: str, s: str, fam: str, name: str, F) -> list:
    """CAST(x AS <type>) with a type name this database doesn't have (TEXT / BIGINT / TIMESTAMP on MySQL/MariaDB, SIGNED /
    DATETIME on PostgreSQL), and CAST(x AS CHAR) on PostgreSQL, which is CHAR(1): it silently keeps the first character."""
    casts = spec(fam).get("cast_types", {})
    refused = spec(fam).get("cast_refused", [])
    out = []
    for m in re.finditer(r"\bcast\s*\(", s, re.I):
        a = m.end() - 1
        b = _close(s, a)
        am = None
        for am in re.finditer(r"\s+as\s+", s[a + 1:b], re.I):
            pass
        if am is None:
            continue
        t0 = a + 1 + am.end()
        typ = re.sub(r"\s+", " ", sql[t0:b].strip()).lower()
        base = re.sub(r"\s*\(.*$", "", typ)
        if fam == "postgresql" and typ == "char":
            out.append((F("wrong", "cast_char", f"CAST(... AS CHAR) keeps only the first character on {name}",
                          f"{name} pe CAST(... AS CHAR) sirf pehla akshar rakhta hai", re.sub(r"\s+", " ", sql[m.start():b + 1])[:90],
                          f"On {name} CHAR with no length is CHAR(1), so CAST(order_date AS CHAR) gave '2' in the lab (MySQL/MariaDB give "
                          f"the whole text). Cast to VARCHAR (or TEXT) for the whole value.",
                          f"{name} pe bina length ka CHAR, CHAR(1) hai: lab mein '2' aaya. Poori value ke liye VARCHAR likho."),
                        [(t0, b, "VARCHAR")]))
            continue
        if base in refused or typ in refused:
            to = casts.get(typ, casts.get(base))
            new = (to.replace("{size}", re.search(r"\(.*\)", typ).group(0) if "(" in typ else "") if to else None)
            out.append((F("error", "dialect", f"{name} can't CAST to {typ.upper()}", f"{name} {typ.upper()} mein CAST nahi karta",
                          re.sub(r"\s+", " ", sql[m.start():b + 1])[:90],
                          f"{name} refuses it ({spec(fam).get('cast_error', 'a syntax error')}). "
                          + (f"Its word for that type is {new}: CAST(... AS {new})." if new else
                             spec(fam).get("cast_manual_en", "Use one of its own cast types.")),
                          f"{name} ise mana karta hai. " + (f"Iska type {new} hai: CAST(... AS {new})." if new else "Iske apne cast type likho.")),
                        [(t0, b, new)] if new else []))
    return out


def _is_date(op: str, sql_op: str) -> str | None:
    """'date' / 'time' when the operand plainly holds a date (a date-named column, CURRENT_DATE, DATE '...', CAST(.. AS DATE))
    or a date and time (a *_at column, NOW()), else None. Read from the text only: a column's name is a strong hint, not a
    proof (the checker says so)."""
    o = op.strip()
    if re.fullmatch(r"(?:current_date|curdate\s*\(\s*\)|date\s*'[^']*'|cast\s*\([\s\S]+\bas\s+date\s*\)|date\s*\([\s\S]+\))", o, re.I):
        return "date"
    if re.fullmatch(r"(?:now\s*\(\s*\)|current_timestamp(?:\s*\(\s*\))?|sysdate\s*\(\s*\)|localtimestamp)", o, re.I):
        return "time"
    if re.fullmatch(r"[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?", o):
        col = o.split(".")[-1]
        if DATE_NAME.search(col):
            return "date"
        if TIME_NAME.search(col):
            return "time"
    return None


def _date_math(sql: str, s: str, fam: str | None, name: str, F) -> list:
    """date - date and date +/- a number: PostgreSQL gives days and a date; MySQL/MariaDB turn both dates into numbers
    (2024-02-03 -> 20240203) and subtract those, silently (lab: 9 days came out as 78; 2024-03-01 - 1 as 20240300)."""
    if fam == "postgresql":
        return []
    out = []
    for m in re.finditer(rf"({OPER})\s*([-+])\s*({OPER})", s, re.I):
        before = s[:m.start()].rstrip()
        after = s[m.end():].lstrip()
        if before.endswith(("*", "/", "%", "-", "+", "^")) or after.startswith(("*", "/", "(")) or \
                re.search(r"\binterval\s*$", before, re.I) or re.match(r"interval\b", after, re.I):
            continue
        a_raw, op, b_raw = sql[m.start(1):m.end(1)], m.group(2), sql[m.start(3):m.end(3)]
        if re.match(r"interval\b", b_raw, re.I) or re.fullmatch(r"(?:date|interval)", a_raw.strip(), re.I):
            continue
        ka, kb = _is_date(m.group(1), a_raw), _is_date(m.group(3), b_raw)
        num = re.fullmatch(r"\d+", b_raw.strip())
        if ka == "date" and kb == "date" and op == "-":
            new = f"DATEDIFF({a_raw.strip()}, {b_raw.strip()})"
            title, what = "date - date is not a number of days on MySQL/MariaDB", "days between the two dates"
        elif ka == "date" and num:
            new = f"{a_raw.strip()} {op} INTERVAL {b_raw.strip()} DAY"
            title, what = f"date {op} {b_raw.strip()} is not a date on MySQL/MariaDB", f"the date {b_raw.strip()} day(s) {'later' if op == '+' else 'earlier'}"
        elif ka and kb and op == "-" and "time" in (ka, kb):
            new = None
            title, what = "date/time - date/time is not a duration on MySQL/MariaDB", "the time between them"
        else:
            continue
        ev = re.sub(r"\s+", " ", sql[m.start():m.end()]).strip()
        why = ("both dates are read as numbers (2024-02-03 -> 20240203) and subtracted: 9 days came out as 78 in the lab"
               if op == "-" and kb else "the date is read as a number (2024-03-01 -> 20240301) and the 1 is subtracted: 20240300 in the lab")
        if fam is None:
            out.append((F("check", "date_math", title, "MySQL/MariaDB pe date ka hisaab number jaisa hota hai", ev,
                          f"On PostgreSQL {ev} gives {what}. On MySQL/MariaDB {why}, with no error. There write "
                          + (f"{new}." if new else "TIMESTAMPDIFF(MINUTE, start, end) (or SECOND / HOUR) for the time between them.")
                          + " Tell me the database and I'll write it for that one.",
                          f"PostgreSQL pe {ev} sahi deta hai; MySQL/MariaDB pe dates number ban jaati hain aur galat number aata hai, "
                          f"bina error. Wahan {new or 'TIMESTAMPDIFF(MINUTE, start, end)'} likho."),
                        []))
            continue
        out.append((F("wrong" if new else "check", "date_math", title, "MySQL/MariaDB pe date ka hisaab number jaisa hota hai", ev,
                      f"On {name} {why}, with no error. " + (f"For {what} write {new}." if new else
                                                              "For the time between them write TIMESTAMPDIFF(MINUTE, start, end) "
                                                              "(or SECOND / HOUR / DAY): the unit is yours to choose."),
                      f"{name} pe dates number ban jaati hain aur galat number aata hai, bina error. "
                      + (f"Sahi: {new}." if new else "TIMESTAMPDIFF(MINUTE, start, end) likho.")),
                    [(m.start(), m.end(), new)] if new else []))
    return out


def _power(sql: str, s: str, fam: str | None, F) -> list:
    """a ^ b: a power on PostgreSQL (2 ^ 3 = 8), a bitwise XOR on MySQL/MariaDB (2 ^ 3 = 1), with no error."""
    if fam == "postgresql":
        return []
    out = []
    for m in re.finditer(rf"({OPER})\s*\^\s*({OPER})", s, re.I):
        a, b = sql[m.start(1):m.end(1)].strip(), sql[m.start(2):m.end(2)].strip()
        ev = sql[m.start():m.end()]
        new = f"POWER({a}, {b})"
        out.append((F("check", "power_xor", f"{ev} is a bitwise XOR on MySQL/MariaDB, not a power", f"MySQL/MariaDB pe {ev} XOR hai, power nahi", ev,
                      f"On MySQL/MariaDB ^ compares the bits (2 ^ 3 = 1 in the lab); on PostgreSQL it raises to a power (8). "
                      f"If you meant the power, write {new} (the same on both).",
                      f"MySQL/MariaDB pe ^ bits ka XOR hai (2 ^ 3 = 1); PostgreSQL pe power (8). Power chahiye to {new} likho."),
                    [(m.start(), m.end(), new)] if fam == "mysql" else []))
    return out


# ---------------------------------------------------------------- translation
PORT_TOPICS = {"dialect", "date_math", "interval_unit", "cast_char", "power_xor", "update_join_syntax", "delete_join_syntax",
               "set_qualified", "upsert_syntax", "change_limit", "ddl_dialect", "limit_in_subquery", "in_limit", "derived_alias",
               "null_fn", "concat", "quotes", "having_alias", "sum_bool", "count_multi"}


def target_of(text: str) -> tuple[str | None, str | None]:
    """(target, source) named in a translation request: 'convert this mysql query to postgres' -> ('postgresql', 'mysql');
    'ye query postgres mein badlo' -> ('postgresql', None). The target is the database after to / into / for, or before
    mein / me / में / wala / version; the other named one is the source."""
    from app.dbknow import engines as E

    q, words = split_request(text)
    words = words if q else (text or "")
    hits = []
    for eid in E.ORDER:
        for rx in E._rx(eid, "named"):
            for m in rx.finditer(words):
                hits.append((m.start(), m.end(), eid))
    hits.sort()
    target = source = None
    for a, b, eid in hits:
        before, after = words[max(0, a - 14):a].lower(), words[b:b + 22].lower()
        if re.search(r"\b(to|into|for|in|on|as)\s+(?:a\s+|the\s+)?$", before) or \
                re.match(r"\s*(mein|me|main|में|ke liye|के लिए|wala|wali|version|syntax|style|format|compatible)(?=[\s:,.;!?]|$)", after):
            if not re.search(r"\b(from)\s+$", before):
                target = eid
                continue
        source = source or eid
    if target == source:
        source = None
    return target, source


TRANSLATE_WORDS = re.compile(r"\b(convert\w*|translat\w*|port|migrat\w*|rewrite|re-write|change|switch|turn|make|badl\w*|badal\w*|"
                             r"likh\w*|bana\w*|kar\s*do|karo)\b|बदल|लिख|बना|कन्वर्ट", re.I)


def split_request(text: str) -> tuple[str | None, str]:
    """(the query, the person's words around it). The query may follow a lead-in on the same line ('convert to postgres:
    SELECT ...') or come first with the words after a ';'."""
    from app.agents.core import query_in
    from app.agents.sql import _split_tail

    q = query_in(text)
    if q:
        query, tail = _split_tail(q)
        words = text.replace(q, " ") + " " + tail
        return query.strip(), words.strip()
    m = re.search(r"(?is)(?<![\w])(select\b[\s\S]+?\bfrom\b|select\s+\S|with\s+\w+\s+as\s*\(|update\s+\S+\s+set\b|delete\s+from\b|"
                  r"insert\s+into\b)[\s\S]*", text or "")
    if not m:
        return None, text or ""
    query, tail = _split_tail(m.group(0))
    return query.strip(), ((text or "")[:m.start()] + " " + tail).strip()


def _words_only(text: str) -> str:
    """The person's words without the pasted query (a query naming a 'postgres' table is not a request)."""
    return split_request(text)[1]


def is_followup(text: str) -> bool:
    """'now convert it to PostgreSQL' / 'ise postgres mein likho' after a query was checked: that query, translated."""
    q, words = split_request(text)
    if q or len(words) > 160:
        return False
    t, _s = target_of(text)
    return bool(t and TRANSLATE_WORDS.search(words) and
                re.search(r"\b(it|this|that|query|same|ise|isko|isse|ye|yeh|wahi|usko|use)\b|इसे|इसको|ये|यह|क्वेरी", words, re.I))


def is_request(text: str) -> bool:
    """A query plus words asking for it in another database's words."""
    q, words = split_request(text)
    if not q or not re.search(r"\b(from|set|values|into)\b|^\s*select\b", q, re.I):
        return False
    t, _s = target_of(text)
    return bool(t and TRANSLATE_WORDS.search(words))


MEANING = [   # (id, pattern on the masked query, source family, target family, en, hi)
    ("case", r"(?:=|<>|!=|\blike|\bin\s*\()\s*'[^']*[A-Za-z]", "mysql", "postgresql",
     "Text comparisons: MySQL/MariaDB's usual _ci collation ignores case ('Delhi' = 'delhi' is true), PostgreSQL doesn't ('a' = 'A' "
     "was false in the lab). If the data's case varies, compare LOWER(col) = LOWER('...') or use ILIKE.",
     "Text compare: MySQL/MariaDB case ignore karte hain, PostgreSQL nahi. Case alag ho sakta hai to LOWER(col) = LOWER('...') ya ILIKE."),
    ("case", r"(?:=|<>|!=|\blike|\bin\s*\()\s*'[^']*[A-Za-z]", "postgresql", "mysql",
     "Text comparisons: PostgreSQL matched case exactly; MySQL/MariaDB's usual _ci collation ignores case, so 'DELHI' now matches "
     "'delhi' too. For an exact match compare with BINARY (col = BINARY 'Delhi') or use a _bin collation.",
     "Text compare: PostgreSQL case dekhta tha, MySQL/MariaDB ignore karte hain. Exact match ke liye BINARY lagao."),
    ("division", r"(?<![\w.])[A-Za-z_][\w.]*\s*/\s*[A-Za-z_][\w.]*(?![\w.(])", "mysql", "postgresql",
     "Division: on PostgreSQL an integer / an integer drops the fraction (7 / 2 = 3; MySQL/MariaDB gave 3.5). If both columns are "
     "integers, write col * 1.0 / other (or CAST(col AS DECIMAL)) to keep it.",
     "Bhaag: PostgreSQL pe integer / integer fraction hata deta hai (7/2 = 3, MySQL/MariaDB 3.5). col * 1.0 / other likho."),
    ("concat_null", r"\bconcat\s*\(", "mysql", "postgresql",
     "CONCAT with a NULL part: MySQL/MariaDB return NULL, PostgreSQL skips the NULL part (CONCAT('a', NULL) = 'a').",
     "CONCAT mein NULL: MySQL/MariaDB NULL dete hain, PostgreSQL NULL wala hissa chhod deta hai."),
    ("concat_null", r"\bconcat\s*\(", "postgresql", "mysql",
     "CONCAT with a NULL part: PostgreSQL skipped it, MySQL/MariaDB return NULL for the whole text. Wrap parts that can be empty "
     "in COALESCE(part, '') or use CONCAT_WS.",
     "CONCAT mein NULL: PostgreSQL chhodta tha, MySQL/MariaDB poora NULL dete hain. COALESCE(part, '') lagao."),
    ("greatest", r"\b(greatest|least)\s*\(", "*", "*",
     "GREATEST / LEAST with a NULL argument: MySQL/MariaDB return NULL, PostgreSQL ignores the NULL (GREATEST(1, NULL, 3) = 3 there, "
     "NULL on MariaDB in the lab). Wrap arguments that can be NULL in COALESCE.",
     "GREATEST/LEAST mein NULL: MySQL/MariaDB NULL dete hain, PostgreSQL NULL ko chhod deta hai. COALESCE lagao."),
    ("length", r"\blength\s*\(", "mysql", "postgresql",
     "LENGTH: bytes on MySQL/MariaDB ('ñab' = 4), characters on PostgreSQL (3). If the old query meant bytes, write OCTET_LENGTH; "
     "CHAR_LENGTH counts characters on both.",
     "LENGTH: MySQL/MariaDB pe bytes, PostgreSQL pe characters. Characters ke liye CHAR_LENGTH dono pe."),
    ("regexp", r"\b(regexp|rlike)\b|~", "*", "*",
     "Regular expressions: MySQL/MariaDB's REGEXP ignores case with a _ci collation; PostgreSQL's ~ matches case, ~* ignores it.",
     "REGEXP: MySQL/MariaDB case ignore karta hai; PostgreSQL ka ~ case dekhta hai, ~* nahi."),
    ("group_concat", r"\bstring_agg\s*\(", "postgresql", "mysql",
     "GROUP_CONCAT stops at group_concat_max_len (1024 bytes by default on MySQL/MariaDB), with only a warning; raise it "
     "(SET SESSION group_concat_max_len = 100000) for long lists.",
     "GROUP_CONCAT 1024 bytes pe kat jaata hai (sirf warning); lambi list ke liye group_concat_max_len badhao."),
    ("nulls_order", r"\border\s+by\b", "*", "*",
     "Sorting: NULLs come first in ascending order on MySQL/MariaDB and last on PostgreSQL. If the column can be NULL, say where "
     "they go (ORDER BY col IS NULL, col works on both).",
     "Sort: MySQL/MariaDB pe NULL pehle (ASC), PostgreSQL pe aakhir mein. ORDER BY col IS NULL, col dono pe ek jaisa."),
]


def translate(sql: str, target: str, source: str | None = None) -> dict:
    """The query in the target database's words: {'sql', 'changes' [(evidence, new, why)], 'also' [definite mistakes fixed on
    the way], 'left' [what couldn't be translated automatically], 'notes' [meaning notes], 'notes_only': bool}."""
    from app.agents import sqlcorrect
    from app.dbknow import engines as E

    fam = E.family(target)
    if source is None:
        shown, _why = E.shown(sql)
        source = shown
    src_fam = E.family(source) or ("mysql" if source in ("sqlserver", "oracle", "sqlite") else None)
    if not fam:
        return {"sql": None, "changes": [], "also": [], "left": [], "notes": notes_for(target, sql), "notes_only": True,
                "target": target, "source": source}
    cur, changes, also, left = sql, [], [], []
    seen = set()
    for _round in range(4):
        res = sqlcorrect.check(cur, engine=target)
        fs = [f for f in res["findings"] if f["kind"] in ("error", "wrong")]
        new_fs = [f for f in fs if (f["topic"], f["evidence"]) not in seen]
        for f in new_fs:
            seen.add((f["topic"], f["evidence"]))
            (changes if f["topic"] in PORT_TOPICS else also).append(f)
        nxt = res.get("corrected")
        if not nxt or nxt == cur or not new_fs:
            break
        cur = nxt
    final = sqlcorrect.check(cur, engine=target)
    for f in final["findings"]:
        if f["kind"] == "error" and f["topic"] in PORT_TOPICS:
            left.append(f)
    if src_fam == "postgresql" and fam == "mysql":
        cur = re.sub(r"(?<![\w.])length\s*\(", "CHAR_LENGTH(", cur, flags=re.I)   # PostgreSQL's LENGTH counts characters
    notes = []
    s = _mask(cur)
    s0 = _mask(sql)
    for _id, pat, frm, to, en, hi in MEANING:
        if (frm in ("*", src_fam) and to in ("*", fam) and src_fam != fam and src_fam is not None
                and (re.search(pat, s0, re.I) or re.search(pat, s, re.I))):
            if (en, hi) not in notes:
                notes.append((en, hi))
    return {"sql": cur if cur != sql else sql, "changed": cur != sql, "changes": changes, "also": also, "left": left, "notes": notes,
            "notes_only": False, "target": target, "source": source}


def notes_for(eid: str, sql: str = "") -> list[tuple[str, str]]:
    """The notes of a notes-only database (SQL Server, Oracle, SQLite) that speak to what this query uses (LIMIT, IFNULL,
    NOW() ...), from its dialect file's 'notes' and 'note_marks'; all of them when nothing matches."""
    from app.dbknow import engines as E

    d = E.dialect(eid)
    notes, marks = d.get("notes", {}), d.get("note_marks", {})
    s = _mask(sql or "")
    hit = [k for k, p in marks.items() if k in notes and re.search(p, s, re.I)]
    keys = hit or list(notes)
    return [(notes[k], notes[k]) for k in keys]


HEADS = {
    "en": {"blind": "I don't have lab-tested {name} rules yet, so I won't rewrite it blind. What to change by hand for {name} "
                    "(from its docs, not lab-tested):",
           "same": "This query already runs on {name} as it is.", "words": "The query in {name}'s words", "from": " (from {src})",
           "changed": "What changed:", "also": "Also fixed (a mistake on every database):",
           "left": "Couldn't translate automatically (write these by hand):", "notes": "Same text, different meaning (check these):",
           "lab": "Rewrites checked on MariaDB 10.11 and PostgreSQL 16."},
    "hinglish": {"blind": "{name} ke lab-tested rules abhi mere paas nahi hain, isliye andaze se nahi badlunga. {name} ke liye haath se ye "
                          "badlo (docs se, lab mein test nahi):",
                 "same": "Ye query {name} pe waise hi chalti hai.", "words": "{name} ke shabdon mein query", "from": " ({src} se)",
                 "changed": "Kya badla:", "also": "Ye galti bhi theek ki (har database pe galat):",
                 "left": "Ye apne aap nahi badal saka (haath se likho):", "notes": "Text same, matlab alag (ye dekh lo):",
                 "lab": "Badlaav MariaDB 10.11 aur PostgreSQL 16 pe jaanche gaye."},
    "hindi": {"blind": "{name} के लैब में जाँचे नियम अभी मेरे पास नहीं हैं, इसलिए अंदाज़े से नहीं बदलूँगा। {name} के लिए हाथ से ये बदलिए "
                       "(docs से, लैब में जाँचा नहीं गया):",
              "same": "यह query {name} पर वैसे ही चलती है।", "words": "{name} के शब्दों में query", "from": " ({src} से)",
              "changed": "क्या बदला:", "also": "यह गलती भी ठीक की (हर database पर गलत):",
              "left": "ये अपने-आप नहीं बदल सका (हाथ से लिखिए):", "notes": "Text वही, मतलब अलग (ये जाँच लीजिए):",
              "lab": "बदलाव MariaDB 10.11 और PostgreSQL 16 पर जाँचे गए।"},
}


def translation_text(sql: str, t: dict, lang: str) -> str:
    """The chat reply for a translation, in the language asked (finding titles in English, or Hinglish for both others)."""
    from app.dbknow import engines as E

    name = E.label(t["target"])
    src = E.label(t["source"]) if t.get("source") else ""
    H = {k: v.replace("{name}", name).replace("{src}", src) for k, v in HEADS.get(lang, HEADS["en"]).items()}
    en = lang == "en"
    if t["notes_only"]:
        return "\n".join([H["blind"]] + [f"- {n[0]}" for n in t["notes"]])
    lines = []
    if not t["changed"]:
        lines.append(H["same"])
    else:
        lines.append(H["words"] + (H["from"] if src else "") + ":")
        lines += ["", t["sql"].strip()]
        if t["changes"]:
            lines += ["", H["changed"]]
            for f in t["changes"]:
                lines.append(f"- {f['title_en' if en else 'title_hi']}: {f['evidence']}")
        if t["also"]:
            lines += ["", H["also"]]
            lines += [f"- {f['title_en' if en else 'title_hi']}" for f in t["also"]]
    if t["left"]:
        lines += ["", H["left"]]
        lines += [f"- {f['title_en' if en else 'title_hi']}: {f['fix_en' if en else 'fix_hi']}" for f in t["left"]]
    if t["notes"]:
        lines += ["", H["notes"]]
        lines += [f"- {n[0] if en else n[1]}" for n in t["notes"]]
    lines += ["", H["lab"]]
    return "\n".join(lines).strip()
