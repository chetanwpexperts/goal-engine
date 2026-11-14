"""Basic SELECT mistakes (night 1 of the curriculum): text joining, NULL in calculations, LIKE patterns, DISTINCT, AND/OR order,
integer division, NULLs in a sorted LIMIT, ties before LIMIT, LIMIT with no order, double-quoted values.

Each rule was reproduced on MariaDB and PostgreSQL (tests/practice/test_night1.py has the lab table). What an engine does
differently comes from its dialect file (app/dbknow/dialects/<engine>.json, 'query_rules'), never from this code: when the
query itself shows its database (backticks, LIMIT a,b -> MySQL/MariaDB; ::casts, ILIKE, $1 -> PostgreSQL) the rule answers for
that engine; otherwise it says what happens on each.
Sure = 'error'/'wrong' (refused, or wrong rows whatever the data). Depends on the data or on what was meant = 'check'.
Returns [(Finding, edit or None)], edit = (start, end, new text) on the original query.
"""

import contextvars
import json
import re
from functools import lru_cache
from pathlib import Path

from app.agents.sqlverify import keep as schema_keep

DIALECTS = Path(__file__).resolve().parent.parent / "dbknow" / "dialects"

# money-like columns that are often left empty (no discount, no commission): NULL in a sum makes the sum NULL
OPTIONAL_NUM = set("""discount discount_amount shipping shipping_cost shipping_fee shipping_charge delivery_charge delivery_fee commission bonus
tax tax_amount tip tips fee fees coupon coupon_amount extra extra_charge surcharge adjustment refund refund_amount penalty late_fee
handling handling_fee deposit advance allowance overtime incentive rebate cashback""".split())
# text parts that are often missing (no middle name): CONCAT with a NULL part is NULL on MySQL/MariaDB
OPTIONAL_TEXT = set("""middle_name middlename mname suffix nickname address2 address_line2 address_line_2 landmark apartment salutation
initial initials second_name alt_phone phone2 alternate_phone""".split())
# columns that are empty until something happens: sorted with LIMIT, their NULLs come first on one engine or the other
OPTIONAL_SORT = OPTIONAL_NUM | set("""last_login last_login_at shipped_at delivered_at paid_at closed_at completed_at deleted_at cancelled_at
ended_at end_date expiry_date score rating""".split())
CONTAINS = re.compile(r"\b(contain\w*|includ\w*|anywhere|having .* in (it|the name)|with .* in (it|the name)|wala|wali|wale|mein .* ho)\b", re.I)
STARTS = re.compile(r"\b(start\w*|begin\w*) with\b|\bse shuru\b", re.I)
ENDS = re.compile(r"\bend(s|ing)? with\b|\bpe khatam\b|\bse khatam\b", re.I)
OPER = (r"(?:'(?:[^'\\]|\\.|'')*'|[`\"]?[A-Za-z_][\w$]*[`\"]?(?:\.[`\"]?[A-Za-z_][\w$]*[`\"]?)?(?:\s*\((?:[^()]|\([^()]*\))*\))?"
        r"|\d+(?:\.\d+)?)")


@lru_cache(maxsize=4)
def rules(engine: str) -> dict:
    return json.loads((DIALECTS / f"{engine}.json").read_text(encoding="utf-8")).get("query_rules", {})


@lru_cache(maxsize=4)
def _marks(engine: str) -> list:
    data = json.loads((DIALECTS / f"{engine}.json").read_text(encoding="utf-8"))
    return [re.compile(p, re.I) for p in data.get("query_marks", [])]


# the database the person named / chose / that was detected for this check (standing rule A), set by analyze_sql
CHOSEN: contextvars.ContextVar = contextvars.ContextVar("dbaba_engine", default=None)


def engine_seen(sql: str) -> str | None:
    """'mysql' or 'postgresql': the database the person named or chose (rule A), else the one the query's own syntax shows,
    else None (then each rule says what happens on each)."""
    from app.dbknow import engines as E

    chosen = CHOSEN.get()
    if chosen:
        return E.family(chosen)              # notes-only (SQL Server, Oracle, SQLite) and 'not sure': no single family
    my = any(rx.search(sql) for rx in _marks("mysql"))
    pg = any(rx.search(sql) for rx in _marks("postgresql"))
    return "mysql" if my and not pg else "postgresql" if pg and not my else None


def _col(op: str) -> str:
    return op.split(".")[-1].strip('`"').lower()


def _inside_null_fn(sql: str, pos: int) -> bool:
    """Is pos inside COALESCE( / IFNULL( / NVL( / ISNULL( ?"""
    depth = 0
    for k in range(pos - 1, -1, -1):
        ch = sql[k]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                if re.search(r"\b(coalesce|ifnull|nvl|isnull|nullif|concat_ws)\s*$", sql[:k], re.I):
                    return True
            else:
                depth -= 1
    return False


def _own_port(chosen: str | None) -> bool:
    """A database whose refused words come from its own dialect file rather than the MySQL/PostgreSQL families (SQLite)."""
    from app.dbknow import engines as E

    fam = E.syntax_family(chosen)
    return bool(fam) and fam not in ("mysql", "postgresql")


def _sql_server(s: str) -> bool:
    if CHOSEN.get() == "sqlserver":
        return True
    return bool(re.search(r"\bselect\s+top\s+\d|\[[A-Za-z_][\w ]*\]|\bgetdate\s*\(|\bnvarchar\b", s, re.I))


def basics(sql: str, s: str, is_select: bool, intent: str, finding_cls) -> list:
    F = finding_cls
    out = []
    eng = engine_seen(sql)
    said = (intent or "").lower()
    low = s.lower()
    tsql = _sql_server(s)
    from app.agents.sqlcorrect import _clauses, _depths

    dep = _depths(s)

    # 0. syntax the chosen database doesn't have (rule A): PostgreSQL's ::cast on MariaDB, backticks on PostgreSQL ...
    out += _foreign_syntax(sql, s, F)

    # 1. 'text' + 'text': + is arithmetic on MySQL/MariaDB (text -> 0, silently) and refused for text on PostgreSQL
    for m in re.finditer(rf"{OPER}(?:\s*\+\s*{OPER})+", s):
        parts = [p.strip() for p in re.split(r"\s*\+\s*", sql[m.start():m.end()])]
        lits = [p for p in parts if p.startswith("'")]
        if tsql or not lits or all(re.fullmatch(r"'[\d.:/-]+'", p) for p in lits) or any(p.lower() == "interval" for p in parts):
            continue
        if re.search(r"\binterval\s*$", sql[:m.start()], re.I) or re.match(r"\s*(day|month|year|hour|minute|second|week)\b", sql[m.end():], re.I):
            continue
        new = "CONCAT(" + ", ".join(parts) + ")"
        out.append((F("wrong", "concat", "+ doesn't join text: use CONCAT", "+ text nahi jodta: CONCAT lagao", sql[m.start():m.end()][:90],
                      "In MySQL and MariaDB + only adds numbers: each text part is turned into a number (usually 0), so the result "
                      "is 0 with a warning, not the joined text. PostgreSQL refuses it (operator does not exist: text + unknown). "
                      f"CONCAT joins text on all of them: {new}. (+ joins text only on SQL Server.)",
                      "MySQL/MariaDB mein + sirf number jodta hai: text 0 ban jaata hai aur result 0 aata hai. PostgreSQL mana kar deta hai. "
                      f"CONCAT har jagah text jodta hai: {new}."), (m.start(), m.end(), new)))

    # 2. 'text' || 'text': joins text on PostgreSQL (and Oracle), but means OR on MySQL/MariaDB (result 0 or 1)
    for m in re.finditer(rf"{OPER}(?:\s*\|\|\s*{OPER})+", s):
        parts = [p.strip() for p in re.split(r"\s*\|\|\s*", sql[m.start():m.end()])]
        if not any(p.startswith("'") and not re.fullmatch(r"'\d*'", p) for p in parts):
            continue                                       # a || b with no text in it may well be a logical OR
        if eng == "postgresql":
            continue
        new = "CONCAT(" + ", ".join(parts) + ")"
        sure = eng == "mysql"
        out.append((F("wrong" if sure else "check", "concat",
                      "|| means OR on MySQL/MariaDB, not 'join text'" if sure else "|| joins text only on PostgreSQL; on MySQL/MariaDB it means OR",
                      "MySQL/MariaDB pe || ka matlab OR hai, text jodna nahi", sql[m.start():m.end()][:90],
                      "On MySQL and MariaDB || is the logical OR (unless sql_mode has PIPES_AS_CONCAT), so first_name || ' ' || last_name "
                      f"returns 0 or 1. On PostgreSQL it joins text. {new} joins text on both"
                      + (". " if sure else " (one difference: on PostgreSQL CONCAT skips NULL parts, while || makes the whole result NULL). "),
                      f"MySQL/MariaDB pe || OR hai, isliye result 0 ya 1 aata hai; PostgreSQL pe ye text jodta hai. {new} dono pe text jodta hai."),
                    (m.start(), m.end(), new)))

    # 3. ISNULL(x, y) is SQL Server's; MySQL's ISNULL takes one argument (error 1582) and PostgreSQL has none
    for m in re.finditer(r"\bisnull\s*\(", s, re.I):
        a = m.end() - 1
        b = _close(s, a)
        args = _split_args(sql[a + 1:b])
        if len(args) != 2 or tsql or _own_port(CHOSEN.get()):
            continue                                   # (a database with its own port knowledge says it as a dialect finding)
        new = f"COALESCE({args[0]}, {args[1]})"
        out.append((F("error", "null_fn", "ISNULL(x, y) is SQL Server only: use COALESCE", "ISNULL(x, y) sirf SQL Server ka hai: COALESCE lagao",
                      sql[m.start():b + 1][:90],
                      "MySQL and MariaDB refuse it (error 1582: incorrect parameter count, their ISNULL(x) only tests for NULL) and "
                      f"PostgreSQL has no ISNULL function. COALESCE returns the first value that isn't NULL on every database: {new}.",
                      f"MySQL/MariaDB error 1582 dete hain, PostgreSQL mein ISNULL hai hi nahi. COALESCE har database pe chalta hai: {new}."),
                    (m.start(), b + 1, new)))

    # 4. IFNULL / NVL: fine on MySQL (NVL only on MariaDB), missing on PostgreSQL. COALESCE gives the same everywhere.
    for m in re.finditer(r"\b(ifnull|nvl)\s*\(", s, re.I):
        fn = m.group(1).lower()
        a = m.end() - 1
        b = _close(s, a)
        args = _split_args(sql[a + 1:b])
        if len(args) != 2 or (eng == "mysql" and fn == "ifnull"):
            continue
        new = f"COALESCE({args[0]}, {args[1]})"
        pg = eng == "postgresql"
        out.append((F("error" if pg else "check", "null_fn", f"{fn.upper()}() doesn't exist on PostgreSQL" if fn == "ifnull" else
                      "NVL() works only on Oracle and MariaDB", f"{fn.upper()}() har database pe nahi chalta", sql[m.start():b + 1][:90],
                      ("IFNULL works on MySQL and MariaDB but PostgreSQL has no such function. " if fn == "ifnull" else
                       "NVL is Oracle's; MariaDB accepts it, MySQL and PostgreSQL don't. ")
                      + f"COALESCE does the same thing and works everywhere: {new}.",
                      f"{fn.upper()} har database pe nahi hai. COALESCE wahi kaam har jagah karta hai: {new}."), (m.start(), b + 1, new)))

    # 5-7. LIKE patterns
    for m in re.finditer(r"\blike\s+'((?:[^'\\]|\\.|'')*)'(\s+escape\s+'[^']*')?", sql, re.I):
        pat, esc = m.group(1), m.group(2)
        if dep[m.start()] < 0:
            continue
        lit_a, lit_b = m.start(1) - 1, m.end(1) + 1
        if "*" in pat and "%" not in pat:                 # file-style wildcards
            new = "'" + pat.replace("*", "%").replace("?", "_") + "'"
            out.append((F("wrong", "like", "* is not a wildcard in LIKE: use %", "LIKE mein * wildcard nahi hai: % lagao", sql[m.start():m.end()][:80],
                          f"SQL's LIKE wildcards are % (any characters) and _ (one character); * and ? are plain characters, so LIKE '{pat}' "
                          f"only finds text that really contains '*', usually nothing (MySQL and PostgreSQL alike). Use LIKE {new}.",
                          f"LIKE ke wildcard % aur _ hain; * aur ? saadharan akshar hain, isliye LIKE '{pat}' aksar kuch nahi deta. LIKE {new} likho."),
                        (lit_a, lit_b, new)))
            continue
        if "%" not in pat and "_" not in pat and pat:
            want = "contains" if CONTAINS.search(said) else "starts" if STARTS.search(said) else "ends" if ENDS.search(said) else ""
            new = {"contains": f"'%{pat}%'", "starts": f"'{pat}%'", "ends": f"'%{pat}'"}.get(want)
            out.append((F("check", "like", f"LIKE '{pat}' has no wildcard, so it only finds exactly '{pat}'",
                          f"LIKE '{pat}' mein koi wildcard nahi, ye sirf poora '{pat}' dhoondhta hai", sql[m.start():m.end()][:80],
                          "Without % or _ a LIKE works like =: whole-value match (case-insensitive on MySQL/MariaDB with the default "
                          "collation, case-sensitive on PostgreSQL). For 'contains' write '%" + pat + "%', for 'starts with' '" + pat + "%'.",
                          "% ya _ ke bina LIKE = jaisa hai: poori value milti hai. 'Jisme ho' ke liye '%" + pat + "%', 'shuru ho' ke liye '" + pat + "%'."),
                        (lit_a, lit_b, new) if new else None))
            continue
        if not esc and re.search(r"[A-Za-z0-9]_[A-Za-z0-9]", pat) and not re.fullmatch(r"[_%]*", pat):
            body = re.sub(r"(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])", "!_", pat.replace("!", "!!"))
            new = f"'{body}' ESCAPE '!'"
            shown = re.sub(r"(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])", "1", pat).replace("%", "")
            out.append((F("check", "like", f"_ in LIKE '{pat}' matches any one character, not only '_'",
                          f"LIKE '{pat}' mein _ koi bhi ek akshar pakadta hai, sirf '_' nahi", sql[m.start():m.end()][:80],
                          f"In LIKE, _ is a wildcard for exactly one character, so '{pat}' also matches values like '{shown}...'. If the "
                          f"underscore is part of the value, escape it: LIKE {new} (works the same on MySQL, MariaDB and PostgreSQL).",
                          f"LIKE mein _ ek akshar ka wildcard hai, isliye '{pat}' doosri values bhi pakad leta hai. Agar _ value ka hissa hai to "
                          f"escape karo: LIKE {new}."), (lit_a, m.end(), new)))

    # 8. DISTINCT(col), other: DISTINCT is not a function of one column
    dm = re.match(r"\s*select\s+distinct\s*\(\s*([`\"]?[\w$.]+[`\"]?)\s*\)\s*(?:as\s+\w+\s*)?,", s, re.I)
    if is_select and dm:
        out.append((F("check", "distinct", f"DISTINCT({dm.group(1)}) still applies to every selected column",
                      f"DISTINCT({dm.group(1)}) ab bhi saare chune columns pe lagta hai", sql[dm.start():dm.end()].strip()[:80],
                      f"DISTINCT is not a function: the brackets do nothing, and the whole row must repeat to be removed, so the same "
                      f"{dm.group(1)} still shows up once per different combination of the other columns. For one row per {dm.group(1)}, "
                      f"GROUP BY {dm.group(1)} and pick the other columns with MIN()/MAX() (or a window function).",
                      f"DISTINCT function nahi hai: poori row dohraani chahiye tabhi hat-ti hai. Har {dm.group(1)} ki ek row ke liye GROUP BY "
                      f"{dm.group(1)} aur baaki columns MIN()/MAX() se."), None))

    # 9. AND and OR mixed without brackets
    wm = None
    if is_select:
        cl = _clauses(s, dep)
        if "where" in cl:
            wm = cl["where"]
    else:
        w = re.search(r"\bwhere\b", s, re.I)
        if w:
            stop = re.search(r"\border\s+by\b|\blimit\b|\breturning\b|;", s[w.end():], re.I)
            wm = (w.end(), w.end() + stop.start() if stop else len(s))
    if wm:
        out += _and_or(sql, s, *wm, F)

    # 10. NULL in arithmetic: price - discount is NULL where discount is NULL (and SUM then skips that row)
    if is_select:
        cl = _clauses(s, dep)
        a, b = cl.get("select", (0, 0))
        seen = set()
        for m in re.finditer(r"(?<![\w.$`\"])([`\"]?[A-Za-z_][\w$]*[`\"]?\.)?[`\"]?([A-Za-z_][\w$]*)[`\"]?(?![\w$`\"(])", s[a:b]):
            name = m.group(2).lower()
            if name not in OPTIONAL_NUM:
                continue
            pos, end = a + m.start(), a + m.end()
            before, after = s[a:pos].rstrip(), s[end:b].lstrip()
            if not (before.endswith(("+", "-")) or re.match(r"[+-]", after)) or _inside_null_fn(s, pos) or pos in seen:
                continue
            seen.add(pos)
            op = sql[pos:end]
            new = f"COALESCE({op}, 0)"
            out += schema_keep((F("check", "null_math", f"If {op} is NULL, the whole calculation is NULL", f"{op} NULL ho to poora hisaab NULL",
                          f"{op} in: " + _expr_around(sql, pos, end)[:80],
                          f"Arithmetic with NULL gives NULL on every database, so a row with no {name} gets NULL instead of a number "
                          f"(and SUM/AVG then skip that row). If a missing {name} means 0, write {new}.",
                          f"NULL ke saath hisaab NULL deta hai, isliye bina {name} wali row ka result NULL. Agar khaali ka matlab 0 hai to {new}."),
                        (pos, end, new)), [("not_null", (m.group(1) or "").strip('.`"') or None, m.group(2).strip('`"'))])

    # 11. CONCAT with a part that is often missing: NULL for the whole value on MySQL/MariaDB
    if eng != "postgresql":
        for m in re.finditer(r"\bconcat\s*\(", s, re.I):
            a = m.end() - 1
            b = _close(s, a)
            args = _split_args(sql[a + 1:b])
            pos = a + 1
            for arg in args:
                k = sql.index(arg, pos)
                pos = k + len(arg)
                if _col(arg) in OPTIONAL_TEXT and re.fullmatch(r"[`\"]?[\w$.]+[`\"]?", arg):
                    new = f"COALESCE({arg}, '')"
                    out += schema_keep((F("check", "concat", f"CONCAT(...) is NULL on MySQL/MariaDB when {arg} is NULL",
                                  f"MySQL/MariaDB pe {arg} NULL ho to CONCAT(...) poora NULL", sql[m.start():b + 1][:90],
                                  f"On MySQL and MariaDB CONCAT returns NULL if any part is NULL, so a person with no {_col(arg)} gets no name "
                                  f"at all (PostgreSQL's CONCAT skips NULL parts). Write {new} inside CONCAT, or use CONCAT_WS(' ', ...), "
                                  f"which skips NULLs on all of them.",
                                  f"MySQL/MariaDB pe CONCAT ka ek hissa NULL ho to poora NULL. CONCAT ke andar {new} likho, ya CONCAT_WS(' ', ...)."),
                                (k, k + len(arg), new)), [("not_null", arg.split(".")[0].strip('`"') if "." in arg else None,
                                                           arg.split(".")[-1].strip('`"'))])

    # 12. integer division: COUNT(...) / COUNT(...) is 0 on PostgreSQL for any share below 100%
    for m in re.finditer(r"\bcount\s*\(", s, re.I):
        a = m.end() - 1
        b = _close(s, a)
        nxt = re.match(r"\s*/\s*(count\s*\(|\d+\b(?!\.))", s[b + 1:], re.I)
        if not nxt or eng == "mysql":
            continue
        if re.search(r"\d+\.\d+\s*\*\s*$|::\s*\w+\s*$|cast\s*\(\s*$", s[:m.start()], re.I):
            continue
        sure = eng == "postgresql"
        out.append((F("wrong" if sure else "check", "division", "COUNT(...) / COUNT(...) is whole-number division on PostgreSQL",
                      "PostgreSQL pe COUNT(...) / COUNT(...) poore number ka bhaag hai", sql[m.start():b + 1 + nxt.end()][:90],
                      "On PostgreSQL an integer divided by an integer drops the fraction, so a share like 3/8 comes out as 0 (and "
                      "x 100 is still 0). MySQL and MariaDB keep the decimals. Multiply by 1.0 first so every database keeps them: "
                      f"1.0 * {sql[m.start():b + 1]} / ...",
                      "PostgreSQL pe integer / integer ka decimal hissa kat jaata hai (3/8 = 0). Pehle 1.0 se guna karo: "
                      f"1.0 * {sql[m.start():b + 1]} / ..."), (m.start(), m.start(), "1.0 * ")))

    # 13-15. ORDER BY ... LIMIT: NULLs first on one engine, ties, and LIMIT with no order at all
    if is_select:
        out += _order_limit(sql, s, dep, eng, F)
    out += _double_quoted(sql, s, eng, F)
    return out


def _order_limit(sql, s, dep, eng, F) -> list:
    from app.agents.sqlcorrect import _clauses, _sources

    out = []
    cl = _clauses(s, dep)
    if "limit" not in cl or "union" in cl:
        return out
    if "order by" not in cl:
        lim = sql[cl["limit"][0]:cl["limit"][1]].strip().rstrip(";")
        whole = ("group by" not in cl and re.search(r"\b(count|sum|avg|min|max)\s*\(", s[cl["select"][0]:cl["select"][1]], re.I))
        one = re.search(r"\bwhere\b[^;]*\b(id)\s*=\s*\S", s, re.I)
        if not whole and not one and not re.fullmatch(r"0", lim):
            out.append((F("check", "stable_order", f"LIMIT {lim} without ORDER BY returns whichever rows come first",
                          f"ORDER BY ke bina LIMIT {lim} jo rows pehle milein wahi deta hai", f"LIMIT {lim}",
                          "Without ORDER BY the database promises no order, so 'the first rows' can differ between runs, after an "
                          "index change or an upgrade (MySQL, MariaDB and PostgreSQL alike). Add the order you mean, ending with a unique "
                          "column, e.g. ORDER BY created_at DESC, id DESC.",
                          "ORDER BY ke bina koi kram tay nahi, isliye 'pehli rows' har baar alag ho sakti hain. Jo kram chahiye wo likho, "
                          "aakhir mein unique column ke saath."), None))
        return out
    oa, ob = cl["order by"]
    keys = [(p, t) for p, t in _split_top_(s[oa:ob])]
    if not keys:
        return out
    p0, first = keys[0]
    km = re.fullmatch(r"\s*([`\"]?[\w$]+[`\"]?\.)?([`\"]?[\w$]+[`\"]?)(\s+(asc|desc))?\s*", first, re.I)
    if km and _col(km.group(2)) in OPTIONAL_SORT and "is null" not in s[oa:ob].lower():
        desc = (km.group(4) or "").lower() == "desc"
        nulls_first_on = "PostgreSQL" if desc else "MySQL and MariaDB"
        if not ((eng == "mysql" and desc) or (eng == "postgresql" and not desc)):
            col = (km.group(1) or "") + km.group(2)
            start = oa + p0 + (len(first) - len(first.lstrip()))
            end = oa + p0 + len(first.rstrip())
            new = f"{col} IS NULL, {sql[start:end]}"
            out.append((F("check", "nulls_order", f"ORDER BY {_col(col)}{' DESC' if desc else ''} + LIMIT: on {nulls_first_on} the NULLs come first",
                          f"{nulls_first_on} pe NULL wali rows pehle aati hain", sql[start:end],
                          f"Rows where {_col(col)} is NULL sort first {'in DESC on PostgreSQL' if desc else 'in ASC on MySQL and MariaDB'}, "
                          f"so the 'top' rows can be the ones with no {_col(col)} at all. ORDER BY {new} puts them last on every database.",
                          f"NULL wali rows pehle aa jaati hain, isliye 'top' mein khaali {_col(col)} wali rows aa sakti hain. ORDER BY {new} "
                          f"unhe har database pe aakhir mein rakhta hai."), (start, end, new)))
    srcs = _sources(s, dep, *cl["from"]) if "from" in cl else []
    if len(srcs) == 1 and srcs[0].name and "group by" not in cl and "distinct" not in s[cl["select"][0]:cl["select"][1]].lower()[:12]:
        names = [_col(re.split(r"\s+", t.strip())[0]) for _p, t in keys]
        from app.agents.sqljoins import key_names

        own = {k.replace("_", "") for k in key_names(srcs[0].name)}
        if names and not any(n in ("id", "uuid", "email", "sku", "code") or n.replace("_", "") in own for n in names) \
                and not any(re.search(r"\(", t) for _p, t in keys):
            q = (srcs[0].alias + ".") if srcs[0].alias and srcs[0].alias.lower() != srcs[0].name.split(".")[-1].lower() else ""
            end = oa + len(s[oa:ob].rstrip().rstrip(";").rstrip())
            out += schema_keep((F("check", "stable_order", "Ties in the ORDER BY make the LIMIT pick rows at random",
                          "ORDER BY mein barabari ho to LIMIT koi bhi row chun leta hai", sql[oa:end].strip()[:80],
                          f"If two rows have the same {', '.join(names)}, the database may return them in either order, so which of the "
                          f"tied rows make the cut (or land on the next page) can change between runs. End the ORDER BY with a unique "
                          f"column: ORDER BY {sql[oa:end].strip()}, {q}id (use your table's own key if it isn't id).",
                          f"Agar do rows ka {', '.join(names)} barabar ho to kram tay nahi, aur LIMIT mein kaunsi aayegi ye badal sakta hai. "
                          f"Aakhir mein unique column jodo: ... , {q}id."), (end, end, f", {q}id")),
                               [("unique", srcs[0].alias or srcs[0].name, names)])
    return out


def _id_of(table: str) -> str:
    """The table's usual key name: customers -> customer_id (a prefix like wp_ or fz12_ is not part of it)."""
    from app.agents.sqljoins import _fk

    return _fk(table)


def _double_quoted(sql, s, eng, F) -> list:
    """col = "Pune": a string on MySQL/MariaDB, a column name on PostgreSQL (and in ANSI_QUOTES mode)."""
    out = []
    if eng == "mysql":
        return out
    for m in re.finditer(r"(=|<>|!=|\blike)\s*\"([^\"\n]*)\"(?!\s*\.)", sql, re.I):
        if re.search(r"\.\s*$", sql[:m.start(2) - 1]):
            continue
        val = m.group(2)
        if eng == "postgresql" and re.fullmatch(r"[a-z_][a-z0-9_]*", val):
            continue                                          # "col" with a plain lower-case name: really a column on PostgreSQL
        new = "'" + val.replace("'", "''") + "'"
        pg = eng == "postgresql"
        out.append((F("error" if pg else "check", "quotes", f"\"{val}\" is a column name on PostgreSQL, not text",
                      f"PostgreSQL pe \"{val}\" column ka naam hai, text nahi", sql[m.start():m.end()][:80],
                      "Text values go in single quotes. MySQL and MariaDB accept double quotes for text by default, but PostgreSQL (and "
                      f"MySQL with ANSI_QUOTES) read \"{val}\" as a column name: column \"{val}\" does not exist. {new} works on all of them.",
                      f"Text single quotes mein likho. PostgreSQL \"{val}\" ko column samajhta hai. {new} har jagah chalta hai."),
                    (m.start(2) - 1, m.end(2) + 1, new)))
    return out


def _and_or(sql, s, a, b, F) -> list:
    """WHERE a = 1 OR a = 2 AND b > 3: AND is done first, so it means a = 1 OR (a = 2 AND b > 3)."""
    from app.agents.sqlcorrect import _depths

    text = s[a:b]
    stop = re.search(r"\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\blimit\b|\bunion\b|;", text, re.I)
    if stop:
        text = text[:stop.start()]
    d = _depths(text)
    ops = [m for m in re.finditer(r"\b(and|or)\b", text, re.I) if d[m.start()] == 0 and
           not (m.group(1).lower() == "and" and re.search(r"\bbetween\s+\S+\s*$", text[:m.start()], re.I))]
    kinds = {m.group(1).lower() for m in ops}
    if kinds != {"and", "or"}:
        return []
    cuts = [0] + [x for m in ops for x in (m.start(), m.end())] + [len(text)]
    parts = [(cuts[k], cuts[k + 1]) for k in range(0, len(cuts), 2)]
    words = [m.group(1).lower() for m in ops]
    out_edit = None
    k = 0
    while k < len(words):
        if words[k] != "or":
            k += 1
            continue
        j = k
        while j < len(words) and words[j] == "or":
            j += 1
        run = parts[k:j + 1]                               # the OR-ed parts k..j
        lefts = {re.match(r"\s*([`\"]?[\w$.]+[`\"]?)\s*(=|in\b|like\b)", text[x:y], re.I) for x, y in run}
        cols = {m.group(1).lower() for m in lefts if m}
        if len(cols) == 1 and all(lefts) and (k > 0 or j < len(words)):
            x, y = run[0][0], run[-1][1]
            lead = len(text[x:y]) - len(text[x:y].lstrip())
            tail = len(text[x:y]) - len(text[x:y].rstrip())
            xs, ys = a + x + lead, a + y - tail
            out_edit = (xs, ys, "(" + sql[xs:ys] + ")")
            break
        k = j
    ev = re.sub(r"\s+", " ", sql[a:a + len(text)]).strip()[:100]
    fixed = out_edit[2] if out_edit else ""
    return [(F("check", "and_or", "AND and OR mixed without brackets: AND is done first",
               "AND aur OR bina brackets ke: AND pehle lagta hai", ev,
               "AND binds tighter than OR on every database, so a OR b AND c means a OR (b AND c): the condition after AND only "
               "applies to the last OR part. " + (f"If the OR is one choice, bracket it: {fixed}." if fixed else
                                                    "Put brackets around the part that belongs together."),
               "AND, OR se pehle lagta hai, isliye a OR b AND c ka matlab a OR (b AND c). " + (f"OR wale hisse pe bracket lagao: {fixed}." if fixed
                                                                                            else "Jo hissa saath hai uspe bracket lagao.")),
             out_edit)]


def _close(s: str, i: int) -> int:
    from app.agents.sqlcorrect import _close as c

    return c(s, i)


def _split_args(text: str) -> list[str]:
    out, depth, cur, q = [], 0, "", None
    for ch in text:
        if q:
            cur += ch
            if ch == q:
                q = None
            continue
        if ch in "'\"":
            q = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _split_top_(text: str) -> list[tuple[int, str]]:
    from app.agents.sqlcorrect import _split_top

    return [(p, t.rstrip().rstrip(";")) for p, t in _split_top(text) if t.strip().rstrip(";").strip()]


def _expr_around(sql: str, a: int, b: int) -> str:
    x = max(sql.rfind(",", 0, a), sql.lower().rfind("select", 0, a) + 5)
    y = sql.find(",", b)
    y = len(sql) if y < 0 else y
    return re.sub(r"\s+", " ", sql[x + 1:y]).strip()


@lru_cache(maxsize=8)
def _refuses(fam: str) -> list:
    data = json.loads((DIALECTS / f"{fam}.json").read_text(encoding="utf-8"))
    return [{**r, "_re": re.compile(r["re"], re.I)} for r in data.get("refuses", [])]


def _foreign_syntax(sql: str, s: str, F) -> list:
    """The person named (or chose) a database, and the query uses syntax that database doesn't have: it refuses the query.
    What each database refuses, its own error, and the rewrite are in its dialect file ('refuses'), proven in the lab."""
    from app.dbknow import engines as E

    chosen = CHOSEN.get()
    fam = E.syntax_family(chosen)
    if not fam:
        return []
    name = E.label(chosen)
    out = []
    for r in _refuses(fam):
        if r.get("unless") and re.search(r["unless"], s, re.I):
            continue                                 # a wider form of it is in the query (OFFSET ... FETCH): that entry says it
        after = re.search(r["after"], s, re.I) if r.get("after") else None
        if r.get("after") and not after:
            continue                                 # only inside that clause (VALUES(col) after ON DUPLICATE KEY UPDATE)
        for m in r["_re"].finditer(s):
            if after and m.start() < after.end():
                continue
            if r.get("not_inside_function") and _inside_null_fn(sql, m.start()):
                continue
            ev = sql[m.start():m.end()]
            new = _bare_ident(m.group(1)) if r.get("to") == "@bare" else m.expand(r["to"]) if r.get("to") is not None else None
            fen = r["fix_en"].replace("{db}", name) + (f" Here: {ev.strip()} -> {new.strip()}." if new is not None and new.strip() else "")
            fhi = r["fix_hi"].replace("{db}", name) + (f" Yahan: {ev.strip()} -> {new.strip()}." if new is not None and new.strip() else "")
            seen_ev = {f.evidence for f, _e in out}
            shown = ev.strip()[:80]
            if shown in seen_ev:                     # the same word twice (SELECT `id` ... ORDER BY `id`): say where
                shown = re.sub(r"\s+", " ", sql[max(0, m.start() - 12):m.end()]).strip()[:80]
            out.append((F("error", "dialect", r["title_en"].replace("{db}", name), r["title_hi"].replace("{db}", name), shown,
                          f"{name} refuses it ({r['error']}). " + fen, f"{name} ise mana karta hai ({r['error']}). " + fhi),
                        (m.start(), m.end(), new) if new is not None else None))
            if r.get("once"):
                break
    return out


RESERVED = set("""all and any as asc between both by case check column constraint create cross current_date current_time current_user
default desc distinct do else end except false fetch for foreign from full grant group having in inner into is join leading left
like limit natural not null offset on only or order outer primary references right select table then to trailing true union unique
user using when where window with""".split())


def _bare_ident(name: str) -> str:
    """A backticked name for PostgreSQL: bare when it is a plain name (unquoted names match however the table was written
    without quotes), in double quotes when it is a reserved word or has spaces or signs."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and name.lower() not in RESERVED:
        return name
    return '"' + name.replace('"', '""') + '"'
