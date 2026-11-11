"""Night 5: tonight's concept questions (changing data: INSERT, INSERT ... SELECT, UPDATE, UPDATE / DELETE with a join, upsert,
INSERT IGNORE vs REPLACE, previewing a change, batches, isolation levels, autocommit; routines: stored procedures, procedure vs
function, triggers, BEFORE vs AFTER, DELIMITER, cursors, loops) through the real chat endpoint, in English, Hinglish and Hindi.
Each phrasing gets its concept, never the query checker, the error diagnoser, the connect step or 'no specific answer'; and real
work (a pasted statement or routine, a pasted error, a complaint about their own procedure) still goes where it went before."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT5 = ["insert_into", "insert_select", "update_statement", "update_join", "delete_join", "upsert", "insert_ignore_replace",
          "preview_before_change", "batch_delete", "isolation_levels", "autocommit", "stored_procedure", "procedure_vs_function", "trigger",
          "before_after_trigger", "delimiter", "cursor", "loop_in_procedure"]


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT5) <= ids and len(NIGHT5) >= 15
    for cid in NIGHT5:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"}, cid
        assert c["examples"] and all(e.get("result") is not None or isinstance(e.get("changed"), int) for e in c["examples"]), cid
        assert c["source"].startswith(("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")), cid
        assert set(c["engines"]) >= {"mysql", "mariadb", "postgresql"} and set(c["fundamentals"]) <= set(funds), cid


@pytest.mark.parametrize("cid", NIGHT5)
def test_every_phrasing_of_tonights_concepts(client, ml_mode, cid):
    c = K.by_id(cid)
    titles = set(c["title"].values())
    for lang, phr in c["phrasings"].items():
        for text in phr:
            fresh(client)
            r = say(client, text)
            assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in titles and not WRONG_PLACES.search(r["reply"]), \
                (lang, text, r["relation"], r["reply"][:200])


@pytest.mark.parametrize("text,cid", [                 # said other ways than the stored phrasings: the shape and the words decide
    ("how do i update one table using values from another table", "update_join"),
    ("update join kaise karte hai postgres mein", "update_join"),
    ("how to insert a row or update it if it already exists in postgresql", "upsert"),
    ("what is the difference between a trigger before and after", "before_after_trigger"),
    ("stored procedure kaise banate hai", "stored_procedure"),
    ("what is on duplicate key update", "upsert"),
    ("how to delete 10 million rows without locking the table", "batch_delete"),
    ("explain cursors in mysql", "cursor"),
    ("trigger kya hai", "trigger"),
    ("what is repeatable read", "isolation_levels"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


def test_a_named_database_gets_its_own_syntax():
    ex = lambda text: text.split("Example", 1)[1].split("Common mistake")[0]   # noqa: E731 - the example shown
    pg, _ = K.answer(K.match("how to update with a join in postgres"), "how to update with a join in postgres")
    change = pg.split("Then the change", 1)[1].split("Common mistake")[0]
    assert "FROM customers c WHERE" in change and "JOIN customers" not in change
    my, _ = K.answer(K.match("how to use upsert in mysql"), "how to use upsert in mysql")
    assert "ON DUPLICATE KEY UPDATE" in ex(my) and "ON CONFLICT" not in ex(my)


@pytest.mark.parametrize("text", [
    "UPDATE orders o JOIN customers c ON c.id = o.customer_id SET o.amount = 0 WHERE c.city = 'Pune'",
    "INSERT INTO suppliers (id, name, country) SELECT 9, country, name FROM suppliers WHERE id = 1",
    "INSERT INTO products (id, name, price) VALUES (1, 'Phone X', 999) ON CONFLICT (id) DO UPDATE SET price = EXCLUDED.price",
    "CREATE TRIGGER tr AFTER UPDATE ON stock FOR EACH ROW UPDATE stock SET note = 'x' WHERE id = NEW.id",
    "DELIMITER //\nCREATE PROCEDURE p()\nBEGIN\n  DECLARE i INT DEFAULT 0;\n  WHILE i < 3 DO\n    SELECT i;\n  END WHILE;\nEND //\nDELIMITER ;",
    "ERROR 1442 (HY000): Can't update table 'stock' in stored function/trigger because it is already used by statement which invoked this stored function/trigger",
    "ERROR 1329 (02000): No data - zero rows fetched, selected, or processed",
    "my stored procedure is slow",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] != "concept", r["reply"][:200]


@pytest.mark.parametrize("text,topic", [
    ("UPDATE orders o JOIN customers c ON c.id = o.customer_id SET o.amount = 0 WHERE c.city = 'Pune' -- this is postgres", "update_join_syntax"),
    ("INSERT INTO suppliers (id, name, country) SELECT 9, country, name FROM suppliers WHERE id = 1", "insert_order"),
    ("CREATE TRIGGER tr AFTER UPDATE ON stock FOR EACH ROW UPDATE stock SET note = 'x' WHERE id = NEW.id", "trigger_same_table"),
])
def test_pasted_changes_and_routines_are_checked(client, ml_mode, text, topic):
    """A pasted INSERT or routine reaches the checker (before tonight only SELECT / UPDATE / DELETE did)."""
    fresh(client)
    r = say(client, text)
    assert r["relation"] in ("agent_run", "agent_plan"), (r["relation"], r["reply"][:200])
