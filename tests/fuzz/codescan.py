"""Generator for night 10A: realistic project folders (plain PHP with PDO or mysqli, WordPress, Laravel, Node with mysql2 or pg,
Python with pymysql or psycopg, Java JDBC, .sql migrations) with wrong queries planted inside code strings, written the way
each language writes them (double quotes, heredoc / nowdoc, template strings, triple quotes, adjacent literals, + / .
concatenation, text blocks). Around them: ordinary strings that look a little like SQL ('Select a file from the list', 'Delete
from cart', log lines), SQL in comments, valid queries with placeholders, ORM / query-builder calls and queries built in pieces.

Each planted query must be found at its own file and line, under its own topic, with the same findings as when the SQL is
pasted alone; ordinary strings must give nothing; valid queries no 'error'/'wrong'; ORM code and queries built in pieces must
be reported as "found, couldn't read completely" at their line. The corrected query must do on MariaDB and on PostgreSQL
exactly what the intended one does (bound values put back in).

Planted mistakes: every family of tests/fuzz/stmtbugs (on that project's database) plus tonight's own: a placeholder inside
quotes ('%?%', '?', '%s', '$1', ':name'), a placeholder where a name goes (FROM ?, ORDER BY ?), and a value written into
the SQL text (interpolated, concatenated, f-string, % formatting).

Run: python -m tests.fuzz.codescan [projects] [seed]
"""

import os
import random
import re
import sys
import tempfile
from collections import Counter

from app.agents import sqlscan
from tests.fuzz import stmtbugs
from tests.fuzz.db import engines_up, make_query, make_schema

STACKS = {   # stack: (language, database, extension)
    "php_pdo": ("php", "mysql", ".php"), "php_mysqli": ("php", "mysql", ".php"), "wordpress": ("php", "mysql", ".php"),
    "laravel": ("php", "mysql", ".php"), "php_pg": ("php", "postgresql", ".php"), "node_mysql": ("js", "mysql", ".js"),
    "node_pg": ("js", "postgresql", ".ts"), "py_pymysql": ("py", "mysql", ".py"), "py_psycopg": ("py", "postgresql", ".py"),
    "java_mysql": ("java", "mysql", ".java"), "java_pg": ("java", "postgresql", ".java"), "sql_my": ("sql", "mysql", ".sql"),
    "sql_pg": ("sql", "postgresql", ".sql")}
PH = {"php_pdo": "?", "php_mysqli": "?", "wordpress": "%s", "laravel": "?", "php_pg": "$1", "node_mysql": "?", "node_pg": "$1",
      "py_pymysql": "%s", "py_psycopg": "%s", "java_mysql": "?", "java_pg": "?", "sql_my": "?", "sql_pg": "$1"}
HEADER = {
    "php_pdo": ["<?php", "", "$pdo = new PDO('mysql:host=localhost;dbname=app', $user, $pass);", ""],
    "php_mysqli": ["<?php", "", "$link = mysqli_connect('localhost', $user, $pass, 'app');", ""],
    "wordpress": ["<?php", "/**", " * Plugin Name: Example reports", " */", "global $wpdb;", ""],
    "laravel": ["<?php", "", "namespace App\\Http\\Controllers;", "", "use Illuminate\\Support\\Facades\\DB;", "use App\\Models\\User;", ""],
    "php_pg": ["<?php", "", "$conn = pg_connect('dbname=app');", ""],
    "node_mysql": ["const mysql = require('mysql2/promise');", "", "const conn = await mysql.createConnection(cfg);", ""],
    "node_pg": ["import { Pool } from 'pg';", "", "const pool = new Pool();", ""],
    "py_pymysql": ["import pymysql", "", "conn = pymysql.connect(**cfg)", "cur = conn.cursor()", ""],
    "py_psycopg": ["import psycopg2", "", "conn = psycopg2.connect(dsn)", "cur = conn.cursor()", ""],
    "java_mysql": ["package app.reports;", "", "import java.sql.*;", "import com.mysql.cj.jdbc.Driver;", "", "public class Reports {",
                   "    void run(Connection conn) throws SQLException {"],
    "java_pg": ["package app.reports;", "", "import java.sql.*;", "import org.postgresql.Driver;", "", "public class Reports {",
                "    void run(Connection conn) throws SQLException {"],
    "sql_my": ["-- migration", ""], "sql_pg": ["-- migration", ""]}
FOOTER = {"java_mysql": ["    }", "}"], "java_pg": ["    }", "}"]}
PROJECT_FILE = {"php": ("composer.json", {"mysql": '{"require": {"ext-pdo_mysql": "*"}}', "postgresql": '{"require": {"ext-pdo_pgsql": "*"}}'}),
                "js": ("package.json", {"mysql": '{"dependencies": {"mysql2": "^3.0.0"}}', "postgresql": '{"dependencies": {"pg": "^8.0.0"}}'}),
                "py": ("requirements.txt", {"mysql": "pymysql==1.1.0\n", "postgresql": "psycopg2-binary==2.9.9\n"}),
                "java": ("pom.xml", {"mysql": "<artifactId>mysql-connector-j</artifactId>", "postgresql": "<groupId>org.postgresql</groupId>"}),
                "sql": (".env", {"mysql": "DB_CONNECTION=mysql\n", "postgresql": "DB_CONNECTION=pgsql\n"})}
DECOYS = ["Select a file from the list", "Delete from cart", "Update your profile settings", "Select the user from the menu.",
          "Insert into the form below", "Order updated successfully", "select one option from the list", "Drop table here",
          "Showing results from the last 7 days", "Create table layout", "Please select your country from the dropdown"]
OWN = ["qp_like", "qp_bound", "ph_table", "ph_order", "inj_interp", "inj_concat", "inj_format", "cm_swallow"]
OWN_TOPIC = {"qp_like": "quoted_placeholder", "qp_bound": "quoted_placeholder", "ph_table": "placeholder_name",
             "ph_order": "placeholder_name", "inj_interp": "injection", "inj_concat": "injection", "inj_format": "injection", "cm_swallow": "comment_swallows"}
SKIP_BUGS = {"lk_batch_delete", "lk_batch_update", "lk_nplus", "ix_keyset"}


# -------------------------------------------------------------------------------------------- writing SQL in code

def _q(lang, sql, rnd):
    """The SQL as a string literal of the language: (lines of code text, line index of the SQL's first character)."""
    lines = sql.split("\n")
    if lang == "php":
        style = rnd.choice(["dq", "dq", "heredoc", "nowdoc", "sq"])
        if style == "dq":
            esc = sql.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
            return ('"' + esc + '"').split("\n"), 0
        if style == "sq":
            return ("'" + sql.replace("\\", "\\\\").replace("'", "\\'") + "'").split("\n"), 0
        if re.search(r"^\s*SQL\b", sql, re.M):
            return ('"' + sql.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"').split("\n"), 0
        ind = rnd.choice(["", "    "])
        body = [ind + ln for ln in lines]
        if style == "heredoc":
            body = [b.replace("\\", "\\\\").replace("$", "\\$") for b in body]
            return ["<<<SQL"] + body + [ind + "SQL"], 1
        return ["<<<'SQL'"] + body + [ind + "SQL"], 1
    if lang == "js":
        if rnd.random() < 0.6 or len(lines) == 1:
            return ("`" + sql.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${") + "`").split("\n"), 0
        q = rnd.choice(["'", '"'])
        out = []
        for k, ln in enumerate(lines):
            sep = ("\\n" if "--" in ln else " ") if k < len(lines) - 1 else ""
            lit = q + ln.replace("\\", "\\\\").replace(q, "\\" + q) + sep + q
            out.append(lit + (" +" if k < len(lines) - 1 else ""))
        return out, 0
    if lang == "py":
        style = rnd.choice(["triple", "triple", "adjacent", "single"])
        if style == "single" and len(lines) == 1:
            q = '"' if "'" in sql else rnd.choice(["'", '"'])
            return [q + sql.replace("\\", "\\\\").replace(q, "\\" + q) + q], 0
        if style == "adjacent":
            out = []
            for k, ln in enumerate(lines):
                sep = ("\\n" if "--" in ln else " ") if k < len(lines) - 1 else ""
                out.append('"' + ln.replace("\\", "\\\\").replace('"', '\\"') + sep + '"')
            out[0] = "(" + out[0]
            out[-1] += ")"
            return out, 0
        body = sql.replace("\\", "\\\\").replace('"', '\\"')
        return ('"""' + body + '"""').split("\n"), 0
    if lang == "java":
        if rnd.random() < 0.5 and len(lines) > 1:
            if lines[-1].endswith('"'):                 # "x"""" is not Java: the closing """ goes on its own line
                return ['"""'] + ["            " + ln.replace("\\", "\\\\") for ln in lines] + ['            """'], 1
            return ['"""'] + ["            " + ln.replace("\\", "\\\\") for ln in lines[:-1]] + \
                ["            " + lines[-1].replace("\\", "\\\\") + '"""'], 1
        out = []
        for k, ln in enumerate(lines):
            sep = ("\\n" if "--" in ln else " ") if k < len(lines) - 1 else ""
            out.append('"' + ln.replace("\\", "\\\\").replace('"', '\\"') + sep + '"' + (" +" if k < len(lines) - 1 else ""))
        return out, 0
    raise ValueError(lang)


def _call(stack, lit_lines, first, rnd, kind="select", params=""):
    """Code that sends the string to the database: (lines, index of the SQL's first line)."""
    lang = STACKS[stack][0]
    var = rnd.choice(["sql", "query", "q"]) if rnd.random() < 0.4 else ""
    p = params

    def glue(prefix, suffix):
        out = list(lit_lines)
        out[0] = prefix + out[0]
        out[-1] = out[-1] + suffix
        return out

    if lang == "php":
        sv = "$" + (var or "sql")
        if var:
            pre = glue(f"{sv} = ", ";")
            if p and stack == "php_pdo":
                return pre + [f"$stmt = $pdo->prepare({sv});", "$stmt->execute([$term]);"], first
            call = {"php_pdo": f"$rows = $pdo->query({sv})->fetchAll();", "php_mysqli": f"$res = mysqli_query($link, {sv});",
                    "wordpress": f"$rows = $wpdb->get_results({sv});", "laravel": f"$rows = DB::select({sv}{p});",
                    "php_pg": f"$res = pg_query($conn, {sv});"}[stack]
            return pre + [call], first
        call = {"php_pdo": ("$stmt = $pdo->prepare(", ");") if p else ("$rows = $pdo->query(", ")->fetchAll();"),
                "php_mysqli": ("$res = mysqli_query($link, ", ");"),
                "wordpress": ("$rows = $wpdb->get_results(", ");") if not p else ("$rows = $wpdb->get_results($wpdb->prepare(", f"{p}));"),
                "laravel": ("$rows = DB::select(" if kind == "select" else "$n = DB::statement(", f"{p});"),
                "php_pg": ("$res = pg_query_params($conn, ", f"{p});") if p else ("$res = pg_query($conn, ", ");")}[stack]
        return glue(call[0], call[1]), first
    if lang == "js":
        obj = "pool" if stack == "node_pg" else "conn"
        if var:
            return glue(f"const {var} = ", ";") + [f"const res = await {obj}.query({var}{p});"], first
        return glue(f"const res = await {obj}.query(", f"{p});"), first
    if lang == "py":
        if var:
            return glue(f"{var} = ", "") + [f"cur.execute({var}{p})"], first
        return glue("cur.execute(", f"{p})"), first
    if lang == "java":
        out = glue("PreparedStatement ps = conn.prepareStatement(", ");")
        return ["        " + x if k == 0 else x for k, x in enumerate(out)], first
    return glue("", ";"), first


# ---------------------------------------------------------------------------------------------- what is planted

def _from_stmtbugs(seed, engine, rnd):
    """A stmtbugs mistake whose database fits this file (none, or the file's own) and that is found from the SQL alone."""
    from app.agents import sqlcorrect
    from app.agents.sql import request_words, unwrap

    for k in range(40):
        item = stmtbugs.make(seed * 97 + k)
        if not item or item["bug"] in SKIP_BUGS or item.get("stmt") == "ddl":
            continue
        if item.get("engine") not in (None, engine):
            continue
        sql = unwrap(item["broken"])
        if request_words(item["broken"]).strip():
            continue                                    # the mistake is about what the person said: not in code
        if "?" in sql or "%s" in sql or "$1" in sql or "{" in sql or "}" in sql:
            continue
        res = sqlcorrect.check(sql, engine=engine)
        want = stmtbugs.TOPIC[item["bug"]]
        if want not in {f["topic"] for f in res["findings"]}:
            continue
        if [f for f in res["findings"] if f["kind"] in ("error", "wrong") and f["topic"] != want]:
            continue
        return {"kind": "bug", "bug": item["bug"], "topic": want, "sql": sql, "item": item, "alone": res}
    return None


def _first_sql_line(sql: str) -> int:
    """Lines before the statement's first real character (blank lines and comments are not the statement)."""
    m = re.match(r"(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)*", sql, re.S)
    return sql.count("\n", 0, m.end())


def _cols(t, *kinds):
    return [c["name"] for c in t["cols"] if c["kind"] in kinds]


def _own(fam, stack, schema, rnd, engine=None):
    """Tonight's own mistakes, on a real table of the schema: (sql in code, truth sql, value bound, code pieces)."""
    lang, own_engine, _ = STACKS[stack]
    engine = engine or own_engine
    t = rnd.choice(list(schema["tables"].values()))
    pk, name = t["pk"], t["name"]
    texts = _cols(t, "cat", "name", "email", "sku")
    nums = _cols(t, "int", "money")
    ph = PH[stack] if lang != "sql" else ("$1" if engine == "postgresql" else "?")
    if fam in ("qp_like", "qp_bound", "ph_table", "ph_order") and stack == "php_mysqli":
        return None                                         # mysqli_query() binds nothing: a '?' there is just a character
    if fam == "qp_like":
        if not texts:
            return None
        c = rnd.choice(texts)
        pat = rnd.choice(["%{}%", "{}%", "%{}"])
        ph2 = ph if lang != "php" or stack != "php_pdo" or rnd.random() < 0.6 else ":term"
        if ph2 == "%s" and stack == "wordpress":
            return None                                     # $wpdb->prepare() strips the quotes around %s itself
        sql = f"SELECT {pk}, {c}\nFROM {name}\nWHERE {c} LIKE '{pat.format(ph2)}'\nORDER BY {pk}"
        val = rnd.choice(["a", "e", "o"])
        truth = f"SELECT {pk}, {c}\nFROM {name}\nWHERE {c} LIKE '{pat.format(val)}'\nORDER BY {pk}"
        return {"sql": sql, "truth": truth, "value": val, "ph": ph2, "params": True, "table": name}
    if fam == "qp_bound":
        if not texts or stack == "wordpress":
            return None
        c = rnd.choice(texts)
        row = rnd.choice(t["rows"])
        val = row[[x["name"] for x in t["cols"]].index(c)]
        if val is None or "'" in str(val):
            return None
        ph2 = ":" + c.lower() if stack == "php_pdo" and rnd.random() < 0.5 else ph
        sql = f"SELECT {pk}\nFROM {name}\nWHERE {c} = '{ph2}'\nORDER BY {pk}"
        truth = f"SELECT {pk}\nFROM {name}\nWHERE {c} = '{val}'\nORDER BY {pk}"
        return {"sql": sql, "truth": truth, "value": str(val), "ph": ph2, "params": True, "table": name}
    if fam == "ph_table":
        ph2 = ph if ph != "%s" else "%s"
        if stack == "wordpress":
            return None
        return {"sql": f"SELECT *\nFROM {ph2}\nWHERE {pk} = 1", "truth": None, "params": True, "table": name}
    if fam == "ph_order":
        if stack == "wordpress":
            return None
        cols = texts + nums
        if not cols:
            return None
        return {"sql": f"SELECT {pk}, {', '.join(cols[:2])}\nFROM {name}\nORDER BY {ph}", "truth": None, "params": True, "table": name}
    if fam == "cm_swallow":
        if lang == "sql" or not (texts or nums):
            return None
        c = rnd.choice(texts + nums)
        row = rnd.choice(t["rows"])
        val = row[[x["name"] for x in t["cols"]].index(c)]
        if val is None or "'" in str(val):
            return None
        lit = str(val) if c in nums else f"'{val}'"
        lines = [f"SELECT {pk}, {c}", f"FROM {name} -- {rnd.choice(['only what we need', 'filtered below', 'see ticket'])}",
                 f"WHERE {c} <> {lit}", f"ORDER BY {pk}"]
        return {"lines": lines, "truth": "\n".join(lines), "table": name, "ph": "", "value": ""}
    if fam in ("inj_interp", "inj_concat", "inj_format"):
        if not texts or lang in ("sql",):
            return None
        c = rnd.choice(texts)
        var = {"php": "$term", "js": "term", "py": "term", "java": "term"}[lang]
        if fam == "inj_interp" and lang == "java":
            return None
        if fam == "inj_format" and lang != "py" and lang != "php":
            return None
        return {"sql": f"SELECT {pk}\nFROM {name}\nWHERE {c} = '<<V>>'", "var": var, "fam": fam, "truth": None, "table": name}
    return None


def _joined(lang, lines):
    """Lines of SQL glued by the language's concatenation with a space and no newline (the way a -- comment swallows the rest)."""
    op = " ." if lang == "php" else " +"
    out = []
    for k, ln in enumerate(lines):
        last = k == len(lines) - 1
        lit = '"' + ln.replace('"', '\\"') + ("" if last else " ") + '"'
        if lang == "py":
            out.append(("(" if k == 0 else "") + lit + (")" if last else ""))
        else:
            out.append(lit + ("" if last else op))
    return out


def _code_for_injection(stack, own, rnd):
    """The SQL with the value put in by code, the language's way: (literal lines, first)."""
    lang = STACKS[stack][0]
    sql, var, fam = own["sql"], own["var"], own["fam"]
    head, tail = sql.split("<<V>>")
    if fam == "inj_interp":
        if lang == "php":
            body = head.replace('"', '\\"') + ("{" + var + "}" if rnd.random() < 0.5 else var) + tail
            return ('"' + body + '"').split("\n"), 0
        if lang == "js":
            return ("`" + head + "${" + var + "}" + tail + "`").split("\n"), 0
        if lang == "py":
            return ('f"""' + head + "{" + var + "}" + tail + '"""').split("\n"), 0
    if fam == "inj_concat":
        op = " . " if lang == "php" else " + "
        h = head.replace("\n", " ")
        return ['"' + h + '"' + op + var + op + '"' + tail.replace("\n", " ") + '"'], 0
    if fam == "inj_format":
        h = (head + "%s" + tail).replace("\n", " ")
        if lang == "py":
            return ['"' + h + '" % ' + var], 0
        return ["sprintf(\"" + h + "\", " + var + ")"], 0
    return None


# ----------------------------------------------------------------------------------------------------- projects

def make_project(seed: int):
    rnd = random.Random(seed)
    schema = make_schema(seed)
    stacks = rnd.sample(sorted(STACKS), rnd.randint(2, 3))
    lang0, engine0 = STACKS[stacks[0]][0], STACKS[stacks[0]][1]
    files, expect = {}, []
    pname, ptext = PROJECT_FILE[lang0]
    files[pname] = ptext[engine0]
    for fi, stack in enumerate(stacks):
        lang, engine, ext = STACKS[stack]
        if lang == "sql" or not sqlscan._file_engine("\n".join(HEADER[stack]), lang):
            engine = engine0                           # no driver in the file (.sql, Laravel): the project's database
        path = os.path.join(rnd.choice(["src", "app", "lib", "db" if lang == "sql" else "src/reports"]), f"file{fi}{ext}")
        lines = list(HEADER[stack])
        if lang == "sql" and engine != STACKS[stack][1]:
            lines = list(HEADER[stack])
        plan = ["bug"] * rnd.randint(1, 3) + ["own"] * rnd.randint(1, 2) + ["clean"] * rnd.randint(0, 2) + \
            ["decoy"] * rnd.randint(1, 3) + (["orm"] if lang in ("php", "py", "js") and rnd.random() < 0.5 else []) + \
            (["pieces"] if lang in ("php", "js", "py") and rnd.random() < 0.5 else []) + ["comment"]
        rnd.shuffle(plan)
        ind = "        " if lang == "java" else ("    " if lang == "py" and rnd.random() < 0.3 else "")
        if lang == "py" and ind:
            lines.append("def report(cur, term):")
        for k, what in enumerate(plan):
            lines.append("")
            if what == "decoy" and lang != "sql":
                d = rnd.choice(DECOYS)
                lit = {"php": f"$msg = '{d}';", "js": f"const msg = \"{d}\";", "py": f"msg = \"{d}\"",
                       "java": f"String msg = \"{d}\";"}[lang]
                lines.append(ind + lit)
                lines.append(ind + {"php": "echo $msg;", "js": "console.log(msg);", "py": "print(msg)",
                                    "java": "System.out.println(msg);"}[lang])
                continue
            if what == "comment":
                c = {"php": "// ", "js": "// ", "py": "# ", "java": "// ", "sql": "-- "}[lang]
                lines.append(ind + c + "SELECT * FROM old_table WHERE id = NULL")
                continue
            if what == "orm" and lang != "sql":
                orm = {"php": "$users = User::where('active', 1)->orderBy('name')->get();",
                       "py": "users = User.objects.filter(active=True).order_by('name')",
                       "js": "const users = await User.findAll({ where: { active: true } });"}[lang]
                lines.append(ind + orm)
                expect.append({"file": path, "line": len(lines), "kind": "unread", "what": "orm"})
                continue
            if what == "pieces" and lang != "sql":
                t = rnd.choice(list(schema["tables"].values()))
                code = {"php": f"$sql = \"SELECT * FROM {t['name']} WHERE \" . implode(' AND ', $conds);",
                        "js": f"const sql = 'SELECT * FROM {t['name']} WHERE ' + conds.join(' AND ');",
                        "py": f"sql = \"SELECT * FROM {t['name']} WHERE \" + \" AND \".join(conds)"}[lang]
                lines.append(ind + code)
                expect.append({"file": path, "line": len(lines), "kind": "unread", "what": "pieces"})
                continue
            if what == "clean" or what == "decoy" and lang == "sql":
                qrnd = random.Random(seed * 1000 + k + fi * 50)
                sql, q = make_query(schema, qrnd)
                if q.mysql_only and engine != "mysql" or "`" in sql or "?" in sql or "%" in sql and lang == "py" or \
                        "$" in sql or "{" in sql:
                    continue
                from app.agents import sqlcorrect

                alone = sqlcorrect.check(sql, engine=engine)
                if [g for g in alone["findings"] if g["kind"] in ("error", "wrong")]:
                    continue                           # MySQL words in a PostgreSQL file: not a valid query there
                lit, first = _q(lang, sql, rnd) if lang != "sql" else (sql.split("\n"), 0)
                code, first = _call(stack, lit, first, rnd, q.kind)
                start = len(lines) + 1 + first
                lines += [ind + x if j == 0 else x for j, x in enumerate(code)]
                expect.append({"file": path, "line": start + _first_sql_line(sql), "kind": "clean", "sql": sql,
                               "alone": sorted(g["topic"] for g in alone["findings"])})
                continue
            if what == "bug":
                got = _from_stmtbugs(seed * 31 + fi * 7 + k, engine, rnd)
                if not got:
                    continue
                sql = got["sql"]
                if lang == "py" and "%" in sql or lang != "sql" and ("$" in sql and lang == "php"):
                    continue
                lit, first = _q(lang, sql, rnd) if lang != "sql" else (sql.split("\n"), 0)
                code, first = _call(stack, lit, first, rnd, got["item"]["stmt"])
                start = len(lines) + 1 + first
                lines += [ind + x if j == 0 else x for j, x in enumerate(code)]
                expect.append({"file": path, "line": start + _first_sql_line(sql), "kind": "bug", "topic": got["topic"], "got": got, "engine": engine})
                continue
            if what == "own":
                fam = rnd.choice(OWN)
                own = _own(fam, stack, schema, rnd, engine)
                if not own:
                    continue
                if lang == "sql" and fam not in ("ph_table", "ph_order"):
                    continue
                if fam == "cm_swallow":
                    lit, first = _joined(lang, own["lines"]), 0
                    params = ""
                elif fam.startswith("inj"):
                    got = _code_for_injection(stack, own, rnd)
                    if not got:
                        continue
                    lit, first = got
                    params = ""
                else:
                    if lang == "sql":
                        lit, first = own["sql"].split("\n"), 0
                    else:
                        lit, first = _q(lang, own["sql"], rnd)
                    params = {"php": ", [$term]", "js": ", [term]", "py": ", (term,)", "java": "", "sql": ""}[lang]
                    if stack == "php_pg":
                        params = ", [$term]"
                    if stack == "php_mysqli":
                        params = ""
                code, first = _call(stack, lit, first, rnd, "select", params)
                start = len(lines) + 1 + first
                lines += [ind + x if j == 0 else x for j, x in enumerate(code)]
                expect.append({"file": path, "line": start, "kind": "own", "fam": fam, "topic": OWN_TOPIC[fam], "own": own,
                               "engine": engine, "stack": stack})
        lines += FOOTER.get(stack, [])
        files[path] = "\n".join(lines) + "\n"
    return {"seed": seed, "schema": schema, "files": files, "expect": expect, "engine0": engine0}


# ---------------------------------------------------------------------------------------------------- checking

def _put_values(sql: str, value: str, ph: str) -> str:
    v = "'" + value.replace("'", "''") + "'"
    return sql.replace(ph, v) if ph else sql


def check(project, engines: dict) -> tuple[list[str], Counter]:
    from tests.fuzz.stmtbugs import _effect

    bad, seen = [], Counter()
    with tempfile.TemporaryDirectory() as root:
        for path, text in project["files"].items():
            p = os.path.join(root, path)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(text)
        os.makedirs(os.path.join(root, "vendor", "lib"), exist_ok=True)
        with open(os.path.join(root, "vendor", "lib", "x.php"), "w") as f:
            f.write("<?php\n$q = \"SELECT * FROM t WHERE a = NULL\";\n")       # vendor/ is never read
        rep = sqlscan.scan(root)
    tag = f"project seed {project['seed']}"
    found = {(f["file"], f["line"]): f for f in rep["with_findings"]}
    unread = {(f["file"], f["line"]): f for f in rep["unread"]}
    wanted = set()
    for e in project["expect"]:
        key = (e["file"], e["line"])
        wanted.add(key)
        seen[e["kind"] if e["kind"] != "own" else e["fam"]] += 1
        if e["kind"] == "unread":
            if key not in unread:
                bad.append(f"{tag}: {e['what']} at {key} not reported as unread\n{_near(project, key)}")
            continue
        f = found.get(key)
        if e["kind"] == "clean":
            if f and [g for g in f["findings"] if g["kind"] in ("error", "wrong")]:
                bad.append(f"{tag}: false finding on a valid query at {key}: {[g['title_en'] for g in f['findings']]}\n{e['sql']}")
            if key in unread:
                bad.append(f"{tag}: valid query at {key} reported as unread: {unread[key]['why']}")
            if sorted(g["topic"] for g in (f["findings"] if f else [])) != e["alone"]:
                bad.append(f"{tag}: valid query at {key}: findings differ from the SQL pasted alone "
                           f"{sorted(g['topic'] for g in (f['findings'] if f else []))} vs {e['alone']}\n{e['sql']}")
            continue
        if not f or e["topic"] not in {g["topic"] for g in f["findings"]}:
            bad.append(f"{tag}: {e.get('bug') or e.get('fam') or e['topic']} at {key} not found "
                       f"(found {sorted(g['topic'] for g in f['findings']) if f else 'nothing'}; unread: {key in unread})\n"
                       f"{_near(project, key)}")
            continue
        if e["kind"] == "bug":
            seen["bug:" + e["got"]["bug"]] += 1
            alone = e["got"]["alone"]
            if {g["topic"] for g in alone["findings"]} != {g["topic"] for g in f["findings"]}:
                bad.append(f"{tag}: at {key} the findings differ from the SQL pasted alone: "
                           f"{sorted(g['topic'] for g in f['findings'])} vs {sorted(g['topic'] for g in alone['findings'])}")
            bad += _prove_bug(e, f, engines, tag, key, _effect)
        else:
            extra = [g["title_en"] for g in f["findings"] if g["kind"] in ("error", "wrong") and g["topic"] != e["topic"]]
            if extra:
                bad.append(f"{tag}: extra findings at {key}: {extra}\n{f['sql']}")
            bad += _prove_own(e, f, project, engines, tag, key, _effect)
    for key, f in found.items():
        if key not in wanted and [g for g in f["findings"] if g["kind"] in ("error", "wrong")]:
            bad.append(f"{tag}: a finding where nothing was planted at {key}: {[g['title_en'] for g in f['findings']]}\n{f['sql']}")
    for key, f in unread.items():
        if key not in wanted:
            bad.append(f"{tag}: unread item where nothing was planted at {key}: {f['why']}\n{f['sql']}")
    if any("vendor" in k[0] for k in list(found) + list(unread)):
        bad.append(f"{tag}: vendor/ was read")
    return bad, seen


def _near(project, key):
    lines = project["files"][key[0]].split("\n")
    a = max(0, key[1] - 3)
    return "\n".join(f"{a + i + 1:4} {x}" for i, x in enumerate(lines[a:key[1] + 3]))


def _prove_bug(e, f, engines, tag, key, _effect):
    got = e["got"]
    item = got["item"]
    bug = got["bug"]
    sure = any(g["topic"] == got["topic"] and g["kind"] in ("error", "wrong") for g in f["findings"])
    if bug in stmtbugs.FIX_THE_CHECK:
        sure = True
    if bug in stmtbugs.NO_FIX or not sure:
        return []
    fixed = f.get("corrected")
    if not fixed:
        return [f"{tag}: {bug} at {key}: no corrected query"]
    norm = lambda x: re.sub(r"\s+", " ", x or "").strip().rstrip(";").strip()   # noqa: E731
    if "/* check:" not in fixed:
        if norm(fixed) != norm(got["alone"].get("corrected")):
            return [f"{tag}: {bug} at {key}: the corrected query differs from the one for the SQL pasted alone\n{fixed}\n---\n"
                    f"{got['alone'].get('corrected')}"]
    if "/* check:" in fixed:
        return []
    bad = []
    pk = item["schema"]["tables"][item["table"]]["pk"]
    for name, eng in engines.items():
        if item.get("only") and name not in item["only"]:
            continue
        if e["engine"] == "postgresql" and name != "postgres" and item.get("engine"):
            continue
        eng.create(item["schema"])
        try:
            if _effect(eng, fixed, item["table"], pk) != _effect(eng, item["truth"], item["table"], pk):
                bad.append(f"{tag}: {bug} at {key}: on {name} the corrected query does something else\n{fixed}")
        finally:
            eng.drop(item["schema"]["order"])
    return bad


def _prove_own(e, f, project, engines, tag, key, _effect):
    own = e["own"]
    if e["fam"] not in ("qp_like", "qp_bound", "cm_swallow"):
        return []
    fixed = f.get("corrected")
    if not fixed:
        return [f"{tag}: {e['fam']} at {key}: no corrected query\n{f['sql']}"]
    ph = own["ph"]
    bad = []
    schema = project["schema"]
    pk = schema["tables"][own["table"]]["pk"]
    for name, eng in engines.items():
        run = fixed
        if name == "postgres" and "CONCAT(" in run:
            pass                                         # CONCAT exists on PostgreSQL too
        if name == "mysql" and " || " in run:
            continue                                     # the PostgreSQL spelling is for a PostgreSQL project
        run = _put_values(run, own["value"], ph)
        if ph.startswith(":"):
            run = run.replace(ph, "'" + own["value"] + "'")
        eng.create(schema)
        try:
            if _effect(eng, run, own["table"], pk) != _effect(eng, own["truth"], own["table"], pk):
                bad.append(f"{tag}: {e['fam']} at {key}: on {name} the corrected query (value bound) does something else\n{run}")
        finally:
            eng.drop(schema["order"])
    return bad


def run(count=40, seed=1, engines=None):
    engines = engines_up() if engines is None else engines
    fails, seen = [], Counter()
    for s in range(seed, seed + count):
        project = make_project(s)
        b, c = check(project, engines)
        fails += b
        seen += c
    return count, fails, seen, engines


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    made, fails, seen, engines = run(count, seed)
    for f in fails[:25]:
        print(f, "\n")
    planted = sum(v for k, v in seen.items() if k in ("bug",) or k in OWN)
    print({k: v for k, v in sorted(seen.items()) if not k.startswith("bug:")})
    print(f"{made} projects, {planted} planted mistakes, {seen['clean']} valid queries, {len(fails)} problems; "
          f"checked on: {', '.join(engines) or 'no database (rows not compared)'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
