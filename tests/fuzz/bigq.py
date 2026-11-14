"""Night 15: the big-query exam. Generated 100-300 line report queries on the practice schema (nested CTEs, two or more window
functions, a UNION ALL, correlated subqueries), each with ONE planted mistake from a family below, and the clean query it was
planted in (the requirement's truth).

For every generated query DBaba must:
- read it as blocks: every CTE in the dependency tree, with the grain going in and coming out of each block, and the tree in
  the reading (30+ lines);
- find the planted mistake, in its block, with the right confidence class (PROVEN / LIKELY RISK / NEEDS SCHEMA / NEEDS
  EXECUTION), and say nothing sure (error / wrong) about anything else -- nor about the clean query;
- prove its answer on MariaDB and PostgreSQL (practice schema): a PROVEN mistake's fix returns the clean query's rows; a speed
  rewrite returns exactly the planted query's rows (same rows, other form); a mistake whose fix needs a choice only the person
  can make is found, not fixed;
- upgrade: the same query pasted with the tables' CREATE TABLE turns NEEDS SCHEMA findings into verified ones or drops them,
  and the proven ones stay proven.

    python -m tests.fuzz.bigq 60 7          # 60 queries from seed 7, all families in turn; prints the problems, 0 = pass
"""

import random
import re
import sys

FAMILIES = {
    # family: (topic(s) DBaba must name, class, proof, where it goes)
    "null_eq": ({"null"}, "proven", "fix", "orders"),            # discount = NULL in the order filter: no row passes
    "between_rev": ({"between"}, "proven", "fix", "orders"),     # BETWEEN high AND low: no row passes
    "or_value": ({"or"}, "proven", "fix", "orders"),             # status = 'paid' OR 'shipped': the second side is a bare value
    "count_case": ({"count_case"}, "proven", "fix", "items"),    # COUNT(CASE ... ELSE 0 END) counts every row
    "cte_order": ({"cte_order"}, "proven", "fix", None),         # a CTE reads one written after it (refused on both)
    "agg_where": ({"where"}, "proven", "fix", "cust"),           # SUM() in WHERE (refused on both)
    "bad_alias": ({"alias"}, "proven", "found", "facts"),        # a column of an alias that isn't in the block
    "cartesian": ({"cartesian"}, "proven", "found", "cat"),      # a comma table never tied to the others
    "on_unrelated": ({"join"}, "proven", "found", "cat"),        # an ON that doesn't mention the table it joins
}
# what the same planted mistake also, truly, causes (said too, not a false alarm): an untied table multiplies the totals
ALSO = {"cartesian": {"group"}, "on_unrelated": {"group"}}
FAMILIES |= {
    "corr_self": ({"corr_self"}, "proven", "found", None),       # the subquery compares its own table with itself
    "fanout": ({"join_fanout"}, "likely_risk", "found", "cust"),  # a parent total after a join to its child rows
    "not_in": ({"not_in"}, "needs_schema", "offer", None),         # NOT IN (subquery): one NULL empties it
    "sargable": ({"sargable"}, "needs_execution", "same", "orders"),  # YEAR()/EXTRACT() on the filtered column
    "wildcard": ({"leading_wildcard"}, "needs_execution", "none", None),  # LIKE '%...' (same rows; can't use an index)
    "distinct_hide": ({"distinct_join"}, "likely_risk", "offer", "buyers"),  # DISTINCT over a join to child rows
    "rank_ties": ({"win_rank_ties"}, "needs_schema", "found", "fav"),      # ROW_NUMBER over a sort that can tie
    "preagg": ({"preagg"}, "needs_execution", "same", None),    # two correlated totals over one table, per row
    "unused_cte": ({"cte_unused"}, "likely_risk", "none", "spare"),   # a CTE nothing reads (a join that was forgotten)
    "lag_default": ({"dialect"}, "proven", "fix", "ranked"),  # LAG(x, 1, 0): MariaDB has no default argument (1064)
}
ORDER = list(FAMILIES)

STATUSES = ["paid", "shipped", "pending", "cancelled"]


def _pick(r: random.Random, xs, k=None):
    return r.choice(xs) if k is None else r.sample(xs, k)


def make(seed: int, family: str | None = None) -> dict:
    """{'clean', 'planted', 'family', 'topics', 'cls', 'proof', 'block', 'engine', 'ctes', 'lines'}"""
    r = random.Random(seed)
    fam = family or ORDER[seed % len(ORDER)]
    engine = "mariadb" if fam == "lag_default" else r.choice(["mariadb", "postgresql"])
    wide = r.random() < 0.5                                  # more columns and notes
    size = r.choice(["small", "mid", "large"])               # small ~100 lines, large adds monthly trend blocks (~250)
    lower = r.random() < 0.3                                 # keywords written in lower case
    if fam in ("cartesian", "on_unrelated", "rank_ties", "distinct_hide") and size == "small":
        size = "mid"                                         # their blocks are in the mid and large shapes
    n = {k: k for k in ("orders", "items", "pays", "rets", "facts", "cust", "ranked", "cat", "fav")}
    names = {"orders": ["paid_orders", "base_orders", "kept_orders", "order_base"],
             "items": ["item_totals", "line_totals", "items_per_order"],
             "pays": ["pay_totals", "payments_per_order", "paid_per_order"],
             "rets": ["returned", "returns_per_order", "returned_units"],
             "facts": ["order_facts", "order_rollup", "orders_enriched"],
             "cust": ["per_customer", "customer_totals", "cust_summary"],
             "ranked": ["ranked", "ranked_customers", "customer_rank"],
             "cat": ["cust_cat", "customer_categories", "cat_units"],
             "fav": ["fav_cat", "top_category", "best_category"],
             "month": ["cust_month", "monthly_spend", "per_month"],
             "trend": ["month_trend", "spend_trend", "monthly_change"],
             "buyers": ["buyers", "product_buyers", "who_bought"],
             "quart": ["quartiles", "spend_quarters", "cust_quarter"],
             "spare": ["supplier_mix", "old_totals", "unused_stats"]}
    for k, opts in names.items():
        n[k] = r.choice(opts)
    ao, ai, ap, ar, af, ac, ark = "o", "i", "p", "rt", "f", "pc", "rk"
    st = sorted(_pick(r, ["paid", "shipped", "pending"], r.choice([1, 2, 2, 3])), key=STATUSES.index)
    if fam == "or_value" and len(st) < 2:
        st = ["paid", "shipped"]
    d1 = r.choice(["2024-01-01", "2024-03-01", "2024-06-01"])
    lo, hi = r.choice([100, 200, 300]), r.choice([70000, 90000, 100000])
    since = r.choice(["2024-01-01", "2023-06-01", "2024-02-01"])
    big = r.choice([1000, 5000, 20000])

    # ---------------------------------------------------------------- the orders kept (the filters the families go into)
    def orders_cte(planted: bool) -> str:
        conds = []
        if fam == "or_value":
            conds.append(f"({ao}.status = '{st[0]}' OR '{st[1]}')" if planted else f"({ao}.status IN ({', '.join(repr(x) for x in st[:2])}))")
        else:
            conds.append(f"{ao}.status IN ({', '.join(repr(x) for x in st)})")
        if fam == "sargable" and planted:
            conds.append(f"YEAR({ao}.order_date) = 2024" if engine == "mariadb" else f"EXTRACT(YEAR FROM {ao}.order_date) = 2024")
        elif fam == "sargable":
            conds.append(f"{ao}.order_date >= '2024-01-01' AND {ao}.order_date < '2025-01-01'")
        else:
            conds.append(f"{ao}.order_date >= '{d1}'")
        if fam == "between_rev":
            conds.append(f"{ao}.amount BETWEEN {hi} AND {lo}" if planted else f"{ao}.amount BETWEEN {lo} AND {hi}")
        if fam == "null_eq":
            conds.append(f"{ao}.discount = NULL" if planted else f"{ao}.discount IS NULL")
        cols = [f"{ao}.id", f"{ao}.customer_id", f"{ao}.order_date", f"{ao}.status", f"{ao}.amount"]
        if wide:
            cols += [f"COALESCE({ao}.shipping, 0) AS shipping", f"COALESCE({ao}.discount, 0) AS discount"]
        return (f"{n['orders']} AS (\n    -- step 1: the orders this report is about\n    SELECT " + ",\n           ".join(cols)
                + f"\n    FROM orders {ao}\n    WHERE " + "\n      AND ".join(conds) + "\n)")

    def items_cte(planted: bool) -> str:
        disc = (f"COUNT(CASE WHEN {ai}.discount IS NOT NULL THEN 1 ELSE 0 END)" if planted and fam == "count_case" else
                f"SUM(CASE WHEN {ai}.discount IS NOT NULL THEN 1 ELSE 0 END)")
        cols = [f"{ai}.order_id", f"SUM({ai}.quantity * {ai}.unit_price) AS items_value", f"SUM({ai}.quantity) AS units",
                "COUNT(*) AS line_count", f"{disc} AS discounted_lines"]
        if wide:
            cols += [f"MAX({ai}.unit_price) AS top_price", f"MIN({ai}.unit_price) AS low_price"]
        return (f"{n['items']} AS (\n    -- step 2: one row per order with its lines added up\n    SELECT " + ",\n           ".join(cols)
                + f"\n    FROM order_items {ai}\n    GROUP BY {ai}.order_id\n)")

    def pays_cte() -> str:
        return (f"{n['pays']} AS (\n    -- step 3: what was paid against each order\n    SELECT {ap}.order_id,\n           SUM({ap}.amount) AS paid,"
                f"\n           COUNT(*) AS payment_count,\n           MAX({ap}.paid_at) AS last_paid\n    FROM payments {ap}\n"
                f"    GROUP BY {ap}.order_id\n)")

    def rets_cte() -> str:
        return (f"{n['rets']} AS (\n    -- step 4: units sent back, per order\n    SELECT ri.order_id,\n           SUM({ar}.quantity) AS returned_units"
                f"\n    FROM returns {ar}\n    JOIN order_items ri ON ri.id = {ar}.order_item_id\n    GROUP BY ri.order_id\n)")

    def facts_cte(planted: bool) -> str:
        amt = "x.amount" if planted and fam == "bad_alias" else f"{ao}.amount"
        cols = [f"{ao}.id", f"{ao}.customer_id", f"{ao}.order_date", f"{amt} AS amount",
                f"COALESCE(it.items_value, 0) AS items_value", "COALESCE(it.units, 0) AS units",
                "COALESCE(it.discounted_lines, 0) AS discounted_lines", "COALESCE(pt.paid, 0) AS paid",
                "COALESCE(rd.returned_units, 0) AS returned_units"]
        if wide:
            cols += [f"{ao}.shipping", "pt.last_paid", f"CASE WHEN COALESCE(pt.paid, 0) >= {amt} THEN 'settled'\n"
                     f"                WHEN COALESCE(pt.paid, 0) > 0 THEN 'part paid'\n                ELSE 'unpaid'\n           END AS pay_state"]
        return (f"{n['facts']} AS (\n    -- step 5: each kept order with its lines, payments and returns (one row per order)\n    SELECT "
                + ",\n           ".join(cols) + f"\n    FROM {n['orders']} {ao}\n    LEFT JOIN {n['items']} it ON it.order_id = {ao}.id"
                f"\n    LEFT JOIN {n['pays']} pt ON pt.order_id = {ao}.id\n    LEFT JOIN {n['rets']} rd ON rd.order_id = {ao}.id\n)")

    def cust_cte(planted: bool) -> str:
        fan = planted and fam == "fanout"
        cols = [f"{af}.customer_id", f"COUNT(DISTINCT {af}.id) AS order_count" if fan else "COUNT(*) AS order_count",
                f"SUM({af}.amount) AS spend", f"SUM({af}.paid) AS paid",
                f"SUM(li.quantity) AS units" if fan else f"SUM({af}.units) AS units", f"SUM({af}.returned_units) AS returned_units",
                f"MIN({af}.order_date) AS first_order", f"MAX({af}.order_date) AS last_order"]
        if wide:
            cols += [f"SUM({af}.discounted_lines) AS discounted_lines", f"SUM({af}.items_value) AS items_value"]
        frm = f"FROM {n['facts']} {af}" + (f"\n    JOIN order_items li ON li.order_id = {af}.id" if fan else "")
        tail = (f"\n    WHERE SUM({af}.amount) > 0\n    GROUP BY {af}.customer_id" if planted and fam == "agg_where" else
                f"\n    GROUP BY {af}.customer_id\n    HAVING SUM({af}.amount) > 0")
        return (f"{n['cust']} AS (\n    -- step 6: one row per customer\n    SELECT " + ",\n           ".join(cols) + f"\n    {frm}{tail}\n)")

    def ranked_cte(planted: bool) -> str:
        cols = [f"{ac}.customer_id", f"{ac}.order_count", f"{ac}.spend", f"{ac}.paid", f"{ac}.units",
                f"RANK() OVER (ORDER BY {ac}.spend DESC) AS spend_rank",
                f"SUM({ac}.spend) OVER () AS all_spend",
                (f"LAG({ac}.spend, 1, 0) OVER (ORDER BY {ac}.spend DESC, {ac}.customer_id) AS next_higher_spend" if planted and fam == "lag_default"
                 else f"COALESCE(LAG({ac}.spend, 1) OVER (ORDER BY {ac}.spend DESC, {ac}.customer_id), 0) AS next_higher_spend"
                 if fam == "lag_default" else f"LAG({ac}.spend) OVER (ORDER BY {ac}.spend DESC, {ac}.customer_id) AS next_higher_spend")]
        if wide:
            cols += [f"DENSE_RANK() OVER (ORDER BY {ac}.units DESC) AS units_rank", f"{ac}.first_order", f"{ac}.last_order"]
        return (f"{n['ranked']} AS (\n    -- step 7: rank the customers and keep the grand total next to each\n    SELECT "
                + ",\n           ".join(cols) + f"\n    FROM {n['cust']} {ac}\n)")

    def cat_cte(planted: bool) -> str:
        if planted and fam == "cartesian":
            j = f"    JOIN order_items ci ON ci.order_id = co.id,\n         products cp"
        elif planted and fam == "on_unrelated":
            j = f"    JOIN order_items ci ON ci.order_id = co.id\n    JOIN products cp ON ci.order_id = co.id"
        else:
            j = f"    JOIN order_items ci ON ci.order_id = co.id\n    JOIN products cp ON cp.id = ci.product_id"
        return (f"{n['cat']} AS (\n    -- step 8: units each customer bought per category\n    SELECT co.customer_id,\n           cp.category_id,"
                f"\n           SUM(ci.quantity) AS cat_units\n    FROM {n['orders']} co\n{j}\n    WHERE cp.category_id IS NOT NULL\n"
                f"    GROUP BY co.customer_id, cp.category_id\n)")

    def fav_cte(planted: bool) -> str:
        return (f"{n['fav']} AS (\n    -- step 9: each customer's top category (ties go to the smaller category id)\n    SELECT cc.customer_id,"
                f"\n           cc.category_id,\n           cc.cat_units,\n           ROW_NUMBER() OVER (PARTITION BY cc.customer_id"
                f"\n                              ORDER BY cc.cat_units DESC{'' if planted and fam == 'rank_ties' else ', cc.category_id'}) AS rn"
                f"\n    FROM {n['cat']} cc\n)")

    def month_cte() -> str:
        mexpr = "EXTRACT(YEAR FROM mo.order_date) * 100 + EXTRACT(MONTH FROM mo.order_date)"   # 202406: the same on both
        return (f"{n['month']} AS (\n    -- step 10: spend per customer per month\n    SELECT mo.customer_id,\n           {mexpr} AS month,"
                f"\n           SUM(mo.amount) AS month_spend,\n           COUNT(*) AS month_orders\n    FROM {n['orders']} mo\n"
                f"    GROUP BY mo.customer_id, {mexpr}\n)")

    def trend_cte() -> str:
        return (f"{n['trend']} AS (\n    -- step 11: each month against the month before, per customer\n    SELECT cm.customer_id,"
                f"\n           cm.month,\n           cm.month_spend,\n           LAG(cm.month_spend) OVER (PARTITION BY cm.customer_id"
                f"\n                                     ORDER BY cm.month) AS prev_spend,\n           ROW_NUMBER() OVER "
                f"(PARTITION BY cm.customer_id\n                              ORDER BY cm.month DESC) AS recent\n    FROM {n['month']} cm\n)")

    def buyers_cte(planted: bool) -> str:
        if planted and fam == "distinct_hide":
            body = (f"    SELECT DISTINCT bc.id AS customer_id,\n           bc.city\n    FROM customers bc\n"
                    f"    JOIN orders bo ON bo.customer_id = bc.id")
        else:
            body = (f"    SELECT bc.id AS customer_id,\n           bc.city\n    FROM customers bc\n"
                    f"    WHERE EXISTS (SELECT 1 FROM orders bo WHERE bo.customer_id = bc.id)")
        return f"{n['buyers']} AS (\n    -- step 12: customers who ordered at least once\n{body}\n)"

    def quart_cte() -> str:
        return (f"{n['quart']} AS (\n    -- step 13: quarter of the spend ranking each customer falls in\n    SELECT q.customer_id,"
                f"\n           NTILE(4) OVER (ORDER BY q.spend DESC, q.customer_id) AS spend_quarter,\n           q.spend - AVG(q.spend) "
                f"OVER () AS above_average\n    FROM {n['ranked']} q\n)")

    def main(planted: bool) -> str:
        corr = ("o2.customer_id = o2.customer_id" if planted and fam == "corr_self" else "o2.customer_id = c.id")
        cols = ["c.id AS customer_id", "c.first_name", "c.last_name", "c.city", f"{ark}.order_count", f"{ark}.spend", f"{ark}.paid",
                f"{ark}.spend_rank", f"ROUND(100 * {ark}.spend / {ark}.all_spend, 2) AS pct_of_all", f"{ark}.next_higher_spend",
                "fc.category_id AS top_category",
                f"(SELECT COUNT(*)\n              FROM returns rr\n              JOIN order_items oi ON oi.id = rr.order_item_id\n"
                f"              JOIN orders o2 ON o2.id = oi.order_id\n             WHERE {corr}) AS returns_count"]
        blanks = ["c.id", "c.first_name", "c.last_name", "c.city", "0", "0", "0", "NULL", "0", "NULL", "NULL", "0"]
        if wide:
            cols.insert(8, f"CASE WHEN {ark}.spend >= {big} THEN 'gold'\n                WHEN {ark}.spend >= {big // 10} THEN 'silver'"
                           f"\n                ELSE 'basic'\n           END AS segment")
            blanks.insert(8, "'none'")
            cols.insert(9, f"{ark}.units_rank")
            blanks.insert(9, "NULL")
        if size == "small":                                  # no category blocks
            k = cols.index("fc.category_id AS top_category")
            cols[k] = "NULL AS top_category"
        if size == "large":
            cols.append("qt.spend_quarter")
            cols.append("ROUND(qt.above_average, 2) AS above_average")
            blanks += ["NULL", "NULL"]
            cols.append("tr.month_spend AS last_month_spend")
            cols.append("tr.month_spend - tr.prev_spend AS month_change")
            blanks += ["NULL", "NULL"]
        if fam == "preagg" or r2.random() < 0.3:
            cols.append(f"(SELECT COUNT(*) FROM payments px JOIN orders ox ON ox.id = px.order_id WHERE ox.customer_id = c.id) AS payment_rows")
            blanks.append("0")
            if fam == "preagg":
                cols.append("(SELECT MAX(oy.order_date) FROM orders oy WHERE oy.customer_id = c.id) AS last_any_order")
                cols.append("(SELECT COUNT(*) FROM orders oy WHERE oy.customer_id = c.id) AS any_orders")
                blanks += ["NULL", "0"]
        w1 = [f"c.created_at >= '{since}'"]
        if fam == "wildcard" and planted:
            w1.append("c.email LIKE '%.test'")
        anti = ("c.id NOT IN (SELECT o.customer_id FROM orders o)" if planted and fam == "not_in" else
                "NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)")
        joins = f"\nFROM customers c\nJOIN {n['ranked']} {ark} ON {ark}.customer_id = c.id"
        if size != "small":
            joins += f"\nLEFT JOIN {n['fav']} fc ON fc.customer_id = c.id AND fc.rn = 1"
            joins += f"\nJOIN {n['buyers']} b ON b.customer_id = c.id"
        if size == "large":
            joins += f"\nLEFT JOIN {n['trend']} tr ON tr.customer_id = c.id AND tr.recent = 1"
            joins += f"\nJOIN {n['quart']} qt ON qt.customer_id = c.id"
        return ("-- the report: ranked customers, then customers who never ordered\nSELECT " + ",\n       ".join(cols)
                + joins + "\nWHERE " + "\n  AND ".join(w1)
                + "\nUNION ALL\n-- customers with no order at all\nSELECT " + ",\n       ".join(blanks)
                + f"\nFROM customers c\nWHERE {anti}\nORDER BY 1")

    def build(planted: bool) -> tuple[str, list[str]]:
        defs = [("orders", orders_cte(planted)), ("items", items_cte(planted)), ("pays", pays_cte()), ("rets", rets_cte()),
                ("facts", facts_cte(planted)), ("cust", cust_cte(planted)), ("ranked", ranked_cte(planted))]
        if size != "small":
            defs += [("cat", cat_cte(planted)), ("fav", fav_cte(planted)), ("buyers", buyers_cte(planted))]
        if size == "large":
            defs += [("month", month_cte()), ("trend", trend_cte()), ("quart", quart_cte())]
        if planted and fam == "unused_cte":                    # a CTE written and then never joined
            defs.insert(r2.randint(1, len(defs)), ("spare", f"{n['spare']} AS (\n    -- supplier totals (meant for the report)\n"
                        f"    SELECT pr.supplier_id,\n           COUNT(*) AS products,\n           SUM(pr.stock) AS stock\n"
                        f"    FROM products pr\n    GROUP BY pr.supplier_id\n)"))
        if planted and fam == "cte_order":                     # the ranking written before the totals it ranks
            k = [d[0] for d in defs]
            i, j = k.index("cust"), k.index("ranked")
            defs[i], defs[j] = defs[j], defs[i]
        head = (f"-- customer value report (generated, seed {seed})\n-- reads: orders, order_items, payments, returns, products, customers\n")
        if size == "large":                                  # a long report carries notes on what each column means
            head += "--\n-- columns:\n" + "".join(f"--   {c:<20} {w}\n" for c, w in [
                ("customer_id", "the customer"), ("order_count", "kept orders"), ("spend", "their amount, added up"),
                ("paid", "payments against them"), ("spend_rank", "1 = the biggest spender (ties share a rank)"),
                ("pct_of_all", "share of all kept spend"), ("next_higher_spend", "spend of the customer just above"),
                ("top_category", "category with most units bought"), ("returns_count", "returned lines, any order"),
                ("spend_quarter", "1 = top quarter"), ("last_month_spend", "spend in their latest month"),
                ("month_change", "against the month before")]) + "--\n"
        r2.seed(seed)
        text = head + "WITH " + ",\n".join(d for _k, d in defs) + "\n" + main(planted)
        return (_lower(text) if lower else text), [n[k] for k, _d in defs]

    r2 = random.Random(seed)
    clean, ctes = build(False)
    planted, _ = build(True)
    topics, cls, proof, where = FAMILIES[fam]
    block = n[where] if where else None
    return {"seed": seed, "family": fam, "clean": clean, "planted": planted, "topics": topics, "cls": cls, "proof": proof,
            "block": block, "engine": engine, "ctes": ctes, "lines": planted.count("\n") + 1}


KW = ("SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING", "LEFT JOIN", "JOIN", "WITH", "UNION ALL", "AS", "AND", "OR",
      "CASE", "WHEN", "THEN", "ELSE", "END", "OVER", "PARTITION BY", "DESC", "DISTINCT", "EXISTS", "NOT", "IN", "IS", "NULL", "ON",
      "BETWEEN", "LIKE", "COUNT", "SUM", "MIN", "MAX", "COALESCE", "ROUND", "RANK", "DENSE_RANK", "ROW_NUMBER", "LAG")


def _lower(sql: str) -> str:
    """Keywords in lower case (outside quotes and comments), as many people write them."""
    out = []
    for part in re.split(r"('(?:[^']|'')*'|--[^\n]*)", sql):
        if part.startswith("'") or part.startswith("--"):
            out.append(part)
        else:
            out.append(re.sub(r"\b(" + "|".join(k.replace(" ", r"\s+") for k in KW) + r")\b", lambda m: m.group(0).lower(), part))
    return "".join(out)


# ------------------------------------------------------------------------------------------------ the practice tables' DDL
def ddl(not_null: tuple = ()) -> str:
    """CREATE TABLE for the practice schema (keys on id); columns in not_null ('orders.customer_id', ...) get NOT NULL."""
    from tests.practice import schema as S

    out = []
    for t in S.ORDER:
        cols = [c + (" NOT NULL" if f"{t}.{c.split()[0]}" in not_null else "") for c in S.TABLES[t][0]]
        out.append(f"CREATE TABLE {t} ({', '.join(cols)}, PRIMARY KEY (id));")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ the exam of one query
def _all(res: dict) -> list:
    corr = (res.get("correct") or {}).get("findings") or []
    return list(corr) + [f for f in res.get("findings") or [] if not f.get("kind")]


def _rows(eng, sql):
    from tests.practice import schema as S

    try:
        return S.rows(eng, sql)
    except Exception as exc:  # noqa: BLE001
        eng.rollback()
        return f"ERROR {str(exc)[:120]}"


def exam(case: dict, engines: dict) -> list[str]:
    """The problems DBaba has with one generated query (empty = passed)."""
    from app.agents.sql import analyze_sql

    P = []
    tag = f"[{case['family']} seed {case['seed']}]"
    if not 100 <= case["lines"] <= 300:
        P.append(f"{tag} generator: {case['lines']} lines")
    res = analyze_sql(case["planted"], engine=case["engine"])
    # the tree and the grain of every block
    bl = (res.get("blocks") or {}).get("blocks") or []
    ids = {b["id"] for b in bl}
    for c in case["ctes"]:
        if c not in ids:
            P.append(f"{tag} the tree has no block for CTE {c}")
    for b in bl:
        if not b.get("input") or not b.get("output"):
            P.append(f"{tag} block {b['id']} has no grain in/out")
    if "Blocks, how they depend" not in ((res.get("reading") or {}).get("en") or ""):
        P.append(f"{tag} the reading of a {case['lines']}-line query doesn't show the tree")
    # the planted finding, its class and its block
    fs = _all(res)
    hit = [f for f in fs if f.get("topic") in case["topics"]]
    if not hit:
        P.append(f"{tag} planted {case['topics']} not found; found {[(f.get('kind'), f.get('topic')) for f in fs]}")
    else:
        good = [f for f in hit if (f.get("confidence") or {}).get("class") == case["cls"]]
        if not good:
            P.append(f"{tag} class {[(f.get('confidence') or {}).get('class') for f in hit]} instead of {case['cls']}")
        if case["block"] and not any(f.get("block") == case["block"] for f in hit) and case["family"] not in ("sargable",):
            if not any(case["block"] in (f.get("title_en") or "") for f in hit):
                P.append(f"{tag} found, but not in block {case['block']}: {[f.get('block') for f in hit]}")
    sure = [f for f in fs if f.get("kind") in ("error", "wrong") and f.get("topic") not in case["topics"] | ALSO.get(case["family"], set())]
    if sure:
        P.append(f"{tag} false alarm on the planted query: {[(f['topic'], f['title_en'][:80]) for f in sure]}")
    clean = analyze_sql(case["clean"], engine=case["engine"])
    sure = [f for f in _all(clean) if f.get("kind") in ("error", "wrong")]
    if sure:
        P.append(f"{tag} false alarm on the clean query: {[(f['topic'], f['title_en'][:80]) for f in sure]}")
    # the proof on both databases
    truth = {k: _rows(e, case["clean"]) for k, e in engines.items()}
    for k, t in truth.items():
        if isinstance(t, str):
            P.append(f"{tag} the clean query fails on {k}: {t}")
    fix = (res.get("correct") or {}).get("corrected") or res.get("rewritten")
    if case["proof"] == "fix":
        if not fix:
            P.append(f"{tag} no fix for a proven mistake")
        else:
            for k, e in engines.items():
                got = _rows(e, fix)
                if got != truth[k]:
                    P.append(f"{tag} the fix gives other rows than the requirement on {k}: {str(got)[:160]}")
    elif case["proof"] == "offer":                           # 'if that's what you meant': the offered query gives the truth
        offer = res.get("suggested") or fix
        if not offer:
            P.append(f"{tag} nothing offered for a risk")
        else:
            for k, e in engines.items():
                got = _rows(e, offer)
                if got != truth[k]:
                    P.append(f"{tag} the offered query gives other rows than the requirement on {k}: {str(got)[:160]}")
    elif case["proof"] == "same":
        if not fix:
            P.append(f"{tag} no faster form offered")
        else:
            for k, e in engines.items():
                if k == "mysql" and case["engine"] != "mariadb" or k == "postgres" and case["engine"] != "postgresql":
                    continue                                   # the planted form is in the other engine's words
                if _rows(e, fix) != _rows(e, case["planted"]):
                    P.append(f"{tag} the faster form gives other rows than the query on {k}")
    return P


def explain_text(eng, sql: str) -> str:
    """The plan as the engine prints it (MariaDB's table with its header row, PostgreSQL's text lines), as a person pastes it."""
    cur = eng.conn.cursor()
    cur.execute("EXPLAIN " + sql)
    rows = cur.fetchall()
    if eng.kind == "mysql":
        cols = [d[0] for d in cur.description]
        return "\t".join(cols) + "\n" + "\n".join("\t".join("NULL" if x is None else str(x) for x in r) for r in rows)
    return "\n".join(str(r[0]) for r in rows)


def upgrade(case: dict, engines: dict | None = None) -> list[str]:
    """The same query with the tables' CREATE TABLE: NEEDS SCHEMA findings verified or dropped, PROVEN ones stay. With its
    EXPLAIN: a NEEDS EXECUTION finding is confirmed by the plan (verified) or ruled out by it, never left hanging."""
    from app.agents.sql import analyze_sql

    tag = f"[upgrade {case['family']} seed {case['seed']}]"
    P = []
    if case["cls"] == "needs_execution" and engines:
        k = "mysql" if case["engine"] == "mariadb" else "postgres"
        if k in engines:
            try:
                plan = explain_text(engines[k], case["planted"])
            except Exception as exc:  # noqa: BLE001
                engines[k].rollback()
                return [f"{tag} EXPLAIN failed on {k}: {exc}"]
            res = analyze_sql(case["planted"], plan, engine=case["engine"])
            if not (res.get("plan") or {}).get("steps"):
                P.append(f"{tag} the pasted EXPLAIN was not read")
            hit = [f for f in _all(res) if f.get("topic") in case["topics"]]
            ruled = [d for d in res.get("ruled_out") or [] if d.get("by") == "plan" and d.get("topic") in case["topics"]]
            if not ruled and not (hit and all((f.get("confidence") or {}).get("plan") == "confirmed" and f["confidence"]["verified"]
                                              for f in hit)):
                P.append(f"{tag} with EXPLAIN, the speed risk is neither confirmed nor ruled out: {[f.get('confidence') for f in hit]}")
    if case["family"] == "not_in":
        for nn, want in (((), "verified"), (("orders.customer_id",), "dropped")):
            res = analyze_sql(ddl(nn) + "\n" + case["planted"], engine=case["engine"])
            hit = [f for f in _all(res) if f.get("topic") == "not_in" and f.get("kind")]
            if want == "verified" and not (hit and all((f.get("confidence") or {}).get("verified") and
                                                       f["confidence"]["class"] == "proven" for f in hit)):
                P.append(f"{tag} nullable customer_id: NOT IN not verified: {[f.get('confidence') for f in hit]}")
            if want == "dropped" and hit:
                P.append(f"{tag} customer_id NOT NULL: NOT IN still said")
        return P
    res = analyze_sql(ddl() + "\n" + case["planted"], engine=case["engine"])
    hit = [f for f in _all(res) if f.get("topic") in case["topics"]]
    if not hit:
        P.append(f"{tag} with the tables, the planted {case['topics']} is no longer found")
    elif case["cls"] == "proven" and not all((f.get("confidence") or {}).get("class") == "proven" for f in hit):
        P.append(f"{tag} a proven finding changed class with the tables")
    elif case["family"] == "fanout" and not any((f.get("confidence") or {}).get("verified") for f in hit):
        P.append(f"{tag} fan-out not verified though order_items.order_id is not a key")
    sure = [f for f in _all(res) if f.get("kind") in ("error", "wrong") and f.get("topic") not in case["topics"] | ALSO.get(case["family"], set())
            and f.get("topic") not in ("schema_column", "schema_table")]
    if sure:
        P.append(f"{tag} false alarm with the tables: {[(f['topic'], f['title_en'][:80]) for f in sure]}")
    return P


def main(argv):
    from tests.practice import schema as S

    n = int(argv[0]) if argv else 28
    seed = int(argv[1]) if len(argv) > 1 else 1
    engines = S.engines()
    problems, lines = [], []
    for k in range(n):
        case = make(seed + k)
        lines.append(case["lines"])
        problems += exam(case, engines)
        problems += upgrade(case, engines)
    for p in problems:
        print(p)
    print(f"{n} big queries ({min(lines)}-{max(lines)} lines), {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
