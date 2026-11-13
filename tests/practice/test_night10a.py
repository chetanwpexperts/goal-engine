"""Night 10A practice bank: SQL inside a project's code. Each problem is a requirement, the code someone wrote for it (PHP
with PDO / mysqli / $wpdb / Laravel, Node with mysql2 / pg, Python with pymysql / psycopg, Java JDBC, a .sql migration) and
what is wrong with it. DBaba must find the query at its line, name the mistake under its topic, and its corrected query
(placeholders bound to the requirement's values) must return the verified rows on the project's database. For a value written
into the SQL text, the query DBaba rewrites with placeholders must return the same rows once the values are bound. Queries
built in pieces and ORM calls must be reported as found but not read; ordinary strings and safe code must give nothing.

LAB is what each engine really does with the placeholder mistakes (MariaDB 10.11 / PostgreSQL 16), re-run here.
"""

import re

import pytest

from app.agents import sqlscan
from tests.practice import schema as S

# (id, requirement, file, code, line, topic, the right query, values bound in order, database)
P = [
    # ---- PHP
    ("pdo_like_quoted", "customers whose email contains the search text", "search.php",
     '<?php\n$stmt = $pdo->prepare("SELECT id, first_name FROM customers WHERE email LIKE \'%?%\' ORDER BY id");\n$stmt->execute([$term]);\n',
     2, "quoted_placeholder", "SELECT id, first_name FROM customers WHERE email LIKE '%diya%' ORDER BY id", ["diya"], "mysql"),
    ("pdo_named_quoted", "customers in the chosen city", "city.php",
     "<?php\n$sql = \"SELECT id FROM customers WHERE city = ':city' ORDER BY id\";\n$stmt = $pdo->prepare($sql);\n"
     "$stmt->execute(['city' => $city]);\n",
     2, "quoted_placeholder", "SELECT id FROM customers WHERE city = 'Pune' ORDER BY id", ["Pune"], "mariadb"),
    ("pdo_starts_with", "products whose sku starts with the typed prefix", "sku.php",
     "<?php\n$stmt = $pdo->prepare(\"SELECT id, sku FROM products WHERE sku LIKE '?%' ORDER BY id\");\n$stmt->execute([$prefix]);\n",
     2, "quoted_placeholder", "SELECT id, sku FROM products WHERE sku LIKE 'LP%' ORDER BY id", ["LP"], "mysql"),
    ("mysqli_interp", "orders with the chosen status", "orders.php",
     "<?php\n$res = mysqli_query($link, \"SELECT id FROM orders WHERE status = '$status' ORDER BY id\");\n",
     2, "injection", "SELECT id FROM orders WHERE status = 'shipped' ORDER BY id", ["shipped"], "mysql"),
    ("wpdb_like_interp", "products whose name contains the search word", "plugin.php",
     "<?php\nglobal $wpdb;\n$rows = $wpdb->get_results(\"SELECT id, name FROM products WHERE name LIKE '%{$term}%' ORDER BY id\");\n",
     3, "injection", "SELECT id, name FROM products WHERE name LIKE '%Laptop%' ORDER BY id", ["%Laptop%"], "mysql"),
    ("laravel_concat", "customers of one city", "CustomerController.php",
     "<?php\nuse Illuminate\\Support\\Facades\\DB;\n$rows = DB::select(\"SELECT id FROM customers WHERE city = '\" . $city . \"' ORDER BY id\");\n",
     3, "injection", "SELECT id FROM customers WHERE city = 'Delhi' ORDER BY id", ["Delhi"], "mysql"),
    ("pdo_order_param", "customers sorted by the column the user picked", "list.php",
     "<?php\n$stmt = $pdo->prepare(\"SELECT id, first_name FROM customers ORDER BY ?\");\n$stmt->execute([$sort]);\n",
     2, "placeholder_name", None, [], "mysql"),
    ("pdo_table_param", "rows of the table the report asks for", "report.php",
     "<?php\n$stmt = $pdo->prepare(\"SELECT * FROM ? WHERE id = 1\");\n$stmt->execute([$table]);\n",
     2, "placeholder_name", None, [], "mysql"),
    ("heredoc_eq_null", "customers with no phone number", "phones.php",
     "<?php\n$sql = <<<SQL\n    SELECT id\n    FROM customers\n    WHERE phone = NULL\n    ORDER BY id\n    SQL;\n$rows = $pdo->query($sql)->fetchAll();\n",
     3, "null", "SELECT id FROM customers WHERE phone IS NULL ORDER BY id", [], "mysql"),
    ("nowdoc_unquoted_word", "orders that were delivered... here 'shipped'", "ship.php",
     "<?php\n$sql = <<<'SQL'\nSELECT id FROM orders WHERE status = shipped ORDER BY id\nSQL;\n$res = mysqli_query($link, $sql);\n",
     3, "value", "SELECT id FROM orders WHERE status = 'shipped' ORDER BY id", [], "mysql"),
    ("php_comment_swallows", "paid orders only", "paid.php",
     "<?php\n$sql = \"SELECT id, amount FROM orders -- paid ones \" .\n       \"WHERE status = 'paid' \" .\n       \"ORDER BY id\";\n"
     "$rows = $pdo->query($sql)->fetchAll();\n",
     2, "comment_swallows", "SELECT id, amount FROM orders WHERE status = 'paid' ORDER BY id", [], "mysql"),
    ("php_between_reversed", "orders between 1000 and 30000", "range.php",
     "<?php\n$rows = $pdo->query(\"SELECT id FROM orders WHERE amount BETWEEN 30000 AND 1000 ORDER BY id\")->fetchAll();\n",
     2, "between", "SELECT id FROM orders WHERE amount BETWEEN 1000 AND 30000 ORDER BY id", [], "mysql"),
    ("php_pieces", "orders filtered by whatever the form sent", "filter.php",
     "<?php\n$sql = \"SELECT id FROM orders WHERE \" . implode(' AND ', $where);\n$rows = $pdo->query($sql)->fetchAll();\n",
     2, "unread", None, [], "mysql"),
    ("php_appended", "orders, then a status filter added later", "append.php",
     "<?php\n$sql = \"SELECT id FROM orders\";\nif ($paid) {\n    $sql .= \" WHERE status = 'paid'\";\n}\n$rows = $pdo->query($sql)->fetchAll();\n",
     2, "unread", None, [], "mysql"),
    ("php_eloquent", "active users through the ORM", "Users.php",
     "<?php\n$users = User::where('active', 1)->orderBy('name')->get();\n", 2, "unread", None, [], "mysql"),
    ("php_int_cast_safe", "one order by id (the id forced to a number)", "one.php",
     "<?php\n$rows = $pdo->query(\"SELECT id, amount FROM orders WHERE id = \" . (int)$id . \" ORDER BY id\")->fetchAll();\n",
     2, "clean", None, [], "mysql"),
    # ---- Node
    ("pg_template_interp", "customers of one city", "customers.js",
     "const { Pool } = require('pg');\nconst pool = new Pool();\nasync function byCity(city) {\n"
     "  return pool.query(`SELECT id FROM customers WHERE city = '${city}' ORDER BY id`);\n}\n",
     4, "injection", "SELECT id FROM customers WHERE city = 'Mumbai' ORDER BY id", ["Mumbai"], "postgresql"),
    ("pg_quoted_dollar", "customers of one city", "city.ts",
     "import { Pool } from 'pg';\nconst pool = new Pool();\nconst r = await pool.query(\"SELECT id FROM customers WHERE city = '$1' ORDER BY id\", [city]);\n",
     3, "quoted_placeholder", "SELECT id FROM customers WHERE city = 'Delhi' ORDER BY id", ["Delhi"], "postgresql"),
    ("pg_like_dollar", "customers whose email contains the text", "find.js",
     "const { Pool } = require('pg');\nconst pool = new Pool();\nconst r = await pool.query(\n"
     "  \"SELECT id FROM customers WHERE email LIKE '%$1%' ORDER BY id\", [text]);\n",
     4, "quoted_placeholder", "SELECT id FROM customers WHERE email LIKE '%mail%' ORDER BY id", ["mail"], "postgresql"),
    ("mysql2_like", "products whose name contains the word", "products.js",
     "const mysql = require('mysql2/promise');\nconst [rows] = await conn.query(\"SELECT id FROM products WHERE name LIKE '%?%' ORDER BY id\", [word]);\n",
     2, "quoted_placeholder", "SELECT id FROM products WHERE name LIKE '%Phone%' ORDER BY id", ["Phone"], "mysql"),
    ("pg_limit_mysql_words", "the second page of 3 orders", "page.js",
     "const { Pool } = require('pg');\nconst pool = new Pool();\nconst r = await pool.query('SELECT id FROM orders ORDER BY id LIMIT 3, 3');\n",
     3, "dialect", "SELECT id FROM orders ORDER BY id LIMIT 3 OFFSET 3", [], "postgresql"),
    ("node_comment_swallows", "customers in Pune only", "pune.js",
     "const mysql = require('mysql2/promise');\nconst sql = 'SELECT id FROM customers -- Pune only ' +\n  \"WHERE city = 'Pune' \" +\n"
     "  'ORDER BY id';\nconst [rows] = await conn.query(sql);\n",
     2, "comment_swallows", "SELECT id FROM customers WHERE city = 'Pune' ORDER BY id", [], "mysql"),
    ("node_sequelize", "active users through the ORM", "users.js",
     "const users = await User.findAll({ where: { active: true } });\n", 1, "unread", None, [], "mysql"),
    ("node_template_safe", "one customer, bound properly", "safe.js",
     "const { Pool } = require('pg');\nconst pool = new Pool();\nconst r = await pool.query(`SELECT id, first_name FROM customers WHERE id = $1`, [id]);\n",
     3, "clean", None, [], "postgresql"),
    # ---- Python
    ("psycopg_quoted", "customers of one city", "report.py",
     "import psycopg2\n\ndef by_city(cur, city):\n    cur.execute(\"SELECT id FROM customers WHERE city = '%s' ORDER BY id\", (city,))\n",
     4, "quoted_placeholder", "SELECT id FROM customers WHERE city = 'Chennai' ORDER BY id", ["Chennai"], "postgresql"),
    ("pymysql_fstring", "orders with the chosen status", "orders.py",
     "import pymysql\n\ndef by_status(cur, status):\n    cur.execute(f\"SELECT id FROM orders WHERE status = '{status}' ORDER BY id\")\n",
     4, "injection", "SELECT id FROM orders WHERE status = 'pending' ORDER BY id", ["pending"], "mysql"),
    ("pymysql_percent", "orders of one customer", "cust.py",
     "import pymysql\n\ndef of_customer(cur, cid):\n    cur.execute(\"SELECT id FROM orders WHERE customer_id = %s ORDER BY id\" % cid)\n",
     4, "injection", "SELECT id FROM orders WHERE customer_id = 3 ORDER BY id", ["3"], "mysql"),
    ("psycopg_format", "suppliers of one country", "sup.py",
     "import psycopg2\n\ndef by_country(cur, country):\n    q = \"SELECT id FROM suppliers WHERE country = '{}' ORDER BY id\".format(country)\n"
     "    cur.execute(q)\n",
     4, "injection", "SELECT id FROM suppliers WHERE country = 'India' ORDER BY id", ["India"], "postgresql"),
    ("py_adjacent_comment", "products in stock only", "stock.py",
     "import pymysql\n\nq = (\"SELECT id FROM products -- in stock \"\n     \"WHERE stock > 0 \"\n     \"ORDER BY id\")\ncur.execute(q)\n",
     3, "comment_swallows", "SELECT id FROM products WHERE stock > 0 ORDER BY id", [], "mysql"),
    ("py_triple_eq_null", "customers without a middle name", "names.py",
     "import psycopg2\n\ncur.execute(\"\"\"\n    SELECT id\n    FROM customers\n    WHERE middle_name = NULL\n    ORDER BY id\n\"\"\")\n",
     4, "null", "SELECT id FROM customers WHERE middle_name IS NULL ORDER BY id", [], "postgresql"),
    ("py_django_orm", "active users through the ORM", "views.py",
     "from app.models import User\nusers = User.objects.filter(active=True).order_by('name')\n", 2, "unread", None, [], "postgresql"),
    ("py_bound_safe", "orders of one customer, bound", "ok.py",
     "import pymysql\ncur.execute(\"SELECT id FROM orders WHERE customer_id = %s ORDER BY id\", (cid,))\n", 2, "clean", None, [], "mysql"),
    # ---- Java
    ("jdbc_quoted", "customers of one city", "Customers.java",
     "import java.sql.*;\nimport com.mysql.cj.jdbc.Driver;\nclass C {\n  void f(Connection conn, String city) throws SQLException {\n"
     "    PreparedStatement ps = conn.prepareStatement(\"SELECT id FROM customers WHERE city = '?' ORDER BY id\");\n"
     "    ps.setString(1, city);\n  }\n}\n",
     5, "quoted_placeholder", "SELECT id FROM customers WHERE city = 'Mumbai' ORDER BY id", ["Mumbai"], "mysql"),
    ("jdbc_concat", "employees of one department", "Staff.java",
     "import java.sql.*;\nimport org.postgresql.Driver;\nclass S {\n  void f(Connection conn, String dept) throws SQLException {\n"
     "    Statement st = conn.createStatement();\n"
     "    ResultSet rs = st.executeQuery(\"SELECT e.id FROM employees e JOIN departments d ON d.id = e.department_id WHERE d.name = '\" + dept + \"' ORDER BY e.id\");\n"
     "  }\n}\n",
     6, "injection", "SELECT e.id FROM employees e JOIN departments d ON d.id = e.department_id WHERE d.name = 'Support' ORDER BY e.id",
     ["Support"], "postgresql"),
    ("jdbc_textblock_date", "orders after 1 July 2024", "Orders.java",
     "import java.sql.*;\nimport com.mysql.cj.jdbc.Driver;\nclass O {\n  void f(Connection conn) throws SQLException {\n"
     "    PreparedStatement ps = conn.prepareStatement(\"\"\"\n        SELECT id FROM orders\n        WHERE order_date > 2024-07-01\n"
     "        ORDER BY id\"\"\");\n  }\n}\n",
     6, "value", "SELECT id FROM orders WHERE order_date > '2024-07-01' ORDER BY id", [], "mysql"),
    ("jdbc_order_param", "employees sorted by the picked column", "Sort.java",
     "import java.sql.*;\nimport org.postgresql.Driver;\nclass T {\n  void f(Connection conn) throws SQLException {\n"
     "    PreparedStatement ps = conn.prepareStatement(\"SELECT id, first_name FROM employees ORDER BY ?\");\n  }\n}\n",
     5, "placeholder_name", None, [], "postgresql"),
    # ---- a .sql migration
    ("sql_file_eq_null", "returns with no reason given", "migrations/0003_report.sql",
     "-- report used by the returns page\nCREATE TABLE demo_child (id INT PRIMARY KEY, note VARCHAR(40));\n\n"
     "SELECT id FROM returns WHERE reason = NULL ORDER BY id;\n",
     4, "null", "SELECT id FROM returns WHERE reason IS NULL ORDER BY id", [], "postgresql"),
]

# what each engine really does with a placeholder mistake (re-run below): MariaDB first, then PostgreSQL
LAB = [
    ("quoted ? with a value bound", "PREPARE s FROM 'SELECT id FROM customers WHERE city = ''?'''; SET @v = 'Pune'; EXECUTE s USING @v",
     "PREPARE s(text) AS SELECT id FROM customers WHERE city = '$1'; EXECUTE s('Pune')", "error 1210", "no rows"),
    ("ORDER BY a bound column name", "PREPARE s FROM 'SELECT id FROM customers WHERE id IN (3, 4, 6, 8) ORDER BY ?, id'; SET @c = 'first_name'; EXECUTE s USING @c",
     "PREPARE s(text) AS SELECT id FROM customers WHERE id IN (3, 4, 6, 8) ORDER BY $1, id; EXECUTE s('first_name')", "3,4,6,8", "3,4,6,8"),
    ("a table name as a placeholder", "PREPARE s FROM 'SELECT * FROM ?'", "PREPARE s AS SELECT * FROM $1", "error 1064", "error 42601"),
    ("IN (?) bound to '1,2,3'", "PREPARE s FROM 'SELECT id FROM customers WHERE id IN (?) ORDER BY id'; SET @l = '2,4,6'; EXECUTE s USING @l",
     "PREPARE s(text) AS SELECT id FROM customers WHERE id IN ($1)", "2", "error 42883"),
    ("CONCAT around an untyped parameter", "PREPARE s FROM 'SELECT id FROM customers WHERE email LIKE CONCAT(''%'', ?, ''%'') ORDER BY id'; SET @v = 'diya'; EXECUTE s USING @v",
     "PREPARE s AS SELECT id FROM customers WHERE email LIKE CONCAT('%', $1, '%')", "2,7", "error 42P18"),
]


def _engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


@pytest.fixture(scope="module")
def engines():
    return _engines()


PH = re.compile(r"\?|%s|\$\d+|:[A-Za-z_]\w*")


def _bind(sql: str, values: list) -> str:
    """Put the requirement's values where the placeholders are (outside quotes), in order."""
    out, k, i = [], 0, 0
    q = False
    while i < len(sql):
        c = sql[i]
        if c == "'":
            q = not q
            out.append(c)
            i += 1
            continue
        m = PH.match(sql, i) if not q else None
        if m and k < len(values) and not (m.group(0).startswith(":") and i > 0 and sql[i - 1] == ":"):
            v = values[k]
            out.append(v if re.fullmatch(r"-?\d+", v) else "'" + v.replace("'", "''") + "'")
            k += 1
            i = m.end()
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _rows(eng, sql):
    S.build(eng)
    return S.rows(eng, sql, ordered=True)


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)


@pytest.mark.parametrize("pid,words,path,code,line,topic,truth,values,engine", P, ids=[p[0] for p in P])
def test_dbaba_finds_and_fixes(engines, pid, words, path, code, line, topic, truth, values, engine):
    found = sqlscan.scan_text(path, code, engine, named=True)
    at = [f for f in found if f.line == line]
    if topic == "unread":
        assert at and at[0].kind == "unread", (pid, [(f.line, f.kind, f.why) for f in found])
        return
    assert at and at[0].kind == "read", (pid, [(f.line, f.kind, f.why, [g["topic"] for g in f.findings]) for f in found])
    f = at[0]
    if topic == "clean":
        assert not f.findings, (pid, [g["title_en"] for g in f.findings])
        return
    mine = [g for g in f.findings if g["topic"] == topic]
    assert mine, (pid, [(g["topic"], g["title_en"]) for g in f.findings])
    other = [g["title_en"] for g in f.findings if g["kind"] in ("error", "wrong") and g["topic"] != topic]
    assert not other, (pid, other)
    if truth is None:
        assert mine[0]["kind"] in ("error", "wrong"), pid
        return
    offered = f.parameterized if topic == "injection" else f.corrected
    assert offered, (pid, "no corrected query")
    if topic == "injection":
        assert len(f.values) == len(values), (pid, f.values)
    eng = engines["postgres" if engine == "postgresql" else "mysql"]
    run = _bind(offered, values)
    assert _rows(eng, run) == _rows(eng, truth), (pid, run)


def test_ordinary_strings_give_nothing():
    code = ("<?php\n$a = 'Select a file from the list';\n$b = \"Delete from cart\";\n$c = 'Update your profile settings';\n"
            "$d = \"select one option from the list\";\n// SELECT * FROM t WHERE a = NULL\n/* DELETE FROM users */\n"
            "$e = 'Showing results from the last 7 days';\n$f = \"Insert into the form below\";\necho \"<select name='x'>\";\n")
    assert sqlscan.scan_text("ui.php", code) == []
    js = ("const re = /it's \"odd\"/g;\nconst label = 'Select the user from the menu.';\nconsole.log(`Update ${n} rows`);\n"
          "// const q = \"SELECT * FROM t WHERE a = NULL\";\n")
    assert sqlscan.scan_text("ui.js", js) == []
    py = "msg = 'Select a file from the list'\n# cur.execute(\"SELECT * FROM t WHERE a = NULL\")\nlog = f\"Deleted {n} rows from cart\"\n"
    assert sqlscan.scan_text("ui.py", py) == []


def test_the_lab_table(engines):
    for what, my, pg, want_my, want_pg in LAB:
        for name, sql, want in (("mysql", my, want_my), ("postgres", pg, want_pg)):
            eng = engines[name]
            S.build(eng)
            got = None
            try:
                rows = None
                for stmt in [x for x in sql.split("; ") if x.strip()]:
                    out = eng.run(stmt)
                    if isinstance(out, (list, tuple)):
                        rows = out
                got = ",".join(str(r[0]) for r in rows) if rows else "no rows"
            except Exception as exc:  # noqa: BLE001
                eng.rollback()
                code = (exc.args[0] if exc.args else "") if name == "mysql" else getattr(exc, "sqlstate", "")
                got = f"error {code}"
            finally:
                try:
                    eng.run("DEALLOCATE PREPARE s" if name == "mysql" else "DEALLOCATE s")
                except Exception:  # noqa: BLE001
                    eng.rollback()
            assert got == want, (what, name, got, want)


def test_project_scan_and_command(tmp_path, capsys):
    """A folder: vendor/ and node_modules/ are never read, the database comes from the project's own files (rule A), the
    command prints file:line and exits 1 when something is definitely wrong; nothing in the folder is changed."""
    (tmp_path / "composer.json").write_text('{"require": {"ext-pdo_mysql": "*"}}')
    (tmp_path / "src").mkdir()
    bad = "<?php\n$rows = $pdo->query(\"SELECT id FROM orders WHERE amount = NULL\")->fetchAll();\n"
    (tmp_path / "src" / "a.php").write_text(bad)
    for skip in ("vendor/lib", "node_modules/x", ".git/hooks", "build"):
        (tmp_path / skip).mkdir(parents=True)
        (tmp_path / skip / "b.php").write_text(bad)
    before = {p: p.read_text() for p in tmp_path.rglob("*") if p.is_file()}
    rep = sqlscan.scan(str(tmp_path))
    assert rep["engine"] == "mysql" and "composer.json" in rep["engine_how"]
    assert [(f["file"], f["line"]) for f in rep["with_findings"]] == [("src/a.php", 2)]
    assert sqlscan.main(["check", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "src/a.php:2" in out and "IS NULL" in out and "Database: MySQL" in out
    assert sqlscan.main(["check", str(tmp_path), "--engine", "postgres"]) == 1
    assert "Database: PostgreSQL (named)" in capsys.readouterr().out
    assert before == {p: p.read_text() for p in tmp_path.rglob("*") if p.is_file()}


def test_database_from_the_project():
    import tempfile
    import os

    cases = [({".env": "APP_KEY=secret\nDB_CONNECTION=pgsql\nDB_PASSWORD=hidden\n"}, "postgresql"),
             ({"package.json": '{"dependencies": {"mysql2": "^3"}}'}, "mysql"),
             ({"requirements.txt": "psycopg2-binary==2.9\n"}, "postgresql"),
             ({"wp-config.php": "<?php define('DB_NAME', 'wp');"}, "mysql"),
             ({"docker-compose.yml": "services:\n  db:\n    image: mariadb:11\n"}, "mariadb"),
             ({"package.json": '{"dependencies": {"mysql2": "^3", "pg": "^8"}}'}, None),
             ({"README.md": "hello"}, None)]
    for files, want in cases:
        with tempfile.TemporaryDirectory() as d:
            for name, text in files.items():
                with open(os.path.join(d, name), "w") as f:
                    f.write(text)
            got, why = sqlscan.detect_engine(d)
            assert got == want, (files, got, why)
            assert "secret" not in why and "hidden" not in why
