"""SQL Query Checker agent: finds why a query is slow and writes the fixes.

Works on the query text alone (no database access needed). If the person also pastes
EXPLAIN output or CREATE TABLE statements, the checks get sharper.
"""

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


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"(--|#)[^\n]*", " ", sql)


def _mask_strings(sql: str) -> str:
    return re.sub(r"'(?:[^'\\]|\\.)*'", "'?'", sql)


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
    original = (query or "").strip()
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

    if re.search(r"\bselect\s+(distinct\s+)?(\w+\.)?\*", low):
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
    if func:
        f, c = func[0]
        add(3, f"{f.upper()}() on a column in WHERE blocks the index", f"WHERE mein column pe {f.upper()}() index ko rok deta hai",
            f"{f.upper()}({c}) in WHERE",
            "Compare the raw column with a range instead (see the rewritten query).",
            "Column ko seedha range se compare karo (rewritten query dekho).")
    if re.search(r"\blike\s+'\?'", where) and re.search(r"like\s+'%", _strip_comments(original), re.I):
        add(2, "LIKE '%...' can't use an index", "LIKE '%...' index use nahi kar sakta", "leading % in LIKE",
            "Use prefix search (LIKE 'abc%') or a FULLTEXT index for word search.",
            "Prefix search (LIKE 'abc%') ya word search ke liye FULLTEXT index.")
    bare = re.sub(r"\b\w+\s*\([^()]*\)", "f()", where)  # drop function calls like DATE(x)
    if re.search(r"\band\b", bare, re.I) and re.search(r"\bor\b", bare, re.I) and "(" not in bare.replace("f()", ""):
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
        elif op == "like" and "%'" not in rhs[:3]:
            table.rng.append(name)
        else:
            table.rng.append(name)
    for m in re.finditer(r"\bon\s+(.+?)(?=\b(?:left|right|inner|join|where|group|order|limit)\b|$)", sql, re.I | re.S):
        for a, b in re.findall(r"([`\w]+\.[`\w]+)\s*=\s*([`\w]+\.[`\w]+)", m.group(1)):
            for side in (a, b):
                t, n = _owner(side, tables)
                if t:
                    t.join.append(n)
    order_clause = _clause(sql, "order by", ("limit", "union"))
    for col in re.findall(r"([`\w.]+)(?:\s+(?:asc|desc))?", order_clause, re.I):
        if col.lower() in ("asc", "desc", "rand"):
            continue
        t, n = _owner(col, tables)
        if t:
            t.order.append(n)

    for _f, c in func:  # DATE(col) = ... is a range on col once rewritten
        t, n = _owner(c, tables)
        if t:
            t.rng.append(n)
    existing, seen_structure = _existing_indexes(extra)

    def clean(cols: list[str]) -> list[str]:
        out: list[str] = []
        for c in cols:
            c = c.strip("`")
            if c and c.lower() not in KEYWORDS and c not in out and c != "?":
                out.append(c)
        return out

    indexes, skipped = [], []
    has_filter = {t.name for t in tables if clean(t.eq + t.rng + t.order)}
    for t in tables:
        short = t.name.split(".")[-1]
        have = existing.get(short.lower(), set()) | existing.get("*", set())
        # filter index: equality columns first, then ONE range column (or the ORDER BY columns)
        filt = clean(t.eq + (t.rng[:1] if t.rng else t.order))[:4]
        if filt and filt[0].lower() not in have and not _likely_primary(short, filt[0]):
            indexes.append(f"ALTER TABLE {t.name} ADD INDEX idx_{short}_{'_'.join(filt)[:40]} ({', '.join(filt)});")
        if t.name in has_filter and len(tables) > 1:
            continue                    # MySQL reads this table through its filter index; its join columns point outward
        joins = []
        for jc in clean(t.join):
            if _likely_primary(short, jc):
                skipped.append(f"{short}.{jc}")
            elif jc not in filt[:1]:
                joins.append(jc)
        if any(j.lower() in have for j in joins):
            joins = []                  # an index already starts with one of the join columns: MySQL can use it
        if joins:                       # one index for all the columns this table is joined on, not one each
            cols = joins[:3]
            indexes.append(f"ALTER TABLE {t.name} ADD INDEX idx_{short}_{'_'.join(cols)[:40]} ({', '.join(cols)});")
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

    rewritten = rewrite(original)
    findings.sort(key=lambda f: f.severity, reverse=True)
    return {"ok": True, "findings": [f.as_dict() for f in findings], "indexes": indexes, "check_query": check_query,
            "indexes_verified": bool(seen_structure), "assumed_primary": skipped,
            "rewritten": rewritten if rewritten != original else None, "tables": [t.name for t in tables]}


def _likely_primary(table: str, col: str) -> bool:
    """'id', or courses.course_id / batches.batch_id: almost always the table's own primary key (already indexed)."""
    c, t = col.lower().strip("`"), table.lower().strip("`")
    forms = {t, t[:-3] + "y" if t.endswith("ies") else t, t[:-1] if t.endswith("s") else t, t[:-2] if t.endswith("es") else t}
    return c == "id" or any(c in (f"{f}_id", f"{f}id") for f in forms)


def _existing_indexes(extra: str) -> tuple[dict, bool]:
    """Indexes already there, from a pasted SHOW CREATE TABLE / SHOW INDEX: {table: {first columns}}, and whether any
    structure was pasted at all (without it, nothing about existing indexes is known)."""
    out: dict = {}
    text = extra or ""
    seen = False
    for m in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"]?(?:\w+[`\"]?\.[`\"]?)?(\w+)[`\"]?\s*\((.*?)\)\s*(?:engine|;|$)",
                         text, re.I | re.S):
        seen = True
        cols = out.setdefault(m.group(1).lower(), set())
        for k in re.finditer(r"(?:primary\s+key|unique\s+key|unique|key|index|foreign\s+key)\s*[`\w]*\s*\(\s*`?(\w+)", m.group(2), re.I):
            cols.add(k.group(1).lower())              # an InnoDB foreign key is indexed too
        for k in re.finditer(r"^\s*`?(\w+)`?\s+\w+[^,\n]*\bprimary\s+key\b", m.group(2), re.I | re.M):
            cols.add(k.group(1).lower())
    for m in re.finditer(r"^\|?\s*(\w+)\s*\|\s*[01]\s*\|\s*(\w+)\s*\|\s*1\s*\|\s*(\w+)\s*\|", text, re.M):
        seen = True                                    # SHOW INDEX rows: Table | Non_unique | Key_name | Seq 1 | Column
        out.setdefault(m.group(1).lower(), set()).add(m.group(3).lower())
    if not seen:
        for k in re.finditer(r"(?:key|index)\s+[`\w]*\s*\(\s*`?(\w+)", text, re.I):
            out.setdefault("*", set()).add(k.group(1).lower())
            seen = True
    return out, seen


def rewrite(sql: str) -> str:
    """Safe, mechanical rewrites only (same results, index-friendly)."""
    out = re.sub(r"\bdate\s*\(\s*([`\w.]+)\s*\)\s*=\s*'(\d{4}-\d{2}-\d{2})'",
                 lambda m: f"{m.group(1)} >= '{m.group(2)}' AND {m.group(1)} < '{m.group(2)}' + INTERVAL 1 DAY", sql, flags=re.I)
    out = re.sub(r"\byear\s*\(\s*([`\w.]+)\s*\)\s*=\s*(\d{4})",
                 lambda m: f"{m.group(1)} >= '{m.group(2)}-01-01' AND {m.group(1)} < '{int(m.group(2)) + 1}-01-01'", out, flags=re.I)
    return out
