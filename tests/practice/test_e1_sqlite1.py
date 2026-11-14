"""E1 (SQLite), night 1 of 4: SQLite's own errors, the other databases' words it refuses, and date arithmetic that runs but
means something else there.

The lab is SQLite itself (Python's sqlite3; SQLite 3.53 when this was written, the 3.45 command line for its message
formats). Every practice problem is a requirement, a query a person wrote in another database's words, and the correct
SQLite query; the correct query's rows come from SQLite, DBaba (told the database is SQLite, rule A) must find the
construct, and its corrected query must return exactly those rows on SQLite. Where the wrong query is valid on the database
it came from (`src`: MariaDB / PostgreSQL), the corrected query on SQLite must also return what the original returned there:
a translation keeps the meaning, not just the spelling.

LAB is what SQLite does with each wrong query: 'error' (refused) or 'rows' (runs, with other values: CAST AS DATE gives the
year, date - date subtracts the years, date + n gives a number). ERRORS are statements that make SQLite raise each of its
error messages; the diagnoser must name the cause from the real message, worded the ways people paste it.
"""

import os
import sqlite3
import tempfile

import pytest

from app.agents import sqlcorrect, sqlport
from app.agents.sql import analyze_sql
from app.dbknow import engines as E
from app.dbknow import reasoning as R
from tests.practice import schema as S

PAY = "FROM orders o JOIN payments p ON p.order_id = o.id"
# (id, words, wrong query, topic, correct SQLite query, keep row order?, database where the wrong query means it)
P = [
    ("now_where", "orders placed before now", "SELECT id FROM orders WHERE order_date < NOW() ORDER BY id", "dialect",
     "SELECT id FROM orders WHERE order_date < CURRENT_TIMESTAMP ORDER BY id", True, "mysql"),
    ("curdate", "orders up to today", "SELECT COUNT(*) FROM orders WHERE order_date <= CURDATE()", "dialect",
     "SELECT COUNT(*) FROM orders WHERE order_date <= CURRENT_DATE", False, "mysql"),
    ("getdate", "how many orders are in the past", "SELECT COUNT(*) FROM orders WHERE order_date < GETDATE()", "dialect",
     "SELECT COUNT(*) FROM orders WHERE order_date < CURRENT_TIMESTAMP", False, None),
    ("date_add", "each order's due date, 10 days later", "SELECT id, DATE_ADD(order_date, INTERVAL 10 DAY) AS due FROM orders ORDER BY id",
     "dialect", "SELECT id, date(julianday(order_date) + 10) AS due FROM orders ORDER BY id", True, "mysql"),
    ("date_sub_month", "a month before each order", "SELECT id, DATE_SUB(order_date, INTERVAL 1 MONTH) AS d FROM orders ORDER BY id",
     "dialect", "SELECT id, date(order_date, '-1 months') AS d FROM orders ORDER BY id", True, "mysql"),
    ("interval_where", "orders in the 30 days before 31 July",
     "SELECT id FROM orders WHERE order_date >= DATE '2024-07-31' - INTERVAL 30 DAY ORDER BY id", "dialect",
     "SELECT id FROM orders WHERE order_date >= '2024-07-01' ORDER BY id", True, "mysql"),
    ("interval_pg", "orders in the 30 days before 31 July",
     "SELECT id FROM orders WHERE order_date >= DATE '2024-07-31' - INTERVAL '30 days' ORDER BY id", "dialect",
     "SELECT id FROM orders WHERE order_date >= '2024-07-01' ORDER BY id", True, "postgres"),
    ("dateadd_ss", "a renewal date 30 days after each order", "SELECT id, DATEADD(day, 30, order_date) AS renew FROM orders ORDER BY id",
     "dialect", "SELECT id, date(julianday(order_date) + 30) AS renew FROM orders ORDER BY id", True, None),
    ("datediff_my", "days from order to each payment", f"SELECT o.id, DATEDIFF(p.paid_at, o.order_date) AS days {PAY} ORDER BY o.id, p.id",
     "dialect", f"SELECT o.id, CAST(julianday(p.paid_at) - julianday(o.order_date) AS INTEGER) AS days {PAY} ORDER BY o.id, p.id", True, "mysql"),
    ("datediff_ss", "days from order to each payment", f"SELECT o.id, DATEDIFF(day, o.order_date, p.paid_at) AS days {PAY} ORDER BY o.id, p.id",
     "dialect", f"SELECT o.id, CAST(julianday(p.paid_at) - julianday(o.order_date) AS INTEGER) AS days {PAY} ORDER BY o.id, p.id", True, None),
    ("timestampdiff", "whole days from order to payment", f"SELECT o.id, TIMESTAMPDIFF(DAY, o.order_date, p.paid_at) AS d {PAY} ORDER BY o.id, p.id",
     "dialect", f"SELECT o.id, CAST(julianday(p.paid_at) - julianday(o.order_date) AS INTEGER) AS d {PAY} ORDER BY o.id, p.id", True, "mysql"),
    ("year_month", "orders per year and month",
     "SELECT YEAR(order_date) AS y, MONTH(order_date) AS m, COUNT(*) AS n FROM orders GROUP BY YEAR(order_date), MONTH(order_date) ORDER BY y, m",
     "dialect", "SELECT CAST(substr(order_date, 1, 4) AS INTEGER) AS y, CAST(substr(order_date, 6, 2) AS INTEGER) AS m, COUNT(*) AS n "
                "FROM orders GROUP BY 1, 2 ORDER BY y, m", True, "mysql"),
    ("extract_month", "the month of each order", "SELECT id, EXTRACT(MONTH FROM order_date) AS m FROM orders ORDER BY id", "dialect",
     "SELECT id, CAST(substr(order_date, 6, 2) AS INTEGER) AS m FROM orders ORDER BY id", True, "postgres"),
    ("date_format", "order dates as day/month/year", "SELECT id, DATE_FORMAT(order_date, '%d/%m/%Y') AS shown FROM orders ORDER BY id",
     "dialect", "SELECT id, substr(order_date, 9, 2) || '/' || substr(order_date, 6, 2) || '/' || substr(order_date, 1, 4) AS shown "
                "FROM orders ORDER BY id", True, "mysql"),
    ("to_char", "orders per month as YYYY-MM",
     "SELECT to_char(order_date, 'YYYY-MM') AS month, COUNT(*) AS n FROM orders GROUP BY to_char(order_date, 'YYYY-MM') ORDER BY month",
     "dialect", "SELECT substr(order_date, 1, 7) AS month, COUNT(*) AS n FROM orders GROUP BY 1 ORDER BY month", True, "postgres"),
    ("date_trunc", "orders per month (first day)",
     "SELECT DATE_TRUNC('month', order_date) AS month, COUNT(*) AS n FROM orders GROUP BY DATE_TRUNC('month', order_date) ORDER BY month",
     "dialect", "SELECT substr(order_date, 1, 7) || '-01' AS month, COUNT(*) AS n FROM orders GROUP BY 1 ORDER BY month", True, None),
    ("left_right", "initials of first names and the last two letters of surnames",
     "SELECT id, LEFT(first_name, 2) AS a, RIGHT(last_name, 2) AS b FROM customers ORDER BY id", "dialect",
     "SELECT id, substr(first_name, 1, 2) AS a, substr(last_name, length(last_name) - 1) AS b FROM customers ORDER BY id", True, "mysql"),
    ("len", "how long each first name is", "SELECT id, LEN(first_name) AS n FROM customers ORDER BY id", "dialect",
     "SELECT id, length(first_name) AS n FROM customers ORDER BY id", True, None),
    ("locate", "where the @ is in each email", "SELECT id, LOCATE('@', email) AS at FROM customers ORDER BY id", "dialect",
     "SELECT id, instr(email, '@') AS at FROM customers ORDER BY id", True, "mysql"),
    ("charindex", "where the @ is in each email", "SELECT id, CHARINDEX('@', email) AS at FROM customers ORDER BY id", "dialect",
     "SELECT id, instr(email, '@') AS at FROM customers ORDER BY id", True, None),
    ("position", "where the @ is in each email", "SELECT id, POSITION('@' IN email) AS at FROM customers ORDER BY id", "dialect",
     "SELECT id, instr(email, '@') AS at FROM customers ORDER BY id", True, "postgres"),
    ("strpos", "where the first dot is in each email", "SELECT id, STRPOS(email, '.') AS dot FROM customers ORDER BY id", "dialect",
     "SELECT id, instr(email, '.') AS dot FROM customers ORDER BY id", True, "postgres"),
    ("greatest", "the bigger of shipping and discount", "SELECT id, GREATEST(shipping, discount) AS g FROM orders ORDER BY id", "dialect",
     "SELECT id, CASE WHEN shipping IS NULL OR discount IS NULL THEN NULL WHEN shipping >= discount THEN shipping ELSE discount END AS g "
     "FROM orders ORDER BY id", True, "mysql"),
    ("nvl", "discounts with 0 for none", "SELECT id, NVL(discount, 0) AS d FROM orders ORDER BY id", "dialect",
     "SELECT id, COALESCE(discount, 0) AS d FROM orders ORDER BY id", True, None),
    ("isnull", "commission with 0 for none", "SELECT id, ISNULL(commission, 0) AS c FROM employees ORDER BY id", "dialect",
     "SELECT id, COALESCE(commission, 0) AS c FROM employees ORDER BY id", True, None),
    ("top", "the 3 biggest orders", "SELECT TOP 3 id, amount FROM orders ORDER BY amount DESC, id", "dialect",
     "SELECT id, amount FROM orders ORDER BY amount DESC, id LIMIT 3", True, None),
    ("fetch_first", "the 2 biggest orders", "SELECT id, amount FROM orders ORDER BY amount DESC, id FETCH FIRST 2 ROWS ONLY", "dialect",
     "SELECT id, amount FROM orders ORDER BY amount DESC, id LIMIT 2", True, "postgres"),
    ("offset_fetch", "orders 3 to 5 by id", "SELECT id FROM orders ORDER BY id OFFSET 2 ROWS FETCH NEXT 3 ROWS ONLY", "dialect",
     "SELECT id FROM orders ORDER BY id LIMIT 3 OFFSET 2", True, "postgres"),
    ("ilike", "customers whose first name starts with a", "SELECT id FROM customers WHERE first_name ILIKE 'a%' ORDER BY id", "dialect",
     "SELECT id FROM customers WHERE lower(first_name) LIKE 'a%' ORDER BY id", True, "postgres"),
    ("pg_cast", "whole amounts and the order dates", "SELECT id, amount::int AS whole, order_date::date AS d FROM orders ORDER BY id",
     "dialect", "SELECT id, CAST(amount AS INTEGER) AS whole, order_date AS d FROM orders ORDER BY id", True, "postgres"),
    ("div", "each amount in whole thousands", "SELECT id, amount DIV 1000 AS thousands FROM orders ORDER BY id", "dialect",
     "SELECT id, amount / 1000 AS thousands FROM orders ORDER BY id", True, "mysql"),
    ("dual", "six times seven", "SELECT 6 * 7 AS answer FROM DUAL", "dialect", "SELECT 42 AS answer", False, "mysql"),
    ("for_update", "the paid orders, locked for a change", "SELECT id, amount FROM orders WHERE status = 'paid' ORDER BY id FOR UPDATE",
     "dialect", "SELECT id, amount FROM orders WHERE status = 'paid' ORDER BY id", True, "mysql"),
    ("rand", "three random orders (how many come back)", "SELECT COUNT(*) AS n FROM (SELECT id FROM orders ORDER BY RAND() LIMIT 3) AS x",
     "dialect", "SELECT 3 AS n", False, "mysql"),
    ("group_concat_sep", "each customer's order ids in one line",
     "SELECT customer_id, GROUP_CONCAT(id ORDER BY id SEPARATOR ';') AS ids FROM orders GROUP BY customer_id ORDER BY customer_id",
     "dialect", "SELECT customer_id, group_concat(id, ';') AS ids FROM (SELECT customer_id, id FROM orders ORDER BY customer_id, id) "
                "GROUP BY customer_id ORDER BY customer_id", True, "mysql"),
    ("show_tables", "the tables in the database", "SHOW TABLES", "dialect",
     "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name", True, None),
    ("describe", "the columns of orders", "DESCRIBE orders", "dialect", "SELECT * FROM pragma_table_info('orders')", True, None),
    # ---- runs on SQLite, but means something else there (dates are text)
    ("cast_date", "orders from July on", "SELECT id FROM orders WHERE CAST(order_date AS DATE) >= '2024-07-01' ORDER BY id", "cast_date",
     "SELECT id FROM orders WHERE order_date >= '2024-07-01' ORDER BY id", True, "mysql"),
    ("date_minus", "days from order to each payment", f"SELECT o.id, p.paid_at - o.order_date AS days {PAY} ORDER BY o.id, p.id",
     "date_math", f"SELECT o.id, CAST(julianday(p.paid_at) - julianday(o.order_date) AS INTEGER) AS days {PAY} ORDER BY o.id, p.id", True,
     "postgres"),
    ("date_plus", "a week after each order", "SELECT id, order_date + 7 AS due FROM orders ORDER BY id", "date_math",
     "SELECT id, date(julianday(order_date) + 7) AS due FROM orders ORDER BY id", True, "postgres"),
]
# changes: (id, words, wrong statement, correct SQLite statement, table whose rows are compared afterwards)
CHANGES = [
    ("insert_ignore", "add category 1 unless it is there", "INSERT IGNORE INTO categories (id, name) VALUES (1, 'Gadgets')",
     "UPDATE categories SET id = id WHERE 1 = 0", "categories"),
    ("on_duplicate", "rename category 5, or add it", "INSERT INTO categories (id, name) VALUES (5, 'Games') ON DUPLICATE KEY UPDATE name = VALUES(name)",
     "UPDATE categories SET name = 'Games' WHERE id = 5", "categories"),
    ("truncate", "empty the returns table", "TRUNCATE TABLE returns", "DELETE FROM returns", "returns"),
]
# what SQLite does with each wrong query: 'error' (refused) or 'rows' (runs, other values)
RUNS = {"cast_date", "date_minus", "date_plus"}


@pytest.fixture()
def lite():
    d = tempfile.mkdtemp()
    conn = S.sqlite_db(os.path.join(d, "bank.db"))
    yield conn
    conn.close()
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    os.rmdir(d)


def _rows(conn, sql, ordered):
    out = [tuple(S.norm(v) for v in r) for r in conn.execute(sql).fetchall()]
    return out if ordered else sorted(out)


def test_the_bank_has_enough_problems():
    assert len(P) + len(CHANGES) >= 30 and len({p[0] for p in P} | {c[0] for c in CHANGES}) == len(P) + len(CHANGES)


@pytest.mark.parametrize("pid,words,wrong,topic,fix,ordered,src", P, ids=[p[0] for p in P])
def test_dbaba_finds_and_fixes(lite, pid, words, wrong, topic, fix, ordered, src):
    res = analyze_sql(f"{wrong.rstrip(';')};\n\n{words}", engine="sqlite")
    found = [f for f in res["correct"]["findings"] if f["topic"] == topic]
    assert found, (pid, [(f["topic"], f["title_en"]) for f in res["correct"]["findings"]])
    assert all(f.get("basis") for f in found), (pid, "every finding names its fundamental")
    extra = [f["title_en"] for f in res["correct"]["findings"] if f["kind"] in ("error", "wrong") and f["topic"] not in sqlport.PORT_TOPICS]
    assert not extra, (pid, extra)
    offered = res["correct"].get("corrected")
    assert offered, (pid, "no corrected query")
    again = sqlcorrect.check(offered, engine="sqlite")["findings"]
    assert not [f for f in again if f["kind"] in ("error", "wrong")], (pid, offered, again)
    assert _rows(lite, offered, ordered) == _rows(lite, fix, ordered), (pid, offered)


def test_the_lab_table(lite):
    """What SQLite really does with each wrong query."""
    for pid, _w, wrong, _t, _f, _o, _s in P:
        try:
            lite.execute(wrong).fetchall()
            got = "rows"
        except sqlite3.Error:
            got = "error"
        assert got == ("rows" if pid in RUNS else "error"), (pid, got)
    # the rows that run are the wrong values the findings describe
    assert lite.execute("SELECT CAST('2024-01-05' AS DATE), '2024-02-03' - '2024-01-25', '2024-02-03' + 1").fetchone() == (2024, 0, 2025)
    assert lite.execute("SELECT date('2024-01-31', '+1 months'), date('2024-01-31', '+-3 days')").fetchone() == ("2024-03-02", None)


def test_a_translation_keeps_the_meaning(lite):
    """Where the wrong query runs on the database it came from, the SQLite rewrite returns what it returned there."""
    engines = S.engines()
    if len(engines) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    for pid, words, wrong, _t, _f, ordered, src in P:
        if not src:
            continue
        offered = analyze_sql(f"{wrong};\n\n{words}", engine="sqlite")["correct"]["corrected"]
        eng = engines[src]
        S.build(eng)
        there = S.rows(eng, wrong, ordered=ordered)
        here = _rows(lite, offered, ordered)
        assert [tuple(r) for r in there] == here, (pid, src, there, here, offered)


@pytest.mark.parametrize("cid,words,wrong,fix,table", CHANGES, ids=[c[0] for c in CHANGES])
def test_changes_do_what_was_meant(lite, cid, words, wrong, fix, table):
    res = sqlcorrect.check(wrong, engine="sqlite")
    assert any(f["topic"] == "dialect" and f["kind"] == "error" for f in res["findings"]), (cid, res["findings"])
    offered = res["corrected"]
    assert offered, cid
    with pytest.raises(sqlite3.Error):
        lite.execute(wrong)
    lite.execute("BEGIN")
    lite.execute(offered)
    got = _rows(lite, f"SELECT * FROM {table}", False)
    lite.execute("ROLLBACK")
    lite.execute("BEGIN")
    lite.execute(fix)
    want = _rows(lite, f"SELECT * FROM {table}", False)
    lite.execute("ROLLBACK")
    assert got == want, (cid, offered)


def test_every_refused_word_is_refused_by_sqlite_and_every_rewrite_runs(lite):
    """The dialect file's 'refuses' and 'port' entries, each tried on SQLite: the other database's word fails, SQLite's runs."""
    samples = {
        "pg_cast_text": "SELECT id::text FROM orders", "pg_cast_int": "SELECT amount::int FROM orders", "pg_cast_num": "SELECT amount::numeric FROM orders",
        "pg_cast_date": "SELECT order_date::date FROM orders", "pg_cast_ts": "SELECT order_date::timestamp FROM orders",
        "pg_cast_other": "SELECT amount::money FROM orders", "ilike": "SELECT id FROM customers WHERE city ILIKE 'p%'",
        "from_dual": "SELECT 1 FROM DUAL", "sysdate_bare": "SELECT SYSDATE", "offset_fetch": "SELECT id FROM orders ORDER BY id OFFSET 1 ROWS FETCH NEXT 2 ROWS ONLY",
        "fetch_first": "SELECT id FROM orders ORDER BY id FETCH FIRST 2 ROWS ONLY", "for_update": "SELECT id FROM orders FOR UPDATE",
        "insert_ignore": "INSERT IGNORE INTO categories (id, name) VALUES (9, 'x')",
        "on_duplicate": "INSERT INTO categories (id, name) VALUES (1, 'x') ON DUPLICATE KEY UPDATE name = 'y'",
        "values_fn": "INSERT INTO categories (id, name) VALUES (1, 'x') ON DUPLICATE KEY UPDATE name = VALUES(name)",
        "truncate": "TRUNCATE TABLE returns", "show_tables": "SHOW TABLES", "describe": "DESCRIBE orders",
        "update_join": "UPDATE orders o JOIN customers c ON c.id = o.customer_id SET o.status = 'x'",
        "delete_join": "DELETE o FROM orders o JOIN customers c ON c.id = o.customer_id", "alter_column": "ALTER TABLE orders MODIFY amount REAL",
        "regexp": "SELECT 'abc' REGEXP '^a'", "div": "SELECT 7 DIV 2", "mod_word": "SELECT 7 MOD 2",
    }
    d = E.dialect("sqlite")
    assert {r["id"] for r in d["refuses"]} == set(samples)
    for r in d["refuses"]:
        assert r["proven"] == ["sqlite"]
        with pytest.raises(sqlite3.Error):
            lite.execute(samples[r["id"]])
        res = sqlcorrect.check(samples[r["id"]], engine="sqlite")
        assert any(f["topic"] == "dialect" for f in res["findings"]), (r["id"], res["findings"])
        if res["corrected"]:
            lite.execute("BEGIN")
            try:
                lite.execute(res["corrected"]).fetchall()    # SQLite's own words run
            finally:
                lite.execute("ROLLBACK")
    calls = {"now": "NOW()", "curdate": "CURDATE()", "curtime": "CURTIME()", "current_date": "CURRENT_DATE()", "getdate": "GETDATE()",
             "sysdatetime": "SYSDATETIME()", "getutcdate": "GETUTCDATE()", "sysdate": "SYSDATE()", "unix_timestamp": "UNIX_TIMESTAMP(order_date)",
             "date_add": "DATE_ADD(order_date, INTERVAL 3 WEEK)", "adddate": "ADDDATE(order_date, INTERVAL 2 DAY)",
             "date_sub": "DATE_SUB(order_date, INTERVAL 1 QUARTER)", "subdate": "SUBDATE(order_date, INTERVAL 5 DAY)",
             "dateadd": "DATEADD(month, 2, order_date)", "datediff": "DATEDIFF('2024-12-31', order_date)",
             "timestampdiff": "TIMESTAMPDIFF(HOUR, order_date, '2024-12-31 10:00:00')", "year": "YEAR(order_date)", "month": "MONTH(order_date)",
             "day": "DAY(order_date)", "dayofmonth": "DAYOFMONTH(order_date)", "hour": "HOUR('2024-01-01 10:20:30')",
             "minute": "MINUTE('2024-01-01 10:20:30')", "datepart": "DATEPART(year, order_date)", "date_format": "DATE_FORMAT(order_date, '%Y-%m')",
             "to_char": "to_char(order_date, 'YYYY-MM-DD')", "date_trunc": "DATE_TRUNC('year', order_date)", "nvl": "NVL(discount, 0)",
             "isnull": "ISNULL(discount, 0)", "left": "LEFT(status, 3)", "len": "LEN(status)", "char_length": "CHAR_LENGTH(status)",
             "character_length": "CHARACTER_LENGTH(status)", "ucase": "UCASE(status)", "lcase": "LCASE(status)", "locate": "LOCATE('a', status)",
             "charindex": "CHARINDEX('a', status)", "strpos": "STRPOS(status, 'a')", "rand": "RAND()", "last_insert_id": "LAST_INSERT_ID()",
             "lastval": "LASTVAL()", "scope_identity": "SCOPE_IDENTITY()"}
    assert {c["fn"] for c in d["port"]["calls"]} == set(calls)
    for fn, expr in calls.items():
        q = f"SELECT {expr} AS v FROM orders"
        with pytest.raises(sqlite3.Error):
            lite.execute(q)
        res = sqlcorrect.check(q, engine="sqlite")
        assert res["corrected"] and any(f["topic"] == "dialect" for f in res["findings"]), (fn, res["findings"])
        lite.execute(res["corrected"]).fetchall()
    for fn, expr in {"position": "POSITION('a' IN status)", "extract": "EXTRACT(DAY FROM order_date)", "greatest": "GREATEST(amount, 1000)",
                     "least": "LEAST(amount, 1000)", "right": "RIGHT(status, 2)", "group_concat": "GROUP_CONCAT(status SEPARATOR '/')",
                     "convert": "CONVERT(INT, amount)"}.items():
        q = f"SELECT {expr} AS v FROM orders"
        with pytest.raises(sqlite3.Error):
            lite.execute(q)
        res = sqlcorrect.check(q, engine="sqlite")
        assert res["corrected"], fn
        lite.execute(res["corrected"]).fetchall()


def test_sqlite_words_get_no_dialect_findings():
    """No false alarms: SQLite's own words are left alone when the database is SQLite."""
    for sql in [
        "SELECT id, date(order_date, '+7 days'), datetime('now', '-1 hours'), julianday(paid_at) - julianday(order_date) FROM orders",
        "SELECT strftime('%Y-%m', order_date) AS m, COUNT(*) FROM orders GROUP BY 1 ORDER BY 1",
        "SELECT IFNULL(discount, 0), COALESCE(shipping, 0), max(amount, 1000), min(amount, 1000) FROM orders",
        "SELECT customer_id, group_concat(id, ';') FROM orders GROUP BY customer_id",
        "SELECT id FROM orders ORDER BY id LIMIT 5 OFFSET 2",
        "SELECT first_name || ' ' || last_name AS name, substr(email, 1, instr(email, '@') - 1) AS u, typeof(id) FROM customers",
        "INSERT OR IGNORE INTO categories (id, name) VALUES (1, 'x')",
        "INSERT INTO categories (id, name) VALUES (1, 'x') ON CONFLICT (id) DO UPDATE SET name = excluded.name",
        "INSERT INTO categories (id, name) VALUES (1, 'x') ON CONFLICT DO UPDATE SET name = excluded.name",
        "SELECT CAST(amount AS INTEGER), CAST(id AS TEXT) FROM orders",
        "SELECT id FROM orders WHERE order_date >= date('now', 'start of month')",
    ]:
        bad = [(f["topic"], f["title_en"]) for f in sqlcorrect.check(sql, engine="sqlite")["findings"] if f["kind"] in ("error", "wrong")]
        assert not bad, (sql, bad)


def test_the_same_words_are_left_alone_on_mysql_and_postgresql():
    """The SQLite rules belong to SQLite (rule B): MySQL's DATE_ADD and PostgreSQL's ::date stay valid there."""
    for engine, sql in [("mariadb", "SELECT DATE_ADD(order_date, INTERVAL 7 DAY), NOW(), LEFT(status, 2) FROM orders"),
                        ("postgresql", "SELECT order_date::date, now() - INTERVAL '7 days', EXTRACT(YEAR FROM order_date) FROM orders")]:
        bad = [f["title_en"] for f in sqlcorrect.check(sql, engine=engine)["findings"] if f["kind"] in ("error", "wrong")]
        assert not bad, (engine, bad)


# ---------------------------------------------------------------- SQLite's own error messages, made on SQLite itself
SETUP = ["CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT NOT NULL, age INTEGER CHECK (age >= 0))",
         "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), amount REAL)",
         "INSERT INTO customers VALUES (1, 'a@b.test', 'Asha', 30)", "INSERT INTO orders VALUES (1, 1, 100)"]
ERRORS = [  # (statement, cause, foreign keys on?)
    ("SELECT * FROM orderz", "unknown_table", False), ("SELECT amont FROM orders", "unknown_column", False),
    ("SELECT id FROM orders JOIN customers ON customers.id = orders.customer_id", "ambiguous_column", False),
    ("SELECT id amount FROM orders WHERE id = 1 1", "syntax_error", False), ("SELECT * FROM orders WHERE", "syntax_error", False),
    ("CREATE TABLE t2 (order INTEGER)", "reserved_word", False), ("SELECT NOW()", "unknown_function", False),
    ("INSERT INTO customers (email, name) VALUES ('a@b.test', 'X')", "duplicate_key", False),
    ("INSERT INTO customers (id, name) VALUES (1, 'X')", "duplicate_key", False),
    ("INSERT INTO customers (email) VALUES ('z@b.test')", "not_null", False),
    ("INSERT INTO customers (name, age) VALUES ('X', -1)", "check_violation", False),
    ("INSERT INTO orders (customer_id, amount) VALUES (99, 5)", "fk_missing_parent", True),
    ("DELETE FROM customers WHERE id = 1", "fk_has_children", True),
    ("INSERT INTO customers (id, name) VALUES ('abc', 'X')", "bad_value_type", False),
    ("SELECT customer_id FROM orders WHERE SUM(amount) > 10", "aggregate_misuse", False),
    ("INSERT INTO orders VALUES (5, 1)", "value_count_mismatch", False),
    ("INSERT INTO orders (id) VALUES (5, 1)", "value_count_mismatch", False),
    ("CREATE TABLE orders (id INTEGER)", "table_exists", False),
    ("ALTER TABLE orders ADD COLUMN note TEXT NOT NULL", "alter_not_supported", False),
    ("ALTER TABLE orders MODIFY amount NUMERIC", "alter_not_supported", False),
    ("ALTER TABLE orders ADD COLUMN code TEXT UNIQUE", "alter_not_supported", False),
]
WRAPS = ["sqlite3.{cls}: {msg}", "{msg}", "SQLSTATE[HY000]: General error: 1 {msg} (Connection: sqlite, SQL: ...)",
         "Error: in prepare, {msg}", "ye error aa raha hai sqlite app mein:\n{msg}"]


def _error_of(sql, fk):
    conn = sqlite3.connect(":memory:")
    if fk:
        conn.execute("PRAGMA foreign_keys = ON")
    for s in SETUP:
        conn.execute(s)
    try:
        conn.execute(sql)
    except sqlite3.Error as exc:
        return type(exc).__name__, str(exc)
    finally:
        conn.close()
    raise AssertionError(f"no error from {sql}")


@pytest.mark.parametrize("sql,cause,fk", ERRORS, ids=[e[1] + "_" + str(i) for i, e in enumerate(ERRORS)])
def test_sqlite_errors_are_diagnosed_from_the_real_message(sql, cause, fk):
    cls, msg = _error_of(sql, fk)
    for k, wrap in enumerate(WRAPS):
        text = wrap.format(cls=cls, msg=msg)
        if cause == "fk_has_children":
            text += "\n(while running: " + sql + ")"            # SQLite's message doesn't say which side; the statement does
        res = R.diagnose(text)
        found = [x["cause"] for x in sorted(res["found"], key=lambda x: -x["score"])]
        assert found[:1] == [cause], (text, found)
        assert res["engine"] == "sqlite", (text, res["engine"])
        rep = R.report(res, False)
        assert "Fix (SQLite)" in rep and "Based on what you pasted" in rep, rep[:300]


def test_the_file_level_errors():
    """Errors about the database file itself, made on SQLite: a lock held by another connection, a read-only file, a path
    that can't be opened, a full database, a damaged file."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "app.db")
    a = sqlite3.connect(p, timeout=0, isolation_level=None)
    a.execute("CREATE TABLE t (x)")
    a.execute("BEGIN IMMEDIATE")
    a.execute("INSERT INTO t VALUES (1)")
    b = sqlite3.connect(p, timeout=0, isolation_level=None)
    msgs = {}
    with pytest.raises(sqlite3.OperationalError) as e:
        b.execute("INSERT INTO t VALUES (2)")
    msgs["db_file_locked"] = str(e.value)
    a.execute("COMMIT")
    ro = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError) as e:
        ro.execute("INSERT INTO t VALUES (3)")
    msgs["file_readonly"] = str(e.value)
    with pytest.raises(sqlite3.OperationalError) as e:
        sqlite3.connect(os.path.join(d, "no", "such", "dir", "x.db")).execute("SELECT 1")
    msgs["cannot_open_file"] = str(e.value)
    m = sqlite3.connect(":memory:")
    m.execute("CREATE TABLE q (x)")
    m.execute("PRAGMA max_page_count = 3")
    with pytest.raises(sqlite3.OperationalError) as e:
        for _ in range(500):
            m.execute("INSERT INTO q VALUES (?)", ("x" * 500,))
    msgs["disk_full"] = str(e.value)
    bad = os.path.join(d, "bad.db")
    with open(bad, "wb") as f:
        f.write(b"this is not a database file " * 50)
    with pytest.raises(sqlite3.DatabaseError) as e:
        sqlite3.connect(bad).execute("SELECT * FROM sqlite_master")
    msgs["file_corrupt"] = str(e.value)
    for cause, msg in msgs.items():
        res = R.diagnose(msg)
        assert [x["cause"] for x in res["found"]][:1] == [cause] and res["engine"] == "sqlite", (msg, res["found"])
        assert "Fix (SQLite)" in R.report(res, False)
    for c in (a, b, ro, m):
        c.close()


def test_command_line_and_driver_formats():
    """The sqlite3 command line (3.45: 'Error: in prepare, ...', 'Error: stepping, ... (19)', 'Parse error near line ...') and
    other drivers' wrappers name SQLite; the codes alone (SQLITE_BUSY, SQLITE_CONSTRAINT_UNIQUE) are read too."""
    for text, cause in [
        ("Error: stepping, UNIQUE constraint failed: c.email (19)", "duplicate_key"),
        ("Parse error near line 2: no such table: nope", "unknown_table"),
        ("Runtime error near line 4: NOT NULL constraint failed: customers.name (19)", "not_null"),
        ("SQLITE_BUSY: database is locked", "db_file_locked"),
        ("android.database.sqlite.SQLiteConstraintException: UNIQUE constraint failed: users.email (code 2067 SQLITE_CONSTRAINT_UNIQUE)", "duplicate_key"),
        ("Microsoft.Data.Sqlite.SqliteException (0x80004005): SQLite Error 1: 'no such column: emial'.", "unknown_column"),
        ("Error: unable to open database \"/srv/app/data.db\": unable to open database file", "cannot_open_file"),
    ]:
        res = R.diagnose(text)
        assert [x["cause"] for x in res["found"]][:1] == [cause] and res["engine"] == "sqlite", (text, res["found"], res["engine"])


def test_general_causes_on_mysql_and_postgresql_too():
    """The three new general causes, with each server's own message (lab: MariaDB 10.11 / PostgreSQL 16)."""
    for text, cause, eng in [
        ("ERROR 1111 (HY000): Invalid use of group function", "aggregate_misuse", "mysql"),
        ("ERROR 1136 (21S01): Column count doesn't match value count at row 1", "value_count_mismatch", "mysql"),
        ("ERROR 1050 (42S01): Table 'orders' already exists", "table_exists", "mysql"),
        ("ERROR:  aggregate functions are not allowed in WHERE\nLINE 1: SELECT a FROM t WHERE SUM(a) > 1", "aggregate_misuse", "postgresql"),
        ("ERROR:  INSERT has more expressions than target columns", "value_count_mismatch", "postgresql"),
        ("ERROR:  relation \"orders\" already exists", "table_exists", "postgresql"),
        ("SQLSTATE[42601]: Syntax error: 7 ERROR:  INSERT has more target columns than expressions", "value_count_mismatch", "postgresql"),
    ]:
        res = R.diagnose(text)
        assert [x["cause"] for x in res["found"]][:1] == [cause], (text, res["found"])
        assert res["engine"] == eng, (text, res["engine"])


def test_rule_a_detection_from_sqlite_words_and_errors():
    for text in ["SELECT strftime('%Y', order_date), COUNT(*) FROM orders GROUP BY 1", "SELECT julianday(b) - julianday(a) FROM t",
                 "INSERT OR REPLACE INTO t (id, n) VALUES (1, 'x')", "SELECT name FROM sqlite_master WHERE type = 'table'",
                 "sqlite3.IntegrityError: UNIQUE constraint failed: users.email", "Error: in prepare, no such column: emial",
                 "database is locked"]:
        assert E.shown(text)[0] == "sqlite", text
    for text in ["SELECT DATE_FORMAT(d, '%Y') FROM t LIMIT 2, 5", "SELECT d::date FROM t", "SELECT TOP 5 id FROM t",
                 "SELECT id FROM t WHERE created >= datetime_col"]:
        assert E.shown(text)[0] != "sqlite", text


def test_the_notes_line_says_what_is_lab_tested():
    line = E.notes_line("sqlite", "en", "SELECT 1")
    assert "lab-tested on SQLite so far" in line and "error messages" in line and "general SQL reasoning" in line
    assert "no lab-tested SQLite knowledge" not in line
    assert "no lab-tested SQL Server knowledge" in E.notes_line("sqlserver", "en", "SELECT 1")    # still notes only
    assert E.syntax_family("sqlite") == "sqlite" and E.family("sqlite") is None and E.notes_only("sqlite")
    assert E.syntax_family("sqlserver") is None and E.syntax_family("mariadb") == "mysql"
    assert "sqlite" in R.engines() and "sqlserver" not in R.engines()


def test_translate_to_sqlite_is_rewritten_and_labelled():
    t = sqlport.translate("SELECT id, DATE_ADD(order_date, INTERVAL 7 DAY) AS due, IFNULL(discount, 0) FROM orders WHERE order_date < NOW()",
                          "sqlite", "mysql")
    assert not t["notes_only"] and "date(order_date, '+7 days')" in t["sql"] and "datetime('now')" in t["sql"]
    text = sqlport.translation_text("", t, "en")
    assert "SQLite 3.53" in text and "MariaDB 10.11" not in text
