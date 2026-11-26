"""Night S2 practice bank: the query shapes of app/agents/sqlshapes.py.

The made-up shop and office of tests/practice/schema.py (employees with a manager_id, departments, categories with a
parent_id, products, orders ...), given as CREATE TABLE. Each request in words (English, Hinglish, Hindi) has a query
written here independently; DBaba's must return the same rows on MariaDB and on PostgreSQL. Changes (UPDATE / DELETE)
run inside a transaction that is rolled back, and the table after them is compared.
"""

import re
from collections import Counter

import pytest

from app.agents import sqlwrite
from tests.practice import schema as S
from tests.practice.test_s1_write import SCHEMA, _rows

RANK = "RANK() OVER (PARTITION BY {g} ORDER BY {v} DESC)"

# (id, request, truth, compare[, state])
#   compare: bag / bag2 (first two columns) / fl (first and last column) / col:<c> (that column) / map:<key>:<col>
#   state: for a change, the SELECT that reads the table after it (the truth is that table, worked out beforehand)
P = [
    # ---- a row and its manager / parent row
    ("self_more", "list employees who earn more than their manager",
     "SELECT e.id FROM employees e JOIN employees m ON e.manager_id = m.id WHERE e.salary > m.salary", "col:id"),
    ("self_two", "employees whose salary is more than their manager's and who were hired before their manager",
     "SELECT e.id FROM employees e JOIN employees m ON e.manager_id = m.id WHERE e.salary > m.salary AND e.hired_on < m.hired_on",
     "col:id"),
    ("self_same", "list employees in the same department as their manager",
     "SELECT e.id FROM employees e JOIN employees m ON e.manager_id = m.id WHERE e.department_id = m.department_id", "col:id"),
    ("self_list", "show first_name of each employee and their manager's first_name, including those without a manager",
     "SELECT e.first_name, m.first_name FROM employees e LEFT JOIN employees m ON e.manager_id = m.id", "bag2"),
    ("self_none", "list employees without a manager", "SELECT * FROM employees WHERE manager_id IS NULL", "bag"),
    ("self_count", "how many direct reports does each manager have in employees",
     "SELECT m.id, COUNT(*) FROM employees e JOIN employees m ON e.manager_id = m.id GROUP BY m.id", "fl"),
    ("parent_name", "list categories with their parent's name",
     "SELECT c.name, p.name FROM categories c JOIN categories p ON c.parent_id = p.id", "bag2"),
    ("hg_self", "employees dikhao jinki salary unke manager se zyada hai",
     "SELECT e.id FROM employees e JOIN employees m ON e.manager_id = m.id WHERE e.salary > m.salary", "col:id"),
    ("hi_self", "employees दिखाओ जिनकी salary उनके manager से कम है",
     "SELECT e.id FROM employees e JOIN employees m ON e.manager_id = m.id WHERE e.salary < m.salary", "col:id"),
    # ---- pairs
    ("pairs", "find pairs of employees in the same department",
     "SELECT a.first_name, b.first_name FROM employees a JOIN employees b ON a.department_id = b.department_id AND a.id < b.id", "bag2"),
    ("hg_pairs", "customers ke ek jaisi city wale jode dikhao",
     "SELECT a.first_name, b.first_name FROM customers a JOIN customers b ON a.city = b.city AND a.id < b.id", "bag2"),
    # ---- the Nth highest
    ("nth_value", "find the 2nd highest salary in employees",
     "SELECT DISTINCT salary FROM (SELECT salary, DENSE_RANK() OVER (ORDER BY salary DESC) rk FROM employees WHERE salary IS NOT NULL) x "
     "WHERE rk = 2", "bag"),
    ("nth_rows", "show employees who have the 2nd highest salary",
     "SELECT id FROM (SELECT id, DENSE_RANK() OVER (ORDER BY salary DESC) rk FROM employees WHERE salary IS NOT NULL) x WHERE rk = 2",
     "col:id"),
    ("nth_group", "find the 2nd highest salary in each department from employees",
     "SELECT DISTINCT department_id, salary FROM (SELECT department_id, salary, DENSE_RANK() OVER (PARTITION BY department_id ORDER BY "
     "salary DESC) rk FROM employees WHERE salary IS NOT NULL AND department_id IS NOT NULL) x WHERE rk = 2", "bag"),
    ("nth_product", "the 3rd most expensive product",
     "SELECT id FROM (SELECT id, DENSE_RANK() OVER (ORDER BY price DESC) rk FROM products WHERE price IS NOT NULL) x WHERE rk = 3",
     "col:id"),
    ("hi_nth", "employees में दूसरी सबसे ज़्यादा salary दिखाओ",
     "SELECT DISTINCT salary FROM (SELECT salary, DENSE_RANK() OVER (ORDER BY salary DESC) rk FROM employees WHERE salary IS NOT NULL) x "
     "WHERE rk = 2", "bag"),
    # ---- the top N of each group
    ("top_per", "top 2 products by price in each category",
     "SELECT id FROM (SELECT id, " + RANK.format(g="category_id", v="price") + " rk FROM products WHERE price IS NOT NULL AND "
     "category_id IS NOT NULL) x WHERE rk <= 2", "col:id"),
    ("hg_top_per", "har category ke top 1 products dikhao by price",
     "SELECT id FROM (SELECT id, " + RANK.format(g="category_id", v="price") + " rk FROM products WHERE price IS NOT NULL AND "
     "category_id IS NOT NULL) x WHERE rk <= 1", "col:id"),
    # ---- groups against the overall average
    ("group_avg", "departments whose average salary is higher than the overall average",
     "SELECT d.id FROM departments d JOIN employees e ON e.department_id = d.id GROUP BY d.id "
     "HAVING AVG(e.salary) > (SELECT AVG(salary) FROM employees)", "col:id"),
    # ---- percentages
    ("pct_amount", "percentage of total amount per status in orders",
     "SELECT status, ROUND(100.0 * SUM(amount) / (SELECT SUM(amount) FROM orders), 2) FROM orders GROUP BY status", "fl"),
    ("pct_rows", "what percentage of orders are in each status",
     "SELECT status, ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM orders), 2) FROM orders GROUP BY status", "fl"),
    ("pct_cond", "percentage of orders where status is paid",
     "SELECT ROUND(100.0 * SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) / COUNT(*), 2) FROM orders", "bag"),
    ("hi_pct", "हर status का total amount में प्रतिशत दिखाओ from orders",
     "SELECT status, ROUND(100.0 * SUM(amount) / (SELECT SUM(amount) FROM orders), 2) FROM orders GROUP BY status", "fl"),
    # ---- ranks, running totals, the previous row
    ("rank", "rank employees by salary within each department",
     "SELECT id, " + RANK.format(g="department_id", v="salary") + " AS rank_no FROM employees WHERE salary IS NOT NULL AND "
     "department_id IS NOT NULL", "map:id:rank_no"),
    ("dense_rank", "dense rank products by price",
     "SELECT id, DENSE_RANK() OVER (ORDER BY price DESC) AS rank_no FROM products WHERE price IS NOT NULL", "map:id:rank_no"),
    ("rank_nowin", "rank employees by salary without window functions",
     "SELECT id, RANK() OVER (ORDER BY salary DESC) AS rank_no FROM employees WHERE salary IS NOT NULL", "map:id:rank_no"),
    ("hg_rank", "employees ko salary ke hisaab se rank karo",
     "SELECT id, RANK() OVER (ORDER BY salary DESC) AS rank_no FROM employees WHERE salary IS NOT NULL", "map:id:rank_no"),
    ("running", "running total of amount in orders by order_date",
     "SELECT id, SUM(amount) OVER (ORDER BY order_date, id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total FROM orders",
     "map:id:running_total"),
    ("prev_row", "each order with the previous order's amount, ordered by order_date",
     "SELECT id, LAG(amount) OVER (ORDER BY order_date, id) AS previous_amount FROM orders", "map:id:previous_amount"),
    # ---- changes
    ("upd_pct", "increase price of products by 10% where stock is 0",
     "SELECT id, CASE WHEN stock = 0 THEN ROUND(price * 1.1, 2) ELSE price END FROM products", "bag", "SELECT id, price FROM products"),
    ("upd_set", "set status to 'cancelled' for orders where amount is less than 500",
     "SELECT id, CASE WHEN amount < 500 THEN 'cancelled' ELSE status END FROM orders", "bag", "SELECT id, status FROM orders"),
    ("hg_upd", "products ka price 5% ghatao jinka stock 0 hai",
     "SELECT id, CASE WHEN stock = 0 THEN ROUND(price * 0.95, 2) ELSE price END FROM products", "bag", "SELECT id, price FROM products"),
    ("del_where", "delete orders where status is 'cancelled'",
     "SELECT id FROM orders WHERE status IS NULL OR status <> 'cancelled'", "bag", "SELECT id FROM orders"),
    ("hi_del", "orders हटाओ जिनका status 'cancelled' है",
     "SELECT id FROM orders WHERE status IS NULL OR status <> 'cancelled'", "bag", "SELECT id FROM orders"),
    ("del_dups", "delete duplicate customers on email keeping the lowest id",
     "SELECT id FROM customers c WHERE email IS NULL OR id = (SELECT MIN(id) FROM customers d WHERE d.email = c.email)", "bag",
     "SELECT id FROM customers"),
]


@pytest.fixture(scope="module")
def engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)
    langs = Counter("hindi" if re.search(r"[ऀ-ॿ]", p[1]) else "hinglish" if p[0].startswith("hg_") else "en" for p in P)
    assert langs["en"] >= 20 and langs["hinglish"] >= 4 and langs["hindi"] >= 4


def _same(cmp, got, want) -> bool:
    (gn, gr), (wn, wr) = got, want
    if cmp == "bag2":
        return Counter(r[:2] for r in gr) == Counter(r[:2] for r in wr)
    if cmp == "fl":
        return Counter((r[0], r[-1]) for r in gr) == Counter((r[0], r[-1]) for r in wr)
    if cmp.startswith("col:"):
        c = cmp[4:]
        return Counter(r[gn.index(c)] for r in gr) == Counter(r[wn.index(c)] for r in wr)
    if cmp.startswith("map:"):
        _m, k, c = cmp.split(":")
        return Counter((r[gn.index(k)], r[gn.index(c)]) for r in gr) == Counter((r[wn.index(k)], r[wn.index(c)]) for r in wr)
    return Counter(gr) == Counter(wr)


@pytest.mark.parametrize("prob", P, ids=[p[0] for p in P])
def test_tonights_problems(engines, prob):
    pid, request_, truth, cmp = prob[:4]
    state = prob[4] if len(prob) > 4 else None
    res = sqlwrite.build(request_, SCHEMA)
    assert res["ok"], (pid, res.get("cant"))
    assert not res["guesses"], (pid, res["guesses"])
    sql = res["sql"]
    for name, eng in engines.items():
        want = _rows(eng, truth)
        if state:
            cur = eng.conn.cursor()
            cur.execute("BEGIN")
            try:
                cur.execute(sql)
                got = _rows(eng, state)
            finally:
                cur.execute("ROLLBACK")
                cur.close()
        else:
            got = _rows(eng, sql)
        assert _same(cmp, got, want), (pid, name, sql, got[1][:5], want[1][:5])


def test_changes_never_touch_every_row_unasked():
    for req in ("delete orders", "update the salary of employees", "remove customers"):
        res = sqlwrite.build(req, SCHEMA)
        assert not res["ok"] and res["cant"], (req, res.get("sql"))


def test_changes_say_how_to_preview_and_undo():
    res = sqlwrite.build("delete orders where status is 'cancelled'", SCHEMA)
    assert res["ok"] and any("ROLLBACK" in s and "SELECT" in s for s in res["steps"])


def test_names_it_cannot_see_are_marked():
    res = sqlwrite.build("list employees who earn more than their manager")
    assert res["ok"] and "JOIN employees m ON e.manager_id = m." in res["sql"] and "-- check" in res["sql"]


def test_engine_words_decide_ranks_without_windows():
    sql = sqlwrite.build("rank employees by salary without window functions", SCHEMA)["sql"]
    assert "OVER" not in sql and "COUNT(*) + 1" in sql


@pytest.mark.parametrize("text", [
    "list employees who earn more than their manager", "find pairs of employees in the same department",
    "find the 3rd highest salary in each department", "top 2 products by price in each category",
    "percentage of total amount per status in orders", "rank employees by salary within each department",
    "running total of amount in orders by order_date", "increase price of products by 10% where stock is 0",
    "delete orders where status is 'cancelled'", "employees dikhao jinki salary unke manager se zyada hai",
    "employees में दूसरी सबसे ज़्यादा salary दिखाओ",
])
def test_requests_are_recognised(text):
    assert sqlwrite.is_request(text)


@pytest.mark.parametrize("text", [
    "what is a self join", "row_number aur rank mein kya farak hai", "how to get the previous row's value",
    "difference between delete and truncate", "delete in batches in mysql", "how to find the nth highest salary",
])
def test_lessons_stay_lessons(text):
    assert not sqlwrite.is_request(text)


CHAT = [
    ("list employees who earn more than their manager", "JOIN employees m ON e.manager_id = m.", "Here's the query:"),
    ("employees ko salary ke hisaab se rank karo", "RANK() OVER", "Ye rahi query:"),
    ("delete orders where status is 'cancelled'", "DELETE FROM orders", "ROLLBACK"),
]


@pytest.mark.parametrize("text,has,also", CHAT, ids=[f"chat{k}" for k in range(len(CHAT))])
def test_through_the_chat(client, ml_mode, text, has, also):
    from tests.test_ml import say

    r = say(client, text)
    assert has in r["reply"] and also.lower() in r["reply"].lower(), r["reply"]
    assert "Paste the SQL query" not in r["reply"] and "don't have a specific answer" not in r["reply"]


# ---- X4: small puzzles about what SQL returns, worked out by running them (app/agents/sqlpuzzle.py)
PUZZLES = [
    ("A column score holds (10, NULL, 20, NULL). What do COUNT(*), COUNT(score), AVG(score) and SUM(score) return?",
     {"COUNT(*)": "4", "COUNT(score)": "2", "AVG(score)": "15", "SUM(score)": "30"}),
    ("column qty has values (3, 3, NULL, 4): what is COUNT(DISTINCT qty) and MAX(qty)?", {"COUNT(DISTINCT qty)": "2", "MAX(qty)": "4"}),
    ("table x has values (2, 2, NULL) and table y has values (2, 2, 2, NULL, 5). how many rows do inner join, left join, right join "
     "and full outer join return?",
     {"x INNER JOIN y": "6", "x LEFT JOIN y": "7", "x RIGHT JOIN y": "8", "x FULL OUTER JOIN y": "9"}),
    ("kitne rows aayenge: table p values (1, NULL) aur table q values (1, 1) ke inner join aur left join mein?",
     {"p INNER JOIN q": "2", "p LEFT JOIN q": "3"}),
]


@pytest.mark.parametrize("text,want", PUZZLES, ids=[f"puzzle{k}" for k in range(len(PUZZLES))])
def test_puzzles_are_worked_out(text, want):
    from app.agents import sqlpuzzle

    assert sqlpuzzle.is_puzzle(text) and not sqlwrite.is_request(text)
    got = {a.split(" ON ")[0]: b for a, b in sqlpuzzle.solve(text)["lines"]}
    assert got == want


def test_full_outer_join_puzzles_say_which_engines_lack_it():
    from app.agents import sqlpuzzle

    out = sqlpuzzle.reply(PUZZLES[2][0])
    assert "MySQL and MariaDB don't have FULL OUTER JOIN" in out and "NULL never equals" in out


@pytest.mark.parametrize("text", ["SELECT COUNT(*) FROM t WHERE a IN (1, 2, 3)", "insert into t values (1, 2)",
                                  "list orders where status is paid"])
def test_queries_and_requests_are_not_puzzles(text):
    from app.agents import sqlpuzzle

    assert not sqlpuzzle.is_puzzle(text)


def test_a_puzzle_through_the_chat(client, ml_mode):
    from tests.test_ml import say

    r = say(client, PUZZLES[0][0])
    assert "COUNT(score) = 2" in r["reply"] and "AVG(score) = 15" in r["reply"], r["reply"]
