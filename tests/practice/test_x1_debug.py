"""Night X1 practice bank: debugging returns the fixed statement itself.

A pasted query with a mistake DBaba can see gets back the corrected statement, runnable, in the reply (UPDATE / DELETE as a
preview, never run). Every problem below is a made-up shop (tests/practice/schema.py's customers, orders, products,
categories, suppliers), and every answer is proven on MariaDB and PostgreSQL:

  fix     the corrected statement returns the rows of the statement meant (written independently), on both databases
  change  the corrected UPDATE / DELETE, run inside the lab, leaves the table as the statement meant does
  alt     the reading named (moved into ON, added to GROUP BY, the row of each group's MAX/MIN, INNER JOIN) is written out
          and returns the rows of the statement meant
  offer   only a 'check' (the words don't say the intent): the rewrite is offered as "if that's what you meant"
  quiet   a correct statement (or one the chosen database accepts): nothing said about it

The mistake families (general rules, not these queries): clauses out of order, == / =< / =>, AND/OR precedence when the
person's words name it, a LEFT JOIN undone by WHERE, columns that aren't grouped, a SELECT name used in WHERE, = NULL, and a
change whose subquery reads the table it changes (refused by MySQL).
"""

import re

import pytest

from app.agents.sql import analyze_sql
from tests.practice import schema as S

# (id, words, query, engine, topic, kind, proof, truth)
P = [
    # ---- clauses out of order: moved back, every condition as written
    ("co_order_where", "", "SELECT id, amount FROM orders ORDER BY amount DESC WHERE status = 'paid'", "mariadb", "clause_order", "error",
     "fix", "SELECT id, amount FROM orders WHERE status = 'paid' ORDER BY amount DESC"),
    ("co_group_where", "", "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id WHERE amount > 400", "postgresql",
     "clause_order", "error", "fix", "SELECT customer_id, COUNT(*) AS n FROM orders WHERE amount > 400 GROUP BY customer_id"),
    ("co_limit_order", "", "SELECT id FROM products LIMIT 3 ORDER BY price DESC", "mariadb", "clause_order", "error", "fix",
     "SELECT id FROM products ORDER BY price DESC LIMIT 3"),
    ("co_having_group", "", "SELECT category_id, AVG(price) AS a FROM products HAVING AVG(price) > 1000 GROUP BY category_id",
     "postgresql", "clause_order", "error", "fix",
     "SELECT category_id, AVG(price) AS a FROM products GROUP BY category_id HAVING AVG(price) > 1000"),
    ("co_join_after_where", "", "SELECT o.id, c.city FROM orders o WHERE o.amount > 1000 JOIN customers c ON c.id = o.customer_id",
     "mariadb", "clause_order", "error", "fix",
     "SELECT o.id, c.city FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.amount > 1000"),
    ("co_all_shuffled", "", "SELECT city, COUNT(*) AS n FROM customers ORDER BY city HAVING COUNT(*) > 1 WHERE city IS NOT NULL "
     "GROUP BY city", "postgresql", "clause_order", "error", "fix",
     "SELECT city, COUNT(*) AS n FROM customers WHERE city IS NOT NULL GROUP BY city HAVING COUNT(*) > 1 ORDER BY city"),
    ("co_comment", "", "SELECT id FROM orders ORDER BY id -- oldest first\nWHERE status = 'paid'", "mariadb", "clause_order", "error",
     "fix", "SELECT id FROM orders WHERE status = 'paid' ORDER BY id"),
    ("co_update_set", "", "UPDATE products WHERE stock = 0 SET stock = 10", "postgresql", "clause_order", "error", "change",
     "UPDATE products SET stock = 10 WHERE stock = 0"),
    # ---- operators from other languages
    ("op_double", "", "SELECT id FROM orders WHERE status == 'paid'", "mariadb", "operator", "error", "fix",
     "SELECT id FROM orders WHERE status = 'paid'"),
    ("op_le", "", "SELECT id FROM products WHERE price =< 800", "postgresql", "operator", "error", "fix",
     "SELECT id FROM products WHERE price <= 800"),
    ("op_ge", "", "SELECT id FROM products WHERE stock => 5", "mariadb", "operator", "error", "fix",
     "SELECT id FROM products WHERE stock >= 5"),
    ("op_triple", "", "SELECT id FROM customers WHERE city === 'Pune'", "postgresql", "operator", "error", "fix",
     "SELECT id FROM customers WHERE city = 'Pune'"),
    ("op_update", "", "UPDATE orders SET shipping == 0 WHERE id == 2", "mariadb", "operator", "error", "change",
     "UPDATE orders SET shipping = 0 WHERE id = 2"),
    ("op_sqlite_ok", "", "SELECT id FROM orders WHERE status == 'paid'", "sqlite", "operator", None, "quiet", None),
    ("op_named_arg", "", "SELECT make_interval(days => 3) AS gap", "postgresql", "operator", None, "quiet", None),
    # ---- AND/OR: the words name the precedence bug -> the brackets are the fix; otherwise offered
    ("ao_said", "fix the operator precedence bug",
     "SELECT id FROM products WHERE category_id = 2 OR category_id = 3 AND price > 20000", "mariadb", "and_or", "wrong", "fix",
     "SELECT id FROM products WHERE (category_id = 2 OR category_id = 3) AND price > 20000"),
    ("ao_hinglish", "isme bracket galat lage hain, theek karo",
     "SELECT id FROM products WHERE supplier_id = 1 OR supplier_id = 2 AND stock > 4", "postgresql", "and_or", "wrong", "fix",
     "SELECT id FROM products WHERE (supplier_id = 1 OR supplier_id = 2) AND stock > 4"),
    ("ao_hindi", "AND और OR की प्राथमिकता ठीक करो",
     "SELECT id FROM orders WHERE status = 'paid' OR status = 'shipped' AND amount > 400", "mariadb", "and_or", "wrong", "fix",
     "SELECT id FROM orders WHERE (status = 'paid' OR status = 'shipped') AND amount > 400"),
    ("ao_unsaid", "", "SELECT id FROM products WHERE category_id = 2 OR category_id = 3 AND price > 20000", "postgresql", "and_or",
     "check", "offer", "SELECT id FROM products WHERE (category_id = 2 OR category_id = 3) AND price > 20000"),
    ("ao_with_order", "fix the clause order and the precedence",
     "SELECT id, price FROM products ORDER BY price DESC WHERE category_id = 2 OR category_id = 3 AND price > 20000",
     "postgresql", "clause_order", "error", "fix",
     "SELECT id, price FROM products WHERE (category_id = 2 OR category_id = 3) AND price > 20000 ORDER BY price DESC"),
    # ---- a LEFT JOIN undone by WHERE: every row wanted -> the condition into ON; INNER JOIN offered too
    ("lj_said", "show every city, including ones with no paid orders",
     "SELECT c.city, COUNT(o.id) AS n FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.status = 'paid' "
     "GROUP BY c.city", "mariadb", "join", "wrong", "fix",
     "SELECT c.city, COUNT(o.id) AS n FROM customers c LEFT JOIN (SELECT * FROM orders WHERE status = 'paid') o "
     "ON o.customer_id = c.id GROUP BY c.city"),
    ("lj_hinglish", "zero wale customers bhi chahiye",
     "SELECT c.id, COUNT(o.id) AS n FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.amount > 1000 "
     "GROUP BY c.id", "postgresql", "join", "wrong", "fix",
     "SELECT c.id, COUNT(o.id) AS n FROM customers c LEFT JOIN (SELECT * FROM orders WHERE amount > 1000) o "
     "ON o.customer_id = c.id GROUP BY c.id"),
    ("lj_left_cond_stays", "all Pune customers, even without paid orders",
     "SELECT c.id, o.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.status = 'paid' AND c.city = 'Pune'",
     "mariadb", "join", "wrong", "fix",
     "SELECT c.id, o.id FROM customers c LEFT JOIN (SELECT * FROM orders WHERE status = 'paid') o ON o.customer_id = c.id "
     "WHERE c.city = 'Pune'"),
    ("lj_unsaid_on", "", "SELECT c.city, COUNT(o.id) AS n FROM customers c LEFT JOIN orders o ON o.customer_id = c.id "
     "WHERE o.status = 'paid' GROUP BY c.city", "postgresql", "join", "check", "alt:on",
     "SELECT c.city, COUNT(o.id) AS n FROM customers c LEFT JOIN (SELECT * FROM orders WHERE status = 'paid') o "
     "ON o.customer_id = c.id GROUP BY c.city"),
    ("lj_said_inner", "list all categories including empty ones",
     "SELECT g.name, COUNT(p.id) AS n FROM categories g LEFT JOIN products p ON p.category_id = g.id WHERE p.stock > 0 "
     "GROUP BY g.name", "mariadb", "join", "wrong", "alt:inner",
     "SELECT g.name, COUNT(p.id) AS n FROM categories g JOIN products p ON p.category_id = g.id WHERE p.stock > 0 GROUP BY g.name"),
    ("lj_anti_quiet", "", "SELECT c.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.id IS NULL",
     "postgresql", "join", None, "quiet", None),
    # ---- columns that aren't grouped: one row per group / the column grouped too / the row holding each group's MAX or MIN
    ("gl_drop", "", "SELECT p.category_id, p.name, MAX(p.price) FROM products p GROUP BY p.category_id", "mariadb", "group",
     "error", "fix", "SELECT p.category_id, MAX(p.price) FROM products p GROUP BY p.category_id"),
    ("gl_add", "", "SELECT p.category_id, p.name, MAX(p.price) FROM products p GROUP BY p.category_id", "postgresql", "group",
     "error", "alt:add", "SELECT p.category_id, p.name, MAX(p.price) FROM products p GROUP BY p.category_id, p.name"),
    ("gl_extreme_said", "which product is the most expensive in each category?",
     "SELECT p.category_id, p.name, MAX(p.price) AS top FROM products p GROUP BY p.category_id", "mariadb", "group", "error", "fix",
     "SELECT p.category_id, p.name, p.price FROM products p WHERE p.category_id IS NOT NULL AND NOT EXISTS "
     "(SELECT 1 FROM products q WHERE q.category_id = p.category_id AND q.price > p.price)"),
    ("gl_join_extreme", "", "SELECT g.name, p.name, MIN(p.price) FROM categories g JOIN products p ON p.category_id = g.id "
     "GROUP BY g.name", "postgresql", "group", "error", "alt:extreme",
     "SELECT g.name, p.name, p.price FROM categories g JOIN products p ON p.category_id = g.id WHERE NOT EXISTS "
     "(SELECT 1 FROM categories g2 JOIN products q ON q.category_id = g2.id WHERE g2.name = g.name AND q.price < p.price)"),
    ("gl_where_kept", "har supplier mein sabse sasta product kaun sa hai",
     "SELECT p.supplier_id, p.name, MIN(p.price) FROM products p WHERE p.stock > 0 GROUP BY p.supplier_id", "mariadb", "group",
     "error", "fix",
     "SELECT p.supplier_id, p.name, p.price FROM products p WHERE p.stock > 0 AND p.supplier_id IS NOT NULL AND NOT EXISTS "
     "(SELECT 1 FROM products q WHERE q.stock > 0 AND q.supplier_id = p.supplier_id AND q.price < p.price)"),
    ("gl_quiet", "", "SELECT g.id, g.name, COUNT(p.id) AS n FROM categories g JOIN products p ON p.category_id = g.id "
     "GROUP BY g.id, g.name", "postgresql", "group", None, "quiet", None),
    # ---- the rest of the X1 families, now returned in the reply too
    ("al_where", "", "SELECT id, amount * 2 AS dbl FROM orders WHERE dbl > 1000", "mariadb", "alias", "error", "fix",
     "SELECT id, amount * 2 AS dbl FROM orders WHERE amount * 2 > 1000"),
    ("eq_null", "", "SELECT id FROM customers WHERE phone = NULL", "postgresql", "null", "wrong", "fix",
     "SELECT id FROM customers WHERE phone IS NULL"),
    # MySQL refuses a change whose subquery reads the changed table (1093); the wrapped form changes the same rows as the
    # original does where it is accepted (MariaDB 10.3+, PostgreSQL), each on its own collation
    ("sr_delete_keep_one", "", "DELETE FROM customers WHERE id NOT IN (SELECT MIN(id) FROM customers GROUP BY email)", "mysql",
     "same_table", "check", "change", "DELETE FROM customers WHERE id NOT IN (SELECT MIN(id) FROM customers GROUP BY email)"),
    ("sr_update_avg", "", "UPDATE products SET stock = 0 WHERE price > (SELECT AVG(price) FROM products)", "mysql",
     "same_table", "check", "change", "UPDATE products SET stock = 0 WHERE price > 26228.57"),
]


@pytest.fixture(scope="module")
def engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


def _state(eng, table):
    return S.rows(eng, f"SELECT * FROM {table}")


def _table(q):
    return re.match(r"(?is)\s*(?:update|delete\s+from)\s+(\w+)", q).group(1)


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)
    assert {p[6].split(":")[0] for p in P} >= {"fix", "change", "alt", "offer", "quiet"}


@pytest.mark.parametrize("pid,words,q,engine,topic,kind,proof,truth", P, ids=[p[0] for p in P])
def test_tonights_problems(engines, pid, words, q, engine, topic, kind, proof, truth):
    res = analyze_sql(f"{q};\n{words}" if words else q, engine=engine)
    c = res["correct"]
    fs = c["findings"]
    if proof == "quiet":
        assert not [f for f in fs if f["kind"] in ("error", "wrong") or f["topic"] == topic], (pid, [(f["topic"], f["kind"]) for f in fs])
        return
    assert any(f["topic"] == topic and f["kind"] == kind for f in fs), (pid, [(f["topic"], f["kind"]) for f in fs])
    if proof == "offer":
        assert kind == "check" and not any(f["kind"] in ("error", "wrong") for f in fs)
        got = c["corrected"]
    elif proof.startswith("alt:"):
        got = next((a["sql"] for a in c.get("alternatives") or [] if a["key"] == proof[4:]), None)
        assert got, (pid, [a["key"] for a in c.get("alternatives") or []])
    else:
        got = c["corrected"]
    assert got, pid
    got = got.rstrip().rstrip(";")
    if proof == "change":
        for name, eng in engines.items():
            table = _table(truth)
            S.build(eng)
            eng.run(truth)
            want = _state(eng, table)
            S.build(eng)
            eng.run(got)
            assert _state(eng, table) == want, (pid, name, got)
            S.build(eng)
        return
    if proof == "change_as_pg":                           # MariaDB runs the corrected form, PostgreSQL the original: same table
        my, pg = engines["mysql"], engines["postgres"]
        table = _table(q)
        S.build(my)
        S.build(pg)
        try:
            my.run(got)
            pg.run(q)
            assert _state(my, table) == _state(pg, table), (pid, got)
        finally:
            S.build(my)
            S.build(pg)
        return
    ordered = bool(re.search(r"(?i)\blimit\b", truth))
    for name, eng in engines.items():
        assert S.rows(eng, got, ordered) == S.rows(eng, truth, ordered), (pid, name, got)


@pytest.mark.parametrize("pid", ["co_order_where", "co_group_where", "op_double", "op_le", "co_update_set"])
def test_the_broken_form_really_is_refused_on_both(engines, pid):
    """The LAB for the two sure families: what the person wrote stops on MariaDB and on PostgreSQL."""
    q = next(p[2] for p in P if p[0] == pid)
    for eng in engines.values():
        with pytest.raises(Exception):
            eng.run(q)
        eng.rollback()


# ---------------------------------------------------------------- through the chat, the way a person asks
CHAT = [
    ("fix this mysql query:\nSELECT id, amount FROM orders ORDER BY amount DESC WHERE status = 'paid';",
     "SELECT id, amount FROM orders WHERE status = 'paid' ORDER BY amount DESC", "Corrected query:"),
    ("postgres query theek karo, precedence ka bug hai:\nSELECT id FROM products WHERE category_id = 2 OR category_id = 3 AND "
     "price > 20000;", "SELECT id FROM products WHERE (category_id = 2 OR category_id = 3) AND price > 20000", "Sahi query:"),
    ("Why does this MariaDB query drop the cities with no paid orders? I want every city:\nSELECT c.city, COUNT(o.id) AS n FROM "
     "customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.status = 'paid' GROUP BY c.city;",
     "SELECT c.city, COUNT(o.id) AS n FROM customers c LEFT JOIN (SELECT * FROM orders WHERE status = 'paid') o "
     "ON o.customer_id = c.id GROUP BY c.city", "Corrected query:"),
    ("Why is this failing with ONLY_FULL_GROUP_BY in MySQL? Rewrite it:\nSELECT p.category_id, p.name, MAX(p.price) FROM products p "
     "GROUP BY p.category_id;", "SELECT p.category_id, MAX(p.price) FROM products p GROUP BY p.category_id", "Corrected query:"),
    ("PostgreSQL: SELECT id FROM orders WHERE status == 'paid';", "SELECT id FROM orders WHERE status = 'paid'", "Corrected query:"),
]


def _statements(reply: str) -> list[str]:
    """Every runnable statement in a reply: a paragraph that starts with SELECT / UPDATE / DELETE / WITH, up to its ';'."""
    out = []
    for para in reply.split("\n\n"):
        para = para.strip()
        if re.match(r"(?i)(select|update|delete|with)\b", para) and ";" in para:
            out.append(re.sub(r"--[^\n]*", "", para).split(";")[0])
    return out


@pytest.mark.parametrize("text,truth,head", CHAT, ids=[f"chat{k}" for k in range(len(CHAT))])
def test_the_reply_itself_has_the_corrected_statement(client, ml_mode, engines, text, truth, head):
    from tests.test_ml import say

    r = say(client, text)
    reply = r["reply"]
    assert head in reply, reply
    stmts = _statements(reply)
    assert stmts, reply
    eng_ok = []
    for s in stmts:
        try:
            eng_ok.append(all(S.rows(e, s) == S.rows(e, truth) for e in engines.values()))
        except Exception:  # noqa: BLE001 - an alternative reading may return other rows; the first match counts
            for e in engines.values():
                e.rollback()
            eng_ok.append(False)
    assert any(eng_ok), (reply, stmts)


def test_a_change_is_only_a_preview_in_the_reply(client, ml_mode, engines):
    from tests.test_ml import say

    r = say(client, "MariaDB: UPDATE products SET stock == stock + 5 WHERE stock = 0;")
    assert "a preview: I don't run it" in r["reply"] and "UPDATE products SET stock = stock + 5 WHERE stock = 0;" in r["reply"]
    assert "Before running it, see the rows it will change" in r["reply"]      # the read-only look first
