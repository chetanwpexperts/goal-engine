"""Night 8 practice bank: indexes, EXPLAIN and speed rewrites that return exactly the same rows.

Each problem: what the person wants (in words), the query they wrote (it returns the right rows, slowly), the topic DBaba must
find, a verified answer (run on MariaDB and PostgreSQL, both agreeing), whether the row order is part of the answer, and the
database said (None = not said). DBaba's faster form (applied, or offered as 'try this') must return the verified rows on both.
fix=None: finding it is enough (keyset paging needs the last row's values from the app; it is proven separately below).

The LAB below is what each engine really does on 20,000 rows (MariaDB 10.11 and PostgreSQL 16, ANALYZEd): the slow form reads
the whole table or index, the faster form searches the index. It is re-checked live: build_big() makes the table on each run.
"""

import datetime as dt
import random
import re

import pytest

from app.agents import sqlexplain, sqlspeed
from app.agents.sql import analyze_sql
from tests.practice import schema as S

JOIN = "FROM orders o JOIN customers c ON c.id = o.customer_id"

# (id, words, slow query, topic, verified answer or None, keep row order?, run only on, database said)
P = [
    # ---- a function around the column: the same rows said on the bare column
    ("sg_year", "orders placed in 2024", "SELECT id FROM orders WHERE YEAR(order_date) = 2024", "sargable",
     "SELECT id FROM orders WHERE order_date BETWEEN '2024-01-01' AND '2024-12-31'", False, None, None),
    ("sg_year_hinglish", "2024 ke saare orders chahiye", "SELECT id, amount FROM orders WHERE YEAR(order_date) = 2024 AND status = 'paid'",
     "sargable", "SELECT id, amount FROM orders WHERE status = 'paid'", False, None, None),
    ("sg_extract_month", "orders of July 2024",
     "SELECT id FROM orders WHERE EXTRACT(YEAR FROM order_date) = 2024 AND EXTRACT(MONTH FROM order_date) = 7", "sargable",
     "SELECT id FROM orders WHERE id IN (4, 5, 6, 7)", False, None, None),
    ("sg_year_month", "orders of June 2024", "SELECT id FROM orders WHERE YEAR(order_date) = 2024 AND MONTH(order_date) = 6",
     "sargable", "SELECT id FROM orders WHERE id IN (1, 2, 3)", False, None, "mariadb"),
    ("sg_month_year", "employees hired in January 2021",
     "SELECT id FROM employees WHERE MONTH(hired_on) = 1 AND YEAR(hired_on) = 2021", "sargable",
     "SELECT id FROM employees WHERE id = 3", False, None, "mysql"),
    ("sg_date_day", "payments made on 28 June 2024", "SELECT id FROM payments WHERE DATE(paid_at) = '2024-06-28'", "sargable",
     "SELECT id FROM payments WHERE id = 3", False, None, None),
    ("sg_cast_between", "orders in June 2024", "SELECT id FROM orders WHERE CAST(order_date AS DATE) BETWEEN '2024-06-01' AND '2024-06-30'",
     "sargable", "SELECT id FROM orders WHERE id IN (1, 2, 3)", False, None, None),
    ("sg_pg_cast", "orders of 10 July 2024 (postgres)", "SELECT id FROM orders WHERE order_date::date = '2024-07-10'", "sargable",
     "SELECT id FROM orders WHERE id = 5", False, None, "postgresql"),
    ("sg_pg_cast_ge", "returns from 20 June 2024 on, in postgres", "SELECT id FROM returns WHERE returned_on::date >= '2024-06-20'",
     "sargable", "SELECT id FROM returns WHERE id IN (1, 2)", False, None, "postgresql"),
    ("sg_date_format", "employees who joined in January 2021 (mysql)",
     "SELECT id FROM employees WHERE DATE_FORMAT(hired_on, '%Y-%m') = '2021-01'", "sargable",
     "SELECT id FROM employees WHERE id = 3", False, None, "mysql"),
    ("sg_to_char", "orders of June 2024, on postgres", "SELECT id FROM orders WHERE TO_CHAR(order_date, 'YYYY-MM') = '2024-06'",
     "sargable", "SELECT id FROM orders WHERE id IN (1, 2, 3)", False, None, "postgresql"),
    ("sg_to_char_day", "orders from 10 July 2024 onwards (postgres)",
     "SELECT id FROM orders WHERE TO_CHAR(order_date, 'YYYY-MM-DD') >= '2024-07-10'", "sargable",
     "SELECT id FROM orders WHERE id IN (5, 6, 7, 8)", False, None, "postgresql"),
    ("sg_year_between", "staff hired from 2019 to 2021", "SELECT id FROM employees WHERE YEAR(hired_on) BETWEEN 2019 AND 2021", "sargable",
     "SELECT id FROM employees WHERE id IN (1, 2, 3, 4)", False, None, None),
    ("sg_year_gt", "customers who signed up after 2023", "SELECT id FROM customers WHERE EXTRACT(YEAR FROM created_at) > 2023",
     "sargable", "SELECT id FROM customers", False, None, None),
    ("sg_year_le", "staff hired up to the end of 2020", "SELECT id FROM employees WHERE YEAR(hired_on) <= 2020", "sargable",
     "SELECT id FROM employees WHERE id IN (1, 2, 6)", False, None, None),
    ("sg_today", "today's orders", "SELECT id FROM orders WHERE DATE(order_date) = CURRENT_DATE", "sargable",
     "SELECT id FROM orders WHERE order_date > '2030-01-01'", False, None, None),
    ("sg_curdate", "orders placed today (mysql)", "SELECT id FROM orders WHERE DATE(order_date) = CURDATE()", "sargable",
     "SELECT id FROM orders WHERE order_date > '2030-01-01'", False, None, "mysql"),
    ("sg_sub", "orders that got a payment in July 2024",
     "SELECT id FROM orders WHERE id IN (SELECT order_id FROM payments WHERE EXTRACT(YEAR FROM paid_at) = 2024 AND EXTRACT(MONTH FROM paid_at) = 7)",
     "sargable", "SELECT id FROM orders WHERE id IN (5, 7)", False, None, None),
    # ---- arithmetic on the column: move the number across
    ("sg_plus", "products that would have 17 in stock after 5 more arrive", "SELECT id FROM products WHERE stock + 5 = 17", "sargable",
     "SELECT id FROM products WHERE id = 1", False, None, None),
    ("sg_minus", "orders above 10000 after a 500 discount", "SELECT id FROM orders WHERE amount - 500 > 10000", "sargable",
     "SELECT id FROM orders WHERE id IN (1, 3, 4, 6)", False, None, None),
    ("sg_times", "products whose double price is at least 50000", "SELECT id FROM products WHERE price * 2 >= 50000", "sargable",
     "SELECT id FROM products WHERE id IN (1, 3, 6)", False, None, None),
    ("sg_lead", "order lines with a single unit", "SELECT id FROM order_items WHERE 1 + quantity = 2", "sargable",
     "SELECT id FROM order_items WHERE id <> 10", False, None, None),
    # ---- COALESCE / IFNULL: the rows the default stands for are the NULL rows
    ("sg_coalesce_zero", "orders with no discount", "SELECT id FROM orders WHERE COALESCE(discount, 0) = 0", "sargable",
     "SELECT id FROM orders WHERE id IN (1, 4, 5, 7, 8)", False, None, None),
    ("sg_coalesce_gt", "staff earning more than 1000 commission", "SELECT id FROM employees WHERE COALESCE(commission, 0) > 1000",
     "sargable", "SELECT id FROM employees WHERE id IN (2, 3)", False, None, None),
    ("sg_ifnull", "orders whose shipping is 100 (mysql)", "SELECT id FROM orders WHERE IFNULL(shipping, 0) = 100", "sargable",
     "SELECT id FROM orders WHERE id IN (1, 4)", False, None, "mysql"),
    ("sg_coalesce_text", "suppliers with no email", "SELECT id FROM suppliers WHERE COALESCE(email, 'none') = 'none'", "sargable",
     "SELECT id FROM suppliers WHERE id = 2", False, None, None),
    # ---- the first characters: a LIKE prefix
    ("sg_left", "products whose code starts with PH", "SELECT id FROM products WHERE LEFT(sku, 2) = 'PH'", "sargable",
     "SELECT id FROM products WHERE id IN (1, 2)", False, None, None),
    ("sg_substring", "customers whose email starts with diya", "SELECT id FROM customers WHERE SUBSTRING(email, 1, 4) = 'diya'",
     "sargable", "SELECT id FROM customers WHERE id IN (2, 7)", False, None, None),
    ("sg_substr_from", "laptops by code (LP2...)", "SELECT id FROM products WHERE SUBSTRING(sku FROM 1 FOR 3) = 'LP2'", "sargable",
     "SELECT id FROM products WHERE id = 6", False, None, None),
    # ---- OR across two tables of a join: UNION ALL, each part with its own index
    ("ou_city_status", "orders from Pune customers, and every pending order",
     f"SELECT o.id {JOIN} WHERE c.city = 'Pune' OR o.status = 'pending'", "or_union",
     "SELECT id FROM orders WHERE customer_id IN (1, 5) OR status = 'pending'", False, None, None),
    ("ou_and", "orders above 500 that are from Delhi or shipped",
     f"SELECT o.id, o.amount {JOIN} WHERE o.amount > 500 AND (c.city = 'Delhi' OR o.status = 'shipped')", "or_union",
     "SELECT id, amount FROM orders WHERE id = 3", False, None, None),
    ("ou_order_limit", "the first 3 orders that are paid or from Mumbai, by id",
     f"SELECT o.id, c.city {JOIN} WHERE o.status = 'paid' OR c.city = 'Mumbai' ORDER BY id LIMIT 3", "or_union",
     "SELECT o.id, c.city FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.id IN (1, 3, 4) ORDER BY o.id", True, None, None),
    ("ou_products", "books and every product above 50000",
     "SELECT p.id FROM products p JOIN categories c ON c.id = p.category_id WHERE c.name = 'Books' OR p.price > 50000", "or_union",
     "SELECT id FROM products WHERE id IN (3, 4, 6)", False, None, None),
    ("ou_three", "Delhi staff, high earners and big commissions",
     "SELECT e.id FROM employees e JOIN departments d ON d.id = e.department_id WHERE d.location = 'Delhi' OR e.salary > 70000 OR e.commission > 2000",
     "or_union", "SELECT id FROM employees WHERE id IN (1, 2, 3, 4, 5, 6)", False, None, None),
    ("ou_left", "customers from Chennai, or customers with a cancelled order (one row per order)",
     "SELECT c.id, o.id AS order_id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE c.city = 'Chennai' OR o.status = 'cancelled'",
     "or_union", "SELECT 6, 6 UNION ALL SELECT 3, 4", False, None, None),
    # ---- deep OFFSET: keyset paging (proven with the last row's values, below)
    ("ks_id", "page 101 of the product list, 20 a page", "SELECT id, name FROM products ORDER BY id LIMIT 20 OFFSET 2000", "keyset",
     None, True, None, None),
    ("ks_date_id", "older orders, 50 a page, page 101", "SELECT id, order_date FROM orders ORDER BY order_date DESC, id DESC LIMIT 50 OFFSET 5000",
     "keyset", None, True, None, None),
    ("ks_mysql", "customers 10001 to 10025 (mysql)", "SELECT id, email FROM customers ORDER BY id LIMIT 10000, 25", "keyset", None, True, None,
     "mysql"),
    ("ks_no_key", "orders by amount, a deep page", "SELECT id, amount FROM orders ORDER BY amount LIMIT 10 OFFSET 3000", "keyset", None, True,
     None, None),
]


def _engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


@pytest.fixture(scope="module")
def engines():
    return _engines()


def _rows(eng, sql, ordered):
    S.build(eng)
    return S.rows(eng, sql, ordered=ordered)


def _runs_on(sql: str, name: str) -> bool:
    """YEAR() / MONTH() / DATE_FORMAT() / IFNULL() / CURDATE() are MySQL's words, ::date and TO_CHAR() PostgreSQL's."""
    if name == "postgres":
        return not re.search(r"\b(year|month|date_format|ifnull|curdate)\s*\(", sql, re.I)
    return not re.search(r"::|\bto_char\s*\(", sql, re.I)


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)


@pytest.mark.parametrize("pid", [p[0] for p in P if p[4]])
def test_the_verified_answer_is_the_same_on_both(engines, pid):
    p = next(x for x in P if x[0] == pid)
    assert _rows(engines["mysql"], p[4], p[5]) == _rows(engines["postgres"], p[4], p[5]), pid


@pytest.mark.parametrize("pid,words,slow,topic,fix,ordered,only,said", P, ids=[p[0] for p in P])
def test_dbaba_finds_and_gives_the_faster_form(engines, pid, words, slow, topic, fix, ordered, only, said):
    res = analyze_sql(f"{slow};\n\n{words}", engine=said)
    found = [f for f in res["correct"]["findings"] if f["topic"] == topic]
    assert found, (pid, [(f["topic"], f["title_en"]) for f in res["correct"]["findings"]])
    assert all(f.get("basis", {}).get("id") == "index_use" for f in found), (pid, "every finding names its fundamental")
    assert all(f["kind"] == "check" for f in found), (pid, "speed is never an 'error' or 'wrong'")
    if fix is None:
        return
    offered = res["correct"].get("corrected")
    assert offered, (pid, "no faster form")
    shown = res.get("rewritten") if topic == "sargable" else res.get("suggested")
    assert offered == shown, (pid, "shown on the card", res.get("rewritten"), res.get("suggested"))
    if topic != "sargable":
        assert res.get("suggested_kind") == "speed", pid
    for name, eng in engines.items():
        if only and name not in only:
            continue
        assert _rows(eng, offered, ordered) == _rows(eng, fix, ordered), (pid, name, offered)
        if topic == "sargable" and _runs_on(slow, name):          # and the slow form gave those rows too, where it runs
            assert _rows(eng, slow, ordered) == _rows(eng, fix, ordered), (pid, name, "the slow form")


def test_valid_queries_stay_quiet():
    """Conditions already on the bare column, functions outside WHERE, OR on one table: no speed finding, nothing rewritten."""
    for q in [
        "SELECT id FROM orders WHERE order_date >= '2024-01-01' AND order_date < '2025-01-01'",
        "SELECT YEAR(order_date) AS y, COUNT(*) FROM orders GROUP BY YEAR(order_date)",
        "SELECT id, CASE WHEN YEAR(order_date) = 2024 THEN 'this' ELSE 'old' END FROM orders",
        "SELECT id FROM orders WHERE amount + shipping > 1000",
        "SELECT id FROM orders WHERE COALESCE(discount, shipping) = 50",
        "SELECT id FROM customers WHERE city = 'Pune' OR city = 'Delhi'",
        "SELECT id FROM orders WHERE customer_id = 5 OR status = 'paid'",
        "SELECT o.id FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune' OR c.city = 'Delhi'",
        "SELECT id FROM products ORDER BY id LIMIT 20 OFFSET 40",
        "SELECT id FROM products WHERE id > 2000 ORDER BY id LIMIT 20",
        "SELECT id FROM products WHERE LEFT(sku, 3) = 'LP_'",
        "SELECT id FROM orders WHERE NOT COALESCE(discount, 0) = 50",
        "SELECT id FROM orders WHERE DATE(order_date) <> '2024-06-01'",
    ]:
        res = analyze_sql(q)
        bad = [(f["topic"], f["title_en"]) for f in res["correct"]["findings"] if f["topic"] in ("sargable", "or_union", "keyset")]
        assert not bad, (q, bad)


def test_not_coalesce_keeps_its_rows(engines):
    """NOT (COALESCE(d, 0) = 50) is true for a NULL discount; NOT (d = 50) is unknown: that rewrite would lose rows, so it isn't
    offered. The form that holds the NULL rows (= the default) is safe even under NOT."""
    q = "SELECT id FROM orders WHERE NOT COALESCE(discount, 0) = 0"
    res = analyze_sql(q)
    fixed = res["correct"].get("corrected")
    assert fixed and "IS NULL" in fixed, fixed
    for eng in engines.values():
        assert _rows(eng, fixed, False) == _rows(eng, q, False)


@pytest.mark.parametrize("said,word", [("postgresql", "CREATE INDEX"), ("mariadb", "ADD INDEX"), (None, "ADD INDEX")])
def test_index_commands_are_in_the_databases_words(engines, said, word):
    """Rule A: ALTER TABLE ... ADD INDEX is a syntax error on PostgreSQL (lab: 42601); it gets CREATE INDEX and pg_indexes."""
    res = analyze_sql("SELECT id FROM orders WHERE status = 'paid' AND customer_id = 3", engine=said)
    assert res["indexes"] and all(word in i for i in res["indexes"]), res["indexes"]
    assert ("pg_indexes" in res["check_query"]) == (said == "postgresql")
    eng = engines["postgres" if said == "postgresql" else "mysql"]
    S.build(eng)
    for stmt in res["indexes"]:
        eng.run(stmt)                                  # it runs there
    eng.run(res["check_query"].replace("current_schema()", "'pg_temp'") if said == "postgresql" else res["check_query"])
    if said == "postgresql":
        with pytest.raises(Exception):
            eng.run("ALTER TABLE orders ADD INDEX ix_x (status)")
        eng.conn.rollback()
    S.build(eng)


# ------------------------------------------------------------------------------------------------ the big lab table
N = 20000


def big_rows():
    rnd = random.Random(8)
    out = []
    for i in range(1, N + 1):
        d = dt.date(2022, 1, 1) + dt.timedelta(days=rnd.randint(0, 1000))
        out.append((i, rnd.randint(1, 2000), rnd.choice(["paid", "shipped", "pending", "cancelled"]), d,
                    dt.datetime(d.year, d.month, d.day, rnd.randint(0, 23), rnd.randint(0, 59)), rnd.randint(100, 100000),
                    None if rnd.random() < 0.3 else rnd.choice([0, 50, 100, 500]), f"user{i}@mail.test", f"98{rnd.randint(10000000, 99999999)}",
                    rnd.randint(1, 20)))
    return out


def build_big(eng):
    my = eng.kind == "mysql"
    for t in ("big_orders", "big_customers"):
        eng.run(f"DROP TEMPORARY TABLE IF EXISTS {t}" if my else f"DROP TABLE IF EXISTS pg_temp.{t}")
    eng.run(f"CREATE TEMPORARY TABLE big_orders (id INT PRIMARY KEY, customer_id INT, status VARCHAR(20), order_date DATE, "
            f"created_at {'DATETIME' if my else 'TIMESTAMP'}, amount DECIMAL(10,2), discount DECIMAL(10,2), email VARCHAR(80), "
            f"phone VARCHAR(20), qty INT)")
    eng.run("INSERT INTO big_orders VALUES (" + ",".join(["%s"] * 10) + ")", many=big_rows())
    eng.run("CREATE TEMPORARY TABLE big_customers (id INT PRIMARY KEY, email VARCHAR(80), city VARCHAR(20))")
    eng.run("INSERT INTO big_customers VALUES (%s, %s, %s)", many=[(i, f"c{i}@mail.test", ["Pune", "Delhi", "Goa"][i % 3]) for i in range(1, 2001)])
    for ix in ("customer_id", "order_date", "created_at", "qty", "discount", "email", "phone", "amount"):
        eng.run(f"CREATE INDEX bx_{ix} ON big_orders ({ix})")
    eng.run("CREATE INDEX bxc_email ON big_customers (email)")
    eng.run("ANALYZE TABLE big_orders, big_customers" if my else "ANALYZE big_orders")
    if not my:
        eng.run("ANALYZE big_customers")              # autovacuum never analyzes a temporary table


@pytest.fixture(scope="module")
def big(engines):
    for eng in engines.values():
        build_big(eng)
    yield engines
    for eng in engines.values():
        my = eng.kind == "mysql"
        for t in ("big_orders", "big_customers"):
            eng.run(f"DROP TEMPORARY TABLE IF EXISTS {t}" if my else f"DROP TABLE IF EXISTS pg_temp.{t}")


def _plan(eng, q) -> str:
    if eng.kind == "mysql":
        return " | ".join(f"{r[2]}:{r[3]}:{r[9]}" for r in eng.run("EXPLAIN " + q))
    return " | ".join(r[0] for r in eng.run("EXPLAIN " + q))


def _full(eng, plan: str) -> bool:
    """The plan reads the whole table or the whole index."""
    if eng.kind == "mysql":
        return bool(re.search(r":(ALL|index):", plan))
    return "Seq Scan" in plan or bool(re.search(r"Bitmap Index Scan[^|]*\(cost=0\.00\.\.[2-9]\d\d", plan))


# (slow form, the faster form DBaba gives): what the lab saw on 20,000 rows. Both return the same rows (checked too).
LAB = [
    ("SELECT id, qty FROM big_orders WHERE YEAR(order_date) = 2023 AND MONTH(order_date) = 5", "mysql"),     # MariaDB: index/ALL vs range
    ("SELECT id, qty FROM big_orders WHERE EXTRACT(YEAR FROM order_date) = 2023 AND EXTRACT(MONTH FROM order_date) = 5", None),  # PG: Seq Scan
    ("SELECT id, qty FROM big_orders WHERE DATE(created_at) = '2023-05-05'", None),
    ("SELECT id, qty FROM big_orders WHERE qty + 1 = 5", None),
    ("SELECT id, qty FROM big_orders WHERE COALESCE(customer_id, 0) = 7", None),
    ("SELECT id, qty FROM big_orders WHERE LEFT(email, 9) = 'user1234@'", "mysql"),   # PostgreSQL's prefix LIKE needs the C collation
]


@pytest.mark.parametrize("slow,only", LAB)
def test_the_lab_slow_form_reads_everything_and_the_fast_form_searches(big, slow, only):
    fast = analyze_sql(slow)["correct"]["corrected"]
    assert fast and fast != slow, slow
    for name, eng in big.items():
        if only and name != only:
            continue
        assert _full(eng, _plan(eng, slow)), (name, slow, _plan(eng, slow))
        assert not _full(eng, _plan(eng, fast)), (name, fast, _plan(eng, fast))
        assert sorted(eng.run(slow)) == sorted(eng.run(fast)), (name, slow)


def test_the_lab_or_across_two_tables(big):
    slow = ("SELECT o.id FROM big_orders o JOIN big_customers c ON c.id = o.customer_id "
            "WHERE c.email = 'c5@mail.test' OR o.phone = '9812345678'")
    res = analyze_sql(slow)
    fast = res["suggested"]
    assert res["suggested_kind"] == "speed" and "UNION ALL" in fast
    for name, eng in big.items():
        assert _full(eng, _plan(eng, slow)), (name, _plan(eng, slow))
        assert not _full(eng, _plan(eng, fast)), (name, _plan(eng, fast))
        assert sorted(eng.run(slow)) == sorted(eng.run(fast))


def test_the_lab_or_on_one_table_is_already_fast(big):
    """MariaDB index_merge / PostgreSQL BitmapOr: nothing to rewrite, and DBaba says so instead of a UNION ALL."""
    q = "SELECT id FROM big_orders WHERE customer_id = 5 OR email = 'user7@mail.test'"
    for name, eng in big.items():
        assert not _full(eng, _plan(eng, q)), (name, _plan(eng, q))
    res = analyze_sql(q)
    assert not [f for f in res["correct"]["findings"] if f["topic"] == "or_union"]
    assert any("index_merge" in n["en"] or "BitmapOr" in n["en"] for n in res["notes"]), res["notes"]


@pytest.mark.parametrize("order,offset,said", [("id", 15000, None), ("created_at, id", 9000, "mariadb"), ("created_at, id", 9000, "postgresql"),
                                               ("order_date DESC, id DESC", 12000, None), ("customer_id, id", 5000, "postgresql")])
def test_keyset_gives_the_same_page_as_offset(big, order, offset, said):
    """The page after row m, by OFFSET and by keyset with the last row's values (what the app remembers), on both engines."""
    q = f"SELECT id, customer_id, order_date, created_at FROM big_orders ORDER BY {order} LIMIT 25 OFFSET {offset}"
    res = analyze_sql(q, engine=said)
    assert [f for f in res["correct"]["findings"] if f["topic"] == "keyset"]
    new = res["suggested"]
    assert ":last_id" in new and "OFFSET" not in new.upper()
    cols = [re.sub(r"\s+(asc|desc)$", "", c.strip(), flags=re.I) for c in order.split(",")]
    for name, eng in big.items():
        if said and (name == "postgres") != (said == "postgresql"):
            continue
        page = eng.run(q)
        last = eng.run(f"SELECT {', '.join(cols)} FROM big_orders ORDER BY {order} LIMIT 1 OFFSET {offset - 1}")[0]
        sql = new
        for c, v in zip(cols, last):
            sql = sql.replace(f":last_{c}", f"'{v}'" if not isinstance(v, int) else str(v))
        assert [tuple(r) for r in eng.run(sql)] == [tuple(r) for r in page], (name, sql)
        if eng.kind == "mysql":                                    # MariaDB: OFFSET reads offset + limit rows, keyset reads the page
            assert int(eng.run("EXPLAIN " + q)[0][8]) >= offset


def test_offset_without_a_unique_tail_is_said_but_not_rewritten():
    res = analyze_sql("SELECT id FROM orders ORDER BY amount LIMIT 10 OFFSET 3000")
    f = [x for x in res["correct"]["findings"] if x["topic"] == "keyset"]
    assert f and "unique" in f[0]["fix_en"] and not res.get("suggested", "").count(":last")


# ------------------------------------------------------------------------------------------------ with the table's structure
DESIGN = """CREATE TABLE orders (
  id INT PRIMARY KEY,
  customer_id INT,
  status VARCHAR(20),
  order_date DATE,
  amount DECIMAL(10,2),
  phone VARCHAR(20),
  KEY ix_cust_date (customer_id, order_date),
  KEY ix_amount_status (amount, status)
);"""


def test_why_an_existing_index_is_not_used():
    res = analyze_sql("SELECT id FROM orders WHERE order_date = '2024-07-10'", DESIGN)
    f = [x for x in res["findings"] if x.get("topic") == "index_prefix"]
    assert f and "(customer_id, order_date)" in f[0]["title_en"] and f[0]["basis"]["id"] == "index_use"
    assert any("(order_date)" in i for i in res["indexes"]), res["indexes"]
    res = analyze_sql("SELECT id FROM orders WHERE amount > 5000 AND status = 'paid'", DESIGN)
    f = [x for x in res["findings"] if x.get("topic") == "index_range_first"]
    assert f and "(status, amount)" in f[0]["fix_en"], [x["title_en"] for x in res["findings"]]
    res = analyze_sql("SELECT id FROM orders WHERE customer_id = 3 AND order_date >= '2024-07-01'", DESIGN)
    assert not [x for x in res["findings"] if x.get("topic") in ("index_prefix", "index_range_first")]


@pytest.mark.parametrize("said,word", [("mariadb", "'098"), ("postgresql", "42883")])
def test_text_column_compared_with_a_number(engines, said, word):
    res = analyze_sql("SELECT id FROM orders WHERE phone = 9812345678", DESIGN, engine=said)
    f = [x for x in res["findings"] if x.get("topic") == "implicit_cast"]
    assert f and word in f[0]["fix_en"] and "'9812345678'" in f[0]["fix_en"], [x["title_en"] for x in res["findings"]]


def test_the_lab_text_vs_number(engines):
    """MariaDB matches '098...' and '98... ' too (no error); PostgreSQL refuses (42883)."""
    my, pg = engines["mysql"], engines["postgres"]
    for e in (my, pg):
        e.run("DROP TEMPORARY TABLE IF EXISTS ph" if e.kind == "mysql" else "DROP TABLE IF EXISTS pg_temp.ph")
        e.run("CREATE TEMPORARY TABLE ph (id INT, phone VARCHAR(20))")
        e.run("INSERT INTO ph VALUES (1, '09812345678'), (2, '9812345678 '), (3, '9812345678')")
    assert sorted(r[0] for r in my.run("SELECT id FROM ph WHERE phone = 9812345678")) == [1, 2, 3]
    assert sorted(r[0] for r in my.run("SELECT id FROM ph WHERE phone = '9812345678'")) == [2, 3]   # PAD SPACE: the trailing space
    with pytest.raises(Exception) as e:
        pg.run("SELECT id FROM ph WHERE phone = 9812345678")
    assert getattr(e.value, "sqlstate", "") == "42883"
    pg.conn.rollback()


# ------------------------------------------------------------------------------------------------ reading EXPLAIN
def test_explain_plans_from_the_lab_are_read(big):
    """Real plans from both engines: the reader names the full scan, the function that hides the column, the join with no
    index, the estimate gap, and says which database the format shows."""
    my, pg = big["mysql"], big["postgres"]
    q = ("SELECT o.id, c.city FROM big_orders o JOIN big_customers c ON c.city = o.status "
         "WHERE YEAR(o.order_date) = 2023 ORDER BY o.amount")
    rows = my.run("EXPLAIN " + q)
    head = "| id | select_type | table | type | possible_keys | key | key_len | ref | rows | Extra |"
    text = head + "\n" + "\n".join("| " + " | ".join("NULL" if v is None else str(v) for v in r) + " |" for r in rows)
    plan = sqlexplain.read(text)
    assert plan and plan["engine"] == "mysql"
    topics = {f["topic"] for f in plan["findings"]}
    assert {"plan_full_scan", "plan_join_scan"} & topics and "plan_filesort" in topics, topics
    pq = q.replace("YEAR(o.order_date)", "EXTRACT(YEAR FROM o.order_date)")
    text = "\n".join(r[0] for r in pg.run("EXPLAIN ANALYZE " + pq))
    plan = sqlexplain.read(text)
    assert plan and plan["engine"] == "postgresql" and plan["analyzed"]
    topics = {f["topic"] for f in plan["findings"]}
    assert "plan_function" in topics and "plan_estimate" in topics, (topics, text)
    out = sqlexplain.render(plan)
    assert out.startswith("This is a PostgreSQL plan") and "Seq Scan on big_orders" in out
    good = "\n".join(r[0] for r in pg.run("EXPLAIN SELECT id FROM big_orders WHERE customer_id = 7"))
    assert sqlexplain.read(good)["findings"] == []


MY_VERTICAL = """*************************** 1. row ***************************
           id: 1
  select_type: SIMPLE
        table: o
         type: ALL
possible_keys: ix_order_date
          key: NULL
      key_len: NULL
          ref: NULL
         rows: 19569
        Extra: Using where"""

MY_TREE = """-> Sort: o.amount  (cost=2104 rows=19569)
    -> Filter: (year(o.order_date) = 2023)  (cost=2104 rows=19569)
        -> Table scan on o  (cost=2104 rows=19569)"""

PG_SPILL = """Sort  (cost=9000.00..9100.00 rows=40000 width=40) (actual time=80.1..95.2 rows=40000 loops=1)
  Sort Key: created_at
  Sort Method: external merge  Disk: 2208kB
  ->  Seq Scan on events  (cost=0.00..700.00 rows=40000 width=40) (actual time=0.01..5.0 rows=40000 loops=1)
Planning Time: 0.1 ms
Execution Time: 98.0 ms"""


@pytest.mark.parametrize("text,topic", [(MY_VERTICAL, "plan_index_ignored"), (MY_TREE, "plan_full_scan"), (MY_TREE, "plan_function"),
                                        (PG_SPILL, "plan_spill")])
def test_other_plan_formats(text, topic):
    plan = sqlexplain.read(text)
    assert plan and topic in {f["topic"] for f in plan["findings"]}, (topic, plan and plan["findings"])


def test_a_plan_pasted_with_the_query_is_read_on_the_card():
    res = analyze_sql("SELECT id FROM orders WHERE YEAR(order_date) = 2023", MY_VERTICAL)
    assert res.get("plan") and res["plan"]["engine"] == "mysql" and "How" not in res["plan"]["en"][:5]
    assert any(f["title_en"].startswith("EXPLAIN shows a full table scan") for f in res["findings"])


def test_every_rule_records_where_it_was_proven():
    """Standing rule B: each rule and each engine fact says which engines the lab proved it on."""
    import json

    from app.agents.sqlread import fundamentals

    assert all(set(v) >= {"mariadb", "postgresql"} for v in sqlspeed.PROVEN.values())
    assert all(set(v) >= {"mariadb", "postgresql"} for v in sqlexplain.PROVEN.values())
    topics = set(fundamentals()["fundamentals"]["index_use"]["topics"])
    assert set(sqlspeed.PROVEN) <= topics and set(sqlexplain.PROVEN) <= topics
    for eng in ("mysql", "mariadb", "postgresql"):
        d = json.load(open(f"app/dbknow/dialects/{eng}.json"))
        for key in ("like_prefix_index", "text_vs_number", "or_same_table", "expression_index", "offset_reads"):
            assert d["query_rules"][key]["proven"] and d["query_rules"][key]["says"], (eng, key)
        assert d["explain"]["proven"]
