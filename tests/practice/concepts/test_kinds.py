"""'How many types of joins in sql' (asked on the live site, 13 Nov): a question about the KINDS of something got the general
JOIN answer, which never lists the kinds. A kinds question is now a shape of its own (types of / kinds of / how many types /
kitne types / kaun kaun se / प्रकार), and the common ones have their own answers: joins, keys, SQL commands, constraints,
indexes, subqueries, window functions. Each phrasing goes through the real chat endpoint; 'what is a join' keeps its answer."""

import pytest

from app.dbknow import concepts as K
from tests.practice.concepts.test_night12 import WRONG_PLACES, fresh
from tests.test_ml import say

KINDS = ["join_types", "key_types", "sql_command_types", "constraint_types", "index_types", "subquery_types", "window_function_types"]


def test_the_kinds_concepts_are_complete():
    for cid in KINDS:
        c = K.by_id(cid)
        assert c and all(c["phrasings"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"} and c["examples"][0].get("result"), cid


@pytest.mark.parametrize("cid", KINDS)
def test_every_phrasing_gets_the_kinds_answer(client, ml_mode, cid):
    c = K.by_id(cid)
    for lang, phr in c["phrasings"].items():
        for text in phr:
            fresh(client)
            r = say(client, text)
            assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(c["title"].values()), (lang, text, r["reply"][:200])
            assert not WRONG_PLACES.search(r["reply"]), text


@pytest.mark.parametrize("text,cid,must", [
    ("how many types of joins in sql", "join_types", ["INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL OUTER JOIN", "CROSS JOIN", "SELF JOIN"]),
    ("join kitne prakar ke hote hain", "join_types", ["INNER JOIN", "CROSS JOIN"]),
    ("types of keys in sql", "key_types", ["Primary key", "Foreign key", "Composite key"]),
    ("what is ddl dml dcl tcl", "sql_command_types", ["DDL", "TCL"]),
    ("how many types of index", "index_types", ["B-tree", "composite"]),
    ("what is a join", "join", []),                                   # the general question keeps the general answer
    ("types of normalization", "normalization", []),
])
def test_kinds_questions_list_the_kinds(client, ml_mode, text, cid, must):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])
    assert all(m in r["reply"] for m in must), (text, r["reply"][:600])


def test_a_concept_answer_carries_its_examples_for_the_side_panel(client, ml_mode):
    """The page shows the explanation in the chat and the worked examples beside it (wide screens): the goal carries them."""
    fresh(client)
    r = say(client, "what is a left join")
    card = r["goal"]["card"]
    assert card["id"] == "left_join" and card["title"] == r["reply"].split("\n", 1)[0] and card["answer"] in r["reply"]
    ex = card["examples"][0]
    assert ex["sql"][0]["query"] in r["reply"] and ex["columns"] and ex["rows"]
    assert {d["engine"] for d in card["engines"]} == {"MySQL", "MariaDB", "PostgreSQL"}
    r = say(client, "how to delete duplicate rows in mysql")
    card = r["goal"]["card"]
    assert card["examples"][0]["preview"]["sql"] and [d["engine"] for d in card["engines"]] == ["MySQL"]   # the named database only
    r = say(client, "kaun kaun se window functions hote hain")
    assert r["goal"]["card"]["lang"] == "hinglish" and r["goal"]["card"]["id"] == "window_function_types"


def test_earlier_answers_keep_their_examples(client, ml_mode):
    """Asked from the live site: after a second question the first answer's examples were gone. The goal keeps each concept
    answer's parts (the latest 12, one per title), so every answer in the chat can show its own examples again."""
    fresh(client)
    say(client, "how many types of join in mysql?")
    r = say(client, "what is a left join", )
    titles = [c["title"] for c in r["goal"]["cards"]]
    assert titles == ["The types of JOIN", "LEFT JOIN"] and r["goal"]["card"]["title"] == "LEFT JOIN"
    r = say(client, "how many types of join in mysql?")
    assert [c["title"] for c in r["goal"]["cards"]] == ["LEFT JOIN", "The types of JOIN"]     # asked again: once, as the latest
