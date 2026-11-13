"""Night 12 practice bank: blocks, their dependency tree, and the grain at every stage.

Each problem is a requirement, a query with CTEs, derived tables or UNION parts, the database, the mistake DBaba must find (in
the right block, with its class) and how its answer is proven on MariaDB and PostgreSQL (practice schema):
- 'fix'   : PROVEN. The corrected query returns the requirement's rows (the truth) on both databases.
- 'offer' : a risk. The query offered returns the truth on both databases.
- 'same'  : a speed risk. The faster form returns the same rows.
- 'found' : found, in its block, with its class (the rewrite needs a choice only the person can make).
- 'quiet' : a correct query whose CTE / derived table is unique on the join columns by its own GROUP BY, DISTINCT or single
            total: DBaba must not call the join a multiplier (no error / wrong, no fan-out), and the query returns the truth.
The tree tests read 30+ line queries: every block, what it depends on, its grain in and out, and the joins that move it.
LAB is what each engine really does with tonight's grain facts (MariaDB 10.11 / PostgreSQL 16), re-run here.
"""

import re

import pytest

from app.agents import sqlblocks
from app.agents.sql import analyze_sql
from tests.practice import schema as S

PAID_PER = "SELECT customer_id, SUM(amount) AS s FROM orders WHERE status = 'paid' GROUP BY customer_id ORDER BY customer_id"
# (id, requirement, query, engine, topic, class, block, proof, truth)
P = [
    # ---- a total inside a block that its own join multiplies (fan-out), with the block named
    ("cte_fanout", "total order amount per customer",
     "WITH t AS (SELECT o.customer_id, SUM(o.amount) AS s FROM orders o JOIN order_items i ON i.order_id = o.id GROUP BY o.customer_id) "
     "SELECT customer_id, s FROM t ORDER BY customer_id", "postgresql", "join_fanout", "likely_risk", "t", "offer",
     "SELECT customer_id, SUM(amount) AS s FROM orders GROUP BY customer_id ORDER BY customer_id"),
    ("derived_fanout", "total order amount per customer",
     "SELECT x.customer_id, x.s FROM (SELECT o.customer_id, SUM(o.amount) AS s FROM orders o JOIN order_items i ON i.order_id = o.id "
     "GROUP BY o.customer_id) x ORDER BY x.customer_id", "mariadb", "join_fanout", "likely_risk", "derived x", "offer",
     "SELECT customer_id, SUM(amount) AS s FROM orders GROUP BY customer_id ORDER BY customer_id"),
    ("cte_fanout_where", "paid total per customer",
     "WITH t AS (SELECT o.customer_id, SUM(o.amount) AS s FROM orders o JOIN order_items i ON i.order_id = o.id WHERE o.status = 'paid' "
     "GROUP BY o.customer_id) SELECT customer_id, s FROM t ORDER BY customer_id", "postgresql", "join_fanout", "likely_risk", "t", "offer",
     PAID_PER),
    ("cte_fanout_count", "orders per customer",
     "WITH t AS (SELECT o.customer_id, COUNT(o.id) AS n FROM orders o JOIN order_items i ON i.order_id = o.id GROUP BY o.customer_id) "
     "SELECT customer_id, n FROM t ORDER BY customer_id", "mariadb", "join_fanout", "likely_risk", "t", "offer",
     "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id ORDER BY customer_id"),
    ("cte_fanout_left", "each order's amount",
     "WITH t AS (SELECT o.id, SUM(o.amount) AS s FROM orders o LEFT JOIN order_items i ON i.order_id = o.id GROUP BY o.id) "
     "SELECT id, s FROM t ORDER BY id", "postgresql", "join_fanout", "likely_risk", "t", "offer", "SELECT id, amount AS s FROM orders ORDER BY id"),
    ("union_fanout", "paid total, and the total of the rest",
     "SELECT 'paid' AS k, SUM(o.amount) AS s FROM orders o JOIN order_items i ON i.order_id = o.id WHERE o.status = 'paid' "
     "UNION ALL SELECT 'other', SUM(amount) FROM orders WHERE status <> 'paid'", "mariadb", "join_fanout", "likely_risk", None, "offer",
     "SELECT 'paid' AS k, SUM(amount) AS s FROM orders WHERE status = 'paid' UNION ALL SELECT 'other', SUM(amount) FROM orders "
     "WHERE status <> 'paid'"),
    ("second_cte_fanout", "total per customer of the paid orders",
     "WITH paid AS (SELECT id, customer_id, amount FROM orders WHERE status = 'paid'), per AS (SELECT p.customer_id, SUM(p.amount) AS s "
     "FROM paid p JOIN order_items i ON i.order_id = p.id GROUP BY p.customer_id) SELECT customer_id, s FROM per ORDER BY customer_id",
     "postgresql", "join_fanout", "likely_risk", "per", "offer", PAID_PER),
    # ---- a block's total joined back at a finer grain (a per-customer total summed over orders)
    ("cross_status", "order total per status",
     "WITH spend AS (SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id) SELECT o.status, SUM(s.total) AS total "
     "FROM orders o JOIN spend s ON s.customer_id = o.customer_id GROUP BY o.status ORDER BY o.status", "mariadb", "join_fanout",
     "likely_risk", None, "found", None),
    ("cross_count", "customers per order status",
     "WITH spend AS (SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id) SELECT o.status, COUNT(s.customer_id) AS n "
     "FROM orders o JOIN spend s ON s.customer_id = o.customer_id GROUP BY o.status ORDER BY o.status", "postgresql", "join_fanout",
     "likely_risk", None, "found", None),
    # ---- the block's own GROUP BY / DISTINCT / single total proves the key: no multiplication, nothing said
    ("q_spend_city", "spend per city",
     "WITH spend AS (SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id) SELECT c.city, SUM(s.total) AS total "
     "FROM customers c JOIN spend s ON s.customer_id = c.id GROUP BY c.city ORDER BY c.city", "mariadb", None, None, None, "quiet",
     "SELECT c.city, SUM(o.amount) AS total FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.city ORDER BY c.city"),
    ("q_pay_per_order", "order amount and payments per customer",
     "WITH pay AS (SELECT order_id, SUM(amount) AS paid FROM payments GROUP BY order_id) SELECT o.customer_id, SUM(o.amount) AS amount, "
     "SUM(p.paid) AS paid FROM orders o JOIN pay p ON p.order_id = o.id GROUP BY o.customer_id ORDER BY o.customer_id", "postgresql",
     None, None, None, "quiet",
     "SELECT o.customer_id, SUM(o.amount) AS amount, SUM((SELECT SUM(p.amount) FROM payments p WHERE p.order_id = o.id)) AS paid "
     "FROM orders o WHERE EXISTS (SELECT 1 FROM payments p WHERE p.order_id = o.id) GROUP BY o.customer_id ORDER BY o.customer_id"),
    ("q_two_pre", "items and payments per order",
     "WITH it AS (SELECT order_id, SUM(quantity) AS qty FROM order_items GROUP BY order_id), pay AS (SELECT order_id, SUM(amount) AS paid "
     "FROM payments GROUP BY order_id) SELECT o.id, it.qty, pay.paid FROM orders o LEFT JOIN it ON it.order_id = o.id "
     "LEFT JOIN pay ON pay.order_id = o.id ORDER BY o.id", "mariadb", None, None, None, "quiet",
     "SELECT o.id, (SELECT SUM(quantity) FROM order_items i WHERE i.order_id = o.id) AS qty, "
     "(SELECT SUM(amount) FROM payments p WHERE p.order_id = o.id) AS paid FROM orders o ORDER BY o.id"),
    ("q_distinct_cte", "customers who ordered, per city",
     "WITH buyers AS (SELECT DISTINCT customer_id FROM orders) SELECT c.city, COUNT(*) AS n FROM customers c JOIN buyers b "
     "ON b.customer_id = c.id GROUP BY c.city ORDER BY c.city", "postgresql", None, None, None, "quiet",
     "SELECT c.city, COUNT(*) AS n FROM customers c WHERE c.id IN (SELECT customer_id FROM orders) GROUP BY c.city ORDER BY c.city"),
    ("q_total_one_row", "each order next to all sales",
     "WITH t AS (SELECT SUM(amount) AS total FROM orders) SELECT o.id, o.amount, t.total FROM orders o CROSS JOIN t ORDER BY o.id",
     "mariadb", None, None, None, "quiet", "SELECT o.id, o.amount, (SELECT SUM(amount) FROM orders) AS total FROM orders o ORDER BY o.id"),
    ("q_derived_key", "items per order, with its amount",
     "SELECT o.id, o.amount, x.n FROM orders o JOIN (SELECT order_id, COUNT(*) AS n FROM order_items GROUP BY order_id) x "
     "ON x.order_id = o.id ORDER BY o.id", "postgresql", None, None, None, "quiet",
     "SELECT o.id, o.amount, (SELECT COUNT(*) FROM order_items i WHERE i.order_id = o.id) AS n FROM orders o "
     "WHERE EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.id) ORDER BY o.id"),
    ("q_sum_of_block", "per-customer spend summed over the customers who have it",
     "WITH spend AS (SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id) SELECT SUM(s.total) AS total "
     "FROM customers c JOIN spend s ON s.customer_id = c.id", "mariadb", None, None, None, "quiet", "SELECT SUM(amount) AS total FROM orders"),
    # ---- a CTE that reads a CTE written after it (refused on both): the CTEs put in the order they need
    ("cte_order2", "number of paid orders",
     "WITH a AS (SELECT id FROM b), b AS (SELECT id FROM orders WHERE status = 'paid') SELECT COUNT(*) AS n FROM a", "mariadb",
     "cte_order", "proven", None, "fix", "SELECT COUNT(*) AS n FROM orders WHERE status = 'paid'"),
    ("cte_order3", "customers with paid orders",
     "WITH x AS (SELECT customer_id FROM y), y AS (SELECT customer_id FROM z), z AS (SELECT customer_id FROM orders WHERE status = 'paid') "
     "SELECT COUNT(DISTINCT customer_id) AS n FROM x", "postgresql", "cte_order", "proven", None, "fix",
     "SELECT COUNT(DISTINCT customer_id) AS n FROM orders WHERE status = 'paid'"),
    ("cte_order_main", "paid total",
     "WITH s AS (SELECT SUM(amount) AS total FROM p), p AS (SELECT amount FROM orders WHERE status = 'paid') SELECT total FROM s",
     "mariadb", "cte_order", "proven", None, "fix", "SELECT SUM(amount) AS total FROM orders WHERE status = 'paid'"),
    # ---- mistakes inside a block, found and fixed in place
    ("cte_eq_null", "orders without a discount", "WITH a AS (SELECT id FROM orders WHERE discount = NULL) SELECT id FROM a ORDER BY id",
     "postgresql", "null", "proven", None, "fix", "SELECT id FROM orders WHERE discount IS NULL ORDER BY id"),
    ("derived_between", "orders between 100 and 1000",
     "SELECT x.id FROM (SELECT id FROM orders WHERE amount BETWEEN 1000 AND 100) x ORDER BY x.id", "mariadb", "between", "proven", None,
     "fix", "SELECT id FROM orders WHERE amount >= 100 AND amount <= 1000 ORDER BY id"),
    ("union_or_value", "paid or shipped orders, then the cancelled ones",
     "SELECT id FROM orders WHERE status = 'paid' OR 'shipped' UNION ALL SELECT id FROM orders WHERE status = 'cancelled'", "mariadb",
     "or", "proven", None, "fix", "SELECT id FROM orders WHERE status IN ('paid', 'shipped', 'cancelled')"),
    ("cte_count_case", "paid orders per customer",
     "WITH t AS (SELECT customer_id, COUNT(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid FROM orders GROUP BY customer_id) "
     "SELECT customer_id, paid FROM t ORDER BY customer_id", "postgresql", "count_case", "proven", None, "fix",
     "SELECT customer_id, SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid FROM orders GROUP BY customer_id ORDER BY customer_id"),
    ("derived_like_star", "products whose name starts with Phone",
     "SELECT x.id FROM (SELECT id FROM products WHERE name LIKE 'Phone*') x ORDER BY x.id", "mariadb", "like", "proven", None, "fix",
     "SELECT id FROM products WHERE name LIKE 'Phone%' ORDER BY id"),
    ("union_between_rev", "cancelled orders, then the small ones",
     "SELECT id FROM orders WHERE status = 'cancelled' UNION ALL SELECT id FROM orders WHERE amount BETWEEN 1000 AND 100", "postgresql",
     "between", "proven", None, "fix",
     "SELECT id FROM orders WHERE status = 'cancelled' UNION ALL SELECT id FROM orders WHERE amount >= 100 AND amount <= 1000"),
    ("cte_agg_where", "customers with more than one order",
     "WITH t AS (SELECT customer_id FROM orders WHERE COUNT(*) > 1 GROUP BY customer_id) SELECT * FROM t", "postgresql", "where", "proven",
     "t", "found", None),
    ("cte_not_in", "departments without employees",
     "WITH d AS (SELECT id FROM departments WHERE id NOT IN (SELECT department_id FROM employees)) SELECT id FROM d ORDER BY id",
     "mariadb", "not_in", "needs_schema", None, "offer", "SELECT id FROM departments WHERE id = 4"),
    ("derived_ties", "the two best paid employees",
     "SELECT x.id FROM (SELECT id, salary FROM employees ORDER BY salary DESC LIMIT 2) x ORDER BY x.id", "postgresql", "stable_order",
     "needs_schema", "derived x", "offer", "SELECT id FROM employees WHERE id IN (1, 6) ORDER BY id"),
    ("cte_year", "orders of 2024", "WITH y AS (SELECT id FROM orders WHERE YEAR(order_date) = 2024) SELECT id FROM y ORDER BY id",
     "mariadb", "sargable", "needs_execution", None, "same", "SELECT id FROM orders WHERE EXTRACT(YEAR FROM order_date) = 2024 ORDER BY id"),
    ("cte_null_math", "net amount of each order",
     "WITH n AS (SELECT id, amount - discount AS net FROM orders) SELECT id, net FROM n ORDER BY id", "postgresql", "null_math",
     "needs_schema", "n", "offer", "SELECT id, amount - CASE WHEN discount IS NULL THEN 0 ELSE discount END AS net FROM orders ORDER BY id"),
    ("cte_like_contains", "products with Laptop in the name",
     "WITH l AS (SELECT id FROM products WHERE name LIKE 'Laptop') SELECT id FROM l ORDER BY id", "mariadb", "like", "likely_risk", None,
     "found", None),
]

LAB = {
    "cte_key_join_rows": ("5", "5"),        # spend (GROUP BY customer_id) joined on customer_id: one row per customer, no copies
    "cross_block_fanout": ("303900", "303900"),  # ... joined back to orders: each customer's total once per order ...
    "cross_block_truth": ("184600", "184600"),   # ... while the real total is this
    "cte_inner_fanout": ("77000", "77000"),  # a SUM inside a CTE after joining items (customer 1) ...
    "cte_inner_truth": ("26000", "26000"),   # ... vs the customer's real total
    "grain_down_rows": ("10", "10"),         # 8 orders joined to their items: the grain moves down to 10 item rows
    "distinct_cte_rows": ("5", "5"),         # DISTINCT customer_id: one row per customer
    "union_rows": ("5", "5"),                # UNION removes repeats ...
    "union_all_rows": ("16", "16"),          # ... UNION ALL keeps them
    "cte_forward_ref": ("1146", "42P01"),    # a CTE reading one written after it is refused
    "derived_no_alias": ("1064", "8"),       # MariaDB refuses a derived table without alias; PostgreSQL 16 accepts it
}
LAB_SQL = {
    "cte_key_join_rows": "WITH spend AS (SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id) "
                         "SELECT COUNT(*) FROM customers c JOIN spend s ON s.customer_id = c.id",
    "cross_block_fanout": "WITH spend AS (SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id) "
                          "SELECT SUM(s.total) FROM orders o JOIN spend s ON s.customer_id = o.customer_id",
    "cross_block_truth": "SELECT SUM(amount) FROM orders",
    "cte_inner_fanout": "WITH t AS (SELECT o.customer_id, SUM(o.amount) AS s FROM orders o JOIN order_items i ON i.order_id = o.id "
                        "GROUP BY o.customer_id) SELECT s FROM t WHERE customer_id = 1",
    "cte_inner_truth": "SELECT SUM(amount) FROM orders WHERE customer_id = 1",
    "grain_down_rows": "SELECT COUNT(*) FROM orders o JOIN order_items i ON i.order_id = o.id",
    "distinct_cte_rows": "WITH c AS (SELECT DISTINCT customer_id FROM orders) SELECT COUNT(*) FROM c",
    "union_rows": "SELECT COUNT(*) FROM (SELECT customer_id FROM orders UNION SELECT customer_id FROM orders) u",
    "union_all_rows": "SELECT COUNT(*) FROM (SELECT customer_id FROM orders UNION ALL SELECT customer_id FROM orders) u",
    "cte_forward_ref": "WITH a AS (SELECT * FROM b), b AS (SELECT id FROM orders) SELECT COUNT(*) FROM a",
    "derived_no_alias": "SELECT COUNT(*) FROM (SELECT id FROM orders)",
}


@pytest.fixture(scope="module")
def engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


def _kind(name):
    return "mariadb" if name == "mysql" else "postgresql"


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)
    assert {p[7] for p in P} >= {"fix", "offer", "quiet", "found", "same"}


@pytest.mark.parametrize("pid,words,q,engine,topic,cls,block,proof,truth", P, ids=[p[0] for p in P])
def test_dbaba_reads_every_block(engines, pid, words, q, engine, topic, cls, block, proof, truth):
    res = analyze_sql(f"{q};\n\n{words}", engine=engine)
    fs = res["correct"]["findings"]
    if proof == "quiet":
        assert not [f for f in fs if f["kind"] in ("error", "wrong") or f["topic"] == "join_fanout"], (pid, fs)
    else:
        found = [f for f in fs if f["topic"] == topic]
        assert found, (pid, [(f["topic"], f["title_en"]) for f in fs])
        assert any(f["confidence"]["class"] == cls for f in found), (pid, [f["confidence"]["class"] for f in found])
        if block:
            assert any(f.get("block") == block for f in found), (pid, [f.get("block") for f in found])
    for name, eng in engines.items():
        if proof == "quiet":
            assert S.rows(eng, q) == S.rows(eng, truth), (pid, name)                 # the query was right: nothing to say
            continue
        if proof == "found":
            continue
        other = res if _kind(name) == engine else analyze_sql(f"{q};\n\n{words}", engine=_kind(name))
        sql = other["correct"].get("corrected") or other.get("suggested")
        assert sql, (pid, name)
        assert S.rows(eng, sql.rstrip().rstrip(";")) == S.rows(eng, truth), (pid, name, sql)


LONG = """WITH paid AS (
  SELECT id, customer_id, amount
  FROM orders
  WHERE status = 'paid'
),
spend AS (
  SELECT customer_id, SUM(amount) AS total, COUNT(*) AS n
  FROM paid
  GROUP BY customer_id
),
items AS (
  SELECT o.customer_id, SUM(o.amount) AS amount_seen, SUM(i.quantity) AS qty
  FROM orders o
  JOIN order_items i ON i.order_id = o.id
  GROUP BY o.customer_id
),
ranked AS (
  SELECT customer_id, total,
         RANK() OVER (ORDER BY total DESC) AS rnk
  FROM spend
)
SELECT
  c.id,
  c.city,
  r.total,
  r.rnk,
  it.qty
FROM customers c
JOIN ranked r ON r.customer_id = c.id
LEFT JOIN items it ON it.customer_id = c.id
WHERE c.id IN (SELECT customer_id FROM payments p JOIN orders o2 ON o2.id = p.order_id)
UNION ALL
SELECT NULL, 'all', SUM(amount), NULL, NULL
FROM (SELECT amount FROM orders WHERE status = 'paid') x
ORDER BY 3 DESC
"""


def test_a_long_query_reads_as_a_tree_with_the_grain_of_each_block():
    assert len(LONG.strip().splitlines()) >= 30
    bl = {b["id"]: b for b in sqlblocks.grain(LONG)["blocks"]}
    assert set(bl) >= {"main", "paid", "spend", "items", "ranked", "subquery 1", "derived x", "branch main #1", "branch main #2"}
    assert bl["spend"]["depends"] == ["paid"] and bl["ranked"]["depends"] == ["spend"]
    assert set(bl["branch main #1"]["depends"]) >= {"ranked", "items", "subquery 1"} and bl["branch main #2"]["depends"] == ["derived x"]
    assert bl["spend"]["output"] == "one row per customer_id" and bl["ranked"]["output"] == bl["ranked"]["input"]   # a window keeps it
    assert bl["items"]["input"] == "one row per order_items row" and bl["items"]["multiplies"]
    assert not bl["branch main #1"]["multiplies"]                     # ranked is one row per customer_id (from spend), items too
    assert sqlblocks.keys(LONG)["spend"] == [{"customer_id"}] and sqlblocks.keys(LONG)["items"] == [{"customer_id"}]
    tree = sqlblocks.tree(LONG)
    assert tree.index("CTE ranked") < tree.index("CTE spend") < tree.index("CTE paid"), tree    # each block under what reads it
    res = analyze_sql(LONG, engine="postgresql")
    assert "Blocks, how they depend on each other" in res["reading"]["en"] and res["blocks"]["en"] == tree
    assert any(f["topic"] == "join_fanout" and f.get("block") == "items" for f in res["correct"]["findings"])


def test_short_queries_keep_a_short_reading():
    res = analyze_sql("WITH t AS (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id) SELECT * FROM t", engine="mariadb")
    assert res["blocks"]["en"] and "Blocks, how they depend" not in res["reading"]["en"]
    res = analyze_sql("SELECT id FROM orders WHERE status = 'paid'", engine="mariadb")
    assert "blocks" not in res


def test_a_block_key_turns_a_false_multiplication_into_the_real_risk():
    """Before tonight: 'COUNT(p.id) is multiplied by the spend rows' (wrong) though spend has one row per customer_id."""
    q = ("WITH spend AS (SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id) SELECT s.customer_id, "
         "SUM(s.total) AS t, COUNT(p.id) AS n FROM spend s JOIN payments p ON p.order_id = s.customer_id GROUP BY s.customer_id")
    fs = analyze_sql(q, engine="mariadb")["correct"]["findings"]
    assert not [f for f in fs if f["kind"] in ("error", "wrong")], fs
    assert [f["title_en"] for f in fs if f["topic"] == "join_fanout"] == ["SUM(s.total) counts each spend row once per payments row"]


def test_the_lab_table_still_holds(engines):
    def one(eng, sql):
        S.build(eng)
        try:
            return str(S.norm(eng.run(sql)[0][0]))
        except Exception as exc:  # noqa: BLE001
            eng.rollback()
            a = getattr(exc, "args", ())
            return str(a[0]) if a and isinstance(a[0], int) else (getattr(exc, "sqlstate", None) or "err")
    for key, (maria, pg) in LAB.items():
        got = (one(engines["mysql"], LAB_SQL[key]), one(engines["postgres"], LAB_SQL[key]))
        assert got == (maria, pg), (key, got)


def test_every_grain_rule_records_where_it_was_proven():
    import json

    assert sqlblocks.PROVEN_ON == ["mariadb", "postgresql"]
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert "cte_order" in funds["names_scope"]["topics"] and "join_fanout" in funds["cardinality"]["topics"]
    assert re.search(r"block", funds["grain"]["rule_en"], re.I)
