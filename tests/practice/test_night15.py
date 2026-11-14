"""Night 15 practice bank: the big-query exam, and the upgrade path.

1. The generated exam (tests/fuzz/bigq.py): 100-300 line report queries (nested CTEs, two or more window functions, a UNION
   ALL, correlated subqueries) with one planted mistake each, two seeds per family. DBaba gives the dependency tree and the
   grain of every block, finds the planted mistake in its block with the right confidence class, says nothing sure about
   anything else (nor about the clean query), and its answer is proven on MariaDB and PostgreSQL: a PROVEN mistake's fix
   returns the requirement's rows, a risk's offer returns them, a speed rewrite returns exactly the query's own rows.
2. The upgrade path: the same query pasted with CREATE TABLE turns NEEDS SCHEMA findings into verified ones or drops them
   (NOT IN on a nullable vs a NOT NULL column); pasted with its EXPLAIN, a NEEDS EXECUTION risk is confirmed by the plan
   (verified) or ruled out by it.
3. Hand-made problems for what the exam taught tonight, each a requirement, a query, the database, the mistake (or none) and
   how the answer is proven on both databases:
   - 'fix'    : the corrected query returns the requirement's rows (the truth) on both databases;
   - 'change' : a data change MariaDB refuses as written: the corrected statement, run on MariaDB, changes exactly the rows
                the original changes on PostgreSQL (which accepts it);
   - 'found'  : found with its class (the cure needs a choice only the person can make);
   - 'quiet'  : a correct query that used to get a false 'check' (no error / wrong, and not that check), returning the truth.
LAB is what each engine really does with tonight's facts (MariaDB 10.11 / PostgreSQL 16), re-run here.
"""

import re

import pytest

from app.agents import sqlblocks, sqlconfidence
from app.agents.sql import analyze_sql
from tests.fuzz import bigq
from tests.practice import schema as S

PAID_PER = "SELECT customer_id, SUM(amount) AS s FROM orders WHERE status = 'paid' GROUP BY customer_id"

# (id, requirement, query, engine, topic, class, proof, truth)
P = [
    # ---- a condition on a total written in WHERE (refused on both): moved to HAVING, the row conditions stay in WHERE
    ("having_move", "customers whose paid orders add up to more than 1000",
     "SELECT customer_id, SUM(amount) AS s FROM orders WHERE status = 'paid' AND SUM(amount) > 1000 GROUP BY customer_id",
     "mariadb", "where", "proven", "fix",
     "SELECT customer_id, SUM(amount) AS s FROM orders WHERE status = 'paid' GROUP BY customer_id HAVING SUM(amount) > 1000"),
    ("having_move_pg", "customers with at least two orders",
     "SELECT customer_id, COUNT(*) AS n FROM orders WHERE COUNT(*) >= 2 GROUP BY customer_id ORDER BY customer_id",
     "postgresql", "where", "proven", "fix",
     "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id HAVING COUNT(*) >= 2 ORDER BY customer_id"),
    ("having_join", "having added to an existing HAVING",
     "SELECT o.customer_id, SUM(o.amount) AS s FROM orders o WHERE MAX(o.amount) > 500 GROUP BY o.customer_id HAVING COUNT(*) > 1",
     "mariadb", "where", "proven", "fix",
     "SELECT o.customer_id, SUM(o.amount) AS s FROM orders o GROUP BY o.customer_id HAVING COUNT(*) > 1 AND MAX(o.amount) > 500"),
    ("having_in_cte", "paid customers whose spend is above 1000, inside a CTE",
     "WITH t AS (SELECT customer_id, SUM(amount) AS s FROM orders\n  WHERE status = 'paid' AND SUM(amount) > 1000\n  GROUP BY customer_id)\n"
     "SELECT customer_id, s FROM t", "postgresql", "where", "proven", "fix",
     "SELECT customer_id, SUM(amount) AS s FROM orders WHERE status = 'paid' GROUP BY customer_id HAVING SUM(amount) > 1000"),
    ("having_or_found", "an OR with a total: no safe move, found only",
     "SELECT customer_id FROM orders WHERE SUM(amount) > 1000 OR status = 'paid' GROUP BY customer_id",
     "mariadb", "where", "proven", "found", None),
    # ---- MariaDB: LAG / LEAD have no default argument (1064); the default goes outside in COALESCE
    ("lag_default", "each order with the amount of the order before it (0 for the first)",
     "SELECT id, amount, LAG(amount, 1, 0) OVER (ORDER BY id) AS prev FROM orders", "mariadb", "dialect", "proven", "fix",
     "SELECT id, amount, CASE WHEN id = (SELECT MIN(id) FROM orders) THEN 0 ELSE "
     "(SELECT o2.amount FROM orders o2 WHERE o2.id < orders.id ORDER BY o2.id DESC LIMIT 1) END AS prev FROM orders"),
    ("lead_default", "each order with the next order's amount (-1 for the last), per customer",
     "SELECT id, customer_id, LEAD(amount, 1, -1) OVER (PARTITION BY customer_id ORDER BY id) AS nxt FROM orders",
     "mariadb", "dialect", "proven", "fix",
     "SELECT o.id, o.customer_id, COALESCE((SELECT o2.amount FROM orders o2 WHERE o2.customer_id = o.customer_id AND o2.id > o.id "
     "ORDER BY o2.id LIMIT 1), -1) AS nxt FROM orders o"),
    ("lag_named_window", "LAG with a default over a named window",
     "SELECT id, LAG(amount, 1, 0) OVER w AS prev FROM orders WHERE customer_id = 1 WINDOW w AS (ORDER BY id)",
     "mariadb", "dialect", "proven", "fix",
     "SELECT id, CASE WHEN id = 1 THEN 0 ELSE 25500 END AS prev FROM orders WHERE customer_id = 1"),
    # ---- MariaDB: WITH in front of UPDATE / DELETE / INSERT (1064): the WITH goes inside
    ("cte_delete", "delete the cancelled orders (named in a CTE)",
     "WITH gone AS (SELECT id FROM orders WHERE status = 'cancelled') DELETE FROM orders WHERE id IN (SELECT id FROM gone)",
     "mariadb", "dialect", "proven", "change", "orders"),
    ("cte_update", "hold the pending orders (named in a CTE)",
     "WITH p AS (SELECT id FROM orders WHERE status = 'pending') UPDATE orders SET status = 'held' WHERE id IN (SELECT id FROM p)",
     "mariadb", "dialect", "proven", "change", "orders"),
    ("cte_insert", "copy the paid orders' customers into returns as a test row (named in a CTE)",
     "WITH x AS (SELECT 50 + id AS id FROM orders WHERE status = 'paid') "
     "INSERT INTO returns (id, order_item_id, returned_on, reason, quantity) SELECT id, 1, '2024-08-01', 'audit', 1 FROM x",
     "mariadb", "dialect", "proven", "change", "returns"),
    ("cte_two_update", "two CTEs feeding one UPDATE",
     "WITH big AS (SELECT id, customer_id FROM orders WHERE amount > 20000), late AS (SELECT id FROM big WHERE customer_id > 1) "
     "UPDATE orders SET status = 'review' WHERE id IN (SELECT id FROM late)",
     "mariadb", "dialect", "proven", "change", "orders"),
    ("cte_join_update", "a CTE joined in the UPDATE itself: said, not rewritten",
     "WITH p AS (SELECT id FROM orders WHERE status = 'pending') UPDATE orders o JOIN p ON p.id = o.id SET o.status = 'held'",
     "mariadb", "dialect", "proven", "found", None),
    # ---- a CTE nothing reads
    ("unused_one", "paid orders per customer (a supplier CTE left behind)",
     "WITH s AS (SELECT supplier_id, COUNT(*) AS n FROM products GROUP BY supplier_id), p AS (SELECT customer_id, amount FROM orders "
     "WHERE status = 'paid') SELECT customer_id, SUM(amount) AS s FROM p GROUP BY customer_id", "postgresql", "cte_unused",
     "likely_risk", "found", None),
    ("unused_chain", "a CTE read only by another unused CTE",
     "WITH a AS (SELECT id FROM orders), b AS (SELECT id FROM a WHERE id > 2), c AS (SELECT id FROM customers) "
     "SELECT COUNT(*) AS n FROM c", "mariadb", "cte_unused", "likely_risk", "found", None),
    # ---- the mistakes inside one block of a long query, located in their block
    ("located_null", "orders without a discount, ranked by amount",
     "WITH base AS (\n  SELECT id, customer_id, amount\n  FROM orders\n  WHERE discount = NULL\n),\nr AS (\n  SELECT id, amount, "
     "RANK() OVER (ORDER BY amount DESC) AS rk\n  FROM base\n)\nSELECT id, amount, rk FROM r", "postgresql", "null", "proven", "fix",
     "SELECT id, amount, RANK() OVER (ORDER BY amount DESC) AS rk FROM orders WHERE discount IS NULL"),
    ("located_between", "orders between 100 and 1000 with their customer",
     "WITH base AS (SELECT id, customer_id, amount FROM orders WHERE amount BETWEEN 1000 AND 100)\n"
     "SELECT b.id, c.first_name FROM base b JOIN customers c ON c.id = b.customer_id", "mariadb", "between", "proven", "fix",
     "SELECT o.id, c.first_name FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.amount BETWEEN 100 AND 1000"),
    ("located_count_case", "paid orders per customer",
     "WITH t AS (SELECT customer_id, COUNT(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid FROM orders GROUP BY customer_id)\n"
     "SELECT customer_id, paid FROM t", "postgresql", "count_case", "proven", "fix",
     "SELECT customer_id, SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid FROM orders GROUP BY customer_id"),
    # ---- DISTINCT over a join with a renamed column: the same risk as without the new name
    ("distinct_renamed", "customers who ordered",
     "SELECT DISTINCT c.id AS customer_id, c.city FROM customers c JOIN orders o ON o.customer_id = c.id", "mariadb",
     "distinct_join", "likely_risk", "fix",
     "SELECT c.id AS customer_id, c.city FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)"),
    ("distinct_renamed_pg", "cities of customers who ordered",
     "SELECT DISTINCT c.city AS town FROM customers c JOIN orders o ON o.customer_id = c.id", "postgresql",
     "distinct_join", "likely_risk", "fix",
     "SELECT DISTINCT c.city AS town FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)"),
    # ---- correct queries that used to get a false 'check'
    ("q_block_cols", "a block plainly makes both columns: no 'two columns, or one?'",
     "WITH f AS (SELECT id, amount, COALESCE(discount, 0) AS discount_amount FROM orders) SELECT SUM(f.discount_amount) AS d, "
     "SUM(f.amount) AS a FROM f", "postgresql", "column", None, "quiet",
     "SELECT SUM(COALESCE(discount, 0)) AS d, SUM(amount) AS a FROM orders"),
    ("q_block_cols2", "units and returned units from one block",
     "WITH f AS (SELECT o.id, COUNT(i.id) AS units, COUNT(i.id) - 1 AS returned_units FROM orders o JOIN order_items i "
     "ON i.order_id = o.id GROUP BY o.id) SELECT SUM(f.units) AS u, SUM(f.returned_units) AS r FROM f", "mariadb", "column", None,
     "quiet", "SELECT COUNT(*) AS u, COUNT(*) - COUNT(DISTINCT order_id) AS r FROM order_items"),
    ("q_rank_block_key", "top category per customer: the block's GROUP BY makes the order total",
     "WITH cc AS (SELECT o.customer_id, p.category_id, SUM(i.quantity) AS units FROM orders o JOIN order_items i ON i.order_id = o.id "
     "JOIN products p ON p.id = i.product_id WHERE p.category_id IS NOT NULL GROUP BY o.customer_id, p.category_id), "
     "r AS (SELECT customer_id, category_id, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY units DESC, category_id) AS rn "
     "FROM cc) SELECT customer_id, category_id FROM r WHERE rn = 1", "postgresql", "win_rank_ties", None, "quiet", None),
    ("q_rank_month_key", "latest month per customer: grouped by an expression, still a key",
     "WITH m AS (SELECT customer_id, EXTRACT(YEAR FROM order_date) * 100 + EXTRACT(MONTH FROM order_date) AS ym, SUM(amount) AS s "
     "FROM orders GROUP BY customer_id, EXTRACT(YEAR FROM order_date) * 100 + EXTRACT(MONTH FROM order_date)), "
     "r AS (SELECT customer_id, ym, s, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY ym DESC) AS rn FROM m) "
     "SELECT customer_id, ym, s FROM r WHERE rn = 1", "mariadb", "win_rank_ties", None, "quiet",
     "SELECT o.customer_id, EXTRACT(YEAR FROM o.order_date) * 100 + EXTRACT(MONTH FROM o.order_date) AS ym, SUM(o.amount) AS s "
     "FROM orders o WHERE EXTRACT(YEAR FROM o.order_date) * 100 + EXTRACT(MONTH FROM o.order_date) = (SELECT "
     "MAX(EXTRACT(YEAR FROM o2.order_date) * 100 + EXTRACT(MONTH FROM o2.order_date)) FROM orders o2 WHERE o2.customer_id = "
     "o.customer_id) GROUP BY o.customer_id, EXTRACT(YEAR FROM o.order_date) * 100 + EXTRACT(MONTH FROM o.order_date)"),
    ("q_cte_ids", "a CTE's id is the table's own id: no 'ids compared' check",
     "WITH x AS (SELECT id FROM orders WHERE status = 'paid') SELECT o.id, o.amount FROM orders o WHERE o.id IN (SELECT id FROM x)",
     "postgresql", "sub_key", None, "quiet", "SELECT id, amount FROM orders WHERE status = 'paid'"),
    ("q_lag_pg", "LAG with a default on PostgreSQL is fine",
     "SELECT id, LAG(amount, 1, 0) OVER (ORDER BY id) AS prev FROM orders", "postgresql", "dialect", None, "quiet",
     "SELECT id, COALESCE(LAG(amount, 1) OVER (ORDER BY id), 0) AS prev FROM orders"),
    ("q_cte_delete_pg", "WITH ... DELETE on PostgreSQL is fine",
     "WITH gone AS (SELECT id FROM orders WHERE status = 'cancelled') DELETE FROM orders WHERE id IN (SELECT id FROM gone)",
     "postgresql", "dialect", None, "quiet", None),
    ("q_all_used", "every CTE read (one only through another): nothing unused",
     "WITH a AS (SELECT id, customer_id FROM orders), b AS (SELECT customer_id FROM a WHERE id > 2) "
     "SELECT COUNT(DISTINCT customer_id) AS n FROM b", "mariadb", "cte_unused", None, "quiet",
     "SELECT COUNT(DISTINCT customer_id) AS n FROM orders WHERE id > 2"),
    ("q_scalar_in_part", "a total inside a scalar subquery doesn't make the outer part a total",
     "SELECT c.id, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) AS n FROM customers c UNION ALL "
     "SELECT NULL, COUNT(*) FROM orders", "postgresql", "group", None, "quiet",
     "SELECT c.id, COUNT(o.id) AS n FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id UNION ALL "
     "SELECT NULL, COUNT(*) FROM orders"),
]


ENGINE_ONLY = {"q_lag_pg", "q_cte_delete_pg"}


@pytest.fixture(scope="module")
def engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


def _kind(name):
    return "mariadb" if name == "mysql" else "postgresql"


def _all(res):
    return bigq._all(res)


def _state(eng, table):
    return S.rows(eng, f"SELECT * FROM {table}")


def test_the_bank_has_enough_problems():
    assert len(P) + 2 * len(bigq.ORDER) >= 30 and len(P) >= 25 and len({p[0] for p in P}) == len(P)
    assert {p[6] for p in P} >= {"fix", "change", "found", "quiet"}


@pytest.mark.parametrize("pid,words,q,engine,topic,cls,proof,truth", P, ids=[p[0] for p in P])
def test_tonights_problems(engines, pid, words, q, engine, topic, cls, proof, truth):
    res = analyze_sql(f"{q};\n\n{words}", engine=engine)
    fs = _all(res)
    if proof == "quiet":
        assert not [f for f in fs if f.get("kind") in ("error", "wrong") or f.get("topic") == topic], \
            (pid, [(f.get("topic"), f["title_en"]) for f in fs])
        if truth:
            for name, eng in engines.items():
                if pid in ENGINE_ONLY and _kind(name) != engine:
                    continue                                 # its own engine's words: the other one refuses them (the LAB)
                assert S.rows(eng, q) == S.rows(eng, truth), (pid, name)
        elif q.lstrip().upper().startswith(("SELECT", "WITH")) and "DELETE" not in q.upper():
            assert S.rows(engines["mysql"], q) == S.rows(engines["postgres"], q), pid                  # it runs, and both databases agree
        return
    found = [f for f in fs if f.get("topic") == topic]
    assert found, (pid, [(f.get("topic"), f["title_en"]) for f in fs])
    assert any(f["confidence"]["class"] == cls for f in found), (pid, [f["confidence"]["class"] for f in found])
    fix = (res["correct"].get("corrected") or res.get("suggested") or "").rstrip().rstrip(";")
    if proof == "found":
        return
    assert fix, pid
    if proof == "fix":
        for name, eng in engines.items():
            assert S.rows(eng, fix) == S.rows(eng, truth), (pid, name, fix)
    elif proof == "change":                                 # the fix on MariaDB changes what the original changes on PostgreSQL
        my, pg = engines["mysql"], engines["postgres"]
        S.build(my)
        S.build(pg)
        try:
            my.run(fix)
            pg.run(q)
            assert _state(my, truth) == _state(pg, truth), (pid, fix)
        finally:
            S.build(my)
            S.build(pg)


@pytest.mark.parametrize("pid", ["cte_delete", "cte_update", "cte_insert", "lag_default"])
def test_the_refused_form_really_is_refused_on_mariadb_and_the_fix_is_not(engines, pid):
    p = next(x for x in P if x[0] == pid)
    my = engines["mysql"]
    S.build(my)
    with pytest.raises(Exception) as exc:
        my.run(p[2])
    my.rollback()
    assert "1064" in str(exc.value)
    fix = analyze_sql(p[2], engine="mariadb")["correct"]["corrected"]
    my.run(fix)
    S.build(my)


# ---------------------------------------------------------------- the generated exam (two seeds per family)
EXAM = [(fam, seed) for k, fam in enumerate(bigq.ORDER) for seed in (1500 + k, 1500 + k + len(bigq.ORDER))]


@pytest.mark.parametrize("fam,seed", EXAM, ids=[f"{f}-{s}" for f, s in EXAM])
def test_the_big_query_exam(engines, fam, seed):
    case = bigq.make(seed, fam)
    assert 100 <= case["lines"] <= 300
    problems = bigq.exam(case, engines) + bigq.upgrade(case, engines)
    assert not problems, problems


def test_the_generator_covers_every_class_and_shape():
    cases = [bigq.make(s) for s in range(60)]
    assert {c["cls"] for c in cases} == {"proven", "likely_risk", "needs_schema", "needs_execution"}
    assert {c["proof"] for c in cases} >= {"fix", "offer", "same", "found", "none"}
    assert min(c["lines"] for c in cases) >= 100 and max(c["lines"] for c in cases) <= 300
    big = max(cases, key=lambda c: c["lines"])["planted"].upper()
    assert big.count(" OVER ") + big.count(" OVER(") >= 2 and "UNION ALL" in big and re.search(r"^WITH ", big, re.M)


def test_a_long_query_gives_the_tree_grain_and_located_mistake():
    case = bigq.make(1500, "null_eq")
    res = analyze_sql(case["planted"], engine=case["engine"])
    assert "Blocks, how they depend on each other" in res["reading"]["en"]
    bl = {b["id"]: b for b in res["blocks"]["blocks"]}
    assert set(case["ctes"]) <= set(bl)
    assert all(b["input"] and b["output"] for b in bl.values())
    f = next(f for f in res["correct"]["findings"] if f["topic"] == "null")
    assert f["block"] == case["block"] and f["evidence"].startswith(f"[CTE {case['block']}]")


def test_an_unused_cte_is_in_the_tree():
    q = ("WITH a AS (SELECT id FROM orders), spare AS (SELECT supplier_id, COUNT(*) AS n FROM products GROUP BY supplier_id) "
         "SELECT COUNT(*) FROM a")
    assert sqlblocks.unused_ctes(q) == ["spare"]
    assert "CTE spare: nothing reads it" in sqlblocks.tree(q)
    assert sqlblocks.unused_ctes("WITH d AS (DELETE FROM orders WHERE id = 0 RETURNING id) SELECT 1") == []


def test_a_scalar_subquery_total_is_not_the_parts_grain():
    q = "SELECT c.id, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) AS n FROM customers c UNION ALL SELECT NULL, 0"
    part = next(b for b in sqlblocks.grain(q)["blocks"] if b["id"] == "branch main #1")
    assert part["output"] == "one row per customers row"


# ---------------------------------------------------------------- the plan decides (EXPLAIN upgrade)
def test_explain_confirms_a_full_read(engines):
    q = "SELECT c.id FROM customers c WHERE c.email LIKE '%.test' AND c.city = 'Pune'"
    for name, eng in engines.items():
        res = analyze_sql(q, bigq.explain_text(eng, q), engine=_kind(name))
        f = next(f for f in res["findings"] if f.get("topic") == "leading_wildcard")
        assert f["confidence"]["verified"] and f["confidence"]["plan"] == "confirmed" and f["confidence"]["class"] == "proven", name
        assert "pasted EXPLAIN shows it" in f["confidence"]["why_en"]


def test_explain_rules_out_a_risk_the_plan_avoids(engines):
    q = "SELECT o.id FROM orders o WHERE EXTRACT(YEAR FROM o.order_date) = 2024 AND o.id = 3"
    for name, eng in engines.items():
        res = analyze_sql(q, bigq.explain_text(eng, q), engine=_kind(name))
        assert not [f for f in _all(res) if f.get("topic") == "sargable"], name
        ruled = [d for d in res["ruled_out"] if d["by"] == "plan" and d["topic"] == "sargable"]
        assert ruled and "through an index" in ruled[0]["why_en"], name
    from app.agents.core import ruled_out_text

    assert "Your EXPLAIN plan rules these out" in ruled_out_text(res, False)


def test_without_a_plan_the_risk_stays_a_risk():
    res = analyze_sql("SELECT c.id FROM customers c WHERE c.email LIKE '%.test'", engine="postgresql")
    f = next(f for f in res["findings"] if f.get("topic") == "leading_wildcard")
    assert f["confidence"]["class"] == "needs_execution" and not f["confidence"]["verified"]


def test_with_tables_not_in_is_verified_or_dropped():
    case = bigq.make(1511, "not_in")
    assert not bigq.upgrade(case)
    with_null = analyze_sql(bigq.ddl() + "\n" + case["planted"], engine=case["engine"])
    assert any(f["topic"] == "not_in" and f["confidence"]["verified"] for f in _all(with_null) if f.get("kind"))
    not_null = analyze_sql(bigq.ddl(("orders.customer_id",)) + "\n" + case["planted"], engine=case["engine"])
    assert not [f for f in _all(not_null) if f.get("topic") == "not_in" and f.get("kind")]


def test_confidence_module_knows_the_plan_words():
    for w in ("ALL", "Seq Scan", "index", "Parallel Seq Scan"):
        assert sqlconfidence.FULL.match(w)
    for w in ("ref", "range", "eq_ref", "const", "Index Scan", "Index Only Scan", "Bitmap Heap Scan"):
        assert not sqlconfidence.FULL.match(w)


# ---------------------------------------------------------------- the lab
def _code(exc) -> str:
    a = getattr(exc, "args", ())
    if a and isinstance(a[0], int):
        return str(a[0])
    return getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None) or "err"


def _one(eng, sql):
    try:
        rows = eng.run(sql)
        if not isinstance(rows, (list, tuple)):
            return "ok"                                      # a change: the row count, not rows
        return str(S.norm(rows[0][0])) if rows and rows[0] else "ok"
    except Exception as exc:  # noqa: BLE001
        eng.rollback()
        return _code(exc)
    finally:
        S.build(eng)


LAB = {   # what each engine does (MariaDB 10.11, PostgreSQL 16)
    "lag_default": ("SELECT LAG(amount, 1, 0) OVER (ORDER BY id) FROM orders ORDER BY id LIMIT 1", "1064", "0"),
    "lag_coalesce": ("SELECT COALESCE(LAG(amount, 1) OVER (ORDER BY id), 0) FROM orders ORDER BY id LIMIT 1", "0", "0"),
    "cte_delete": ("WITH g AS (SELECT id FROM orders WHERE status = 'cancelled') DELETE FROM orders WHERE id IN (SELECT id FROM g)",
                   "1064", "ok"),
    "cte_inside_delete": ("DELETE FROM orders WHERE id IN (WITH g AS (SELECT id FROM orders WHERE status = 'cancelled') SELECT id FROM g)",
                          "ok", "ok"),
    "cte_insert": ("WITH x AS (SELECT 9 AS id) INSERT INTO returns (id, order_item_id, returned_on, reason, quantity) "
                   "SELECT id, 1, '2024-01-01', 'x', 1 FROM x", "1064", "ok"),
    "insert_with": ("INSERT INTO returns (id, order_item_id, returned_on, reason, quantity) WITH x AS (SELECT 9 AS id) "
                    "SELECT id, 1, '2024-01-01', 'x', 1 FROM x", "ok", "ok"),
    "agg_in_where": ("SELECT customer_id FROM orders WHERE SUM(amount) > 0 GROUP BY customer_id", "1111", "42803"),
    "unused_cte_missing_table": ("WITH a AS (SELECT id FROM orders), b AS (SELECT id FROM no_such_table) SELECT COUNT(*) FROM a",
                                 "1146", "42P01"),
    "unused_cte_not_run": ("WITH a AS (SELECT id FROM orders), b AS (SELECT 1 / (id - id) AS x FROM orders) SELECT COUNT(*) FROM a",
                           "8", "8"),
    "union_types": ("SELECT name FROM products UNION ALL SELECT id FROM products", "Phone X", "42804"),
}


def test_lab(engines):
    for k, (sql, my, pg) in LAB.items():
        assert _one(engines["mysql"], sql) == my, (k, "mariadb")
        assert _one(engines["postgres"], sql) == pg, (k, "postgresql")


def test_the_facts_live_in_the_engine_file():
    from app.agents.sqlport import dialect

    facts = dialect("mariadb")["syntax_refused"]
    assert {"lag_default", "cte_before_change"} <= set(facts) and all(facts[k]["proven"] == ["mariadb"] for k in ("lag_default", "cte_before_change"))
    assert "syntax_refused" not in dialect("mysql") and "syntax_refused" not in dialect("postgresql")
