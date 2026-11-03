"""Night 1 (rule D): basic and concept questions reach the concepts pack through the real chat endpoint, in English,
Hinglish and Hindi, and never the query checker ('paste the query'), the error diagnoser, the connect step or the generic
'I don't have a specific answer'. Real work (a pasted query, an error, 'my database is slow') still goes where it went."""

import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai|Samjha: GROUP BY", re.I)


def ask(client, text):
    client.post("/api/new-chat")
    client.cookies.clear()
    return say(client, text)


def _is_answer(r, cid):
    c = K.by_id(cid)
    titles = {c["title"][k] for k in c["title"]}
    return r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in titles and not WRONG_PLACES.search(r["reply"])


# the questions a check on 2 Nov 2026 got wrong, each with the concept it must get
NOV2 = [("what is a JOIN in SQL?", "join"), ("LEFT JOIN vs INNER JOIN", "left_vs_inner"), ("what is an index?", "index"),
        ("group by kya hota hai", "group_by"), ("how to delete duplicate rows in mysql", "delete_duplicates"),
        ("difference between WHERE and HAVING", "where_vs_having"), ("what is a primary key", "primary_key"),
        ("what is a foreign key", "foreign_key"), ("what is normalization", "normalization"),
        ("what is the difference between DELETE and TRUNCATE", "delete_truncate_drop"), ("how to find duplicate emails", "find_duplicates")]


@pytest.mark.parametrize("text,cid", NOV2)
def test_the_questions_missed_on_nov_2(client, ml_mode, text, cid):
    r = ask(client, text)
    assert _is_answer(r, cid), (text, r["relation"], r["reply"][:300])


def test_every_phrasing_of_every_concept_gets_its_answer(client, ml_mode):
    bad, asked = [], 0
    for c in K.concepts():
        for lang, phr in c["phrasings"].items():
            for text in phr:
                asked += 1
                r = ask(client, text)
                if not _is_answer(r, c["id"]):
                    bad.append(f"{lang} {text!r} -> {r['relation']} {r['reply'][:90]!r}")
    assert not bad, f"{len(bad)}/{asked} phrasings missed:\n" + "\n".join(bad)


def test_the_pack_is_complete():
    cs = K.concepts()
    assert len(cs) >= 15
    ids = {c["id"] for c in cs}
    for c in cs:
        assert sum(len(v) for v in c["phrasings"].values()) >= 3, c["id"]
        assert all(c["phrasings"][k] for k in ("en", "hinglish", "hindi")), c["id"]
        for k in ("en", "hinglish", "hindi"):
            assert c["answer"][k] and c["mistake"][k], (c["id"], k)
        assert c["source"].startswith(("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")), c["id"]
        assert set(c["proven"]) >= {"mariadb", "postgresql"} and c["examples"], c["id"]
        assert all(r in ids for r in c["related"]), c["id"]
        for ex in c["examples"]:                      # every example was run in the lab: its result (or change) is kept
            assert ex.get("result") is not None or ex.get("changed") is not None, (c["id"], ex)
    for need in ["select", "where", "order_by", "limit", "distinct", "null", "like", "group_by", "having", "where_vs_having", "join",
                 "inner_join", "left_join", "right_join", "full_join", "cross_join", "self_join", "primary_key", "foreign_key", "unique",
                 "index", "normalization", "delete_truncate_drop", "transaction", "view", "find_duplicates", "delete_duplicates"]:
        assert need in ids, need


def test_answer_parts_and_languages():
    m = K.match("what is a LEFT JOIN")
    en, taps = K.answer(m, "what is a LEFT JOIN")
    assert "Example" in en and "Result:" in en and "Common mistake" in en and "PostgreSQL" in en and taps
    hi, _ = K.answer(K.match("left join kya hota hai"), "left join kya hota hai")
    assert "Aam galti" in hi and "rakhta hai" in hi
    dev, _ = K.answer(K.match("LEFT JOIN क्या है"), "LEFT JOIN क्या है")
    assert "आम गलती" in dev and "रखता है" in dev


def test_a_named_database_gets_only_its_own_notes_and_syntax():
    my, _ = K.answer(K.match("how to delete duplicate rows in mysql"), "how to delete duplicate rows in mysql")
    assert "DELETE a FROM customers a JOIN" in my and "USING" not in my and "- PostgreSQL:" not in my
    pg, _ = K.answer(K.match("how to delete duplicates in postgresql"), "how to delete duplicates in postgresql")
    assert "USING customers b" in pg and "DELETE a FROM" not in pg
    both, _ = K.answer(K.match("how to remove duplicate records but keep one"), "how to remove duplicate records but keep one")
    assert "-- MySQL / MariaDB" in both and "-- PostgreSQL" in both
    ss, _ = K.answer(K.match("what is a primary key in sql server"), "what is a primary key in sql server")
    assert "SQL Server" in ss and "not lab-tested" in ss


def test_changes_come_with_a_preview_first():
    text, _ = K.answer(K.match("how to delete duplicate rows in mysql"), "how to delete duplicate rows in mysql")
    assert text.index("preview") < text.index("DELETE a FROM")
    assert "SELECT a.id, a.email, b.id AS kept_id" in text and "Rows changed: 2" in text


def test_unknown_concepts_are_said_honestly_and_kept_to_learn(client, ml_mode):
    r = ask(client, "what is a stored procedure in mysql")
    assert r["relation"] == "concept" and "won't guess" in r["reply"] and not WRONG_PLACES.search(r["reply"])
    feed = client.get("/api/learning/feed").json() if False else None
    from app.db import _SessionLocal
    from app.models import LearnRequest

    with _SessionLocal() as db:
        rows = db.query(LearnRequest).filter(LearnRequest.brain == "database").all()
    assert any("stored procedure" in x.text for x in rows), feed


@pytest.mark.parametrize("text", [
    "SELECT name FROM customers WHERE city = NULL",
    "what does this query do?",
    "my query is slow, what is wrong",
    "ERROR 1062 (23000): Duplicate entry 'a@b.c' for key 'email'",
    "site pe baar baar too many connections error aa raha hai",
    "how to connect my database",
    "what is the size of my database",
    "how to remove duplicates in excel",
])
def test_real_work_is_not_taken_as_a_concept(text):
    assert K.match(text) is None, text


def test_a_pasted_error_still_goes_to_the_diagnoser(client, ml_mode):
    r = ask(client, "ERROR 1062 (23000): Duplicate entry 'a@b.c' for key 'email'")
    assert r["relation"] != "concept"
    r = ask(client, "SELECT name FROM customers WHERE city = NULL")
    assert r["relation"] == "agent_run"


def test_a_concept_question_after_a_checked_query(client, ml_mode):
    client.post("/api/new-chat")
    client.cookies.clear()
    r = say(client, "SELECT c.name, COUNT(*) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.name")
    assert r["relation"] == "agent_run"
    r = say(client, "what is a left join")
    assert _is_answer(r, "left_join"), r["reply"][:200]
    r = say(client, "what does this query do?")
    assert r["relation"] == "agent_reply" and "stage by stage" in r["reply"]


def test_the_examples_still_give_their_stored_results_on_both_databases():
    from tests.practice.concepts import lab
    from tests.practice import schema as S

    if len(S.engines()) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    assert lab.check() == []
