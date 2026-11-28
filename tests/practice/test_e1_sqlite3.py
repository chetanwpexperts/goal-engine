"""E1 (SQLite), night 3 of 4: every query rule the first fifteen nights proved on MariaDB and PostgreSQL, re-checked on SQLite.

The lab is SQLite itself (Python's sqlite3: 3.53 when this was written). Three parts:
- the dialect file's query_rules for SQLite (app/products/dbaba/dbknow/dialects/sqlite.json, rule B): one entry for every rule MySQL and
  PostgreSQL have, plus SQLite's own (ANY / ALL, a SELECT name in WHERE, the first row of a scalar subquery, x / 0 ...), each
  with its lab lines. test_every_query_rule_lab_line_holds runs every line again and compares with what is stored;
- the practice bank: a requirement, the query a person wrote, and the correct SQLite query. On SQLite (rule A: the database is
  named), DBaba must find the mistake under its topic, and its corrected query (or its 'if that's what you meant' form) must
  return the correct query's rows on the practice tables. LAB is what the person's query really does on SQLite;
- the changes (UPDATE / DELETE / REPLACE): DBaba's rewrite run on a copy, its effect compared with the correct statement's.
Valid SQLite (|| for text, SUM(condition), HAVING / WHERE with a SELECT name, a subquery without a name ...) gets no
error/wrong finding, and no finding speaks of MySQL's or PostgreSQL's error numbers once SQLite is the database.
"""

import json
import os
import re
import sqlite3
import tempfile

import pytest

from app.agents import sqlcorrect, sqlexplain
from app.dbknow import engines as E
from tests.practice import schema as S

ERR = "error"
BUILD = "build"        # refused unless SQLite was built with SQLITE_ENABLE_UPDATE_DELETE_LIMIT (Ubuntu's 3.45 library has it)


def _limit_build() -> bool:
    c = sqlite3.connect(":memory:")
    try:
        c.execute("CREATE TABLE x (a)")
        c.execute("DELETE FROM x WHERE a = 1 LIMIT 1")
        return True
    except sqlite3.Error:
        return False
    finally:
        c.close()


LIMIT_BUILD = _limit_build()
PLANS_AS_LAB = sqlite3.sqlite_version_info >= (3, 53, 0)    # plan lines are compared on the lab's version (3.45 plans EXISTS otherwise)
# (id, requirement, the person's query, topic DBaba must find, the correct SQLite query (None: finding it is enough), keep order?,
#  LAB: what the person's query does on SQLite: 'error', 'same' (the right rows by luck or leniency) or 'differs')
P = [
    ("plus_names", "full name of every customer", "SELECT id, first_name + ' ' + last_name AS full_name FROM customers",
     "concat", "SELECT id, first_name || ' ' || last_name AS full_name FROM customers", False, "differs"),
    ("plus_label", "a label like 'Order #1' for every order", "SELECT id, 'Order #' + id AS label FROM orders",
     "concat", "SELECT id, 'Order #' || id AS label FROM orders", False, "differs"),
    ("count_share", "share of orders that are paid", "SELECT COUNT(CASE WHEN status = 'paid' THEN 1 END) / COUNT(*) AS share FROM orders",
     "division", "SELECT 1.0 * COUNT(CASE WHEN status = 'paid' THEN 1 END) / COUNT(*) AS share FROM orders", False, "differs"),
    ("sum_count_avg", "average stock of a product", "SELECT SUM(stock) / COUNT(*) AS avg_stock FROM products",
     "division", "SELECT 1.0 * SUM(stock) / COUNT(*) AS avg_stock FROM products", False, "differs"),
    ("sum_sum_share", "share of the stock that is phones", "SELECT SUM(CASE WHEN category_id = 2 THEN stock ELSE 0 END) / SUM(stock) AS share FROM products",
     "division", "SELECT 1.0 * SUM(CASE WHEN category_id = 2 THEN stock ELSE 0 END) / SUM(stock) AS share FROM products", False, "differs"),
    ("nulls_smallest", "the 3 orders with the smallest discount", "SELECT id, discount FROM orders ORDER BY discount LIMIT 3",
     "nulls_order", "SELECT id, discount FROM orders ORDER BY discount IS NULL, discount LIMIT 3", True, "differs"),
    ("nulls_desc_ok", "the 2 orders with the biggest shipping charge", "SELECT id, shipping FROM orders ORDER BY shipping DESC LIMIT 2",
     "stable_order", "SELECT id, shipping FROM orders ORDER BY shipping DESC, id LIMIT 2", True, "same"),
    ("dq_value", "orders that are paid", "SELECT id FROM orders WHERE status = \"paid\"", "quotes",
     "SELECT id FROM orders WHERE status = 'paid'", False, "same"),
    ("dq_column_name", "customers whose city is the word 'city'", "SELECT id FROM customers WHERE city = \"city\"", "quotes",
     "SELECT id FROM customers WHERE city = 'city'", False, "differs"),
    ("bare_sum", "total of every customer's orders, with the status", "SELECT customer_id, status, SUM(amount) FROM orders GROUP BY customer_id",
     "group", None, False, "differs"),
    ("total_no_group", "number of orders of each customer", "SELECT customer_id, COUNT(*) FROM orders", "group",
     None, False, "differs"),
    ("count_two", "how many different customer and status pairs", "SELECT COUNT(DISTINCT customer_id, status) FROM orders",
     "count_multi", "SELECT COUNT(*) FROM (SELECT DISTINCT customer_id, status FROM orders WHERE customer_id IS NOT NULL AND status IS NOT NULL)",
     False, ERR),
    ("win_ungrouped", "each status's total and the grand total", "SELECT status, SUM(amount) AS s, SUM(amount) OVER () AS grand FROM orders GROUP BY status",
     "window_agg", "SELECT status, SUM(amount) AS s, SUM(SUM(amount)) OVER () AS grand FROM orders GROUP BY status", False, "differs"),
    ("eq_any", "customers who placed an order", "SELECT id FROM customers WHERE id = ANY (SELECT customer_id FROM orders)",
     "dialect", "SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders)", False, ERR),
    ("ne_all", "customers who never ordered", "SELECT id FROM customers WHERE id <> ALL (SELECT customer_id FROM orders)",
     "dialect", "SELECT id FROM customers WHERE id NOT IN (SELECT customer_id FROM orders)", False, ERR),
    ("gt_all", "products dearer than every phone", "SELECT id FROM products WHERE price > ALL (SELECT price FROM products WHERE category_id = 2)",
     "dialect", "SELECT id FROM products WHERE price > (SELECT MAX(price) FROM products WHERE category_id = 2)", False, ERR),
    ("lt_any", "products cheaper than at least one laptop", "SELECT id FROM products WHERE price < ANY (SELECT price FROM products WHERE category_id = 3)",
     "dialect", None, False, ERR),
    ("minus", "customers who never ordered (Oracle words)", "SELECT id FROM customers MINUS SELECT customer_id FROM orders",
     "dialect", "SELECT id FROM customers EXCEPT SELECT customer_id FROM orders", False, ERR),
    ("offset_alone", "every order after the first five", "SELECT id FROM orders ORDER BY id OFFSET 5",
     "dialect", "SELECT id FROM orders ORDER BY id LIMIT -1 OFFSET 5", True, ERR),
    ("union_inner_order", "customer ids and ordering customer ids together", "SELECT id FROM customers ORDER BY id UNION SELECT customer_id FROM orders",
     "dialect", None, False, ERR),
    ("lateral", "each customer's latest order (PostgreSQL words)",
     "SELECT c.id, o.id FROM customers c, LATERAL (SELECT id FROM orders WHERE customer_id = c.id ORDER BY order_date DESC LIMIT 1) AS o",
     "dialect", None, False, ERR),
    ("distinct_on", "each customer's first order (PostgreSQL words)", "SELECT DISTINCT ON (customer_id) customer_id, id FROM orders ORDER BY customer_id, order_date",
     "dialect", None, False, ERR),
    ("first_row_only", "orders of the customers from Mumbai", "SELECT id FROM orders WHERE customer_id = (SELECT id FROM customers WHERE city = 'Mumbai')",
     "subquery", "SELECT id FROM orders WHERE customer_id IN (SELECT id FROM customers WHERE city = 'Mumbai')", False, "differs"),
    ("join_no_on", "each order with its customer's first name", "SELECT o.id, c.first_name FROM orders o JOIN customers c",
     "join", "SELECT o.id, c.first_name FROM orders o JOIN customers c ON c.id = o.customer_id", False, "differs"),
    ("lag_no_order", "each order with the amount of the order before it", "SELECT id, LAG(amount) OVER () AS prev FROM orders",
     "win_no_order", None, False, "differs"),
    ("rank_no_order", "rank the products", "SELECT id, RANK() OVER () AS r FROM products", "win_no_order", None, False, "differs"),
    ("case_mixed", "each order's discount, or the word none", "SELECT id, CASE WHEN discount IS NULL THEN 'none' ELSE discount END AS d FROM orders",
     "case_types", None, False, "same"),
    ("like_no_wildcard", "customers whose city contains 'un'", "SELECT id FROM customers WHERE city LIKE 'un'", "like",
     "SELECT id FROM customers WHERE city LIKE '%un%'", False, "differs"),
    ("eq_null", "orders with no discount", "SELECT id FROM orders WHERE discount = NULL", "null",
     "SELECT id FROM orders WHERE discount IS NULL", False, "differs"),
    ("between_reversed", "orders between 100 and 1000", "SELECT id FROM orders WHERE amount BETWEEN 1000 AND 100", "between",
     "SELECT id FROM orders WHERE amount BETWEEN 100 AND 1000", False, "differs"),
    ("agg_in_where", "customers whose orders add up to more than 20000",
     "SELECT customer_id FROM orders WHERE SUM(amount) > 20000 GROUP BY customer_id", "where",
     "SELECT customer_id FROM orders GROUP BY customer_id HAVING SUM(amount) > 20000", False, ERR),
    ("nested_total", "the most orders any customer has", "SELECT MAX(COUNT(*)) FROM orders GROUP BY customer_id", "nested_agg",
     "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM orders GROUP BY customer_id)", False, ERR),
    ("window_in_where", "the first order by date", "SELECT id FROM orders WHERE ROW_NUMBER() OVER (ORDER BY order_date) = 1", "win_where",
     "SELECT id FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY order_date) AS rn FROM orders) WHERE rn = 1", False, ERR),
    ("or_value", "orders that are paid or shipped", "SELECT id FROM orders WHERE status = 'paid' OR 'shipped'", "or",
     "SELECT id FROM orders WHERE status IN ('paid', 'shipped')", False, "differs"),
    ("unquoted_word", "orders that are paid", "SELECT id FROM orders WHERE status = paid", "value",
     "SELECT id FROM orders WHERE status = 'paid'", False, ERR),
    ("last_value", "every order with the last order's amount", "SELECT id, LAST_VALUE(amount) OVER (ORDER BY id) AS last_amount FROM orders",
     "win_last_value", "SELECT id, LAST_VALUE(amount) OVER (ORDER BY id ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS last_amount FROM orders",
     False, "differs"),
    ("not_in_null", "customers whose city no later customer has", "SELECT id FROM customers WHERE city NOT IN (SELECT city FROM customers WHERE id > 3)",
     "not_in", None, False, "differs"),
]

# (id, requirement, the person's statement, topic, the correct SQLite statement, what to read after it, LAB)
CHANGES = [
    ("update_limit", "mark the two oldest paid orders archived", "UPDATE orders SET status = 'archived' WHERE status = 'paid' ORDER BY order_date LIMIT 2",
     "change_limit", "UPDATE orders SET status = 'archived' WHERE rowid IN (SELECT rowid FROM orders WHERE status = 'paid' ORDER BY order_date LIMIT 2)",
     "SELECT id, status FROM orders ORDER BY id", BUILD),
    ("delete_limit", "delete one of the returns", "DELETE FROM returns WHERE quantity = 1 LIMIT 1", "change_limit",
     "DELETE FROM returns WHERE rowid IN (SELECT rowid FROM returns WHERE quantity = 1 LIMIT 1)", "SELECT COUNT(*) FROM returns", BUILD),
    ("delete_using", "delete the payments of shipped orders", "DELETE FROM payments USING orders WHERE orders.id = payments.order_id AND orders.status = 'shipped'",
     "delete_join_syntax", "DELETE FROM payments WHERE EXISTS (SELECT 1 FROM orders WHERE orders.id = payments.order_id AND orders.status = 'shipped')",
     "SELECT id FROM payments ORDER BY id", ERR),
    ("set_qualified", "close order 1", "UPDATE orders SET orders.status = 'closed' WHERE id = 1", "set_qualified",
     "UPDATE orders SET status = 'closed' WHERE id = 1", "SELECT id, status FROM orders ORDER BY id", ERR),
    ("replace_keeps", "rename product 7, keeping its price and stock", "REPLACE INTO products (id, name) VALUES (7, 'Pager Two')", "replace_into",
     "INSERT INTO products (id, name) VALUES (7, 'Pager Two') ON CONFLICT (id) DO UPDATE SET name = excluded.name",
     "SELECT id, name, price, stock FROM products WHERE id = 7", "differs"),
]

VALID = [
    "SELECT id, first_name || ' ' || last_name AS full_name FROM customers",
    "SELECT SUM(status = 'paid') AS paid FROM orders",
    "SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id HAVING total > 1000",
    "SELECT id, amount * 2 AS doubled FROM orders WHERE doubled > 100000",
    "SELECT COUNT(*) FROM (SELECT id FROM orders WHERE status = 'paid')",
    "SELECT id FROM orders WHERE id IN (SELECT id FROM orders ORDER BY amount DESC LIMIT 2)",
    "SELECT id, IFNULL(discount, 0) AS discount FROM orders",
    "SELECT id FROM orders ORDER BY id LIMIT 3 OFFSET 2",
    "SELECT id FROM orders ORDER BY id LIMIT -1 OFFSET 5",
    "SELECT id, discount FROM orders ORDER BY discount NULLS LAST, id",
    "SELECT id FROM customers EXCEPT SELECT customer_id FROM orders",
    "SELECT COUNT(*) FILTER (WHERE status = 'paid') FROM orders",
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 3) SELECT x FROM n",
    "WITH n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 3) SELECT x FROM n",
    "UPDATE orders SET status = c.city FROM customers c WHERE c.id = orders.customer_id AND c.id = 1",
    "INSERT INTO categories (id, name) VALUES (9, 'Games') ON CONFLICT (id) DO UPDATE SET name = excluded.name",
    "DELETE FROM payments WHERE order_id IN (SELECT id FROM orders WHERE status = 'cancelled')",
    "SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders) ORDER BY id",
]

# night 3's words SQLite refuses (the dialect file's 'refuses'), each with a sample: refused by SQLite, found by DBaba
REFUSED_NIGHT3 = {
    "eq_any": "SELECT id FROM customers WHERE id = ANY (SELECT customer_id FROM orders)",
    "ne_all": "SELECT id FROM customers WHERE id <> ALL (SELECT customer_id FROM orders)",
    "any_all": "SELECT id FROM products WHERE price > ALL (SELECT price FROM products WHERE category_id = 2)",
    "minus": "SELECT id FROM customers MINUS SELECT customer_id FROM orders",
    "lateral": "SELECT c.id FROM customers c, LATERAL (SELECT 1 AS x) AS z",
    "distinct_on": "SELECT DISTINCT ON (customer_id) customer_id, id FROM orders",
    "qualify": "SELECT id FROM orders QUALIFY ROW_NUMBER() OVER (ORDER BY id) = 1",
    "offset_alone": "SELECT id FROM orders ORDER BY id OFFSET 5",
    "union_part_order": "SELECT id FROM customers ORDER BY id UNION SELECT customer_id FROM orders",
}

OTHER_ENGINES = re.compile(r"\berror (1\d{3}|[2-4]\d{3})\b|\b42[0-9A-Z]\d\d\b|MySQL stops|PostgreSQL refuses|MySQL refuses|"
                           r"PostgreSQL stops|MariaDB stops|on PostgreSQL, not text|doesn't exist on PostgreSQL")


@pytest.fixture()
def lite():
    d = tempfile.mkdtemp()
    conn = S.sqlite_db(os.path.join(d, "bank.db"))
    yield conn
    conn.close()
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    os.rmdir(d)


def _rows(conn, sql, ordered=False):
    rows = [tuple(S.norm(v) for v in r) for r in conn.execute(sql).fetchall()]
    return rows if ordered else sorted(rows)


def _try(conn, sql, ordered=False):
    try:
        return _rows(conn, sql, ordered)
    except sqlite3.Error:
        return ERR


def _rule_conn(setup):
    c = sqlite3.connect(":memory:", isolation_level=None)
    for s in setup:
        c.execute(s)
    return c


def _run_line(setup, sql):
    c = _rule_conn(setup)
    try:
        cur = None
        for part in sql.split(";\n"):
            cur = c.execute(part)
        rows = cur.fetchall()
        if sql.split(";\n")[-1].lstrip().upper().startswith("EXPLAIN QUERY PLAN"):
            return [r[3] for r in rows]
        return [list(r) for r in rows]
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    finally:
        c.close()


def _plan_words(lines):
    """What a plan line says about the way in (SCAN / SEARCH and the table), without the version-dependent details."""
    return [" ".join(x.split()[:2]) for x in lines]


def test_every_mysql_and_postgresql_query_rule_is_rechecked_on_sqlite():
    lite_rules = E.dialect("sqlite")["query_rules"]
    for fam in ("mysql", "postgresql"):
        missing = set(E.dialect(fam)["query_rules"]) - set(lite_rules)
        assert not missing, (fam, missing)
    assert E.query_family("sqlite") == "sqlite" and E.dialect("sqlite")["query_rules_proven"]
    for key, r in lite_rules.items():
        assert r["proven"] == ["sqlite"] and (r.get("says") or r.get("says_en")), key
        assert r.get("lab") or key in ("lock_scan", "gap_locks", "deadlock"), key   # locking: night 4 (transactions)


def test_every_query_rule_lab_line_holds():
    """Each stored lab line, run again on SQLite now: the stored result is what SQLite does."""
    d = E.dialect("sqlite")
    setup = d["query_lab_setup"]
    for key, r in d["query_rules"].items():
        if key in ("type_compare", "date_shape"):
            continue                                       # night 2's lines are described, run by test_e1_sqlite2
        if tuple(r.get("since", (0,))) > sqlite3.sqlite_version_info:
            continue
        for sql, want in r["lab"]:
            got = _run_line(setup, sql)
            if r.get("build_option") and LIMIT_BUILD and isinstance(want, dict):
                assert not isinstance(got, dict), (key, sql, got)    # this build has the option: what the lab's refused, runs
            elif sql.split(";\n")[-1].lstrip().upper().startswith("EXPLAIN QUERY PLAN"):
                if PLANS_AS_LAB:
                    assert _plan_words(got) == _plan_words(want), (key, sql, got)
            elif "ORDER BY" in sql.upper() or isinstance(want, dict):
                assert got == want, (key, sql, got)
            else:
                assert sorted(map(str, got)) == sorted(map(str, want)), (key, sql, got)


def test_the_bank_has_enough_problems():
    assert len(P) + len(CHANGES) >= 30
    assert len({p[0] for p in P} | {c[0] for c in CHANGES}) == len(P) + len(CHANGES)


@pytest.mark.parametrize("pid,words,wrong,topic,fix,ordered,lab", P, ids=[p[0] for p in P])
def test_dbaba_finds_and_fixes_on_sqlite(lite, pid, words, wrong, topic, fix, ordered, lab):
    got_wrong = _try(lite, wrong, ordered)
    if lab == ERR:
        assert got_wrong == ERR, (pid, got_wrong)
    elif fix:
        right = _rows(lite, fix, ordered)
        assert (got_wrong == right) == (lab == "same"), (pid, got_wrong, right)
    res = sqlcorrect.check(wrong, intent=words, engine="sqlite")
    topics = [f["topic"] for f in res["findings"]]
    assert topic in topics, (pid, res["findings"])
    for f in res["findings"]:
        assert not OTHER_ENGINES.search(f["title_en"] + " " + f["fix_en"]), (pid, f["title_en"], f["fix_en"][:300])
    if fix:
        right = _rows(lite, fix, ordered)
        offered = [res.get("corrected")] + [g for g in res.get("guesses", []) if isinstance(g, str) and g.lstrip().upper().startswith(("SELECT", "WITH"))]
        assert any(o and _try(lite, re.sub(r"/\*.*?\*/", "", o), ordered) == right for o in offered), (pid, offered, right)


@pytest.mark.parametrize("cid,words,wrong,topic,fix,probe,lab", CHANGES, ids=[c[0] for c in CHANGES])
def test_changes_are_rewritten_in_sqlites_words(lite, cid, words, wrong, topic, fix, probe, lab):
    def effect(sql):
        lite.execute("BEGIN")
        try:
            lite.execute(sql)
            return _rows(lite, probe, True)
        except sqlite3.Error:
            return ERR
        finally:
            lite.execute("ROLLBACK")

    assert effect(fix) != ERR
    got = effect(wrong)
    if lab == BUILD:
        assert got == (effect(fix) if LIMIT_BUILD else ERR), (cid, got)      # the rowid form does the same on every build
    else:
        assert (got == ERR) if lab == ERR else (got != effect(fix)), (cid, got)
    res = sqlcorrect.check(wrong, intent=words, engine="sqlite")
    assert topic in [f["topic"] for f in res["findings"]], (cid, res["findings"])
    for f in res["findings"]:
        assert not OTHER_ENGINES.search(f["title_en"] + " " + f["fix_en"]), (cid, f["title_en"], f["fix_en"][:300])
    assert res["corrected"] and effect(res["corrected"]) == effect(fix), (cid, res["corrected"])


@pytest.mark.parametrize("sql", VALID)
def test_valid_sqlite_gets_no_error_or_wrong(lite, sql):
    lite.execute("BEGIN")
    try:
        lite.execute(sql).fetchall()                       # it runs on SQLite
    finally:
        lite.execute("ROLLBACK")
    res = sqlcorrect.check(sql, engine="sqlite")
    sure = [f for f in res["findings"] if f["kind"] in ("error", "wrong")]
    assert not sure, (sql, sure)


def test_the_same_queries_still_get_the_other_databases_answers():
    """Rule A both ways: what SQLite accepts is still a mistake on the database that refuses it."""
    assert any(f["topic"] == "alias" for f in sqlcorrect.check(VALID[3], engine="postgresql")["findings"])
    assert any(f["topic"] == "concat" for f in sqlcorrect.check(VALID[0], engine="mysql")["findings"])
    assert any(f["topic"] == "sum_bool" and f["kind"] == "error" for f in sqlcorrect.check(VALID[1], engine="postgresql")["findings"])
    assert any(f["topic"] == "change_limit" for f in sqlcorrect.check(CHANGES[0][2], engine="postgresql")["findings"])


def test_bare_column_with_one_max_is_only_a_check_on_sqlite(lite):
    """SQLite's promise: with exactly one MAX(), the other columns come from that row (lab: the same rows as the window form)."""
    q = "SELECT customer_id, id, MAX(amount) AS biggest FROM orders GROUP BY customer_id"
    window = ("SELECT customer_id, id, amount FROM (SELECT customer_id, id, amount, ROW_NUMBER() OVER (PARTITION BY customer_id "
              "ORDER BY amount DESC) AS rn FROM orders) WHERE rn = 1")
    assert _rows(lite, q) == _rows(lite, window)
    fs = [f for f in sqlcorrect.check(q, engine="sqlite")["findings"] if f["topic"] == "group"]
    assert fs and all(f["kind"] == "check" for f in fs), fs
    assert [f["kind"] for f in sqlcorrect.check(q.replace("MAX(amount)", "SUM(amount)"), engine="sqlite")["findings"] if f["topic"] == "group"] == ["wrong"]
    assert any(f["kind"] == "error" for f in sqlcorrect.check(q, engine="mysql")["findings"] if f["topic"] == "group")


def test_a_random_value_used_twice_gets_sqlites_materialized_form(lite):
    q = "SELECT id, abs(random()) % 10 AS r FROM orders WHERE abs(random()) % 10 > 4"
    res = sqlcorrect.check(q, engine="sqlite")
    f = next(f for f in res["findings"] if f["topic"] == "volatile_repeat")
    assert "MATERIALIZED" in f["fix_en"] and "MariaDB" not in f["fix_en"]
    offered = res.get("corrected") or ""
    assert "AS MATERIALIZED" in offered, offered
    for _ in range(20):                                    # computed once: the r shown is always the one tested
        assert all(int(r[1]) > 4 for r in lite.execute(offered).fetchall())


@pytest.mark.parametrize("rid,sample", sorted(REFUSED_NIGHT3.items()))
def test_tonights_refused_words(lite, rid, sample):
    r = next(x for x in E.dialect("sqlite")["refuses"] if x["id"] == rid)
    assert r["proven"] == ["sqlite"]
    with pytest.raises(sqlite3.Error):
        lite.execute(sample)
    res = sqlcorrect.check(sample, engine="sqlite")
    assert any(f["topic"] == "dialect" for f in res["findings"]), (rid, res["findings"])
    if res["corrected"]:
        lite.execute(res["corrected"]).fetchall()


def test_earlier_nights_banks_speak_sqlite_once_it_is_named():
    """Nights 1-4 and 7 (the query banks): with SQLite named, no finding quotes MySQL's or PostgreSQL's error numbers."""
    import importlib

    seen = 0
    for n in ("1", "2", "3", "4", "7"):
        for p in importlib.import_module(f"tests.practice.test_night{n}").P:
            wrong = p[2]
            if not isinstance(wrong, str):
                continue
            for f in sqlcorrect.check(wrong, intent=p[1], engine="sqlite")["findings"]:
                seen += 1
                assert not OTHER_ENGINES.search(f["title_en"] + " " + f["fix_en"]), (n, p[0], f["title_en"], f["fix_en"][:300])
    assert seen > 150


# ---- EXPLAIN QUERY PLAN, read by sqlexplain (the plans below were made by SQLite on the practice tables)
PLANS = [
    ("cli_tree", "QUERY PLAN\n|--SCAN c\n|--CORRELATED SCALAR SUBQUERY 1\n|  `--SCAN o\n`--USE TEMP B-TREE FOR ORDER BY",
     {"plan_dependent", "plan_full_scan", "plan_filesort"}),
    ("driver_rows", "(3, 0, 216, 'SCAN o')\n(5, 0, 45, 'SEARCH c USING INTEGER PRIMARY KEY (rowid=?)')", {"plan_full_scan"}),
    ("pipes", "2|0|216|SCAN customers\n8|0|0|USE TEMP B-TREE FOR GROUP BY", {"plan_full_scan", "plan_temporary"}),
    ("auto_index", "QUERY PLAN\n|--SCAN c USING COVERING INDEX sqlite_autoindex_customers_1\n|--BLOOM FILTER ON o (customer_id=?)\n"
                   "`--SEARCH o EXISTS USING AUTOMATIC PARTIAL COVERING INDEX (customer_id=?)", {"plan_auto_index", "plan_full_index"}),
    ("good", "QUERY PLAN\n`--SEARCH orders USING INDEX ix_orders_status_date (status=?)", set()),
]


@pytest.mark.parametrize("pid,text,topics", PLANS, ids=[p[0] for p in PLANS])
def test_sqlites_plans_are_read(pid, text, topics):
    assert sqlexplain.looks_like_plan(text)
    plan = sqlexplain.read(text)
    assert plan and plan["engine"] == "sqlite" and plan["steps"]
    assert {f["topic"] for f in plan["findings"]} == topics, (pid, plan["findings"])
    out = sqlexplain.render(plan)
    assert "SQLite" in out and "MySQL" not in out and "PostgreSQL" not in out
    assert all(st["en"].count(":") >= 1 for st in plan["steps"])          # every line explained in words


def test_the_plans_in_the_bank_are_what_sqlite_prints(lite):
    """The plan shapes above come from SQLite itself: the same queries give the same steps here."""
    lite.execute("CREATE INDEX ix_orders_status_date ON orders (status, order_date)")
    got = [r[3] for r in lite.execute("EXPLAIN QUERY PLAN SELECT id FROM orders WHERE status = 'paid' ORDER BY order_date")]
    assert _plan_words(got) == ["SEARCH orders"]
    got = [r[3] for r in lite.execute("EXPLAIN QUERY PLAN SELECT c.id, d.id FROM customers c JOIN customers d ON d.city = c.city "
                                      "AND d.id <> c.id")]
    assert any("AUTOMATIC" in x for x in got), got                         # 3.45 and 3.53 alike
    got = [r[3] for r in lite.execute("EXPLAIN QUERY PLAN SELECT id, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) AS n "
                                      "FROM customers c ORDER BY n")]
    assert any(x.startswith("CORRELATED SCALAR SUBQUERY") for x in got) and any(x.startswith("USE TEMP B-TREE") for x in got), got
    rows = lite.execute("EXPLAIN QUERY PLAN SELECT id, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) AS n FROM customers c "
                        "ORDER BY n").fetchall()
    plan = sqlexplain.read("\n".join(str(tuple(r)) for r in rows))            # the rows a driver returns, as printed
    assert plan and "plan_dependent" in {f["topic"] for f in plan["findings"]}, plan


def test_postgres_and_mysql_plans_are_still_theirs():
    pg = "Seq Scan on orders  (cost=0.00..35.50 rows=2550 width=4)\n  Filter: (status = 'paid'::text)"
    assert sqlexplain.read(pg)["engine"] == "postgresql"
    my = "-> Table scan on orders  (cost=1.05 rows=8)"
    assert sqlexplain.read(my)["engine"] == "mysql"


def test_the_lab_rules_say_what_sqlite_does():
    rules = E.dialect("sqlite")["query_rules"]
    assert rules["any_all"]["lab"][0][1] == {"error": 'near "SELECT": syntax error'}
    assert rules["scalar_subquery_rows"]["behaviour"] == "first_row"
    assert rules["alias_where"]["behaviour"] == "works" and rules["having_alias"]["behaviour"] == "works"
    assert json.loads(json.dumps(rules["volatile_once"]["lab"]))[-1][1] == [[0]]      # only MATERIALIZED keeps one value
