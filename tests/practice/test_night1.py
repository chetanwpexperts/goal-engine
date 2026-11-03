"""Night 1 practice bank: SELECT basics (filters, expressions, sorting, NULL, LIKE, DISTINCT, AND/OR, text joining).

Each problem: what the person wants (in words), the query they wrote (wrong), and a correct query. The correct query is run
on MariaDB and PostgreSQL first and both must agree (the verified answer). Then DBaba gets the wrong query with the words:
it must find the mistake under the right topic, and its corrected (or 'if that's what you meant') query must return the
verified rows on both databases. Problems marked fix=None only need the mistake found (there is no single right rewrite).

The lab table (what each engine really does with the wrong query) is in LAB below and checked by test_the_lab_table.
"""

import pytest

from app.agents.sql import analyze_sql
from tests.practice import schema as S

# (id, words, wrong query, topic DBaba must find, correct query (None: found is enough), keep row order?, engines to run on)
P = [
    ("plus_concat", "full name of every customer", "SELECT id, first_name + ' ' + last_name AS full_name FROM customers",
     "concat", "SELECT id, CONCAT(first_name, ' ', last_name) AS full_name FROM customers", False, None),
    ("plus_number", "a label like 'Order #1' for each order", "SELECT 'Order #' + id AS label FROM orders",
     "concat", "SELECT CONCAT('Order #', id) AS label FROM orders", False, None),
    ("pipes_unknown", "full name of every employee", "SELECT id, first_name || ' ' || last_name AS full_name FROM employees",
     "concat", "SELECT id, CONCAT(first_name, ' ', last_name) AS full_name FROM employees", False, None),
    ("pipes_mysql", "city and name together, on MySQL", "SELECT `id`, city || ', ' || first_name AS label FROM `customers` WHERE city IS NOT NULL",
     "concat", "SELECT `id`, CONCAT(city, ', ', first_name) AS label FROM `customers` WHERE city IS NOT NULL", False, ["mysql"]),
    ("isnull_two", "discount of every order, 0 when there is none", "SELECT id, ISNULL(discount, 0) AS discount FROM orders",
     "null_fn", "SELECT id, COALESCE(discount, 0) AS discount FROM orders", False, None),
    ("ifnull_pg", "shipping per paid order, 0 when missing", "SELECT id, IFNULL(shipping, 0) AS shipping FROM orders WHERE status ILIKE 'PAID'",
     "null_fn", "SELECT id, COALESCE(shipping, 0) AS shipping FROM orders WHERE status ILIKE 'PAID'", False, ["postgres"]),
    ("nvl_portable", "commission per employee, 0 when missing", "SELECT id, NVL(commission, 0) AS commission FROM employees",
     "null_fn", "SELECT id, COALESCE(commission, 0) AS commission FROM employees", False, None),
    ("like_star", "customers whose first name contains 'ar'", "SELECT id, first_name FROM customers WHERE first_name LIKE '*ar*'",
     "like", "SELECT id, first_name FROM customers WHERE first_name LIKE '%ar%'", False, None),
    ("like_star_end", "products whose name ends with Lite", "SELECT id, name FROM products WHERE name LIKE '*Lite'",
     "like", "SELECT id, name FROM products WHERE name LIKE '%Lite'", False, None),
    ("like_contains", "products whose name contains Phone", "SELECT id, name FROM products WHERE name LIKE 'Phone'",
     "like", "SELECT id, name FROM products WHERE name LIKE '%Phone%'", False, None),
    ("like_starts", "products starting with Laptop", "SELECT id, name FROM products WHERE name LIKE 'Laptop'",
     "like", "SELECT id, name FROM products WHERE name LIKE 'Laptop%'", False, None),
    ("like_underscore", "the sku that starts with PH_1 (the underscore is part of the code)", "SELECT id, sku FROM products WHERE sku LIKE 'PH_1%'",
     "like", "SELECT id, sku FROM products WHERE sku LIKE 'PH!_1%' ESCAPE '!'", False, None),
    ("like_underscore2", "skus starting with LP_2", "SELECT id, sku FROM products WHERE sku LIKE 'LP_2%'",
     "like", "SELECT id, sku FROM products WHERE sku LIKE 'LP!_2%' ESCAPE '!'", False, None),
    ("distinct_paren", "one row per city with a customer name", "SELECT DISTINCT(city), first_name FROM customers",
     "distinct", None, False, None),
    ("and_or_first", "paid or shipped orders above 1000", "SELECT id FROM orders WHERE status = 'paid' OR status = 'shipped' AND amount > 1000",
     "and_or", "SELECT id FROM orders WHERE (status = 'paid' OR status = 'shipped') AND amount > 1000", False, None),
    ("and_or_last", "orders above 400 that are paid or pending", "SELECT id FROM orders WHERE amount > 400 AND status = 'paid' OR status = 'pending'",
     "and_or", "SELECT id FROM orders WHERE amount > 400 AND (status = 'paid' OR status = 'pending')", False, None),
    ("and_or_update", "phones or laptops priced above 50000 should get stock 0",
     "UPDATE products SET stock = 0 WHERE category_id = 2 OR category_id = 3 AND price > 50000",
     "and_or", "UPDATE products SET stock = 0 WHERE (category_id = 2 OR category_id = 3) AND price > 50000", False, None),
    ("and_or_delete", "remove the cash or card payments of order 1",
     "DELETE FROM payments WHERE method = 'cash' OR method = 'card' AND order_id = 1",
     "and_or", "DELETE FROM payments WHERE (method = 'cash' OR method = 'card') AND order_id = 1", False, None),
    ("null_math", "amount payable per order: amount plus shipping minus discount",
     "SELECT id, amount + shipping - discount AS payable FROM orders",
     "null_math", "SELECT id, amount + COALESCE(shipping, 0) - COALESCE(discount, 0) AS payable FROM orders", False, None),
    ("null_math_sum", "total of line values after discount", "SELECT SUM(unit_price * quantity - discount) AS net FROM order_items",
     "null_math", "SELECT SUM(unit_price * quantity - COALESCE(discount, 0)) AS net FROM order_items", False, None),
    ("null_math_pay", "salary plus commission for each employee", "SELECT id, salary + commission AS total_pay FROM employees",
     "null_math", "SELECT id, salary + COALESCE(commission, 0) AS total_pay FROM employees", False, None),
    ("concat_null", "full name with the middle name when there is one",
     "SELECT id, CONCAT(first_name, ' ', middle_name, ' ', last_name) AS full_name FROM customers",
     "concat", "SELECT id, CONCAT(first_name, ' ', COALESCE(middle_name, ''), ' ', last_name) AS full_name FROM customers", False, None),
    ("division_share", "percent of orders that are paid",
     "SELECT COUNT(CASE WHEN status = 'paid' THEN 1 END) / COUNT(*) * 100 AS pct FROM orders",
     "division", "SELECT 1.0 * COUNT(CASE WHEN status = 'paid' THEN 1 END) / COUNT(*) * 100 AS pct FROM orders", False, None),
    ("division_phone", "share of customers with a phone", "SELECT COUNT(phone) / COUNT(*) AS share FROM customers",
     "division", "SELECT 1.0 * COUNT(phone) / COUNT(*) AS share FROM customers", False, None),
    ("nulls_desc", "top 3 employees by commission", "SELECT first_name, commission FROM employees ORDER BY commission DESC LIMIT 3",
     "nulls_order", "SELECT first_name, commission FROM employees ORDER BY commission IS NULL, commission DESC, id LIMIT 3", True, None),
    ("nulls_asc", "the 2 orders with the smallest discount", "SELECT id, discount FROM orders ORDER BY discount LIMIT 2",
     "nulls_order", "SELECT id, discount FROM orders ORDER BY discount IS NULL, discount, id LIMIT 2", True, None),
    ("ties_limit", "top 3 salaries", "SELECT first_name, salary FROM employees ORDER BY salary DESC LIMIT 3",
     "stable_order", "SELECT first_name, salary FROM employees ORDER BY salary DESC, id LIMIT 3", True, None),
    ("ties_page", "second page of products by price, 3 per page", "SELECT name, price FROM products ORDER BY price LIMIT 3 OFFSET 3",
     "stable_order", "SELECT name, price FROM products ORDER BY price, id LIMIT 3 OFFSET 3", True, None),
    ("limit_no_order", "the latest 3 orders", "SELECT * FROM orders LIMIT 3", "stable_order", None, False, None),
    ("double_quotes", "customers in Pune", "SELECT id FROM customers WHERE city = \"Pune\"",
     "quotes", "SELECT id FROM customers WHERE city = 'Pune'", False, None),
    ("double_quotes_like", "employees whose name starts with A", "SELECT id FROM employees WHERE first_name LIKE \"A%\"",
     "quotes", "SELECT id FROM employees WHERE first_name LIKE 'A%'", False, None),
    ("eq_null", "customers without a phone", "SELECT id FROM customers WHERE phone = NULL",
     "null", "SELECT id FROM customers WHERE phone IS NULL", False, None),
    ("ne_null", "customers that have a middle name", "SELECT id FROM customers WHERE middle_name != NULL",
     "null", "SELECT id FROM customers WHERE middle_name IS NOT NULL", False, None),
    ("alias_where", "orders whose doubled amount is above 50000", "SELECT id, amount * 2 AS doubled FROM orders WHERE doubled > 50000",
     "alias", "SELECT id, amount * 2 AS doubled FROM orders WHERE (amount * 2) > 50000", False, None),
    ("or_value", "orders that are paid or shipped", "SELECT id FROM orders WHERE status = 'paid' OR 'shipped'",
     "or", "SELECT id FROM orders WHERE status IN ('paid', 'shipped')", False, None),
    ("unquoted_word", "customers from Delhi", "SELECT id FROM customers WHERE city = Delhi",
     "value", "SELECT id FROM customers WHERE city = 'Delhi'", False, None),
    ("between_reversed", "orders between 1000 and 30000", "SELECT id FROM orders WHERE amount BETWEEN 30000 AND 1000",
     "between", "SELECT id FROM orders WHERE amount BETWEEN 1000 AND 30000", False, None),
    ("agg_in_where", "customers with more than one order", "SELECT customer_id FROM orders WHERE COUNT(*) > 1 GROUP BY customer_id",
     "where", None, False, None),
]

# what each engine does with the wrong query (reproduced on MariaDB 10.11 / 11 and PostgreSQL 16)
LAB = {
    "plus_concat": ("rows", "error"),       # MariaDB: 0 for every row (text -> number), PostgreSQL: operator does not exist
    "isnull_two": ("error", "error"),       # 1582 incorrect parameter count / function isnull(numeric, integer) does not exist
    "ifnull_pg": ("error", "error"),        # MariaDB: no ILIKE; PostgreSQL: function ifnull does not exist
    "nvl_portable": ("rows", "error"),      # MariaDB has NVL; PostgreSQL doesn't
    "like_star": ("rows", "rows"),          # both: no rows (silently wrong)
    "pipes_unknown": ("rows", "rows"),      # MariaDB: 0 (logical OR), PostgreSQL: the joined text
    "double_quotes": ("rows", "error"),     # PostgreSQL: column "Pune" does not exist
    "division_share": ("rows", "rows"),     # MariaDB 50.0000, PostgreSQL 0
    "nulls_desc": ("rows", "rows"),         # PostgreSQL returns the NULL commissions first
    "alias_where": ("error", "error"),      # 1054 unknown column / column "doubled" does not exist
}


def _engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


@pytest.fixture(scope="module")
def engines():
    return _engines()


def _effect(eng, sql, ordered):
    """Rows of a SELECT, or the changed table after an UPDATE/DELETE (rolled back)."""
    S.build(eng)
    low = sql.lstrip().lower()
    if low.startswith(("update", "delete")):
        eng.begin()
        try:
            eng.run(sql)
            table = sql.split()[1] if low.startswith("update") else sql.split()[2]
            return S.rows(eng, f"SELECT * FROM {table} ORDER BY id", ordered=True)
        finally:
            eng.rollback()
    return S.rows(eng, sql, ordered=ordered)


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)


@pytest.mark.parametrize("pid,words,wrong,topic,fix,ordered,only", P, ids=[p[0] for p in P])
def test_dbaba_finds_and_fixes(engines, pid, words, wrong, topic, fix, ordered, only):
    res = analyze_sql(f"{wrong};\n\n{words}")
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
        want = _effect(eng, fix, ordered)
        assert _effect(eng, offered, ordered) == want, (pid, name, offered)


def test_the_verified_answers_agree_on_both_databases(engines):
    for pid, _w, _q, _t, fix, ordered, only in P:
        if fix is None or only:
            continue
        m, p = (_effect(engines[k], fix, ordered) for k in ("mysql", "postgres"))
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
    # and the wrong rows are really wrong where they run
    m = engines["mysql"]
    S.build(m)
    assert S.rows(m, by["plus_concat"][2])[0][1] == "0"
    assert S.rows(m, by["pipes_unknown"][2])[0][1] == "0"
    assert S.rows(m, by["like_star"][2]) == []
    pg = engines["postgres"]
    S.build(pg)
    assert S.rows(pg, by["division_share"][2]) == [("0",)]
    assert S.rows(pg, by["nulls_desc"][2], ordered=True)[0][1] == "NULL"


def test_valid_basics_get_no_findings():
    for sql in ["SELECT id, CONCAT(first_name, ' ', last_name) FROM customers",
                "SELECT id FROM orders WHERE (status = 'paid' OR status = 'shipped') AND amount > 1000",
                "SELECT id, amount + COALESCE(shipping, 0) FROM orders",
                "SELECT first_name FROM customers WHERE first_name LIKE '%ar%' ORDER BY id",
                "SELECT first_name, salary FROM employees ORDER BY salary DESC, id LIMIT 3",
                "SELECT 1.0 * COUNT(phone) / COUNT(*) FROM customers",
                "SELECT DISTINCT city FROM customers",
                "SELECT id FROM orders WHERE created_at + INTERVAL '1 day' > now()",
                "SELECT id, price + 10 FROM products WHERE name = 'x' || 'y' AND id::text = '1'"]:
        res = analyze_sql(sql)
        bad = [f["title_en"] for f in res["correct"]["findings"] if f["kind"] in ("error", "wrong")]
        assert not bad, (sql, bad)
