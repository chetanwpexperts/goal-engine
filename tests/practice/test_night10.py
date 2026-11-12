"""Night 10 practice bank: portability. A query written in another database's words, checked for the database the person
uses (standing rule A): DBaba must find each construct that database refuses (TOP, ROWNUM, DATEADD, DATE_ADD, IFNULL, [names],
GROUP_CONCAT, string_agg, ...) and each spelling that runs there but means something else (date - date, date + 7, INTERVAL '2'
WEEK, CAST AS CHAR, ^), and its corrected query must return the verified rows on that database.

Where the wrong query is valid on the database it came from (`src`), the proof is stronger: the corrected query on the target
returns exactly what the original returned on its own database (a translation keeps the meaning, not just the spelling).

LAB is what each engine really does with the wrong query (MariaDB 10.11 / PostgreSQL 16); MEANING holds the values that
run on both but differ. The SQLite notes are run on Python's sqlite3. The final exam (50 generated problems across nights 1-10)
closes the first ten nights.
"""

import json
import sqlite3
from collections import Counter

import pytest

from app.agents import sqlcorrect, sqlport
from app.agents.sql import analyze_sql
from tests.practice import schema as S

PAY = "FROM orders o JOIN payments p ON p.order_id = o.id"
# (id, words, wrong query, topic, correct query, keep row order?, database said, engine where the wrong query means it)
P = [
    # ---- SQL Server / Oracle words in MySQL / MariaDB / PostgreSQL
    ("top_mysql", "the 3 biggest orders", "SELECT TOP 3 id, amount FROM orders ORDER BY amount DESC, id", "dialect",
     "SELECT id, amount FROM orders ORDER BY amount DESC, id LIMIT 3", True, "mysql", None),
    ("top_paren_pg", "the 2 newest customers", "SELECT TOP (2) id FROM customers ORDER BY created_at DESC, id;", "dialect",
     "SELECT id FROM customers ORDER BY created_at DESC, id LIMIT 2", True, "postgresql", None),
    ("rownum_mysql", "the 2 biggest paid orders", "SELECT id, amount FROM orders WHERE status = 'paid' AND ROWNUM <= 2 ORDER BY amount DESC, id",
     "dialect", "SELECT id, amount FROM orders WHERE status = 'paid' ORDER BY amount DESC, id LIMIT 2", True, "mariadb", None),
    ("rownum_pg", "the first 3 products by price", "SELECT id, price FROM products WHERE ROWNUM < 4 ORDER BY price, id", "dialect",
     "SELECT id, price FROM products ORDER BY price, id LIMIT 3", True, "postgresql", None),
    ("dateadd_mysql", "each order's due date, 30 days after ordering", "SELECT id, DATEADD(day, 30, order_date) AS due FROM orders ORDER BY id",
     "dialect", "SELECT id, DATE_ADD(order_date, INTERVAL 30 DAY) AS due FROM orders ORDER BY id", True, "mariadb", None),
    ("dateadd_pg", "orders whose renewal (a month later) is after 20 July",
     "SELECT id FROM orders WHERE DATEADD(month, 1, order_date) > '2024-07-20' ORDER BY id", "dialect",
     "SELECT id FROM orders WHERE order_date + INTERVAL '1' MONTH > '2024-07-20' ORDER BY id", True, "postgresql", None),
    ("datediff_ss_mysql", "days from order to each payment", f"SELECT o.id, DATEDIFF(day, o.order_date, p.paid_at) AS days {PAY} ORDER BY o.id, p.id",
     "dialect", f"SELECT o.id, DATEDIFF(p.paid_at, o.order_date) AS days {PAY} ORDER BY o.id, p.id", True, "mysql", None),
    ("datediff_ss_pg", "days from order to each payment", f"SELECT o.id, DATEDIFF(day, o.order_date, p.paid_at) AS days {PAY} ORDER BY o.id, p.id",
     "dialect", f"SELECT o.id, p.paid_at - o.order_date AS days {PAY} ORDER BY o.id, p.id", True, "postgresql", None),
    ("len_mysql", "how long each product name is", "SELECT id, LEN(name) AS n FROM products ORDER BY id", "dialect",
     "SELECT id, CHAR_LENGTH(name) AS n FROM products ORDER BY id", True, "mariadb", None),
    ("charindex_pg", "where the @ is in each email", "SELECT id, CHARINDEX('@', email) AS at FROM customers ORDER BY id", "dialect",
     "SELECT id, POSITION('@' IN email) AS at FROM customers ORDER BY id", True, "postgresql", None),
    ("iif_mysql", "label orders big or small", "SELECT id, IIF(amount > 1000, 'big', 'small') AS size FROM orders ORDER BY id", "dialect",
     "SELECT id, CASE WHEN amount > 1000 THEN 'big' ELSE 'small' END AS size FROM orders ORDER BY id", True, "mysql", None),
    ("getdate_pg", "how many orders are in the past", "SELECT COUNT(*) FROM orders WHERE order_date < GETDATE()", "dialect",
     "SELECT COUNT(*) FROM orders WHERE order_date < now()", False, "postgresql", None),
    ("brackets_mysql", "customers in Pune", "SELECT [id], [first_name] FROM [customers] WHERE [city] = 'Pune' ORDER BY [id]", "dialect",
     "SELECT id, first_name FROM customers WHERE city = 'Pune' ORDER BY id", True, "mariadb", None),
    ("brackets_pg", "customers in Pune", "SELECT [id], [first_name] FROM [customers] WHERE [city] = 'Pune' ORDER BY [id]", "dialect",
     "SELECT id, first_name FROM customers WHERE city = 'Pune' ORDER BY id", True, "postgresql", None),
    ("convert_ss_pg", "the whole-rupee amount", "SELECT id, CONVERT(INT, amount) AS whole FROM orders ORDER BY id", "dialect",
     "SELECT id, CAST(amount AS INTEGER) AS whole FROM orders ORDER BY id", True, "postgresql", None),
    ("datepart_mysql", "orders per month",
     "SELECT DATEPART(month, order_date) AS m, COUNT(*) AS n FROM orders GROUP BY DATEPART(month, order_date) ORDER BY m", "dialect",
     "SELECT EXTRACT(MONTH FROM order_date) AS m, COUNT(*) AS n FROM orders GROUP BY EXTRACT(MONTH FROM order_date) ORDER BY m", True, "mysql", None),
    ("sysdate_mysql", "orders already placed", "SELECT COUNT(*) FROM orders WHERE order_date < SYSDATE", "dialect",
     "SELECT COUNT(*) FROM orders WHERE order_date < NOW()", False, "mariadb", None),
    # ---- MySQL / MariaDB words on PostgreSQL (src = the wrong query's meaning on MariaDB)
    ("date_add_pg", "each order's date plus 10 days", "SELECT id, CAST(DATE_ADD(order_date, INTERVAL 10 DAY) AS DATE) AS due FROM orders ORDER BY id",
     "dialect", "SELECT id, CAST(order_date + 10 AS DATE) AS due FROM orders ORDER BY id", True, "postgresql", "mysql"),
    ("date_sub_pg", "a month before each order", "SELECT id, CAST(DATE_SUB(order_date, INTERVAL 1 MONTH) AS DATE) AS d FROM orders ORDER BY id",
     "dialect", "SELECT id, CAST(order_date - INTERVAL '1 month' AS DATE) AS d FROM orders ORDER BY id", True, "postgresql", "mysql"),
    ("interval_where_pg", "orders in the 30 days before 31 July",
     "SELECT id FROM orders WHERE order_date >= DATE '2024-07-31' - INTERVAL 30 DAY ORDER BY id", "dialect",
     "SELECT id FROM orders WHERE order_date >= DATE '2024-07-01' ORDER BY id", True, "postgresql", "mysql"),
    ("datediff_pg", "days from order to each payment", f"SELECT o.id, DATEDIFF(p.paid_at, o.order_date) AS days {PAY} ORDER BY o.id, p.id",
     "dialect", f"SELECT o.id, p.paid_at - o.order_date AS days {PAY} ORDER BY o.id, p.id", True, "postgresql", "mysql"),
    ("year_month_pg", "orders per year and month",
     "SELECT YEAR(order_date) AS y, MONTH(order_date) AS m, COUNT(*) AS n FROM orders GROUP BY YEAR(order_date), MONTH(order_date) ORDER BY y, m",
     "dialect", "SELECT EXTRACT(YEAR FROM order_date) AS y, EXTRACT(MONTH FROM order_date) AS m, COUNT(*) AS n FROM orders "
                "GROUP BY 1, 2 ORDER BY y, m", True, "postgresql", "mysql"),
    ("if_pg", "label orders big or small", "SELECT id, IF(amount >= 10000, 'big', 'small') AS size FROM orders ORDER BY id", "dialect",
     "SELECT id, CASE WHEN amount >= 10000 THEN 'big' ELSE 'small' END AS size FROM orders ORDER BY id", True, "postgresql", "mysql"),
    ("locate_pg", "where the @ is in each email", "SELECT id, LOCATE('@', email) AS at FROM customers ORDER BY id", "dialect",
     "SELECT id, strpos(email, '@') AS at FROM customers ORDER BY id", True, "postgresql", "mysql"),
    ("instr_pg", "where the first dot is in each email", "SELECT id, INSTR(email, '.') AS dot FROM customers ORDER BY id", "dialect",
     "SELECT id, POSITION('.' IN email) AS dot FROM customers ORDER BY id", True, "postgresql", "mysql"),
    ("substring_index_pg", "the part of each email before the @", "SELECT id, SUBSTRING_INDEX(email, '@', 1) AS u FROM customers ORDER BY id",
     "dialect", "SELECT id, split_part(email, '@', 1) AS u FROM customers ORDER BY id", True, "postgresql", "mysql"),
    ("group_concat_pg", "each customer's order ids in one line",
     "SELECT customer_id, GROUP_CONCAT(id ORDER BY id SEPARATOR ',') AS ids FROM orders GROUP BY customer_id ORDER BY customer_id", "dialect",
     "SELECT customer_id, string_agg(id::text, ',' ORDER BY id) AS ids FROM orders GROUP BY customer_id ORDER BY customer_id", True, "postgresql", "mysql"),
    ("date_format_pg", "order dates as day/month/year", "SELECT id, DATE_FORMAT(order_date, '%d/%m/%Y') AS shown FROM orders ORDER BY id", "dialect",
     "SELECT id, to_char(order_date, 'DD/MM/YYYY') AS shown FROM orders ORDER BY id", True, "postgresql", "mysql"),
    ("date_format_month_pg", "orders per month as 'Jun 2024'",
     "SELECT DATE_FORMAT(order_date, '%b %Y') AS month, COUNT(*) AS n FROM orders GROUP BY DATE_FORMAT(order_date, '%b %Y') ORDER BY MIN(order_date)",
     "dialect", "SELECT to_char(order_date, 'Mon YYYY') AS month, COUNT(*) AS n FROM orders GROUP BY 1 ORDER BY MIN(order_date)", True,
     "postgresql", "mysql"),
    ("str_to_date_pg", "read 15-06-2024 as a date", "SELECT STR_TO_DATE('15-06-2024', '%d-%m-%Y') AS d", "dialect",
     "SELECT DATE '2024-06-15' AS d", False, "postgresql", "mysql"),
    ("ucase_pg", "first names in capitals", "SELECT id, UCASE(first_name) AS n FROM customers ORDER BY id", "dialect",
     "SELECT id, UPPER(first_name) AS n FROM customers ORDER BY id", True, "postgresql", "mysql"),
    ("dual_pg", "six times seven", "SELECT 6 * 7 AS answer FROM DUAL", "dialect", "SELECT 42 AS answer", False, "postgresql", "mysql"),
    ("convert_my_pg", "the whole-rupee amount", "SELECT id, CONVERT(amount, SIGNED) AS whole FROM orders ORDER BY id", "dialect",
     "SELECT id, CAST(amount AS BIGINT) AS whole FROM orders ORDER BY id", True, "postgresql", "mysql"),
    ("regexp_pg", "customers whose first name starts with a", "SELECT id FROM customers WHERE first_name REGEXP '^a' ORDER BY id", "dialect",
     "SELECT id FROM customers WHERE first_name ILIKE 'a%' ORDER BY id", True, "postgresql", "mysql"),
    ("div_pg", "each amount in whole thousands", "SELECT id, amount DIV 1000 AS thousands FROM orders ORDER BY id", "dialect",
     "SELECT id, TRUNC(amount / 1000) AS thousands FROM orders ORDER BY id", True, "postgresql", "mysql"),
    ("timestampdiff_pg", "whole days from order to payment",
     f"SELECT o.id, TIMESTAMPDIFF(DAY, o.order_date, p.paid_at) AS d {PAY} ORDER BY o.id, p.id", "dialect",
     f"SELECT o.id, p.paid_at - o.order_date AS d {PAY} ORDER BY o.id, p.id", True, "postgresql", "mysql"),
    ("nvl_pg", "discounts with 0 for none", "SELECT id, NVL(discount, 0) AS d FROM orders ORDER BY id", "null_fn",
     "SELECT id, COALESCE(discount, 0) AS d FROM orders ORDER BY id", True, "postgresql", "mysql"),
    ("cast_char_pg", "order dates as text", "SELECT id, CAST(order_date AS CHAR) AS d FROM orders ORDER BY id", "cast_char",
     "SELECT id, CAST(order_date AS VARCHAR) AS d FROM orders ORDER BY id", True, "postgresql", "mysql"),
    ("interval_week_pg", "two weeks after each order", "SELECT id, CAST(order_date + INTERVAL '2' WEEK AS DATE) AS d FROM orders ORDER BY id",
     "interval_unit", "SELECT id, order_date + 14 AS d FROM orders ORDER BY id", True, "postgresql", "mysql"),
    # ---- PostgreSQL words on MySQL / MariaDB (src = the wrong query's meaning on PostgreSQL)
    ("strpos_my", "where the @ is in each email", "SELECT id, STRPOS(email, '@') AS at FROM customers ORDER BY id", "dialect",
     "SELECT id, LOCATE('@', email) AS at FROM customers ORDER BY id", True, "mariadb", "postgres"),
    ("split_part_my", "the part of each email before the @", "SELECT id, SPLIT_PART(email, '@', 1) AS u FROM customers ORDER BY id", "dialect",
     "SELECT id, SUBSTRING_INDEX(email, '@', 1) AS u FROM customers ORDER BY id", True, "mysql", "postgres"),
    ("date_trunc_my", "orders per month",
     "SELECT CAST(DATE_TRUNC('month', order_date) AS DATE) AS month, COUNT(*) AS n FROM orders GROUP BY DATE_TRUNC('month', order_date) ORDER BY month",
     "dialect", "SELECT CAST(DATE_FORMAT(order_date, '%Y-%m-01') AS DATE) AS month, COUNT(*) AS n FROM orders GROUP BY 1 ORDER BY month", True,
     "mariadb", "postgres"),
    ("string_agg_my", "each customer's order ids in one line",
     "SELECT customer_id, string_agg(id::text, ',' ORDER BY id) AS ids FROM orders GROUP BY customer_id ORDER BY customer_id", "dialect",
     "SELECT customer_id, GROUP_CONCAT(id ORDER BY id) AS ids FROM orders GROUP BY customer_id ORDER BY customer_id", True, "mariadb", "postgres"),
    ("interval_text_my", "orders in the 30 days before 31 July",
     "SELECT id FROM orders WHERE order_date >= DATE '2024-07-31' - INTERVAL '30 days' ORDER BY id", "dialect",
     "SELECT id FROM orders WHERE order_date >= '2024-07-01' ORDER BY id", True, "mysql", "postgres"),
    ("cast_text_my", "the amount as text", "SELECT id, CAST(amount AS TEXT) AS t FROM orders ORDER BY id", "dialect",
     "SELECT id, CAST(amount AS CHAR) AS t FROM orders ORDER BY id", True, "mariadb", "postgres"),
    ("to_date_my", "read 15-06-2024 as a date", "SELECT TO_DATE('15-06-2024', 'DD-MM-YYYY') AS d", "dialect", "SELECT DATE '2024-06-15' AS d",
     False, "mariadb", "postgres"),
    ("regex_my", "customers whose email starts with d", "SELECT id FROM customers WHERE email ~* '^d' ORDER BY id", "dialect",
     "SELECT id FROM customers WHERE email LIKE 'd%' ORDER BY id", True, "mysql", "postgres"),
    ("date_minus_my", "days from order to each payment", f"SELECT o.id, CAST(p.paid_at AS DATE) - o.order_date AS days {PAY} ORDER BY o.id, p.id",
     "date_math", f"SELECT o.id, DATEDIFF(p.paid_at, o.order_date) AS days {PAY} ORDER BY o.id, p.id", True, "mariadb", "postgres"),
    ("date_plus_my", "a week after each order", "SELECT id, order_date + 7 AS due FROM orders ORDER BY id", "date_math",
     "SELECT id, DATE_ADD(order_date, INTERVAL 7 DAY) AS due FROM orders ORDER BY id", True, "mariadb", "postgres"),
    ("date_minus_where_my", "orders in the 30 days before 31 July",
     "SELECT id FROM orders WHERE order_date >= DATE '2024-07-31' - 30 ORDER BY id", "date_math",
     "SELECT id FROM orders WHERE order_date >= '2024-07-01' ORDER BY id", True, "mysql", "postgres"),
    ("power_my", "the square of each quantity", "SELECT id, quantity ^ 2 AS sq FROM order_items ORDER BY id", "power_xor",
     "SELECT id, quantity * quantity AS sq FROM order_items ORDER BY id", True, "mariadb", "postgres"),
]

# what each engine does with the wrong query: 'rows' (runs) or 'error' (refused)
LAB = {
    "top_mysql": ("error", "error"), "rownum_mysql": ("error", "error"), "dateadd_mysql": ("error", "error"),
    "datediff_ss_mysql": ("error", "error"),    # MariaDB 1582 (two arguments), PostgreSQL 42703 column "day"
    "len_mysql": ("error", "error"), "iif_mysql": ("error", "error"), "brackets_mysql": ("error", "error"),
    "convert_ss_pg": ("error", "error"), "sysdate_mysql": ("error", "error"),
    "date_add_pg": ("rows", "error"), "interval_where_pg": ("rows", "error"), "datediff_pg": ("rows", "error"), "year_month_pg": ("rows", "error"),
    "if_pg": ("rows", "error"), "locate_pg": ("rows", "error"), "substring_index_pg": ("rows", "error"), "group_concat_pg": ("rows", "error"),
    "date_format_pg": ("rows", "error"), "dual_pg": ("rows", "error"), "convert_my_pg": ("rows", "error"), "regexp_pg": ("rows", "error"),
    "div_pg": ("rows", "error"), "timestampdiff_pg": ("rows", "error"), "nvl_pg": ("rows", "error"),
    "strpos_my": ("error", "rows"), "split_part_my": ("error", "rows"), "date_trunc_my": ("error", "rows"), "string_agg_my": ("error", "rows"),
    "interval_text_my": ("error", "rows"), "cast_text_my": ("error", "rows"), "to_date_my": ("error", "rows"), "regex_my": ("error", "rows"),
    # run on both, different values (MEANING below has them)
    "cast_char_pg": ("rows", "rows"), "interval_week_pg": ("rows", "error"),   # inside CAST: 42601 at WEEK "date_minus_my": ("rows", "rows"), "date_plus_my": ("rows", "rows"),
    "date_minus_where_my": ("rows", "rows"), "power_my": ("rows", "rows"),
}
# the same text, different values: (sql, MariaDB's value, PostgreSQL's value)
MEANING = [
    ("SELECT DATE '2024-02-03' - DATE '2024-01-25'", "78", "9"),                        # numbers 20240203 - 20240125 vs days
    ("SELECT DATE '2024-03-01' - 1", "20240300", "2024-02-29"),
    ("SELECT DATE '2024-01-31' + INTERVAL '2' WEEK", "2024-02-14", "2024-01-31 00:00:02"),    # PostgreSQL: '2' seconds, column 'week'
    ("SELECT DATE '2024-01-31' + INTERVAL '2' QUARTER", "2024-07-31", "2024-01-31 00:00:02"),
    ("SELECT CAST(DATE '2024-06-01' AS CHAR)", "2024-06-01", "2"),
    ("SELECT 2 ^ 3", "1", "8"),
    ("SELECT GREATEST(1, NULL, 3)", "NULL", "3"),
    ("SELECT LENGTH('ñab')", "4", "3"),
    ("SELECT CHAR_LENGTH('ñab')", "3", "3"),
    ("SELECT CONCAT('a', NULL)", "NULL", "a"),
    ("SELECT 'a' = 'A'", "1", "0"),
    ("SELECT 7 / 2", "3.5", "3"),
    ("SELECT CAST('5.25' AS DECIMAL)", "5", "5.25"),
    ("SELECT 'ABC' REGEXP 'b'", "1", None),
    ("SELECT POSITION('b' IN 'abc')", "2", "2"),
    ("SELECT EXTRACT(YEAR FROM DATE '2024-05-06')", "2024", "2024"),
    ("SELECT DATE '2024-01-31' + INTERVAL '1' MONTH = DATE '2024-02-29'", "1", "1"),
]


def _engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


@pytest.fixture(scope="module")
def engines():
    return _engines()


def _only(said):
    return "postgres" if said == "postgresql" else "mysql"


def _rows(eng, sql, ordered):
    S.build(eng)
    return S.rows(eng, sql, ordered=ordered)


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)


@pytest.mark.parametrize("pid,words,wrong,topic,fix,ordered,said,src", P, ids=[p[0] for p in P])
def test_dbaba_finds_and_fixes(engines, pid, words, wrong, topic, fix, ordered, said, src):
    res = analyze_sql(f"{wrong.rstrip(';')};\n\n{words}", engine=said)
    found = [f for f in res["correct"]["findings"] if f["topic"] == topic]
    assert found, (pid, [(f["topic"], f["title_en"]) for f in res["correct"]["findings"]])
    assert all(f.get("basis") for f in found), (pid, "every finding names its fundamental")
    extra = [f["title_en"] for f in res["correct"]["findings"] if f["kind"] in ("error", "wrong") and f["topic"] not in sqlport.PORT_TOPICS]
    assert not extra, (pid, extra)
    offered = res["correct"].get("corrected")
    assert offered, (pid, "no corrected / suggested query")
    again = sqlcorrect.check(offered, engine=said)["findings"]
    assert not [f for f in again if f["kind"] in ("error", "wrong")], (pid, offered, again)
    eng = engines[_only(said)]
    assert _rows(eng, offered, ordered) == _rows(eng, fix, ordered), (pid, offered)
    if src:                                     # a translation keeps the meaning: the same rows the original gave where it came from
        assert _rows(engines[src], wrong, ordered) == _rows(eng, offered, ordered), (pid, "meaning changed", offered)


def test_the_lab_table(engines):
    by = {p[0]: p for p in P}
    for pid, (want_m, want_p) in LAB.items():
        wrong = by[pid][2]
        for name, want in (("mysql", want_m), ("postgres", want_p)):
            eng = engines[name]
            S.build(eng)
            try:
                S.rows(eng, wrong)
                got = "rows"
            except Exception:  # noqa: BLE001
                eng.rollback()
                got = "error"
            assert got == want, (pid, name, got)


def test_same_text_different_values(engines):
    for sql, want_m, want_p in MEANING:
        for name, want in (("mysql", want_m), ("postgres", want_p)):
            eng = engines[name]
            if want is None:
                with pytest.raises(Exception):  # noqa: B017 - PostgreSQL has no REGEXP operator
                    eng.run(sql)
                eng.rollback()
                continue
            got = S.norm(eng.run(sql)[0][0])
            assert got == want or (want.count("-") == 2 and got.startswith(want)), (sql, name, got, want)


def test_sqlite_notes_are_lab_checked():
    """SQLite stays notes-only, but every note with a lab line was run on SQLite itself (Python's sqlite3)."""
    from app.dbknow import engines as E

    d = E.dialect("sqlite")
    assert d["notes_only"] and d["lab"]
    for item in d["lab"]:
        assert item["note"] in d["notes"], item
        if tuple(item.get("since", (0,))) > sqlite3.sqlite_version_info:
            continue                                   # the note itself says from which SQLite version it holds
        conn = sqlite3.connect(":memory:")
        try:
            rows = None
            for stmt in [x for x in item["sql"].split(";") if x.strip()]:
                cur = conn.execute(stmt)
                if cur.description:
                    rows = [list(r) for r in cur.fetchall()]
            assert rows == item["result"], (item, rows)
        finally:
            conn.close()


def test_valid_queries_in_the_right_words_get_no_port_findings():
    """No false alarms: each database's own words, and the words every engine shares, are left alone."""
    for engine, sql in [
        ("mariadb", "SELECT id, DATE_ADD(order_date, INTERVAL 7 DAY), DATEDIFF(NOW(), order_date), IFNULL(discount, 0) FROM orders LIMIT 5"),
        ("mysql", "SELECT customer_id, GROUP_CONCAT(id ORDER BY id SEPARATOR ';') FROM orders GROUP BY customer_id"),
        ("mysql", "SELECT id, LOCATE('@', email), SUBSTRING_INDEX(email, '@', 1), DATE_FORMAT(created_at, '%Y-%m') FROM customers"),
        ("mysql", "SELECT id FROM orders WHERE order_date >= CURRENT_DATE - INTERVAL 30 DAY"),
        ("mysql", "SELECT CAST(amount AS SIGNED), CAST(id AS CHAR), CONVERT(amount, DECIMAL(10,2)) FROM orders"),
        ("postgresql", "SELECT id, order_date + INTERVAL '7 days', CURRENT_DATE - order_date, COALESCE(discount, 0) FROM orders LIMIT 5"),
        ("postgresql", "SELECT customer_id, string_agg(id::text, ',' ORDER BY id) FROM orders GROUP BY customer_id"),
        ("postgresql", "SELECT id, strpos(email, '@'), split_part(email, '@', 2), to_char(created_at, 'YYYY-MM') FROM customers"),
        ("postgresql", "SELECT CAST(amount AS INTEGER), CAST(id AS VARCHAR), CAST(id AS CHAR(5)), 2 ^ 3 FROM orders"),
        ("postgresql", "SELECT EXTRACT(YEAR FROM order_date), DATE_TRUNC('month', order_date) FROM orders"),
        ("postgresql", "SELECT id FROM orders WHERE order_date + INTERVAL '2' DAY > CURRENT_DATE"),
        (None, "SELECT id, POSITION('a' IN email), CHAR_LENGTH(email), CASE WHEN id > 2 THEN 1 ELSE 0 END FROM customers"),
        (None, "SELECT price - discount, id - 1, stock + 5, amount * 2 FROM products"),
        (None, "SELECT city, COUNT(*) FROM customers GROUP BY 1"),
        (None, "SELECT UPPER(city) AS c, COUNT(*) FROM customers GROUP BY c"),
    ]:
        fs = sqlcorrect.check(sql, engine=engine)["findings"]
        bad = [(f["topic"], f["title_en"]) for f in fs if f["kind"] in ("error", "wrong")]
        assert not bad, (engine, sql, bad)


def test_unknown_database_date_math_is_a_check_with_both_meanings():
    fs = sqlcorrect.check("SELECT id, shipped_on - order_date AS days FROM orders")["findings"]
    f = next(f for f in fs if f["topic"] == "date_math")
    assert f["kind"] == "check" and "PostgreSQL" in f["fix_en"] and "MySQL/MariaDB" in f["fix_en"]
    fs = sqlcorrect.check("SELECT id, shipped_on - order_date AS days FROM orders", engine="postgresql")["findings"]
    assert not [f for f in fs if f["topic"] == "date_math"]


# ---------------------------------------------------------------- translation: the whole query in another database's words
TRANSLATIONS = [   # (query, source engine, target, its rows must equal the source's)
    ("SELECT `id`, IFNULL(discount, 0) AS d, DATE_FORMAT(order_date, '%Y-%m') AS m FROM orders WHERE amount > 400 ORDER BY id LIMIT 2, 3",
     "mysql", "postgresql"),
    ("SELECT customer_id, GROUP_CONCAT(id ORDER BY id SEPARATOR ';') AS ids, DATEDIFF(MAX(order_date), MIN(order_date)) AS span "
     "FROM orders GROUP BY customer_id ORDER BY customer_id", "mysql", "postgresql"),
    ("SELECT id, IF(stock = 0, 'out', 'in') AS state, UCASE(name) AS n FROM products WHERE YEAR(CURRENT_DATE) > 2000 ORDER BY id",
     "mysql", "postgresql"),
    ("SELECT id, email FROM customers WHERE created_at >= DATE '2024-04-01' - INTERVAL 30 DAY ORDER BY id", "mysql", "postgresql"),
    ("SELECT id, split_part(email, '@', 1) AS u, strpos(email, '.') AS dot, amount_text FROM (SELECT c.id, c.email, "
     "CAST(c.id * 100 AS TEXT) AS amount_text FROM customers c) t ORDER BY id", "postgres", "mariadb"),
    ("SELECT customer_id, string_agg(id::text, ',' ORDER BY id) AS ids FROM orders WHERE order_date >= DATE '2024-07-31' - INTERVAL '30 days' "
     "GROUP BY customer_id ORDER BY customer_id", "postgres", "mysql"),
    ("SELECT id, order_date + 7 AS next_week, CAST(DATE_TRUNC('month', order_date) AS DATE) AS month FROM orders ORDER BY id", "postgres", "mariadb"),
]


@pytest.mark.parametrize("sql,src,target", TRANSLATIONS)
def test_translate_keeps_the_rows(engines, sql, src, target):
    t = sqlport.translate(sql, target)
    assert t["changed"] and t["changes"] and not t["left"], t
    dst = "postgres" if target == "postgresql" else "mysql"
    assert _rows(engines[src], sql, True) == _rows(engines[dst], t["sql"], True), t["sql"]


def test_translate_names_the_meaning_it_cant_remove():
    t = sqlport.translate("SELECT id FROM customers WHERE city = 'pune' AND amount / qty > 2", "postgresql", "mysql")
    text = sqlport.translation_text("", t, "en")
    assert "ignores case" in text and "drops the fraction" in text and "already runs" in text
    t = sqlport.translate("SELECT CONCAT(first_name, ' ', middle_name) FROM customers ORDER BY middle_name", "mysql", "postgresql")
    text = sqlport.translation_text("", t, "en")
    assert "CONCAT with a NULL part" in text and "NULLs come first" in text


def test_translate_to_a_notes_only_database_says_so():
    t = sqlport.translate("SELECT id, IFNULL(discount, 0) FROM orders ORDER BY id LIMIT 5", "sqlserver", "mysql")
    assert t["notes_only"] and t["sql"] is None
    text = sqlport.translation_text("", t, "en")
    assert "no lab-tested SQL Server rules" in text.replace("don't have lab-tested", "no lab-tested") and "TOP" in text and "ISNULL" in text
    assert "FULL OUTER JOIN" not in text                       # only the notes that speak to this query
    t = sqlport.translate("SELECT id FROM orders ORDER BY id LIMIT 5", "oracle", "mysql")
    assert "FETCH FIRST" in sqlport.translation_text("", t, "en")


def test_request_and_target_are_read_from_the_words():
    assert sqlport.target_of("convert this mysql query to postgres: SELECT 1 FROM t") == ("postgresql", "mysql")
    assert sqlport.target_of("ye query postgres mein badlo: SELECT IF(a, 1, 0) FROM t")[0] == "postgresql"
    assert sqlport.target_of("इस query को PostgreSQL में बदलो: SELECT 1 FROM t")[0] == "postgresql"
    assert sqlport.target_of("SELECT TOP 5 id FROM t;\n\nconvert to mariadb")[0] == "mariadb"
    assert sqlport.is_request("convert from sql server to mysql: SELECT TOP 5 id FROM orders")
    assert sqlport.target_of("convert from sql server to mysql: SELECT TOP 5 id FROM orders") == ("mysql", "sqlserver")
    assert not sqlport.is_request("SELECT * FROM postgres_logs WHERE id = 1")
    assert not sqlport.is_request("SELECT id FROM orders LIMIT 5")
    assert not sqlport.is_request("this mysql query is slow: SELECT id FROM orders WHERE YEAR(order_date) = 2024")


def test_format_codes_round_trip():
    assert sqlport.convert_format("'%d/%m/%Y %T'", "mysql", "postgresql") == "'DD/MM/YYYY HH24:MI:SS'"
    for my, pg in [("'%Y-%m-%d'", "'YYYY-MM-DD'"), ("'%d/%m/%Y %H:%i'", "'DD/MM/YYYY HH24:MI'"), ("'%b %Y'", "'Mon YYYY'"),
                   ("'%M %e, %Y'", "'FMMonth FMDD, YYYY'")]:
        assert sqlport.convert_format(my, "mysql", "postgresql") == pg
        assert sqlport.convert_format(pg, "postgresql", "mysql") == my
    assert sqlport.convert_format("'%Y %U'", "mysql", "postgresql") is None      # week number: no exact twin, not guessed
    assert sqlport.convert_format("'YYYY Q'", "postgresql", "mysql") is None


# ---------------------------------------------------------------- the final exam of nights 1-10
def test_final_exam_50_generated_problems(engines):
    """50 problems drawn by the statement generator from every night's families (one seed run, so the same exam each time):
    each mistake found under its own topic, nothing else called wrong, and the fix doing on MariaDB and PostgreSQL exactly
    what was meant."""
    from tests.fuzz import stmtbugs

    eng = {"mysql": engines["mysql"], "postgres": engines["postgres"]}
    picked, by_night, fails = [], Counter(), []
    nights = {"value": 0, "basic": 1, "join": 2, "agg": 3, "sub": 4, "ch": 5, "sd": 6, "wf": 7, "ix": 8, "lk": 9, "pt": 10}
    first = {"unquoted_date", "unquoted_word", "scalar_subquery", "set_and", "or_value", "between_reversed", "agg_in_where",
             "eq_null_dml", "same_table", "per_group"}

    def night(bug):
        p = bug.split("_")[0]
        if p in nights:
            return nights[p]
        if bug in first:
            return 0
        if bug in stmtbugs.AGG_BUGS:
            return 3
        if bug in stmtbugs.SUB_BUGS:
            return 4
        k = stmtbugs.BUGS.index(bug)
        return 1 if k < stmtbugs.BUGS.index("comma_cartesian") else 2

    seed = 777000
    while len(picked) < 50 and seed < 780000:
        seed += 1
        item = stmtbugs.make(seed)
        if item is None or by_night[night(item["bug"])] >= 5:
            continue
        by_night[night(item["bug"])] += 1
        picked.append(item)
    assert len(picked) == 50 and len(by_night) >= 10, by_night
    for item in picked:
        for e in eng.values():
            e.create(item["schema"])
        try:
            fails += stmtbugs.check(item, eng)
        finally:
            for e in eng.values():
                e.drop(item["schema"]["order"])
    assert not fails, fails[:5]


def test_tonights_fundamental_is_in_the_core():
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    f = funds["portability"]
    assert {"date_math", "interval_unit", "cast_char", "power_xor"} <= set(f["topics"]) and set(f["proven"]) >= {"mariadb", "postgresql"}
    from app.dbknow import engines as E

    for fam in ("mysql", "postgresql"):
        port = E.dialect(fam)["port"]
        assert port["calls"] and port["interval"] and port["date_codes"] and port["cast_types"]
        assert all(c["proven"] for c in port["calls"]), fam
