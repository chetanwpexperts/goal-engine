"""Night 3 practice bank: aggregation (COUNT / SUM / AVG, conditional totals, totals at the wrong grain, shares of a total),
plus the aggregation syntax one database has and another doesn't (rule A).

Each problem: what the person wants (in words), the query they wrote (wrong), a correct query, and the database it is for
(None = not said). The correct query is run on MariaDB and PostgreSQL first and both must agree (the verified answer). Then
DBaba gets the wrong query with the words (and the database, when one is said): it must find the mistake under the right
topic, name its fundamental, and its corrected (or 'if that's what you meant') query must return the verified rows.
fix=None: finding it is enough (there is no single right rewrite, e.g. which customer 'has the most' when ties are possible).

LAB below is what each engine really does with the wrong query (reproduced on MariaDB 10.11 and PostgreSQL 16).
"""

import pytest

from app.agents.sql import analyze_sql
from tests.practice import schema as S

# (id, words, wrong query, topic, correct query or None, keep row order?, run only on, database said)
P = [
    # ---- COUNT of CASE ... ELSE 0 / IF(c, 1, 0): 0 is a value, so every row is counted
    ("cc_paid", "number of paid orders", "SELECT COUNT(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid FROM orders",
     "count_case", "SELECT SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) FROM orders", False, None, None),
    ("cc_per_customer", "paid orders of each customer",
     "SELECT customer_id, COUNT(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid FROM orders GROUP BY customer_id",
     "count_case", "SELECT customer_id, SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) FROM orders GROUP BY customer_id", False, None, None),
    ("cc_if_mariadb", "number of orders above 1000 (MariaDB)", "SELECT COUNT(IF(amount > 1000, 1, 0)) FROM orders",
     "count_case", "SELECT COUNT(CASE WHEN amount > 1000 THEN 1 END) FROM orders", False, ["mysql"], "mariadb"),
    ("cc_text", "employees with a commission in each department",
     "SELECT department_id, COUNT(CASE WHEN commission > 0 THEN 'y' ELSE 'n' END) FROM employees GROUP BY department_id",
     "count_case", "SELECT department_id, SUM(CASE WHEN commission > 0 THEN 1 ELSE 0 END) FROM employees GROUP BY department_id",
     False, None, None),
    # ---- COUNT of a comparison: false is a value too
    ("cb_status", "number of shipped orders", "SELECT COUNT(status = 'shipped') FROM orders",
     "count_bool", "SELECT COUNT(*) FROM orders WHERE status = 'shipped'", False, None, None),
    ("cb_stock", "products out of stock in each category", "SELECT category_id, COUNT(stock = 0) FROM products GROUP BY category_id",
     "count_bool", "SELECT category_id, SUM(CASE WHEN stock = 0 THEN 1 ELSE 0 END) FROM products GROUP BY category_id", False, None, None),
    ("cb_null", "customers without a phone number", "SELECT COUNT(phone IS NULL) FROM customers",
     "count_bool", "SELECT COUNT(*) FROM customers WHERE phone IS NULL", False, None, None),
    ("cb_amount", "big orders (over 10000) of each customer", "SELECT customer_id, COUNT(amount > 10000) FROM orders GROUP BY customer_id",
     "count_bool", "SELECT customer_id, SUM(CASE WHEN amount > 10000 THEN 1 ELSE 0 END) FROM orders GROUP BY customer_id", False, None, None),
    # ---- SUM / AVG of a comparison: a number on MySQL/MariaDB, refused on PostgreSQL
    ("sb_pg", "number of paid orders (PostgreSQL)", "SELECT SUM(status = 'paid') FROM orders",
     "sum_bool", "SELECT COUNT(*) FROM orders WHERE status = 'paid'", False, None, "postgresql"),
    ("sb_avg_pg", "share of orders that are paid, on postgres", "SELECT AVG(status = 'paid') FROM orders",
     "sum_bool", "SELECT AVG(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) FROM orders", False, None, "postgresql"),
    ("sb_not_said", "paid orders of each customer", "SELECT customer_id, SUM(status = 'paid') FROM orders GROUP BY customer_id",
     "sum_bool", "SELECT customer_id, SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) FROM orders GROUP BY customer_id", False, None, None),
    # ---- a total inside a total
    ("na_max", "the most orders any one customer has", "SELECT MAX(COUNT(*)) FROM orders GROUP BY customer_id",
     "nested_agg", "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM orders GROUP BY customer_id) t", False, None, None),
    ("na_avg_sum", "average amount spent per customer", "SELECT AVG(SUM(amount)) AS avg_spent FROM orders GROUP BY customer_id",
     "nested_agg", "SELECT AVG(s) FROM (SELECT SUM(amount) AS s FROM orders GROUP BY customer_id) t", False, None, None),
    ("na_items", "the most units in a single order", "SELECT MAX(SUM(quantity)) FROM order_items GROUP BY order_id",
     "nested_agg", "SELECT MAX(q) FROM (SELECT SUM(quantity) AS q FROM order_items GROUP BY order_id) t", False, None, None),
    ("na_which", "which customer has the most orders", "SELECT customer_id, MAX(COUNT(*)) FROM orders GROUP BY customer_id",
     "nested_agg", None, False, None, None),
    # ---- a window in a grouped query
    ("wa_grand", "each status's total and the grand total",
     "SELECT status, SUM(amount) AS total, SUM(amount) OVER () AS grand FROM orders GROUP BY status",
     "window_agg", "SELECT status, SUM(amount), SUM(SUM(amount)) OVER () FROM orders GROUP BY status", False, None, None),
    ("wa_count", "orders per customer and the number of all orders",
     "SELECT customer_id, COUNT(*) AS n, COUNT(id) OVER () AS all_orders FROM orders GROUP BY customer_id",
     "window_agg", "SELECT customer_id, COUNT(*), 8 FROM orders GROUP BY customer_id", False, None, None),
    ("wa_max", "each customer's biggest order and the biggest order of all",
     "SELECT customer_id, MAX(amount), MAX(amount) OVER () FROM orders GROUP BY customer_id",
     "window_agg", "SELECT customer_id, MAX(amount), 80000 FROM orders GROUP BY customer_id", False, None, None),
    ("wa_rank", "statuses ranked by their total amount", "SELECT status, RANK() OVER (ORDER BY amount DESC) FROM orders GROUP BY status",
     "window_agg", None, False, None, None),
    # ---- a SELECT name in HAVING
    ("ha_pg", "customers with more than one order (PostgreSQL)",
     "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id HAVING n > 1",
     "having_alias", "SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id HAVING COUNT(*) > 1", False, None, "postgresql"),
    ("ha_sum_pg", "categories holding more than 10 units (postgres)",
     "SELECT category_id, SUM(stock) AS total_stock FROM products GROUP BY category_id HAVING total_stock > 10",
     "having_alias", "SELECT category_id, SUM(stock) FROM products GROUP BY category_id HAVING SUM(stock) > 10", False, None, "postgresql"),
    ("ha_not_said", "departments paying more than 100000 in salaries",
     "SELECT department_id, SUM(salary) AS bill FROM employees GROUP BY department_id HAVING bill > 100000",
     "having_alias", "SELECT department_id, SUM(salary) FROM employees GROUP BY department_id HAVING SUM(salary) > 100000", False, None, None),
    # ---- COUNT(DISTINCT a, b)
    ("cm_pg", "how many different city and surname pairs (PostgreSQL)", "SELECT COUNT(DISTINCT city, last_name) FROM customers",
     "count_multi", "SELECT COUNT(*) FROM (SELECT DISTINCT city, last_name FROM customers WHERE city IS NOT NULL AND last_name IS NOT NULL) d",
     False, None, "postgresql"),
    ("cm_where_pg", "distinct customer and status pairs among orders over 400 (PostgreSQL)",
     "SELECT COUNT(DISTINCT customer_id, status) FROM orders WHERE amount > 400",
     "count_multi", "SELECT COUNT(*) FROM (SELECT DISTINCT customer_id, status FROM orders WHERE amount > 400) d", False, None, "postgresql"),
    # ---- SUM over a LEFT JOIN: NULL when nothing matched
    ("sn_customers", "total spent by every customer, 0 if nothing",
     "SELECT c.id, SUM(o.amount) AS spent FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id",
     "sum_null", "SELECT c.id, COALESCE(SUM(o.amount), 0) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id",
     False, None, None),
    ("sn_products", "units sold of each product, 0 when never sold",
     "SELECT p.id, SUM(oi.quantity) AS sold FROM products p LEFT JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id",
     "sum_null", "SELECT p.id, COALESCE(SUM(oi.quantity), 0) FROM products p LEFT JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id",
     False, None, None),
    ("sn_departments", "salary bill of every department, 0 for an empty one",
     "SELECT d.name, SUM(e.salary) FROM departments d LEFT JOIN employees e ON e.department_id = d.id GROUP BY d.id, d.name",
     "sum_null", "SELECT d.name, COALESCE(SUM(e.salary), 0) FROM departments d LEFT JOIN employees e ON e.department_id = d.id GROUP BY d.id, d.name",
     False, None, None),
    # ---- SUM(DISTINCT x)
    ("sd_total", "total value of all orders", "SELECT SUM(DISTINCT amount) FROM orders", "sum_distinct", None, False, None, None),
    ("sd_join", "total order amount of each customer",
     "SELECT c.id, SUM(DISTINCT o.amount) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id",
     "sum_distinct", None, False, None, None),
    # ---- AVG skips NULLs
    ("an_discount", "average discount per order, no discount counts as 0", "SELECT AVG(discount) FROM orders",
     "avg_null", "SELECT SUM(COALESCE(discount, 0)) / COUNT(*) FROM orders", False, None, None),
    ("an_commission", "average commission of all employees in each department",
     "SELECT department_id, AVG(commission) FROM employees GROUP BY department_id",
     "avg_null", "SELECT department_id, AVG(COALESCE(commission, 0)) FROM employees GROUP BY department_id", False, None, None),
    # ---- an average of averages
    ("aa_customer", "the average order amount", "SELECT AVG(avg_amt) FROM (SELECT customer_id, AVG(amount) AS avg_amt FROM orders GROUP BY customer_id) t",
     "avg_avg", "SELECT AVG(amount) FROM orders", False, None, None),
    ("aa_cte", "the average product price",
     "WITH c AS (SELECT category_id, AVG(price) AS avg_price FROM products GROUP BY category_id) SELECT AVG(avg_price) FROM c",
     "avg_avg", "SELECT AVG(price) FROM products", False, None, None),
    # ---- what was asked to be counted
    ("cs_customers", "how many customers placed an order", "SELECT COUNT(customer_id) FROM orders",
     "count_said", "SELECT COUNT(DISTINCT customer_id) FROM orders", False, None, None),
    ("cs_paid", "how many customers have a paid order", "SELECT COUNT(*) FROM orders WHERE status = 'paid'",
     "count_said", "SELECT COUNT(DISTINCT customer_id) FROM orders WHERE status = 'paid'", False, None, None),
    ("cs_join", "number of customers in each city who ordered",
     "SELECT c.city, COUNT(*) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.city",
     "count_said", "SELECT c.city, COUNT(DISTINCT c.id) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.city", False, None, None),
    ("cs_hinglish", "kitne products bike hain", "SELECT COUNT(*) FROM order_items",
     "count_said", "SELECT COUNT(DISTINCT product_id) FROM order_items", False, None, None),
    ("cs_suppliers", "how many suppliers have something in stock", "SELECT COUNT(supplier_id) FROM products WHERE stock > 0",
     "count_said", "SELECT COUNT(DISTINCT supplier_id) FROM products WHERE stock > 0", False, None, None),
    # ---- shares of a total
    ("pt_july", "each status's share of July's order value",
     "SELECT status, 100.0 * SUM(amount) / (SELECT SUM(amount) FROM orders) AS pct FROM orders WHERE order_date >= '2024-07-01' "
     "AND order_date < '2024-08-01' GROUP BY status",
     "pct_total", "SELECT status, 100.0 * SUM(amount) / SUM(SUM(amount)) OVER () FROM orders WHERE order_date >= '2024-07-01' "
     "AND order_date < '2024-08-01' GROUP BY status", False, None, None),
    ("pt_methods", "each method's share of the card and upi payments",
     "SELECT method, 100.0 * COUNT(*) / (SELECT COUNT(*) FROM payments) FROM payments WHERE method IN ('card', 'upi') GROUP BY method",
     "pct_total", "SELECT method, 100.0 * COUNT(*) / SUM(COUNT(*)) OVER () FROM payments WHERE method IN ('card', 'upi') GROUP BY method",
     False, None, None),
]

# what each engine does with the wrong query: 'rows' (runs; the rows are wrong), 'error' (refused)
LAB = {
    "cc_paid": ("rows", "rows"),            # 8, not 4: the 0s are counted
    "cb_status": ("rows", "rows"),          # 8, not 2
    "cb_null": ("rows", "rows"),            # 8, not 4
    "sb_pg": ("rows", "error"),             # MariaDB 4; PostgreSQL 42883 function sum(boolean) does not exist
    "sb_avg_pg": ("rows", "error"),         # MariaDB 0.5; PostgreSQL 42883 function avg(boolean) does not exist
    "na_max": ("error", "error"),           # MariaDB 1111 Invalid use of group function; PostgreSQL 42803 cannot be nested
    "na_which": ("error", "error"),
    "wa_grand": ("rows", "error"),          # MariaDB 103000 (one row's amount per group, summed); PostgreSQL 42803
    "wa_count": ("rows", "error"),          # MariaDB 5 (the groups), not 8
    "wa_rank": ("rows", "error"),           # MariaDB ranks by one arbitrary row's amount
    "ha_pg": ("rows", "error"),             # PostgreSQL 42703 column "n" does not exist
    "ha_sum_pg": ("rows", "error"),
    "cm_pg": ("rows", "error"),             # PostgreSQL 42883 function count(character varying, character varying) does not exist
    "sn_customers": ("rows", "rows"),       # NULL for customers 4, 5, 7
    "sd_total": ("rows", "rows"),           # 184100, not 184600: the two 500s count once
    "an_discount": ("rows", "rows"),        # 516.67 (3 orders with a discount), not 193.75
    "aa_customer": ("rows", "rows"),        # 24990, not 23075
    "cs_customers": ("rows", "rows"),       # 8 orders, not 5 customers
    "pt_july": ("rows", "rows"),            # shares add up to about 42.6%, not 100%
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
        assert _rows(eng, by["cc_paid"][2], False) == [("8",)]
        assert _rows(eng, by["cb_null"][2], False) == [("8",)]
        assert _rows(eng, by["sd_total"][2], False) == [("184100",)] != _rows(eng, "SELECT SUM(amount) FROM orders", False)
        assert _rows(eng, by["an_discount"][2], False) == [("516.67",)]
        assert _rows(eng, by["aa_customer"][2], False) == [("24990",)]
        assert _rows(eng, by["cs_customers"][2], False) == [("8",)]
        assert ("4", "NULL") in _rows(eng, by["sn_customers"][2], False)
        july = sum(float(r[1]) for r in _rows(eng, by["pt_july"][2], False))
        assert 40 < july < 45
    m = engines["mysql"]
    assert {r[2] for r in _rows(m, by["wa_grand"][2], False)} == {"103000"}       # MariaDB's silent wrong grand total
    assert {r[2] for r in _rows(m, by["wa_count"][2], False)} == {"5"}


VALID = [
    "SELECT SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid FROM orders",
    "SELECT COUNT(CASE WHEN status = 'paid' THEN 1 END) FROM orders",
    "SELECT COUNT(CASE WHEN status = 'paid' THEN 1 ELSE NULL END) FROM orders",
    "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM orders GROUP BY customer_id) t",
    "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id HAVING COUNT(*) > 1",
    "SELECT status, SUM(amount), SUM(SUM(amount)) OVER () FROM orders GROUP BY status",
    "SELECT status, RANK() OVER (ORDER BY SUM(amount) DESC) FROM orders GROUP BY status",
    "SELECT status, SUM(COUNT(*)) OVER () FROM orders GROUP BY status",
    "SELECT COUNT(DISTINCT customer_id) FROM orders",
    "SELECT c.id, COALESCE(SUM(o.amount), 0) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id",
    "SELECT AVG(COALESCE(discount, 0)) FROM orders",
    "SELECT AVG(amount) FROM orders",
    "SELECT id, amount, SUM(amount) OVER (PARTITION BY customer_id) FROM orders",
    "SELECT status, 100.0 * SUM(amount) / (SELECT SUM(amount) FROM orders) FROM orders GROUP BY status",
    "SELECT customer_id, SUM(amount) FROM orders WHERE amount > 0 GROUP BY customer_id",
    "SELECT COUNT(*) FROM orders WHERE amount > 1000",
]


def test_valid_totals_get_no_findings():
    """The aggregation checks stay quiet on totals that are right (no false alarms)."""
    from app.agents import sqlaggs

    for sql in VALID:
        res = analyze_sql(sql)
        bad = [(f["kind"], f["topic"], f["title_en"]) for f in res["correct"]["findings"]
               if f["topic"] in sqlaggs.PROVEN or f["kind"] in ("error", "wrong")]
        assert not bad, (sql, bad)


def test_the_named_database_decides():
    """Rule A: what one database allows and another refuses is said for the database named, and only for it."""
    for q in ("SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id HAVING n > 1",
              "SELECT SUM(status = 'paid') FROM orders", "SELECT COUNT(DISTINCT city, last_name) FROM customers"):
        on_my = [f for f in analyze_sql(q, engine="mysql")["correct"]["findings"] if f["topic"] in ("having_alias", "sum_bool", "count_multi")]
        on_maria = [f for f in analyze_sql(q, engine="mariadb")["correct"]["findings"] if f["topic"] in ("having_alias", "sum_bool", "count_multi")]
        on_pg = [f for f in analyze_sql(q, engine="postgresql")["correct"]["findings"] if f["topic"] in ("having_alias", "sum_bool", "count_multi")]
        unknown = [f for f in analyze_sql(q)["correct"]["findings"] if f["topic"] in ("having_alias", "sum_bool", "count_multi")]
        assert not on_my and not on_maria, q
        assert on_pg and on_pg[0]["kind"] == "error" and "PostgreSQL" in on_pg[0]["fix_en"], q
        assert unknown and unknown[0]["kind"] == "check", q
    # the window over a plain column: silently wrong on MariaDB, refused on PostgreSQL
    q = "SELECT status, SUM(amount) OVER () FROM orders GROUP BY status"
    assert [f["kind"] for f in analyze_sql(q, engine="mariadb")["correct"]["findings"] if f["topic"] == "window_agg"] == ["wrong"]
    assert [f["kind"] for f in analyze_sql(q, engine="postgresql")["correct"]["findings"] if f["topic"] == "window_agg"] == ["error"]


def test_every_rule_says_where_it_was_proven():
    """Standing rule B: each aggregation rule records the engines the lab proved it on, the engine facts live in the
    dialect files, and every topic belongs to a fundamental."""
    import json
    from pathlib import Path

    from app.agents import sqlaggs
    from app.agents.sqlread import fundamentals

    assert all(set(v) >= {"mariadb", "postgresql"} for v in sqlaggs.PROVEN.values())
    topics = {t for f in fundamentals()["fundamentals"].values() for t in f["topics"]}
    assert set(sqlaggs.PROVEN) <= topics
    d = Path(sqlaggs.__file__).resolve().parents[1] / "dbknow" / "dialects"
    for fam in ("mysql", "mariadb", "postgresql"):
        rules = json.loads((d / f"{fam}.json").read_text(encoding="utf-8"))["query_rules"]
        for key in ("sum_boolean", "having_alias", "count_distinct_multi", "window_over_ungrouped"):
            assert rules[key]["says"] and rules[key]["proven"], (fam, key)
