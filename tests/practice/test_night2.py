"""Night 2 practice bank: joins and relationship reasoning, plus syntax that the chosen database doesn't have (rule A).

Each problem: what the person wants (in words), the query they wrote (wrong), a correct query, and the database it is for
(None = not said). The correct query is run on MariaDB and PostgreSQL first and both must agree (the verified answer). Then
DBaba gets the wrong query with the words (and the database, when one is said): it must find the mistake under the right
topic, and its corrected (or 'if that's what you meant') query must return the verified rows. fix=None: finding it is
enough (there is no single right rewrite, e.g. which column links two tables when the query never says).

LAB below is what each engine really does with the wrong query (reproduced on MariaDB 10.11 and PostgreSQL 16).
"""

import pytest

from app.agents.sql import analyze_sql
from tests.practice import schema as S

# (id, words, wrong query, topic, correct query or None, keep row order?, run only on, database said)
P = [
    # ---- cartesian products: a table never tied to the others
    ("comma_cartesian", "each order with its customer's name", "SELECT o.id, c.first_name FROM orders o, customers c",
     "cartesian", None, False, None, None),
    ("comma_filter_only", "paid orders with the customer name", "SELECT o.id, c.first_name FROM orders o, customers c WHERE o.status = 'paid'",
     "cartesian", None, False, None, None),
    ("comma_third_table", "orders with customer and payment",
     "SELECT o.id, c.first_name, p.method FROM orders o, customers c, payments p WHERE o.customer_id = c.id",
     "cartesian", None, False, None, None),
    ("on_only_joined", "customers with their paid orders", "SELECT c.first_name, o.id FROM customers c JOIN orders o ON o.status = 'paid'",
     "cartesian", None, False, None, None),
    ("on_same_table", "customers with their orders", "SELECT c.first_name, o.id FROM customers c JOIN orders o ON o.customer_id = o.id",
     "cartesian", None, False, None, None),
    ("join_no_on", "orders with the customer", "SELECT c.first_name, o.id FROM customers c JOIN orders o",
     "join", None, False, None, None),
    # ---- the columns compared: primary keys, a key the wrong way round, the wrong parent, a name instead of a key
    ("pk_pk", "each order with its customer's name", "SELECT o.id, c.first_name FROM orders o JOIN customers c ON o.id = c.id",
     "join_key", "SELECT o.id, c.first_name FROM orders o JOIN customers c ON o.customer_id = c.id", False, None, None),
    ("pk_pk_items", "order lines with the product name", "SELECT oi.id, p.name FROM order_items oi JOIN products p ON p.id = oi.id",
     "join_key", "SELECT oi.id, p.name FROM order_items oi JOIN products p ON oi.product_id = p.id", False, None, None),
    ("pk_pk_payments", "payments with the order status", "SELECT p.id, o.status FROM payments p JOIN orders o ON p.id = o.id",
     "join_key", "SELECT p.id, o.status FROM payments p JOIN orders o ON p.order_id = o.id", False, None, None),
    ("pk_pk_department", "employees with their department name", "SELECT e.first_name, d.name FROM employees e JOIN departments d ON e.id = d.id",
     "join_key", "SELECT e.first_name, d.name FROM employees e JOIN departments d ON e.department_id = d.id", False, None, None),
    ("pk_pk_supplier", "products with their supplier", "SELECT p.name, s.name FROM products p JOIN suppliers s ON s.id = p.id",
     "join_key", "SELECT p.name, s.name FROM products p JOIN suppliers s ON p.supplier_id = s.id", False, None, None),
    ("fk_reversed", "each order with its customer's name", "SELECT o.id, c.first_name FROM orders o JOIN customers c ON c.customer_id = o.id",
     "join_key", "SELECT o.id, c.first_name FROM orders o JOIN customers c ON o.customer_id = c.id", False, None, None),
    ("fk_wrong_parent", "order lines with the order date and product name",
     "SELECT oi.id, o.order_date, p.name FROM order_items oi JOIN orders o ON oi.product_id = o.id JOIN products p ON p.id = oi.order_id",
     "join_key", "SELECT oi.id, o.order_date, p.name FROM order_items oi JOIN orders o ON oi.order_id = o.id JOIN products p ON oi.product_id = p.id",
     False, None, None),
    ("name_join", "products with their category", "SELECT p.name AS product, c.name AS category FROM products p JOIN categories c ON c.name = p.name",
     "join_key", "SELECT p.name AS product, c.name AS category FROM products p JOIN categories c ON p.category_id = c.id", False, None, None),
    ("natural_join", "orders with their customer", "SELECT * FROM orders NATURAL JOIN customers", "natural_join", None, False, None, None),
    ("using_id", "orders with the customer name", "SELECT o.id, c.first_name FROM orders o JOIN customers c USING (id)",
     "natural_join", None, False, None, None),
    # ---- anti-joins: rows with no match
    ("anti_nullable", "customers who never placed an order",
     "SELECT c.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.discount IS NULL",
     "anti_join", "SELECT c.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.customer_id IS NULL", False, None, None),
    ("anti_products", "products that were never ordered",
     "SELECT p.id FROM products p LEFT JOIN order_items oi ON oi.product_id = p.id WHERE oi.discount IS NULL",
     "anti_join", "SELECT p.id FROM products p LEFT JOIN order_items oi ON oi.product_id = p.id WHERE oi.product_id IS NULL", False, None, None),
    ("anti_payments", "orders without any payment",
     "SELECT o.id FROM orders o LEFT JOIN payments p ON p.order_id = o.id WHERE p.method IS NULL",
     "anti_join", "SELECT o.id FROM orders o LEFT JOIN payments p ON p.order_id = o.id WHERE p.order_id IS NULL", False, None, None),
    ("anti_departments", "departments with no employees",
     "SELECT d.id FROM departments d LEFT JOIN employees e ON e.department_id = d.id WHERE e.commission IS NULL",
     "anti_join", "SELECT d.id FROM departments d LEFT JOIN employees e ON e.department_id = d.id WHERE e.department_id IS NULL", False, None, None),
    ("anti_returns", "order lines that were not returned",
     "SELECT oi.id FROM order_items oi LEFT JOIN returns r ON r.order_item_id = oi.id WHERE r.reason IS NULL",
     "anti_join", "SELECT oi.id FROM order_items oi LEFT JOIN returns r ON r.order_item_id = oi.id WHERE r.order_item_id IS NULL", False, None, None),
    # ---- self joins
    ("self_pairs_dept", "pairs of employees in the same department",
     "SELECT a.first_name, b.first_name FROM employees a JOIN employees b ON a.department_id = b.department_id",
     "self_join", "SELECT a.first_name, b.first_name FROM employees a JOIN employees b ON a.department_id = b.department_id AND a.id < b.id",
     False, None, None),
    ("self_pairs_city", "pairs of customers from the same city",
     "SELECT a.id, b.id FROM customers a JOIN customers b ON a.city = b.city",
     "self_join", "SELECT a.id, b.id FROM customers a JOIN customers b ON a.city = b.city AND a.id < b.id", False, None, None),
    ("self_pairs_twice", "pairs of products from the same supplier",
     "SELECT a.name, b.name FROM products a JOIN products b ON a.supplier_id = b.supplier_id AND a.id <> b.id",
     "self_join", "SELECT a.name, b.name FROM products a JOIN products b ON a.supplier_id = b.supplier_id AND a.id < b.id", False, None, None),
    ("self_pairs_salary", "pairs of employees with the same salary",
     "SELECT a.id, b.id FROM employees a JOIN employees b ON a.salary = b.salary",
     "self_join", "SELECT a.id, b.id FROM employees a JOIN employees b ON a.salary = b.salary AND a.id < b.id", False, None, None),
    ("self_manager", "each employee with their manager's name",
     "SELECT e.first_name, m.first_name AS manager FROM employees e JOIN employees m ON m.manager_id = e.id",
     "join", "SELECT e.first_name, m.first_name AS manager FROM employees e JOIN employees m ON m.id = e.manager_id", False, None, None),
    # ---- DISTINCT hiding the copies a join made
    ("distinct_customers", "customers who placed an order",
     "SELECT DISTINCT c.id, c.first_name FROM customers c JOIN orders o ON o.customer_id = c.id",
     "distinct_join", "SELECT c.id, c.first_name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)", False, None, None),
    ("distinct_paid", "customers with a paid order",
     "SELECT DISTINCT c.id, c.first_name FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.status = 'paid' ORDER BY c.id",
     "distinct_join", "SELECT c.id, c.first_name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id AND o.status = 'paid') ORDER BY c.id",
     True, None, None),
    ("distinct_products", "names of products that have been ordered",
     "SELECT DISTINCT p.name FROM products p JOIN order_items oi ON oi.product_id = p.id",
     "distinct_join", "SELECT DISTINCT p.name FROM products p WHERE EXISTS (SELECT 1 FROM order_items oi WHERE oi.product_id = p.id)", False, None, None),
    ("distinct_cities", "cities of customers who ordered",
     "SELECT DISTINCT c.city FROM customers c JOIN orders o ON o.customer_id = c.id",
     "distinct_join", "SELECT DISTINCT c.city FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)", False, None, None),
    # ---- outer joins (rules from earlier nights, now in the joins bank)
    ("inner_after_left", "every customer, with orders and payments if any",
     "SELECT c.id, o.id, p.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id JOIN payments p ON p.order_id = o.id",
     "join", "SELECT c.id, o.id, p.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id LEFT JOIN payments p ON p.order_id = o.id",
     False, None, None),
    ("left_count_star", "number of orders of every customer, 0 when none",
     "SELECT c.id, COUNT(*) AS n FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id",
     "count", "SELECT c.id, COUNT(o.id) AS n FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id", False, None, None),
    ("left_where", "all customers with their paid orders",
     "SELECT c.id, o.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.status = 'paid'",
     "join", None, False, None, None),
    # ---- rule A: the database was named, and the query uses syntax that database doesn't have
    ("pg_cast_on_mariadb", "every order amount as text, on MariaDB", "SELECT id, amount::text AS a FROM orders",
     "dialect", "SELECT id, CAST(amount AS CHAR) AS a FROM orders", False, ["mysql"], "mariadb"),
    ("ilike_on_mysql", "customers called aarav, any case (MySQL)", "SELECT id FROM customers WHERE first_name ILIKE 'aarav'",
     "dialect", "SELECT id FROM customers WHERE first_name LIKE 'aarav'", False, ["mysql"], "mysql"),
    ("nulls_last_on_mariadb", "orders by discount, biggest first, empty ones last",
     "SELECT id, discount FROM orders ORDER BY discount DESC NULLS LAST, id",
     "dialect", "SELECT id, discount FROM orders ORDER BY discount IS NULL, discount DESC, id", True, None, "mariadb"),
    ("getdate_on_mariadb", "orders placed before now", "SELECT id FROM orders WHERE order_date < GETDATE()",
     "dialect", "SELECT id FROM orders WHERE order_date < NOW()", False, None, "mariadb"),
    ("backticks_on_pg", "orders above 1000 (PostgreSQL)", "SELECT `id`, `status` FROM `orders` WHERE `amount` > 1000",
     "dialect", "SELECT id, status FROM orders WHERE amount > 1000", False, ["postgres"], "postgresql"),
    ("limit_comma_on_pg", "orders 3 to 5 by id (PostgreSQL)", "SELECT id FROM orders ORDER BY id LIMIT 2, 3",
     "dialect", "SELECT id FROM orders ORDER BY id LIMIT 3 OFFSET 2", True, None, "postgresql"),
    ("curdate_on_pg", "orders placed before today (PostgreSQL)", "SELECT id FROM orders WHERE order_date < CURDATE()",
     "dialect", "SELECT id FROM orders WHERE order_date < CURRENT_DATE", False, None, "postgresql"),
    ("group_concat_on_pg", "the customer ids per city in one line (PostgreSQL)",
     "SELECT city, GROUP_CONCAT(id) FROM customers GROUP BY city", "dialect", None, False, None, "postgresql"),
    ("top_on_mysql", "the 3 biggest orders (MySQL)", "SELECT TOP 3 id FROM orders ORDER BY amount DESC", "dialect", None, False, None, "mysql"),
]

# what each engine does with the wrong query: 'rows' (runs, the rows are wrong), 'error' (refused)
LAB = {
    "comma_cartesian": ("rows", "rows"),        # 64 rows: 8 orders x 8 customers
    "on_only_joined": ("rows", "rows"),         # every customer x every paid order
    "join_no_on": ("rows", "error"),            # MariaDB: a cross join; PostgreSQL: syntax error at end of input
    "pk_pk": ("rows", "rows"),                  # order N with customer N
    "fk_reversed": ("error", "error"),          # customers has no customer_id: 1054 / column c.customer_id does not exist
    "natural_join": ("rows", "rows"),           # joined on id = id (the only shared name)
    "using_id": ("rows", "rows"),
    "anti_nullable": ("rows", "rows"),          # also customers whose orders have no discount
    "anti_payments": ("rows", "rows"),          # also order 2, whose payment has no method
    "self_pairs_dept": ("rows", "rows"),        # each employee also paired with themselves
    "distinct_customers": ("rows", "rows"),     # right rows, built from the multiplied ones
    "pg_cast_on_mariadb": ("error", "rows"),    # MariaDB 1064 near '::text'
    "ilike_on_mysql": ("error", "rows"),        # MariaDB 1064 near 'ILIKE'
    "nulls_last_on_mariadb": ("error", "rows"), # MariaDB 1064 near 'NULLS LAST'
    "getdate_on_mariadb": ("error", "error"),   # MariaDB 1305 FUNCTION GETDATE does not exist / PostgreSQL 42883
    "backticks_on_pg": ("rows", "error"),       # PostgreSQL 42601 syntax error at or near "`"
    "limit_comma_on_pg": ("rows", "error"),     # PostgreSQL: LIMIT #,# syntax is not supported
    "curdate_on_pg": ("rows", "error"),         # PostgreSQL 42883 function curdate() does not exist
    "group_concat_on_pg": ("rows", "error"),    # PostgreSQL 42883 function group_concat(integer) does not exist
    "top_on_mysql": ("error", "error"),         # MariaDB 1064 / PostgreSQL 42601
}


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


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)


@pytest.mark.parametrize("pid,words,wrong,topic,fix,ordered,only,said", P, ids=[p[0] for p in P])
def test_dbaba_finds_and_fixes(engines, pid, words, wrong, topic, fix, ordered, only, said):
    res = analyze_sql(f"{wrong};\n\n{words}", engine=said)
    found = [f for f in res["correct"]["findings"] if f["topic"] == topic]
    assert found, (pid, [(f["topic"], f["title_en"]) for f in res["correct"]["findings"]])
    assert all(f.get("basis") for f in found), (pid, "every finding names its fundamental")
    if fix is None:
        return
    offered = res["correct"].get("corrected")
    assert offered, (pid, "no corrected / suggested query")
    assert offered in (res.get("rewritten"), res.get("suggested")), (pid, "shown on the card")
    for name, eng in engines.items():
        if only and name not in only:
            continue
        assert _rows(eng, offered, ordered) == _rows(eng, fix, ordered), (pid, name, offered)


def test_the_verified_answers_agree_on_both_databases(engines):
    for pid, _w, _q, _t, fix, ordered, only, _s in P:
        if fix is None or only:
            continue
        m, p = (_rows(engines[k], fix, ordered) for k in ("mysql", "postgres"))
        assert m == p, (pid, m, p)


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
    # and the wrong rows are really wrong
    for name, eng in engines.items():
        assert len(_rows(eng, by["comma_cartesian"][2], False)) == 64
        assert len(_rows(eng, by["pk_pk"][2], False)) == 8            # 8 pairs by id, though order 1's customer is 1 only by luck
        assert ("2",) in _rows(eng, by["anti_payments"][2], False) and ("2",) not in _rows(eng, by["anti_payments"][4], False)
        assert ("Asha", "Asha") in _rows(eng, by["self_pairs_dept"][2], False)
        assert len(_rows(eng, by["natural_join"][2], False)) == 8


def test_valid_joins_get_no_findings():
    """The join checks stay quiet on joins that are right (no false alarms)."""
    for sql in ["SELECT o.id, c.first_name FROM orders o JOIN customers c ON o.customer_id = c.id",
                "SELECT o.id, c.first_name FROM orders o, customers c WHERE o.customer_id = c.id",
                "SELECT c.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.id IS NULL",
                "SELECT c.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.customer_id IS NULL",
                "SELECT a.id, b.id FROM employees a JOIN employees b ON a.department_id = b.department_id AND a.id < b.id",
                "SELECT e.first_name, m.first_name FROM employees e JOIN employees m ON m.id = e.manager_id",
                "SELECT c.id, c.first_name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)",
                "SELECT oi.id, p.name FROM order_items oi JOIN orders o ON oi.order_id = o.id JOIN products p ON oi.product_id = p.id",
                "SELECT o.id, oi.product_id FROM orders o JOIN order_items oi ON oi.order_id = o.id",
                "SELECT d.name, COUNT(e.id) FROM departments d LEFT JOIN employees e ON e.department_id = d.id GROUP BY d.id, d.name",
                "SELECT p.name, c.name FROM products p CROSS JOIN categories c",
                "SELECT o.id FROM orders o JOIN order_items oi USING (order_id)",
                "SELECT u.id FROM users u JOIN user_profiles p ON p.user_id = u.id"]:
        res = analyze_sql(sql)
        bad = [(f["kind"], f["title_en"]) for f in res["correct"]["findings"] if f["topic"] in
               ("cartesian", "join_key", "natural_join", "anti_join", "self_join", "distinct_join") or f["kind"] in ("error", "wrong")]
        assert not bad, (sql, bad)


def test_named_database_syntax_is_said_per_engine():
    """The same query, three databases: only the one named gets its own refusal (rule A)."""
    q = "SELECT `id` FROM orders WHERE status ILIKE 'paid' LIMIT 0, 5"
    on_pg = [f["title_en"] for f in analyze_sql(q, engine="postgresql")["correct"]["findings"] if f["topic"] == "dialect"]
    on_my = [f["title_en"] for f in analyze_sql(q, engine="mysql")["correct"]["findings"] if f["topic"] == "dialect"]
    unknown = [f for f in analyze_sql(q)["correct"]["findings"] if f["topic"] == "dialect"]
    assert any("backticks" in t for t in on_pg) and any("LIMIT offset" in t for t in on_pg) and not any("ILIKE" in t for t in on_pg)
    assert on_my == ["MySQL has no ILIKE (that's PostgreSQL)"]
    assert not unknown                                   # no database said: no engine's refusal is claimed
    sqlserver = analyze_sql("SELECT TOP 3 id FROM orders ORDER BY amount DESC", engine="sqlserver")["correct"]["findings"]
    assert not [f for f in sqlserver if f["topic"] == "dialect"]   # TOP is SQL Server's own


def test_every_rule_says_where_it_was_proven():
    """Standing rule B: each join rule and each per-engine refusal records the engines the lab proved it on."""
    import json
    from pathlib import Path

    from app.agents import sqljoins

    assert set(sqljoins.PROVEN) == {"cartesian", "join_key", "natural_join", "anti_join", "self_join", "distinct_join"}
    assert all(set(v) >= {"mariadb", "postgresql"} for v in sqljoins.PROVEN.values())
    d = Path(sqljoins.__file__).resolve().parents[1] / "dbknow" / "dialects"
    for fam, eng in (("mysql", "mariadb"), ("postgresql", "postgresql")):
        refuses = json.loads((d / f"{fam}.json").read_text(encoding="utf-8"))["refuses"]
        assert refuses and all(eng in r["proven"] for r in refuses), fam
    for notes in ("sqlserver", "oracle", "sqlite"):
        assert json.loads((d / f"{notes}.json").read_text(encoding="utf-8"))["notes_only"] is True
