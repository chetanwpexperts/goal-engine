"""Night 13 practice bank: the remaining structural checks (correlation, the same subquery twice, UNION vs UNION ALL, filters
that could run earlier, a CTE read twice, SELECT * across a join, a join with no ON or an ON that doesn't connect).

Each problem is a requirement, a query, the database, the finding DBaba must give (with its class) and how its answer is proven on
MariaDB and PostgreSQL (practice schema):
- 'fix'   : PROVEN. The corrected query returns the requirement's rows (the truth) on both databases.
- 'offer' : a risk that depends on what was meant. The query offered returns the truth on both databases.
- 'same'  : a speed risk (or UNION ALL where repeats can't exist). The form offered returns exactly the rows of the query as
            written, on both databases.
- 'found' : found, with its class (the rewrite needs a choice only the person can make, or the table structure).
- 'quiet' : a correct query tonight's checks must not complain about (none of tonight's topics, no error / wrong).
LAB is what each engine really does with tonight's structures (MariaDB 10.11 / PostgreSQL 16), re-run here.
"""

import re

import pytest

from app.agents import sqlstruct
from app.agents.sql import analyze_sql
from tests.practice import schema as S

TONIGHT = {"scalar_uncorr", "corr_inner", "corr_key", "repeat_subquery", "same_subquery", "union_all", "union_all_dups", "having_key",
           "cte_filter_late", "star_dup", "star_join"}
REP_MAX = "(SELECT MAX(o.amount) FROM orders o WHERE o.customer_id = c.id)"
REP_CNT = "(SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id)"
DEPT_AVG = "(SELECT AVG(x.salary) FROM employees x WHERE x.department_id = e.department_id)"
TWICE = ("WITH x AS (SELECT customer_id, SUM(amount) AS t FROM orders GROUP BY customer_id) SELECT a.customer_id, a.t, b.t AS t2 "
         "FROM x a JOIN x b ON b.customer_id = a.customer_id WHERE a.customer_id = 3")
# (id, requirement, query, engine, topic, class, proof, truth)
P = [
    # ---- a total in SELECT that never names the outer row: every row gets the grand total
    ("uncorr_sum", "each customer's total order amount", "SELECT c.id, (SELECT SUM(o.amount) FROM orders o) AS total FROM customers c "
     "ORDER BY c.id", "postgresql", "scalar_uncorr", "likely_risk", "offer",
     "SELECT c.id, (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id) AS total FROM customers c ORDER BY c.id"),
    ("uncorr_count", "number of orders of each customer", "SELECT c.id, (SELECT COUNT(*) FROM orders o) AS n FROM customers c ORDER BY c.id",
     "mariadb", "scalar_uncorr", "likely_risk", "offer",
     "SELECT c.id, COUNT(o.id) AS n FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id ORDER BY c.id"),
    ("uncorr_max", "each customer's biggest order", "SELECT c.id, (SELECT MAX(o.amount) FROM orders o) AS top FROM customers c ORDER BY c.id",
     "mariadb", "scalar_uncorr", "likely_risk", "offer",
     "SELECT c.id, MAX(o.amount) AS top FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id ORDER BY c.id"),
    ("uncorr_where", "paid orders of each customer", "SELECT c.id, (SELECT COUNT(*) FROM orders o WHERE o.status = 'paid') AS paid "
     "FROM customers c ORDER BY c.id", "postgresql", "scalar_uncorr", "likely_risk", "offer",
     "SELECT c.id, COUNT(o.id) AS paid FROM customers c LEFT JOIN orders o ON o.customer_id = c.id AND o.status = 'paid' GROUP BY c.id "
     "ORDER BY c.id"),
    ("uncorr_units", "units sold of each product", "SELECT p.id, (SELECT SUM(i.quantity) FROM order_items i) AS sold FROM products p "
     "ORDER BY p.id", "mariadb", "scalar_uncorr", "likely_risk", "offer",
     "SELECT p.id, SUM(i.quantity) AS sold FROM products p LEFT JOIN order_items i ON i.product_id = p.id GROUP BY p.id ORDER BY p.id"),
    ("uncorr_headcount", "headcount of each department", "SELECT d.id, (SELECT COUNT(*) FROM employees e) AS people FROM departments d "
     "ORDER BY d.id", "postgresql", "scalar_uncorr", "likely_risk", "offer",
     "SELECT d.id, COUNT(e.id) AS people FROM departments d LEFT JOIN employees e ON e.department_id = d.id GROUP BY d.id ORDER BY d.id"),
    ("q_share", "each order's share of all sales", "SELECT o.id, o.amount * 100 / (SELECT SUM(amount) FROM orders) AS pct FROM orders o "
     "ORDER BY o.id", "postgresql", None, None, "quiet", None),
    # ---- the condition compares the inner table with itself
    ("inner_sum", "each customer's total", "SELECT c.id, (SELECT MAX(o.amount) FROM orders o WHERE o.customer_id = o.id) AS top "
     "FROM customers c ORDER BY c.id", "postgresql", "corr_inner", "likely_risk", "offer",
     "SELECT c.id, MAX(o.amount) AS top FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id ORDER BY c.id"),
    ("inner_bare", "number of items in each order", "SELECT o.id, (SELECT COUNT(*) FROM order_items WHERE order_id = id) AS items "
     "FROM orders o ORDER BY o.id", "mariadb", "corr_inner", "likely_risk", "offer",
     "SELECT o.id, COUNT(i.id) AS items FROM orders o LEFT JOIN order_items i ON i.order_id = o.id GROUP BY o.id ORDER BY o.id"),
    ("inner_in", "customers with a paid order", "SELECT c.id FROM customers c WHERE c.id IN (SELECT o.customer_id FROM orders o "
     "WHERE o.status = 'paid' AND o.customer_id = o.id) ORDER BY c.id", "postgresql", "corr_inner", "likely_risk", "found", None),
    # ---- a link column compared with the wrong outer table
    ("key_wrong_col", "the latest payment date of each order", "SELECT o.id, (SELECT MAX(p.paid_at) FROM payments p "
     "WHERE p.order_id = o.customer_id) AS last_paid FROM orders o ORDER BY o.id", "mariadb", "corr_key", "likely_risk", "offer",
     "SELECT o.id, MAX(p.paid_at) AS last_paid FROM orders o LEFT JOIN payments p ON p.order_id = o.id GROUP BY o.id ORDER BY o.id"),
    ("key_wrong_table", "the latest payment of each order with its customer", "SELECT c.id AS cid, o.id AS oid, (SELECT MAX(p.paid_at) "
     "FROM payments p WHERE p.order_id = c.id) AS last_paid FROM customers c JOIN orders o ON o.customer_id = c.id ORDER BY o.id",
     "postgresql", "corr_key", "likely_risk", "offer",
     "SELECT o.customer_id AS cid, o.id AS oid, (SELECT MAX(p.paid_at) FROM payments p WHERE p.order_id = o.id) AS last_paid "
     "FROM orders o ORDER BY o.id"),
    ("key_items", "how many returns each order line had", "SELECT i.id, (SELECT COUNT(*) FROM returns r WHERE r.order_item_id = i.order_id) "
     "AS returned FROM order_items i ORDER BY i.id", "mariadb", "corr_key", "likely_risk", "offer",
     "SELECT i.id, COUNT(r.id) AS returned FROM order_items i LEFT JOIN returns r ON r.order_item_id = i.id GROUP BY i.id ORDER BY i.id"),
    ("q_key_right", "the latest payment date of each order", "SELECT o.id, (SELECT MAX(p.paid_at) FROM payments p WHERE p.order_id = o.id) "
     "AS last_paid FROM orders o ORDER BY o.id", "mariadb", None, None, "quiet",
     "SELECT o.id, MAX(p.paid_at) AS last_paid FROM orders o LEFT JOIN payments p ON p.order_id = o.id GROUP BY o.id ORDER BY o.id"),
    # ---- the same correlated subquery written again (worked out once, same rows)
    ("rep_where_order", "customers whose biggest order is over 1000, biggest first",
     f"SELECT c.id, {REP_MAX} AS top FROM customers c WHERE {REP_MAX} > 1000 ORDER BY {REP_MAX} DESC, c.id", "postgresql",
     "repeat_subquery", "needs_execution", "same", None),
    ("rep_order", "customers by number of orders", f"SELECT c.id, {REP_CNT} AS n FROM customers c ORDER BY {REP_CNT} DESC, c.id",
     "mariadb", "repeat_subquery", "needs_execution", "same", None),
    ("rep_where", "customers with more than one order", f"SELECT c.id, {REP_CNT} AS n FROM customers c WHERE {REP_CNT} > 1", "postgresql",
     "repeat_subquery", "needs_execution", "same", None),
    ("rep_dept_avg", "employees paid above their department's average",
     f"SELECT e.id, e.salary, {DEPT_AVG} AS dept_avg FROM employees e WHERE e.salary > {DEPT_AVG} ORDER BY e.id", "mariadb", "repeat_subquery",
     "needs_execution", "same", None),
    ("rep_keep_filter", "Pune customers whose biggest order is over 1000",
     f"SELECT c.id, {REP_MAX} AS top FROM customers c WHERE c.city = 'Pune' AND {REP_MAX} > 1000 ORDER BY c.id", "mariadb",
     "repeat_subquery", "needs_execution", "same", None),
    # ---- the same uncorrelated subquery written again (one-row CROSS JOIN, same rows)
    ("same_avg", "products priced between the average and twice the average",
     "SELECT id FROM products WHERE price > (SELECT AVG(price) FROM products) AND price < 2 * (SELECT AVG(price) FROM products) ORDER BY id",
     "postgresql", "same_subquery", "needs_execution", "same", None),
    ("same_min", "how much more than the lowest salary people earn, for those above it",
     "SELECT id, salary - (SELECT MIN(salary) FROM employees) AS above_min FROM employees WHERE salary > (SELECT MIN(salary) FROM employees) "
     "ORDER BY id", "mariadb", "same_subquery", "needs_execution", "same", None),
    ("same_or", "big orders or orders with big shipping",
     "SELECT id FROM orders WHERE amount >= (SELECT AVG(amount) FROM orders) OR shipping > (SELECT AVG(amount) FROM orders) / 1000 "
     "ORDER BY id", "postgresql", "same_subquery", "needs_execution", "same", None),
    # ---- UNION where the parts can't share a row; UNION ALL where each was wanted once
    ("union_ids", "every customer and supplier id with what it is",
     "SELECT id, 'customer' AS kind FROM customers UNION SELECT id, 'supplier' FROM suppliers ORDER BY kind, id", "postgresql",
     "union_all", "needs_schema", "same", None),
    ("union_distinct", "the places customers and suppliers are in, each once per kind",
     "SELECT DISTINCT city AS place, 'customer' AS kind FROM customers WHERE city IS NOT NULL UNION SELECT DISTINCT country, 'supplier' "
     "FROM suppliers ORDER BY kind, place", "mariadb", "union_all", "needs_schema", "same", None),
    ("union_tag_repeats", "the cities of customers and the countries of suppliers",
     "SELECT city, 'customer' AS kind FROM customers UNION SELECT country, 'supplier' FROM suppliers", "mariadb", "union_all",
     "needs_schema", "found", None),
    ("q_union_overlap", "every e-mail address of customers and suppliers, each once",
     "SELECT LOWER(email) FROM customers UNION SELECT LOWER(email) FROM suppliers", "postgresql", None, None, "quiet",
     "SELECT DISTINCT LOWER(email) FROM (SELECT email FROM customers UNION ALL SELECT email FROM suppliers) u"),
    ("union_all_once", "every e-mail address of customers and suppliers, without duplicates",
     "SELECT LOWER(email) FROM customers UNION ALL SELECT LOWER(email) FROM suppliers", "postgresql", "union_all_dups", "likely_risk",
     "offer", "SELECT DISTINCT LOWER(email) FROM (SELECT email FROM customers UNION ALL SELECT email FROM suppliers) u"),
    ("union_all_once_hi", "customers aur suppliers ke sheher, duplicate nahi chahiye",
     "SELECT city FROM customers WHERE city IS NOT NULL UNION ALL SELECT country FROM suppliers", "mariadb", "union_all_dups",
     "likely_risk", "offer", "SELECT DISTINCT x FROM (SELECT city AS x FROM customers WHERE city IS NOT NULL UNION ALL SELECT country FROM "
     "suppliers) u"),
    ("q_union_all_rows", "all payments and refunds as one list",
     "SELECT id, amount FROM payments UNION ALL SELECT id, quantity FROM returns", "postgresql", None, None, "quiet",
     "SELECT id, amount FROM payments UNION ALL SELECT id, quantity FROM returns"),
    # ---- a HAVING condition on a GROUP BY column (same rows from WHERE, before grouping)
    ("having_key_eq", "customer 3's order total", "SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id "
     "HAVING customer_id = 3", "mariadb", "having_key", "needs_execution", "same", None),
    ("having_key_or", "paid or shipped totals of customers 1 and 2",
     "SELECT o.customer_id, SUM(o.amount) AS total FROM orders o WHERE o.status = 'paid' OR o.status = 'shipped' GROUP BY o.customer_id "
     "HAVING o.customer_id IN (1, 2) ORDER BY o.customer_id", "postgresql", "having_key", "needs_execution", "same", None),
    ("having_key_status", "totals of paid and shipped orders per status",
     "SELECT status, COUNT(*) AS n, SUM(amount) AS total FROM orders GROUP BY status HAVING status IN ('paid', 'shipped') ORDER BY status",
     "mariadb", "having_key", "needs_execution", "same", None),
    ("having_key_mixed", "customers 1 to 3 with more than one order",
     "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id HAVING customer_id <= 3 AND COUNT(*) > 1", "postgresql",
     "having_key", "needs_execution", "found", None),
    ("q_having_total", "customers with more than one order", "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id "
     "HAVING COUNT(*) > 1 ORDER BY customer_id", "postgresql", None, None, "quiet",
     "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id HAVING COUNT(*) > 1 ORDER BY customer_id"),
    # ---- a CTE read twice and filtered outside
    ("cte_twice_pg", "customer 3's total next to itself", TWICE, "postgresql", "cte_filter_late", "needs_execution", "same", None),
    ("cte_twice_maria", "customer 3's total next to itself", TWICE, "mariadb", "cte_filter_late", "needs_execution", "found", None),
    # ---- SELECT * across a join
    ("star_derived_maria", "orders after the second one with their customer", "SELECT COUNT(*) FROM (SELECT * FROM customers c "
     "JOIN orders o ON o.customer_id = c.id WHERE o.id > 2) t", "mariadb", "star_dup", "proven", "found", None),
    ("star_cte_pg", "orders after the second one with their customer", "WITH t AS (SELECT * FROM customers c JOIN orders o "
     "ON o.customer_id = c.id WHERE o.id > 2) SELECT COUNT(*) FROM t", "postgresql", "star_dup", "likely_risk", "found", None),
    ("star_top", "orders with their customers", "SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.id > 2",
     "postgresql", "star_join", "needs_execution", "found", None),
    # ---- a join with no ON, an ON that doesn't connect the table it joins
    ("no_on_pg", "every order with its customer's name", "SELECT c.first_name, o.id FROM customers c JOIN orders o ORDER BY o.id",
     "postgresql", "join", "proven", "fix", "SELECT c.first_name, o.id FROM customers c JOIN orders o ON o.customer_id = c.id ORDER BY o.id"),
    ("no_on_maria", "every order with its customer's name", "SELECT c.first_name, o.id FROM customers c INNER JOIN orders o ORDER BY o.id",
     "mariadb", "join", "proven", "fix", "SELECT c.first_name, o.id FROM customers c JOIN orders o ON o.customer_id = c.id ORDER BY o.id"),
    ("on_not_connect", "each order line with its customer",
     "SELECT c.id AS cid, i.id AS iid FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items i ON o.id = o.customer_id "
     "ORDER BY i.id", "mariadb", "join", "proven", "fix",
     "SELECT o.customer_id AS cid, i.id AS iid FROM orders o JOIN order_items i ON i.order_id = o.id ORDER BY i.id"),
]

LAB = {
    "comma_rows": ("64", "64"),                 # FROM customers, orders: every pair
    "join_no_on": ("64", "42601"),              # JOIN without ON: a cross join on MariaDB, refused on PostgreSQL
    "on_not_connect": ("30", "30"),             # an ON that skips the joined table: 30 rows instead of 10
    "uncorr_values": ("1", "1"),                # a total that never names the outer row: one value for every customer
    "corr_values": ("5", "5"),                  # tied to the row: each customer's own
    "inner_only_values": ("1", "1"),            # o.customer_id = o.id: still one value for all
    "alias_in_where": ("1054", "42703"),        # WHERE can't see a SELECT name (why the repeat is wrapped, not renamed)
    "alias_in_order": ("8", "8"),               # ORDER BY can
    "having_alias_no_group": ("1", "42703"),    # HAVING alias without GROUP BY: MariaDB yes, PostgreSQL no
    "union_tag_repeats": ("7", "7"),            # UNION also removes repeats inside a part ...
    "union_all_tag_repeats": ("11", "11"),      # ... so UNION ALL differs there
    "union_tag_keys": ("11", "11"),             # with a key in each part both give the same rows
    "union_all_tag_keys": ("11", "11"),
    "union_collation": ("9", "10"),             # 'aarav@' and 'AARAV@': one row under MariaDB's _ci collation, two on PostgreSQL
    "star_derived": ("1060", "8"),              # SELECT * over a join in a derived table: refused on MariaDB, runs on PostgreSQL ...
    "star_derived_ref": ("1060", "42702"),      # ... until the repeated name is used
    "star_cte": ("1060", "8"),
    "not_materialized": ("1064", "12800"),      # AS NOT MATERIALIZED: PostgreSQL only
}
LAB_SQL = {
    "comma_rows": "SELECT COUNT(*) FROM customers c, orders o",
    "join_no_on": "SELECT COUNT(*) FROM customers c JOIN orders o",
    "on_not_connect": "SELECT COUNT(*) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items i ON o.id = o.customer_id",
    "uncorr_values": "SELECT COUNT(DISTINCT t) FROM (SELECT c.id, (SELECT SUM(o.amount) FROM orders o) AS t FROM customers c) x",
    "corr_values": "SELECT COUNT(DISTINCT t) FROM (SELECT c.id, (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id) AS t "
                   "FROM customers c) x",
    "inner_only_values": "SELECT COUNT(DISTINCT t) FROM (SELECT c.id, (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = o.id) "
                         "AS t FROM customers c) x",
    "alias_in_where": "SELECT c.id, (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id) AS t FROM customers c WHERE t > 100",
    "alias_in_order": "SELECT COUNT(*) FROM (SELECT c.id, (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id) AS t "
                      "FROM customers c ORDER BY t DESC) x",
    "having_alias_no_group": "SELECT c.id, (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id) AS t FROM customers c "
                             "HAVING t > 100 ORDER BY c.id",
    "union_tag_repeats": "SELECT COUNT(*) FROM (SELECT city, 'c' AS k FROM customers UNION SELECT country, 's' FROM suppliers) u",
    "union_all_tag_repeats": "SELECT COUNT(*) FROM (SELECT city, 'c' AS k FROM customers UNION ALL SELECT country, 's' FROM suppliers) u",
    "union_tag_keys": "SELECT COUNT(*) FROM (SELECT id, 'c' AS k FROM customers UNION SELECT id, 's' FROM suppliers) u",
    "union_all_tag_keys": "SELECT COUNT(*) FROM (SELECT id, 'c' AS k FROM customers UNION ALL SELECT id, 's' FROM suppliers) u",
    "union_collation": "SELECT COUNT(*) FROM (SELECT email FROM customers UNION SELECT email FROM suppliers) u",
    "star_derived": "SELECT COUNT(*) FROM (SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id) t",
    "star_derived_ref": "SELECT t.id FROM (SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id) t",
    "star_cte": "WITH t AS (SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id) SELECT COUNT(*) FROM t",
    "not_materialized": "WITH x AS NOT MATERIALIZED (SELECT customer_id, SUM(amount) AS t FROM orders GROUP BY customer_id) "
                        "SELECT t FROM x WHERE customer_id = 3",
}
# what EXPLAIN shows for the same work written twice (the reason the repeats are speed risks)
EXPLAIN_REPEAT = ("SELECT c.id, (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id) AS t FROM customers c "
                  "WHERE (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id) > 100 "
                  "ORDER BY (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id) DESC")
EXPLAIN_SAME = "SELECT id FROM products WHERE price > (SELECT AVG(price) FROM products) AND stock < (SELECT AVG(price) FROM products)"


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
    assert {p[6] for p in P} >= {"fix", "offer", "quiet", "found", "same"}
    assert {p[4] for p in P if p[4]} >= TONIGHT


@pytest.mark.parametrize("pid,words,q,engine,topic,cls,proof,truth", P, ids=[p[0] for p in P])
def test_dbaba_reads_the_structure(engines, pid, words, q, engine, topic, cls, proof, truth):
    res = analyze_sql(f"{q};\n\n{words}", engine=engine)
    fs = res["correct"]["findings"]
    if proof == "quiet":
        said = [(f["topic"], f["title_en"]) for f in fs if f["kind"] in ("error", "wrong") or f["topic"] in TONIGHT]
        assert not said, (pid, said)
    else:
        found = [f for f in fs if f["topic"] == topic]
        assert found, (pid, [(f["topic"], f["title_en"]) for f in fs])
        assert any(f["confidence"]["class"] == cls for f in found), (pid, [f["confidence"]["class"] for f in found])
        if cls == "needs_execution":                   # a speed finding is a risk, never a promised bottleneck
            assert not re.search(r"\b(is|will be) (slow|a bottleneck)\b", " ".join(f["title_en"] + f["fix_en"] for f in found), re.I)
    for name, eng in engines.items():
        if proof == "quiet":
            if truth:
                assert S.rows(eng, q) == S.rows(eng, truth), (pid, name)
            continue
        if proof == "found":
            continue
        if proof == "same" and topic == "cte_filter_late" and name == "mysql":
            continue                                   # the PostgreSQL keyword: MariaDB pushes the filter in by itself
        other = res if _kind(name) == engine else analyze_sql(f"{q};\n\n{words}", engine=_kind(name))
        sql = other["correct"].get("corrected") or other.get("suggested")
        assert sql, (pid, name)
        want = q if proof == "same" else truth
        ordered = bool(re.search(r"\border\s+by\b(?![^()]*\))", want, re.I))
        assert S.rows(eng, sql.rstrip().rstrip(";"), ordered) == S.rows(eng, want, ordered), (pid, name, sql)


def test_a_union_all_on_parts_with_repeats_would_change_the_rows(engines):
    q = "SELECT city, 'customer' AS kind FROM customers UNION SELECT country, 'supplier' FROM suppliers"
    res = analyze_sql(q, engine="mariadb")
    f = next(f for f in res["correct"]["findings"] if f["topic"] == "union_all")
    assert "no repeated rows of its own" in f["fix_en"] and f["confidence"]["class"] == "needs_schema"
    for eng in engines.values():                       # exactly why it is offered, never applied
        assert S.rows(eng, q) != S.rows(eng, q.replace("UNION", "UNION ALL"))


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


def test_the_repeats_show_in_each_engines_plan(engines):
    def plan(eng, sql):
        S.build(eng)
        return "\n".join(" | ".join(str(x) for x in r) for r in eng.run("EXPLAIN " + sql))
    pg, my = plan(engines["postgres"], EXPLAIN_REPEAT), plan(engines["mysql"], EXPLAIN_REPEAT)
    assert "SubPlan 2" in pg and "SubPlan 3" not in pg, pg          # the ORDER BY copy shares the SELECT one; WHERE has its own
    assert my.count("DEPENDENT SUBQUERY") == 3, my
    pg, my = plan(engines["postgres"], EXPLAIN_SAME), plan(engines["mysql"], EXPLAIN_SAME)
    assert "InitPlan 2" in pg and my.count("SUBQUERY") == 2, (pg, my)


def test_every_structural_rule_records_where_it_was_proven():
    import json

    assert all(v == ["mariadb", "postgresql"] for v in sqlstruct.PROVEN.values()) and set(sqlstruct.PROVEN) >= TONIGHT
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    mapped = {t for f in funds.values() for t in f["topics"]}
    assert TONIGHT <= mapped
    for eng in ("mysql", "mariadb", "postgresql"):
        rules = json.load(open(f"app/dbknow/dialects/{eng}.json"))["query_rules"]
        for key in ("join_without_on", "star_dup_derived", "union_collation", "repeated_subquery", "same_subquery", "cte_read_twice"):
            assert rules[key]["says"] and rules[key]["proven"], (eng, key)
