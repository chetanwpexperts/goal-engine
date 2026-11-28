"""E1 (SQLite), night 4 of 4: transactions and locking, read as scripts; and a practice exam across the four SQLite nights.

The lab (tests/practice/txnlab.py) runs a script the way each database's command line does (autocommit on, an error reported
and the next statement run), then reads what was SAVED from a second connection. Tonight's parts:
- every txn_rules lab line in the three dialect files (sqlite.json, mysql.json, postgresql.json) is run again here and must
  give its stored result (MariaDB and PostgreSQL in CI; SQLite always);
- the two-connection facts that a script can't show: a plain BEGIN that reads and then writes fails at once when another
  connection wrote in between, a busy timeout or not, while BEGIN IMMEDIATE waits; the rollback journal vs WAL; 500 INSERTs
  with and without a transaction; 'database table is locked' from the same connection;
- the practice bank: a requirement, the script a person wrote for one database, and the correct script. DBaba must find the
  mistake (topic 'transaction' or 'dialect'), the person's script must really do something else on that database (LAB), and
  DBaba's corrected script must save exactly what the correct one saves;
- another database's transaction words SQLite refuses (its 'refuses'), each with a sample;
- SQLite leaves notes-only (rule A): its notes line is gone, a named SQLite query is 'Checked for SQLite';
- the practice exam: questions from all four nights (errors, dates, types, query rules, plans, transactions), each answered
  by the part of DBaba that owns it.
"""

import os
import sqlite3
import tempfile
import threading
import time

import pytest

from app.agents import sqlcorrect, sqltxn
from app.products.dbaba.dbknow import engines as E
from app.products.dbaba.dbknow import reasoning as R
from tests.practice import txnlab as L

ENGINE_FILE = {"sqlite": "sqlite", "mariadb": "mysql", "postgresql": "postgresql"}


def _servers() -> set:
    out = {"sqlite"}
    for eng in ("mariadb", "postgresql"):
        try:
            L.run(eng, "SELECT 1")
            out.add(eng)
        except Exception:  # noqa: BLE001 - that server isn't running here
            pass
    return out


SERVERS = _servers()


def _need(eng):
    if eng not in SERVERS:
        pytest.skip(f"{eng} is not running here (it is in CI)")


# ------------------------------------------------------------------ the dialect files' txn_rules, run again
def _lab_lines():
    out = []
    for eng, fam in ENGINE_FILE.items():
        for rid, r in E.dialect(fam)["txn_rules"].items():
            if not isinstance(r, dict):
                continue
            for k, (script, want) in enumerate(r.get("lab", [])):
                if isinstance(want, dict) and "v" in want:
                    out.append((eng, rid, k, script, want))
    return out


def test_every_txn_rule_is_lab_proven_on_its_own_database():
    for eng, fam in ENGINE_FILE.items():
        rules = E.dialect(fam)["txn_rules"]
        assert rules["proven"] == [eng]
        for rid, r in rules.items():
            if isinstance(r, dict):
                assert r["kind"] in ("error", "wrong", "check", "none") and r["says"] and r["says_hi"] and r["lab"], (fam, rid)


@pytest.mark.parametrize("eng,rid,k,script,want", _lab_lines(), ids=[f"{e}-{r}-{k}" for e, r, k, _s, _w in _lab_lines()])
def test_every_txn_rule_lab_line_holds(eng, rid, k, script, want):
    _need(eng)
    got = L.run(eng, script)
    got["warnings"] = [w for w in got["warnings"] if w != "there is no transaction in progress" or w in want["warnings"]]
    assert {key: got[key] for key in want} == want, (eng, rid, script, got)


# ------------------------------------------------------------------ what two connections show
@pytest.fixture()
def dbfile():
    d = tempfile.mkdtemp()
    yield os.path.join(d, "lab.db")
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    os.rmdir(d)


def _conns(path, wal, timeout_b=0.0):
    a = sqlite3.connect(path, isolation_level=None, timeout=0, check_same_thread=False)
    if wal:
        a.execute("PRAGMA journal_mode = WAL")
    a.execute("CREATE TABLE acct (id INTEGER PRIMARY KEY, bal INT)")
    a.execute("INSERT INTO acct VALUES (1, 100), (2, 50)")
    b = sqlite3.connect(path, isolation_level=None, timeout=timeout_b, check_same_thread=False)
    return a, b


@pytest.mark.parametrize("wal", [False, True])
def test_a_read_then_write_transaction_fails_at_once_and_begin_immediate_waits(dbfile, wal):
    """deferred_upgrade: B reads in a plain BEGIN while A holds the write lock (and commits half a second later); B's write fails
    at once although B has a 3-second busy timeout. With BEGIN IMMEDIATE, B waits for A and goes through."""
    a, b = _conns(dbfile, wal, timeout_b=3.0)
    a.execute("BEGIN IMMEDIATE")
    a.execute("UPDATE acct SET bal = bal - 10 WHERE id = 1")
    b.execute("BEGIN")
    b.execute("SELECT bal FROM acct WHERE id = 1").fetchall()
    threading.Timer(0.5, lambda: a.execute("COMMIT")).start()
    t0 = time.time()
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        b.execute("UPDATE acct SET bal = bal + 1 WHERE id = 1")
    assert time.time() - t0 < 0.3                                     # the busy timeout was not used
    b.execute("ROLLBACK")
    time.sleep(0.7)
    a.execute("BEGIN IMMEDIATE")
    a.execute("UPDATE acct SET bal = bal - 10 WHERE id = 1")
    threading.Timer(0.5, lambda: a.execute("COMMIT")).start()
    t0 = time.time()
    b.execute("BEGIN IMMEDIATE")
    b.execute("SELECT bal FROM acct WHERE id = 1").fetchall()
    b.execute("UPDATE acct SET bal = bal + 1 WHERE id = 1")
    b.execute("COMMIT")
    assert 0.3 < time.time() - t0 < 3.0                                # it waited for A, then went through
    assert a.execute("SELECT bal FROM acct WHERE id = 1").fetchone()[0] == 81
    a.close()
    b.close()


def test_wal_lets_a_reader_and_a_writer_go_on_and_the_snapshot_error(dbfile):
    """In WAL mode a writer commits while another connection reads; the reader keeps its view and its later write gets
    SQLITE_BUSY_SNAPSHOT. In rollback-journal mode the same write is refused while the reader holds its lock."""
    a, b = _conns(dbfile, wal=True)
    b.execute("BEGIN")
    assert b.execute("SELECT bal FROM acct WHERE id = 1").fetchone()[0] == 100
    assert a.execute("UPDATE acct SET bal = 90 WHERE id = 1").rowcount == 1
    assert b.execute("SELECT bal FROM acct WHERE id = 1").fetchone()[0] == 100     # its own view
    with pytest.raises(sqlite3.OperationalError) as e:
        b.execute("UPDATE acct SET bal = 1 WHERE id = 1")
    assert "database is locked" in str(e.value)
    if hasattr(e.value, "sqlite_errorname"):
        assert e.value.sqlite_errorname == "SQLITE_BUSY_SNAPSHOT"
    b.execute("ROLLBACK")
    a.close()
    b.close()
    os.remove(dbfile)
    for s in ("-wal", "-shm"):
        if os.path.exists(dbfile + s):
            os.remove(dbfile + s)
    a, b = _conns(dbfile, wal=False)
    b.execute("BEGIN")
    b.execute("SELECT bal FROM acct WHERE id = 1").fetchall()
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        a.execute("UPDATE acct SET bal = 90 WHERE id = 1")
    b.execute("COMMIT")
    a.close()
    b.close()


def test_one_transaction_makes_many_inserts_fast(dbfile):
    c = sqlite3.connect(dbfile, isolation_level=None)
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    t0 = time.time()
    for i in range(300):
        c.execute("INSERT INTO t (v) VALUES (?)", (str(i),))
    alone = time.time() - t0
    t0 = time.time()
    c.execute("BEGIN")
    for i in range(300):
        c.execute("INSERT INTO t (v) VALUES (?)", (str(i),))
    c.execute("COMMIT")
    together = time.time() - t0
    c.close()
    assert alone > 5 * together, (alone, together)


def test_database_table_is_locked_comes_from_the_same_connection(dbfile):
    c = sqlite3.connect(dbfile, isolation_level=None)
    c.execute("CREATE TABLE t (x)")
    c.execute("INSERT INTO t VALUES (1), (2)")
    cur = c.execute("SELECT x FROM t")
    cur.fetchone()
    with pytest.raises(sqlite3.OperationalError) as e:
        c.execute("DROP TABLE t")
    assert str(e.value) == "database table is locked"
    assert [x["cause"] for x in R.diagnose("sqlite3.OperationalError: " + str(e.value))["found"]][:1] == ["statement_still_open"]
    cur.close()
    c.execute("DROP TABLE t")
    c.close()


# ------------------------------------------------------------------ the practice bank
# (id, database, requirement, the person's script, topic DBaba must find, the correct script, LAB)
# LAB: 'error' (the database refuses part of it), 'differs' (it saves something else than meant), 'same' (the same saved
# state here; the difference is under load or in a warning, proven by the tests above)
P = [
    ("s_start", "sqlite", "mark order 1 paid in one transaction", "START TRANSACTION;\nUPDATE lab_tx SET v = 20 WHERE id = 1;\nCOMMIT;",
     "dialect", "BEGIN;\nUPDATE lab_tx SET v = 20 WHERE id = 1;\nCOMMIT;", "error"),
    ("s_begin_work", "sqlite", "the same, written BEGIN WORK", "BEGIN WORK;\nUPDATE lab_tx SET v = 20 WHERE id = 1;\nCOMMIT WORK;",
     "dialect", "BEGIN;\nUPDATE lab_tx SET v = 20 WHERE id = 1;\nCOMMIT;", "error"),
    ("s_begin_tran", "sqlite", "the same, written BEGIN TRAN", "BEGIN TRAN;\nUPDATE lab_tx SET v = 20 WHERE id = 1;\nCOMMIT TRAN;",
     "dialect", "BEGIN;\nUPDATE lab_tx SET v = 20 WHERE id = 1;\nCOMMIT;", "error"),
    ("s_lock_tables", "sqlite", "change v while nobody else writes", "LOCK TABLES lab_tx WRITE;\nUPDATE lab_tx SET v = 21 WHERE id = 1;\nUNLOCK TABLES;",
     "dialect", "BEGIN IMMEDIATE;\nUPDATE lab_tx SET v = 21 WHERE id = 1;\nCOMMIT;", "error"),
    ("s_set_iso", "sqlite", "a serializable change", "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;\nBEGIN;\nUPDATE lab_tx SET v = 22 WHERE id = 1;\nCOMMIT;",
     "dialect", "BEGIN;\nUPDATE lab_tx SET v = 22 WHERE id = 1;\nCOMMIT;", "error"),
    ("s_begin_iso", "sqlite", "a serializable change, said on BEGIN", "BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE;\nUPDATE lab_tx SET v = 23 WHERE id = 1;\nCOMMIT;",
     "dialect", "BEGIN;\nUPDATE lab_tx SET v = 23 WHERE id = 1;\nCOMMIT;", "error"),
    ("s_autocommit", "sqlite", "two changes that go together", "SET autocommit = 0;\nUPDATE lab_tx SET v = 24 WHERE id = 1;\nINSERT INTO lab_log VALUES (1, 'v set');\nCOMMIT;",
     "dialect", "BEGIN;\nUPDATE lab_tx SET v = 24 WHERE id = 1;\nINSERT INTO lab_log VALUES (1, 'v set');\nCOMMIT;", "error"),
    ("s_nested_commit", "sqlite", "a change and its log line, the log as an inner step",
     "BEGIN;\nUPDATE lab_tx SET v = 25 WHERE id = 1;\nBEGIN;\nINSERT INTO lab_log VALUES (1, 'v set');\nCOMMIT;\nCOMMIT;",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 25 WHERE id = 1;\nSAVEPOINT s;\nINSERT INTO lab_log VALUES (1, 'v set');\nRELEASE s;\nCOMMIT;", "error"),
    ("s_nested_rollback", "sqlite", "keep the first change, undo only the inner one",
     "BEGIN;\nUPDATE lab_tx SET v = 26 WHERE id = 1;\nBEGIN;\nUPDATE lab_tx SET v = 99 WHERE id = 1;\nROLLBACK;\nCOMMIT;",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 26 WHERE id = 1;\nSAVEPOINT s;\nUPDATE lab_tx SET v = 99 WHERE id = 1;\nROLLBACK TO s;\nCOMMIT;", "differs"),
    ("s_rollback_no_begin", "sqlite", "try two changes, then undo them", "UPDATE lab_tx SET v = 27 WHERE id = 1;\nINSERT INTO lab_log VALUES (1, 'try');\nROLLBACK;",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 27 WHERE id = 1;\nINSERT INTO lab_log VALUES (1, 'try');\nROLLBACK;", "differs"),
    ("s_vacuum", "sqlite", "change v, then compact the file", "BEGIN;\nUPDATE lab_tx SET v = 28 WHERE id = 1;\nVACUUM;\nCOMMIT;",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 28 WHERE id = 1;\nCOMMIT;\nVACUUM;", "error"),
    ("s_journal", "sqlite", "switch to WAL and change v", "BEGIN;\nPRAGMA journal_mode = WAL;\nUPDATE lab_tx SET v = 29 WHERE id = 1;\nCOMMIT;",
     "transaction", "PRAGMA journal_mode = WAL;\nBEGIN;\nUPDATE lab_tx SET v = 29 WHERE id = 1;\nCOMMIT;", "error"),
    ("s_savepoint_typo", "sqlite", "change v, then undo it back to the savepoint",
     "BEGIN;\nSAVEPOINT before_log;\nUPDATE lab_tx SET v = 30 WHERE id = 1;\nROLLBACK TO befor_log;\nCOMMIT;",
     "transaction", "BEGIN;\nSAVEPOINT before_log;\nUPDATE lab_tx SET v = 30 WHERE id = 1;\nROLLBACK TO before_log;\nCOMMIT;", "differs"),
    ("s_left_open", "sqlite", "set v to 31", "BEGIN;\nUPDATE lab_tx SET v = 31 WHERE id = 1;", "transaction",
     "BEGIN;\nUPDATE lab_tx SET v = 31 WHERE id = 1;\nCOMMIT;", "differs"),
    ("s_rollback_to_end", "sqlite", "keep the first change, undo the second", "BEGIN;\nUPDATE lab_tx SET v = 32 WHERE id = 1;\nSAVEPOINT a;\nUPDATE lab_tx SET v = 99 WHERE id = 1;\nROLLBACK TO a;",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 32 WHERE id = 1;\nSAVEPOINT a;\nUPDATE lab_tx SET v = 99 WHERE id = 1;\nROLLBACK TO a;\nCOMMIT;", "differs"),
    ("s_deferred", "sqlite", "read v, then take one off it", "BEGIN;\nSELECT v FROM lab_tx WHERE id = 1;\nUPDATE lab_tx SET v = v - 1 WHERE id = 1;\nCOMMIT;",
     "transaction", "BEGIN IMMEDIATE;\nSELECT v FROM lab_tx WHERE id = 1;\nUPDATE lab_tx SET v = v - 1 WHERE id = 1;\nCOMMIT;", "same"),
    ("s_deferred_word", "sqlite", "read v, then set it, said DEFERRED", "BEGIN DEFERRED TRANSACTION;\nSELECT v FROM lab_tx;\nUPDATE lab_tx SET v = 33 WHERE id = 1;\nCOMMIT;",
     "transaction", "BEGIN IMMEDIATE TRANSACTION;\nSELECT v FROM lab_tx;\nUPDATE lab_tx SET v = 33 WHERE id = 1;\nCOMMIT;", "same"),
    ("s_two_tables", "sqlite", "change v and log it, both or neither", "UPDATE lab_tx SET v = 34 WHERE id = 1;\nINSERT INTO lab_log VALUES (1, 'v set');",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 34 WHERE id = 1;\nINSERT INTO lab_log VALUES (1, 'v set');\nCOMMIT;", "same"),
    ("s_for_share", "sqlite", "read v without letting it change", "BEGIN;\nSELECT v FROM lab_tx WHERE id = 1 FOR SHARE;\nUPDATE lab_tx SET v = 35 WHERE id = 1;\nCOMMIT;",
     "dialect", "BEGIN IMMEDIATE;\nSELECT v FROM lab_tx WHERE id = 1;\nUPDATE lab_tx SET v = 35 WHERE id = 1;\nCOMMIT;", "error"),
    ("s_lock_wait", "sqlite", "wait up to 5 seconds for a lock", "SET innodb_lock_wait_timeout = 5;\nBEGIN;\nUPDATE lab_tx SET v = 36 WHERE id = 1;\nCOMMIT;",
     "dialect", "PRAGMA busy_timeout = 5000;\nBEGIN;\nUPDATE lab_tx SET v = 36 WHERE id = 1;\nCOMMIT;", "error"),
    ("s_lock_timeout", "sqlite", "wait up to 3 seconds for a lock (PostgreSQL words)", "SET lock_timeout = '3s';\nBEGIN;\nUPDATE lab_tx SET v = 37 WHERE id = 1;\nCOMMIT;",
     "dialect", "PRAGMA busy_timeout = 3000;\nBEGIN;\nUPDATE lab_tx SET v = 37 WHERE id = 1;\nCOMMIT;", "error"),
    ("s_end", "sqlite", "END as COMMIT is fine; a second COMMIT is not", "BEGIN;\nUPDATE lab_tx SET v = 38 WHERE id = 1;\nEND;\nCOMMIT;",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 38 WHERE id = 1;\nEND;", "error"),
    ("m_nested", "mariadb", "both changes or neither", "START TRANSACTION;\nUPDATE lab_tx SET v = 40 WHERE id = 1;\nSTART TRANSACTION;\nINSERT INTO lab_log VALUES (1, 'x');\nROLLBACK;",
     "transaction", "START TRANSACTION;\nUPDATE lab_tx SET v = 40 WHERE id = 1;\nINSERT INTO lab_log VALUES (1, 'x');\nROLLBACK;", "differs"),
    ("m_ddl", "mariadb", "try a change and a new table, then undo the change", "START TRANSACTION;\nUPDATE lab_tx SET v = 41 WHERE id = 1;\nCREATE TABLE lab_tx2 (a INT);\nROLLBACK;",
     "transaction", "CREATE TABLE lab_tx2 (a INT);\nSTART TRANSACTION;\nUPDATE lab_tx SET v = 41 WHERE id = 1;\nROLLBACK;", "differs"),
    ("m_ddl_fails", "mariadb", "try a change, drop a table that isn't there, undo", "START TRANSACTION;\nUPDATE lab_tx SET v = 42 WHERE id = 1;\nDROP TABLE IF EXISTS lab_tx2;\nROLLBACK;",
     "transaction", "DROP TABLE IF EXISTS lab_tx2;\nSTART TRANSACTION;\nUPDATE lab_tx SET v = 42 WHERE id = 1;\nROLLBACK;", "differs"),
    ("m_truncate", "mariadb", "clear the log and change v, or neither", "START TRANSACTION;\nUPDATE lab_tx SET v = 43 WHERE id = 1;\nTRUNCATE TABLE lab_log;\nROLLBACK;",
     "transaction", "TRUNCATE TABLE lab_log;\nSTART TRANSACTION;\nUPDATE lab_tx SET v = 43 WHERE id = 1;\nROLLBACK;", "differs"),
    ("m_lock_inside", "mariadb", "change v under a table lock, then undo", "START TRANSACTION;\nUPDATE lab_tx SET v = 44 WHERE id = 1;\nLOCK TABLES lab_tx WRITE;\nROLLBACK;\nUNLOCK TABLES;",
     "transaction", "LOCK TABLES lab_tx WRITE;\nSTART TRANSACTION;\nUPDATE lab_tx SET v = 44 WHERE id = 1;\nROLLBACK;\nUNLOCK TABLES;", "differs"),
    ("m_set_inside", "mariadb", "a serializable change", "START TRANSACTION;\nSET TRANSACTION ISOLATION LEVEL SERIALIZABLE;\nUPDATE lab_tx SET v = 45 WHERE id = 1;\nCOMMIT;",
     "transaction", "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;\nSTART TRANSACTION;\nUPDATE lab_tx SET v = 45 WHERE id = 1;\nCOMMIT;", "error"),
    ("m_rollback_no_begin", "mariadb", "try a change, then undo it", "UPDATE lab_tx SET v = 46 WHERE id = 1;\nROLLBACK;",
     "transaction", "START TRANSACTION;\nUPDATE lab_tx SET v = 46 WHERE id = 1;\nROLLBACK;", "differs"),
    ("m_begin_transaction", "mariadb", "a change in a transaction (SQL Server words)", "BEGIN TRANSACTION;\nUPDATE lab_tx SET v = 47 WHERE id = 1;\nCOMMIT TRANSACTION;",
     "dialect", "START TRANSACTION;\nUPDATE lab_tx SET v = 47 WHERE id = 1;\nCOMMIT;", "error"),
    ("m_savepoint_outside", "mariadb", "change v, then undo back to the savepoint", "SAVEPOINT a;\nUPDATE lab_tx SET v = 48 WHERE id = 1;\nROLLBACK TO a;",
     "transaction", "START TRANSACTION;\nSAVEPOINT a;\nUPDATE lab_tx SET v = 48 WHERE id = 1;\nROLLBACK TO a;\nCOMMIT;", "error"),
    ("m_left_open", "mariadb", "set v to 49", "START TRANSACTION;\nUPDATE lab_tx SET v = 49 WHERE id = 1;", "transaction",
     "START TRANSACTION;\nUPDATE lab_tx SET v = 49 WHERE id = 1;\nCOMMIT;", "differs"),
    ("p_nested", "postgresql", "keep the first change, undo only the inner one",
     "BEGIN;\nUPDATE lab_tx SET v = 50 WHERE id = 1;\nBEGIN;\nUPDATE lab_tx SET v = 99 WHERE id = 1;\nROLLBACK;\nCOMMIT;",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 50 WHERE id = 1;\nSAVEPOINT s;\nUPDATE lab_tx SET v = 99 WHERE id = 1;\nROLLBACK TO SAVEPOINT s;\nCOMMIT;", "differs"),
    ("p_vacuum", "postgresql", "change v, then vacuum the table", "BEGIN;\nUPDATE lab_tx SET v = 51 WHERE id = 1;\nVACUUM lab_tx;\nCOMMIT;",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 51 WHERE id = 1;\nCOMMIT;\nVACUUM lab_tx;", "error"),
    ("p_cic", "postgresql", "change v and add an index without blocking writers",
     "BEGIN;\nUPDATE lab_tx SET v = 52 WHERE id = 1;\nCREATE INDEX CONCURRENTLY ix_lab_tx_v ON lab_tx (v);\nCOMMIT;",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 52 WHERE id = 1;\nCOMMIT;\nCREATE INDEX CONCURRENTLY ix_lab_tx_v ON lab_tx (v);", "error"),
    ("p_set_before", "postgresql", "a serializable change", "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;\nBEGIN;\nUPDATE lab_tx SET v = 53 WHERE id = 1;\nCOMMIT;",
     "transaction", "BEGIN;\nSET TRANSACTION ISOLATION LEVEL SERIALIZABLE;\nUPDATE lab_tx SET v = 53 WHERE id = 1;\nCOMMIT;", "same"),
    ("p_set_late", "postgresql", "a serializable read and change", "BEGIN;\nSELECT v FROM lab_tx;\nSET TRANSACTION ISOLATION LEVEL SERIALIZABLE;\nUPDATE lab_tx SET v = 54 WHERE id = 1;\nCOMMIT;",
     "transaction", "BEGIN;\nSET TRANSACTION ISOLATION LEVEL SERIALIZABLE;\nSELECT v FROM lab_tx;\nUPDATE lab_tx SET v = 54 WHERE id = 1;\nCOMMIT;", "error"),
    ("p_savepoint_typo", "postgresql", "keep the first change, undo the second",
     "BEGIN;\nUPDATE lab_tx SET v = 55 WHERE id = 1;\nSAVEPOINT before_two;\nUPDATE lab_tx SET v = 99 WHERE id = 1;\nROLLBACK TO SAVEPOINT before_tow;\nCOMMIT;",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 55 WHERE id = 1;\nSAVEPOINT before_two;\nUPDATE lab_tx SET v = 99 WHERE id = 1;\nROLLBACK TO SAVEPOINT before_two;\nCOMMIT;", "error"),
    ("p_lock_tables", "postgresql", "change v with the table locked", "BEGIN;\nLOCK TABLES lab_tx WRITE;\nUPDATE lab_tx SET v = 56 WHERE id = 1;\nCOMMIT;",
     "dialect", "BEGIN;\nLOCK TABLE lab_tx IN EXCLUSIVE MODE;\nUPDATE lab_tx SET v = 56 WHERE id = 1;\nCOMMIT;", "error"),
    ("p_rollback_no_begin", "postgresql", "try a change, then undo it", "UPDATE lab_tx SET v = 57 WHERE id = 1;\nINSERT INTO lab_log VALUES (1, 'try');\nROLLBACK;",
     "transaction", "BEGIN;\nUPDATE lab_tx SET v = 57 WHERE id = 1;\nINSERT INTO lab_log VALUES (1, 'try');\nROLLBACK;", "differs"),
    ("p_savepoint_outside", "postgresql", "change v, then undo back to the savepoint", "SAVEPOINT a;\nUPDATE lab_tx SET v = 58 WHERE id = 1;\nROLLBACK TO SAVEPOINT a;\nCOMMIT;",
     "transaction", "BEGIN;\nSAVEPOINT a;\nUPDATE lab_tx SET v = 58 WHERE id = 1;\nROLLBACK TO SAVEPOINT a;\nCOMMIT;", "error"),
    ("p_autocommit", "postgresql", "two changes together (MySQL words)", "SET autocommit = 0;\nUPDATE lab_tx SET v = 59 WHERE id = 1;\nINSERT INTO lab_log VALUES (1, 'x');\nCOMMIT;",
     "dialect", "BEGIN;\nUPDATE lab_tx SET v = 59 WHERE id = 1;\nINSERT INTO lab_log VALUES (1, 'x');\nCOMMIT;", "error"),
]
CHOSEN = {"sqlite": "sqlite", "mariadb": "mariadb", "postgresql": "postgresql"}


def _state(r):
    return {"v": r["v"], "log": r["log"], "open": r["open"]}


@pytest.mark.parametrize("pid,eng,req,wrong,topic,right,lab", P, ids=[p[0] for p in P])
def test_practice_bank(pid, eng, req, wrong, topic, right, lab):
    _need(eng)
    res = sqlcorrect.check(wrong, intent=req, engine=CHOSEN[eng])
    assert any(f["topic"] == topic for f in res["findings"]), (pid, res["findings"])
    correct = L.run(eng, right)
    assert not correct["errors"], (pid, "the correct script itself failed", correct)
    person = L.run(eng, wrong)
    if lab == "error":
        assert person["errors"], (pid, person)
    elif lab == "differs":
        assert _state(person) != _state(correct) and not person["errors"] or person["errors"], (pid, person, correct)
        assert _state(person) != _state(correct), (pid, person, correct)
    else:
        assert _state(person) == _state(correct), (pid, person, correct)
    fixed = res["corrected"]
    assert fixed, (pid, "no corrected script", res["findings"])
    got = L.run(eng, fixed)
    keep = {"v", "log"} if pid.endswith("_outside") and eng == "mariadb" else {"v", "log", "open"}
    assert not got["errors"] and {k: got[k] for k in keep} == {k: correct[k] for k in keep}, (pid, fixed, got, correct)


def test_the_bank_is_big_enough_and_on_three_databases():
    assert len(P) >= 30 and {p[1] for p in P} == {"sqlite", "mariadb", "postgresql"}
    assert len({p[0] for p in P}) == len(P)


VALID = [   # correct scripts: no error / wrong finding on their own database
    ("sqlite", "BEGIN;\nUPDATE lab_tx SET v = 1 WHERE id = 1;\nCOMMIT;"),
    ("sqlite", "BEGIN IMMEDIATE;\nSELECT v FROM lab_tx WHERE id = 1;\nUPDATE lab_tx SET v = v + 1 WHERE id = 1;\nCOMMIT;"),
    ("sqlite", "SAVEPOINT a;\nUPDATE lab_tx SET v = 2 WHERE id = 1;\nRELEASE a;"),
    ("sqlite", "BEGIN;\nUPDATE lab_tx SET v = 3 WHERE id = 1;\nEND TRANSACTION;"),
    ("sqlite", "BEGIN;\nCREATE TABLE lab_tx2 (a INT);\nROLLBACK;"),
    ("sqlite", "PRAGMA journal_mode = WAL;\nBEGIN;\nUPDATE lab_tx SET v = 4 WHERE id = 1;\nCOMMIT;\nVACUUM;"),
    ("sqlite", "BEGIN EXCLUSIVE;\nDELETE FROM lab_log;\nCOMMIT;"),
    ("mariadb", "START TRANSACTION;\nUPDATE lab_tx SET v = 5 WHERE id = 1;\nSAVEPOINT a;\nUPDATE lab_tx SET v = 6 WHERE id = 1;\nROLLBACK TO SAVEPOINT a;\nCOMMIT;"),
    ("mariadb", "SET TRANSACTION ISOLATION LEVEL READ COMMITTED;\nSTART TRANSACTION;\nUPDATE lab_tx SET v = 7 WHERE id = 1;\nCOMMIT;"),
    ("mariadb", "CREATE TABLE lab_tx2 (a INT);\nSTART TRANSACTION;\nINSERT INTO lab_tx2 VALUES (1);\nCOMMIT;"),
    ("postgresql", "BEGIN;\nSET TRANSACTION ISOLATION LEVEL SERIALIZABLE;\nUPDATE lab_tx SET v = 8 WHERE id = 1;\nCOMMIT;"),
    ("postgresql", "BEGIN;\nCREATE TABLE lab_tx2 (a INT);\nINSERT INTO lab_tx2 VALUES (1);\nROLLBACK;"),
    ("postgresql", "BEGIN;\nUPDATE lab_tx SET v = 9 WHERE id = 1;\nCOMMIT;\nVACUUM lab_tx;"),
    ("postgresql", "BEGIN ISOLATION LEVEL REPEATABLE READ;\nSELECT v FROM lab_tx;\nCOMMIT;"),
]


@pytest.mark.parametrize("eng,script", VALID)
def test_valid_scripts_get_no_error_or_wrong_finding(eng, script):
    _need(eng)
    assert not L.run(eng, script)["errors"], script
    fs = sqlcorrect.check(script, engine=CHOSEN[eng])["findings"]
    assert not [f for f in fs if f["kind"] in ("error", "wrong")], (script, fs)
    assert not [f for f in fs if f["topic"] == "transaction"], (script, fs)


def test_not_sure_says_each_database_and_a_rewrite_that_runs_on_both():
    s = "BEGIN;\nUPDATE lab_tx SET v = 60 WHERE id = 1;\nBEGIN;\nUPDATE lab_tx SET v = 99 WHERE id = 1;\nROLLBACK;\nCOMMIT;"
    res = sqlcorrect.check(s, engine="not_sure")
    f = next(f for f in res["findings"] if f["topic"] == "transaction")
    assert "MySQL/MariaDB:" in f["fix_en"] and "PostgreSQL:" in f["fix_en"] and "SQLite" not in f["fix_en"]
    for eng in ("mariadb", "postgresql"):
        if eng in SERVERS:
            got = L.run(eng, res["corrected"])
            assert got["v"] == 60 and not got["errors"], (eng, got)


def test_the_statements_inside_a_script_are_checked_too():
    res = sqlcorrect.check("BEGIN;\nUPDATE lab_tx SET v = 1 WHERE id = 1;\nSELECT id FROM lab_tx WHERE v = NULL;\nCOMMIT;", engine="sqlite")
    assert any(f["topic"] == "null" for f in res["findings"]) and "v IS NULL" in res["corrected"]


def test_a_routine_is_still_a_routine_not_a_script():
    for sql in ["CREATE PROCEDURE p() BEGIN UPDATE t SET a = 1; END",
                "CREATE TRIGGER tr AFTER UPDATE ON t BEGIN UPDATE t SET b = 1 WHERE rowid = NEW.rowid; END"]:
        assert not sqltxn.is_script(sql), sql
    assert sqltxn.is_script("BEGIN;\nUPDATE t SET a = 1;\nCOMMIT;") and sqltxn.is_script("COMMIT")


# ------------------------------------------------------------------ another database's transaction words SQLite refuses
REFUSED_NIGHT4 = {
    "start_transaction": "START TRANSACTION", "begin_options": "BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE", "work_word": "BEGIN WORK",
    "tran_word": "BEGIN TRAN", "set_transaction": "SET TRANSACTION ISOLATION LEVEL READ COMMITTED", "autocommit_off": "SET autocommit = 0",
    "autocommit_on": "SET autocommit = 1", "lock_tables": "LOCK TABLES lab_tx WRITE", "unlock_tables": "UNLOCK TABLES",
    "lock_wait_seconds": "SET innodb_lock_wait_timeout = 5", "lock_timeout_seconds": "SET lock_timeout = '3s'",
    "lock_timeout_ms": "SET lock_timeout = 2500", "for_share": "SELECT v FROM lab_tx WHERE id = 1 FOR SHARE",
    "nolock": "SELECT v FROM lab_tx WITH (NOLOCK) WHERE id = 1",
}


@pytest.mark.parametrize("rid,sample", sorted(REFUSED_NIGHT4.items()))
def test_tonights_refused_words(rid, sample):
    r = next(x for x in E.dialect("sqlite")["refuses"] if x["id"] == rid)
    assert r["proven"] == ["sqlite"]
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.execute("CREATE TABLE lab_tx (id INT PRIMARY KEY, v INT)")
    with pytest.raises(sqlite3.Error) as e:
        c.execute(sample)
    assert str(e.value).split(":")[0] in r["error"] or str(e.value) in r["error"], (rid, str(e.value), r["error"])
    res = sqlcorrect.check(sample, engine="sqlite")
    assert any(f["topic"] == "dialect" for f in res["findings"]), (rid, res["findings"])
    if res["corrected"]:
        stmts = [x for x in res["corrected"].split(";") if x.strip()]
        if stmts[0].strip().upper() in ("COMMIT", "END"):
            c.execute("BEGIN")                              # UNLOCK TABLES / SET autocommit = 1 end a transaction
        for stmt in stmts:
            c.execute(stmt)                                 # SQLite's own words run
        if c.in_transaction:
            c.execute("COMMIT")
    c.close()


def test_mysql_and_postgresql_refuse_the_other_transaction_words():
    for eng, sample, rid in [("mariadb", "BEGIN TRANSACTION", "begin_transaction"), ("mariadb", "COMMIT TRANSACTION", "commit_tran"),
                             ("postgresql", "LOCK TABLES lab_tx WRITE", "lock_tables"), ("postgresql", "UNLOCK TABLES", "unlock_tables"),
                             ("postgresql", "SET autocommit = 0", "set_autocommit"), ("postgresql", "BEGIN TRAN", "begin_tran")]:
        fam = ENGINE_FILE[eng]
        r = next(x for x in E.dialect(fam)["refuses"] if x["id"] == rid)
        assert r["proven"] == [eng]
        res = sqlcorrect.check(sample, engine=eng)
        assert any(f["topic"] == "dialect" for f in res["findings"]), (eng, sample, res["findings"])
        if eng in SERVERS:
            assert L.run(eng, sample)["errors"], (eng, sample)
            assert not L.run(eng, "BEGIN;\n" + res["corrected"] + ";\nCOMMIT;" if "LOCK TABLE" in (res["corrected"] or "") else
                             res["corrected"] or "SELECT 1")["errors"], (eng, res["corrected"])


# ------------------------------------------------------------------ the diagnoser reads the transaction errors
@pytest.mark.parametrize("text,cause,eng", [
    ("sqlite3.OperationalError: cannot start a transaction within a transaction", "txn_nested", "sqlite"),
    ("Error: stepping, cannot rollback - no transaction is active (1)", "txn_not_active", "sqlite"),
    ("sqlite3.OperationalError: no such savepoint: before_pay", "savepoint_missing", "sqlite"),
    ("sqlite3.OperationalError: cannot VACUUM from within a transaction", "outside_txn_only", "sqlite"),
    ("sqlite3.OperationalError: cannot change into wal mode from within a transaction", "outside_txn_only", "sqlite"),
    ("SQLITE_LOCKED: database table is locked", "statement_still_open", "sqlite"),
    ("SQLITE_BUSY_SNAPSHOT: database is locked", "read_upgrade_busy", "sqlite"),
    ("sqlite3.OperationalError: database is locked", "db_file_locked", "sqlite"),
    ("ERROR:  current transaction is aborted, commands ignored until end of transaction block", "txn_aborted", "postgresql"),
    ("ERROR:  savepoint \"before_pay\" does not exist", "savepoint_missing", "postgresql"),
    ("ERROR:  VACUUM cannot run inside a transaction block", "outside_txn_only", "postgresql"),
    ("ERROR:  CREATE INDEX CONCURRENTLY cannot run inside a transaction block", "outside_txn_only", "postgresql"),
    ("WARNING:  there is already a transaction in progress", "txn_nested", "postgresql"),
    ("ERROR:  SET TRANSACTION ISOLATION LEVEL must be called before any query", "txn_settings_late", "postgresql"),
    ("ERROR 1305 (42000): SAVEPOINT before_pay does not exist", "savepoint_missing", "mysql"),
    ("ERROR 1568 (25001): Transaction characteristics can't be changed while a transaction is in progress", "txn_settings_late", "mysql"),
])
def test_transaction_errors_are_diagnosed(text, cause, eng):
    res = R.diagnose(text)
    assert [x["cause"] for x in sorted(res["found"], key=lambda x: -x["score"])][:1] == [cause], (text, res["found"])
    assert res["engine"] == eng, (text, res["engine"])
    assert "Fix (" in R.report(res, False)


def test_the_diagnosed_messages_are_the_real_ones():
    """Each SQLite / PostgreSQL / MariaDB message above is what the lab printed (txn_rules lab lines)."""
    seen = set()
    for eng, fam in ENGINE_FILE.items():
        for r in E.dialect(fam)["txn_rules"].values():
            if isinstance(r, dict):
                for _s, want in r.get("lab", []):
                    if isinstance(want, dict):
                        seen |= set(want.get("errors", [])) | set(want.get("warnings", []))
    for msg in ["cannot start a transaction within a transaction", "cannot rollback - no transaction is active", "no such savepoint: b",
                "cannot VACUUM from within a transaction", "cannot change into wal mode from within a transaction",
                "current transaction is aborted, commands ignored until end of transaction block", "savepoint \"b\" does not exist",
                "VACUUM cannot run inside a transaction block", "there is already a transaction in progress",
                "SET TRANSACTION ISOLATION LEVEL must be called before any query", "SAVEPOINT b does not exist",
                "Transaction characteristics can't be changed while a transaction is in progress"]:
        assert msg in seen, msg


# ------------------------------------------------------------------ SQLite is no longer notes-only (rule A)
def test_sqlite_answers_for_itself_now():
    assert not E.notes_only("sqlite") and E.notes_only("sqlserver") and E.notes_only("oracle")
    assert E.notes_line("sqlite", "en", "SELECT 1") == ""
    assert E.query_family("sqlite") == "sqlite" and E.syntax_family("sqlite") == "sqlite"
    from app.agents import sqlconfidence

    assert sqlconfidence._verify("sqlite")[0][0] == "SQLite"


def test_rule_a_detection_from_transaction_words():
    assert E.shown("BEGIN IMMEDIATE;\nUPDATE lab_tx SET v = 1 WHERE id = 1;\nCOMMIT;")[0] == "sqlite"
    assert E.shown("BEGIN EXCLUSIVE;\nDELETE FROM lab_log;\nCOMMIT;")[0] == "sqlite"
    assert E.shown("BEGIN;\nUPDATE lab_tx SET v = 1 WHERE id = 1;\nCOMMIT;")[0] is None     # plain BEGIN: every database has it


def test_sqlite_verify_queries_run():
    v = E.dialect("sqlite")["verify"]
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, code INT PRIMARY KEY_NOT, n TEXT NOT NULL)".replace(" PRIMARY KEY_NOT", ""))
    c.execute("CREATE TABLE k (code TEXT PRIMARY KEY, n TEXT)")
    c.execute("CREATE INDEX ix_t_n ON t (n)")
    assert c.execute(v["nullable"].format(table="t", cols="'id', 'n', 'code'")).fetchall() == [("id", "NO", "INTEGER"), ("code", "YES", "INT"), ("n", "NO", "TEXT")]
    assert c.execute(v["nullable"].format(table="k", cols="'code'")).fetchall() == [("code", "YES", "TEXT")]   # night 2: it takes NULL
    assert c.execute(v["indexes"].format(table="t")).fetchall()[0][0] == "ix_t_n"
    assert c.execute(v["dupes"].format(table="t", cols="n")).fetchall() == []
    assert c.execute(v["plan"].format(query="SELECT * FROM t WHERE n = 'a'")).fetchall()
    c.close()


def test_a_named_sqlite_query_is_checked_for_sqlite(client, ml_mode):
    from tests.practice.concepts.test_e1_sqlite1 import fresh
    from tests.test_ml import say

    fresh(client)
    r = say(client, "this is a sqlite query: SELECT id FROM orders WHERE status = NULL")
    assert "Checked for SQLite" in r["reply"] and "general SQL reasoning" not in r["reply"] and "not yet lab-tested" not in r["reply"], r["reply"][-400:]


# ------------------------------------------------------------------ the practice exam across the four SQLite nights
EXAM_QUERIES = [   # (night, the person's SQLite query, the topic DBaba must name)
    (1, "SELECT id FROM orders WHERE order_date > DATE_SUB(NOW(), INTERVAL 7 DAY)", "dialect"),
    (1, "SELECT id, NVL(discount, 0) FROM orders", "dialect"),
    (1, "SELECT TOP 3 id FROM orders ORDER BY amount DESC", "dialect"),
    (1, "SHOW TABLES", "dialect"),
    (3, "SELECT id FROM orders WHERE customer_id = (SELECT id FROM customers WHERE city = 'Pune')", "subquery"),
    (3, "SELECT COUNT(CASE WHEN status = 'paid' THEN 1 END) / COUNT(*) FROM orders", "division"),
    (3, "SELECT id FROM customers WHERE id <> ALL (SELECT customer_id FROM orders)", "dialect"),
    (3, "SELECT o.id, c.first_name FROM orders o JOIN customers c", "join"),
    (4, "BEGIN;\nUPDATE orders SET status = 'paid' WHERE id = 3;\nBEGIN;\nUPDATE payments SET amount = 0 WHERE id = 2;\nCOMMIT;\nCOMMIT;", "transaction"),
    (4, "BEGIN;\nSELECT stock FROM products WHERE id = 1;\nUPDATE products SET stock = stock - 1 WHERE id = 1;\nCOMMIT;", "transaction"),
    (4, "LOCK TABLES orders WRITE;\nUPDATE orders SET status = 'x' WHERE id = 1;\nUNLOCK TABLES;", "dialect"),
]
EXAM_DESIGNS = [   # night 2: table designs read the way SQLite takes them
    ("CREATE TABLE invoices (id INT PRIMARY KEY, total DECIMAL(10,2))", "Unknown"),
    ("CREATE TABLE users (id INTEGER PRIMARY KEY AUTO_INCREMENT, email VARCHAR(80))", "Unknown"),
]
EXAM_ERRORS = [   # nights 1 and 4: SQLite's own messages
    ("sqlite3.OperationalError: no such column: emial", "unknown_column"),
    ("Error: stepping, UNIQUE constraint failed: customers.email (19)", "duplicate_key"),
    ("sqlite3.OperationalError: database is locked", "db_file_locked"),
    ("sqlite3.OperationalError: attempt to write a readonly database", "file_readonly"),
    ("sqlite3.OperationalError: cannot start a transaction within a transaction", "txn_nested"),
    ("sqlite3.OperationalError: no such savepoint: s1", "savepoint_missing"),
]


@pytest.mark.parametrize("night,sql,topic", EXAM_QUERIES)
def test_exam_queries(night, sql, topic):
    res = sqlcorrect.check(sql, engine="sqlite")
    assert any(f["topic"] == topic for f in res["findings"]), (night, sql, res["findings"])
    assert all(f["kind"] in ("error", "wrong", "check") for f in res["findings"])


@pytest.mark.parametrize("sql,_", EXAM_DESIGNS)
def test_exam_designs(sql, _):
    res = sqlcorrect.check(sql, engine="sqlite")
    assert res["findings"] and any(f["kind"] in ("error", "wrong") for f in res["findings"]), (sql, res["findings"])


@pytest.mark.parametrize("text,cause", EXAM_ERRORS)
def test_exam_errors(text, cause):
    res = R.diagnose(text)
    assert [x["cause"] for x in sorted(res["found"], key=lambda x: -x["score"])][:1] == [cause] and res["engine"] == "sqlite", (text, res["found"])


def test_exam_dates_and_plans():
    from app.agents import sqlexplain

    res = sqlcorrect.check("SELECT id FROM orders WHERE order_date - 7 > '2024-06-01'", engine="sqlite")
    assert res["findings"], res
    plan = sqlexplain.read("QUERY PLAN\n`--SCAN orders")
    assert plan and plan.get("engine") == "sqlite", plan


def test_the_exam_covers_all_four_nights():
    assert {n for n, _s, _t in EXAM_QUERIES} >= {1, 3, 4} and EXAM_DESIGNS and EXAM_ERRORS
    assert len(EXAM_QUERIES) + len(EXAM_DESIGNS) + len(EXAM_ERRORS) + 2 >= 20


def test_a_huge_change_on_sqlite_is_batched_by_rowid_and_ends_the_same():
    """sqllock's batch_change for SQLite (it locks the whole database while one big change runs): the batched form picks rows
    by rowid (UPDATE / DELETE ... LIMIT exists only in some builds), and running it until it changes 0 rows leaves the same
    table as the single statement."""
    def mk():
        c = sqlite3.connect(":memory:", isolation_level=None)
        c.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, level TEXT, archived INT)")
        c.executemany("INSERT INTO logs (level, archived) VALUES (?, 0)", [("debug" if i % 3 else "info",) for i in range(12000)])
        return c
    for stmt in ["UPDATE logs SET archived = 1 WHERE level = 'debug'", "DELETE FROM logs WHERE level = 'debug'"]:
        res = sqlcorrect.check(stmt, intent="the logs table has millions of rows", engine="sqlite")
        f = next(f for f in res["findings"] if f["topic"] == "batch_change")
        assert "rowid IN (SELECT rowid FROM logs" in res["corrected"] and "SQLite" in f["title_en"] and "1205" not in f["fix_en"], res
        one, many = mk(), mk()
        one.execute(stmt)
        runs = 0
        while many.execute(res["corrected"]).rowcount:
            runs += 1
        assert runs >= 2 and one.execute("SELECT * FROM logs ORDER BY id").fetchall() == many.execute("SELECT * FROM logs ORDER BY id").fetchall()
