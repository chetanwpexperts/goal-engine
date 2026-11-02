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
    from tests.test_ml import say

    r = say(client, "I have query make joins correct in this")
    assert [a["recipe"] for a in r["agents"]] == ["sql_review"]
    goal = r["goal"]["id"]
    r = client.post("/api/message", json={"text": BROKEN, "goal_id": goal}).json()
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
    from tests.test_ml import say

    q = "SELECT name, email FROM users WHERE id IN (SELECT user_id FROM orders WHERE amount > 100)"
    res = analyze_sql(q)
    assert not res["findings"] and "semi-join" in res["notes"][0]["en"] and "Don't rewrite it as a plain JOIN" in res["notes"][0]["en"]
    r = say(client, "I have query correct this")
    r = client.post("/api/message", json={"text": q, "goal_id": r["goal"]["id"]}).json()
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
    from tests.test_ml import say

    r = say(client, "i have another query issue can you solve it?")
    r = client.post("/api/message", json={"text": QUOTES_UPDATE, "goal_id": r["goal"]["id"]}).json()
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
    from tests.test_ml import say

    r = say(client, "check the query I have")
    g = r["goal"]["id"]
    client.post("/api/message", json={"text": "SELECT name FROM employees WHERE status = 'active' OR 'pending'", "goal_id": g})
    r = client.post("/api/message", json={"text": "Check another query", "goal_id": g}).json()
    assert "Paste the next SQL query" in r["reply"] and "covered this above" not in r["reply"]
    r = client.post("/api/message", json={"text": SCALAR_SUB, "goal_id": g}).json()
    assert "more than one row" in r["reply"]
