"""Night S1 practice bank: writing a query from a description.

Each problem is a request in words (English, Hinglish or Hindi) about the made-up shop of tests/practice/schema.py
(customers, orders, order_items, products, categories, suppliers, payments, returns), the shop's CREATE TABLE given as
the schema, and a query written here independently. DBaba's query (app/agents/sqlwrite.py) must return the same rows on
MariaDB and on PostgreSQL. Then the same kind of request through the chat: it reaches the writer (never the checker's
"paste the query", the concepts or the generic reply), and concept questions, pasted queries and errors still go where
they went.
"""

import re
from collections import Counter

import pytest

from app.products.dbaba import sqlproject
from app.products.dbaba import sqlwrite
from tests.practice import schema as S

DDL = "\n".join(f"CREATE TABLE {n} ({', '.join(cols)}, PRIMARY KEY (id));" for n, (cols, _rows) in S.TABLES.items()) + """
ALTER TABLE orders ADD FOREIGN KEY (customer_id) REFERENCES customers(id);
ALTER TABLE order_items ADD FOREIGN KEY (order_id) REFERENCES orders(id);
ALTER TABLE order_items ADD FOREIGN KEY (product_id) REFERENCES products(id);
ALTER TABLE products ADD FOREIGN KEY (category_id) REFERENCES categories(id);
ALTER TABLE products ADD FOREIGN KEY (supplier_id) REFERENCES suppliers(id);
ALTER TABLE payments ADD FOREIGN KEY (order_id) REFERENCES orders(id);
"""
SCHEMA = sqlproject.from_text(DDL)

# (id, request, truth, compare)   compare: bag (same rows) / ordered (same rows in the same order)
P = [
    # ---- selection and filters
    ("cols_filter", "write a query to list first_name and city of customers where city is Pune",
     "SELECT first_name, city FROM customers WHERE city = 'Pune'", "bag"),
    ("num_more", "show products with price more than 10000", "SELECT * FROM products WHERE price > 10000", "bag"),
    ("num_at_least", "write a query to fetch name and price of products where price is at least 800",
     "SELECT name, price FROM products WHERE price >= 800", "bag"),
    ("in_list", "show orders where status is paid or shipped", "SELECT * FROM orders WHERE status IN ('paid', 'shipped')", "bag"),
    ("in_list_3", "list orders whose status is paid, shipped or pending",
     "SELECT * FROM orders WHERE status IN ('paid', 'shipped', 'pending')", "bag"),
    ("not_equal", "write a query to get orders where status is not cancelled",
     "SELECT * FROM orders WHERE status <> 'cancelled'", "bag"),
    ("between", "find orders with amount between 500 and 30000", "SELECT * FROM orders WHERE amount BETWEEN 500 AND 30000", "bag"),
    ("is_null", "list customers where phone is missing", "SELECT * FROM customers WHERE phone IS NULL", "bag"),
    ("not_null", "get customers that have a non-null middle_name", "SELECT * FROM customers WHERE middle_name IS NOT NULL", "bag"),
    ("starts_with", "show products whose name starts with Laptop", "SELECT * FROM products WHERE name LIKE 'Laptop%'", "bag"),
    ("contains", "write a query to find suppliers whose name contains Goods", "SELECT * FROM suppliers WHERE name LIKE '%Goods%'", "bag"),
    ("two_conds", "show products where stock is 0 and price less than 20000",
     "SELECT * FROM products WHERE stock = 0 AND price < 20000", "bag"),
    ("date_after", "list orders where order_date after 2024-07-01", "SELECT * FROM orders WHERE order_date > '2024-07-01'", "bag"),
    # ---- order and top N
    ("top_n", "show top 3 products sorted by price highest first", "SELECT * FROM products ORDER BY price DESC LIMIT 3", "ordered"),
    ("lowest_first", "list orders sorted by amount lowest first", "SELECT * FROM orders ORDER BY amount ASC, id", "ordered_key"),
    ("newest", "show the newest 2 orders by order_date", "SELECT * FROM orders ORDER BY order_date DESC LIMIT 2", "ordered"),
    ("cheapest", "write a query for the 2 cheapest products", "SELECT * FROM products ORDER BY price ASC LIMIT 2", "ordered"),
    # ---- distinct and functions
    ("distinct", "list all unique city values from customers", "SELECT DISTINCT city FROM customers", "bag"),
    ("distinct_upper", "write a query to list unique country from suppliers in uppercase",
     "SELECT DISTINCT UPPER(country) FROM suppliers", "bag"),
    # ---- totals per group
    ("group_count", "count of orders per status", "SELECT status, COUNT(*) FROM orders GROUP BY status", "bag2"),
    ("group_sum", "show the total amount of orders for each status", "SELECT status, SUM(amount) FROM orders GROUP BY status", "bag2"),
    ("group_avg_having", "average price per category_id in products, only groups with more than 1 rows",
     "SELECT category_id, AVG(price) FROM products GROUP BY category_id HAVING COUNT(*) > 1", "bag2"),
    ("duplicates", "find duplicate email values in customers with how many times each appears",
     "SELECT email, COUNT(*) FROM customers GROUP BY email HAVING COUNT(*) > 1", "bag2"),
    ("count_rows", "how many orders have status = 'paid'", "SELECT COUNT(*) FROM orders WHERE status = 'paid'", "bag"),
    # ---- across tables
    ("anti_join", "list customers that have no orders",
     "SELECT * FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)", "bag"),
    ("anti_join_adj", "write a query to find suppliers without any products",
     "SELECT * FROM suppliers s WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.supplier_id = s.id)", "bag"),
    ("child_count", "each name of categories and its product count, including those with none",
     "SELECT c.name, COUNT(p.id) FROM categories c LEFT JOIN products p ON p.category_id = c.id GROUP BY c.id, c.name", "bag"),
    ("parent_col", "list orders with their customer city",
     "SELECT o.*, c.city FROM orders o JOIN customers c ON o.customer_id = c.id", "col:city"),
    ("above_avg", "find products whose price is above the average",
     "SELECT * FROM products WHERE price > (SELECT AVG(price) FROM products)", "bag"),
    # ---- Hinglish and Hindi
    ("hg_filter", "products dikhao jinka price 1000 se zyada hai", "SELECT * FROM products WHERE price > 1000", "bag"),
    ("hg_list", "orders dikhao jinka status paid ya shipped hai", "SELECT * FROM orders WHERE status IN ('paid', 'shipped')", "bag"),
    ("hg_group", "har status ke kitne orders hain", "SELECT status, COUNT(*) FROM orders GROUP BY status", "bag2"),
    ("hg_null", "customers dikhao jinka phone khali hai", "SELECT * FROM customers WHERE phone IS NULL", "bag"),
    ("hg_sort", "products ke top 2 dikhao, price ke hisaab se sort karo highest first",
     "SELECT * FROM products ORDER BY price DESC LIMIT 2", "ordered"),
    ("hi_filter", "products दिखाओ जिनका stock 10 से ज़्यादा है", "SELECT * FROM products WHERE stock > 10", "bag"),
    ("hi_anti", "ऐसे customers दिखाओ जिनके कोई orders नहीं है",
     "SELECT * FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)", "bag"),
    ("hi_avg", "orders दिखाओ जिनका amount average से ज़्यादा है",
     "SELECT * FROM orders WHERE amount > (SELECT AVG(amount) FROM orders)", "bag"),
]


@pytest.fixture(scope="module")
def engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


def _rows(eng, sql):
    cur = eng.conn.cursor()
    try:
        cur.execute(sql)
        names = [d[0].lower() for d in cur.description]
        return names, [tuple(S.norm(v) for v in r) for r in cur.fetchall()]
    finally:
        cur.close()


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)
    langs = Counter("hindi" if re.search(r"[ऀ-ॿ]", p[1]) else "hinglish" if p[0].startswith("hg_") else "en" for p in P)
    assert langs["en"] >= 20 and langs["hinglish"] >= 4 and langs["hindi"] >= 3


@pytest.mark.parametrize("pid,request_,truth,cmp", P, ids=[p[0] for p in P])
def test_tonights_problems(engines, pid, request_, truth, cmp):
    res = sqlwrite.build(request_, SCHEMA)
    assert res["ok"], (pid, res.get("cant"))
    assert not res["guesses"], (pid, res["guesses"])            # the schema was given: nothing to guess
    sql = res["sql"]
    for name, eng in engines.items():
        (gn, got), (wn, want) = _rows(eng, sql), _rows(eng, truth)
        if cmp == "ordered":
            assert got == want, (pid, name, sql)
        elif cmp == "ordered_key":
            k = gn.index("amount")
            assert [r[k] for r in got] == [r[wn.index("amount")] for r in want], (pid, name, sql)
        elif cmp == "bag2":
            assert Counter(r[:2] for r in got) == Counter(r[:2] for r in want), (pid, name, sql)
        elif cmp.startswith("col:"):
            c = cmp[4:]
            assert Counter(r[gn.index(c)] for r in got) == Counter(r[wn.index(c)] for r in want), (pid, name, sql)
        else:
            assert Counter(got) == Counter(want), (pid, name, sql)


def test_names_it_cannot_see_are_marked_not_invented():
    res = sqlwrite.build("write a query to list the 10 most expensive products")
    assert res["ok"] and "-- check: products.price" in res["sql"] and res["guesses"] == ["products.price"]
    res = sqlwrite.build("write a query for customers who joined after 2024-01-01")
    assert res["ok"] and "created_at > '2024-01-01'" in res["sql"] and "-- check" in res["sql"]


def test_engine_words_decide_the_cut():
    req = "show top 3 products sorted by price highest first"
    assert "LIMIT 3" in sqlwrite.build(req, SCHEMA, "postgresql")["sql"]
    assert "TOP (3)" in sqlwrite.build(req, SCHEMA, "sqlserver")["sql"]
    assert "FETCH FIRST 3 ROWS ONLY" in sqlwrite.build(req, SCHEMA, "oracle")["sql"]


def test_an_unclear_request_is_said_honestly():
    res = sqlwrite.build("write a query for the thing we talked about")
    assert not res["ok"] and res["cant"]


@pytest.mark.parametrize("text", [
    "write a query to list the 10 most expensive products", "query likho jo orders dikhaye jinka total 500 se zyada hai",
    "एक क्वेरी लिखो जो orders दिखाए जिनका status pending है", "customers ki list kaise likhu sql mein",
    "give me a sql query for customers who joined after 2024-01-01",
])
def test_requests_are_recognised(text):
    assert sqlwrite.is_request(text)


@pytest.mark.parametrize("text", [
    "what is a join", "index kya hota hai", "SELECT * FROM t WHERE a = 1", "ERROR 1054 (42S22): Unknown column 'x' in 'field list'",
    "my database is slow", "why is my query slow", "difference between delete and truncate", "how to find duplicate emails",
    "show me the error log", "list the steps to backup mysql",
])
def test_other_work_is_not_taken(text):
    assert not sqlwrite.is_request(text)


CHAT = [
    ("write a query to list first_name and city of customers where city is Pune", "WHERE city = 'Pune'", "Here's the query:"),
    ("orders dikhao jinka status paid ya shipped hai", "status IN ('paid', 'shipped')", "Ye rahi query:"),
    ("एक क्वेरी लिखो जो orders दिखाए जिनका status pending है", "status = 'pending'", "query"),
    ("write a postgres query to show the newest 2 orders by order_date", "LIMIT 2", "Runs on PostgreSQL"),
]


@pytest.mark.parametrize("text,has,head", CHAT, ids=[f"chat{k}" for k in range(len(CHAT))])
def test_through_the_chat(client, ml_mode, text, has, head):
    from tests.test_ml import say

    r = say(client, text)
    assert has in r["reply"] and head.lower() in r["reply"].lower(), r["reply"]
    assert "Paste the SQL query" not in r["reply"] and "don't have a specific answer" not in r["reply"]
