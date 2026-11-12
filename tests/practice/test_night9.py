"""Night 9 practice bank: locks, concurrency and large data.

Each problem: what the person says (the words carry the size or the concurrency), the statement they wrote, the topic DBaba must
find, and how the offer is proven:
- batch_change: the batched form, run again and again (with batches of 2 here, the tables are small) until it changes 0 rows,
  leaves exactly the table the single statement leaves, on MariaDB and on PostgreSQL;
- skip_locked: with no other worker, FOR UPDATE SKIP LOCKED returns the rows FOR UPDATE returns;
- n_plus_one: the one combined statement returns (or changes) exactly what the separate statements did.
The LAB below is reproduced live with two connections on real lab9_ tables: a deadlock (1213 / 40P01), a lock wait timeout
(1205 / 55P03), MariaDB locking every row an unindexed UPDATE reads (PostgreSQL doesn't), SKIP LOCKED vs FOR UPDATE vs NOWAIT,
a gap lock (MariaDB only) and a plain read that doesn't wait (MVCC).
"""

import re
import threading
import time

import pytest

from app.agents import sqllock
from app.agents.sql import analyze_sql
from tests.fuzz.db import MYSQL, PG, Engine
from tests.practice import schema as S

BIG = "the table has 30 million rows and this times out"

# (id, words, statement, topic, proof: 'batch' | 'same' | None, database said)
P = [
    # ---- one huge change -> batches, each its own short transaction
    ("bc_returns", BIG, "DELETE FROM returns WHERE returned_on < '2024-07-01'", "batch_change", "batch", None),
    ("bc_cancelled", "orders has millions of rows, deleting cancelled ones locks everything", "DELETE FROM orders WHERE status = 'cancelled'",
     "batch_change", "batch", "mariadb"),
    ("bc_pg", "payments table is huge (postgres)", "DELETE FROM payments WHERE method IS NULL OR amount < 1000", "batch_change", "batch",
     "postgresql"),
    ("bc_archive", "archive old orders, the table is huge", "UPDATE orders SET status = 'archived' WHERE order_date < '2024-07-01'",
     "batch_change", "batch", None),
    ("bc_archive_pg", "archive old orders on postgres, 50 lakh rows", "UPDATE orders SET status = 'archived' WHERE order_date < '2024-07-01'",
     "batch_change", "batch", "postgresql"),
    ("bc_stock", "zero out stock for old products, it times out", "UPDATE products SET stock = 0 WHERE price < 1000", "batch_change", "batch",
     "mariadb"),
    ("bc_null", "clear the phone of these customers, millions of rows", "UPDATE customers SET phone = NULL WHERE city = 'Delhi'",
     "batch_change", "batch", None),
    ("bc_two", "mark and note old shipped orders, huge table", "UPDATE orders SET status = 'done', discount = 0 WHERE status = 'shipped'",
     "batch_change", "batch", "postgresql"),
    ("bc_hinglish", "table mein lakhs rows hain, delete atak jata hai", "DELETE FROM order_items WHERE quantity = 1 AND discount IS NULL",
     "batch_change", "batch", None),
    ("bc_alias", "huge table, update by alias", "UPDATE employees e SET commission = 0 WHERE e.department_id = 1", "batch_change", "batch",
     "mariadb"),
    ("bc_expr", "prices go up by 10% on a huge table", "UPDATE products SET price = price * 1.1 WHERE category_id = 2", "batch_change", None, None),
    ("bc_returns_pg", "postgres, the returns table has crores of rows", "DELETE FROM returns WHERE reason IS NULL", "batch_change", "batch",
     "postgresql"),
    ("bc_suppliers", "supplier table is very big and the update hangs", "UPDATE suppliers SET country = 'India' WHERE country = 'UAE'",
     "batch_change", "batch", "mariadb"),
    # ---- queue workers -> FOR UPDATE SKIP LOCKED
    ("sk_next", "several workers pick the next pending order", "SELECT id FROM orders WHERE status = 'pending' ORDER BY id LIMIT 1 FOR UPDATE",
     "skip_locked", "same", None),
    ("sk_batch", "each worker takes 2 jobs from the queue", "SELECT id, amount FROM orders WHERE status = 'paid' ORDER BY id LIMIT 2 FOR UPDATE",
     "skip_locked", "same", "mariadb"),
    ("sk_pg", "background jobs, parallel workers (postgres)", "SELECT id FROM payments WHERE method = 'upi' ORDER BY paid_at, id LIMIT 1 FOR UPDATE",
     "skip_locked", "same", "postgresql"),
    ("sk_hinglish", "do worker ek saath agla job lete hain", "SELECT id FROM orders WHERE status = 'paid' ORDER BY order_date, id LIMIT 1 FOR UPDATE",
     "skip_locked", "same", None),
    ("sk_of", "workers pick the next task, lock only the order (postgres)",
     "SELECT o.id FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune' ORDER BY o.id LIMIT 1 FOR UPDATE OF o",
     "skip_locked", "same", "postgresql"),
    ("sk_consumers", "consumers read the next item", "SELECT id FROM order_items WHERE discount IS NULL ORDER BY id LIMIT 3 FOR UPDATE",
     "skip_locked", "same", None),
    ("sk_hindi", "कई workers एक साथ अगला job लेते हैं", "SELECT id FROM orders WHERE status = 'shipped' ORDER BY id LIMIT 1 FOR UPDATE",
     "skip_locked", "same", None),
    # ---- the same statement per value (N+1) -> one statement
    ("np_select", "", "SELECT first_name FROM customers WHERE id = 1; SELECT first_name FROM customers WHERE id = 3; "
                      "SELECT first_name FROM customers WHERE id = 6;", "n_plus_one", "same", None),
    ("np_items", "", "SELECT product_id, quantity FROM order_items WHERE order_id = 1;\nSELECT product_id, quantity FROM order_items WHERE order_id = 2;\n"
                     "SELECT product_id, quantity FROM order_items WHERE order_id = 3;\nSELECT product_id, quantity FROM order_items WHERE order_id = 1;",
     "n_plus_one", "same", None),
    ("np_text", "", "SELECT id FROM customers WHERE city = 'Pune'; SELECT id FROM customers WHERE city = 'Delhi'; SELECT id FROM customers WHERE city = 'Goa'",
     "n_plus_one", "same", None),
    ("np_alias", "", "SELECT o.id, o.amount FROM orders o WHERE o.customer_id = 1; SELECT o.id, o.amount FROM orders o WHERE o.customer_id = 2; "
                     "SELECT o.id, o.amount FROM orders o WHERE o.customer_id = 3", "n_plus_one", "same", None),
    ("np_and", "", "SELECT id FROM orders WHERE status = 'paid' AND customer_id = 1; SELECT id FROM orders WHERE status = 'paid' AND customer_id = 2; "
                   "SELECT id FROM orders WHERE status = 'paid' AND customer_id = 3", "n_plus_one", "same", None),
    ("np_update", "", "UPDATE orders SET status = 'paid' WHERE id = 2; UPDATE orders SET status = 'paid' WHERE id = 6; UPDATE orders SET status = 'paid' WHERE id = 8",
     "n_plus_one", "same", None),
    ("np_delete", "", "DELETE FROM returns WHERE id = 1; DELETE FROM returns WHERE id = 2; DELETE FROM returns WHERE id = 3", "n_plus_one", "same", None),
    ("np_insert", "", "INSERT INTO departments (id, name, location) VALUES (5, 'Ops', 'Pune'); INSERT INTO departments (id, name, location) VALUES "
                      "(6, 'Legal', NULL); INSERT INTO departments (id, name, location) VALUES (7, 'Audit', 'Delhi')", "n_plus_one", "same", None),
    ("np_update_text", "", "UPDATE customers SET city = 'Pune' WHERE email = 'riya@mail.test'; UPDATE customers SET city = 'Pune' WHERE email = "
                           "'tara@mail.test'; UPDATE customers SET city = 'Pune' WHERE email = 'sara_a@mail.test'", "n_plus_one", "same", None),
    ("np_sku", "", "SELECT name, price FROM products WHERE sku = 'PH_100'; SELECT name, price FROM products WHERE sku = 'LP_200'; "
                   "SELECT name, price FROM products WHERE sku = 'BK_001'", "n_plus_one", "same", None),
]

# what the lab saw with two sessions (re-run below): MariaDB 10.11 / 11 and PostgreSQL 16
LAB = {
    "deadlock": ("1213", "40P01"),        # two sessions update rows 1 and 2 in opposite orders: one is rolled back
    "lock_wait": ("1205", "55P03"),       # the second session waits for the first's row until the lock timeout
    "unindexed_update": ("1205", "ok"),   # MariaDB: an UPDATE with no index on its WHERE blocks a change of another row
    "for_update_queue": ("1205", "55P03"),  # the second worker waits for the first worker's row
    "skip_locked": ("2", "2"),            # ... with SKIP LOCKED it takes job 2 at once
    "nowait": ("1205", "55P03"),          # NOWAIT fails at once
    "gap_lock": ("1205", "ok"),           # MariaDB REPEATABLE READ locks the gap: an INSERT into the range waits
    "mvcc_read": ("100", "100"),          # a plain read sees the committed value while a writer holds 999
}


def _engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


@pytest.fixture(scope="module")
def engines():
    return _engines()


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)


def _table_of(sql):
    m = re.match(r"\s*(?:delete\s+from|update)\s+(\w+)", sql, re.I) or re.search(r"\bfrom\s+(\w+)", sql, re.I) or \
        re.search(r"\binsert\s+into\s+(\w+)", sql, re.I)
    return m.group(1)


def _state(eng, table):
    return sorted(tuple(S.norm(v) for v in r) for r in eng.run(f"SELECT * FROM {table}"))


def _run_batched(eng, batched_sql, limit=2):
    """Run the batched statement until it changes 0 rows (each run its own statement in autocommit)."""
    sql = re.sub(rf"LIMIT {sqllock.BATCH}\b", f"LIMIT {limit}", batched_sql)
    for _ in range(100):
        cur = eng.conn.cursor()
        try:
            cur.execute(sql)
            n = cur.rowcount
        finally:
            cur.close()
        if not n:
            return
    raise AssertionError(f"the batches never ended: {sql}")


@pytest.mark.parametrize("pid,words,stmt,topic,proof,said", P, ids=[p[0] for p in P])
def test_dbaba_finds_and_its_offer_does_the_same_work(engines, pid, words, stmt, topic, proof, said):
    text = f"{stmt}" + (f"\n\n{words}" if not stmt.rstrip().endswith(";") else f"\n{words}") if words else stmt
    if words and not stmt.rstrip().endswith(";"):
        text = f"{stmt};\n\n{words}"
    res = analyze_sql(text, engine=said)
    found = [f for f in res["correct"]["findings"] if f["topic"] == topic]
    assert found, (pid, [(f["topic"], f["title_en"]) for f in res["correct"]["findings"]])
    assert all(f["kind"] == "check" for f in found)
    assert all((f.get("basis") or {}).get("id") == "locking" for f in found), (pid, "every finding names its fundamental")
    if proof is None:
        assert not res.get("suggested"), pid
        return
    offered = res["correct"]["corrected"]
    assert offered, pid
    if topic != "n_plus_one":
        assert offered == res.get("suggested") and res.get("suggested_kind") == "speed", (pid, res.get("suggested"))
    else:
        assert offered == res.get("rewritten"), pid
    table = _table_of(stmt)
    for name, eng in engines.items():
        fam = "postgresql" if name == "postgres" else "mysql"
        mine = offered
        if topic == "batch_change":
            if said is None:
                assert offered.rstrip(";") == sqllock.batched(stmt, "mysql")["sql"]
            if (said == "postgresql") != (name == "postgres"):
                mine = sqllock.batched(stmt, fam)["sql"]       # (the chat asks first, rule A); each engine's own form is proven
        if proof == "batch":
            S.build(eng)
            for one in [s for s in stmt.split(";") if s.strip()]:
                eng.run(one)
            want = _state(eng, table)
            S.build(eng)
            _run_batched(eng, mine.rstrip(";"))
            assert _state(eng, table) == want, (pid, name, mine)
        elif topic == "skip_locked":
            S.build(eng)
            if name == "mysql" and re.search(r"\bfor\s+update\s+of\b", stmt, re.I):
                continue                                        # FOR UPDATE OF is PostgreSQL's (and MySQL 8's)
            assert S.rows(eng, mine.rstrip(";"), ordered=True) == S.rows(eng, stmt, ordered=True), (pid, name)
        else:                                                   # n_plus_one
            S.build(eng)
            parts = [s for s in stmt.split(";") if s.strip()]
            if re.match(r"\s*select", stmt, re.I):
                want = set()
                for one in parts:
                    want |= set(S.rows(eng, one))
                got = S.rows(eng, mine)
                cols = len(next(iter(want))) if want else 0
                assert {r[-cols:] for r in got} == want, (pid, name, mine)   # the key column is put first
            else:
                for one in parts:
                    eng.run(one)
                want = _state(eng, table)
                S.build(eng)
                eng.run(mine)
                assert _state(eng, table) == want, (pid, name, mine)
            S.build(eng)


def test_quiet_without_the_words_and_on_safe_statements():
    for q in ["DELETE FROM returns WHERE returned_on < '2024-07-01'",
              "UPDATE orders SET status = 'archived' WHERE order_date < '2024-07-01'",
              "SELECT id FROM orders WHERE status = 'pending' ORDER BY id LIMIT 1 FOR UPDATE",
              "SELECT id FROM orders WHERE status = 'pending' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED;\n\nseveral workers",
              "SELECT id FROM orders WHERE status = 'pending' ORDER BY id LIMIT 1 FOR UPDATE NOWAIT;\n\nqueue workers",
              "SELECT first_name FROM customers WHERE id = 1; SELECT last_name FROM customers WHERE id = 2; SELECT city FROM customers WHERE id = 3",
              "SELECT first_name FROM customers WHERE id = 1; SELECT first_name FROM customers WHERE id = 2",
              "SELECT id FROM orders WHERE id = 1 OR customer_id = 5; SELECT id FROM orders WHERE id = 2 OR customer_id = 5; "
              "SELECT id FROM orders WHERE id = 3 OR customer_id = 5"]:
        res = analyze_sql(q)
        bad = [f["topic"] for f in res["correct"]["findings"] if f["topic"] in ("batch_change", "skip_locked", "n_plus_one")]
        assert not bad, (q, bad)


def test_the_batched_update_skips_rows_already_done():
    out = sqllock.batched("UPDATE orders SET status = 'archived', note = NULL WHERE order_date < '2024-07-01'", "mysql")["sql"]
    assert "(status <> 'archived' OR status IS NULL) OR note IS NOT NULL" in out and out.endswith("LIMIT 5000")
    out = sqllock.batched("DELETE FROM logs WHERE created_at < '2024-01-01'", "postgresql")["sql"]
    assert out == "DELETE FROM logs WHERE ctid IN (SELECT ctid FROM logs WHERE (created_at < '2024-01-01') LIMIT 5000)"
    assert sqllock.batched("UPDATE t SET a = a + 1 WHERE id > 5", "mysql") is None


# ------------------------------------------------------------------------------------------------ with the table structure
DESIGN = "CREATE TABLE accounts (id INT PRIMARY KEY, owner VARCHAR(20), bal INT, KEY ix_bal (bal));"


@pytest.mark.parametrize("said,word", [("mariadb", "locks every row"), ("postgresql", "reads all of"), (None, "locks every row")])
def test_a_change_without_an_index_locks_everything_on_innodb(said, word):
    res = analyze_sql("UPDATE accounts SET bal = 0 WHERE owner = 'a'", DESIGN, engine=said)
    f = [x for x in res["findings"] if x.get("topic") == "lock_scan"]
    assert f and word in f[0]["title_en"] and f[0]["basis"]["id"] == "locking", [x["title_en"] for x in res["findings"]]
    res = analyze_sql("UPDATE accounts SET owner = 'z' WHERE bal = 5", DESIGN, engine=said)
    assert not [x for x in res["findings"] if x.get("topic") == "lock_scan"]


# ------------------------------------------------------------------------------------------------ reading a deadlock report
INNODB = """------------------------
LATEST DETECTED DEADLOCK
------------------------
2026-11-12 16:40:32 0x7fbd600776c0
*** (1) TRANSACTION:
TRANSACTION 33637114, ACTIVE 1 sec starting index read
mysql tables in use 1, locked 1
LOCK WAIT 3 lock struct(s), heap size 1128, 2 row lock(s), undo log entries 1
MariaDB thread id 36, OS thread handle 140451336648384, query id 109 localhost 127.0.0.1 app Updating
UPDATE accounts SET bal = bal + 10 WHERE id = 1
*** WAITING FOR THIS LOCK TO BE GRANTED:
RECORD LOCKS space id 12900 page no 3 n bits 320 index PRIMARY of table `shop`.`accounts` trx id 33637114 lock_mode X locks rec but not gap waiting
*** CONFLICTING WITH:
RECORD LOCKS space id 12900 page no 3 n bits 320 index PRIMARY of table `shop`.`accounts` trx id 33637113 lock_mode X locks rec but not gap

*** (2) TRANSACTION:
TRANSACTION 33637113, ACTIVE 1 sec starting index read
mysql tables in use 1, locked 1
LOCK WAIT 3 lock struct(s), heap size 1128, 2 row lock(s), undo log entries 1
MariaDB thread id 35, OS thread handle 140451336955584, query id 108 localhost 127.0.0.1 app Updating
UPDATE accounts SET bal = bal + 10 WHERE id = 2
*** WAITING FOR THIS LOCK TO BE GRANTED:
RECORD LOCKS space id 12900 page no 3 n bits 320 index PRIMARY of table `shop`.`accounts` trx id 33637113 lock_mode X locks rec but not gap waiting
*** WE ROLL BACK TRANSACTION (2)"""

PG_DEADLOCK = """ERROR:  deadlock detected
DETAIL:  Process 552 waits for ShareLock on transaction 232448; blocked by process 553.
Process 553 waits for ShareLock on transaction 232447; blocked by process 552.
HINT:  See server log for query details.
CONTEXT:  while updating tuple (0,2) in relation "accounts\""""


@pytest.mark.parametrize("text,eng,victim", [(INNODB, "mysql", "(transaction 2)"), (PG_DEADLOCK, "postgresql", "")])
def test_a_deadlock_report_is_read(text, eng, victim):
    d = sqllock.read_deadlock(text)
    assert d and d["engine"] == eng and d["tables"] == ["accounts"]
    assert "same order" in d["en"] and "retry" in d["en"] and victim in d["en"]
    if eng == "mysql":
        assert d["statements"] == ["UPDATE accounts SET bal = bal + 10 WHERE id = 1", "UPDATE accounts SET bal = bal + 10 WHERE id = 2"]
        assert not d["gap"]
    assert "ek hi kram" in d["hi"]


def test_a_deadlock_report_in_the_chat(client, ml_mode):
    from tests.test_ml import say

    client.post("/api/new-chat")
    r = say(client, INNODB)
    assert r["relation"] != "concept" and "same order" in r["reply"] and "UPDATE accounts SET bal = bal + 10 WHERE id = 1" in r["reply"], \
        r["reply"][:500]


# ------------------------------------------------------------------------------------------------ the live lab (two sessions)
def _pair(cfg):
    a, b = Engine(cfg).open(), Engine(cfg).open()
    for e in (a, b):
        e.run("SET SESSION innodb_lock_wait_timeout = 2" if e.kind == "mysql" else "SET lock_timeout = '2s'")
    return a, b


def _code(ex):
    return str(getattr(ex, "sqlstate", None) if getattr(ex, "sqlstate", None) and not str(getattr(ex, "args", [""])[0]).isdigit()
               else ex.args[0])


@pytest.fixture(scope="module")
def lab(engines):
    out = {}
    for cfg in (MYSQL, PG):
        a, b = _pair(cfg)
        for t in ("lab9_jobs", "lab9_acc"):
            a.run(f"DROP TABLE IF EXISTS {t}")
        a.run("CREATE TABLE lab9_acc (id INT PRIMARY KEY, owner VARCHAR(20), bal INT)")
        a.run("CREATE TABLE lab9_jobs (id INT PRIMARY KEY, status VARCHAR(10))")
        out[cfg["type"]] = (a, b)
    yield out
    for a, b in out.values():
        for e in (a, b):
            e.rollback()
        for t in ("lab9_jobs", "lab9_acc"):
            a.run(f"DROP TABLE IF EXISTS {t}")


def _reset(a):
    a.rollback()
    a.run("DELETE FROM lab9_acc")
    a.run("DELETE FROM lab9_jobs")
    a.run("INSERT INTO lab9_acc VALUES (1, 'a', 100), (2, 'b', 100), (3, 'c', 100), (4, 'd', 100)")
    a.run("INSERT INTO lab9_jobs VALUES (1, 'new'), (2, 'new'), (3, 'new')")


def _try(e, sql):
    try:
        r = e.run(sql)
        return "ok" if not isinstance(r, (list, tuple)) else (S.norm(r[0][0]) if r else "none")
    except Exception as ex:  # noqa: BLE001
        e.rollback()
        return _code(ex)


def _outcomes(a, b):
    got = {}
    # deadlock: opposite orders
    _reset(a)
    a.begin(); b.begin()
    a.run("UPDATE lab9_acc SET bal = bal - 10 WHERE id = 1")
    b.run("UPDATE lab9_acc SET bal = bal - 10 WHERE id = 2")
    res = {}
    th = threading.Thread(target=lambda: res.__setitem__("a", _try(a, "UPDATE lab9_acc SET bal = bal + 10 WHERE id = 2")))
    th.start()
    time.sleep(0.5)
    res["b"] = _try(b, "UPDATE lab9_acc SET bal = bal + 10 WHERE id = 1")
    th.join()
    a.rollback(); b.rollback()
    got["deadlock"] = next(v for v in res.values() if v != "ok")
    # lock wait
    _reset(a)
    a.begin(); a.run("UPDATE lab9_acc SET bal = 0 WHERE id = 3"); b.begin()
    got["lock_wait"] = _try(b, "UPDATE lab9_acc SET bal = 1 WHERE id = 3")
    a.rollback(); b.rollback()
    # an UPDATE with no index on its WHERE, and another session changing another row
    a.begin(); a.run("UPDATE lab9_acc SET bal = 5 WHERE owner = 'a'"); b.begin()
    got["unindexed_update"] = _try(b, "UPDATE lab9_acc SET bal = 6 WHERE owner = 'd'")
    a.rollback(); b.rollback()
    # queue workers
    a.begin(); a.run("SELECT id FROM lab9_jobs WHERE status = 'new' ORDER BY id LIMIT 1 FOR UPDATE"); b.begin()
    got["for_update_queue"] = _try(b, "SELECT id FROM lab9_jobs WHERE status = 'new' ORDER BY id LIMIT 1 FOR UPDATE")
    b.rollback(); b.begin()
    got["skip_locked"] = _try(b, "SELECT id FROM lab9_jobs WHERE status = 'new' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED")
    b.rollback(); b.begin()
    got["nowait"] = _try(b, "SELECT id FROM lab9_jobs WHERE status = 'new' ORDER BY id LIMIT 1 FOR UPDATE NOWAIT")
    b.rollback(); a.rollback()
    # gap lock (REPEATABLE READ)
    a.run("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ" if a.kind == "mysql" else
          "SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    a.begin(); a.run("UPDATE lab9_acc SET bal = 7 WHERE id > 2"); b.begin()
    got["gap_lock"] = _try(b, "INSERT INTO lab9_acc VALUES (10, 'k', 1)")
    b.rollback(); a.rollback()
    # a plain read while a writer holds the row
    a.begin(); a.run("UPDATE lab9_acc SET bal = 999 WHERE id = 4")
    got["mvcc_read"] = _try(b, "SELECT bal FROM lab9_acc WHERE id = 4")
    a.rollback()
    return got


def test_the_lab_table_still_holds(lab):
    my = _outcomes(*lab["mysql"])
    pg = _outcomes(*lab["postgres"])
    for k, (want_my, want_pg) in LAB.items():
        assert my[k] == want_my, (k, "mariadb", my[k])
        assert pg[k] == want_pg, (k, "postgresql", pg[k])


def test_every_rule_records_where_it_was_proven():
    import json

    from app.agents.sqlread import fundamentals

    assert all(set(v) >= {"mariadb", "postgresql"} for v in sqllock.PROVEN.values())
    assert set(sqllock.PROVEN) <= set(fundamentals()["fundamentals"]["locking"]["topics"])
    for eng in ("mysql", "mariadb", "postgresql"):
        rules = json.load(open(f"app/dbknow/dialects/{eng}.json"))["query_rules"]
        for key in ("skip_locked", "lock_scan", "gap_locks", "deadlock"):
            assert rules[key]["proven"] and rules[key]["says"], (eng, key)
