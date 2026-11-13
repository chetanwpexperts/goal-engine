"""Night 9: tonight's concept questions (deadlocks, lock wait timeouts, row vs table locks, SELECT ... FOR UPDATE, SKIP LOCKED and
NOWAIT for job queues, long and idle transactions, MVCC, gap locks, optimistic vs pessimistic locking, the N+1 problem, shared vs
exclusive locks, finding blocking queries, stopping a query, counting a big table, online DDL, advisory locks, bulk loading,
partitioning) through the real chat endpoint, in English, Hinglish and Hindi. 'timeout' in a general question is a lesson; the
person's own timing-out query or database still goes to the checker / diagnoser; a pasted deadlock report is read."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT9 = ["deadlock", "lock_wait_timeout", "row_vs_table_lock", "select_for_update", "skip_locked_nowait", "long_transaction", "mvcc", "gap_lock",
          "optimistic_pessimistic", "n_plus_one", "shared_exclusive_lock", "find_blocking_queries", "kill_query", "count_big_table", "online_ddl",
          "advisory_lock", "bulk_insert", "partition_table"]


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT9) <= ids and len(NIGHT9) >= 15
    for cid in NIGHT9:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"}, cid
        assert c["examples"] and all(e.get("result") is not None or e.get("changed") is not None for e in c["examples"]), cid
        assert c["source"].startswith(("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")), cid
        assert set(c["engines"]) >= {"mysql", "mariadb", "postgresql"}, cid
        assert "locking" in c["fundamentals"] and set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid


@pytest.mark.parametrize("cid", NIGHT9)
def test_every_phrasing_of_tonights_concepts(client, ml_mode, cid):
    c = K.by_id(cid)
    titles = set(c["title"].values())
    for lang, phr in c["phrasings"].items():
        for text in phr:
            fresh(client)
            r = say(client, text)
            assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in titles and not WRONG_PLACES.search(r["reply"]), \
                (lang, text, r["relation"], r["reply"][:200])


@pytest.mark.parametrize("text,cid", [
    ("how do deadlocks happen in postgres", "deadlock"),
    ("what is skip locked in mysql", "skip_locked_nowait"),
    ("what is the n+1 problem in laravel", "n_plus_one"),
    ("how to insert lakhs of rows fast", "bulk_insert"),
    ("what is a next-key lock", "gap_lock"),
    ("pg_blocking_pids kya hai", "find_blocking_queries"),
    ("what is a transaction", "transaction"),                     # earlier nights' concepts keep their words
    ("how to delete millions of rows", "batch_delete"),
    ("what are isolation levels", "isolation_levels"),
    ("how to add a column in mysql", "alter_table"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


@pytest.mark.parametrize("text", [
    "my query times out",
    "ERROR 1205 (HY000): Lock wait timeout exceeded; try restarting transaction",
    "ERROR 1213 (40001): Deadlock found when trying to get lock; try restarting transaction",
    "ERROR:  deadlock detected",
    "our checkout page times out",
    "meri update query atak jaati hai",
    "DELETE FROM logs WHERE created_at < '2024-01-01'; it has 40 million rows and times out",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] != "concept", (text, r["reply"][:200])


def test_a_pasted_deadlock_report_gets_the_order_and_the_fix(client, ml_mode):
    fresh(client)
    r = say(client, "ERROR:  deadlock detected\nDETAIL:  Process 552 waits for ShareLock on transaction 232448; blocked by process 553.\n"
                    "Process 553 waits for ShareLock on transaction 232447; blocked by process 552.\n"
                    "CONTEXT:  while updating tuple (0,2) in relation \"accounts\"")
    assert r["relation"] != "concept" and "same order" in r["reply"] and "accounts" in r["reply"], r["reply"][:400]
