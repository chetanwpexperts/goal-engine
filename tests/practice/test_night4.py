"""Night 4 practice bank: subqueries (EXISTS, IN / NOT IN, ANY / ALL, scalar subqueries in SELECT, correlated subqueries,
latest row per group, a JOIN used where EXISTS was meant), plus the subquery syntax one database has and another doesn't (rule A).

Each problem: what the person wants (in words), the query they wrote (wrong), a correct query, and the database it is for
(None = not said). The correct query is run on MariaDB and PostgreSQL first and both must agree (the verified answer). Then
DBaba gets the wrong query with the words (and the database, when one is said): it must find the mistake under the right
topic, name its fundamental, and its corrected (or 'if that's what you meant') query must return the verified rows.
fix=None: finding it is enough (there is no single right rewrite, e.g. which one of a customer's orders was meant).

LAB below is what each engine really does with the wrong query (reproduced on MariaDB 10.11 and PostgreSQL 16).
"""

import pytest

from app.agents.sql import analyze_sql
from tests.practice import schema as S

# (id, words, wrong query, topic, correct query or None, keep row order?, run only on, database said)
P = [
    # ---- EXISTS (SELECT <total> ...): a total without GROUP BY is always one row
    ("ea_count", "customers who have at least one order",
     "SELECT id FROM customers c WHERE EXISTS (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id)",
     "exists_agg", "SELECT id FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)", False, None, None),
    ("ea_never_sold", "products that were never sold",
     "SELECT id FROM products p WHERE NOT EXISTS (SELECT MAX(oi.quantity) FROM order_items oi WHERE oi.product_id = p.id)",
     "exists_agg", "SELECT id FROM products p WHERE NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.product_id = p.id)", False, None, None),
    ("ea_depts", "departments that have employees",
     "SELECT name FROM departments d WHERE EXISTS (SELECT SUM(salary) FROM employees e WHERE e.department_id = d.id)",
     "exists_agg", "SELECT name FROM departments d WHERE EXISTS (SELECT 1 FROM employees e WHERE e.department_id = d.id)", False, None, None),
    # ---- x = x inside a correlated subquery: names are looked up in the subquery's own FROM first
    ("cs_alias", "orders above their customer's average order",
     "SELECT id FROM orders o WHERE amount > (SELECT AVG(amount) FROM orders o2 WHERE o2.customer_id = o2.customer_id)",
     "corr_self", "SELECT id FROM orders o WHERE amount > (SELECT AVG(o2.amount) FROM orders o2 WHERE o2.customer_id = o.customer_id)",
     False, None, None),
    ("cs_same_name", "orders above their customer's average order",
     "SELECT id, amount FROM orders WHERE amount > (SELECT AVG(amount) FROM orders WHERE customer_id = orders.customer_id)",
     "corr_self", "SELECT id, amount FROM orders o WHERE amount > (SELECT AVG(o2.amount) FROM orders o2 WHERE o2.customer_id = o.customer_id)",
     False, None, None),
    ("cs_employees", "employees earning more than their department's average",
     "SELECT id FROM employees e WHERE salary > (SELECT AVG(x.salary) FROM employees x WHERE x.department_id = x.department_id)",
     "corr_self", "SELECT id FROM employees e WHERE salary > (SELECT AVG(x.salary) FROM employees x WHERE x.department_id = e.department_id)",
     False, None, None),
    ("cs_products", "products priced above their category's average",
     "SELECT id FROM products WHERE price > (SELECT AVG(price) FROM products WHERE products.category_id = category_id)",
     "corr_self", "SELECT id FROM products p WHERE price > (SELECT AVG(q.price) FROM products q WHERE q.category_id = p.category_id)",
     False, None, None),
    # ---- the subquery returns the outer row's own column
    ("oc_customers", "customers with a paid order",
     "SELECT id FROM customers c WHERE c.id IN (SELECT c.id FROM orders o WHERE o.status = 'paid')", "sub_outer_col", None, False, None, None),
    ("oc_products", "products that were returned",
     "SELECT p.id FROM products p WHERE p.id IN (SELECT p.id FROM order_items oi JOIN returns r ON r.order_item_id = oi.id)",
     "sub_outer_col", None, False, None, None),
    # ---- one table's id compared with another table's id
    ("sk_customers", "customers who have a paid order",
     "SELECT id FROM customers WHERE id IN (SELECT id FROM orders WHERE status = 'paid')",
     "sub_key", "SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders WHERE status = 'paid')", False, None, None),
    ("sk_products", "products that have been ordered", "SELECT id, name FROM products WHERE id IN (SELECT id FROM order_items)",
     "sub_key", "SELECT id, name FROM products WHERE id IN (SELECT product_id FROM order_items)", False, None, None),
    # ---- IN (SELECT MAX(y) ... GROUP BY g): any group's maximum, not the row's own
    ("ig_paid", "the cheapest paid order of each customer",
     "SELECT id FROM orders WHERE amount IN (SELECT MIN(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id)",
     "in_grouped_agg", "SELECT id FROM orders o WHERE amount = (SELECT MIN(amount) FROM orders x WHERE x.status = 'paid' AND x.customer_id = o.customer_id)",
     False, None, None),
    ("ig_employees", "the highest paid employee of each department",
     "SELECT id FROM employees WHERE salary IN (SELECT MAX(salary) FROM employees GROUP BY department_id)",
     "in_grouped_agg", "SELECT id FROM employees e WHERE salary = (SELECT MAX(salary) FROM employees x WHERE x.department_id = e.department_id)",
     False, None, None),
    ("ig_products", "the cheapest product in each category",
     "SELECT name FROM products WHERE price IN (SELECT MIN(price) FROM products GROUP BY category_id)",
     "in_grouped_agg", "SELECT name FROM products p WHERE price = (SELECT MIN(price) FROM products x WHERE x.category_id = p.category_id)",
     False, None, None),
    # ---- two columns where one is compared
    ("sc_in", "customers who ordered", "SELECT id FROM customers WHERE id IN (SELECT customer_id, amount FROM orders)",
     "sub_columns", None, False, None, None),
    ("sc_keep", "orders of customers who also have a shipped order",
     "SELECT id FROM orders WHERE customer_id IN (SELECT customer_id, status FROM orders WHERE status = 'shipped')",
     "sub_columns", "SELECT id FROM orders WHERE customer_id IN (SELECT customer_id FROM orders WHERE status = 'shipped')", False, None, None),
    ("sc_eq", "the first name of the customer of order 3",
     "SELECT first_name FROM customers WHERE id = (SELECT customer_id, amount FROM orders WHERE id = 3)", "sub_columns", None, False, None, None),
    # ---- a subquery in SELECT that can give several rows
    ("sr_amount", "each customer's order amount",
     "SELECT c.id, (SELECT o.amount FROM orders o WHERE o.customer_id = c.id) AS amount FROM customers c", "scalar_rows", None, False, None, None),
    ("sr_total", "each customer's total order amount",
     "SELECT c.id, (SELECT o.amount FROM orders o WHERE o.customer_id = c.id) AS total FROM customers c",
     "scalar_rows", "SELECT c.id, (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id) FROM customers c", False, None, None),
    ("sr_staff", "each department with an employee name",
     "SELECT d.name, (SELECT e.first_name FROM employees e WHERE e.department_id = d.id) FROM departments d",
     "scalar_rows", None, False, None, None),
    # ---- a correlated SUM: NULL, not 0, when there is nothing
    ("sn_spent", "total spent by each customer, 0 when nothing",
     "SELECT c.id, (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id) AS spent FROM customers c",
     "sub_sum_null", "SELECT c.id, COALESCE(SUM(o.amount), 0) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id",
     False, None, None),
    ("sn_sold", "units sold of each product, 0 if never sold",
     "SELECT p.id, (SELECT SUM(oi.quantity) FROM order_items oi WHERE oi.product_id = p.id) AS sold FROM products p",
     "sub_sum_null", "SELECT p.id, COALESCE(SUM(oi.quantity), 0) FROM products p LEFT JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id",
     False, None, None),
    ("sn_paid", "amount paid for every order (0 if unpaid)",
     "SELECT o.id, (SELECT SUM(pm.amount) FROM payments pm WHERE pm.order_id = o.id) AS paid FROM orders o",
     "sub_sum_null", "SELECT o.id, COALESCE(SUM(pm.amount), 0) FROM orders o LEFT JOIN payments pm ON pm.order_id = o.id GROUP BY o.id",
     False, None, None),
    # ---- LIMIT without ORDER BY inside a subquery
    ("lo_latest", "each customer's latest order amount",
     "SELECT c.id, (SELECT o.amount FROM orders o WHERE o.customer_id = c.id LIMIT 1) AS latest FROM customers c",
     "sub_limit_order", None, False, None, None),
    ("lo_top", "products of the first top-level category",
     "SELECT id FROM products WHERE category_id = (SELECT id FROM categories WHERE parent_id IS NULL LIMIT 1)",
     "sub_limit_order", None, False, None, None),
    # ---- LIMIT inside IN / ANY: MySQL and MariaDB refuse it
    ("il_mariadb", "customers behind the 3 biggest orders (MariaDB)",
     "SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders ORDER BY amount DESC LIMIT 3)",
     "in_limit", "SELECT DISTINCT c.id FROM customers c JOIN (SELECT customer_id FROM orders ORDER BY amount DESC LIMIT 3) t ON t.customer_id = c.id", False, None, "mariadb"),
    ("il_any", "products dearer than one of the 2 cheapest, on mysql",
     "SELECT id FROM products WHERE price > ANY (SELECT price FROM products ORDER BY price LIMIT 2)",
     "in_limit", "SELECT id FROM products WHERE price > (SELECT MIN(price) FROM products)", False, None, "mysql"),
    # ---- a subquery in FROM without a name
    ("da_mysql", "the most orders one customer has (MySQL)",
     "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM orders GROUP BY customer_id)",
     "derived_alias", "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM orders GROUP BY customer_id) t", False, None, "mysql"),
    ("da_mariadb", "average units per order, on mariadb",
     "SELECT AVG(q) FROM (SELECT order_id, SUM(quantity) AS q FROM order_items GROUP BY order_id)",
     "derived_alias", "SELECT AVG(q) FROM (SELECT order_id, SUM(quantity) AS q FROM order_items GROUP BY order_id) t", False, None, "mariadb"),
    # ---- > ALL with NULLs / no rows
    ("an_commission", "employees whose salary is above every commission",
     "SELECT id FROM employees WHERE salary > ALL (SELECT commission FROM employees)",
     "all_null", "SELECT id FROM employees WHERE salary > (SELECT MAX(commission) FROM employees WHERE commission IS NOT NULL)", False, None, None),
    ("an_laptops", "products cheaper than every laptop",
     "SELECT id FROM products WHERE price < ALL (SELECT price FROM products WHERE category_id = 3)",
     "all_null", "SELECT id FROM products WHERE price < (SELECT MIN(price) FROM products WHERE category_id = 3)", False, None, None),
    # ---- a NULL in a written list
    ("nl_city", "customers not from Pune or Delhi", "SELECT id FROM customers WHERE city NOT IN ('Pune', 'Delhi', NULL)",
     "in_list_null", "SELECT id FROM customers WHERE city NOT IN ('Pune', 'Delhi')", False, None, None),
    ("nl_discount", "orders with no discount or a discount of 50", "SELECT id FROM orders WHERE discount IN (50, NULL)",
     "in_list_null", "SELECT id FROM orders WHERE discount = 50 OR discount IS NULL", False, None, None),
    # ---- NOT IN on a column that may be NULL
    ("no_supplier", "products not from a UAE supplier",
     "SELECT id FROM products WHERE supplier_id NOT IN (SELECT id FROM suppliers WHERE country = 'UAE')",
     "not_in_outer", "SELECT p.id FROM products p LEFT JOIN suppliers s ON s.id = p.supplier_id AND s.country = 'UAE' WHERE s.id IS NULL", False, None, None),
    ("no_dept", "employees who don't work in a Pune department",
     "SELECT id FROM employees WHERE department_id NOT IN (SELECT id FROM departments WHERE location = 'Pune')",
     "not_in_outer", "SELECT e.id FROM employees e LEFT JOIN departments d ON d.id = e.department_id AND d.location = 'Pune' WHERE d.id IS NULL", False, None, None),
    # ---- a JOIN used only to keep the parents that have a child
    ("sj_paid", "customers who have a paid order",
     "SELECT c.id FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.status = 'paid'",
     "semi_join", "SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders WHERE status = 'paid')", False, None, None),
    ("sj_sold", "products which were sold",
     "SELECT p.id, p.name FROM products p JOIN order_items oi ON oi.product_id = p.id",
     "semi_join", "SELECT id, name FROM products WHERE id IN (SELECT product_id FROM order_items)", False, None, None),
    ("sj_hinglish", "wo customers jinhone order kiya hai",
     "SELECT c.first_name FROM customers c JOIN orders o ON o.customer_id = c.id",
     "semi_join", "SELECT first_name FROM customers WHERE id IN (SELECT customer_id FROM orders)", False, None, None),
    # ---- EXISTS that never mentions the outer row
    ("eu_cancelled", "customers who have a cancelled order",
     "SELECT id FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.status = 'cancelled')", "exists_uncorr", None, False, None, None),
    ("eu_empty", "departments with no employees",
     "SELECT id FROM departments d WHERE NOT EXISTS (SELECT 1 FROM employees e WHERE e.salary > 0)", "exists_uncorr", None, False, None, None),
    # ---- latest row per group written with an uncorrelated MAX
    ("lp_latest", "the latest order of each customer",
     "SELECT customer_id, id, order_date FROM orders WHERE order_date = (SELECT MAX(order_date) FROM orders)",
     "per_group", "SELECT customer_id, id, order_date FROM orders o WHERE order_date = (SELECT MAX(order_date) FROM orders x WHERE x.customer_id = o.customer_id)",
     False, None, None),
]

# what each engine does with the wrong query: 'rows' (runs; the rows are wrong), 'error' (refused)
LAB = {
    "ea_count": ("rows", "rows"),           # all 8 customers, not the 5 with orders
    "ea_never_sold": ("rows", "rows"),      # nothing, not product 6... (none here) -- NOT EXISTS of one row is always false
    "cs_alias": ("rows", "rows"),           # 1, 3, 6 (above the overall average 23075), not 1, 3, 4
    "cs_same_name": ("rows", "rows"),       # the same: orders.customer_id inside means the inner orders
    "oc_customers": ("rows", "rows"),       # all 8 customers
    "sk_customers": ("rows", "rows"),       # customers 1, 3, 5, 7 (the paid ORDER ids), not 1, 2, 3
    "ig_paid": ("rows", "rows"),            # 1, 2, 5, 7: order 2 (500, shipped) matches customer 2's cheapest paid 500
    "sc_in": ("error", "error"),            # MariaDB 1241 Operand should contain 1 column(s); PostgreSQL 42601 subquery has too many columns
    "sc_eq": ("error", "error"),            # MariaDB 4078 Illegal parameter data types int and row; PostgreSQL 42601 must return only one column
    "sr_amount": ("error", "error"),        # MariaDB 1242 Subquery returns more than 1 row; PostgreSQL 21000
    "sn_spent": ("rows", "rows"),           # NULL for customers 4, 5, 7
    "lo_latest": ("rows", "rows"),          # whichever order was read first
    "il_mariadb": ("error", "rows"),        # MariaDB 1235 doesn't yet support 'LIMIT & IN/ALL/ANY/SOME subquery'; PostgreSQL 1, 2, 6
    "da_mysql": ("error", "rows"),          # MariaDB 1064 (MySQL 1248 Every derived table must have its own alias); PostgreSQL 16 runs it
    "an_commission": ("rows", "rows"),      # nothing: commission has NULLs
    "nl_city": ("rows", "rows"),            # nothing
    "nl_discount": ("rows", "rows"),        # only order 2; the 4 orders with no discount are missed
    "no_supplier": ("rows", "rows"),        # 1, 2, 4: products 5 and 7 (no supplier) are dropped
    "sj_paid": ("rows", "rows"),            # 1, 2, 2, 3: customer 2 twice
    "eu_cancelled": ("rows", "rows"),       # all 8 customers
    "lp_latest": ("rows", "rows"),          # only order 8
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


def _outcome(eng, sql):
    S.build(eng)
    try:
        S.rows(eng, sql)
        return "rows"
    except Exception:  # noqa: BLE001 - the engine refused it
        try:
            eng.conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return "error"


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)


def test_the_lab_table_still_holds(engines):
    """What each engine does with the wrong query is what the lab saw (a rule is only as good as this)."""
    wrong = {p[0]: p[2] for p in P}
    for pid, (my, pg) in LAB.items():
        assert _outcome(engines["mysql"], wrong[pid]) == my, (pid, "mariadb")
        assert _outcome(engines["postgres"], wrong[pid]) == pg, (pid, "postgresql")


def test_the_lab_rows_are_really_wrong(engines):
    """The wrong queries that run give other rows than the verified answer, on both databases."""
    by = {p[0]: p for p in P}
    for pid in ("ea_count", "cs_alias", "cs_same_name", "sk_customers", "ig_paid", "an_commission", "nl_city", "nl_discount",
                "no_supplier", "sj_paid", "lp_latest"):
        _pid, _w, wrong, _t, fix, ordered, _o, _s = by[pid]
        for name, eng in engines.items():
            assert _rows(eng, wrong, False) != _rows(eng, fix, False), (pid, name)


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


def test_valid_subqueries_raise_no_alarm():
    """Right subqueries of every shape: no error/wrong finding (correlated, EXISTS (SELECT 1 / *), tuple IN, LATERAL ...)."""
    for q in [
        "SELECT id FROM customers c WHERE EXISTS (SELECT * FROM orders o WHERE o.customer_id = c.id)",
        "SELECT id FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)",
        "SELECT id FROM orders o WHERE amount > (SELECT AVG(x.amount) FROM orders x WHERE x.customer_id = o.customer_id)",
        "SELECT customer_id, id FROM orders WHERE (customer_id, amount) IN (SELECT customer_id, MAX(amount) FROM orders GROUP BY customer_id)",
        "SELECT c.id, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) AS n FROM customers c",
        "SELECT c.id, (SELECT o.amount FROM orders o WHERE o.customer_id = c.id ORDER BY o.order_date DESC, o.id DESC LIMIT 1) FROM customers c",
        "SELECT c.id, t.amount FROM customers c JOIN LATERAL (SELECT amount FROM orders o WHERE o.customer_id = c.id ORDER BY amount DESC LIMIT 1) t ON true",
        "SELECT id FROM customers WHERE id = ANY (SELECT customer_id FROM orders)",
        "SELECT id FROM products WHERE price > (SELECT AVG(price) FROM products)",
        "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM orders GROUP BY customer_id) t",
        "SELECT e.id FROM employees e JOIN departments d ON d.id = e.department_id WHERE e.salary > (SELECT AVG(x.salary) FROM employees x WHERE x.department_id = d.id)",
        "SELECT c.id, COALESCE((SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id), 0) AS spent FROM customers c",
        "DELETE FROM orders WHERE customer_id NOT IN (SELECT id FROM customers)",
        "UPDATE products p SET stock = 0 WHERE NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.product_id = p.id)",
    ]:
        res = analyze_sql(q)
        bad = [(f["topic"], f["title_en"]) for f in res["correct"]["findings"] if f["kind"] in ("error", "wrong")]
        assert not bad, (q, bad)


def test_every_rule_records_where_it_was_proven():
    """Standing rule B: each subquery rule and each engine fact says which engines the lab proved it on."""
    import json

    from app.agents import sqlsubq
    from app.agents.sqlread import fundamentals

    topics = {p[3] for p in P} - {"per_group"}
    assert topics <= set(sqlsubq.PROVEN) and all(set(v) >= {"mariadb", "postgresql"} for v in sqlsubq.PROVEN.values())
    funds = fundamentals()["fundamentals"]
    assert all(any(t in f["topics"] for f in funds.values()) for t in sqlsubq.PROVEN), "every topic has its fundamental"
    for eng in ("mysql", "mariadb", "postgresql"):
        rules = json.load(open(f"app/dbknow/dialects/{eng}.json"))["query_rules"]
        for key in ("limit_in_subquery", "derived_alias"):
            assert rules[key]["proven"] and rules[key]["says"], (eng, key)


@pytest.mark.parametrize("said,kind", [("mariadb", "error"), ("postgresql", None), (None, "check")])
def test_engine_facts_follow_the_database(said, kind):
    """Rule A: LIMIT inside IN is an error on MariaDB, fine on PostgreSQL (said nothing), a check naming both when not known."""
    res = analyze_sql("SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders ORDER BY amount DESC LIMIT 3)", engine=said)
    got = [f["kind"] for f in res["correct"]["findings"] if f["topic"] == "in_limit"]
    assert got == ([kind] if kind else []), (said, got)
