"""Night 14 practice bank: safe rewrites with proof of equivalence (pre-aggregating correlated totals, computing a repeated
expression once, IN <-> EXISTS, NOT IN <-> NOT EXISTS with the NULL cases written out, IN as a JOIN to distinct values, a
derived table as a CTE, OFFSET as keyset paging with mixed directions) and the honest refusals (UNION ALL where the parts can
share a value, RAND() written twice, EXISTS asked for an IN under NOT or in SELECT).

Each problem is a requirement (the words around the query), a query, the database, the finding DBaba must give (with its class)
and how its answer is proven on MariaDB and PostgreSQL (practice schema):
- 'same'   : the rewrite returns exactly the rows of the query as written, in the same order when it has ORDER BY, on both
             databases, and carries its proof (family, verdict 'same' or 'same_if', the five things it keeps).
- 'refuse' : found, and no rewrite that claims the same rows (the swap would change them).
- 'quiet'  : nothing of tonight's is said (no rewrite asked for, nothing repeated).
LAB is what each engine really does with the facts tonight's rules rest on (MariaDB 10.11 / PostgreSQL 16), re-run here.
"""

import json
import re

import pytest

from app.agents import sqlproof
from app.agents.sql import analyze_sql
from tests.practice import schema as S

TONIGHT = {"preagg", "compute_once", "volatile_repeat", "rw_exists", "rw_not_exists", "rw_in", "rw_not_in", "rw_join", "rw_cte",
           "rw_cte_derived", "rw_union_all", "rw_keyset", "rw_once", "rw_nothing"}
N = "(SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id)"
# (id, words, query, engine, topic, class, proof)
P = [
    # ---- several correlated totals over one child table: one grouped pass, the same rows
    ("pre_count_max", "", f"SELECT c.id, {N} AS n, (SELECT MAX(o.amount) FROM orders o WHERE o.customer_id = c.id) AS top "
     "FROM customers c ORDER BY c.id", "mariadb", "preagg", "needs_execution", "same"),
    ("pre_count_two", "", f"SELECT c.id, {N} AS n, (SELECT COUNT(o.discount) FROM orders o WHERE o.customer_id = c.id) AS with_discount "
     "FROM customers c ORDER BY c.id", "postgresql", "preagg", "needs_execution", "same"),
    ("pre_items", "", "SELECT p.id, (SELECT COUNT(*) FROM order_items i WHERE i.product_id = p.id) AS line_count, "
     "(SELECT MAX(i.quantity) FROM order_items i WHERE i.product_id = p.id) AS most FROM products p ORDER BY p.id", "mariadb", "preagg",
     "needs_execution", "same"),
    ("pre_dept_three", "", "SELECT d.id, (SELECT COUNT(*) FROM employees e WHERE e.department_id = d.id) AS people, "
     "(SELECT MAX(e.salary) FROM employees e WHERE e.department_id = d.id) AS top, (SELECT MIN(e.salary) FROM employees e "
     "WHERE e.department_id = d.id) AS low FROM departments d ORDER BY d.id", "postgresql", "preagg", "needs_execution", "same"),
    ("pre_payments", "", "SELECT o.id, (SELECT COUNT(*) FROM payments p WHERE p.order_id = o.id) AS payments, "
     "(SELECT MAX(p.paid_at) FROM payments p WHERE p.order_id = o.id) AS last_paid FROM orders o ORDER BY o.id", "mariadb", "preagg",
     "needs_execution", "same"),
    ("pre_own_filter", "", f"SELECT c.id, {N} AS n, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id AND o.status = 'paid') "
     "AS paid FROM customers c ORDER BY c.id", "postgresql", "preagg", "needs_execution", "same"),
    ("pre_common_filter", "", "SELECT c.id, (SELECT COUNT(*) FROM orders o WHERE o.status <> 'cancelled' AND o.customer_id = c.id) AS n, "
     "(SELECT MIN(o.amount) FROM orders o WHERE o.customer_id = c.id AND o.status <> 'cancelled') AS low FROM customers c ORDER BY c.id",
     "mariadb", "preagg", "needs_execution", "same"),
    ("pre_cte_asked", "CTE mein likho", f"SELECT c.id, {N} AS n FROM customers c ORDER BY c.id", "postgresql", "rw_cte",
     "needs_execution", "same"),
    ("pre_join_asked", "join se likho, bina subquery", f"SELECT c.id, c.city, {N} AS n FROM customers c WHERE c.id <= 6 ORDER BY c.id",
     "mariadb", "preagg", "needs_execution", "same"),
    # ---- the same calculation in SELECT and WHERE / ORDER BY: named once
    ("once_net", "", "SELECT id, amount - COALESCE(discount, 0) AS net FROM orders WHERE amount - COALESCE(discount, 0) > 600 "
     "ORDER BY amount - COALESCE(discount, 0) DESC, id", "postgresql", "compute_once", "needs_execution", "same"),
    ("once_pay", "", "SELECT id, salary + COALESCE(commission, 0) AS pay FROM employees WHERE salary + COALESCE(commission, 0) >= 60000 "
     "AND department_id IS NOT NULL ORDER BY id", "mariadb", "compute_once", "needs_execution", "same"),
    ("once_order_asked", "compute it once", "SELECT id, salary * 12 AS yearly FROM employees ORDER BY salary * 12 DESC, id", "postgresql",
     "rw_once", "needs_execution", "same"),
    ("once_stock_value", "", "SELECT id, name, price * stock AS stock_value FROM products WHERE price * stock > 10000 AND stock > 0 "
     "ORDER BY id", "mariadb", "compute_once", "needs_execution", "same"),
    ("once_limit", "", "SELECT id, amount + COALESCE(shipping, 0) AS gross FROM orders WHERE amount + COALESCE(shipping, 0) < 30000 "
     "ORDER BY gross DESC, id LIMIT 3", "postgresql", "compute_once", "needs_execution", "same"),
    # ---- a volatile expression written twice: the copies differ; computing once changes the rows
    ("rand_twice_maria", "", "SELECT id, FLOOR(RAND() * 10) AS r FROM orders WHERE FLOOR(RAND() * 10) > 4", "mariadb", "volatile_repeat",
     "likely_risk", "refuse"),
    ("random_twice_pg", "", "SELECT id, random() AS r FROM orders WHERE random() < 0.5", "postgresql", "volatile_repeat", "likely_risk",
     "refuse"),
    # ---- IN -> EXISTS (asked)
    ("in_exists_paid", "rewrite this with EXISTS", "SELECT c.id FROM customers c WHERE c.id IN (SELECT o.customer_id FROM orders o "
     "WHERE o.status = 'paid') ORDER BY c.id", "mariadb", "rw_exists", "needs_execution", "same"),
    ("in_exists_products", "IN ki jagah EXISTS use karo", "SELECT p.id, p.name FROM products p WHERE p.id IN (SELECT i.product_id FROM "
     "order_items i) AND p.price > 400 ORDER BY p.id", "postgresql", "rw_exists", "needs_execution", "same"),
    ("in_exists_bare", "EXISTS से लिखो", "SELECT id FROM suppliers WHERE id IN (SELECT supplier_id FROM products) ORDER BY id", "mariadb",
     "rw_exists", "needs_execution", "same"),
    # ---- NOT IN -> NOT EXISTS (asked): NOT IN's own rows kept, NULL cases written out
    ("not_in_null_list", "use NOT EXISTS instead", "SELECT c.id FROM categories c WHERE c.id NOT IN (SELECT p.category_id FROM products p) "
     "ORDER BY c.id", "postgresql", "rw_not_exists", "needs_schema", "same"),
    ("not_in_null_outer", "NOT IN ki jagah NOT EXISTS likho", "SELECT e.id FROM employees e WHERE e.department_id NOT IN (SELECT d.id FROM "
     "departments d WHERE d.location = 'Delhi') ORDER BY e.id", "mariadb", "rw_not_exists", "needs_schema", "same"),
    ("not_in_clean", "rewrite it with not exists", "SELECT c.id FROM customers c WHERE c.id NOT IN (SELECT o.customer_id FROM orders o) "
     "ORDER BY c.id", "postgresql", "rw_not_exists", "needs_schema", "same"),
    # ---- EXISTS -> IN (asked)
    ("exists_in_big", "use IN instead of EXISTS", "SELECT c.id FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id "
     "AND o.amount > 1000) ORDER BY c.id", "mariadb", "rw_in", "needs_execution", "same"),
    ("exists_in_reversed", "EXISTS ki jagah IN use karo", "SELECT e.id FROM employees e WHERE EXISTS (SELECT 1 FROM departments d WHERE "
     "e.department_id = d.id AND d.location = 'Pune') ORDER BY e.id", "postgresql", "rw_in", "needs_execution", "same"),
    # ---- NOT EXISTS -> NOT IN (asked): NOT EXISTS's rows kept
    ("not_exists_in_outer", "NOT IN se likho", "SELECT e.id FROM employees e WHERE NOT EXISTS (SELECT 1 FROM departments d WHERE "
     "d.id = e.department_id AND d.location = 'Delhi') ORDER BY e.id", "postgresql", "rw_not_in", "needs_schema", "same"),
    ("not_exists_in_list", "use NOT IN instead of NOT EXISTS", "SELECT c.id FROM categories c WHERE NOT EXISTS (SELECT 1 FROM products p "
     "WHERE p.category_id = c.id) ORDER BY c.id", "mariadb", "rw_not_in", "needs_schema", "same"),
    # ---- IN -> JOIN (asked): joined to the distinct values
    ("in_join_orders", "use a JOIN instead of IN", "SELECT c.id FROM customers c WHERE c.id IN (SELECT o.customer_id FROM orders o) "
     "ORDER BY c.id", "postgresql", "rw_join", "needs_execution", "same"),
    ("in_join_items", "JOIN से लिखो", "SELECT p.id FROM products p WHERE p.id IN (SELECT i.product_id FROM order_items i WHERE i.quantity >= 1) "
     "AND p.stock > 0 ORDER BY p.id", "mariadb", "rw_join", "needs_execution", "same"),
    # ---- OFFSET -> keyset (asked)
    ("keyset_mixed", "use keyset pagination", "SELECT id, price FROM products ORDER BY price DESC, id ASC LIMIT 2 OFFSET 2", "postgresql",
     "rw_keyset", "needs_schema", "same"),
    ("keyset_one", "offset ke bina page karo, keyset se", "SELECT id, amount FROM orders ORDER BY id LIMIT 3 OFFSET 3", "mariadb",
     "rw_keyset", "needs_schema", "same"),
    ("keyset_two_asc", "rewrite it without OFFSET", "SELECT id, salary FROM employees ORDER BY salary, id LIMIT 2 OFFSET 2", "postgresql",
     "rw_keyset", "needs_schema", "same"),
    # ---- a derived table as a CTE (asked)
    ("cte_totals", "write it as a CTE", "SELECT x.customer_id, x.total FROM (SELECT customer_id, SUM(amount) AS total FROM orders "
     "GROUP BY customer_id) x WHERE x.total > 1000 ORDER BY x.customer_id", "mariadb", "rw_cte_derived", "needs_execution", "same"),
    ("cte_counts", "use a WITH clause instead", "SELECT d.k, d.n FROM (SELECT department_id AS k, COUNT(*) AS n FROM employees "
     "GROUP BY department_id) d WHERE d.n > 1 ORDER BY d.k", "postgresql", "rw_cte_derived", "needs_execution", "same"),
    # ---- honest refusals
    ("union_all_refused", "can i use UNION ALL here", "SELECT customer_id FROM orders UNION SELECT id FROM customers", "mariadb",
     "rw_union_all", "needs_schema", "refuse"),
    ("exists_under_not", "rewrite this with EXISTS", "SELECT c.id FROM categories c WHERE NOT (c.id IN (SELECT p.category_id FROM "
     "products p)) ORDER BY c.id", "postgresql", "rw_nothing", "likely_risk", "refuse"),
    ("exists_in_select", "use EXISTS instead of IN", "SELECT c.id, c.id IN (SELECT p.category_id FROM products p) AS used FROM categories c "
     "ORDER BY c.id", "mariadb", "rw_nothing", "likely_risk", "refuse"),
    # ---- nothing to say
    ("q_one_total", "", f"SELECT c.id, {N} AS n FROM customers c ORDER BY c.id", "postgresql", None, None, "quiet"),
    ("q_select_only", "", "SELECT id, amount - COALESCE(discount, 0) AS net FROM orders ORDER BY id", "mariadb", None, None, "quiet"),
    ("q_in_plain", "", "SELECT c.id FROM customers c WHERE c.id IN (SELECT o.customer_id FROM orders o) ORDER BY c.id", "postgresql", None,
     None, "quiet"),
]

LAB = {
    "in_rows": ("5", "5"),                          # IN and EXISTS: the same customers
    "exists_rows": ("5", "5"),
    "plain_join_rows": ("8", "8"),                  # a JOIN repeats a customer per order
    "join_distinct_rows": ("5", "5"),               # ... unless the joined side is DISTINCT
    "not_in_null_list": ("0", "0"),                 # one NULL in the list: NOT IN keeps nothing
    "not_exists_null_list": ("1", "1"),             # NOT EXISTS keeps category 1
    "not_in_exact": ("0", "0"),                     # the exact NOT EXISTS form keeps NOT IN's rows
    "not_in_null_outer": ("4", "4"),                # a NULL outer value: NOT IN drops it ...
    "not_exists_null_outer": ("5", "5"),            # ... NOT EXISTS keeps it
    "not_exists_as_in": ("5", "5"),                 # NOT IN over non-NULL values OR x IS NULL = NOT EXISTS
    "in_select_value": ("NULL", "NULL"),            # as a SELECT value IN is unknown ...
    "exists_select_value": ("0", "0"),              # ... where EXISTS is false
    "count_left_join": ("NULL", "NULL"),            # COUNT from a grouped LEFT JOIN with no match
    "count_coalesce": ("0", "0"),
    "volatile_once": ("0", "0"),                    # one value per row in a derived table
    "nulls_first_asc": ("1", "2"),                  # NULLs first on MariaDB, last on PostgreSQL
    "row_compare_nulls": ("3", "3"),                # (discount, id) > (0, 0) skips the NULL rows
    "keyset_mixed_page": ("4,5,2", "4,5,2"),        # the page after (25500, 1) = OFFSET 3
    "order_alias_expr": ("8", "42703"),             # an alias inside an ORDER BY expression: MariaDB yes, PostgreSQL no
}
LAB_SQL = {
    "in_rows": "SELECT COUNT(*) FROM customers WHERE id IN (SELECT customer_id FROM orders)",
    "exists_rows": "SELECT COUNT(*) FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)",
    "plain_join_rows": "SELECT COUNT(*) FROM customers c JOIN orders o ON o.customer_id = c.id",
    "join_distinct_rows": "SELECT COUNT(*) FROM customers c JOIN (SELECT DISTINCT customer_id FROM orders) s ON s.customer_id = c.id",
    "not_in_null_list": "SELECT COUNT(*) FROM categories WHERE id NOT IN (SELECT category_id FROM products)",
    "not_exists_null_list": "SELECT COUNT(*) FROM categories c WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.category_id = c.id)",
    "not_in_exact": "SELECT COUNT(*) FROM categories c WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.category_id = c.id) AND NOT EXISTS "
                    "(SELECT 1 FROM products p WHERE p.category_id IS NULL) AND (c.id IS NOT NULL OR NOT EXISTS (SELECT 1 FROM products p))",
    "not_in_null_outer": "SELECT COUNT(*) FROM employees WHERE department_id NOT IN (SELECT id FROM departments WHERE location = 'Delhi')",
    "not_exists_null_outer": "SELECT COUNT(*) FROM employees e WHERE NOT EXISTS (SELECT 1 FROM departments d WHERE d.location = 'Delhi' "
                             "AND d.id = e.department_id)",
    "not_exists_as_in": "SELECT COUNT(*) FROM employees e WHERE (e.department_id NOT IN (SELECT d.id FROM departments d WHERE "
                        "d.location = 'Delhi' AND d.id IS NOT NULL) OR e.department_id IS NULL)",
    "in_select_value": "SELECT c.id IN (SELECT category_id FROM products) FROM categories c WHERE c.id = 1",
    "exists_select_value": "SELECT EXISTS (SELECT 1 FROM products p WHERE p.category_id = c.id) FROM categories c WHERE c.id = 1",
    "count_left_join": "SELECT t.n FROM customers c LEFT JOIN (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id) t "
                       "ON t.customer_id = c.id WHERE c.id = 4",
    "count_coalesce": "SELECT COALESCE(t.n, 0) FROM customers c LEFT JOIN (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY "
                      "customer_id) t ON t.customer_id = c.id WHERE c.id = 4",
    "volatile_once": {"mysql": "SELECT COUNT(*) FROM (SELECT RAND() AS r FROM orders o, customers c) x WHERE r < 0.5 AND r >= 0.5",
                      "postgres": "SELECT COUNT(*) FROM (SELECT random() AS r FROM orders o, customers c) x WHERE r < 0.5 AND r >= 0.5"},
    "nulls_first_asc": "SELECT id FROM orders ORDER BY discount, id LIMIT 1",
    "row_compare_nulls": "SELECT COUNT(*) FROM orders WHERE (discount, id) > (0, 0)",
    "keyset_mixed_page": "SELECT id FROM orders WHERE amount < 25500 OR (amount = 25500 AND id > 1) ORDER BY amount DESC, id LIMIT 3",
    "order_alias_expr": "SELECT COUNT(*) FROM (SELECT id, amount AS a FROM orders ORDER BY a * -1) x",
}


@pytest.fixture(scope="module")
def engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


def _kind(name):
    return "mariadb" if name == "mysql" else "postgresql"


def _text(q, words):
    return f"{q};\n\n{words}" if words else q


def _fill_keyset(eng, q, sql):
    """The keyset query with the last row of the page before put in place of the placeholders (what the app keeps)."""
    m = re.search(r"LIMIT (\d+) OFFSET (\d+)$", q)
    prev = eng.run(q[:m.start()] + f"LIMIT 1 OFFSET {int(m.group(2)) - 1}")[0]
    cols = [c.strip() for c in q[len("SELECT "):q.index(" FROM")].split(",")]
    for c, v in sorted(zip(cols, prev), key=lambda x: -len(x[0])):
        sql = sql.replace(f":last_{c.lower()}", str(v))
    return sql


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)
    assert {p[6] for p in P} >= {"same", "refuse", "quiet"}
    assert {p[4] for p in P if p[4]} >= TONIGHT


@pytest.mark.parametrize("pid,words,q,engine,topic,cls,proof", P, ids=[p[0] for p in P])
def test_dbaba_rewrites_with_proof(engines, pid, words, q, engine, topic, cls, proof):
    res = analyze_sql(_text(q, words), engine=engine)
    fs = res["correct"]["findings"]
    if proof == "quiet":
        said = [(f["topic"], f["title_en"]) for f in fs if f["kind"] in ("error", "wrong") or f["topic"] in TONIGHT]
        assert not said, (pid, said)
        return
    found = [f for f in fs if f["topic"] == topic]
    assert found, (pid, [(f["topic"], f["title_en"]) for f in fs])
    assert any(f["confidence"]["class"] == cls for f in found), (pid, [f["confidence"]["class"] for f in found])
    assert not [f for f in fs if f["kind"] in ("error", "wrong")], pid
    if proof == "refuse":
        sql = res["correct"].get("corrected") or ""
        assert not (topic == "rw_union_all" and re.search(r"union\s+all", sql, re.I)), (pid, sql)
        assert not any(f["topic"] in sqlproof.SAME for f in fs), pid
        assert not [p for p in res.get("proof") or [] if p["topic"] == topic], pid
        return
    pr = found[0]["proof"]
    assert pr["verdict"] in ("same", "same_if") and set(pr["keeps"]) == {"matching", "nulls", "duplicates", "ordering", "grain"}, pid
    assert res.get("suggested_kind") == "speed" and res.get("proof"), (pid, res.get("suggested_kind"))
    ordered = bool(re.search(r"\border\s+by\b(?![^()]*\))", q, re.I))
    for name, eng in engines.items():
        other = res if _kind(name) == engine else analyze_sql(_text(q, words), engine=_kind(name))
        sql = (other["correct"].get("corrected") or "").rstrip().rstrip(";")
        assert sql, (pid, name)
        if topic == "rw_keyset":
            sql = _fill_keyset(eng, q, sql)
        assert S.rows(eng, sql, ordered) == S.rows(eng, q, ordered), (pid, name, sql)


def test_the_plain_not_exists_offered_in_words_is_not_called_the_same(engines):
    """NOT IN over a list with a NULL: the plain NOT EXISTS (named in the answer) gives other rows; the applied form doesn't."""
    q = "SELECT c.id FROM categories c WHERE c.id NOT IN (SELECT p.category_id FROM products p) ORDER BY c.id"
    res = analyze_sql(_text(q, "use NOT EXISTS instead"), engine="postgresql")
    f = next(f for f in res["correct"]["findings"] if f["topic"] == "rw_not_exists")
    plain = re.search(r"the plain form is (NOT EXISTS \(SELECT 1 FROM .*?\)):", f["fix_en"]).group(1)
    for eng in engines.values():
        assert S.rows(eng, q.replace("c.id NOT IN (SELECT p.category_id FROM products p)", plain)) != S.rows(eng, q)


def test_a_union_all_swap_would_change_the_rows(engines):
    q = "SELECT customer_id FROM orders UNION SELECT id FROM customers"
    for eng in engines.values():
        assert len(S.rows(eng, q)) != len(S.rows(eng, q.replace("UNION", "UNION ALL")))


def test_the_lab_table_still_holds(engines):
    def one(eng, key):
        S.build(eng)
        sql = LAB_SQL[key]
        sql = sql[eng.kind] if isinstance(sql, dict) else sql
        try:
            if key == "keyset_mixed_page":
                return ",".join(str(r[0]) for r in eng.run(sql))
            return str(S.norm(eng.run(sql)[0][0]))
        except Exception as exc:  # noqa: BLE001
            eng.rollback()
            a = getattr(exc, "args", ())
            return str(a[0]) if a and isinstance(a[0], int) else (getattr(exc, "sqlstate", None) or "err")
    for key, (maria, pg) in LAB.items():
        got = (one(engines["mysql"], key), one(engines["postgres"], key))
        assert got == (maria, pg), (key, got)


def test_every_rewrite_has_a_contract_and_records_where_it_was_proven():
    C = sqlproof.contracts()
    assert set(C["dimensions"]) == {"matching", "nulls", "duplicates", "ordering", "grain"}
    for fid, f in C["families"].items():
        assert set(f["keeps"]) == set(C["dimensions"]) and f["holds_when"] and f["breaks_when"], fid
        assert f["proven"] == ["mariadb", "postgresql"] and f["source"].startswith(("https://www.postgresql.org/docs/",
                                                                                    "https://dev.mysql.com/", "https://mariadb.com/kb/")), fid
    for t in sqlproof.SAME:
        assert sqlproof.family_of(t), t
    assert all(v == ["mariadb", "postgresql"] for v in sqlproof.PROVEN.values()) and set(sqlproof.PROVEN) >= TONIGHT
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert TONIGHT <= {t for f in funds.values() for t in f["topics"]}
    for eng in ("mysql", "mariadb", "postgresql"):
        rules = json.load(open(f"app/dbknow/dialects/{eng}.json"))["query_rules"]
        for key in ("nulls_sort_order", "row_comparison", "volatile_once", "exists_to_in"):
            assert rules[key]["says"] and rules[key]["proven"], (eng, key)


@pytest.mark.parametrize("words,want", [
    ("rewrite this with EXISTS", ["exists"]), ("use EXISTS instead of IN", ["exists", "in"]), ("IN ki jagah EXISTS use karo", ["exists", "in"]),
    ("convert IN to EXISTS", ["exists", "in"]), ("EXISTS se IN mein badlo", ["in", "exists"]), ("use NOT EXISTS instead", ["not_exists"]),
    ("JOIN से लिखो", ["join"]), ("make it UNION ALL", ["union_all"]), ("use keyset pagination", ["keyset"]), ("CTE mein likho", ["cte"]),
    ("compute it once", ["once"]), ("IN ki jagah JOIN lagao", ["join", "in"]), ("check this query in mysql", []), ("is it correct", []),
    ("show users IN Pune", []), ("show me why this join is slow", []),
])
def test_what_was_asked_for_is_read_from_the_words(words, want):
    got = sqlproof.wanted(words)
    assert (got[:1] == want[:1] and set(got) <= set(want)) if want else got == [], got   # the target first
