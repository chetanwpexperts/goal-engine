"""Night 5 practice bank: changing data (UPDATE / DELETE with a join on each engine, which rows and how often they change,
SET from a subquery, upserts, INSERT ... SELECT column order, LIMIT on a change, deleting duplicates, INSERT IGNORE, REPLACE) and
stored routines (loops that never end, functions without RETURN, cursors without a handler, DECLARE order, DELIMITER, triggers
that change their own table, NEW in AFTER triggers, PostgreSQL trigger functions and their RETURN).

Each change problem: what the person wants (in words), the statement they wrote (wrong), a correct statement (per engine when
the syntax differs), the database it is for (None = not said) and the table whose rows show the effect. The correct statement
is run first and its effect (the table afterwards) is the verified answer. Then DBaba gets the wrong statement with the words
(and the database): it must find the mistake under the right topic, name its fundamental, and its corrected (or 'if that's
what you meant') statement must leave the table exactly as the verified answer does. fix=None: finding it is enough.

Routine problems are run on real scratch tables and routines named n5_* (MySQL/MariaDB can't put a trigger on a temporary
table), dropped afterwards. The script is run the way the database's client runs it (DELIMITER and $$ understood).

LAB below is what each engine really does with the wrong statement (reproduced on MariaDB 10.11 and PostgreSQL 16).
"""

import pytest

from app.agents.sql import analyze_sql
from app.agents.sqlroutine import split_script
from tests.practice import schema as S

PG_ORDERS_PUNE = "UPDATE orders o SET amount = 0 FROM customers c WHERE c.id = o.customer_id AND c.city = 'Pune'"
MY_ORDERS_PUNE = "UPDATE orders o JOIN customers c ON c.id = o.customer_id SET o.amount = 0 WHERE c.city = 'Pune'"

# (id, words, wrong, topic, correct ({'mysql':, 'postgres':} or one text for both, or None), effect query, database said)
P = [
    # ---- UPDATE with a join: each engine's own syntax (rule A decides which one is right)
    ("uj_pg", "set the amount to 0 for orders of Pune customers, this is postgresql", MY_ORDERS_PUNE, "update_join_syntax",
     {"postgres": PG_ORDERS_PUNE}, "SELECT id, amount FROM orders", "postgresql"),
    ("uj_pg_discount", "10% discount on orders of Delhi customers (postgres)",
     "UPDATE orders o INNER JOIN customers c ON c.id = o.customer_id SET o.discount = o.amount * 0.1 WHERE c.city = 'Delhi'",
     "update_join_syntax", {"postgres": "UPDATE orders o SET discount = o.amount * 0.1 FROM customers c WHERE c.id = o.customer_id AND c.city = 'Delhi'"},
     "SELECT id, discount FROM orders", "postgresql"),
    ("uj_pg_stock", "products of the supplier Beta Goods are out of stock (PostgreSQL)",
     "UPDATE products p JOIN suppliers s ON s.id = p.supplier_id SET p.stock = 0 WHERE s.name = 'Beta Goods'", "update_join_syntax",
     {"postgres": "UPDATE products p SET stock = 0 FROM suppliers s WHERE s.id = p.supplier_id AND s.name = 'Beta Goods'"},
     "SELECT id, stock FROM products", "postgresql"),
    ("uf_mariadb", "set the amount to 0 for orders of Pune customers on mariadb", PG_ORDERS_PUNE, "update_join_syntax",
     {"mysql": MY_ORDERS_PUNE}, "SELECT id, amount FROM orders", "mariadb"),
    ("uf_mysql_salary", "5% raise for employees of Pune departments, mysql",
     "UPDATE employees e SET salary = salary * 1.05 FROM departments d WHERE d.id = e.department_id AND d.location = 'Pune'",
     "update_join_syntax", {"mysql": "UPDATE employees e JOIN departments d ON d.id = e.department_id SET e.salary = e.salary * 1.05 WHERE d.location = 'Pune'"},
     "SELECT id, salary FROM employees", "mysql"),
    # ---- SET o.col = ...: PostgreSQL refuses the table name on the column being set
    ("sq_pg", "set order 1's amount to 0 in postgres", "UPDATE orders o SET o.amount = 0 WHERE o.id = 1", "set_qualified",
     {"postgres": "UPDATE orders SET amount = 0 WHERE id = 1"}, "SELECT id, amount FROM orders", "postgresql"),
    ("sq_pg_price", "laptops get 10% dearer (postgresql)", "UPDATE products p SET p.price = p.price * 1.1 WHERE p.category_id = 3",
     "set_qualified", {"postgres": "UPDATE products SET price = price * 1.1 WHERE category_id = 3"}, "SELECT id, price FROM products", "postgresql"),
    # ---- PostgreSQL FROM that reads the target again: an unlinked copy, every row changes
    ("us_orders", "set the amount to 0 for orders of Pune customers",
     "UPDATE orders SET amount = 0 FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune'", "update_from_self",
     {"postgres": PG_ORDERS_PUNE}, "SELECT id, amount FROM orders", None),
    ("us_staff", "commission 100 for employees of Pune departments",
     "UPDATE employees SET commission = 100 FROM employees e JOIN departments d ON d.id = e.department_id WHERE d.location = 'Pune'",
     "update_from_self", {"postgres": "UPDATE employees e SET commission = 100 FROM departments d WHERE d.id = e.department_id AND d.location = 'Pune'"},
     "SELECT id, commission FROM employees", None),
    # ---- a joined table never tied to the row being changed
    ("ul_orders", "zero the amount of orders placed by Pune customers (postgres)",
     "UPDATE orders o SET amount = 0 FROM customers c WHERE c.city = 'Pune'", "change_unlinked", None, "SELECT id, amount FROM orders", "postgresql"),
    ("ul_products", "no stock for products from UAE suppliers, on mariadb",
     "UPDATE products p, suppliers s SET p.stock = 0 WHERE s.country = 'UAE'", "change_unlinked", None, "SELECT id, stock FROM products", "mariadb"),
    # ---- one target row, several joined rows: changed once
    ("jm_shipping", "add all payments of each order to its shipping column (mariadb)",
     "UPDATE orders o JOIN payments p ON p.order_id = o.id SET o.shipping = COALESCE(o.shipping, 0) + p.amount", "join_change_many",
     {"mysql": "UPDATE orders o JOIN (SELECT order_id, SUM(amount) AS amount FROM payments GROUP BY order_id) p ON p.order_id = o.id "
               "SET o.shipping = COALESCE(o.shipping, 0) + p.amount"}, "SELECT id, shipping FROM orders", "mariadb"),
    ("jm_stock_pg", "add every ordered quantity back to the product's stock, postgres",
     "UPDATE products p SET stock = p.stock + oi.quantity FROM order_items oi WHERE oi.product_id = p.id", "join_change_many",
     {"postgres": "UPDATE products p SET stock = p.stock + oi.quantity FROM (SELECT product_id, SUM(quantity) AS quantity FROM order_items "
                  "GROUP BY product_id) oi WHERE oi.product_id = p.id"}, "SELECT id, stock FROM products", "postgresql"),
    ("jm_paid_date", "set each order's date to its payment date (mysql)",
     "UPDATE orders o JOIN payments p ON p.order_id = o.id SET o.order_date = p.paid_at", "join_change_many", None,
     "SELECT id, order_date FROM orders", "mysql"),
    # ---- SET col = (SELECT ...) with no WHERE: NULL where nothing matches
    ("sn_discount", "copy the biggest payment of each order into its discount",
     "UPDATE orders o SET discount = (SELECT MAX(p.amount) FROM payments p WHERE p.order_id = o.id)", "set_subquery_null",
     "UPDATE orders o SET discount = (SELECT MAX(p.amount) FROM payments p WHERE p.order_id = o.id) "
     "WHERE EXISTS (SELECT 1 FROM payments p WHERE p.order_id = o.id)", "SELECT id, discount FROM orders", None),
    ("sn_returns", "store each order item's returned quantity in its discount column",
     "UPDATE order_items oi SET discount = (SELECT SUM(r.quantity) FROM returns r WHERE r.order_item_id = oi.id)", "set_subquery_null",
     "UPDATE order_items oi SET discount = (SELECT SUM(r.quantity) FROM returns r WHERE r.order_item_id = oi.id) "
     "WHERE EXISTS (SELECT 1 FROM returns r WHERE r.order_item_id = oi.id)", "SELECT id, discount FROM order_items", None),
    # ---- upserts in each engine's syntax
    ("ud_pg", "insert product 1 or update its price, on postgresql",
     "INSERT INTO products (id, name, price) VALUES (1, 'Phone X', 999) ON DUPLICATE KEY UPDATE price = VALUES(price)", "upsert_syntax",
     {"postgres": "INSERT INTO products (id, name, price) VALUES (1, 'Phone X', 999) ON CONFLICT (id) DO UPDATE SET price = EXCLUDED.price"},
     "SELECT id, name, price FROM products", "postgresql"),
    ("ud_pg_add", "add 5 to the stock of product 1, or insert it (postgres)",
     "INSERT INTO products (id, name, stock) VALUES (1, 'Phone X', 5) ON DUPLICATE KEY UPDATE stock = stock + VALUES(stock)", "upsert_syntax",
     {"postgres": "INSERT INTO products (id, name, stock) VALUES (1, 'Phone X', 5) ON CONFLICT (id) DO UPDATE SET stock = products.stock + EXCLUDED.stock"},
     "SELECT id, stock FROM products", "postgresql"),
    ("uc_mariadb", "insert product 1 or update its price, mariadb",
     "INSERT INTO products (id, name, price) VALUES (1, 'Phone X', 999) ON CONFLICT (id) DO UPDATE SET price = EXCLUDED.price", "upsert_syntax",
     {"mysql": "INSERT INTO products (id, name, price) VALUES (1, 'Phone X', 999) ON DUPLICATE KEY UPDATE price = VALUES(price)"},
     "SELECT id, name, price FROM products", "mariadb"),
    ("uc_nothing", "add the supplier only if it isn't there yet, in mysql",
     "INSERT INTO suppliers (id, name, country) VALUES (1, 'Alpha Traders', 'India') ON CONFLICT (id) DO NOTHING", "upsert_syntax",
     {"mysql": "INSERT INTO suppliers (id, name, country) VALUES (1, 'Alpha Traders', 'India') ON DUPLICATE KEY UPDATE id = id"},
     "SELECT id, name, country FROM suppliers", "mysql"),
    ("ca_stock", "add 5 to product 1's stock, or insert it",
     "INSERT INTO products (id, name, stock) VALUES (1, 'Phone X', 5) ON CONFLICT (id) DO UPDATE SET stock = stock + EXCLUDED.stock",
     "conflict_ambiguous", {"postgres": "INSERT INTO products (id, name, stock) VALUES (1, 'Phone X', 5) ON CONFLICT (id) DO UPDATE SET "
                                       "stock = products.stock + EXCLUDED.stock"}, "SELECT id, stock FROM products", None),
    ("ct_price", "insert product 1 or update its price",
     "INSERT INTO products (id, name, price) VALUES (1, 'Phone X', 999) ON CONFLICT DO UPDATE SET price = EXCLUDED.price",
     "conflict_no_target", {"postgres": "INSERT INTO products (id, name, price) VALUES (1, 'Phone X', 999) ON CONFLICT (id) DO UPDATE SET "
                                        "price = EXCLUDED.price"}, "SELECT id, price FROM products", None),
    # ---- INSERT ... SELECT fills by position
    ("io_supplier", "copy supplier 1 as supplier 9",
     "INSERT INTO suppliers (id, name, country) SELECT 9, country, name FROM suppliers WHERE id = 1", "insert_order",
     "INSERT INTO suppliers (id, name, country) SELECT 9, name, country FROM suppliers WHERE id = 1", "SELECT id, name, country FROM suppliers", None),
    ("io_customer", "copy customer 1 as customer 101",
     "INSERT INTO customers (id, first_name, last_name, email) SELECT id + 100, last_name, first_name, email FROM customers WHERE id = 1",
     "insert_order", "INSERT INTO customers (id, first_name, last_name, email) SELECT id + 100, first_name, last_name, email FROM customers WHERE id = 1",
     "SELECT id, first_name, last_name, email FROM customers", None),
    ("io_alias", "add a category for each supplier country",
     "INSERT INTO categories (id, name, parent_id) SELECT id + 10, NULL AS parent_id, country AS name FROM suppliers", "insert_order",
     "INSERT INTO categories (id, name, parent_id) SELECT id + 10, country, NULL FROM suppliers", "SELECT id, name, parent_id FROM categories", None),
    # ---- too few values
    ("ic_select", "copy supplier 1 as supplier 9", "INSERT INTO suppliers (id, name, country) SELECT 9, name FROM suppliers WHERE id = 1",
     "insert_count", None, "SELECT id FROM suppliers", None),
    ("ic_values", "add the category Games", "INSERT INTO categories (id, name, parent_id) VALUES (6, 'Games')", "insert_count", None,
     "SELECT id FROM categories", None),
    ("in_nocols", "add supplier Delta from India", "INSERT INTO suppliers VALUES (9, 'Delta', 'India')", "insert_no_columns", None,
     "SELECT id FROM suppliers", None),
    # ---- LIMIT on UPDATE / DELETE: PostgreSQL has none
    ("cl_delete", "delete the 2 oldest paid orders (postgresql)", "DELETE FROM orders WHERE status = 'paid' ORDER BY order_date LIMIT 2",
     "change_limit", {"postgres": "DELETE FROM orders WHERE id IN (SELECT id FROM orders WHERE status = 'paid' ORDER BY order_date LIMIT 2)"},
     "SELECT id FROM orders", "postgresql"),
    ("cl_update", "archive the oldest shipped order, postgres", "UPDATE orders SET status = 'archived' WHERE status = 'shipped' ORDER BY order_date LIMIT 1",
     "change_limit", {"postgres": "UPDATE orders SET status = 'archived' WHERE id IN (SELECT id FROM orders WHERE status = 'shipped' "
                                  "ORDER BY order_date LIMIT 1)"}, "SELECT id, status FROM orders", "postgresql"),
    # ---- deleting duplicates through a self-join
    ("dd_all", "delete duplicate customers by email, keep one of each",
     "DELETE c1 FROM customers c1 JOIN customers c2 ON c1.email = c2.email", "dedupe_self",
     {"mysql": "DELETE c1 FROM customers c1 JOIN customers c2 ON c1.email = c2.email AND c1.id > c2.id"}, "SELECT id FROM customers", None),
    ("dd_both_pg", "remove duplicate customers (same email), keep the first one, postgresql",
     "DELETE FROM customers c1 USING customers c2 WHERE c1.email = c2.email AND c1.id <> c2.id", "dedupe_self",
     {"postgres": "DELETE FROM customers c1 USING customers c2 WHERE c1.email = c2.email AND c1.id > c2.id"}, "SELECT id FROM customers", "postgresql"),
    ("dd_staff", "employees with the same salary in the same department are duplicates: keep one",
     "DELETE e1 FROM employees e1 JOIN employees e2 ON e1.salary = e2.salary AND e1.department_id = e2.department_id", "dedupe_self",
     {"mysql": "DELETE e1 FROM employees e1 JOIN employees e2 ON e1.salary = e2.salary AND e1.department_id = e2.department_id AND e1.id > e2.id"},
     "SELECT id FROM employees", None),
    # ---- DELETE through a join: say which table, in each engine's syntax
    ("dn_mariadb", "delete the orders of Pune customers (mariadb)",
     "DELETE FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune'", "delete_no_target",
     {"mysql": "DELETE o FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune'"}, "SELECT id FROM orders", "mariadb"),
    ("dn_pg", "delete the orders of Pune customers in postgres",
     "DELETE FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune'", "delete_no_target",
     {"postgres": "DELETE FROM orders o USING customers c WHERE c.id = o.customer_id AND c.city = 'Pune'"}, "SELECT id FROM orders", "postgresql"),
    ("dj_pg", "delete the orders of Pune customers (PostgreSQL)",
     "DELETE o FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune'", "delete_join_syntax",
     {"postgres": "DELETE FROM orders o USING customers c WHERE c.id = o.customer_id AND c.city = 'Pune'"}, "SELECT id FROM orders", "postgresql"),
    ("du_mariadb", "delete the orders of Pune customers, mariadb",
     "DELETE FROM orders o USING customers c WHERE c.id = o.customer_id AND c.city = 'Pune'", "delete_join_syntax",
     {"mysql": "DELETE o FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune'"}, "SELECT id FROM orders", "mariadb"),
    # ---- INSERT IGNORE and REPLACE
    ("ig_pg", "add products 1 and 20, skip the ones that exist (postgres)",
     "INSERT IGNORE INTO products (id, name) VALUES (1, 'dup'), (20, 'new')", "insert_ignore",
     {"postgres": "INSERT INTO products (id, name) VALUES (1, 'dup'), (20, 'new') ON CONFLICT DO NOTHING"}, "SELECT id, name FROM products", "postgresql"),
    ("ig_my", "add products 1 and 20, skip the ones that exist", "INSERT IGNORE INTO products (id, name) VALUES (1, 'dup'), (20, 'new')",
     "insert_ignore", None, "SELECT id, name FROM products", None),
    ("rp_name", "rename product 1, keep the rest of the row (mariadb)", "REPLACE INTO products (id, name) VALUES (1, 'Phone X2')", "replace_into",
     {"mysql": "UPDATE products SET name = 'Phone X2' WHERE id = 1"}, "SELECT id, name, price, stock FROM products", "mariadb"),
    ("rp_pg", "insert or rename product 1 in postgres", "REPLACE INTO products (id, name) VALUES (1, 'Phone X2')", "replace_into",
     {"postgres": "INSERT INTO products (id, name) VALUES (1, 'Phone X2') ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name"},
     "SELECT id, name, price, stock FROM products", "postgresql"),
]

# what each engine does with the wrong statement: 'rows' (runs; check the table), 'error' (refused)
LAB = {
    "uj_pg": ("rows", "error"),           # PostgreSQL: syntax error at or near "JOIN"
    "uf_mariadb": ("error", "rows"),      # MariaDB 1064 near 'FROM customers'
    "sq_pg": ("rows", "error"),           # PostgreSQL 42703 column "o" of relation "orders" does not exist
    "us_orders": ("error", "rows"),       # PostgreSQL: all 8 orders set to 0, not 2
    "ul_orders": ("error", "rows"),       # PostgreSQL: all 8 orders
    "ul_products": ("rows", "error"),     # MariaDB: all 7 products out of stock
    "jm_shipping": ("rows", "error"),     # MariaDB: order 3 gets 40000, not 79000
    "jm_stock_pg": ("error", "rows"),     # PostgreSQL: product 4 gets one of its 4 quantities
    "sn_discount": ("rows", "rows"),      # orders 4, 6, 8 lose their discount (NULL)
    "ud_pg": ("rows", "error"),           # PostgreSQL: syntax error at or near "DUPLICATE"
    "uc_mariadb": ("error", "rows"),      # MariaDB 1064 near 'CONFLICT'
    "ca_stock": ("error", "error"),       # PostgreSQL 42702 column reference "stock" is ambiguous
    "ct_price": ("error", "error"),       # PostgreSQL: ON CONFLICT DO UPDATE requires inference specification or constraint name
    "io_supplier": ("rows", "rows"),      # supplier 9 is named 'India' from the country 'Alpha Traders'
    "ic_select": ("error", "error"),      # MariaDB 1136; PostgreSQL: INSERT has more target columns than expressions
    "ic_values": ("error", "error"),
    "in_nocols": ("error", "rows"),       # MariaDB 1136 (suppliers has 4 columns); PostgreSQL fills email with NULL
    "cl_delete": ("rows", "error"),       # PostgreSQL: syntax error at or near "ORDER"
    "dd_all": ("rows", "error"),          # MariaDB: all 8 customers deleted
    "dd_both_pg": ("error", "rows"),      # PostgreSQL: both copies of a@ / diya@ ... deleted
    "dn_mariadb": ("error", "error"),     # MariaDB 1064; PostgreSQL syntax error at or near "JOIN"
    "dj_pg": ("rows", "error"),
    "du_mariadb": ("error", "rows"),      # MariaDB 1064
    "ig_pg": ("rows", "error"),           # PostgreSQL: syntax error at or near "IGNORE"
    "rp_name": ("rows", "error"),         # MariaDB: price and stock of product 1 become NULL
}


@pytest.fixture(scope="module")
def engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


def _effect(eng, stmt, query):
    """The table after the statement (on fresh practice tables), or 'error'."""
    S.build(eng)
    try:
        eng.run(stmt)
    except Exception:  # noqa: BLE001 - the engine refused it
        eng.rollback()
        return "error"
    return S.rows(eng, query)


def _correct(fix, name):
    return fix if isinstance(fix, str) else (fix or {}).get(name)


def test_the_bank_has_enough_problems():
    assert len(P) + len(R) >= 30 and len({p[0] for p in P} | {r[0] for r in R}) == len(P) + len(R)


def test_the_lab_table_still_holds(engines):
    """What each engine does with the wrong statement is what the lab saw (a rule is only as good as this)."""
    by = {p[0]: p for p in P}
    for pid, (my, pg) in LAB.items():
        _pid, _w, wrong, _t, fix, query, _s = by[pid]
        for name, want in (("mysql", my), ("postgres", pg)):
            got = _effect(engines[name], wrong, query)
            assert (got == "error") == (want == "error"), (pid, name, got)
            right = _correct(fix, name)
            if want == "rows" and right:                # it ran: the rows it left are not the verified answer
                assert got != _effect(engines[name], right, query), (pid, name, "the wrong statement did the right thing")


@pytest.mark.parametrize("pid,words,wrong,topic,fix,query,said", P, ids=[p[0] for p in P])
def test_dbaba_finds_and_fixes(engines, pid, words, wrong, topic, fix, query, said):
    res = analyze_sql(f"{wrong};\n\n{words}", engine=said)
    assert res.get("ok"), (pid, res.get("error"))
    found = [f for f in res["correct"]["findings"] if f["topic"] == topic]
    assert found, (pid, [(f["topic"], f["title_en"]) for f in res["correct"]["findings"]])
    assert all(f.get("basis") for f in found), (pid, "every finding names its fundamental")
    if fix is None:
        return
    offered = res["correct"].get("corrected")
    assert offered, (pid, "no corrected / suggested statement")
    ran = 0
    for name, eng in engines.items():
        right = _correct(fix, name)
        if right is None:
            continue
        ran += 1
        assert _effect(eng, offered, query) == _effect(eng, right, query), (pid, name, offered)
    assert ran


def test_change_statements_get_a_preview():
    """Fundamental 'changes_safety': every UPDATE / DELETE comes with the read-only SELECT of the rows it changes."""
    for q, want in [
        ("UPDATE orders SET status = 'paid' WHERE id = 3", "SELECT orders.*, 'paid' AS new_status FROM orders WHERE id = 3"),
        (MY_ORDERS_PUNE, "SELECT o.*, 0 AS new_amount FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune'"),
        (PG_ORDERS_PUNE, "SELECT o.*, 0 AS new_amount FROM orders o, customers c WHERE c.id = o.customer_id AND c.city = 'Pune'"),
        ("DELETE FROM orders WHERE status = 'cancelled'", "SELECT orders.* FROM orders WHERE status = 'cancelled'"),
        ("DELETE o FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune'",
         "SELECT o.* FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune'"),
    ]:
        assert analyze_sql(q)["preview"] == want, q


def test_previews_only_read_and_show_the_rows(engines):
    eng = engines["postgres"]
    S.build(eng)
    rows = S.rows(eng, analyze_sql(PG_ORDERS_PUNE)["preview"].replace("o.*", "o.id"))
    assert rows == [("1", "0"), ("2", "0")]


def test_valid_changes_raise_no_alarm():
    """Right statements of every shape: no error/wrong finding."""
    for q, eng in [
        (MY_ORDERS_PUNE, None), (MY_ORDERS_PUNE, "mariadb"), (PG_ORDERS_PUNE, None), (PG_ORDERS_PUNE, "postgresql"),
        ("UPDATE orders SET amount = 0 WHERE id = 1", "postgresql"),
        ("UPDATE orders o JOIN (SELECT order_id, SUM(amount) AS paid FROM payments GROUP BY order_id) p ON p.order_id = o.id SET o.shipping = p.paid", None),
        ("UPDATE customers c JOIN orders o ON o.id = c.id SET c.city = 'X'", None),
        ("UPDATE orders o SET discount = (SELECT MAX(p.amount) FROM payments p WHERE p.order_id = o.id) "
         "WHERE EXISTS (SELECT 1 FROM payments p WHERE p.order_id = o.id)", None),
        ("UPDATE orders o SET discount = COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.order_id = o.id), 0)", None),
        ("UPDATE customers c SET city = (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id)", None),
        ("INSERT INTO products (id, name, price) VALUES (1, 'Phone X', 999) ON DUPLICATE KEY UPDATE price = VALUES(price)", None),
        ("INSERT INTO products (id, name, price) VALUES (1, 'Phone X', 999) ON CONFLICT (id) DO UPDATE SET price = EXCLUDED.price", None),
        ("INSERT INTO products (id, name, stock) VALUES (1, 'Phone X', 5) ON CONFLICT (id) DO UPDATE SET stock = products.stock + EXCLUDED.stock", None),
        ("INSERT INTO suppliers (id, name, country) SELECT 9, name, country FROM suppliers WHERE id = 1", None),
        ("INSERT INTO payments (id, order_id, amount) SELECT o.id + 100, o.id, o.amount FROM orders o WHERE o.status = 'pending'", None),
        ("INSERT INTO categories (id, name) VALUES (6, 'Games'), (7, 'Garden')", None),
        ("DELETE c1 FROM customers c1 JOIN customers c2 ON c1.email = c2.email AND c1.id > c2.id", None),
        ("DELETE FROM customers c1 USING customers c2 WHERE c1.email = c2.email AND c1.id > c2.id", None),
        ("DELETE FROM orders WHERE status = 'cancelled' LIMIT 1000", None),
        ("DELETE FROM orders WHERE ctid IN (SELECT ctid FROM orders WHERE status = 'cancelled' LIMIT 1000)", "postgresql"),
        ("DELETE o FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune'", "mysql"),
        ("DELETE FROM orders o USING customers c WHERE c.id = o.customer_id AND c.city = 'Pune'", "postgresql"),
    ]:
        res = analyze_sql(q, engine=eng)
        bad = [(f["topic"], f["title_en"]) for f in res["correct"]["findings"] if f["kind"] in ("error", "wrong")]
        assert not bad, (q, eng, bad)


@pytest.mark.parametrize("said,kind", [("postgresql", "error"), ("mariadb", None)])
def test_engine_syntax_follows_the_database(said, kind):
    """Rule A: MySQL's UPDATE ... JOIN is an error for PostgreSQL, right for MariaDB, and not flagged when the text shows MySQL."""
    res = analyze_sql(MY_ORDERS_PUNE, engine=said)
    got = [f["kind"] for f in res["correct"]["findings"] if f["topic"] == "update_join_syntax"]
    assert got == ([kind] if kind else []), (said, got)
    assert not [f for f in analyze_sql(MY_ORDERS_PUNE)["correct"]["findings"] if f["topic"] == "update_join_syntax"]


def test_every_rule_records_where_it_was_proven():
    """Standing rule B: each rule and each engine fact says which engines the lab proved it on."""
    import json

    from app.agents import sqlchange, sqlroutine
    from app.agents.sqlread import fundamentals

    topics = {p[3] for p in P} | {r[3] for r in R}
    assert topics <= set(sqlchange.PROVEN) | set(sqlroutine.PROVEN)
    assert all(set(v) >= {"mariadb", "postgresql"} for v in {**sqlchange.PROVEN, **sqlroutine.PROVEN}.values())
    funds = fundamentals()["fundamentals"]
    assert all(any(t in f["topics"] for f in funds.values()) for t in {**sqlchange.PROVEN, **sqlroutine.PROVEN}), "every topic has its fundamental"
    for eng in ("mysql", "mariadb", "postgresql"):
        rules = json.load(open(f"app/dbknow/dialects/{eng}.json"))["query_rules"]
        for key in ("update_join", "update_from", "set_qualified", "delete_join", "delete_using", "on_duplicate_key", "on_conflict",
                    "change_limit", "insert_ignore", "replace_into"):
            assert rules[key]["proven"] and rules[key]["says"] and rules[key]["behaviour"] in ("works", "error"), (eng, key)


# ------------------------------------------------------------------------------------------------ routines
STOCK = "CREATE TABLE n5_stock (id INT PRIMARY KEY, qty INT, note VARCHAR(40))"
STOCK_ROWS = "INSERT INTO n5_stock VALUES (1, 5, NULL), (2, 0, NULL)"
CHECK = "SELECT id, qty, note FROM n5_stock ORDER BY id"
MY_WHILE = """CREATE PROCEDURE n5_fill()
BEGIN
  DECLARE i INT DEFAULT 0;
  WHILE i < 3 DO
    UPDATE n5_stock SET qty = qty + 1 WHERE id = 1;
  END WHILE;
END"""
# (id, words, wrong script, topic, said, fire (statement run after the script), correct script or None)
R = [
    ("rw_while", "fill 3 rows (mariadb)", "DELIMITER //\n" + MY_WHILE + " //\nDELIMITER ;", "endless_loop", "mariadb", None, None),
    ("rw_while_pg", "count to 3", "DO $$ DECLARE i int := 0; BEGIN WHILE i < 3 LOOP PERFORM 1; END LOOP; END $$", "endless_loop",
     "postgresql", None, None),
    ("rw_loop", "read all rows, mysql",
     "DELIMITER //\nCREATE PROCEDURE n5_read()\nBEGIN\n  DECLARE v INT;\n  read_loop: LOOP\n    SELECT qty INTO v FROM n5_stock LIMIT 1;\n  END LOOP;\nEND //\nDELIMITER ;",
     "endless_loop", "mariadb", None, None),
    ("rc_handler", "sum the stock with a cursor (mariadb)",
     "DELIMITER //\nCREATE PROCEDURE n5_sum()\nBEGIN\n  DECLARE v INT;\n  DECLARE s INT DEFAULT 0;\n  DECLARE cur CURSOR FOR SELECT qty FROM n5_stock;\n"
     "  OPEN cur;\n  read_loop: LOOP\n    FETCH cur INTO v;\n    IF v IS NULL THEN LEAVE read_loop; END IF;\n    SET s = s + v;\n  END LOOP;\n  CLOSE cur;\n"
     "  SELECT s;\nEND //\nDELIMITER ;", "cursor_handler", "mariadb", "CALL n5_sum()", None),
    ("rd_order", "a procedure with a counter (mariadb)",
     "DELIMITER //\nCREATE PROCEDURE n5_cnt()\nBEGIN\n  SET @started = 1;\n  DECLARE n INT DEFAULT 0;\n  SELECT COUNT(*) INTO n FROM n5_stock;\nEND //\nDELIMITER ;",
     "declare_order", "mariadb", None, None),
    ("rd_cursor_order", "cursor with a handler (mariadb)",
     "DELIMITER //\nCREATE PROCEDURE n5_cur()\nBEGIN\n  DECLARE done INT DEFAULT 0;\n  DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;\n"
     "  DECLARE cur CURSOR FOR SELECT qty FROM n5_stock;\nEND //\nDELIMITER ;", "declare_order", "mariadb", None, None),
    ("rs_same_my", "stamp the row as changed whenever it is updated (mariadb)",
     "CREATE TRIGGER n5_tr AFTER UPDATE ON n5_stock FOR EACH ROW UPDATE n5_stock SET note = 'changed' WHERE id = NEW.id",
     "trigger_same_table", "mariadb", "UPDATE n5_stock SET qty = 7 WHERE id = 1",
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW SET NEW.note = 'changed'"),
    ("ra_new_my", "stamp the row as changed whenever it is updated, mysql",
     "CREATE TRIGGER n5_tr AFTER UPDATE ON n5_stock FOR EACH ROW SET NEW.note = 'changed'", "after_new", "mariadb",
     "UPDATE n5_stock SET qty = 7 WHERE id = 1", "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW SET NEW.note = 'changed'"),
    ("rf_return_my", "a function that doubles positive numbers (mariadb)",
     "DELIMITER //\nCREATE FUNCTION n5_f(x INT) RETURNS INT\nBEGIN\n  IF x > 0 THEN\n    RETURN x * 2;\n  END IF;\nEND //\nDELIMITER ;",
     "function_return", "mariadb", "UPDATE n5_stock SET qty = n5_f(qty - 3)",
     "DELIMITER //\nCREATE FUNCTION n5_f(x INT) RETURNS INT\nBEGIN\n  IF x > 0 THEN\n    RETURN x * 2;\n  END IF;\n  RETURN NULL;\nEND //\nDELIMITER ;"),
    ("rf_return_pg", "a function that doubles positive numbers (postgres)",
     "CREATE FUNCTION n5_f(x int) RETURNS int LANGUAGE plpgsql AS $$ BEGIN IF x > 0 THEN RETURN x * 2; END IF; END $$", "function_return",
     "postgresql", "UPDATE n5_stock SET qty = n5_f(qty - 3)",
     "CREATE FUNCTION n5_f(x int) RETURNS int LANGUAGE plpgsql AS $$ BEGIN IF x > 0 THEN RETURN x * 2; END IF; RETURN NULL; END $$"),
    ("rt_noreturn", "stamp the row as changed (postgresql)",
     "CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.note := 'changed'; END $$;\n"
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();", "trigger_return", "postgresql",
     "UPDATE n5_stock SET qty = 7 WHERE id = 1",
     "CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.note := 'changed'; RETURN NEW; END $$;\n"
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();"),
    ("rt_null", "stamp the row as changed, postgres",
     "CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.note := 'changed'; RETURN NULL; END $$;\n"
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();", "trigger_return", "postgresql",
     "UPDATE n5_stock SET qty = 7 WHERE id = 1",
     "CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.note := 'changed'; RETURN NEW; END $$;\n"
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();"),
    ("ra_new_pg", "stamp the row as changed (postgresql)",
     "CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.note := 'changed'; RETURN NEW; END $$;\n"
     "CREATE TRIGGER n5_tr AFTER UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();", "after_new", "postgresql",
     "UPDATE n5_stock SET qty = 7 WHERE id = 1",
     "CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.note := 'changed'; RETURN NEW; END $$;\n"
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();"),
    ("rs_same_pg", "stamp the row as changed whenever it is updated, postgres",
     "CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN UPDATE n5_stock SET note = 'changed' WHERE id = NEW.id; RETURN NEW; END $$;\n"
     "CREATE TRIGGER n5_tr AFTER UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();", "trigger_same_table", "postgresql",
     "UPDATE n5_stock SET qty = 7 WHERE id = 1", None),
    ("rb_pg_trigger", "stamp the row as changed whenever it is updated (postgresql)",
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW SET NEW.note = 'changed'", "trigger_body_pg", "postgresql",
     "UPDATE n5_stock SET qty = 7 WHERE id = 1",
     "CREATE FUNCTION n5_tr_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.note := 'changed'; RETURN NEW; END $$;\n"
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_tr_fn();"),
    ("rb_pg_set", "stamp the row as changed (postgres)",
     "CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN SET NEW.note = 'changed'; RETURN NEW; END $$;\n"
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();", "trigger_body_pg", "postgresql",
     "UPDATE n5_stock SET qty = 7 WHERE id = 1",
     "CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.note := 'changed'; RETURN NEW; END $$;\n"
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();"),
    ("rb_pg_set_expr", "stamp the row with today's date (postgres)",
     "CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN SET NEW.note = CURRENT_DATE::text; RETURN NEW; END $$;\n"
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();", "trigger_body_pg", "postgresql",
     "UPDATE n5_stock SET qty = 7 WHERE id = 1",
     "CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.note := CURRENT_DATE::text; RETURN NEW; END $$;\n"
     "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();"),
    ("rl_language", "a function that doubles a number (postgresql)",
     "CREATE FUNCTION n5_f(x int) RETURNS int AS $$ BEGIN RETURN x * 2; END $$", "no_language", "postgresql",
     "UPDATE n5_stock SET qty = n5_f(qty)", "CREATE FUNCTION n5_f(x int) RETURNS int LANGUAGE plpgsql AS $$ BEGIN RETURN x * 2; END $$"),
    ("rx_delimiter", "fill 3 rows, mariadb", MY_WHILE.replace("UPDATE n5_stock", "SET i = i + 1;\n    UPDATE n5_stock"), "delimiter", "mariadb",
     "CALL n5_fill()", "DELIMITER //\n" + MY_WHILE.replace("UPDATE n5_stock", "SET i = i + 1;\n    UPDATE n5_stock") + " //\nDELIMITER ;"),
]

# what each engine does with the wrong script (on the engine it is written for)
R_LAB = {
    "rw_while": "timeout",       # MariaDB: ERROR 1969 max_statement_time exceeded
    "rw_while_pg": "timeout",    # PostgreSQL: canceling statement due to statement timeout
    "rc_handler": "error",       # MariaDB 1329 No data - zero rows fetched (at the end of the rows)
    "rd_order": "error",         # MariaDB 1064 near 'DECLARE n INT'
    "rd_cursor_order": "error",  # MariaDB 1338 Cursor declaration after handler declaration
    "rs_same_my": "error",       # MariaDB 1442 Can't update table 'n5_stock' in stored function/trigger (when it fires)
    "ra_new_my": "error",        # MariaDB 1362 Updating of NEW row is not allowed in after trigger
    "rf_return_my": "error",     # MariaDB 1321 FUNCTION n5_f ended without RETURN (for qty - 3 <= 0)
    "rf_return_pg": "error",     # PostgreSQL: control reached end of function without RETURN
    "rt_noreturn": "error",      # PostgreSQL: control reached end of trigger procedure without RETURN
    "rt_null": "nothing",        # PostgreSQL: 0 rows updated, no error
    "ra_new_pg": "nothing",      # PostgreSQL: the update runs, note stays empty
    "rs_same_pg": "error",       # PostgreSQL: stack depth limit exceeded
    "rb_pg_trigger": "error",    # PostgreSQL: syntax error at or near "SET"
    "rb_pg_set": "nothing",      # PostgreSQL: SET NEW.note = 'changed' sets a custom setting named new.note; the row stays as it was
    "rb_pg_set_expr": "error",   # PostgreSQL: syntax error at or near "CURRENT_DATE" (SET takes only a plain value)
    "rl_language": "error",      # PostgreSQL: no language specified
    "rx_delimiter": "error",     # mysql client: ERROR 1064 near '' (the CREATE is cut at the first ';')
}


def _fresh(eng):
    """A clean n5_stock with two rows, and none of tonight's routines left over."""
    if eng.kind == "mysql":
        drops = ["DROP TRIGGER IF EXISTS n5_tr"] + [f"DROP PROCEDURE IF EXISTS {p}" for p in ("n5_fill", "n5_read", "n5_sum", "n5_cnt", "n5_cur")] + \
                ["DROP FUNCTION IF EXISTS n5_f", "DROP TABLE IF EXISTS n5_stock"]
    else:
        drops = ["DROP TABLE IF EXISTS n5_stock CASCADE"] + [f"DROP FUNCTION IF EXISTS {f} CASCADE" for f in ("n5_f", "n5_fn", "n5_tr_fn")]
    for d in drops:
        try:
            eng.run(d)
        except Exception:  # noqa: BLE001
            eng.rollback()
    eng.run(STOCK)
    eng.run(STOCK_ROWS)


CALLS = {"rw_while": "CALL n5_fill()", "rw_loop": "CALL n5_read()"}


def _outcome(eng, script, fire, call=None):
    """'error' / 'timeout' / 'ran' / the table after the fire statement. The script is sent the way the client sends it
    (DELIMITER and $$ understood, a bare body cut at every ';', as the mysql client does)."""
    _fresh(eng)
    eng.run("SET SESSION max_statement_time = 2" if eng.kind == "mysql" else "SET statement_timeout = '2s'")
    try:
        for st in split_script(script):
            eng.run(st)
        if call:
            eng.run(call)
        if fire:
            eng.run(fire)
            return S.rows(eng, CHECK)
        return "ran"
    except Exception as exc:  # noqa: BLE001
        eng.rollback()
        return "timeout" if "timeout" in str(exc) or "max_statement_time" in str(exc) else "error"
    finally:
        try:
            eng.run("SET SESSION max_statement_time = 0" if eng.kind == "mysql" else "RESET statement_timeout")
        except Exception:  # noqa: BLE001
            eng.rollback()


def _eng_of(engines, said):
    return engines["postgres" if said == "postgresql" else "mysql"]


def test_the_routine_lab_still_holds(engines):
    by = {r[0]: r for r in R}
    for rid, want in R_LAB.items():
        _r, _w, wrong, _t, said, fire, _fix = by[rid]
        got = _outcome(_eng_of(engines, said), wrong, fire, CALLS.get(rid))
        if want == "nothing":                       # it ran without an error, and the row was not stamped
            assert isinstance(got, list) and all(row[2] == "NULL" for row in got), (rid, got)
        else:
            assert got == want, (rid, got)


@pytest.mark.parametrize("rid,words,wrong,topic,said,fire,fix", R, ids=[r[0] for r in R])
def test_dbaba_reads_routines(engines, rid, words, wrong, topic, said, fire, fix):
    res = analyze_sql(wrong, engine=said)
    assert res.get("ok"), (rid, res.get("error"))
    found = [f for f in res["correct"]["findings"] if f["topic"] == topic]
    assert found, (rid, [(f["topic"], f["title_en"]) for f in res["correct"]["findings"]])
    assert all(f.get("basis") for f in found), (rid, "every finding names its fundamental")
    if fix is None:
        return
    offered = res["correct"].get("corrected")
    assert offered, (rid, "no corrected script")
    eng = _eng_of(engines, said)
    got, want = _outcome(eng, offered, fire), _outcome(eng, fix, fire)
    assert got not in ("error", "timeout") and got == want, (rid, offered, got, want)


def test_good_routines_raise_no_alarm():
    for q, said in [
        ("DELIMITER //\nCREATE PROCEDURE n5_fill()\nBEGIN\n  DECLARE i INT DEFAULT 0;\n  WHILE i < 3 DO\n    SET i = i + 1;\n  END WHILE;\nEND //\nDELIMITER ;", None),
        ("DELIMITER //\nCREATE PROCEDURE n5_sum()\nBEGIN\n  DECLARE done INT DEFAULT 0;\n  DECLARE v INT;\n  DECLARE s INT DEFAULT 0;\n"
         "  DECLARE cur CURSOR FOR SELECT qty FROM n5_stock;\n  DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;\n  OPEN cur;\n"
         "  rd: LOOP\n    FETCH cur INTO v;\n    IF done THEN LEAVE rd; END IF;\n    SET s = s + v;\n  END LOOP;\n  CLOSE cur;\n  SELECT s;\nEND //\nDELIMITER ;", None),
        ("CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW SET NEW.note = 'changed'", None),
        ("CREATE TRIGGER n5_log AFTER UPDATE ON n5_stock FOR EACH ROW INSERT INTO n5_log (stock_id) VALUES (NEW.id)", None),
        ("CREATE FUNCTION n5_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.note := 'changed'; RETURN NEW; END $$;\n"
         "CREATE TRIGGER n5_tr BEFORE UPDATE ON n5_stock FOR EACH ROW EXECUTE FUNCTION n5_fn();", None),
        ("CREATE FUNCTION n5_f(x int) RETURNS int LANGUAGE plpgsql AS $$ BEGIN IF x > 0 THEN RETURN x; ELSE RETURN 0; END IF; END $$", None),
        ("CREATE FUNCTION n5_f(x int) RETURNS int AS $$ SELECT x * 2 $$ LANGUAGE sql", None),
        ("DO $$ DECLARE i int := 0; BEGIN WHILE i < 3 LOOP i := i + 1; END LOOP; END $$", None),
        ("DO $$ BEGIN FOR i IN 1..3 LOOP PERFORM 1; END LOOP; END $$", None),
        ("DO $$ DECLARE r record; BEGIN LOOP EXIT WHEN NOT FOUND; END LOOP; END $$", None),
        ("DELIMITER //\nCREATE PROCEDURE n5_batch()\nBEGIN\n  REPEAT\n    DELETE FROM n5_stock WHERE qty = 0 LIMIT 1000;\n  UNTIL ROW_COUNT() = 0 END REPEAT;\nEND //\nDELIMITER ;", None),
    ]:
        res = analyze_sql(q, engine=said)
        assert res.get("ok"), q
        bad = [(f["topic"], f["title_en"]) for f in res["correct"]["findings"] if f["kind"] in ("error", "wrong")]
        assert not bad, (q, bad)


def test_split_script_runs_like_the_clients():
    assert split_script("DELIMITER //\nCREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END //\nDELIMITER ;\nCALL p();") == \
        ["CREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END", "CALL p()"]
    assert split_script("CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $$ BEGIN RETURN 1; END $$; SELECT f();") == \
        ["CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $$ BEGIN RETURN 1; END $$", "SELECT f()"]
    assert split_script("CREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END") == ["CREATE PROCEDURE p() BEGIN SELECT 1", "SELECT 2", "END"]
    assert split_script("CREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END", blocks=True) == ["CREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END"]
