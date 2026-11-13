"""Night 11 practice bank: with only the query, say how sure each finding is, and never invent the tables.

Each problem is a requirement (what the person wants), the query they wrote, the database it is for, the mistake DBaba must
find, the CLASS it must give it, and how its answer is proven on MariaDB and PostgreSQL (practice schema, tests/practice/schema.py):
- 'fix'   : PROVEN from the text. The corrected query returns the requirement's rows (the truth) on both databases.
- 'offer' : LIKELY RISK / NEEDS SCHEMA. The query offered ("if that's what you meant") returns the truth on both databases.
- 'same'  : NEEDS EXECUTION (a speed risk). The faster form returns exactly the rows the query returns, on both databases.
- 'found' : found with the right class; the rewrite needs a value the person supplies (keyset) or isn't one query.
For every problem, on both engines: the inferred schema names only tables and columns the practice schema really has (nothing
invented, and nothing marked verified), and every read-only check query in "what can't be proven" runs on that engine.
The upgrade path: the same queries with the practice schema's CREATE TABLE turn 'needs schema' findings into verified ones, or
drop them when the tables rule them out.
LAB is what each engine really does with tonight's classes (MariaDB 10.11 / PostgreSQL 16), re-run here.
"""

import re

import pytest

from app.agents import sqlconfidence, sqlproject, sqlverify
from app.agents.sql import analyze_sql
from tests.fuzz.db import MYSQL, PG, Engine
from tests.practice import schema as S

# (id, requirement, query, engine, topic, class, proof, truth)
P = [
    # ---- PROVEN: the text alone decides it
    ("eq_null", "customers without a phone", "SELECT id FROM customers WHERE phone = NULL ORDER BY id", "mariadb", "null", "proven", "fix",
     "SELECT id FROM customers WHERE phone IS NULL ORDER BY id"),
    ("neq_null", "orders that got a discount", "SELECT id FROM orders WHERE discount != NULL ORDER BY id", "postgresql", "null", "proven",
     "fix", "SELECT id FROM orders WHERE discount IS NOT NULL ORDER BY id"),
    ("agg_where", "customers with more than one order", "SELECT customer_id FROM orders WHERE COUNT(*) > 1 GROUP BY customer_id",
     "mariadb", "where", "proven", "found", None),
    ("unknown_alias", "each order with its customer's first name",
     "SELECT o.id, c.first_name FROM orders o JOIN customers c ON c.id = x.customer_id ORDER BY o.id", "postgresql", "alias", "proven",
     "found", None),
    ("alias_as_table", "paid orders", "SELECT o.id FROM orders o WHERE orders.status = 'paid' ORDER BY o.id", "mariadb", "alias", "proven",
     "fix", "SELECT id FROM orders WHERE status = 'paid' ORDER BY id"),
    ("between_rev", "orders between 100 and 1000", "SELECT id FROM orders WHERE amount BETWEEN 1000 AND 100 ORDER BY id", "postgresql",
     "between", "proven", "fix", "SELECT id FROM orders WHERE amount >= 100 AND amount <= 1000 ORDER BY id"),
    ("or_value", "paid or shipped orders", "SELECT id FROM orders WHERE status = 'paid' OR 'shipped' ORDER BY id", "mariadb", "or", "proven",
     "fix", "SELECT id FROM orders WHERE status = 'paid' OR status = 'shipped' ORDER BY id"),
    ("count_case", "number of paid orders", "SELECT COUNT(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid FROM orders", "postgresql",
     "count_case", "proven", "fix", "SELECT COUNT(*) AS paid FROM orders WHERE status = 'paid'"),
    ("like_star", "products whose name starts with Phone", "SELECT id FROM products WHERE name LIKE 'Phone*' ORDER BY id", "mariadb",
     "like", "proven", "fix", "SELECT id FROM products WHERE name LIKE 'Phone%' ORDER BY id"),
    ("win_where", "the cheapest order", "SELECT id FROM orders WHERE ROW_NUMBER() OVER (ORDER BY amount) = 1", "postgresql", "win_where",
     "proven", "fix", "SELECT id FROM orders WHERE amount = (SELECT MIN(amount) FROM orders)"),
    ("case_null", "label orders with or without a discount",
     "SELECT id, CASE WHEN discount = NULL THEN 'none' ELSE 'some' END AS d FROM orders ORDER BY id", "mariadb", "null", "proven", "fix",
     "SELECT id, CASE WHEN discount IS NULL THEN 'none' ELSE 'some' END AS d FROM orders ORDER BY id"),
    # ---- LIKELY RISK: the shape suggests it; the data (how many rows match) or the intent decides
    ("fanout", "total order amount per customer",
     "SELECT o.customer_id, SUM(o.amount) AS total FROM orders o JOIN order_items i ON i.order_id = o.id GROUP BY o.customer_id "
     "ORDER BY o.customer_id", "postgresql", "join_fanout", "likely_risk", "offer",
     "SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id ORDER BY customer_id"),
    ("fanout_where", "total of paid orders per customer",
     "SELECT o.customer_id, SUM(o.amount) AS total FROM orders o JOIN order_items i ON i.order_id = o.id WHERE o.status = 'paid' "
     "GROUP BY o.customer_id ORDER BY o.customer_id", "mariadb", "join_fanout", "likely_risk", "offer",
     "SELECT customer_id, SUM(amount) AS total FROM orders WHERE status = 'paid' GROUP BY customer_id ORDER BY customer_id"),
    ("fanout_left", "each order's amount",
     "SELECT o.id, SUM(o.amount) AS total FROM orders o LEFT JOIN order_items i ON i.order_id = o.id GROUP BY o.id ORDER BY o.id",
     "postgresql", "join_fanout", "likely_risk", "offer", "SELECT id, amount AS total FROM orders ORDER BY id"),
    ("fanout_count", "number of orders per customer",
     "SELECT o.customer_id, COUNT(o.id) AS n FROM orders o JOIN order_items i ON i.order_id = o.id GROUP BY o.customer_id "
     "ORDER BY o.customer_id", "mariadb", "join_fanout", "likely_risk", "offer",
     "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id ORDER BY customer_id"),
    ("distinct_join", "customers who have orders, with their first name",
     "SELECT DISTINCT c.id, c.first_name FROM customers c JOIN orders o ON o.customer_id = c.id ORDER BY c.id", "postgresql",
     "distinct_join", "likely_risk", "offer",
     "SELECT id, first_name FROM customers WHERE id IN (SELECT customer_id FROM orders) ORDER BY id"),
    ("and_or", "paid or shipped orders, both above 1000",
     "SELECT id FROM orders WHERE status = 'paid' OR status = 'shipped' AND amount > 1000 ORDER BY id", "mariadb", "and_or",
     "likely_risk", "offer", "SELECT id FROM orders WHERE status IN ('paid', 'shipped') AND amount > 1000 ORDER BY id"),
    ("avg_avg", "average order amount over all orders",
     "SELECT AVG(avg_amt) AS avg_order FROM (SELECT customer_id, AVG(amount) AS avg_amt FROM orders GROUP BY customer_id) t",
     "postgresql", "avg_avg", "likely_risk", "offer", "SELECT AVG(amount) AS avg_order FROM orders"),
    ("like_contains", "products with Laptop in the name", "SELECT id FROM products WHERE name LIKE 'Laptop' ORDER BY id", "mariadb",
     "like", "likely_risk", "offer", "SELECT id FROM products WHERE id IN (3, 6) ORDER BY id"),
    ("self_pairs", "pairs of employees in the same department, each pair once",
     "SELECT a.id, b.id FROM employees a JOIN employees b ON a.department_id = b.department_id ORDER BY a.id, b.id", "postgresql",
     "self_join", "likely_risk", "offer",
     "SELECT a.id, b.id FROM employees a JOIN employees b ON a.department_id = b.department_id AND a.id < b.id ORDER BY a.id, b.id"),
    # ---- NEEDS SCHEMA: a fact about the tables decides it (NULLs, keys, a time of day)
    ("not_in", "departments that have no employees",
     "SELECT id FROM departments WHERE id NOT IN (SELECT department_id FROM employees) ORDER BY id", "mariadb", "not_in", "needs_schema",
     "offer", "SELECT id FROM departments WHERE id = 4"),
    ("ties_limit", "the two highest paid employees", "SELECT id, salary FROM employees ORDER BY salary DESC LIMIT 2", "postgresql",
     "stable_order", "needs_schema", "offer", "SELECT id, salary FROM employees WHERE id IN (1, 6)"),
    ("between_date", "orders placed in June 2024",
     "SELECT id FROM orders WHERE order_date BETWEEN '2024-06-01' AND '2024-06-30' ORDER BY id", "mariadb", "date_range", "needs_schema",
     "offer", "SELECT id FROM orders WHERE id IN (1, 2, 3) ORDER BY id"),
    ("null_math", "net amount of each order (amount minus discount)", "SELECT id, amount - discount AS net FROM orders ORDER BY id",
     "postgresql", "null_math", "needs_schema", "offer",
     "SELECT id, amount - CASE WHEN discount IS NULL THEN 0 ELSE discount END AS net FROM orders ORDER BY id"),
    ("concat_null", "full name of each customer",
     "SELECT id, CONCAT(first_name, ' ', middle_name, ' ', last_name) AS full_name FROM customers ORDER BY id", "mariadb", "concat",
     "needs_schema", "found", None),
    ("anti_nullable", "customers with no orders",
     "SELECT c.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.discount IS NULL ORDER BY c.id", "mariadb",
     "anti_join", "needs_schema", "offer", "SELECT id FROM customers WHERE id NOT IN (1, 2, 3, 6, 8) ORDER BY id"),
    ("pk_pk", "each order with its customer's email", "SELECT o.id, c.email FROM orders o JOIN customers c ON c.id = o.id ORDER BY o.id",
     "postgresql", "join_key", "needs_schema", "offer",
     "SELECT o.id, c.email FROM orders o JOIN customers c ON c.id = o.customer_id ORDER BY o.id"),
    # ---- NEEDS EXECUTION: speed risks; the faster form returns the same rows
    ("year", "orders of 2024", "SELECT id FROM orders WHERE YEAR(order_date) = 2024 ORDER BY id", "mariadb", "sargable", "needs_execution",
     "same", "SELECT id FROM orders WHERE EXTRACT(YEAR FROM order_date) = 2024 ORDER BY id"),
    ("year_pg", "orders of 2024", "SELECT id FROM orders WHERE EXTRACT(YEAR FROM order_date) = 2024 ORDER BY id", "postgresql",
     "sargable", "needs_execution", "same", "SELECT id FROM orders WHERE EXTRACT(YEAR FROM order_date) = 2024 ORDER BY id"),
    ("left", "products whose sku starts with PH", "SELECT id FROM products WHERE LEFT(sku, 2) = 'PH' ORDER BY id", "mariadb", "sargable",
     "needs_execution", "same", "SELECT id FROM products WHERE LEFT(sku, 2) = 'PH' ORDER BY id"),
    ("cast_day", "orders placed on 2024-06-01", "SELECT id FROM orders WHERE DATE(order_date) = '2024-06-01' ORDER BY id", "mariadb",
     "sargable", "needs_execution", "same", "SELECT id FROM orders WHERE CAST(order_date AS DATE) = '2024-06-01' ORDER BY id"),
    ("arith", "orders whose double amount is above 1000", "SELECT id FROM orders WHERE amount * 2 > 1000 ORDER BY id", "postgresql",
     "sargable", "needs_execution", "same", "SELECT id FROM orders WHERE amount * 2 > 1000 ORDER BY id"),
    ("coalesce", "orders with a discount above 10", "SELECT id FROM orders WHERE COALESCE(discount, 0) > 10 ORDER BY id", "mariadb",
     "sargable", "needs_execution", "same", "SELECT id FROM orders WHERE COALESCE(discount, 0) > 10 ORDER BY id"),
    ("or_union", "orders that are paid or whose customer lives in Delhi",
     "SELECT o.id FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.status = 'paid' OR c.city = 'Delhi' ORDER BY o.id",
     "postgresql", "or_union", "needs_execution", "same",
     "SELECT o.id FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.status = 'paid' OR c.city = 'Delhi' ORDER BY o.id"),
    ("keyset", "page 5000 of orders", "SELECT id, amount FROM orders ORDER BY id LIMIT 20 OFFSET 100000", "mariadb", "keyset",
     "needs_execution", "found", None),
]

# what each engine really does (lab re-run in test_the_lab_table_still_holds) -- (MariaDB, PostgreSQL)
LAB = {
    "eq_null_rows": ("0", "0"),                 # PROVEN: '= NULL' keeps nothing on any data
    "agg_in_where": ("1111", "42803"),          # PROVEN: refused
    "unknown_alias": ("1054", "42P01"),         # PROVEN: refused
    "fanout_one_child": ("500", "500"),         # LIKELY RISK: order 2 has one item: the joined SUM is right ...
    "fanout_three_children": ("76500", "76500"),  # ... order 1 has three: the same query triples it (25500 x 3)
    "not_in_with_null": ("0", "0"),             # NEEDS SCHEMA: a NULL department_id empties NOT IN ...
    "not_in_without_null": ("1", "1"),          # ... and without it department 4 comes back
    "between_datetime": ("1", "1"),             # NEEDS SCHEMA: a DATETIME at 10:00 on the last day is lost ...
    "between_date": ("2", "2"),                 # ... a DATE on the last day is kept
    "explain_analyze": ("1064", "ok"),          # NEEDS EXECUTION: MariaDB has no EXPLAIN ANALYZE (ANALYZE SELECT instead)
    "analyze_select": ("ok", "42601"),          # ... and PostgreSQL has no ANALYZE SELECT
    "nullable_temp_table": ("0", "2"),          # information_schema lists TEMPORARY tables on PostgreSQL only
}
REAL = {t: {c.split()[0].lower() for c in cols} for t, (cols, _rows) in S.TABLES.items()}
DDL = "\n".join(f"CREATE TABLE {t} ({', '.join(cols)}, PRIMARY KEY (id));" for t, (cols, _rows) in S.TABLES.items())


@pytest.fixture(scope="module")
def engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)
    assert {p[5] for p in P} == {"proven", "likely_risk", "needs_schema", "needs_execution"}


def _kind(name):
    return "mariadb" if name == "mysql" else "postgresql"


@pytest.mark.parametrize("pid,words,q,engine,topic,cls,proof,truth", P, ids=[p[0] for p in P])
def test_dbaba_classes_the_finding_and_its_answer_holds(engines, pid, words, q, engine, topic, cls, proof, truth):
    res = analyze_sql(f"{q};\n\n{words}", engine=engine)
    fs = res["correct"]["findings"]
    found = [f for f in fs if f["topic"] == topic]
    assert found, (pid, [(f["topic"], f["title_en"]) for f in fs])
    assert all(f["confidence"]["class"] == cls for f in found), (pid, [f["confidence"] for f in found])
    assert all(f.get("confidence") for f in fs), pid                             # every finding says how sure it is
    if cls != "proven":
        assert all(f["kind"] == "check" for f in found), pid                     # never 'wrong' for what the text can't prove
    qo = res["query_only"]
    for t in qo["inferred_schema"]["tables"]:                                    # nothing invented, nothing claimed
        assert t["name"] in REAL and not t["verified"], (pid, t)
        for c, info in t["columns"].items():
            assert c in REAL[t["name"]] or info.get("owner_guess") and any(c in REAL[x["name"]] for x in qo["inferred_schema"]["tables"]), (pid, t["name"], c)
            assert not info["verified"]
    assert qo["cant_prove"] and not re.search(r"missing index|index is missing", qo["en"], re.I), pid
    fixed = res["correct"].get("corrected")
    for name, eng in engines.items():
        if proof == "fix":
            sql = fixed if _kind(name) == engine else analyze_sql(f"{q};\n\n{words}", engine=_kind(name))["correct"]["corrected"]
            assert S.rows(eng, sql.rstrip().rstrip(";")) == S.rows(eng, truth), (pid, name, sql)
        elif proof == "offer":
            sql = fixed if _kind(name) == engine else analyze_sql(f"{q};\n\n{words}", engine=_kind(name))["correct"]["corrected"]
            assert sql and (res.get("suggested") or res.get("rewritten")), pid
            assert S.rows(eng, sql.rstrip().rstrip(";")) == S.rows(eng, truth), (pid, name, sql)
        elif proof == "same":
            other = analyze_sql(f"{q};\n\n{words}", engine=_kind(name))
            sql = other["correct"].get("corrected") or other.get("suggested")
            assert sql, (pid, name)
            assert S.rows(eng, sql.rstrip().rstrip(";")) == S.rows(eng, truth), (pid, name, sql)


@pytest.mark.parametrize("pid,words,q,engine,topic,cls,proof,truth", P, ids=[p[0] for p in P])
def test_every_check_query_runs_read_only_on_its_engine(engines, pid, words, q, engine, topic, cls, proof, truth):
    for name, eng in engines.items():
        res = analyze_sql(f"{q};\n\n{words}", engine=_kind(name))
        refused = any(f["kind"] == "error" for f in res["correct"]["findings"])
        for x in res["query_only"]["cant_prove"]:
            for c in x["check"]:
                sql = c["sql"]
                if refused and re.match(r"\s*(explain|analyze)\b", sql, re.I):
                    continue                                                    # the query itself is refused: so is its plan
                S.build(eng)
                try:
                    eng.run(sql)
                except Exception as exc:  # noqa: BLE001
                    eng.rollback()
                    pytest.fail(f"{pid} on {name}: {sql}: {exc}")


@pytest.mark.parametrize("pid,expect", [
    ("not_in", "verified"),          # employees.department_id is nullable in the practice schema: the risk is real
    ("null_math", "verified"),       # orders.discount is nullable
    ("anti_nullable", "verified"),   # orders.discount is nullable in a matched row
    ("between_date", "dropped"),     # orders.order_date is a DATE: no time of day, the last day is whole
    ("ties_limit", "verified"),      # salary is not a key: ties are possible
    ("fanout", "verified"),          # order_items.order_id is not a key: an order can have several items
    ("distinct_join", "verified"),
])
def test_the_tables_upgrade_or_drop_what_needed_them(pid, expect):
    p = next(x for x in P if x[0] == pid)
    q, engine, topic = p[2], p[3], p[4]
    res = sqlverify.check(q, sqlproject.from_text(DDL), engine=engine)
    sqlconfidence.annotate(q, res, sqlproject.from_text(DDL))
    found = [f for f in res["findings"] if f["topic"] == topic]
    if expect == "dropped":
        assert not found and topic in {d["topic"] for d in res.get("dropped", [])}, (pid, res["findings"], res.get("dropped"))
    else:
        assert found and all(f.get("schema") == "verified" and f["confidence"]["verified"] and f["confidence"]["class"] == "proven"
                             for f in found), (pid, found)


def test_with_tables_the_inferred_schema_is_verified():
    q = P[[p[0] for p in P].index("fanout")][2]
    inf = sqlconfidence.infer_schema(q, sqlproject.from_text(DDL))
    assert all(t["verified"] for t in inf["tables"])
    assert inf["links"] and inf["links"][0]["verified"] and inf["links"][0]["shape"] == "many order_items -> one orders"
    assert inf["tables"][0]["columns"]["amount"]["real_type"].upper().startswith("DECIMAL")


def test_quiet_on_totals_that_join_only_to_one():
    """A total joined to its parent (each order has one customer) copies nothing: no fan-out finding."""
    for q in ["SELECT c.city, SUM(o.amount) FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY c.city",
              "SELECT o.customer_id, SUM(i.quantity) FROM orders o JOIN order_items i ON i.order_id = o.id GROUP BY o.customer_id",
              "SELECT o.id, SUM(o.amount) FROM orders o GROUP BY o.id"]:
        res = analyze_sql(q, engine="mariadb")
        assert not [f for f in res["correct"]["findings"] if f["topic"] == "join_fanout"], q


def test_index_advice_is_worth_checking_never_missing():
    res = analyze_sql("SELECT id FROM orders WHERE status = 'paid' AND customer_id = 3", engine="postgresql")
    titles = " ".join(f["title_en"] for f in res["findings"])
    assert "Worth checking whether these columns have an index" in titles and "Missing indexes" not in titles
    assert all(f["confidence"]["class"] == "needs_execution" for f in res["findings"] if not f.get("kind"))
    assert "Whether an index exists on orders(status)" in res["query_only"]["en"]


# ---------------------------------------------------------------- the lab
def _code(exc) -> str:
    a = getattr(exc, "args", ())
    if a and isinstance(a[0], int):
        return str(a[0])
    return getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None) or "err"


def _one(eng, sql):
    try:
        rows = eng.run(sql)
        return str(S.norm(rows[0][0])) if rows and rows[0] else "ok"
    except Exception as exc:  # noqa: BLE001
        eng.rollback()
        return _code(exc)


def _lab(eng, kind):
    S.build(eng)
    out = {"eq_null_rows": _one(eng, "SELECT COUNT(*) FROM customers WHERE middle_name = NULL"),
           "agg_in_where": _one(eng, "SELECT customer_id FROM orders WHERE SUM(amount) > 100"),
           "unknown_alias": _one(eng, "SELECT o.id FROM orders o JOIN customers c ON c.id = x.customer_id"),
           "fanout_one_child": _one(eng, "SELECT SUM(o.amount) FROM orders o JOIN order_items i ON i.order_id = o.id WHERE o.id = 2"),
           "fanout_three_children": _one(eng, "SELECT SUM(o.amount) FROM orders o JOIN order_items i ON i.order_id = o.id WHERE o.id = 1"),
           "not_in_with_null": _one(eng, "SELECT COUNT(*) FROM departments WHERE id NOT IN (SELECT department_id FROM employees)"),
           "not_in_without_null": _one(eng, "SELECT COUNT(*) FROM departments WHERE id NOT IN "
                                            "(SELECT department_id FROM employees WHERE department_id IS NOT NULL)")}
    ts = "DATETIME" if kind == "mysql" else "TIMESTAMP"
    for t in ("lab11_dt", "lab11_d"):
        eng.run(f"DROP TABLE IF EXISTS {t}")
    try:
        eng.run(f"CREATE TABLE lab11_dt (id INT PRIMARY KEY, at {ts})")
        eng.run("INSERT INTO lab11_dt VALUES (1, '2024-01-15 09:00:00'), (2, '2024-01-31 10:00:00')")
        eng.run("CREATE TABLE lab11_d (id INT PRIMARY KEY, at DATE)")
        eng.run("INSERT INTO lab11_d VALUES (1, '2024-01-15'), (2, '2024-01-31')")
        out["between_datetime"] = _one(eng, "SELECT COUNT(*) FROM lab11_dt WHERE at BETWEEN '2024-01-01' AND '2024-01-31'")
        out["between_date"] = _one(eng, "SELECT COUNT(*) FROM lab11_d WHERE at BETWEEN '2024-01-01' AND '2024-01-31'")
    finally:
        for t in ("lab11_dt", "lab11_d"):
            eng.run(f"DROP TABLE IF EXISTS {t}")
    out["explain_analyze"] = _one(eng, "EXPLAIN ANALYZE SELECT id FROM orders WHERE status = 'paid'")
    out["analyze_select"] = _one(eng, "ANALYZE SELECT id FROM orders WHERE status = 'paid'")
    out["nullable_temp_table"] = _one(eng, "SELECT COUNT(*) FROM information_schema.columns WHERE table_name = 'payments' "
                                           "AND column_name IN ('method', 'paid_at')")
    for k in ("explain_analyze", "analyze_select"):
        out[k] = "ok" if out[k] not in ("1064", "42601") else out[k]
    S.build(eng)
    return out


def test_the_lab_table_still_holds(engines):
    got = {name: _lab(eng, eng.kind) for name, eng in engines.items()}
    for key, (maria, pg) in LAB.items():
        assert (got["mysql"][key], got["postgres"][key]) == (maria, pg), (key, got["mysql"][key], got["postgres"][key])


def test_every_class_rule_records_where_it_was_proven():
    import json

    from app.dbknow import engines as E

    assert sqlconfidence.PROVEN_ON == ["mariadb", "postgresql"]
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert funds["proof_classes"]["proven"] == ["mariadb", "postgresql"]
    for eid in ("mysql", "mariadb", "postgresql"):                     # rule B: each engine's own check words, in its own file
        v = E.dialect(eid)["verify"]
        assert {"nullable", "dupes", "indexes", "plan", "plan_run"} <= set(v) and v["proven"], eid
    assert E.dialect("mariadb")["verify"]["plan_run"].startswith("ANALYZE ")
    assert E.dialect("postgresql")["verify"]["plan_run"].startswith("EXPLAIN (ANALYZE")
