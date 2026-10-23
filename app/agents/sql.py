"""SQL Query Checker agent: finds why a query is slow and writes the fixes.

Works on the query text alone (no database access needed). If the person also pastes
EXPLAIN output or CREATE TABLE statements, the checks get sharper.
"""

import datetime as _dt
import re
from dataclasses import dataclass, field


KEYWORDS = {"where", "on", "left", "right", "inner", "outer", "cross", "join", "group", "order", "limit",
            "having", "union", "using", "set", "values", "select", "as", "natural", "straight_join"}


@dataclass
class SqlFinding:
    severity: int
    title_en: str
    title_hi: str
    evidence: str
    fix_en: str
    fix_hi: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Table:
    name: str
    alias: str
    eq: list[str] = field(default_factory=list)       # columns compared with a value using =
    rng: list[str] = field(default_factory=list)      # range / prefix-LIKE columns
    join: list[str] = field(default_factory=list)     # join columns
    order: list[str] = field(default_factory=list)


_ASSIGN = r"(?:\$\w+|(?:(?:var|let|const|final|val|string|String|str)\s+)*[A-Za-z_][\w.]*)(?:\[[^\]]*\])?\s*[.+]?=\s*"   # $sql = / const q =
_CALL = r"[A-Za-z_$][\w.$]*(?:->\w+)*\s*\(\s*(?:\$?\w+\s*,\s*)?"                                             # cursor.execute( / $db->query($c,
_CODE_START = re.compile(rf"^\s*(?:{_ASSIGN}(?:{_CALL})?|{_CALL})(?=<<<|[rRfFbBuU]{{0,2}}[\"'`])")


def _unclosed_quote(q: str) -> int | None:
    """Where a quote opens that never closes (strings and comments read the way SQL reads them), or None."""
    i, n, state, start = 0, len(q), None, None
    while i < n:
        c = q[i]
        if state:
            if c == "\\":
                i += 2
                continue
            if c == state:
                if q[i + 1:i + 2] == state:          # 'it''s'
                    i += 2
                    continue
                state = None
        elif c in "'\"`":
            state, start = c, i
        elif q.startswith("--", i) or c == "#":
            j = q.find("\n", i)
            i = n if j < 0 else j
            continue
        elif q.startswith("/*", i):
            j = q.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        i += 1
    if state is None:
        return None
    while start > 0 and q[start - 1] == state:          # the rest of a closing \"\"\"
        start -= 1
    return start


def unwrap(query: str) -> str:
    """A query pasted straight from code: $sql = "SELECT ...";  ->  SELECT ...  (and the indentation tidied).
    Also PHP heredoc (<<<SQL ... SQL;), JS template strings, Python strings and calls like cursor.execute(\"\"\"...\"\"\")."""
    q = (query or "").strip()
    m = _CODE_START.match(q)
    rest = q[m.end():] if m else q
    rest = re.sub(r"^[rRfFbBuU]{1,2}(?=[\"'`])", "", rest) if m else rest
    body = None
    h = re.match(r"<<<\s*['\"]?(\w+)['\"]?[ \t]*\r?\n", rest)
    if h:                                            # heredoc: up to the line that closes it
        end = re.search(rf"\r?\n[ \t]*{h.group(1)}\b[^\n]*$", rest[h.end():])
        body = rest[h.end():][:end.start()] if end else rest[h.end():]
    else:
        for tok in ('"""', "'''", '"', "'", "`"):
            if rest.startswith(tok) and (m or tok in ('"""', "'''", "`") or rest.rstrip().rstrip(";").rstrip().endswith(tok)):
                last = rest.rfind(tok)
                if last >= len(tok):
                    body = rest[len(tok):last]
                break
    if body is not None:
        q = body
    else:                                            # the code before it was cut off: drop a closing quote left alone
        cut = _unclosed_quote(q)
        if cut is not None and re.fullmatch(r"[\"'`]*\s*(?:,[^\"'`]*)?\)?\s*;?\s*", q[cut:]):
            q = q[:cut]
    lines = [ln.rstrip() for ln in q.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    pad = min((len(ln) - len(ln.lstrip()) for ln in lines[1:] if ln.strip()), default=0)
    return "\n".join([lines[0].strip()] + [ln[pad:] for ln in lines[1:]]).strip() if lines else ""


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"(--|#)[^\n]*", " ", sql)


def _mask_strings(sql: str) -> str:
    """Every string becomes '?' ('%?' when it starts with %, so a leading-wildcard LIKE is still visible)."""
    return re.sub(r"'(?:[^'\\]|\\.)*'", lambda m: "'%?'" if m.group(0).startswith("'%") else "'?'", sql)


def _clause(sql: str, start: str, stops: tuple[str, ...]) -> str:
    m = re.search(rf"\b{start}\b(.*?)(?=\b(?:{'|'.join(stops)})\b|$)", sql, re.I | re.S)
    return m.group(1).strip() if m else ""


def _tables(sql: str) -> list[Table]:
    tables: list[Table] = []
    for m in re.finditer(r"\b(?:from|join)\s+([`\"\w.]+)(?:\s+(?:as\s+)?([`\w]+))?", sql, re.I):
        name = m.group(1).strip('`"')
        if name.lower() in KEYWORDS or name.startswith("("):
            continue
        alias = (m.group(2) or "").strip("`")
        if not alias or alias.lower() in KEYWORDS:
            alias = name.split(".")[-1]
        tables.append(Table(name=name, alias=alias))
    # comma-joined tables: FROM a, b
    m = re.search(r"\bfrom\s+(.*?)(?=\bwhere\b|\bgroup\b|\border\b|\blimit\b|$)", sql, re.I | re.S)
    if m and "," in m.group(1) and "(" not in m.group(1):
        for part in m.group(1).split(",")[1:]:
            bits = part.strip().split()
            if bits:
                name = bits[0].strip('`"')
                alias = bits[-1].strip("`") if len(bits) > 1 else name
                tables.append(Table(name=name, alias=alias))
    return tables


def _owner(col: str, tables: list[Table]) -> tuple[Table | None, str]:
    col = col.strip("`\"")
    if "." in col:
        alias, name = col.split(".", 1)
        for t in tables:
            if t.alias == alias or t.name.split(".")[-1] == alias:
                return t, name.strip("`")
        return None, name
    return (tables[0] if len(tables) == 1 else None), col


def analyze_sql(query: str, extra: str = "") -> dict:
    """query: the SQL. extra: optional EXPLAIN output and/or CREATE TABLE statements."""
    original = unwrap(query)
    if not original:
        return {"ok": False, "error": "No query given", "findings": []}
    if not re.match(r"^\s*(\(|with\b|select\b|update\b|delete\b)", _strip_comments(original), re.I):
        return {"ok": False, "error": "This checker works on SELECT, UPDATE and DELETE queries", "findings": []}
    sql = _mask_strings(_strip_comments(original))
    low = sql.lower()
    findings: list[SqlFinding] = []
    tables = _tables(sql)

    def add(sev, ten, thi, ev, fen, fhi):
        findings.append(SqlFinding(sev, ten, thi, ev, fen, fhi))

    if re.search(r"\bselect\s+(distinct\s+)?([`\"]?\w+[`\"]?\.)?\*", low):
        add(2, "SELECT * reads every column", "SELECT * har column padhta hai", "SELECT *",
            "Select only the columns you show or use; it reads less data and can use covering indexes.",
            "Sirf wahi columns lo jo chahiye; data kam padha jaata hai aur covering index kaam aata hai.")

    where = _clause(sql, "where", ("group by", "order by", "limit", "having", "union"))
    is_select = low.lstrip("( ").startswith(("select", "with"))
    if is_select and not where and not re.search(r"\blimit\b", low) and tables:
        add(3, "No WHERE and no LIMIT: reads the whole table", "WHERE aur LIMIT dono nahi: poori table padhi jaati hai",
            f"FROM {tables[0].name} without a filter",
            "Filter rows (WHERE) or paginate (LIMIT); for reports, pre-calculate totals in a summary table.",
            "Rows filter karo (WHERE) ya pagination (LIMIT); report ke liye totals pehle se summary table mein rakho.")
    if not is_select and not where:
        add(3, "UPDATE/DELETE without WHERE changes every row", "WHERE ke bina UPDATE/DELETE har row badal dega",
            "no WHERE clause", "Add a WHERE condition and test it with SELECT first.",
            "WHERE condition lagao aur pehle SELECT se test karo.")

    func = re.findall(r"\b(date|year|month|lower|upper|trim|substring|cast|concat|ifnull|coalesce)\s*\(\s*([`\w.]+)", where, re.I)
    rewritten = rewrite(original)
    reported = set()
    for f, c in func:
        f = f.upper()
        if f in reported:
            continue
        reported.add(f)
        if f in ("DATE", "YEAR", "MONTH") and rewritten != original and re.search(rf"\b{f}\s*\(", rewritten, re.I) is None:
            fen, fhi = ("Compare the raw column with a range instead (see the rewritten query).",
                        "Column ko seedha range se compare karo (rewritten query dekho).")
        elif f in ("DATE", "YEAR", "MONTH"):
            fen, fhi = (f"Compare the raw column with a date range instead, e.g. {c} >= '2026-01-01' AND {c} < '2026-02-01'.",
                        f"Column ko seedha date range se compare karo, jaise {c} >= '2026-01-01' AND {c} < '2026-02-01'.")
        elif f in ("LOWER", "UPPER"):
            fen, fhi = ("Compare the column as it is: MySQL's usual (_ci) collation already ignores case. In PostgreSQL use ILIKE, "
                        "or an index on the same expression.",
                        "Column ko seedha compare karo: MySQL ka aam (_ci) collation case pehle se ignore karta hai. PostgreSQL mein "
                        "ILIKE, ya usi expression pe index.")
        else:
            fen, fhi = ("Store the cleaned value in its own column (or index the same expression) and compare that column directly.",
                        "Saaf value alag column mein rakho (ya usi expression pe index) aur us column ko seedha compare karo.")
        add(3, f"{f}() on a column in WHERE blocks the index", f"WHERE mein column pe {f}() index ko rok deta hai",
            f"{f}({c}) in WHERE", fen, fhi)
    if re.search(r"\blike\s+'%\?'", where, re.I):
        add(2, "LIKE '%...' can't use an index", "LIKE '%...' index use nahi kar sakta", "leading % in LIKE",
            "Use prefix search (LIKE 'abc%') or a FULLTEXT index for word search.",
            "Prefix search (LIKE 'abc%') ya word search ke liye FULLTEXT index.")
    if _mixed_and_or(where):
        add(3, "AND and OR mixed without brackets: probably wrong results", "AND aur OR bina bracket ke: shayad galat result",
            "a AND b OR c", "AND runs before OR. Add brackets to say what you mean, e.g. a AND (b OR c).",
            "AND pehle chalta hai, OR baad mein. Bracket lagao, jaise a AND (b OR c).")
    if re.search(r"\bor\b", where, re.I):
        cols = {c.lower() for c in re.findall(r"([`\w.]+)\s*(?:=|in\b|like\b|>|<)", where, re.I)}
        if len(cols) > 1:
            add(2, "OR across different columns often skips indexes", "Alag columns pe OR aksar index chhod deta hai",
                "OR in WHERE", "Split into two queries joined with UNION ALL, each using its own index.",
                "Do queries banake UNION ALL se jodo, har ek apna index use kare.")
    if re.search(r"\bnot\s+in\s*\(\s*select\b", low):
        add(2, "NOT IN (SELECT ...) is slow and breaks on NULLs", "NOT IN (SELECT ...) slow hai aur NULL pe galat",
            "NOT IN subquery", "Use NOT EXISTS (or LEFT JOIN ... WHERE x IS NULL).", "NOT EXISTS (ya LEFT JOIN ... WHERE x IS NULL) use karo.")
    elif re.search(r"\bin\s*\(\s*select\b", low):
        add(1, "IN (SELECT ...) subquery", "IN (SELECT ...) subquery", "IN subquery",
            "Rewrite as a JOIN or EXISTS if it's slow.", "Slow ho to JOIN ya EXISTS mein badlo.")
    select_list = _clause(sql, "select", ("from",))
    if re.search(r"\(\s*select\b", select_list, re.I):
        add(3, "Subquery inside SELECT runs once per row", "SELECT ke andar subquery har row ke liye chalti hai",
            "correlated subquery in the column list", "Replace it with a JOIN plus GROUP BY.", "JOIN aur GROUP BY se badlo.")
    if "order by rand()" in low:
        add(3, "ORDER BY RAND() sorts the whole table", "ORDER BY RAND() poori table sort karta hai", "ORDER BY RAND()",
            "Pick a random id in code, or pre-shuffle into a small table.", "Code mein random id chuno, ya chhoti table mein pehle se shuffle.")
    offset = re.search(r"\blimit\s+(\d+)\s*,\s*\d+|\boffset\s+(\d+)", low)
    if offset and int(offset.group(1) or offset.group(2)) >= 10000:
        add(2, "Large OFFSET reads and throws away rows", "Bada OFFSET rows padh ke phenk deta hai", offset.group(0),
            "Use keyset pagination: WHERE id > last_seen_id ORDER BY id LIMIT n.",
            "Keyset pagination: WHERE id > last_seen_id ORDER BY id LIMIT n.")
    if re.search(r"\bfrom\s+[`\w.]+(\s+\w+)?\s*,\s*[`\w.]+", low) and not where:
        add(3, "Comma join without a condition multiplies rows", "Bina condition ke comma join rows guna kar deta hai",
            "FROM a, b without WHERE", "Use explicit JOIN ... ON with the matching columns.", "Saaf JOIN ... ON matching columns ke saath.")
    if re.search(r"\bselect\s+distinct\b", low) and re.search(r"\bjoin\b", low):
        add(1, "DISTINCT with JOIN may be hiding duplicate rows", "JOIN ke saath DISTINCT duplicate rows chhupa raha ho sakta hai",
            "SELECT DISTINCT ... JOIN", "Check the join conditions; aggregate with GROUP BY or EXISTS instead.",
            "Join conditions check karo; GROUP BY ya EXISTS use karo.")
    having = _clause(sql, "having", ("order by", "limit", "union"))
    if having and not re.search(r"\b(count|sum|avg|min|max)\s*\(", having, re.I):
        add(1, "HAVING filter could be in WHERE", "HAVING wala filter WHERE mein ho sakta hai", f"HAVING {having[:40]}",
            "Move non-aggregate conditions to WHERE so fewer rows are grouped.",
            "Bina aggregate wali condition WHERE mein le jao taaki kam rows group ho.")

    # ---- index suggestions from equality, range, join and order columns
    for cond in re.split(r"\band\b", where, flags=re.I):
        m = re.search(r"([`\w.]+)\s*(=|<=|>=|<|>|\bbetween\b|\blike\b|\bin\b)\s*(.+)", cond.strip(), re.I)
        if not m:
            continue
        col, op, rhs = m.group(1), m.group(2).lower(), m.group(3).strip()
        if col.lower() in KEYWORDS or re.match(r"^\d", col):
            continue
        table, name = _owner(col, tables)
        if not table:
            continue
        rhs_is_column = re.match(r"^[`a-z_][`\w]*\.[`\w]+$", rhs, re.I) is not None
        if rhs_is_column:
            table.join.append(name)
            other, oname = _owner(rhs, tables)
            if other:
                other.join.append(oname)
        elif op in ("=", "in"):
            table.eq.append(name)
        elif op == "like" and rhs.startswith("'%"):
            continue                              # LIKE '%x' can't use an index on this column
        else:
            table.rng.append(name)
    for m in re.finditer(r"\bon\s+(.+?)(?=\b(?:left|right|inner|join|where|group|order|limit)\b|$)", sql, re.I | re.S):
        for a, b in re.findall(r"([`\w]+\.[`\w]+)\s*=\s*([`\w]+\.[`\w]+)", m.group(1)):
            for side in (a, b):
                t, n = _owner(side, tables)
                if t:
                    t.join.append(n)
        for a in re.findall(r"([`\w]+\.[`\w]+)\s*=\s*(?:\d+|'[^']*'|\?)", m.group(1)):
            t, n = _owner(a, tables)
            if t:
                t.join.append(n)                     # 'AND fs.is_deleted = 0' in the ON: part of how that table is looked up
    order_clause = _clause(sql, "order by", ("limit", "union"))
    aliases = {a.strip("`").lower() for a in re.findall(r"\bas\s+([`\w]+)", _clause(sql, "select", ("from",)), re.I)}
    for col in re.findall(r"([`\w.]+)(?:\s+(?:asc|desc))?", order_clause, re.I):
        if col.lower() in ("asc", "desc", "rand") or col.strip("`").lower() in aliases or re.match(r"^\d+$", col):
            continue
        t, n = _owner(col, tables)
        if t:
            t.order.append(n)

    for _f, c in func:  # DATE(col) = ... is a range on col once rewritten
        t, n = _owner(c, tables)
        if t:
            t.rng.append(n)
    existing, seen_structure = _existing_indexes(extra)
    known = _structure_columns(extra)

    def clean(cols: list[str], table: str = "") -> list[str]:
        real = known.get(table.lower()) if table else None
        out: list[str] = []
        for c in cols:
            c = c.strip('`"')
            if real is not None:
                if c.lower() not in real:
                    continue                          # not a column of this table (the structure says so)
                c = real[c.lower()]
            if c and c.lower() not in KEYWORDS and c not in out and c != "?":
                out.append(c)
        return out

    indexes, skipped = [], []
    has_filter = {t.name for t in tables if clean(t.eq + t.rng + t.order)}
    for t in tables:
        short = t.name.split(".")[-1]
        have = existing.get(short.lower(), set()) | existing.get("*", set())
        # filter index: equality columns first, then ONE range column (or the ORDER BY columns)
        rng = clean(t.rng, short)
        filt = clean(t.eq + (rng[:1] if rng else t.order), short)[:4]
        if filt and filt[0].lower() not in have and not (_likely_primary(short, filt[0]) and short.lower() not in known):
            indexes.append(f"ALTER TABLE {t.name} ADD INDEX {_index_name(short, filt)} ({', '.join(filt)});")
        if t.name in has_filter and len(tables) > 1:
            continue                    # MySQL reads this table through its filter index; its join columns point outward
        joins = []
        for jc in clean(t.join, short):
            if jc.lower() in have and short.lower() in known:
                continue                      # the structure shows it's already indexed
            if _likely_primary(short, jc) and short.lower() not in known:
                skipped.append(f"{short}.{jc}")
            elif jc not in filt[:1]:
                joins.append(jc)
        if any(j.lower() in have for j in joins):
            joins = []                  # an index already starts with one of the join columns: MySQL can use it
        if joins:                       # one index for all the columns this table is joined on, not one each
            cols = joins[:4]
            indexes.append(f"ALTER TABLE {t.name} ADD INDEX {_index_name(short, cols)} ({', '.join(cols)});")
    check_query = None
    if indexes and not seen_structure:
        names = ", ".join(f"'{t.name.split('.')[-1]}'" for t in tables)
        check_query = ("SELECT TABLE_NAME, INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns\n"
                       "FROM information_schema.STATISTICS\n"
                       f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({names})\n"
                       "GROUP BY TABLE_NAME, INDEX_NAME;")
        add(2, "These columns need an index if they don't have one yet (I can't see your current indexes from the query alone)",
            "In columns pe index chahiye agar pehle se nahi hai (sirf query se aapke maujooda index nahi dikhte)",
            "columns used in WHERE / JOIN / ORDER BY",
            "Run the check query first, or paste the SHOW CREATE TABLE output of these tables in the second box: "
            "then only the really missing indexes are listed.",
            "Pehle check query chalao, ya in tables ka SHOW CREATE TABLE output doosre box mein paste karo: "
            "phir sirf sach mein missing index hi dikhenge.")
    elif indexes:
        add(2, "Missing indexes on the columns this query filters and joins on (checked against your table structure)",
            "Jin columns pe query filter aur join karti hai unpe index nahi hai (aapke table structure se check kiya)",
            "columns used in WHERE / JOIN / ORDER BY", "Add the indexes below.", "Neeche wale index lagao.")

    # ---- EXPLAIN output, if pasted
    if extra:
        ex = extra.lower()
        if re.search(r"\btype\b.*\ball\b|\|\s*all\s*\|", ex):
            add(3, "EXPLAIN shows a full table scan (type: ALL)", "EXPLAIN mein poori table scan (type: ALL)",
                "type = ALL", "The indexes above should turn this into ref/range.", "Upar ke indexes se ye ref/range ho jaana chahiye.")
        if "using filesort" in ex:
            add(2, "Sorting without an index (Using filesort)", "Bina index ke sorting (Using filesort)", "Using filesort",
                "Add the ORDER BY columns to the end of the index.", "ORDER BY wale columns index ke aakhir mein jodo.")
        if "using temporary" in ex:
            add(2, "Temporary table for GROUP BY/DISTINCT", "GROUP BY/DISTINCT ke liye temporary table", "Using temporary",
                "Index the GROUP BY columns, or pre-aggregate.", "GROUP BY columns pe index, ya pehle se aggregate.")

    findings.sort(key=lambda f: f.severity, reverse=True)
    notes = _notes(sql, tables, original)
    return {"ok": True, "findings": [f.as_dict() for f in findings], "indexes": indexes, "check_query": check_query,
            "indexes_verified": bool(seen_structure), "assumed_primary": skipped, "notes": notes,
            "rewritten": rewritten if rewritten != original else None, "tables": [t.name for t in tables]}


def _notes(sql: str, tables, original: str = "") -> list[dict]:
    """What this query's own shape means for speed, beyond rules that rewrite it (plain words, both languages)."""
    out = []
    low = sql.lower()
    group = _clause(sql, "group by", ("having", "order by", "limit", "union")).strip().rstrip(";")
    order = _clause(sql, "order by", ("limit", "union")).strip().rstrip(";")
    if group and order and re.sub(r"\s+", "", group.lower()) != re.sub(r"\s+|\b(asc|desc)\b", "", order.lower()):
        out.append({"en": f"It groups by {group.strip()} but sorts by {order.strip()}: MySQL builds the groups first, then sorts "
                          "them in a temporary table. That's normal for a report and fast when the WHERE keeps the rows few.",
                    "hi": f"Ye {group.strip()} se group karti hai par {order.strip()} se sort: MySQL pehle group banata hai, "
                          "phir temporary table mein sort. Report ke liye ye normal hai."})
    if re.search(r"\blimit\b", low) is None and (group or len(tables) > 2):
        out.append({"en": "There's no LIMIT, so it returns every matching row. If this is a list on a page, show it page by page "
                          "(LIMIT 50 OFFSET ...) or filter it (by center, batch, date): that is usually the biggest speed-up.",
                    "hi": "LIMIT nahi hai, isliye saari rows aati hain. Page pe list hai to page-wise dikhao (LIMIT 50 OFFSET ...) "
                          "ya filter lagao (center, batch, date): aksar sabse bada fayda yahi hota hai."})
    if re.search(r"\bcase\s+when\b[^;]*\blike\s+'%", (original or sql).lower()):
        out.append({"en": "The LIKE '%...%' checks inside CASE run on rows already found; they don't search the table, so they "
                          "don't need an index and aren't what makes it slow.",
                    "hi": "CASE ke andar LIKE '%...%' sirf mil chuki rows pe chalta hai, table search nahi karta; ye slow hone ki "
                          "wajah nahi hai."})
    if re.search(r"\bleft\s+join\b[^;]*?\bon\b[^;]*?\band\s+[`\w]+\.[`\w]+\s*=\s*(\d+|'[^']*')", low):
        out.append({"en": "The extra condition in the LEFT JOIN's ON (like is_deleted = 0) is in the right place: in WHERE it would "
                          "drop the rows that have no match.",
                    "hi": "LEFT JOIN ke ON mein extra condition (jaise is_deleted = 0) sahi jagah hai: WHERE mein daalne se bina "
                          "match wali rows hat jaati."})
    return out


def _index_name(table: str, cols: list[str]) -> str:
    """idx_<table>_<columns>, never longer than MySQL (64) and PostgreSQL (63) allow."""
    return f"idx_{table}_{'_'.join(cols)[:40]}"[:60].rstrip("_")


def _likely_primary(table: str, col: str) -> bool:
    """'id', or courses.course_id / batches.batch_id: almost always the table's own primary key (already indexed)."""
    c, t = col.lower().strip("`"), table.lower().strip("`")
    forms = {t, t[:-3] + "y" if t.endswith("ies") else t, t[:-1] if t.endswith("s") else t, t[:-2] if t.endswith("es") else t}
    return c == "id" or any(c in (f"{f}_id", f"{f}id") for f in forms)


_TNAME = r"[`\"]?(?:\w+[`\"]?\.[`\"]?)?(\w+)[`\"]?"
_NOT_COLUMN = {"primary", "key", "index", "unique", "constraint", "foreign", "check", "fulltext", "spatial", "exclude", "like"}


def _looks_postgres(text: str) -> bool:
    return not re.search(r"\bengine\s*=", text, re.I) and bool(
        re.search(r"\bpublic\.|using\s+btree|character varying|without time zone|\bserial\b|::|\bbigserial\b", text, re.I))


def _existing_indexes(extra: str) -> tuple[dict, bool]:
    """Indexes already there, from a pasted SHOW CREATE TABLE / SHOW INDEX / pg_dump --schema-only: {table: {first columns}},
    and whether any structure was pasted at all (without it, nothing about existing indexes is known)."""
    out: dict = {}
    text = extra or ""
    seen = False
    pg = _looks_postgres(text)
    keys = r"primary\s+key|unique\s+key|unique\s+index|unique|key|index" + ("" if pg else r"|foreign\s+key")  # InnoDB indexes a FK
    for m in re.finditer(rf"create\s+table\s+(?:if\s+not\s+exists\s+)?{_TNAME}\s*\((.*?)\)\s*(?:engine|;|$)", text, re.I | re.S):
        seen = True
        cols = out.setdefault(m.group(1).lower(), set())
        for k in re.finditer(rf"(?:{keys})\s*[`\"\w]*\s*\(\s*[`\"]?(\w+)", m.group(2), re.I):
            cols.add(k.group(1).lower())
        for k in re.finditer(r"^\s*[`\"]?(\w+)[`\"]?\s+\w+[^,\n]*\b(?:primary\s+key|unique)\b", m.group(2), re.I | re.M):
            cols.add(k.group(1).lower())
    for m in re.finditer(rf"alter\s+table\s+(?:only\s+)?(?:if\s+exists\s+)?{_TNAME}\s+add\s+(?:constraint\s+[`\"\w]+\s+)?"
                         rf"(?:{keys})\s*[`\"\w]*\s*\(\s*[`\"]?(\w+)", text, re.I):
        seen = True                                    # ALTER TABLE ... ADD CONSTRAINT x PRIMARY KEY (id) / ADD INDEX ...
        out.setdefault(m.group(1).lower(), set()).add(m.group(2).lower())
    for m in re.finditer(rf"create\s+(?:unique\s+)?index\s+(?:concurrently\s+)?(?:if\s+not\s+exists\s+)?[`\"\w]*\s*on\s+(?:only\s+)?{_TNAME}"
                         r"\s*(?:using\s+\w+\s*)?\(\s*[`\"]?(\w+)[`\"]?\s*[,)]", text, re.I):
        seen = True                                    # CREATE INDEX x ON public.t USING btree (col)
        out.setdefault(m.group(1).lower(), set()).add(m.group(2).lower())
    for m in re.finditer(r"^\|?\s*(\w+)\s*\|\s*[01]\s*\|\s*(\w+)\s*\|\s*1\s*\|\s*(\w+)\s*\|", text, re.M):
        seen = True                                    # SHOW INDEX rows: Table | Non_unique | Key_name | Seq 1 | Column
        out.setdefault(m.group(1).lower(), set()).add(m.group(3).lower())
    if not seen:
        for k in re.finditer(r"(?:key|index)\s+[`\w]*\s*\(\s*`?(\w+)", text, re.I):
            out.setdefault("*", set()).add(k.group(1).lower())
            seen = True
    return out, seen


def _structure_columns(extra: str) -> dict:
    """{table: {column in lower case: column as the table spells it}} from pasted CREATE TABLE statements."""
    out: dict = {}
    for m in re.finditer(rf"create\s+table\s+(?:if\s+not\s+exists\s+)?{_TNAME}\s*\((.*?)\)\s*(?:engine|;|$)", extra or "", re.I | re.S):
        cols = out.setdefault(m.group(1).lower(), {})
        for k in re.finditer(r"(?:^|,)\s*(`[^`]+`|\"[^\"]+\"|[A-Za-z_]\w*)\s+[A-Za-z]", m.group(2), re.M):
            name = k.group(1)
            bare = name.strip('`"')
            if name[0] not in '`"' and bare.lower() in _NOT_COLUMN:
                continue
            real = bare if name[0] in '`"' or not _looks_postgres(extra) else bare.lower()   # PostgreSQL folds unquoted names
            cols[bare.lower()] = real
    return out


_DAY = r"'(\d{4}-\d{2}-\d{2})'"
_DATE_CMP = re.compile(rf"(\bnot\s+)?\bdate\s*\(\s*([`\"\w.]+)\s*\)\s*(?:(between)\s+{_DAY}\s+and\s+{_DAY}|(>=|<=|<>|!=|=|>|<)\s*{_DAY})",
                       re.I)
_YEAR_CMP = re.compile(r"(\bnot\s+)?\byear\s*\(\s*([`\"\w.]+)\s*\)\s*(?:(between)\s+(\d{4})\s+and\s+(\d{4})|(>=|<=|<>|!=|=|>|<)\s*(\d{4}))\b",
                       re.I)
_ENDS_CONDITION = re.compile(r"^\s*(?:$|\)|;|\b(?:and|or|order|group|limit|having|union|offset|then|when|else|end)\b)", re.I)


def _range(col: str, lo: str | None, hi: str | None) -> str:
    """col >= lo AND col < hi (hi is the first day NOT included)."""
    parts = ([f"{col} >= '{lo}'"] if lo else []) + ([f"{col} < '{hi}'"] if hi else [])
    return " AND ".join(parts)


def _ranged(m, text: str, lo_hi) -> str:
    try:
        cond = lo_hi(m)
    except ValueError:                                # not a real date: leave it as written
        return m.group(0)
    if cond is None:
        return m.group(0)
    two = " AND " in cond
    if m.group(1):                                   # NOT DATE(x) = ...: keep it one condition
        return f"{m.group(1)}({cond})"
    if two and not _ENDS_CONDITION.match(text[m.end():]):
        return f"({cond})"
    return cond


def _date_bounds(m):
    col, between, d1, d2, op, d = m.group(2), m.group(3), m.group(4), m.group(5), m.group(6), m.group(7)
    day = lambda x, k=0: (_dt.date.fromisoformat(x) + _dt.timedelta(days=k)).isoformat()  # noqa: E731
    if between:
        return _range(col, day(d1), day(d2, 1))
    return {"=": lambda: _range(col, day(d), day(d, 1)), ">=": lambda: _range(col, day(d), None), ">": lambda: _range(col, day(d, 1), None),
            "<": lambda: _range(col, None, day(d)), "<=": lambda: _range(col, None, day(d, 1))}.get(op, lambda: None)()


def _year_bounds(m):
    col, between, y1, y2, op, y = m.group(2), m.group(3), m.group(4), m.group(5), m.group(6), m.group(7)
    jan = lambda x, k=0: f"{int(x) + k:04d}-01-01"  # noqa: E731
    if between:
        return _range(col, jan(y1), jan(y2, 1))
    return {"=": lambda: _range(col, jan(y), jan(y, 1)), ">=": lambda: _range(col, jan(y), None), ">": lambda: _range(col, jan(y, 1), None),
            "<": lambda: _range(col, None, jan(y)), "<=": lambda: _range(col, None, jan(y, 1))}.get(op, lambda: None)()


def rewrite(sql: str) -> str:
    """Safe, mechanical rewrites only (same results, index-friendly): DATE(col) / YEAR(col) compared with a constant
    becomes a range on the plain column, with the end date worked out here, so it runs the same on MySQL and PostgreSQL."""
    out = _DATE_CMP.sub(lambda m: _ranged(m, m.string, _date_bounds), sql)
    return _YEAR_CMP.sub(lambda m: _ranged(m, m.string, _year_bounds), out)


def _mixed_and_or(where: str) -> bool:
    """AND and OR side by side at the same bracket level (BETWEEN x AND y is not a logical AND)."""
    w = re.sub(r"\bbetween\b(.+?)\band\b", lambda m: "between" + m.group(1) + "_band_", where, flags=re.I | re.S)
    both = lambda g: bool(re.search(r"\band\b", g, re.I) and re.search(r"\bor\b", g, re.I))  # noqa: E731
    stack: list[list[str]] = [[]]
    for ch in w:
        if ch == "(":
            stack.append([])
        elif ch == ")" and len(stack) > 1:
            if both("".join(stack.pop())):
                return True
            stack[-1].append(" x ")
        else:
            stack[-1].append(ch)
    return any(both("".join(g)) for g in stack)
