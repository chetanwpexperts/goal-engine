"""Night 14 practice bank: safe rewrites with a proof that they return the same rows (a correlated total totalled once and
joined, IN <-> EXISTS, IN -> JOIN through DISTINCT, a calculation named once, and the contracts of the older same-rows
rewrites: the repeated subquery, UNION ALL, HAVING -> WHERE, keyset paging).

Each problem is a requirement (with what the person asked for), a query, the database, the finding DBaba must give and how
its answer is proven on MariaDB and PostgreSQL (practice schema):
- 'same'  : the finding's own rewrite returns exactly the rows of the query as written (in the same order when there is an
            ORDER BY), on both databases, and the finding carries its contract (matching, NULLs, duplicates, ordering, grain).
- 'quiet' : no rewrite of tonight may be offered: the forms would differ (NOT IN vs NOT EXISTS with NULLs, IN as a value in
            SELECT, a link through two columns ...), or nothing was asked.
NAIVE is what the careless rewrite gives instead (the reason each contract exists), re-run on both databases.
"""

import re

import pytest

from app.agents import sqlequiv
from app.agents.sql import analyze_sql
from tests.practice import schema as S

TONIGHT = {"preagg_subquery", "to_exists", "to_in", "to_join", "expr_once"}
CNT = "(SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id)"
TOT = "(SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id)"
# (id, requirement, query, engine, topic, proof)
P = [
    # ---- a correlated total totalled once and joined
    ("pre_count", "number of orders of each customer", f"SELECT c.id, {CNT} AS n FROM customers c ORDER BY c.id", "postgresql",
     "preagg_subquery", "same"),
    ("pre_count_maria", "number of orders of each customer", f"SELECT c.id, {CNT} AS n FROM customers c ORDER BY c.id", "mariadb",
     "preagg_subquery", "same"),
    ("pre_max", "each customer's biggest order", "SELECT c.id, (SELECT MAX(o.amount) FROM orders o WHERE o.customer_id = c.id) AS top "
     "FROM customers c ORDER BY c.id", "mariadb", "preagg_subquery", "same"),
    ("pre_min_date", "each customer's first order date", "SELECT c.id, (SELECT MIN(o.order_date) FROM orders o WHERE o.customer_id = c.id) "
     "AS first_order FROM customers c ORDER BY c.id", "postgresql", "preagg_subquery", "same"),
    ("pre_avg", "average order of each customer", "SELECT c.id, (SELECT AVG(o.amount) FROM orders o WHERE o.customer_id = c.id) AS avg_amt "
     "FROM customers c ORDER BY c.id", "mariadb", "preagg_subquery", "same"),
    ("pre_filter_inside", "paid orders of each customer", "SELECT c.id, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id AND "
     "o.status = 'paid') AS paid FROM customers c ORDER BY c.id", "postgresql", "preagg_subquery", "same"),
    ("pre_two", "orders and units of each customer", f"SELECT c.id, {CNT} AS n, (SELECT MAX(o.order_date) FROM orders o "
     "WHERE o.customer_id = c.id) AS last_order FROM customers c ORDER BY c.id", "mariadb", "preagg_subquery", "same"),
    ("pre_items", "lines in each order", "SELECT o.id, (SELECT COUNT(i.id) FROM order_items i WHERE i.order_id = o.id) AS n_lines "
     "FROM orders o ORDER BY o.id", "postgresql", "preagg_subquery", "same"),
    ("pre_units", "units sold of each product", "SELECT p.id, p.name, (SELECT SUM(i.quantity) FROM order_items i WHERE i.product_id = p.id) "
     "AS sold FROM products p ORDER BY p.id", "mariadb", "preagg_subquery", "same"),
    ("pre_headcount", "people in each department", "SELECT d.name, (SELECT COUNT(*) FROM employees e WHERE e.department_id = d.id) AS people "
     "FROM departments d ORDER BY d.name", "postgresql", "preagg_subquery", "same"),
    ("pre_nullable_key", "products from the same supplier as each product", "SELECT p.id, (SELECT COUNT(*) FROM products q "
     "WHERE q.supplier_id = p.supplier_id) AS same_supplier FROM products p ORDER BY p.id", "mariadb", "preagg_subquery", "same"),
    ("pre_where", "customers with more than one order", f"SELECT c.id, {CNT} AS n FROM customers c WHERE {CNT} > 1 ORDER BY c.id",
     "postgresql", "preagg_subquery", "same"),
    ("pre_where_none", "customers with no orders", f"SELECT c.id FROM customers c WHERE {CNT} = 0 ORDER BY c.id", "mariadb",
     "preagg_subquery", "same"),
    ("pre_order", "customers by total spent", f"SELECT c.id, {TOT} AS spent FROM customers c ORDER BY {TOT} DESC, c.id", "postgresql",
     "preagg_subquery", "same"),
    ("pre_outer_join", "each order with its customer's city and the customer's order count",
     f"SELECT o.id, c.city, {CNT} AS n FROM orders o JOIN customers c ON c.id = o.customer_id ORDER BY o.id", "mariadb",
     "preagg_subquery", "same"),
    ("pre_limit", "the three customers with most orders", f"SELECT c.id, {CNT} AS n FROM customers c ORDER BY {CNT} DESC, c.id LIMIT 3",
     "postgresql", "preagg_subquery", "same"),
    ("pre_payments", "amount paid on each order", "SELECT o.id, o.amount, (SELECT SUM(p.amount) FROM payments p WHERE p.order_id = o.id) "
     "AS paid FROM orders o ORDER BY o.id", "mariadb", "preagg_subquery", "same"),
    ("q_pre_two_links", "orders of the same customer on the same day",
     "SELECT o.id, (SELECT COUNT(*) FROM orders x WHERE x.customer_id = o.customer_id AND x.order_date = o.order_date) AS same_day "
     "FROM orders o ORDER BY o.id", "postgresql", None, "quiet"),
    ("q_pre_outer_value", "orders bigger than this one, per customer",
     "SELECT o.id, (SELECT COUNT(*) FROM orders x WHERE x.customer_id = o.customer_id AND x.amount > o.amount) AS bigger "
     "FROM orders o ORDER BY o.id", "mariadb", None, "quiet"),
    ("q_pre_grouped", "orders per city with the city's customers",
     "SELECT c.city, COUNT(*) AS n FROM customers c GROUP BY c.city ORDER BY c.city", "postgresql", None, "quiet"),
    # ---- the IN / EXISTS / JOIN form the person asked for
    ("in_to_exists", "customers who placed an order, rewrite it with EXISTS",
     "SELECT c.id FROM customers c WHERE c.id IN (SELECT o.customer_id FROM orders o) ORDER BY c.id", "postgresql", "to_exists", "same"),
    ("in_to_exists_filter", "Pune customers with a paid order, use EXISTS instead",
     "SELECT c.id, c.first_name FROM customers c WHERE c.city = 'Pune' AND c.id IN (SELECT o.customer_id FROM orders o "
     "WHERE o.status = 'paid') ORDER BY c.id", "mariadb", "to_exists", "same"),
    ("in_to_exists_hi", "jin products ki return hui, IN ko EXISTS mein badlo",
     "SELECT p.id FROM products p WHERE p.id IN (SELECT i.product_id FROM order_items i JOIN returns r ON r.order_item_id = i.id) "
     "ORDER BY p.id", "postgresql", None, "quiet"),
    ("in_to_exists_bare", "departments that have people, convert to exists",
     "SELECT id, name FROM departments WHERE id IN (SELECT department_id FROM employees) ORDER BY id", "mariadb", "to_exists", "same"),
    ("in_to_exists_nullable", "products whose supplier is in India, with EXISTS",
     "SELECT p.id FROM products p WHERE p.supplier_id IN (SELECT s.id FROM suppliers s WHERE s.country = 'India') ORDER BY p.id",
     "postgresql", "to_exists", "same"),
    ("q_not_in_to_exists", "departments without people, rewrite with NOT EXISTS",
     "SELECT d.id FROM departments d WHERE d.id NOT IN (SELECT e.department_id FROM employees e) ORDER BY d.id", "mariadb", None, "quiet"),
    ("q_in_as_value", "for each customer, whether they ordered, use EXISTS",
     "SELECT c.id, c.id IN (SELECT o.customer_id FROM orders o) AS ordered FROM customers c ORDER BY c.id", "postgresql", None, "quiet"),
    ("exists_to_in", "customers with a shipped order, isko IN se likho",
     "SELECT c.id FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id AND o.status = 'shipped') ORDER BY c.id",
     "mariadb", "to_in", "same"),
    ("exists_to_in_en", "employees who manage someone, rewrite it using IN",
     "SELECT m.id FROM employees m WHERE EXISTS (SELECT 1 FROM employees e WHERE e.manager_id = m.id) ORDER BY m.id", "postgresql",
     "to_in", "same"),
    ("q_exists_two_links", "orders paid the same day they were placed, use IN",
     "SELECT o.id FROM orders o WHERE EXISTS (SELECT 1 FROM payments p WHERE p.order_id = o.id AND p.paid_at = o.order_date) ORDER BY o.id",
     "mariadb", None, "quiet"),
    ("in_to_join", "customers with a paid order, write it as a join",
     "SELECT c.id, c.first_name FROM customers c WHERE c.id IN (SELECT o.customer_id FROM orders o WHERE o.status = 'paid') ORDER BY c.id",
     "postgresql", "to_join", "same"),
    ("in_to_join_more", "Delhi customers who ordered, join se likho",
     "SELECT c.id FROM customers c WHERE c.city = 'Delhi' AND c.id IN (SELECT o.customer_id FROM orders o) ORDER BY c.id", "mariadb",
     "to_join", "same"),
    ("in_to_join_products", "products that were ever ordered, convert the IN to a JOIN",
     "SELECT p.id, p.name FROM products p WHERE p.id IN (SELECT i.product_id FROM order_items i) ORDER BY p.id", "postgresql", "to_join",
     "same"),
    ("q_no_ask", "customers who placed an order", "SELECT c.id FROM customers c WHERE c.id IN (SELECT o.customer_id FROM orders o) "
     "ORDER BY c.id", "mariadb", None, "quiet"),
    # ---- a calculation named once
    ("expr_net", "orders by net amount", "SELECT id, amount - COALESCE(discount, 0) AS net FROM orders ORDER BY amount - COALESCE(discount, 0) "
     "DESC, id", "postgresql", "expr_once", "same"),
    ("expr_line", "order lines by line value", "SELECT i.id, i.quantity * i.unit_price AS line_value FROM order_items i "
     "ORDER BY i.quantity * i.unit_price DESC, i.id", "mariadb", "expr_once", "same"),
    ("expr_name", "customers by full name", "SELECT id, CONCAT(first_name, ' ', last_name) AS full_name FROM customers "
     "ORDER BY CONCAT(first_name, ' ', last_name), id", "postgresql", "expr_once", "same"),
    ("expr_null", "employees by total pay", "SELECT id, salary + commission AS pay FROM employees ORDER BY salary + commission DESC, id",
     "mariadb", "expr_once", "same"),
    ("q_expr_col_name", "orders by net amount", "SELECT id, amount - COALESCE(discount, 0) AS amount FROM orders "
     "ORDER BY amount - COALESCE(discount, 0) DESC, id", "postgresql", None, "quiet"),
    ("q_expr_rand", "a random order of products", "SELECT id, RAND() * 10 AS r FROM products ORDER BY RAND() * 10", "mariadb", None, "quiet"),
]

# what the careless rewrite gives instead: (careful, careless) row counts are different on both engines
NAIVE = {
    # IN -> a plain JOIN repeats a customer once per order
    "in_vs_plain_join": ("SELECT c.id FROM customers c WHERE c.id IN (SELECT o.customer_id FROM orders o)",
                         "SELECT c.id FROM customers c JOIN orders o ON o.customer_id = c.id"),
    # NOT IN with a NULL in the list keeps nothing; NOT EXISTS keeps the department with nobody
    "not_in_vs_not_exists": ("SELECT d.id FROM departments d WHERE NOT EXISTS (SELECT 1 FROM employees e WHERE e.department_id = d.id)",
                             "SELECT d.id FROM departments d WHERE d.id NOT IN (SELECT e.department_id FROM employees e)"),
    # a LEFT JOIN to totals without COALESCE gives NULL where the correlated COUNT gave 0
    "count_without_coalesce": (f"SELECT c.id FROM customers c WHERE {CNT} = 0",
                               "SELECT c.id FROM customers c LEFT JOIN (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id) x "
                               "ON x.customer_id = c.id WHERE x.n = 0"),
    # a JOIN to the child table without totalling first repeats the parent once per child
    "join_without_group": (f"SELECT c.id, {CNT} AS n FROM customers c",
                           "SELECT c.id, o.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id"),
}
LAB = {
    "corr_count_none": ("0", "0"),         # a correlated COUNT over no rows
    "left_join_count_none": ("NULL", "NULL"),  # the same after a LEFT JOIN to grouped totals
    "corr_sum_none": ("NULL", "NULL"),     # SUM over no rows, both ways
    "order_by_alias": ("8", "8"),          # ORDER BY a SELECT name works on both
    "where_alias": ("1054", "42703"),      # WHERE can't
    "alias_beats_column": ("3", "3"),      # ORDER BY prefers the SELECT name over a column of the same name (first id)
}
LAB_SQL = {
    "corr_count_none": f"SELECT {CNT} FROM customers c WHERE c.id = 4",
    "left_join_count_none": "SELECT x.n FROM customers c LEFT JOIN (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id) x "
                            "ON x.customer_id = c.id WHERE c.id = 4",
    "corr_sum_none": f"SELECT {TOT} FROM customers c WHERE c.id = 4",
    "order_by_alias": "SELECT COUNT(*) FROM (SELECT id, amount - COALESCE(discount, 0) AS net FROM orders ORDER BY net DESC) t",
    "where_alias": "SELECT id, amount - COALESCE(discount, 0) AS net FROM orders WHERE net > 1000",
    "alias_beats_column": "SELECT id FROM (SELECT id, 0 - amount AS amount FROM orders ORDER BY amount LIMIT 1) t",
}


@pytest.fixture(scope="module")
def engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


def _kind(name):
    return "mariadb" if name == "mysql" else "postgresql"


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)
    assert {p[5] for p in P} == {"same", "quiet"}
    assert {p[4] for p in P if p[4]} >= TONIGHT


@pytest.mark.parametrize("pid,words,q,engine,topic,proof", P, ids=[p[0] for p in P])
def test_dbaba_rewrites_with_proof(engines, pid, words, q, engine, topic, proof):
    res = analyze_sql(f"{q};\n\n{words}", engine=engine)
    fs = res["correct"]["findings"]
    if proof == "quiet":
        said = [(f["topic"], f["title_en"]) for f in fs if f["topic"] in TONIGHT or f["kind"] in ("error", "wrong")]
        assert not said, (pid, said)
        return
    found = [f for f in fs if f["topic"] == topic]
    assert found, (pid, [(f["topic"], f["title_en"]) for f in fs])
    f = found[0]
    assert f["confidence"]["class"] == "needs_execution", (pid, f["confidence"])
    assert set(f["same"]["keeps"]) == set(sqlequiv.ASPECTS) and f["same"]["proof"] == "query", pid
    assert not re.search(r"\b(is|will be) (slow|a bottleneck|faster)\b", f["title_en"] + f["fix_en"], re.I)
    for name, eng in engines.items():
        other = res if _kind(name) == engine else analyze_sql(f"{q};\n\n{words}", engine=_kind(name))
        g = next(x for x in other["correct"]["findings"] if x["topic"] == topic)
        sql = g["rewrite"]
        assert sql and sql.strip().rstrip(";") != q, (pid, name)
        ordered = bool(re.search(r"\border\s+by\b(?![^()]*\))", q, re.I))
        assert S.rows(eng, sql.rstrip().rstrip(";"), ordered) == S.rows(eng, q, ordered), (pid, name, sql)


def test_the_careless_rewrites_really_differ(engines):
    for key, (good, bad) in NAIVE.items():
        for name, eng in engines.items():
            assert S.rows(eng, good) != S.rows(eng, bad), (key, name)


def test_the_lab_table_still_holds(engines):
    def one(eng, sql):
        S.build(eng)
        try:
            return str(S.norm(eng.run(sql)[0][0]))
        except Exception as exc:  # noqa: BLE001
            eng.rollback()
            a = getattr(exc, "args", ())
            return str(a[0]) if a and isinstance(a[0], int) else (getattr(exc, "sqlstate", None) or "err")
    for key, (maria, pg) in LAB.items():
        got = (one(engines["mysql"], LAB_SQL[key]), one(engines["postgres"], LAB_SQL[key]))
        assert got == (maria, pg), (key, got)


def test_the_suggested_form_and_the_report_say_why_it_is_the_same():
    q = f"SELECT c.id, {CNT} AS n FROM customers c ORDER BY {CNT} DESC, c.id"
    res = analyze_sql(q, engine="postgresql")
    assert res.get("suggested_kind") == "speed" and "COALESCE(pre1.n, 0)" in res["suggested"]
    assert res["equivalence"]["proof"] == "query" and "preagg_subquery" in res["equivalence"]["topics"]
    rep = res["query_only"]["en"]
    assert "Why the rewrite returns the same rows" in rep and "COALESCE" in rep and "This follows from the query alone" in rep
    hi = res["query_only"]["hi"]
    assert "Ye wahi rows kyun deti hai" in hi


def test_a_contract_that_needs_the_tables_says_which_fact():
    q = "SELECT id, 'customer' AS kind FROM customers UNION SELECT id, 'supplier' FROM suppliers ORDER BY kind, id"
    res = analyze_sql(q, engine="mariadb")
    f = next(f for f in res["correct"]["findings"] if f["topic"] == "union_all")
    assert f["same"]["proof"] == "schema" and "no repeated rows of its own" in f["same"]["holds_if"][0]["en"]
    k = sqlequiv.contract("keyset")
    assert k["proof"] == "schema" and any("unique" in h["en"] for h in k["holds_if"])


def test_every_rewrite_records_where_it_was_proven_and_its_fundamental():
    import json

    assert all(v == ["mariadb", "postgresql"] for v in sqlequiv.PROVEN.values()) and set(sqlequiv.PROVEN) >= TONIGHT
    for t, c in sqlequiv.contracts()["rewrites"].items():
        assert set(c["keeps"]) == set(sqlequiv.ASPECTS) and c["proven"] == ["mariadb", "postgresql"], t
        assert all(v["en"] and v["hi"] for v in c["keeps"].values()), t
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert TONIGHT <= set(funds["equivalence"]["topics"])
    for eng in ("mysql", "mariadb", "postgresql"):
        rules = json.load(open(f"app/dbknow/dialects/{eng}.json"))["query_rules"]
        for key in ("preaggregate", "in_exists", "order_by_alias"):
            assert rules[key]["says"] and rules[key]["proven"], (eng, key)
