"""Night 10A: find the SQL in a project's files and check it, read only.

A project keeps its queries in two places: .sql files (scripts, dumps, migrations) and strings inside code (PHP, JS/TS,
Python, Java), usually handed to a raw-query call (mysqli_query, PDO, $wpdb, DB::select, cursor.execute, pool.query ...).
The scanner reads each file the way its language does (strings, comments, heredocs, template strings), joins pieces glued
with the language's concatenation, and keeps a string only when it has a real SQL shape. A piece of code inside the SQL
(an interpolated or concatenated expression) is read by where it stands:

- where a value goes (after =, IN (, VALUES (, LIMIT, inside quotes): it is a value; the query is read, and putting a
  value into the SQL text instead of binding it is reported (a placeholder keeps data and code apart);
- where a name goes (FROM, JOIN, ORDER BY, or glued to a name like {$wpdb->prefix}posts): it is a name; the query is read;
- anywhere else (a whole WHERE built elsewhere, a query appended with .= / +=): the query is not complete in this file,
  so it is reported as "found, couldn't read completely" and never guessed. ORM / query-builder code is reported the same.

Placeholders (?, :name, $1, %s, %(name)s, %d) are values. Each finding is reported as file:line with the corrected query.
Nothing is ever written to the project.

Run: python -m app.agents.sqlscan check <folder> [--engine mysql|mariadb|postgresql]   (or: scripts/dbaba check <folder>)
"""

import contextvars
import json
import os
import re
import sys
from dataclasses import dataclass, field

SKIP_DIRS = {".git", ".svn", ".hg", "vendor", "node_modules", "bower_components", "build", "dist", "out", "target", "coverage",
             "__pycache__", ".venv", "venv", "env", ".tox", "site-packages", ".next", ".nuxt", ".cache", "cache", ".idea",
             ".vscode", "storage", "bin", "obj", "wp-admin", "wp-includes", ".pytest_cache", ".mypy_cache"}
LANGS = {".sql": "sql", ".php": "php", ".phtml": "php", ".inc": "php", ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js",
         ".ts": "js", ".tsx": "js", ".py": "py", ".java": "java"}
MAX_BYTES = 2_000_000

# calls that send their first string to the database as SQL
RAW_CALL = re.compile(
    r"(?:\b(?:mysqli_query|mysqli_prepare|mysqli_real_query|mysqli_multi_query|mysql_query|pg_query|pg_query_params|pg_prepare|"
    r"pg_send_query|sqlsrv_query|executeQuery|executeUpdate|prepareStatement|prepareCall|addBatch|queryForObject|queryForList|"
    r"queryForMap|createNativeQuery|executemany|executescript|execute|query|prepare|exec|get_results|get_var|get_row|get_col|"
    r"select|selectOne|insert|update|delete|statement|unprepared|raw|all|run|each|one|none|any|many|oneOrNone|manyOrNone|"
    r"text|sql|batchUpdate)|(?<![\w$])(?:sql|knex\.raw))\s*$")
STRONG_CALL = re.compile(r"(?:mysqli_|mysql_|pg_|sqlsrv_|\$wpdb\s*->|wpdb\s*->|DB\s*::|->\s*(?:query|prepare|exec)\b|"
                         r"\b(?:cursor|cur|conn|connection|db|pool|client|pdo|dbh|stmt|session|engine|knex|sequelize|jdbcTemplate|"
                         r"statement|con)\s*(?:\.|->)\s*\w+$|\b(?:execute|executemany|executeQuery|executeUpdate|prepareStatement|"
                         r"createNativeQuery)$)", re.I)
SQL_VAR = re.compile(r"(?:sql|query|qry|stmt|statement|select|insert|update|delete|ddl|dml)", re.I)
SHAPE = re.compile(
    r"""^\s*\(*\s*(?:
      select\b(?P<sel>.*?)\bfrom\s+(?P<from>[`"\[]?[\w$.{}:?%]+)
    | select\s+(?:\d+|now\(\)|current_\w+|version\(\)|last_insert_id\(\)|count\(|exists\s*\(|@@|1\b)
    | insert\s+(?:ignore\s+|low_priority\s+|delayed\s+)?into\s+[`"\[]?[\w$.{}:]+
    | replace\s+into\s+[`"\[]?[\w$.{}:]+
    | update\s+(?:low_priority\s+|ignore\s+|only\s+)?[`"\[]?[\w$.{}:]+[`"\]]?(?:\s+(?:as\s+)?\w+)?(?:\s+(?:inner\s+|left\s+)?join\b.*?)?\s+set\s
    | delete\s+(?:\w+\s+)?from\s+[`"\[]?[\w$.{}:]+
    | with\s+(?:recursive\s+)?\w+\s*(?:\([^)]*\))?\s+as\s*\(
    | create\s+(?:or\s+replace\s+)?(?:temporary\s+|unique\s+)?(?:table|index|view|procedure|function|trigger|sequence|database|schema)\b
    | alter\s+table\s | drop\s+(?:table|index|view|procedure|function|trigger|database)\b | truncate\s+(?:table\s+)?[\w`"]
    | show\s+(?:full\s+)?(?:tables|columns|index|indexes|create|variables|status|processlist)\b
    | call\s+\w+\s*\( | explain\s+(?:analyze\s+)?(?:select|update|delete|insert)\b
    )""", re.I | re.S | re.X)
PROSE_WORDS = {"a", "an", "the", "your", "my", "our", "their", "his", "her", "this", "these", "those", "please", "you", "we",
               "here", "there", "below", "above", "list", "menu", "option", "options", "file", "files", "cart", "it", "them"}
ORM = re.compile(
    r"(?:\bDB\s*::\s*table\s*\(|\b(?!DB\b|Schema\b)[A-Z]\w*\s*::\s*(?:where|query|find|findOrFail|all|first|create|select|with|whereIn|orderBy)\s*\(|"
    r"\.objects\s*\.\s*(?:filter|get|all|exclude|annotate|values|select_related|aggregate|raw)\s*\(|\bsession\s*\.\s*query\s*\(|"
    r"\.(?:findAll|findOne|findByPk|findMany|findUnique|findFirst|createQueryBuilder|getRepository)\s*\(|\bknex\s*\(\s*['\"]|"
    r"@Query\s*\(|\.createQuery\s*\()")
DANGLING = re.compile(r"\b(?:where|and|or|on|set|from|join|by|having|select|values|in|not|like|between|into|limit|offset)\s*\(?\s*$|,\s*$",
                      re.I)


@dataclass
class Piece:
    text: str          # the SQL text this piece adds (a literal's value, or '' for code)
    pos: list          # file offset of each character of text
    code: str = ""     # the code of an expression piece (interpolated or concatenated)
    at: int = 0        # file offset of the expression
    via: str = ""      # interp / concat / format


@dataclass
class Found:
    file: str
    line: int
    sql: str                       # the SQL as found (code pieces shown as {code})
    kind: str = "read"            # read / unread
    why: str = ""                 # for unread: why it couldn't be read completely
    findings: list = field(default_factory=list)
    corrected: str | None = None
    engine: str | None = None
    parameterized: str | None = None   # the same query with each value written into the text turned into a placeholder
    values: list = field(default_factory=list)   # the code that gives each placeholder its value, in order
    dropped: list = field(default_factory=list)  # findings the project's schema ruled out, with the reason (night 10B)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


# ---------------------------------------------------------------------------------------------------------------- lexing

_PY_ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", "'": "'", '"': '"', "`": "`", "$": "$", "b": "\b", "f": "\f"}


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _balanced(text: str, i: int, open_c: str = "{", close_c: str = "}") -> int:
    """i at an opening bracket: the index just after its partner (strings inside skipped)."""
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _php_var(text: str, i: int) -> int:
    """i at '$' of a simple interpolated PHP variable: $name, $name->prop, $name[key]; the index after it."""
    m = re.match(r"\$[A-Za-z_]\w*(?:->[A-Za-z_]\w*|\[[^\]\n]*\])?", text[i:])
    return i + m.end() if m else i


def _string(text: str, i: int, lang: str):
    """A string literal starting at i: (end, pieces, prefix) or None. Pieces: literal characters and interpolated code."""
    n = len(text)
    pieces, buf, pos = [], [], []

    def flush():
        if buf:
            pieces.append(Piece("".join(buf), list(pos)))
            buf.clear()
            pos.clear()

    def code(src, at, via="interp"):
        flush()
        pieces.append(Piece("", [], src, at, via))

    prefix = ""
    if lang == "py":
        m = re.match(r"([rRbBuUfF]{1,2})?(\"\"\"|'''|\"|')", text[i:])
        if not m:
            return None
        prefix = (m.group(1) or "").lower()
        q = m.group(2)
        raw, fmt = "r" in prefix, "f" in prefix
        j = i + m.end()
        while j < n:
            if text.startswith(q, j):
                flush()
                return j + len(q), pieces, prefix
            c = text[j]
            if c == "\n" and len(q) == 1:
                return None
            if c == "\\" and not raw and j + 1 < n:
                d = text[j + 1]
                if d == "\n":
                    j += 2
                    continue
                buf.append(_PY_ESC.get(d, "\\" + d))
                pos.append(j)
                j += 2
                continue
            if fmt and c == "{":
                if text.startswith("{{", j):
                    buf.append("{")
                    pos.append(j)
                    j += 2
                    continue
                e = _balanced(text, j)
                code(text[j + 1:e - 1], j)
                j = e
                continue
            if fmt and text.startswith("}}", j):
                buf.append("}")
                pos.append(j)
                j += 2
                continue
            buf.append(c)
            pos.append(j)
            j += 1
        return None
    if lang == "php" and text.startswith("<<<", i):
        m = re.match(r"<<<[ \t]*(['\"]?)([A-Za-z_]\w*)\1[ \t]*\r?\n", text[i:])
        if not m:
            return None
        nowdoc, tag = m.group(1) == "'", m.group(2)
        start = i + m.end()
        end = re.compile(rf"^([ \t]*){tag}\b", re.M).search(text, start)
        if not end:
            return None
        body_end = end.start() - 1 if end.start() > start else start
        ind = len(end.group(1))
        j = start
        at_line_start = True
        while j < body_end:
            c = text[j]
            if at_line_start and ind:
                k = 0
                while k < ind and j < body_end and text[j] in " \t":
                    j += 1
                    k += 1
                at_line_start = False
                continue
            if not nowdoc and c == "\\" and j + 1 < body_end and text[j + 1] in "nt\\$\"":
                buf.append(_PY_ESC.get(text[j + 1], text[j + 1]))
                pos.append(j)
                j += 2
                continue
            if not nowdoc and c == "{" and text.startswith("{$", j):
                e = _balanced(text, j)
                code(text[j + 1:e - 1], j)
                j = e
                continue
            if not nowdoc and c == "$" and re.match(r"\$[A-Za-z_]", text[j:j + 2]):
                e = _php_var(text, j)
                code(text[j:e], j)
                j = e
                continue
            buf.append(c)
            pos.append(j)
            at_line_start = c == "\n"
            j += 1
        flush()
        return end.end(), pieces, "heredoc"
    if lang == "java" and text.startswith('"""', i):
        j = text.find("\n", i + 3)
        close = text.find('"""', i + 3)
        if j < 0 or close < 0:
            return None
        for k in range(j + 1, close):
            buf.append(text[k])
            pos.append(k)
        flush()
        return close + 3, pieces, "block"
    q = text[i]
    if q not in "'\"`" or (q == "`" and lang != "js") or (q == "'" and lang == "java"):
        return None
    interp = (lang == "php" and q == '"') or (lang == "js" and q == "`")
    j = i + 1
    while j < n:
        c = text[j]
        if c == q:
            flush()
            return j + 1, pieces, ""
        if c == "\n" and q != "`" and lang != "php":
            return None
        if c == "\\" and j + 1 < n:
            d = text[j + 1]
            if lang == "php" and q == "'" and d not in "'\\":
                buf.append("\\")
                pos.append(j)
                j += 1
                continue
            if d == "\n":
                j += 2
                continue
            buf.append(_PY_ESC.get(d, d))
            pos.append(j)
            j += 2
            continue
        if interp and lang == "js" and text.startswith("${", j):
            e = _balanced(text, j + 1)
            code(text[j + 2:e - 1], j)
            j = e
            continue
        if interp and lang == "php":
            if text.startswith("{$", j):
                e = _balanced(text, j)
                code(text[j + 1:e - 1], j)
                j = e
                continue
            if text.startswith("${", j):
                e = _balanced(text, j + 1)
                code("$" + text[j + 2:e - 1], j)
                j = e
                continue
            if c == "$" and re.match(r"\$[A-Za-z_]", text[j:j + 2]):
                e = _php_var(text, j)
                code(text[j:e], j)
                j = e
                continue
        buf.append(c)
        pos.append(j)
        j += 1
    return None


def _tokens(text: str, lang: str) -> list:
    """The file as ('str', start, end, pieces, prefix) and ('code', start, end) tokens; comments are dropped."""
    out, i, n, last = [], 0, len(text), 0
    if lang == "php":
        tag = text.find("<?php")
        if tag < 0 and "<?=" not in text:
            return []
        i = last = max(tag, 0)
    while i < n:
        c = text[i]
        if lang == "php" and text.startswith("?>", i):
            nxt = text.find("<?php", i)
            if i > last:
                out.append(("code", last, i))
            i = last = n if nxt < 0 else nxt + 5
            continue
        if text.startswith("//", i) and lang in ("php", "js", "java") or c == "#" and lang in ("php", "py"):
            if i > last:
                out.append(("code", last, i))
            e = text.find("\n", i)
            i = last = n if e < 0 else e
            continue
        if text.startswith("/*", i) and lang in ("php", "js", "java"):
            if i > last:
                out.append(("code", last, i))
            e = text.find("*/", i + 2)
            i = last = n if e < 0 else e + 2
            continue
        if lang == "js" and c == "/" and _regex_here(text, i):
            e = _regex_end(text, i)
            if e:
                i = e
                continue
        start = i
        if lang == "py":
            m = re.match(r"[rRbBuUfF]{1,2}(?=['\"])", text[i:])
            if m and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
                start = i
            elif c not in "'\"":
                i += 1
                continue
        elif not (c in "'\"`" or (lang == "php" and text.startswith("<<<", i))):
            i += 1
            continue
        got = _string(text, start, lang)
        if got is None:
            i += 1
            continue
        end, pieces, prefix = got
        if start > last:
            out.append(("code", last, start))
        out.append(("str", start, end, pieces, prefix))
        i = last = end
    if last < n:
        out.append(("code", last, n))
    return out


def _regex_here(text: str, i: int) -> bool:
    if text.startswith("//", i) or text.startswith("/*", i):
        return False
    k = i - 1
    while k >= 0 and text[k] in " \t":
        k -= 1
    if k < 0 or text[k] in "(,=:[!&|?{};\n+-*%<>~^":
        return True
    return bool(re.search(r"\b(?:return|typeof|case|in|of|delete|void|throw)$", text[max(0, k - 8):k + 1]))


def _regex_end(text: str, i: int) -> int | None:
    j, cls, n = i + 1, False, len(text)
    while j < n and text[j] != "\n":
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            cls = True
        elif c == "]":
            cls = False
        elif c == "/" and not cls:
            return j + 1
        j += 1
    return None


# ------------------------------------------------------------------------------------------------------------ chains

CONCAT = {"php": r"\.(?![=.\d])", "js": r"\+(?![=+])", "java": r"\+(?![=+])", "py": r"\+(?![=+])"}
STOP = set(";,)]}")


def _operand_end(text: str, i: int, lang: str, limit: int) -> int:
    """i at the start of a code operand inside a concatenation: the index where it ends (before the next concatenation
    operator at depth 0, or a closing bracket / separator)."""
    depth, j = 0, i
    op = re.compile(r"\s*" + CONCAT[lang])
    while j < limit:
        c = text[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return j
            depth -= 1
        elif depth == 0 and (c in ";," or c == "?" and lang != "php" or c == ":" and lang in ("js", "java")):
            return j
        elif depth == 0 and op.match(text, j) and not (lang == "php" and text.startswith("->", j - 1)):
            return j
        j += 1
    return j


def _chains(text: str, toks: list, lang: str) -> list:
    """Strings joined by the language's concatenation (and Python's adjacent literals), with code operands in between:
    [{'pieces': [...], 'start', 'end'}]."""
    strs = {t[1]: t for t in toks if t[0] == "str"}
    op = re.compile(r"(?:\s|\\\n)*" + CONCAT[lang] + r"\s*")
    ws = re.compile(r"\s*")
    out, used = [], set()
    n = len(text)
    for t in toks:
        if t[0] != "str" or t[1] in used or t[4] in ("b", "rb", "br"):
            continue
        pieces, start, end = list(t[3]), t[1], t[2]
        while True:
            w = ws.match(text, end).end()
            if lang == "py" and w in strs:
                nxt = strs[w]
                pieces += nxt[3]
                used.add(w)
                end = nxt[2]
                continue
            m = op.match(text, end)
            if not m or m.end() >= n:
                break
            p = m.end()
            if p in strs:
                nxt = strs[p]
                pieces += nxt[3]
                used.add(p)
                end = nxt[2]
                continue
            o_end = _operand_end(text, p, lang, n)
            operand = text[p:o_end].strip()
            if not operand or re.match(r"['\"`]", operand):
                break
            pieces.append(Piece("", [], operand, p, "concat"))
            end = p + len(text[p:o_end].rstrip())
        out.append({"pieces": pieces, "start": start, "end": end})
    return out


# ---------------------------------------------------------------------------------------------------------- context

def _before(text: str, start: int, size: int = 400) -> str:
    return text[max(0, start - size):start]


def _call_name(text: str, start: int) -> str:
    """The call a string is the first argument of: 'mysqli_query', '$wpdb->get_results', 'cursor.execute' ..."""
    b = _before(text, start)
    m = re.search(r"([\w$]+(?:\s*(?:->|::|\.|\?\.)\s*[\w$]+)*)\s*\(\s*(?:[\w$]+(?:\s*(?:->|\.)\s*\w+)*\s*,\s*)?\(?\s*$", b)
    if not m:
        return ""
    name = re.sub(r"\s+", "", m.group(1))
    # mysqli_query($link, "SELECT ...") / pg_query($conn, "..."): the connection comes first
    if m.group(0).rstrip().endswith(",") and not re.search(r"(?:mysqli_\w+|pg_\w+|sqlsrv_query|mysql_query)$", name):
        return ""
    return name


def _assigned(text: str, start: int) -> tuple[str, str]:
    b = _before(text, start, 160)
    m = re.search(r"([$\w]+(?:\s*(?:->|\.)\s*\w+)?(?:\[[^\]]*\])?)\s*(?::\s*\w+\s*)?(\.=|\+=|=)\s*\(?\s*$", b)
    if m and not re.search(r"[=!<>]=\s*\(?\s*$", b):
        return re.sub(r"\s+", "", m.group(1)), m.group(2)
    m = re.search(r"\b(?:String|var|let|const|final)\s+([\w$]+)\s*=\s*$", b)
    return (m.group(1), "=") if m else ("", "")


def _sent_later(text: str, after: int, var: str) -> bool:
    """Is the variable later handed to a raw-query call (mysqli_query($link, $q), $pdo->prepare($q), cur.execute(q, ...))?"""
    for m in re.finditer(rf"([\w$]+(?:\s*(?:->|::|\.)\s*[\w$]+)*)\s*\(\s*(?:[\w$>\-.]+\s*,\s*)?{re.escape(var)}\s*[,)]",
                         text[after:after + 4000]):
        name = re.sub(r"\s+", "", m.group(1))
        if RAW_CALL.search(name) and (STRONG_CALL.search(name) or "wpdb" in name):
            return True
    return False


def _driver(lang: str, call: str, text: str) -> str:
    """Which placeholder style the call's driver uses: '?', '%s', '$n', ':name'."""
    if lang == "php":
        if "wpdb" in call:
            return "%s"
        if call.startswith("pg_"):
            return "$n"
        return "?"
    if lang == "py":
        if re.search(r"\bimport\s+sqlite3|\bfrom\s+sqlite3\b", text):
            return "?"
        return "%s"
    if lang == "js":
        if re.search(r"require\(\s*['\"](?:pg|postgres)['\"]\)|from\s+['\"](?:pg|postgres)['\"]", text):
            return "$n"
        return "?"
    return "?"


# ------------------------------------------------------------------------------------------------------------- shape

def _mask_sql(sql: str) -> str:
    out, i, n = list(sql), 0, len(sql)
    while i < n:
        c = sql[i]
        if c in "'\"":
            j = i + 1
            while j < n and sql[j] != c:
                j += 2 if sql[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                out[k] = "x"
            i = j + 1
            continue
        if sql.startswith("--", i) or c == "#":
            j = sql.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def sql_shape(sql: str, strong: bool) -> bool:
    """Does this text have the shape of a SQL statement (not just a sentence that starts with 'Select')?"""
    s = _mask_sql(sql).strip()
    m = SHAPE.match(s)
    if not m:
        return False
    verb = re.match(r"\(*\s*(\w+)", s).group(1)
    if not strong and not verb.isupper():
        return False                                   # 'Delete from cart', 'select one from the list': labels and prose
    if m.group("sel") is not None:
        words = re.findall(r"(?<![\w.$:'])[A-Za-z]+(?![\w(.])", m.group("sel"))
        if any(w.lower() in PROSE_WORDS for w in words) or m.group("from").lower() in PROSE_WORDS:
            return False
    if re.search(r"[.!]\s*$", s) and not re.search(r"[=(),*]", s):
        return False
    return True


# ------------------------------------------------------------------------------------------------- pieces into SQL

def _clause_before(masked_prefix: str) -> str:
    low = masked_prefix.lower()
    best, at = "", -1
    for kw in ("select", "from", "join", "where", "having", "group by", "order by", "limit", "offset", "set", "values",
               "into", "update", "on", "table", "using", "returning", "delete"):
        for m in re.finditer(rf"\b{kw.replace(' ', r'\s+')}\b", low):
            if m.start() > at:
                best, at = kw, m.start()
    return best


def _role(sql_so_far: str, after: str) -> str:
    """Where a code piece stands in the SQL: 'string' (inside quotes), 'value', 'name' or 'fragment'."""
    masked = _mask_sql(sql_so_far)
    quotes = 0
    i = 0
    while i < len(sql_so_far):
        c = sql_so_far[i]
        if c == "\\" and quotes % 2:
            i += 2
            continue
        if c == "'":
            quotes += 1
        i += 1
    if quotes % 2:
        return "string"
    tail = masked.rstrip()
    glued_before = bool(sql_so_far) and (sql_so_far[-1].isalnum() or sql_so_far[-1] in "_.`\"")
    glued_after = bool(after) and (after[0].isalnum() or after[0] in "_`\"")
    if glued_before or glued_after:
        if re.search(r"(?:=|<|>|\(|,|\blike|\bin)\s*$", tail, re.I) and not glued_after:
            return "value"
        return "name"
    clause = _clause_before(masked)
    if re.search(r"(?:\bfrom|\bjoin|\binto|\bupdate|\btable|\bexists)\s*$", tail, re.I):
        return "name"
    if clause in ("order by", "group by") and re.search(r"(?:\bby|,)\s*$", tail, re.I):
        return "name"
    if clause == "select" and re.search(r"(?:\bselect|\bdistinct|,)\s*$", tail, re.I):
        return "name"
    if re.search(r"(?:[=<>+\-*/%]|\bin\s*\(|\(|,|\blike|\bbetween|\band|\bthen|\belse|\bwhen|\blimit|\boffset|\binterval|"
                 r"\bvalues\s*\(|\bis|\bnot)\s*$", tail, re.I):
        if re.search(r"\b(?:and|or|not)\s*$", tail, re.I) and not re.search(r"\bbetween\s+\S+\s+and\s*$", tail, re.I):
            return "fragment"
        if re.search(r"\bis\s*$", tail, re.I):
            return "fragment"
        return "value"
    return "fragment"


def assemble(pieces: list) -> dict:
    """The SQL text with each code piece replaced by a stand-in the checker can read, plus where each one stands."""
    sql, pos, slots = "", [], []
    for idx, p in enumerate(pieces):
        if not p.code:
            sql += p.text
            pos += p.pos
            continue
        after = next((q.text for q in pieces[idx + 1:] if not q.code), "")
        nxt = pieces[idx + 1] if idx + 1 < len(pieces) else None
        if nxt is not None and nxt.code:
            after = "x"
        role = _role(sql, after)
        n = len(slots) + 1
        if role == "string":
            token = f"dbabav{n}"
        elif role == "value":
            token = f":dbabav{n}"
        elif role == "name":
            token = _name_token(p.code, n, sql)
        else:
            token = f"dbabaf{n}"
        slots.append({"code": p.code, "role": role, "token": token, "at": p.at, "via": p.via, "sql_at": len(sql),
                      "label": getattr(p, "label", "")})
        sql += token
        pos += [p.at] * len(token)
    return {"sql": sql, "pos": pos, "slots": slots}


WP_TABLES = {"posts", "postmeta", "users", "usermeta", "options", "comments", "commentmeta", "terms", "termmeta",
             "term_taxonomy", "term_relationships", "links"}


def _name_token(code: str, n: int, before: str) -> str:
    m = re.fullmatch(r"\$?(?:this->)?wpdb\s*->\s*(\w+)", code.strip())
    if m and m.group(1) in WP_TABLES:
        return "wp_" + m.group(1)
    if m and m.group(1) in ("prefix", "base_prefix"):
        return "wp_"
    if before and (before[-1].isalnum() or before[-1] == "_"):
        return f"dbabax{n}"
    return f"dbabat{n}"


def _show(sql: str, slots: list) -> str:
    """The SQL with each stand-in shown as the code it came from ({$id}); a WordPress table keeps its usual name."""
    out = sql
    for s_ in sorted(slots, key=lambda s_: -len(s_["token"])):
        if not s_["token"].startswith("wp_"):
            out = out.replace(s_["token"], "{" + s_["code"] + "}")
    return out


# ---------------------------------------------------------------------------------------------- tonight's own checks

QUOTED_PH = re.compile(r"'(%?)(\?|:[A-Za-z_]\w*|\$\d+|%s|%d|%\([A-Za-z_]\w*\)s)(%?)'")


def _quoted_finding(sql: str, m, driver: str, engine: str | None, lang: str) -> dict:
    """One placeholder written inside quotes: what each engine does with it, and the placeholder without quotes."""
    ph, like = m.group(2), bool(m.group(1) or m.group(3))
    use = "?" if engine in ("mysql", "mariadb") and ph.startswith("$") else ph     # MySQL/MariaDB drivers have no $1
    pg_note_en = pg_note_hi = ""
    if like:
        lead = [f"'{m.group(1)}'"] if m.group(1) else []
        tail = [f"'{m.group(3)}'"] if m.group(3) else []
        mysql_fix = "CONCAT(" + ", ".join(lead + [use] + tail) + ")"
        pg_fix = " || ".join(lead + [use] + tail)
        fix = pg_fix if engine == "postgresql" or engine is None and use.startswith("$") else mysql_fix
        if engine is None and fix == mysql_fix:
            pg_note_en = (f" On PostgreSQL write {pg_fix} instead: CONCAT with a parameter of unknown type is refused there "
                          "(42P18 could not determine data type of parameter).")
            pg_note_hi = f" PostgreSQL pe {pg_fix} likho: wahan CONCAT untyped parameter ke saath mana karta hai (42P18)."
    else:
        fix = use
    if ph in ("%s", "%d") or ph.startswith("%("):
        why_en = (f"The driver (pymysql, psycopg) puts the value in with its own quotes, so '{ph}' becomes ''value'': a syntax "
                  "error, or text that never matches.")
        why_hi = f"Driver value ko apne quotes ke saath daalta hai, isliye '{ph}' ''value'' ban jaata hai: syntax error ya galat text."
    else:
        parts = []
        if engine in (None, "mysql", "mariadb"):
            parts.append("MariaDB/MySQL refuse it once a value is bound (1210 Incorrect arguments to EXECUTE; PDO: number of "
                         "bound variables does not match), and without values it searches for the character itself")
        if engine in (None, "postgresql"):
            parts.append(f"PostgreSQL runs it and compares with the text '{ph}', so it silently finds the wrong rows")
        why_en = f"Inside quotes, {ph} is just the characters {ph}, never the value: " + "; ".join(parts) + "."
        why_hi = f"Quotes ke andar {ph} sirf text hai, value nahi: query wahi akshar dhoondhti hai, value kabhi andar nahi jaati."
    return {"kind": "wrong", "topic": "quoted_placeholder", "evidence": m.group(0), "edit": (m.start(), m.end(), fix),
            "title_en": f"{m.group(0)} is text, not a placeholder: the value is never put in",
            "title_hi": f"{m.group(0)} quotes ke andar sirf text hai, placeholder nahi: value kabhi andar nahi jaati",
            "fix_en": why_en + f" Write the placeholder without quotes: {fix}" + (" (or put the % signs into the value you bind)."
                                                                                  if like else ".") + pg_note_en,
            "fix_hi": why_hi + f" Placeholder bina quotes ke likho: {fix}" + (" (ya % value mein hi jod do)." if like else ".") + pg_note_hi}


PLAIN_QUOTED = re.compile(r"'(%?)(\?|\$\d+)(%?)'")


def plain_param_slips(sql: str, engine: str | None) -> list[dict]:
    """For a query pasted on its own: the placeholder mistakes the text alone proves. '?' alone and '%?' are left out, since
    a query with its values hidden looks exactly like that."""
    out = []
    for m in PLAIN_QUOTED.finditer(sql):
        ph = m.group(2)
        if ph == "?" and not m.group(3):
            continue                                   # '?' / '%?': maybe a value hidden on purpose
        out.append(_quoted_finding(sql, m, ph if ph == "?" else "$n", engine, "sql"))
    out += [f for f in code_checks(sql, [], "sql", "", "?", engine, False) if f["topic"] == "placeholder_name"]
    return out


def code_checks(sql: str, slots: list, lang: str, call: str, driver: str, engine: str | None, params_given: bool) -> list:
    """Mistakes that only exist because the SQL lives in code: a placeholder inside quotes (never bound), a placeholder
    where a name goes, a value written into the SQL text instead of bound. Each is a dict like the checker's findings."""
    out = []
    masked_quotes = []
    for m in QUOTED_PH.finditer(sql):
        ph = m.group(2)
        if ph in ("%s", "%d") and not (lang == "py" and params_given or "prepare" in call and "wpdb" not in call):
            continue                                   # sprintf('%s'), $wpdb->prepare('%s') (strips the quotes): fine
        if ph.startswith(":") and ph.startswith(":dbaba"):
            continue
        if ph == "?" and not params_given and not m.group(1) and not m.group(3):
            continue                                   # '?' alone with nothing bound may be a real question mark
        masked_quotes.append(m)
    for m in masked_quotes:
        out.append(_quoted_finding(sql, m, driver, engine, lang))
    s = _mask_sql(sql)
    for m in re.finditer(r"\b(from|join|into|update|table)\s+(\?|\$\d+|:[A-Za-z_]\w*|%s)(?=[\s;),]|$)", s, re.I):
        if m.group(2).startswith(":dbaba"):
            continue
        out.append({
            "kind": "error", "topic": "placeholder_name", "evidence": sql[m.start():m.end()], "edit": None,
            "title_en": f"A placeholder can't be a table name ({m.group(2)} after {m.group(1).upper()})",
            "title_hi": f"Placeholder table ka naam nahi ho sakta ({m.group(1).upper()} ke baad {m.group(2)})",
            "fix_en": ("Placeholders carry values only; both MariaDB (1064) and PostgreSQL (42601) refuse a placeholder where a "
                       "name goes. Choose the table in code from a fixed list of allowed names and write it into the SQL."),
            "fix_hi": ("Placeholder sirf value ke liye hai; MariaDB (1064) aur PostgreSQL (42601) dono mana karte hain. Table ka "
                       "naam code mein allowed list se chuno aur SQL mein likho.")})
    for m in re.finditer(r"\b(order|group)\s+by\s+((?:\?|\$\d+|:[A-Za-z_]\w*|%s)(?:\s*,\s*(?:\?|\$\d+|:[A-Za-z_]\w*|%s))*)",
                         s, re.I):
        if ":dbaba" in m.group(2):
            continue
        what = m.group(1).upper()
        out.append({
            "kind": "wrong", "topic": "placeholder_name", "evidence": sql[m.start():m.end()], "edit": None,
            "title_en": f"{what} BY {m.group(2)} {'sorts' if what == 'ORDER' else 'groups'} by a constant, not by the column you bind",
            "title_hi": f"{what} BY {m.group(2)} column se nahi, ek constant value se {'sort' if what == 'ORDER' else 'group'} karta hai",
            "fix_en": (f"A bound value is a value: binding 'name' makes it {what} BY 'name', the same text on every row, so "
                       f"{'nothing is sorted (lab: rows came back in storage order on MariaDB and PostgreSQL)' if what == 'ORDER' else 'all rows fall into one group'}. "
                       "Pick the column in code from a fixed list of allowed names (and ASC/DESC the same way) and write it "
                       "into the SQL."),
            "fix_hi": (f"Bind ki hui value sirf value hai: 'name' bind karo to {what} BY 'name' banta hai, har row pe same text. "
                       "Column ka naam code mein allowed list se chuno aur SQL mein likho.")})
    for s_ in slots:
        if s_["role"] not in ("value", "string"):
            continue
        if re.fullmatch(r"\(?\s*(?:int|integer|float|bool)\s*\)?\s*\$\w+|intval\(.*\)|int\(.*\)|parseInt\(.*\)|Number\(.*\)|\d+",
                        s_["code"].strip()):
            continue                                   # forced to a number in code: no text can get into the SQL
        how = "by string formatting " if s_["via"] == "format" else ""
        if s_["via"] == "format" and s_.get("label"):
            s_ = {**s_, "code": s_["label"]}
        out.append({
            "kind": "check", "topic": "injection", "evidence": "{" + s_["code"] + "}", "edit": None,
            "title_en": f"A value is written into the SQL text {how}({s_['code']}) instead of being bound",
            "title_hi": f"Value ({s_['code']}) SQL text mein seedha likhi gayi hai, bind nahi ki gayi",
            "fix_en": (f"If {s_['code']} can ever hold text a user typed, a quote in it changes the query (SQL injection). "
                       f"Send it separately with a placeholder ({'$1' if driver == '$n' else driver}) and pass the value to the call; the database then "
                       "treats it only as a value."),
            "fix_hi": (f"Agar {s_['code']} mein kabhi user ka likha text aa sakta hai, to usme ek quote query badal deta hai "
                       f"(SQL injection). Placeholder ({'$1' if driver == '$n' else driver}) lagao aur value call ko alag se do.")})
    return out


def comment_checks(sql: str, pos: list, text: str) -> list:
    """A -- comment in a query glued together from several code lines with no newline between them runs to the end of
    the joined text: everything the later lines add is part of the comment, so the database never sees it."""
    out = []
    if not pos or len(pos) != len(sql):
        return out
    m = _mask_sql(sql)
    i = 0
    while True:
        i = sql.find("--", i)
        if i < 0:
            break
        if m[i:i + 2] != "  ":                         # inside quotes: not a comment
            i += 2
            continue
        end = sql.find("\n", i)
        end = len(sql) if end < 0 else end
        line = _line_of(text, pos[i])
        later = next((k for k in range(i, end) if _line_of(text, pos[k]) != line and sql[k].strip()), None)
        if later is not None:
            hidden = sql[later:end].strip()
            cut = later
            while cut > i and sql[cut - 1] in " \t":
                cut -= 1
            out.append({
                "kind": "wrong", "topic": "comment_swallows", "evidence": sql[i:end].strip()[:120], "edit": (cut, later, "\n"),
                "title_en": f"The -- comment swallows the rest of the query: {_clip(hidden)} is never run",
                "title_hi": f"-- comment baaki query nigal leta hai: {_clip(hidden)} kabhi chalta hi nahi",
                "fix_en": ("The code lines are joined without a newline, so the -- comment on one line runs to the end of the "
                           "joined text and everything the next lines add becomes part of the comment (on MariaDB and "
                           "PostgreSQL alike). End that piece with a newline (\\n), or use /* ... */ for the comment."),
                "fix_hi": ("Code ki lines bina newline ke judti hain, isliye -- comment joined text ke ant tak chalta hai aur aage "
                           "ki lines comment ban jaati hain. Us piece ke ant mein newline (\\n) do, ya /* ... */ comment likho.")})
            break
        i = end
    return out


def _clip(t: str, n: int = 48) -> str:
    t = re.sub(r"\s+", " ", t)
    return t if len(t) <= n else t[:n - 3] + "..."


# ------------------------------------------------------------------------------------------------------ the scanner

def detect_engine(root: str) -> tuple[str | None, str]:
    """The project's database from its own files (rule A, shown): composer / package / requirements / .env / wp-config /
    Laravel config / Java build files. Only the DB_CONNECTION line of .env is read."""
    seen = {}

    def add(engine, why):
        seen.setdefault(engine, why)

    def read(p, size=200_000):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read(size)
        except OSError:
            return ""

    for name in ("composer.json", "package.json", "requirements.txt", "pyproject.toml", "Pipfile", "pom.xml", "build.gradle",
                 ".env", ".env.example", "wp-config.php", "config/database.php", "settings.py", "docker-compose.yml"):
        p = os.path.join(root, name)
        if not os.path.isfile(p):
            continue
        t = read(p)
        if name.startswith(".env"):
            m = re.search(r"^\s*DB_CONNECTION\s*=\s*['\"]?(\w+)", t, re.M)
            t = m.group(0) if m else ""
        low = t.lower()
        if name == "wp-config.php" and "db_name" in low:
            add("mysql", "wp-config.php (WordPress runs on MySQL/MariaDB)")
        if re.search(r"\bmariadb\b", low):
            add("mariadb", f"{name} names MariaDB")
        if re.search(r"db_connection\s*=\s*['\"]?mysql|\"mysql2?\"|\bpymysql\b|\bmysqlclient\b|mysql-connector|ext-mysqli|"
                     r"ext-pdo_mysql|mysql:\d|image:\s*mysql|com\.mysql|mysql-connector-j", low):
            add("mysql", f"{name} uses MySQL")
        if re.search(r"db_connection\s*=\s*['\"]?pgsql|\"pg\"\s*:|\"postgres\"\s*:|\bpsycopg|\basyncpg\b|ext-pdo_pgsql|"
                     r"image:\s*postgres|org\.postgresql|postgresql:", low):
            add("postgresql", f"{name} uses PostgreSQL")
    if len(seen) == 1:
        e, why = next(iter(seen.items()))
        return e, why
    if set(seen) == {"mysql", "mariadb"}:
        return "mariadb", seen["mariadb"]
    if len(seen) > 1:
        return None, "the project names more than one database (" + ", ".join(sorted(seen)) + ")"
    return None, "no database named in the project's files"


def _file_engine(text: str, lang: str) -> str | None:
    if re.search(r"\bpg_(?:query|connect|prepare)\b|\bpsycopg|require\(\s*['\"]pg['\"]\)|from\s+['\"]pg['\"]|"
                 r"org\.postgresql|pgsql:", text):
        return "postgresql"
    if re.search(r"\bmysqli_|\$wpdb\b|\bmysql_query\b|\bpymysql\b|mysql\.connector|require\(\s*['\"]mysql2?\b|"
                 r"from\s+['\"]mysql2?\b|com\.mysql|mysql:host", text):
        return "mysql"
    return None


def _lead(sql_text: str) -> int:
    m = re.match(r"(?:\s|--[^\n]*(?:\n|$)|#[^\n]*(?:\n|$)|/\*.*?\*/)*", sql_text, re.S)
    return m.end() if m else 0


def _lines(sql_text: str) -> int:
    """How far the statement's first real character is from where the text starts (blank lines and comments skipped)."""
    m = re.match(r"(?:\s|--[^\n]*\n|#[^\n]*\n|/\*.*?\*/)*", sql_text, re.S)
    return sql_text.count("\n", 0, m.end() if m else 0)


# the schema the project's own files create (night 10B, app/agents/sqlproject.py), while a project is scanned
PROJECT: contextvars.ContextVar = contextvars.ContextVar("dbaba_project_schema", default=None)


def _run_check(sql: str, engine: str | None) -> dict:
    from app.agents import sqlcorrect, sqlverify

    try:
        sch = PROJECT.get()
        if sch and sch.get("tables"):
            try:
                return sqlverify.check(sql, sch, engine=engine)
            except Exception:  # noqa: BLE001 - the schema is an extra; the plain check still answers
                pass
        return sqlcorrect.check(sql, engine=engine)
    except Exception as exc:  # noqa: BLE001 - one query the checker can't read never stops the scan
        return {"findings": [], "corrected": None, "unreadable": str(exc)}


def _apply_edits(sql: str, edits: list) -> str:
    for a, b, new in sorted(edits, key=lambda e: -e[0]):
        sql = sql[:a] + new + sql[b:]
    return sql


def scan_text(path: str, text: str, engine: str | None = None, named: bool = False) -> list[Found]:
    """Every SQL statement in one file's text, checked. The database: the one the user named; else the driver this file
    imports (a pg pool is PostgreSQL even in a PHP + MySQL project); else the project's."""
    lang = LANGS.get(os.path.splitext(path)[1].lower())
    if lang is None:
        return []
    if not named:
        engine = _file_engine(text, lang) or engine
    if lang == "sql":
        return _scan_sql_file(path, text, engine)
    toks = _tokens(text, lang)
    out = []
    appended = set()
    chains = _chains(text, toks, lang)
    for ch in chains:
        var, op = _assigned(text, ch["start"])
        if var and op in (".=", "+="):
            appended.add(var)
    for m in ORM.finditer(text):
        if re.search(r"nativeQuery\s*=\s*true", text[m.start():m.start() + 400]):
            continue
        b = text.rfind("\n", 0, m.start()) + 1
        out.append(Found(path, _line_of(text, m.start()), text[b:text.find("\n", m.start()) if "\n" in text[m.start():]
                                                               else len(text)].strip()[:160],
                         kind="unread", why="ORM / query-builder code: the SQL is built by the library at run time, so "
                                            "DBaba doesn't guess it (paste the SQL it logs to have it checked)",
                         engine=engine))
    orm_lines = {f.line for f in out}
    for ch in chains:
        pieces = ch["pieces"]
        lits = "".join(p.text for p in pieces if not p.code)
        if not re.search(r"\b(?:select|insert|update|delete|with|create|alter|drop|truncate|replace|show|call|explain)\b", lits, re.I):
            continue
        call = _call_name(text, ch["start"])
        var, op = _assigned(text, ch["start"])
        strong = bool(call and RAW_CALL.search(call) and (STRONG_CALL.search(call) or lang == "py" or "wpdb" in call)) or \
            bool(var and SQL_VAR.search(var.split("->")[-1].split(".")[-1])) or \
            bool(var and _sent_later(text, ch["end"], var))
        fmt = _formatted(text, ch, lang, call)
        if fmt:
            pieces = _format_pieces(pieces, fmt, _format_args(text, ch, lang))
        asm = assemble(pieces)
        sql = asm["sql"]
        if not sql_shape(sql, strong):
            continue
        if op in (".=", "+="):
            continue                                   # an appended piece of a query built elsewhere in the file
        line = _line_of(text, asm["pos"][0] if asm["pos"] else ch["start"])
        lead = _lines(sql)
        if asm["pos"]:
            k = _lead(sql)                             # the first real SQL character (blank lines and comments skipped)
            line = _line_of(text, asm["pos"][min(k, len(asm["pos"]) - 1)])
        elif lead:
            line += lead
        shown = _show(sql, asm["slots"]).strip()
        if line in orm_lines and re.search(r"@Query|createQuery", text[max(0, ch["start"] - 200):ch["start"]]):
            continue                                   # JPQL / HQL: reported once, as ORM
        driver = _driver(lang, call, text)
        why = ""
        if var and var in appended:
            why = f"the query in {var} is built in pieces (later parts are added with .= / +=)"
        elif any(s["role"] == "fragment" for s in asm["slots"]):
            frag = next(s for s in asm["slots"] if s["role"] == "fragment")
            why = f"part of the SQL comes from code ({frag['code']}) that isn't a single value or name"
        elif DANGLING.search(_mask_sql(sql)):
            why = "the SQL text ends where more SQL must follow (it is completed somewhere else)"
        if why:
            out.append(Found(path, line, shown, kind="unread", why=why, engine=engine))
            continue
        params_given = bool(re.match(r"\s*\)?\s*,", text[ch["end"]:ch["end"] + 8])) or "prepare" in call.lower()
        if var and not params_given:                   # $sql = "..."; later $pdo->prepare($sql) / cur.execute(sql, (x,))
            later = text[ch["end"]:ch["end"] + 3000]
            v = re.escape(var)
            params_given = bool(re.search(rf"\(\s*{v}\s*,|prepare\w*\s*\(\s*{v}\s*\)", later))
        mine = code_checks(sql, asm["slots"], lang, call, driver, engine, params_given)
        mine += comment_checks(sql, asm["pos"], text)
        swallowed = [f["edit"] for f in mine if f["topic"] == "comment_swallows"]
        res = _run_check(_apply_edits(sql, swallowed) if swallowed else sql, engine)   # the query as it was meant to read
        out.append(_finish(path, line, sql, asm, res, mine, engine, driver, "wpdb" in call or "wpdb" in text))
    return sorted(out, key=lambda f: f.line)


def _finish(path, line, sql, asm, res, mine, engine, driver: str = "?", wp: bool = False) -> Found:
    slots = asm["slots"]
    fs = []
    have = set()
    for f in mine:
        fs.append({k: v for k, v in f.items() if k != "edit"})
        have.add(f["topic"])
    mine_ev = {(f["topic"], f["evidence"]) for f in mine}
    for f in res.get("findings") or []:
        if (f["topic"], f.get("evidence")) in mine_ev or f["topic"] in have and f["topic"] in CODE_TOPICS:
            continue                                   # the same placeholder mistake, already said in code terms
        g = dict(f)
        for k in ("title_en", "title_hi", "evidence", "fix_en", "fix_hi"):
            if isinstance(g.get(k), str):
                g[k] = _show(g[k], slots)
        fs.append(g)
    order = {"error": 0, "wrong": 1, "check": 2}
    fs.sort(key=lambda f: order.get(f["kind"], 3))
    try:                                               # every finding names the fundamental it breaks
        from app.agents import sqlread

        funds = sqlread.fundamentals()["fundamentals"]
        for f in fs:
            fid = sqlread.fundamental_of(f)
            if fid and "basis" not in f:
                f["basis"] = {"id": fid, "en": funds[fid]["title_en"], "hi": funds[fid]["title_hi"]}
    except Exception:  # noqa: BLE001 - the basis is an extra
        pass
    edits = [f["edit"] for f in mine if f.get("edit")]
    corrected = None
    base = res.get("corrected")
    if edits:
        corrected = _apply_edits(sql, edits)
        again = _run_check(corrected, engine).get("corrected")
        corrected = again or corrected
    elif base:
        corrected = base
    params, values = None, []
    if any(f["topic"] == "injection" for f in mine):
        params, values = _parameterized(corrected or sql, slots, driver, engine, wp)
        params = _show(params, slots).strip() if params else None
    if corrected:
        corrected = _show(corrected, slots).strip()
    return Found(path, line, _dedent(_show(sql, slots).strip()), findings=fs, corrected=_dedent(corrected), engine=engine,
                 parameterized=_dedent(params), values=values, dropped=res.get("dropped") or [])


def _dedent(t: str | None) -> str | None:
    """Code indentation inside a multi-line string (lines 2+ indented to match the code) taken off for reading."""
    if not t or "\n" not in t:
        return t
    first, *rest = t.split("\n")
    ind = min((len(x) - len(x.lstrip(" \t")) for x in rest if x.strip()), default=0)
    return "\n".join([first] + [x[ind:] if x.strip() else "" for x in rest])


def _parameterized(sql: str, slots: list, driver: str, engine: str | None, wp: bool = False) -> tuple[str | None, list]:
    """Each value written into the SQL text becomes a placeholder of the call's driver; a value inside a longer text (a LIKE
    pattern) becomes that text built around the placeholder. Numbers forced in code stay as they are."""
    order = sorted((s_ for s_ in slots if s_["role"] in ("value", "string")), key=lambda s_: sql.find(s_["token"]))
    out, values, n = sql, [], 0
    for s_ in order:
        tok = s_["token"]
        if tok not in out:
            continue
        n += 1
        ph = f"${n}" if driver == "$n" else driver
        code = s_.get("label") or s_["code"]
        if s_["role"] == "value":
            out = out.replace(tok, ph, 1)
            values.append(code)
            continue
        m = re.search(r"'([^']*)" + re.escape(tok) + r"([^']*)'", out)
        if not m:
            return None, []
        a, b = m.group(1), m.group(2)
        if not a and not b:
            new = ph
        elif driver == "%s" and wp:                   # $wpdb->prepare: the % signs go into the value, escaped for LIKE
            new = ph
            out = out[:m.start()] + new + out[m.end():]
            values.append((f"'{a}' . " if a else "") + f"$wpdb->esc_like({code})" + (f" . '{b}'" if b else ""))
            continue
        elif engine == "postgresql" or driver == "$n":
            new = " || ".join(([f"'{a}'"] if a else []) + [ph] + ([f"'{b}'"] if b else []))
        else:
            new = "CONCAT(" + ", ".join(([f"'{a}'"] if a else []) + [ph] + ([f"'{b}'"] if b else [])) + ")"
        out = out[:m.start()] + new + out[m.end():]
        values.append(code)
    return out, values


FMT_PH = {"percent": re.compile(r"%(?:\([A-Za-z_]\w*\))?[sdf]|%\d+\$[sd]"), "brace": re.compile(r"\{[\w.]*\}")}


def _formatted(text: str, ch: dict, lang: str, call: str) -> str:
    """Is this string a format template filled in by code ('...' % x, '...'.format(x), sprintf / String.format)? Then its
    %s / {} are values written into the text, not placeholders the driver binds."""
    after = text[ch["end"]:ch["end"] + 12]
    if lang == "py" and re.match(r"\s*%(?!=)", after):
        return "percent"
    if lang == "py" and re.match(r"\s*\.\s*format\s*\(", after):
        return "brace"
    if re.search(r"(?:^|[^>\w])(?:sprintf|String\.format|vsprintf)$", call):
        return "percent"
    return ""


def _format_args(text: str, ch: dict, lang: str) -> str:
    """The code that fills a format template: '% (a, b)' -> '(a, b)', '.format(x)' -> 'x', sprintf("...", $x) -> '$x'."""
    after = text[ch["end"]:ch["end"] + 300]
    m = re.match(r"\s*%\s*", after) or re.match(r"\s*\.\s*format\s*\(", after) or re.match(r"\s*,\s*", after)
    if not m:
        return ""
    k = ch["end"] + m.end()
    end = _operand_end(text, k, lang if lang in CONCAT else "py", len(text))
    if after.lstrip().startswith(","):
        end = _balanced(text, text.rfind("(", 0, ch["start"]), "(", ")") - 1
    return re.sub(r"\s+", " ", text[k:end]).strip()[:60]


def _format_pieces(pieces: list, kind: str, label: str = "") -> list:
    out = []
    for p in pieces:
        if p.code:
            out.append(p)
            continue
        last = 0
        for m in FMT_PH[kind].finditer(p.text):
            if m.start() > last:
                out.append(Piece(p.text[last:m.start()], p.pos[last:m.start()]))
            out.append(Piece("", [], m.group(0), p.pos[m.start()] if p.pos else 0, "format"))
            out[-1].label = label
            last = m.end()
        if last < len(p.text):
            out.append(Piece(p.text[last:], p.pos[last:]))
    return out


def _scan_sql_file(path: str, text: str, engine: str | None) -> list[Found]:
    from app.agents.sqlroutine import split_script

    out, cur = [], 0
    for stmt in split_script(text):
        at = text.find(stmt, cur)
        if at < 0:
            at = cur
        cur = at + len(stmt)
        lead = re.match(r"(?:\s|--[^\n]*(?:\n|$)|#[^\n]*(?:\n|$)|/\*.*?\*/)*", stmt, re.S).end()
        line = _line_of(text, at + lead)
        stmt = stmt[lead:]
        if not stmt or not sql_shape(stmt, True):
            continue
        res = _run_check(stmt, engine)
        mine = code_checks(stmt, [], "sql", "", "?", engine, False)
        out.append(_finish(path, line, stmt, {"slots": []}, res, [m for m in mine if m["topic"] == "placeholder_name"], engine))
    return out


def walk(root: str):
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith(".") or x in (".",))
        for f in sorted(files):
            if f.endswith((".min.js", ".bundle.js", ".map")):
                continue
            if os.path.splitext(f)[1].lower() in LANGS:
                yield os.path.join(d, f)


def scan(root: str, engine: str | None = None) -> dict:
    """Read every file of a project (read only) and check the SQL in it. engine: the user's database (rule A); when not
    given, the project's own files decide it, and when they don't, the report says so and answers in general SQL."""
    named = engine
    how = "named" if engine else ""
    if not engine:
        engine, how = detect_engine(root) if os.path.isdir(root) else (None, "")
    from app.agents import sqlproject

    sch = sqlproject.read_project(root) if os.path.isdir(root) else sqlproject.empty()
    token = PROJECT.set(sch)
    try:
        found, files = _scan_files(root, engine, named)
    finally:
        PROJECT.reset(token)
    read = [f for f in found if f.kind == "read"]
    return {"engine": engine, "engine_how": how if named else how, "files": files, "queries": len(read),
            "unread": [f.as_dict() for f in found if f.kind == "unread"],
            "with_findings": [f.as_dict() for f in read if f.findings], "clean": sum(1 for f in read if not f.findings),
            "ruled_out": [{"file": f.file, "line": f.line, "dropped": f.dropped} for f in read if f.dropped],
            "schema": {"summary": sqlproject.summary(sch), "tables": len(sch["tables"]), "files": sch["files"], "unread": sch["unread"]}}


def _scan_files(root: str, engine, named):
    found = []
    files = 0
    for p in walk(root) if os.path.isdir(root) else [root]:
        try:
            if os.path.getsize(p) > MAX_BYTES:
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        files += 1
        rel = os.path.relpath(p, root) if os.path.isdir(root) else os.path.basename(p)
        found += scan_text(rel, text, engine, named=bool(named))
    return found, files


ENGINE_NAMES = {"mysql": "MySQL", "mariadb": "MariaDB", "postgresql": "PostgreSQL"}


def report_text(rep: dict, lang: str = "en") -> str:
    lines = []
    eng = rep["engine"]
    if eng:
        lines.append(f"Database: {ENGINE_NAMES.get(eng, eng)} ({rep['engine_how'] or 'named'}). Say --engine to change it.")
    else:
        lines.append(f"Database: not known ({rep['engine_how']}); findings are general SQL. Say --engine mysql | mariadb | "
                     "postgresql to get answers in your database's words.")
    sch = rep.get("schema") or {}
    if sch.get("tables"):
        lines.append(f"Schema: {sch['summary']}; findings that depend on keys, types, NULLs or indexes are checked against it"
                     + (f" ({len(sch['unread'])} schema statement(s) couldn't be read completely: dbaba schema <folder> lists them)."
                        if sch.get("unread") else "."))
    else:
        lines.append("Schema: no CREATE TABLE scripts, schema dumps or migrations found, so what depends on keys, types, NULLs or "
                     "indexes stays a 'check'.")
    lines.append(f"Read {rep['files']} files: {rep['queries']} queries read, {len(rep['with_findings'])} with something to "
                 f"fix or check, {len(rep['unread'])} found but not readable completely. Nothing was changed.")
    for f in rep["with_findings"]:
        lines.append("")
        lines.append(f"{f['file']}:{f['line']}")
        for g in f["findings"]:
            lines.append(f"  [{g['kind']}{', verified with the schema' if g.get('schema') == 'verified' else ''}] {g['title_en']}")
            if g.get("fix_en"):
                lines.append(f"      {g['fix_en']}")
        if f.get("corrected"):
            lines.append("  Corrected query:")
            lines += ["    " + x for x in f["corrected"].splitlines()]
        if f.get("parameterized"):
            lines.append(f"  With placeholders (values: {', '.join(f['values'])}):")
            lines += ["    " + x for x in f["parameterized"].splitlines()]
    if rep.get("ruled_out"):
        lines.append("")
        lines.append("Ruled out by the project's schema (not a problem here):")
        for f in rep["ruled_out"]:
            for d in f["dropped"]:
                lines.append(f"  {f['file']}:{f['line']}  {d['title_en']}: {d['why_en']}")
    if rep["unread"]:
        lines.append("")
        lines.append("Found, couldn't read completely (not guessed):")
        for f in rep["unread"]:
            lines.append(f"  {f['file']}:{f['line']}  {f['why']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "schema":                  # the schema the project's files create, as DBaba read it
        from app.agents import sqlproject, sqlverify

        if len(argv) < 2:
            print("usage: dbaba schema <folder> [--json]")
            return 2
        sch = sqlproject.read_project(argv[1])
        print(json.dumps(sch, indent=1, ensure_ascii=False) if "--json" in argv else sqlverify.schema_text(sch))
        return 0
    if argv and argv[0] == "check":
        argv.pop(0)
    engine, as_json = None, False
    if "--engine" in argv:
        k = argv.index("--engine")
        engine = {"postgres": "postgresql", "pg": "postgresql", "maria": "mariadb"}.get(argv[k + 1].lower(), argv[k + 1].lower())
        del argv[k:k + 2]
    if "--json" in argv:
        as_json = True
        argv.remove("--json")
    if not argv:
        print("usage: dbaba check <folder or file> [--engine mysql|mariadb|postgresql] [--json]\n       dbaba schema <folder> [--json]")
        return 2
    rep = scan(argv[0], engine)
    print(json.dumps(rep, indent=1, ensure_ascii=False) if as_json else report_text(rep))
    return 1 if any(g["kind"] in ("error", "wrong") for f in rep["with_findings"] for g in f["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())


# ------------------------------------------------------------------------------------------ code pasted in the chat

CODE_MARKS = {
    "php": re.compile(r"<\?php|\$\w+\s*(?:->|=)|\bmysqli_\w+\s*\(|\$wpdb\b|\bDB::\w+\s*\(|\bpg_query", re.I),
    "py": re.compile(r"\b(?:cursor|cur|conn|connection|session)\.execute(?:many)?\s*\(|^\s*(?:import|from)\s+\w+|^\s*def\s+\w+\s*\(", re.M),
    "js": re.compile(r"\b(?:await|const|let|require\s*\(|=>)|\.(?:query|execute)\s*\(\s*`"),
    "java": re.compile(r"\b(?:PreparedStatement|prepareStatement|executeQuery|executeUpdate|String\s+\w+\s*=)\b")}
CODE_TOPICS = {"quoted_placeholder", "placeholder_name", "injection"}


def code_lang(text: str) -> str | None:
    """The language of code pasted in the chat, from its own marks (None: not code)."""
    t = text or ""
    for lang in ("php", "java", "py", "js"):
        if CODE_MARKS[lang].search(t):
            if lang == "js" and re.search(r"\$\w+\s*=", t):
                continue
            return lang
    return None


def code_paste(text: str, engine: str | None = None) -> dict | None:
    """Code with SQL in it pasted in the chat: every query found in it, checked, with its line. Only when the paste holds
    more than one query, a query that can't be read completely, or a mistake that exists because the SQL is in code
    (a quoted placeholder, a placeholder as a name, a value written into the SQL text); one plain query keeps going to
    the query checker as before."""
    lang = code_lang(text)
    if lang is None:
        return None
    body, shift = text, 0
    if lang == "php" and "<?" not in text:
        body, shift = "<?php\n" + text, 1
    ext = {"php": ".php", "py": ".py", "js": ".js", "java": ".java"}[lang]
    found = scan_text("pasted" + ext, body, engine, named=bool(engine))
    if not found:
        return None
    for f in found:
        f.line -= shift
    code_level = any(g["topic"] in CODE_TOPICS for f in found for g in f.findings)
    if len(found) < 2 and not code_level and not any(f.kind == "unread" for f in found):
        return None
    return {"lang": lang, "found": found, "engine": engine or _file_engine(body, lang)}


def paste_text(rep: dict, lang: str = "en") -> str:
    """The chat reply for pasted code: each query by its line, what's wrong, the corrected query; what couldn't be read."""
    hi = lang != "en"
    found = rep["found"]
    read = [f for f in found if f.kind == "read"]
    bad = [f for f in read if f.findings]
    out = [(f"Code mein {len(read)} SQL query mili" + (f", {len(bad)} mein kuch theek karna ya check karna hai" if bad else ", sab theek") + ":")
           if hi else (f"Found {len(read)} SQL quer{'y' if len(read) == 1 else 'ies'} in the code" +
                       (f", {len(bad)} with something to fix or check" if bad else ", all fine") + ":")]
    for f in read:
        out.append("")
        if not f.findings:
            out.append((f"Line {f.line}: theek hai." if hi else f"Line {f.line}: no problems found."))
            continue
        out.append(f"Line {f.line}:")
        for g in f.findings:
            title = g.get("title_hi") if hi else g.get("title_en")
            fix = g.get("fix_hi") if hi else g.get("fix_en")
            out.append(f"- [{g['kind']}] {title}")
            if fix:
                out.append(f"  {fix}")
        if f.corrected:
            out.append("Sahi query:" if hi else "Corrected query:")
            out.append(f.corrected)
        if f.parameterized:
            out.append((f"Placeholder ke saath (values: {', '.join(f.values)}):" if hi else
                        f"With placeholders (values: {', '.join(f.values)}):"))
            out.append(f.parameterized)
    un = [f for f in found if f.kind == "unread"]
    if un:
        out.append("")
        out.append("Ye mili, par poori padh nahi saka (andaza nahi lagaya):" if hi else
                   "Found, but couldn't read completely (not guessed):")
        for f in un:
            out.append(f"- Line {f.line}: {f.why}")
    return "\n".join(out)
