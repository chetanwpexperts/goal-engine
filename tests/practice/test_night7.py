"""Night 7 practice bank: CASE (first true WHEN wins, NULL in a simple CASE, no ELSE, one result type), WITH and recursive CTEs
(RECURSIVE, something that ends it, loops in the data, the anchor's type, the 1000-round limits, what the recursive part can't
have) and window functions (where they can stand, ORDER BY and frames, ties, PARTITION BY said in words, nesting, old versions).

Each problem: what the person wants (in words), the query they wrote (wrong), a correct query, and the database it is for
(None = not said). The correct query is run on MariaDB and PostgreSQL first and both must agree (the verified answer). Then
DBaba gets the wrong query with the words (and the database, when one is said): it must find the mistake under the right
topic, name its fundamental, and its corrected (or 'if that's what you meant') query must return the verified rows.
fix=None: finding it is enough (the right rewrite depends on something the query can't show, e.g. which order was meant).

LAB below is what each engine really does with the wrong query (reproduced on MariaDB 10.11 and PostgreSQL 16, with a
3-second statement limit so a query that never ends shows as 'error' on PostgreSQL).
"""

import pytest

from app.agents.sql import analyze_sql
from tests.practice import schema as S

TOP = "SELECT id, path FROM p ORDER BY id"
TREE = "FROM categories c JOIN p ON c.parent_id = p.id) " + TOP

# (id, words, wrong query, topic, correct query or None, keep row order?, run only on, database said)
P = [
    # ---- a WHEN an earlier WHEN already takes: CASE stops at the first true one
    ("cu_amount", "label orders: big above 10000, mid above 100, otherwise small",
     "SELECT id, CASE WHEN amount > 100 THEN 'mid' WHEN amount > 10000 THEN 'big' ELSE 'small' END AS size FROM orders",
     "case_unreachable", "SELECT id, CASE WHEN amount > 10000 THEN 'big' WHEN amount > 100 THEN 'mid' ELSE 'small' END FROM orders",
     False, None, None),
    ("cu_salary", "grade employees: A from 80000, B from 50000, else C",
     "SELECT id, CASE WHEN salary >= 50000 THEN 'B' WHEN salary >= 80000 THEN 'A' ELSE 'C' END AS grade FROM employees",
     "case_unreachable", "SELECT id, CASE WHEN salary >= 80000 THEN 'A' WHEN salary >= 50000 THEN 'B' ELSE 'C' END FROM employees",
     False, None, None),
    ("cu_price", "products under 500 are very cheap, under 1000 cheap, the rest costly",
     "SELECT id, CASE WHEN price < 1000 THEN 'cheap' WHEN price < 500 THEN 'very cheap' ELSE 'costly' END AS band FROM products",
     "case_unreachable", "SELECT id, CASE WHEN price < 500 THEN 'very cheap' WHEN price < 1000 THEN 'cheap' ELSE 'costly' END FROM products",
     False, None, None),
    ("cu_and", "big orders from 1000, and big paid orders marked separately",
     "SELECT id, CASE WHEN amount >= 1000 THEN 'big' WHEN amount >= 1000 AND status = 'paid' THEN 'big paid' ELSE 'other' END AS kind FROM orders",
     "case_unreachable",
     "SELECT id, CASE WHEN amount >= 1000 AND status = 'paid' THEN 'big paid' WHEN amount >= 1000 THEN 'big' ELSE 'other' END FROM orders",
     False, None, None),
    ("cu_stock", "stock status: out when 0, low up to 10, else ok",
     "SELECT id, CASE WHEN stock <= 10 THEN 'low' WHEN stock = 0 THEN 'out' ELSE 'ok' END AS st FROM products",
     "case_unreachable", "SELECT id, CASE WHEN stock = 0 THEN 'out' WHEN stock <= 10 THEN 'low' ELSE 'ok' END FROM products", False, None, None),
    ("cu_status", "paid orders are done, shipped are on the way",
     "SELECT id, CASE WHEN status IN ('paid', 'shipped') THEN 'done' WHEN status = 'shipped' THEN 'on the way' ELSE 'open' END FROM orders",
     "case_unreachable",
     "SELECT id, CASE WHEN status = 'shipped' THEN 'on the way' WHEN status IN ('paid', 'shipped') THEN 'done' ELSE 'open' END FROM orders",
     False, None, None),
    # ---- CASE x WHEN NULL: x = NULL is never true
    ("cn_discount", "say which orders have no discount",
     "SELECT id, CASE discount WHEN NULL THEN 'no discount' ELSE 'discount' END AS d FROM orders",
     "case_null", "SELECT id, CASE WHEN discount IS NULL THEN 'no discount' ELSE 'discount' END FROM orders", False, None, None),
    ("cn_commission", "commission, 0 when there is none",
     "SELECT id, CASE commission WHEN NULL THEN 0 ELSE commission END AS c FROM employees",
     "case_null", "SELECT id, COALESCE(commission, 0) FROM employees", False, None, None),
    ("cn_city", "Pune customers are local, no city is unknown, the rest are away",
     "SELECT id, CASE city WHEN 'Pune' THEN 'local' WHEN NULL THEN 'unknown' ELSE 'away' END AS place FROM customers",
     "case_null", "SELECT id, CASE WHEN city = 'Pune' THEN 'local' WHEN city IS NULL THEN 'unknown' ELSE 'away' END FROM customers",
     False, None, None),
    # ---- no ELSE
    ("ce_order", "pending orders first, then paid, then all the others",
     "SELECT id FROM orders ORDER BY CASE status WHEN 'pending' THEN 1 WHEN 'paid' THEN 2 END, id",
     "case_no_else", "SELECT id FROM orders ORDER BY CASE status WHEN 'pending' THEN 1 WHEN 'paid' THEN 2 ELSE 3 END, id", True, None, None),
    ("ce_order_pg", "cancelled orders last, the rest by id",
     "SELECT id FROM orders ORDER BY CASE WHEN status <> 'cancelled' THEN 1 END, id",
     "case_no_else", "SELECT id FROM orders ORDER BY CASE WHEN status <> 'cancelled' THEN 1 ELSE 2 END, id", True, None, None),
    ("ce_label", "label paid and shipped orders",
     "SELECT id, CASE WHEN status = 'paid' THEN 'done' WHEN status = 'shipped' THEN 'on the way' END AS label FROM orders",
     "case_no_else", None, False, None, None),
    # ---- text and numbers in one CASE
    ("ct_pg", "show the discount, or 'none' (postgres)",
     "SELECT id, CASE WHEN discount IS NULL THEN 'none' ELSE discount END AS d FROM orders",
     "case_types", "SELECT id, CASE WHEN discount IS NULL THEN 'none' ELSE CAST(discount AS TEXT) END FROM orders", False, ["postgres"], "postgresql"),
    ("ct_count_pg", "repeat customers say 'repeat', others show their order count, on postgres",
     "SELECT customer_id, CASE WHEN COUNT(*) > 1 THEN 'repeat' ELSE COUNT(*) END AS what FROM orders GROUP BY customer_id",
     "case_types", "SELECT customer_id, CASE WHEN COUNT(*) > 1 THEN 'repeat' ELSE CAST(COUNT(*) AS TEXT) END FROM orders GROUP BY customer_id",
     False, ["postgres"], "postgresql"),
    ("ct_mysql", "stock, or 'sold out' when nothing is left (mysql)",
     "SELECT id, CASE WHEN stock = 0 THEN 'sold out' ELSE stock END AS st FROM products",
     "case_types", "SELECT id, CASE WHEN stock = 0 THEN 'sold out' ELSE CAST(stock AS CHAR) END FROM products", False, ["mysql"], "mysql"),
    # ---- a WITH that reads itself without RECURSIVE
    ("rk_numbers", "the numbers 1 to 10",
     "WITH n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n WHERE x < 10) SELECT x FROM n",
     "cte_recursive_kw", "WITH RECURSIVE n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n WHERE x < 10) SELECT x FROM n", False, None, None),
    ("rk_tree", "the category Phones and every category above it",
     "WITH up AS (SELECT id, parent_id FROM categories WHERE id = 2 UNION ALL "
     "SELECT c.id, c.parent_id FROM categories c JOIN up ON c.id = up.parent_id) SELECT id FROM up",
     "cte_recursive_kw", "SELECT id FROM categories WHERE id IN (1, 2)", False, None, None),
    ("rk_boss", "the chain of managers above employee 5",
     "WITH boss AS (SELECT id, manager_id FROM employees WHERE id = 5 UNION ALL "
     "SELECT e.id, e.manager_id FROM employees e JOIN boss b ON e.id = b.manager_id) SELECT id FROM boss",
     "cte_recursive_kw", "SELECT id FROM employees WHERE id IN (1, 4, 5)", False, None, None),
    # ---- nothing ends the recursion
    ("ns_numbers", "the numbers 1 to 10",
     "WITH RECURSIVE n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n) SELECT x FROM n",
     "cte_no_stop", "WITH RECURSIVE n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n WHERE x < 10) SELECT x FROM n", False, None, None),
    ("ns_days", "day numbers 1 to 7 for a weekly report",
     "WITH RECURSIVE d AS (SELECT 1 AS day_no UNION ALL SELECT day_no + 1 FROM d) SELECT day_no FROM d",
     "cte_no_stop", "SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5 UNION SELECT 6 UNION SELECT 7", False, None, None),
    ("ns_even", "even numbers from 2 up to 20",
     "WITH RECURSIVE e AS (SELECT 2 AS v UNION ALL SELECT v + 2 FROM e) SELECT v FROM e",
     "cte_no_stop", "WITH RECURSIVE e AS (SELECT 2 AS v UNION ALL SELECT v + 2 FROM e WHERE v < 20) SELECT v FROM e", False, None, None),
    ("ns_hinglish", "1 se 5 tak numbers chahiye",
     "WITH RECURSIVE n AS (SELECT 1 AS k UNION ALL SELECT k + 1 FROM n) SELECT k FROM n",
     "cte_no_stop", "SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5", False, None, None),
    ("ns_open", "a counter for my report",
     "WITH RECURSIVE n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n) SELECT x FROM n", "cte_no_stop", None, False, None, None),
    # ---- a walk with no depth limit
    ("cy_tree", "every category with its level under the top ones",
     "WITH RECURSIVE t AS (SELECT id, 1 AS lvl FROM categories WHERE parent_id IS NULL UNION ALL "
     "SELECT c.id, t.lvl + 1 FROM categories c JOIN t ON c.parent_id = t.id) SELECT id, lvl FROM t",
     "cte_cycle", None, False, None, None),
    # ---- the anchor fixes the column's type
    ("cw_mariadb", "the full path of every category, on mariadb",
     "WITH RECURSIVE p AS (SELECT id, 'Top' AS path FROM categories WHERE parent_id IS NULL UNION ALL "
     "SELECT c.id, CONCAT(p.path, ' > ', c.name) " + TREE,
     "cte_width", "WITH RECURSIVE p AS (SELECT id, CAST('Top' AS CHAR(1000)) AS path FROM categories WHERE parent_id IS NULL UNION ALL "
                  "SELECT c.id, CONCAT(p.path, ' > ', c.name) " + TREE, True, ["mysql"], "mariadb"),
    ("cw_pg", "the full path of every category (postgres)",
     "WITH RECURSIVE p AS (SELECT id, name AS path FROM categories WHERE parent_id IS NULL UNION ALL "
     "SELECT c.id, CONCAT(p.path, ' > ', c.name) " + TREE,
     "cte_width", "WITH RECURSIVE p AS (SELECT id, CAST(name AS TEXT) AS path FROM categories WHERE parent_id IS NULL UNION ALL "
                  "SELECT c.id, CONCAT(p.path, ' > ', c.name) " + TREE, True, ["postgres"], "postgresql"),
    ("cw_pg_pipes", "category paths with || in postgres",
     "WITH RECURSIVE p AS (SELECT id, name AS path FROM categories WHERE parent_id IS NULL UNION ALL "
     "SELECT c.id, p.path || ' > ' || c.name " + TREE,
     "cte_width", "WITH RECURSIVE p AS (SELECT id, CAST(name AS TEXT) AS path FROM categories WHERE parent_id IS NULL UNION ALL "
                  "SELECT c.id, CONCAT(p.path, ' > ', c.name) " + TREE, True, ["postgres"], "postgresql"),
    # ---- the 1000-round limit
    ("cd_mariadb", "numbers 1 to 1500 on mariadb",
     "WITH RECURSIVE n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n WHERE x < 1500) SELECT COUNT(*) FROM n",
     "cte_depth", None, False, None, "mariadb"),
    # ---- what the recursive part can't have
    ("ct_agg", "a running maximum, built recursively",
     "WITH RECURSIVE n AS (SELECT 1 AS x UNION ALL SELECT MAX(x) + 1 FROM n WHERE x < 3) SELECT x FROM n",
     "cte_recursive_term", None, False, None, None),
    ("ct_order", "numbers 1 to 3 in order",
     "WITH RECURSIVE n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n WHERE x < 3 ORDER BY x) SELECT x FROM n",
     "cte_recursive_term", None, False, None, None),
    ("cc_cols", "order ids under a new name", "WITH t(a, b) AS (SELECT id FROM orders) SELECT * FROM t", "cte_columns", None, False, None, None),
    ("cdup", "two summaries", "WITH a AS (SELECT 1 AS x), a AS (SELECT 2 AS x) SELECT x FROM a", "cte_dup_name", None, False, None, None),
    # ---- a window function in WHERE / HAVING, or its SELECT name in WHERE
    ("ww_top", "the highest paid employee",
     "SELECT id FROM employees WHERE ROW_NUMBER() OVER (ORDER BY salary DESC) = 1",
     "win_where", "SELECT id FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY salary DESC) AS w FROM employees) t WHERE w = 1", False, None, None),
    ("wa_latest", "the latest order of each customer",
     "SELECT customer_id, id, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date DESC) AS rn FROM orders WHERE rn = 1",
     "win_alias_where",
     "SELECT customer_id, id, 1 FROM orders o WHERE order_date = (SELECT MAX(order_date) FROM orders x WHERE x.customer_id = o.customer_id)",
     False, None, None),
    ("wa_rank", "the top earners of each department, ties included",
     "SELECT department_id, id, RANK() OVER (PARTITION BY department_id ORDER BY salary DESC) AS r FROM employees WHERE r = 1 ORDER BY department_id, id",
     "win_alias_where",
     "SELECT department_id, id, 1 FROM employees e WHERE salary = (SELECT MAX(salary) FROM employees x WHERE x.department_id = e.department_id "
     "OR (x.department_id IS NULL AND e.department_id IS NULL))", False, None, None),
    ("wa_cheap", "the 2 cheapest products",
     "SELECT p.id, p.price, DENSE_RANK() OVER (ORDER BY p.price) AS dr FROM products p WHERE dr <= 2 AND p.stock > 0",
     "win_alias_where", "SELECT id, price, 1 FROM products WHERE id = 4 UNION ALL SELECT id, price, 2 FROM products WHERE id = 5", False, None, None),
    ("wh_having", "the customers with the most orders",
     "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id HAVING RANK() OVER (ORDER BY COUNT(*) DESC) = 1",
     "win_where", "SELECT customer_id, COUNT(*) FROM orders WHERE customer_id IN (1, 2, 3) GROUP BY customer_id", False, None, None),
    # ---- LAST_VALUE / NTH_VALUE with the default frame
    ("lv_max", "each employee with the highest salary of their department",
     "SELECT id, LAST_VALUE(salary) OVER (PARTITION BY department_id ORDER BY salary) AS top FROM employees",
     "win_last_value", "SELECT id, MAX(salary) OVER (PARTITION BY department_id) FROM employees", False, None, None),
    ("lv_last", "every order with its customer's last order date",
     "SELECT id, LAST_VALUE(order_date) OVER (PARTITION BY customer_id ORDER BY order_date) AS last_date FROM orders",
     "win_last_value", "SELECT id, MAX(order_date) OVER (PARTITION BY customer_id) FROM orders", False, None, None),
    # ---- a running total over a sort with ties
    ("rt_items", "a running total of quantity, item by item",
     "SELECT id, SUM(quantity) OVER (ORDER BY order_id) AS run FROM order_items",
     "win_running_ties", "SELECT id, SUM(quantity) OVER (ORDER BY order_id, id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM order_items",
     False, None, None),
    ("rt_salary", "a running total of salaries from the lowest, one employee at a time",
     "SELECT id, SUM(salary) OVER (ORDER BY salary) AS run FROM employees",
     "win_running_ties", "SELECT id, SUM(salary) OVER (ORDER BY salary, id) FROM employees", False, None, None),
    # ---- ROW_NUMBER over ties
    ("rn_check", "the lowest paid employee of each department",
     "SELECT department_id, id FROM (SELECT department_id, id, ROW_NUMBER() OVER (PARTITION BY department_id ORDER BY salary) AS rn FROM employees) t WHERE rn = 1",
     "win_rank_ties",
     "SELECT department_id, id FROM (SELECT department_id, id, ROW_NUMBER() OVER (PARTITION BY department_id ORDER BY salary, id) AS rn FROM employees) t WHERE rn = 1",
     False, None, None),
    ("rn_ties", "the lowest paid employees of each department, all of them if tied",
     "SELECT department_id, id FROM (SELECT department_id, id, ROW_NUMBER() OVER (PARTITION BY department_id ORDER BY salary) AS rn FROM employees) t WHERE rn = 1",
     "ties",
     "SELECT department_id, id FROM (SELECT department_id, id, RANK() OVER (PARTITION BY department_id ORDER BY salary) AS rn FROM employees) t WHERE rn = 1",
     False, None, None),
    ("rn_third", "employees with the third highest salary",
     "SELECT id FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY salary DESC) AS rn FROM employees) t WHERE rn = 3",
     "win_rank_ties", "SELECT id FROM employees WHERE salary = 60000", False, None, None),
    # ---- no ORDER BY where the order is the point
    ("no_lag", "each order's amount next to the previous order's (mariadb)",
     "SELECT id, amount, LAG(amount) OVER () AS prev FROM orders", "win_no_order", None, False, None, "mariadb"),
    ("no_rank_pg", "rank the orders by amount, in postgres",
     "SELECT id, RANK() OVER () AS r FROM orders", "win_no_order", None, False, None, "postgresql"),
    ("no_first", "every order with the first order's amount",
     "SELECT id, FIRST_VALUE(amount) OVER () AS first_amount FROM orders", "win_no_order", None, False, None, None),
    # ---- PARTITION BY said in words
    ("ps_running", "running total of amount per customer, by date",
     "SELECT id, customer_id, SUM(amount) OVER (ORDER BY order_date, id) AS run FROM orders",
     "win_partition_said", "SELECT id, customer_id, SUM(amount) OVER (PARTITION BY customer_id ORDER BY order_date, id) FROM orders", False, None, None),
    ("ps_rank", "rank products by price within each category",
     "SELECT id, category_id, RANK() OVER (ORDER BY price DESC) AS r FROM products",
     "win_partition_said", "SELECT id, category_id, RANK() OVER (PARTITION BY category_id ORDER BY price DESC) FROM products", False, None, None),
    ("ps_hinglish", "har department mein salary ke hisaab se number do",
     "SELECT id, department_id, DENSE_RANK() OVER (ORDER BY salary DESC) AS n FROM employees",
     "win_partition_said", "SELECT id, department_id, DENSE_RANK() OVER (PARTITION BY department_id ORDER BY salary DESC) FROM employees",
     False, None, None),
    # ---- a window inside a window, an old version
    ("wn_nested", "the total of the row numbers",
     "SELECT id, SUM(ROW_NUMBER() OVER (ORDER BY id)) OVER () FROM orders", "win_nested", None, False, None, None),
    ("wv_old", "latest order per customer, we are on mysql 5.7",
     "SELECT customer_id, id FROM (SELECT customer_id, id, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date DESC, id DESC) AS rn "
     "FROM orders) t WHERE rn = 1", "win_version", None, False, None, None),
]

# what each engine does with the wrong query: 'rows' (runs; the rows are wrong), 'error' (refused, or never ends)
LAB = {
    "cu_amount": ("rows", "rows"),        # every order 'mid': 'big' never shows
    "cu_and": ("rows", "rows"),           # 'big paid' never shows
    "cn_discount": ("rows", "rows"),      # every order 'discount'
    "cn_city": ("rows", "rows"),          # customer 4 (no city) is 'away'
    "ce_order": ("rows", "rows"),         # the others first on MariaDB (2, 4, 8, 6, ...), last on PostgreSQL
    "ct_pg": ("rows", "error"),           # MariaDB: text column ('1000.00' < '50.00'); PostgreSQL 22P02 invalid input syntax for type numeric
    "ct_count_pg": ("rows", "error"),     # PostgreSQL 22P02 invalid input syntax for type bigint
    "rk_numbers": ("error", "error"),     # MariaDB 1146 Table 'n' doesn't exist; PostgreSQL 42P01 relation "n" does not exist
    "rk_tree": ("error", "error"),
    "ns_numbers": ("rows", "error"),      # MariaDB: 1001 rows after max_recursive_iterations, no error; PostgreSQL never ends (cancelled)
    "cw_mariadb": ("error", "rows"),      # MariaDB 1406 Data too long for column 'path'; PostgreSQL fine
    "cw_pg": ("rows", "error"),           # MariaDB fits (paths <= 40); PostgreSQL 42804 character varying(40) ... character varying overall
    "cd_mariadb": ("rows", "rows"),       # MariaDB counts 1001, PostgreSQL 1500
    "ct_agg": ("error", "error"),         # MariaDB 4008; PostgreSQL 42P19 aggregate functions are not allowed in a recursive query's recursive term
    "ct_order": ("error", "error"),       # MariaDB 1235; PostgreSQL 0A000 ORDER BY in a recursive query is not implemented
    "cc_cols": ("error", "error"),        # MariaDB 4002; PostgreSQL 42P10
    "cdup": ("error", "error"),           # MariaDB 4004; PostgreSQL 42712
    "ww_top": ("error", "error"),         # MariaDB 4015; PostgreSQL 42P20 window functions are not allowed in WHERE
    "wa_latest": ("error", "error"),      # MariaDB 1054 Unknown column 'rn'; PostgreSQL 42703
    "wh_having": ("error", "error"),      # MariaDB 4015; PostgreSQL 42P20 ... in HAVING
    "lv_max": ("rows", "rows"),           # each employee's own salary
    "rt_items": ("rows", "rows"),         # items 1, 2, 10 of order 1 all show 4
    "no_lag": ("error", "rows"),          # MariaDB 4018 No order list in window specification for 'lag'; PostgreSQL any row
    "no_rank_pg": ("error", "rows"),      # MariaDB 4018; PostgreSQL every rank is 1
    "no_first": ("rows", "rows"),         # MariaDB 300, PostgreSQL 25500 (in the lab)
    "rn_ties": ("rows", "rows"),          # one of the two 60000s of department 1
    "rn_third": ("rows", "rows"),         # one employee instead of two
    "ps_running": ("rows", "rows"),       # one running total over all customers
    "wn_nested": ("error", "error"),      # MariaDB 4074; PostgreSQL 42P20 window function calls cannot be nested
}


def _engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


@pytest.fixture(scope="module")
def engines():
    return _engines()


def _limit(eng):
    """A query that never ends must not hold the test: 3 seconds, then it counts as refused."""
    eng.run("SET statement_timeout = 3000" if eng.kind != "mysql" else "SET max_statement_time = 3")


def _rows(eng, sql, ordered):
    S.build(eng)
    _limit(eng)
    return S.rows(eng, sql, ordered=ordered)


def _outcome(eng, sql):
    S.build(eng)
    _limit(eng)
    try:
        S.rows(eng, sql)
        return "rows"
    except Exception:  # noqa: BLE001 - the engine refused it (or it ran out of time)
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
    for pid in ("cu_amount", "cu_and", "cn_discount", "cn_city", "lv_max", "rt_items", "rn_ties", "rn_third", "ps_running"):
        _pid, _w, wrong, _t, fix, ordered, _o, _s = by[pid]
        for name, eng in engines.items():
            assert _rows(eng, wrong, False) != _rows(eng, fix, False), (pid, name)
    # the same query, different rows on each database: NULL's place, and an unordered FIRST_VALUE
    assert _rows(engines["mysql"], by["ce_order"][2], True) != _rows(engines["postgres"], by["ce_order"][2], True)
    assert _rows(engines["mysql"], "SELECT id, FIRST_VALUE(amount) OVER () FROM orders", False) != \
        _rows(engines["postgres"], "SELECT id, FIRST_VALUE(amount) OVER () FROM orders", False)
    # MariaDB stops a recursive series after 1000 rounds without an error
    assert _rows(engines["mysql"], by["cd_mariadb"][2], False) == [("1001",)]
    assert _rows(engines["postgres"], by["cd_mariadb"][2], False) == [("1500",)]


@pytest.mark.parametrize("pid", [p[0] for p in P if p[4]])
def test_the_verified_answer_is_the_same_on_both(engines, pid):
    p = next(x for x in P if x[0] == pid)
    if p[6]:
        return                                              # an answer written for one database
    assert _rows(engines["mysql"], p[4], p[5]) == _rows(engines["postgres"], p[4], p[5]), pid


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


def test_valid_queries_raise_no_alarm():
    """Right CASE / WITH / window queries of every shape: no error/wrong finding."""
    for q in [
        "SELECT id, CASE WHEN amount > 10000 THEN 'big' WHEN amount > 100 THEN 'mid' ELSE 'small' END AS size FROM orders",
        "SELECT id, CASE WHEN discount IS NULL THEN 'none' ELSE 'some' END FROM orders",
        "SELECT id, CASE status WHEN 'paid' THEN 1 WHEN 'shipped' THEN 2 ELSE 3 END FROM orders",
        "SELECT SUM(CASE WHEN status = 'paid' THEN amount END) FROM orders",
        "SELECT id FROM orders ORDER BY CASE status WHEN 'pending' THEN 1 ELSE 2 END, id",
        "SELECT id, CASE WHEN amount BETWEEN 0 AND 1000 THEN 'low' WHEN amount BETWEEN 1001 AND 50000 THEN 'mid' ELSE 'high' END FROM orders",
        "WITH RECURSIVE n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n WHERE x < 10) SELECT x FROM n",
        "WITH RECURSIVE n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n) SELECT x FROM n LIMIT 5",
        "WITH RECURSIVE t AS (SELECT id, 1 AS lvl FROM categories WHERE parent_id IS NULL UNION ALL "
        "SELECT c.id, t.lvl + 1 FROM categories c JOIN t ON c.parent_id = t.id WHERE t.lvl < 10) SELECT id, lvl FROM t",
        "WITH s AS (SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id) SELECT c.id, s.total FROM customers c "
        "LEFT JOIN s ON s.customer_id = c.id",
        "WITH a AS (SELECT 1 AS x), b AS (SELECT x + 1 AS y FROM a) SELECT y FROM b",
        "SELECT id, SUM(amount) OVER (ORDER BY id) AS run FROM orders",
        "SELECT id, SUM(amount) OVER (ORDER BY order_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM orders",
        "SELECT id, amount - LAG(amount) OVER (ORDER BY order_date, id) AS change FROM orders",
        "SELECT id, LAST_VALUE(salary) OVER (PARTITION BY department_id ORDER BY salary ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) FROM employees",
        "SELECT customer_id, id FROM (SELECT customer_id, id, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date DESC, id DESC) AS rn "
        "FROM orders) t WHERE rn = 1",
        "SELECT id, RANK() OVER (PARTITION BY department_id ORDER BY salary DESC) AS r FROM employees ORDER BY r",
        "SELECT id, COUNT(*) OVER () AS total_rows FROM orders",
        "SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS n FROM orders",
        "SELECT id, NTILE(4) OVER (ORDER BY amount, id) AS quartile FROM orders",
        "SELECT customer_id, SUM(amount) AS s, SUM(SUM(amount)) OVER () AS whole FROM orders GROUP BY customer_id",
        "SELECT id, SUM(amount) OVER w FROM orders WINDOW w AS (ORDER BY id)",
    ]:
        res = analyze_sql(q)
        bad = [(f["topic"], f["title_en"]) for f in res["correct"]["findings"] if f["kind"] in ("error", "wrong")]
        assert not bad, (q, bad)


def test_every_rule_records_where_it_was_proven():
    """Standing rule B: each rule and each engine fact says which engines the lab proved it on."""
    import json

    from app.agents import sqlwin
    from app.agents.sqlread import fundamentals

    topics = {p[3] for p in P} - {"ties"}           # 'keep the ties' said in words: the ranking rule of night 1
    assert topics <= set(sqlwin.PROVEN) and all(set(v) >= {"mariadb", "postgresql"} for v in sqlwin.PROVEN.values())
    funds = fundamentals()["fundamentals"]
    assert all(any(t in f["topics"] for f in funds.values()) for t in sqlwin.PROVEN), "every topic has its fundamental"
    for eng in ("mysql", "mariadb", "postgresql"):
        rules = json.load(open(f"app/dbknow/dialects/{eng}.json"))["query_rules"]
        for key in ("case_mixed_types", "recursion_limit", "recursive_text_width", "window_needs_order"):
            assert rules[key]["proven"] and rules[key]["says"], (eng, key)


@pytest.mark.parametrize("said,kind", [("mariadb", "error"), ("postgresql", "wrong"), (None, "error")])
def test_engine_facts_follow_the_database(said, kind):
    """Rule A: LAG() OVER () is refused by MariaDB and silently arbitrary on PostgreSQL."""
    res = analyze_sql("SELECT id, LAG(amount) OVER () AS prev FROM orders", engine=said)
    got = [f["kind"] for f in res["correct"]["findings"] if f["topic"] == "win_no_order"]
    assert got == [kind], (said, got)


@pytest.mark.parametrize("said,kinds", [("mariadb", ["error"]), ("postgresql", []), (None, ["check"])])
def test_the_anchor_width_follows_the_database(said, kinds):
    """Rule A: a text literal anchor that grows is refused by MySQL/MariaDB (1406) and fine on PostgreSQL."""
    q = ("WITH RECURSIVE p AS (SELECT id, 'Top' AS path FROM categories WHERE parent_id IS NULL UNION ALL "
         "SELECT c.id, CONCAT(p.path, ' > ', c.name) " + TREE)
    res = analyze_sql(q, engine=said)
    assert [f["kind"] for f in res["correct"]["findings"] if f["topic"] == "cte_width"] == kinds, said


def test_learned_rules_from_lab_trouble_are_not_shown():
    """A drafted rule whose only engine message is about the lab (a full disk, a made-up table) proves nothing."""
    res = analyze_sql("WITH RECURSIVE t AS (SELECT id, 1 AS lvl FROM categories WHERE parent_id IS NULL UNION ALL "
                      "SELECT c.id, t.lvl + 1 FROM categories c JOIN t ON c.parent_id = t.id WHERE t.lvl < 10) SELECT id, lvl FROM t")
    assert not [f for f in res["correct"]["findings"] if f["topic"] == "learned"]
