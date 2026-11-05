"""Is the query right, before is it fast: a query with many kinds of mistakes, random broken queries (tests/fuzz/sqlbugs.py)
and valid ones (tests/fuzz/db.py) that must get no false 'error' or 'wrong'."""

import random
from pathlib import Path

import pytest

from app.agents import sqlcorrect
from app.agents.sql import analyze_sql
from tests.fuzz import db as dbfuzz
from tests.fuzz import sqlbugs

BROKEN = (Path(__file__).parent / "data" / "broken_joins.sql").read_text()


def test_every_kind_of_mistake_in_one_query_is_found():
    r = sqlcorrect.check(BROKEN)
    titles = " | ".join(f["title_en"] for f in r["findings"])
    for must in ["'products.id'", "'city.id'", "FULL OUTER JOIN", "ORDER BY uses customer", "aren't grouped", "Self join",
                 "departments is joined twice", "cities is joined twice", "'!= NULL'", "COUNT(o.id)", "HAVING", "RIGHT JOIN",
                 "o.customer is probably o.customer_id", "cat.category_id: is the key of categories called id?", "oi.price and oi.unit_price",
                 "quantity has no table"]:
        assert must in titles, must
    fixed = r["corrected"]
    assert "FULL OUTER" not in fixed and "dep2" not in fixed and "ci2" not in fixed and "GROUP BY" not in fixed
    assert "m.id = e.manager_id" in fixed and "IS NOT NULL" in fixed and "-- check: customer_id" in fixed
    assert fixed.rstrip().endswith("(you wrote customer)") and "customer_name ASC;" in fixed
    assert [f for f in sqlcorrect.check(fixed)["findings"] if f["kind"] in ("error", "wrong")] == []


def test_a_wrong_query_gets_no_index_advice_until_its_right():
    res = analyze_sql(BROKEN)
    assert res["indexes"] == [] and res["check_query"] is None and res["speed_later"]["indexes"]
    assert res["rewritten"] == res["correct"]["corrected"]


@pytest.mark.parametrize("seed", range(1, 60))
def test_broken_queries_are_found_and_corrected(seed):
    item = sqlbugs.make(seed)
    if item is None:
        pytest.skip("no parent/child tables in this schema")
    assert sqlbugs.check(item, None) == []


def test_valid_queries_get_no_false_mistakes():
    for seed in range(1, 120):
        schema, rnd = dbfuzz.make_schema(seed), random.Random(seed)
        for _ in range(3):
            sql, _q = dbfuzz.make_query(schema, rnd)
            bad = [f["title_en"] for f in sqlcorrect.check(sql)["findings"] if f["kind"] in ("error", "wrong")]
            assert bad == [], (sql, bad)


def test_the_conversation(client, ml_mode):
    from tests.test_ml import say, say_sql

    r = say(client, "I have query make joins correct in this")
    assert [a["recipe"] for a in r["agents"]] == ["sql_review"]
    goal = r["goal"]["id"]
    r = say_sql(client, BROKEN, goal_id=goal)
    assert "in the card" in r["reply"] and "ALTER TABLE" not in r["reply"]
    assert len(r["reply"].splitlines()) <= 5 and "Fixed" in r["reply"]
    assert r["options"] == ["Optimize it"] and r["next"] == []                                  # no quiz buttons
    r = client.post("/api/message", json={"text": "In this query join galat tha join sahi krna tha", "goal_id": goal}).json()
    assert "'products.id'" in r["reply"] and "Self join" in r["reply"] and "'!= NULL'" not in r["reply"]
    r = client.post("/api/message", json={"text": "big query I need small query", "goal_id": goal}).json()
    assert "lines down to" in r["reply"] and "dep2" not in r["reply"].split("Removed:")[0]
    r = client.post("/api/message", json={"text": "query slow hai", "goal_id": goal}).json()
    assert "stop it running" not in r["reply"] and "ALTER TABLE" in r["reply"]      # speed advice, for the corrected query


def test_second_broken_query_school():
    r = sqlcorrect.check((Path(__file__).parent / "data" / "broken_school.sql").read_text())
    titles = " | ".join(f["title_en"] for f in r["findings"])
    for must in ["'subjects.id'", "WHERE uses percentage", "ORDER BY uses student", "aren't grouped", "INNER JOIN sub",
                 "Self join on teachers", "fees is joined twice", "'= NULL'", "SUM(f.amount) is multiplied by the marks rows",
                 "HAVING marks", "s.class is probably s.class_id", "m.max_mark is probably m.max_marks", "exam_year has no table"]:
        assert must in titles, must
    assert "m.marks and m.max_marks: two columns, or one?" in titles          # asked, never merged
    assert "m.marks," in r["corrected"] and "AND f.status IS NULL" in r["corrected"] and "LEFT JOIN fees f" in r["corrected"]
    assert {x["title_en"] for x in r["left"]} >= {"SUM(f.amount) is multiplied by the marks rows"}


def test_a_bare_column_goes_to_the_table_the_rows_belong_to():
    q = ("SELECT u.id, u.name, o.id AS order_id, o.total_amount FROM users u LEFT JOIN orders o ON u.id = o.customer_id "
         "WHERE o.status = 'paid' AND u.email != NULL ORDER BY created_at DESC;")
    fixed = sqlcorrect.check(q)["corrected"]
    assert "INNER JOIN orders o" in fixed and "u.email IS NOT NULL" in fixed and "ORDER BY o.created_at DESC;" in fixed


def test_numbers_in_quotes_in_range_comparisons():
    q = ("SELECT c.id, c.name, p.amount, p.payment_date FROM customers c INNER JOIN payments p ON c.id = p.customer "
         "WHERE p.status = 'success' AND p.amount > '1000' AND c.phone = NULL ORDER BY payment_date ASC;")
    fixed = sqlcorrect.check(q)["corrected"]
    for must in ["p.customer_id", "p.amount > 1000", "c.phone IS NULL", "ORDER BY p.payment_date ASC", "p.status = 'success'"]:
        assert must in fixed, must
    kept = sqlcorrect.check("SELECT t.a FROM t WHERE t.code = '0042' AND t.d >= '2026-01-01'")
    assert kept["corrected"] is None                              # = keeps its quotes; a date string isn't a number


def test_the_recognisers_are_ready_before_the_first_message():
    from app.dbknow import brain as dbb
    from app.officeknow import brain as ofb

    assert dbb._train() is dbb._train() and ofb._train() is ofb._train()


def test_in_subquery_is_not_a_problem_and_nothing_is_promised(client, ml_mode):
    """Seen on the live site: an IN (SELECT ...) query got '1 problem found' and 'the rewritten query and index commands are in
    the card' while the card had neither. IN (SELECT ...) runs as a semi-join on MySQL 5.6+, MariaDB and PostgreSQL (checked on
    the test databases), and the JOIN rewrite people suggest returns duplicate rows."""
    from tests.test_ml import say, say_sql

    q = "SELECT name, email FROM users WHERE id IN (SELECT user_id FROM orders WHERE amount > 100)"
    res = analyze_sql(q)
    assert not res["findings"] and "semi-join" in res["notes"][0]["en"] and "Don't rewrite it as a plain JOIN" in res["notes"][0]["en"]
    r = say(client, "I have query correct this")
    r = say_sql(client, q, goal_id=r["goal"]["id"])
    assert "no problems found" in r["reply"] and "ready to copy" not in r["reply"]
    d = analyze_sql("DELETE FROM users WHERE id IN (SELECT user_id FROM bans)")       # older MySQL/MariaDB: no semi-join for these
    assert not d["findings"] and "8.0.21" in d["notes"][0]["en"] and "JOIN" in d["notes"][0]["en"]


def test_the_join_rewrite_really_duplicates_rows():
    import socket

    try:
        socket.create_connection(("127.0.0.1", 5432), timeout=0.5).close()
    except OSError:
        pytest.skip("needs the local PostgreSQL test database")
    from tests.dbreal import real_errors as RE

    pg = RE.postgres()
    with pg.cursor() as c:
        c.execute("CREATE TEMP TABLE semi_p(id int primary key); CREATE TEMP TABLE semi_c(pid int);"
                  "INSERT INTO semi_p VALUES (1),(2); INSERT INTO semi_c VALUES (1),(1),(1);")
        c.execute("SELECT (SELECT count(*) FROM semi_p WHERE id IN (SELECT pid FROM semi_c)),"
                  " (SELECT count(*) FROM semi_p JOIN semi_c ON semi_c.pid = semi_p.id)")
        assert c.fetchone() == (1, 3)


@pytest.mark.parametrize("before", ["-- my query\n", "-- report\n-- by month\n\n", "/* list */\n", "# note\n", "(\n"])
def test_a_select_after_a_comment_is_still_a_select(before):
    """Seen on the live site: a query starting with a comment line was read as UPDATE/DELETE ('IN (SELECT ...) inside
    UPDATE/DELETE'), because the removed comment left a newline in front of SELECT."""
    q = before + "SELECT name FROM users WHERE id IN (SELECT user_id FROM orders)" + (")" if before.startswith("(") else "")
    res = analyze_sql(q)
    assert not [f for f in res["findings"] if "UPDATE/DELETE" in f["title_en"]], res["findings"]
    assert any("semi-join" in n["en"] for n in res["notes"])
    plain = analyze_sql(before.replace("(", "") + "SELECT id FROM users")
    assert any("No WHERE and no LIMIT" in f["title_en"] for f in plain["findings"])     # read-query rules apply too


QUOTES_UPDATE = ("UPDATE employees SET salary = salary * 1.1\nWHERE id IN (SELECT emp_id FROM reviews)\n"
                 "  AND hire_date < 2024-01-01\n  AND status = active")


@pytest.mark.parametrize("q", [QUOTES_UPDATE, "SELECT name, salary FROM employees WHERE hire_date < 2024-01-01 AND status = active",
                               "SELECT e.name FROM employees e JOIN depts d ON d.id = e.dept_id\nWHERE e.hire_date < 2024-01-01 AND e.status = active"])
def test_values_without_quotes_are_found_and_quoted(q):
    """Seen on the live site: hire_date < 2024-01-01 and status = active went unnoticed."""
    c = analyze_sql(q)["correct"]
    kinds = {f["evidence"].split()[-1].strip("'"): f["kind"] for f in c["findings"] if f["topic"] == "value"}
    assert kinds.get("2024-01-01") == "wrong" and kinds.get("active") == "error", c["findings"]
    assert "< '2024-01-01'" in c["corrected"] and "= 'active'" in c["corrected"]
    assert not [f for f in c["findings"] if f["topic"] == "column" and "active" in f["title_en"]]   # not also "which table is active from"
    assert "2022" in next(f for f in c["findings"] if f["topic"] == "value" and "2024" in f["title_en"])["title_en"]


@pytest.mark.parametrize("q", ["SELECT id FROM users WHERE status = 'active' AND created > '2024-01-01'",
                               "SELECT a.id FROM a JOIN b ON a.type = b.type", "UPDATE users SET mode = DEFAULT WHERE id = 7",
                               "SELECT id FROM orders WHERE status = old_status", "SELECT id FROM t WHERE a = b AND b > 3",
                               "SELECT id FROM users WHERE owner = current_user", "SELECT id FROM t WHERE created > NOW() - INTERVAL 7 DAY"])
def test_quoted_values_and_real_columns_are_left_alone(q):
    c = analyze_sql(q).get("correct") or {}
    assert not [f for f in c.get("findings", []) if f["topic"] == "value" and f["kind"] in ("error", "wrong")], c.get("findings")


def test_unquoted_values_in_the_chat(client, ml_mode):
    from tests.test_ml import say, say_sql

    r = say(client, "i have another query issue can you solve it?")
    r = say_sql(client, QUOTES_UPDATE, goal_id=r["goal"]["id"])
    assert "needs quotes" in r["reply"] and "problem found" not in r["reply"]


def test_what_the_databases_really_do_with_unquoted_values():
    """The lab proof behind the findings: MySQL/MariaDB read 2024-01-01 as 2022 (wrong rows, no error) and 'active' as a column."""
    import socket

    for port in (3306, 5432):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        except OSError:
            pytest.skip("needs the local MariaDB and PostgreSQL test databases")
    from tests.dbreal import real_errors as RE

    my = RE.mariadb()
    with my.cursor() as c:
        c.execute("CREATE TEMPORARY TABLE q_emp (id INT, hire_date DATE, status VARCHAR(10))")
        c.execute("INSERT INTO q_emp VALUES (1, '2023-05-01', 'active'), (2, '2025-02-01', 'active')")
        c.execute("SELECT 2024-01-01")
        assert c.fetchone()[0] == 2022
        c.execute("SELECT count(*) FROM q_emp WHERE hire_date < 2024-01-01")
        assert c.fetchone()[0] == 0                                      # silently wrong
        c.execute(analyze_sql("SELECT count(*) FROM q_emp WHERE hire_date < 2024-01-01 AND status = active")["correct"]["corrected"])
        assert c.fetchone()[0] == 1                                      # the corrected query is right
    assert "1054" in RE.my_error(my, "SELECT count(*) FROM q_emp WHERE status = active")
    pg = RE.postgres()
    pg.execute("CREATE TEMP TABLE q_emp (id int, hire_date date, status text)")
    assert "operator does not exist: date < integer" in RE.pg_error(pg, "SELECT count(*) FROM q_emp WHERE hire_date < 2024-01-01")
    assert 'column "active" does not exist' in RE.pg_error(pg, "SELECT count(*) FROM q_emp WHERE status = active")


SCALAR_SUB = ("UPDATE employees\nSET salary = salary * 1.10\nWHERE department_id = (\n    SELECT id\n    FROM departments\n"
              "    WHERE location = 'Delhi'\n);")


@pytest.mark.parametrize("q,topic,kind,fixed", [
    (SCALAR_SUB, "subquery", "error", "department_id IN ("),                              # seen on the live site
    ("UPDATE employees SET manager_id = NULL WHERE manager_id = NULL", "null", "wrong", "SET manager_id = NULL WHERE manager_id IS NULL"),
    ("UPDATE employees SET bonus = 50 AND salary = 999 WHERE id = 1", "set", "wrong", "SET bonus = 50, salary = 999"),
    ("SELECT name FROM employees WHERE status = 'active' OR 'pending'", "or", "wrong", "status IN ('active', 'pending')"),
    ("DELETE FROM orders WHERE amount BETWEEN 1000 AND 10", "between", "wrong", "BETWEEN 10 AND 1000"),
    ("SELECT dept, COUNT(*) FROM employees WHERE SUM(salary) > 10 GROUP BY dept", "where", "error", None),
    ("DELETE FROM employees WHERE salary < (SELECT AVG(salary) FROM employees)", "same_table", "check", None),
    ("SELECT id FROM doctors WHERE speciality = Child", "value", "error", "speciality = 'Child'"),
    ("SELECT id FROM staff WHERE shift = day", "value", "error", "shift = 'day'"),
])
def test_statement_mistakes_in_any_query(q, topic, kind, fixed):
    c = analyze_sql(q)["correct"]
    hit = [f for f in c["findings"] if f["topic"] == topic]
    assert hit and hit[0]["kind"] == kind, c["findings"]
    if fixed:
        assert fixed in c["corrected"], c["corrected"]


@pytest.mark.parametrize("q", ["UPDATE employees SET x = 1 WHERE dept_id = (SELECT id FROM departments WHERE id = 5)",
                               "SELECT * FROM t WHERE a = (SELECT MAX(b) FROM u)", "SELECT * FROM t WHERE a = (SELECT b FROM u LIMIT 1)",
                               "UPDATE t SET a = 1, b = 2 WHERE c IN (1, 2)", "UPDATE t SET flag = (a = 1 AND b = 2) WHERE id = 3",
                               "SELECT id FROM t WHERE x BETWEEN 1 AND 10", "SELECT id FROM t WHERE status = 'a' OR kind = 'b'",
                               "SELECT dept FROM e GROUP BY dept HAVING SUM(salary) > 10",
                               "SELECT id FROM e WHERE salary > (SELECT AVG(salary) FROM e)", "UPDATE t SET a = NULL WHERE id = 4"])
def test_correct_statements_are_left_alone(q):
    c = analyze_sql(q).get("correct") or {}
    assert not [f for f in c.get("findings", []) if f["kind"] in ("error", "wrong")], c.get("findings")


@pytest.mark.parametrize("seed", [1, 3001])
def test_statement_mistakes_generator(seed):
    """Random tables, random statement mistakes: found, corrected, and (with the test databases up) the corrected statement does
    on MariaDB and PostgreSQL exactly what the intended one does."""
    from tests.fuzz import stmtbugs

    made, fails, _by, _eng = stmtbugs.run(60, seed)
    assert made >= 50 and not fails, "\n\n".join(fails[:5])


def test_check_another_query_asks_for_it(client, ml_mode):
    """Seen on the live site: the 'Check another query' tap answered 'I've covered this above...'."""
    from tests.test_ml import say, say_sql

    r = say(client, "check the query I have")
    g = r["goal"]["id"]
    say_sql(client, "SELECT name FROM employees WHERE status = 'active' OR 'pending'", goal_id=g)
    r = client.post("/api/message", json={"text": "Check another query", "goal_id": g}).json()
    assert "Paste the next SQL query" in r["reply"] and "covered this above" not in r["reply"]
    r = client.post("/api/message", json={"text": SCALAR_SUB, "goal_id": g}).json()
    assert "more than one row" in r["reply"]


SAME_TABLE = ("UPDATE products\nSET price = price * 0.90\nWHERE category_id IN (\n   SELECT category_id\n   FROM categories\n"
              "   WHERE is_active = 1\n)\nAND stock_quantity = (\n   SELECT MAX(stock_quantity)\n   FROM products\n); correct this")


def test_same_table_subquery_gets_a_version_for_every_database(client, ml_mode):
    """Seen on the live site: the finding was right (MySQL error 1093) but the reply had no why and no corrected query,
    and the request typed after the query ('; correct this') was read as part of it."""
    from tests.test_ml import say, say_sql

    c = analyze_sql(SAME_TABLE)["correct"]
    assert [f["topic"] for f in c["findings"]] == ["same_table"] and "correct this" not in c["findings"][0]["evidence"]
    assert "SELECT v FROM (SELECT MAX(stock_quantity) AS v FROM products) AS products_copy" in c["corrected"]
    assert sqlcorrect.check(c["corrected"])["findings"] == []                  # the rewrite is clean, not a new 'more than one row'
    r = say(client, "check the query I have")
    r = say_sql(client, SAME_TABLE, goal_id=r["goal"]["id"])
    assert "1093" in r["reply"] and "runs on every database" in r["reply"]


def test_a_total_named_like_its_column_is_not_a_select_name_in_where():
    """SUM(total) AS total: WHERE total means the real column (found by the valid-query sweep), while a name that exists only
    in SELECT (price * qty AS total) is still reported."""
    ok = "SELECT customer_id, SUM(total) AS total FROM orders WHERE total BETWEEN 10 AND 50 GROUP BY customer_id"
    assert not [f for f in sqlcorrect.check(ok)["findings"] if f["kind"] in ("error", "wrong")]
    bad = sqlcorrect.check("SELECT price * qty AS total FROM items WHERE total > 100")["findings"]
    assert any("a name made in SELECT" in f["title_en"] for f in bad)


PER_GROUP = ("UPDATE products\nSET price = price * 0.90\nWHERE category_id IN (\n   SELECT category_id\n   FROM categories\n"
             "   WHERE is_active = 1\n)\nAND stock_quantity = (\n   SELECT MAX(stock_quantity)\n   FROM products\n);\n\n"
             "Assume you want to give a 10% discount to all products in active categories that have the highest stock within their "
             "own category. Don't touch the others.\n\nThis query does not correctly do that. Correct it.")


def test_what_the_person_said_they_want_is_checked(client, ml_mode):
    """Seen on the live site: the person said 'the highest stock within their own category'; MAX() over the whole table was
    not reported. Lab (MariaDB and PostgreSQL): the original discounts nothing when the overall maximum sits in an inactive
    category; the rewrite discounts each active category's top-stock products, ties included."""
    from tests.test_ml import say, say_sql

    c = analyze_sql(PER_GROUP)["correct"]
    assert [(f["kind"], f["topic"]) for f in c["findings"]] == [("wrong", "per_group")]
    assert ("(SELECT v FROM (SELECT category_id, MAX(stock_quantity) AS v FROM products GROUP BY category_id) AS products_copy "
            "WHERE products_copy.category_id = products.category_id)") in c["corrected"]
    assert sqlcorrect.check(c["corrected"])["findings"] == []
    hinglish = analyze_sql(PER_GROUP.split(";")[0] + "; har category mein sabse zyada stock wale products")["correct"]
    assert [f["topic"] for f in hinglish["findings"]] == ["per_group"]
    plain = analyze_sql(PER_GROUP.split(";")[0] + ";")["correct"]               # nothing said about groups: no guessing
    assert [f["topic"] for f in plain["findings"]] == ["same_table"]
    r = say(client, "check the query I have")
    r = say_sql(client, PER_GROUP, goal_id=r["goal"]["id"])
    assert "not within each category" in r["reply"] and "Fixed 1 mistake" in r["reply"]


def test_the_per_group_rewrite_on_the_test_databases():
    import socket

    for port in (3306, 5432):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        except OSError:
            pytest.skip("needs the local MariaDB and PostgreSQL test databases")
    from tests.dbreal import real_errors as RE

    q = PER_GROUP.replace("products", "pg_products").replace("categories", "pg_categories")
    fixed = analyze_sql(q)["correct"]["corrected"].split(";")[0]
    setup = ["CREATE TEMPORARY TABLE pg_categories (category_id INT PRIMARY KEY, is_active INT)",
             "CREATE TEMPORARY TABLE pg_products (id INT PRIMARY KEY, category_id INT, price DECIMAL(10,2), stock_quantity INT)",
             "INSERT INTO pg_categories VALUES (1,1),(2,1),(3,0)",
             "INSERT INTO pg_products VALUES (1,1,100,50),(2,1,100,10),(3,2,100,20),(4,2,100,20),(5,2,100,5),(6,3,100,90)"]
    for conn in (RE.mariadb(), RE.postgres()):
        with conn.cursor() as c:
            for st in setup:
                c.execute(st)
            c.execute(q.split(";")[0])
            c.execute("SELECT id FROM pg_products WHERE price < 100 ORDER BY id")
            assert c.fetchall() in ((), [])                               # the original: nothing discounted
            c.execute("UPDATE pg_products SET price = 100")
            c.execute(fixed)
            c.execute("SELECT id FROM pg_products WHERE price < 100 ORDER BY id")
            assert [r[0] for r in c.fetchall()] == [1, 3, 4]               # each active category's top stock, ties included


# Traps from an SQL practice workbook (customers / orders / products), each reproduced on MariaDB and PostgreSQL: the original
# gives the wrong rows, DBaba's rewrite gives the right ones on both.
TRAP_SETUP = ["CREATE TEMPORARY TABLE wc (id INT PRIMARY KEY, name VARCHAR(20))",
              "CREATE TEMPORARY TABLE wo (id INT PRIMARY KEY, customer_id INT, order_date TIMESTAMP)",
              "CREATE TEMPORARY TABLE wp (id INT PRIMARY KEY, category_id INT, price INT)",
              "INSERT INTO wc VALUES (1,'a'),(2,'b'),(3,'c')",
              "INSERT INTO wo VALUES (1,1,'2026-01-31 10:00:00'),(2,1,'2026-01-05 00:00:00'),(3,NULL,'2026-01-10 00:00:00')",
              "INSERT INTO wp VALUES (1,1,100),(2,1,100),(3,1,50),(4,2,70),(5,2,60),(6,2,10)"]
TRAPS = [  # (query + what the person said, topic, kind, wrong rows, right rows)
    ("SELECT c.id, COUNT(*) AS orders FROM wc c LEFT JOIN wo o ON o.customer_id = c.id GROUP BY c.id ORDER BY c.id",
     "count", "check", [(1, 2), (2, 1), (3, 1)], [(1, 2), (2, 0), (3, 0)]),
    ("SELECT id FROM wc WHERE id NOT IN (SELECT customer_id FROM wo) ORDER BY id", "not_in", "check", [], [(2,), (3,)]),
    ("SELECT COUNT(*) FROM wo WHERE order_date BETWEEN '2026-01-01' AND '2026-01-31'", "date_range", "check", [(2,)], [(3,)]),
    ("SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY category_id ORDER BY price DESC) AS rn FROM wp) x "
     "WHERE rn <= 1 ORDER BY id; the most expensive product per category, ties must share rank", "ties", "wrong",
     [(1,), (4,)], [(1,), (2,), (4,)]),
    ("SELECT id, category_id, price FROM wp ORDER BY price DESC LIMIT 2; top 2 products per category", "top_n", "wrong",
     [(1, 1, 100), (2, 1, 100)], [(1, 1, 100, 1), (2, 1, 100, 2), (4, 2, 70, 1), (5, 2, 60, 2)]),
]


@pytest.mark.parametrize("q,topic,kind,wrong_rows,right_rows", TRAPS)
def test_workbook_traps_are_found_and_the_rewrite_gives_the_right_rows(q, topic, kind, wrong_rows, right_rows):
    import socket

    c = analyze_sql(q)["correct"]
    hit = [f for f in c["findings"] if f["topic"] == topic]
    assert hit and hit[0]["kind"] == kind and c["corrected"], c
    for port in (3306, 5432):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        except OSError:
            pytest.skip("needs the local MariaDB and PostgreSQL test databases")
    from tests.dbreal import real_errors as RE

    for conn in (RE.mariadb(), RE.postgres()):
        with conn.cursor() as cur:
            for st in TRAP_SETUP:
                cur.execute(st)
            cur.execute(q.split(";")[0])
            assert sorted(map(tuple, cur.fetchall())) == sorted(wrong_rows), "the trap really gives the wrong rows"
            cur.execute(c["corrected"].rstrip().rstrip(";"))
            assert sorted(tuple(int(v) for v in r) for r in cur.fetchall()) == sorted(right_rows), c["corrected"]


def test_two_child_tables_multiply_a_parent_total():
    drill = ("SELECT DISTINCT c.*, SUM(o.total_amount) AS sales FROM customers c LEFT JOIN orders o ON o.customer_id = c.id "
             "LEFT JOIN order_items oi ON oi.order_id = o.id LEFT JOIN payments p ON p.order_id = o.id "
             "WHERE YEAR(o.order_date) = 2026 AND p.status = 'success' GROUP BY c.id ORDER BY sales DESC")
    titles = [f["title_en"] for f in sqlcorrect.check(drill)["findings"]]
    assert any("SUM(o.total_amount) is multiplied" in t for t in titles), titles
    one_child = "SELECT o.id, SUM(o.total_amount) FROM orders o JOIN order_items oi ON oi.order_id = o.id GROUP BY o.id"
    assert not [f for f in sqlcorrect.check(one_child)["findings"] if "multiplied" in f["title_en"]]


def test_a_wrong_answer_is_kept_to_learn_from_with_values_hidden(client, ml_mode):
    """'Still not right' after a check: the query is kept for the nightly learning, every value hidden, one row per miss."""
    from app import learning
    from app.db import _SessionLocal
    from app.models import LearnMiss
    from tests.test_ml import say, say_sql

    q = "SELECT name FROM customers WHERE email = 'ravi@example.in' AND phone = '9876543210' AND city = 'Delhi' AND id = 1234567"
    r = say(client, "check the query I have")
    g = r["goal"]["id"]
    say_sql(client, q, goal_id=g)
    r = client.post("/api/message", json={"text": "this is wrong, it should only show active customers", "goal_id": g}).json()
    assert "kept this query" in r["reply"] and "what result it should give" in r["reply"]
    client.post("/api/message", json={"text": "still not right", "goal_id": g})
    with _SessionLocal() as db:
        rows = db.query(LearnMiss).all()
        assert len(rows) == 1 and rows[0].count == 2 and rows[0].brain == "database"
        d = rows[0].detail
        assert "ravi" not in d and "9876543210" not in d and "Delhi" not in d and "1234567" not in d
        assert "customers" in d and "city = '?'" in d
        feed = learning.feed(db)
        item = next(x for x in feed if x.get("kind") == "said_wrong")
        assert item["brain"] == "database" and item["detail"] == d and item["asked"] == 2
