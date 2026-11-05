"""Night 2: standing rule A (the user's database decides the answer) through the real chat endpoint, in English, Hinglish
and Hindi, for every path: named -> only that database, remembered; shown by the syntax or the error -> detected and said,
with a tap to correct it; neither -> asked first; a database switched mid-conversation. And tonight's concept questions
(joins, which database) each get their concept, never the checker, the diagnoser, the connect step or 'no specific answer'."""

import re

import pytest

from app.dbknow import concepts as K
from app.dbknow import engines as E
from tests.test_ml import say

ASK_EN = ["MySQL", "MariaDB", "PostgreSQL", "SQL Server", "Oracle", "Not sure"]
ASK_HI = ["MySQL", "MariaDB", "PostgreSQL", "SQL Server", "Oracle", "Pata nahi"]
WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


# ------------------------------------------------------------------ the reasoning itself
def test_named_shown_and_neither():
    assert E.named("this is a MySQL query") == "mysql" and E.named("postgres error aa raha hai") == "postgresql"
    assert E.named("ye MariaDB hai") == "mariadb" and E.named("ये PostgreSQL की query है") == "postgresql"
    assert E.named("SQL Server pe chalani hai") == "sqlserver" and E.named("oracle 19c") == "oracle"
    assert E.named("check this SQL query") is None and E.named("my sql query is slow") is None     # SQL is the language
    assert E.named("postgres hai, mysql nahi") == "postgresql" and E.named("not mysql, it's postgres") == "postgresql"
    assert E.named("mysql nahi chal raha") == "mysql"                                             # 'not running', not 'not mysql'
    assert E.shown("SELECT `id` FROM t")[0] == "mysql" and E.shown("SELECT * FROM t LIMIT 5, 10")[0] == "mysql"
    assert E.shown("SELECT id::date FROM t")[0] == "postgresql" and E.shown("SELECT * FROM t WHERE a ILIKE 'x'")[0] == "postgresql"
    assert E.shown("SELECT TOP 5 * FROM t")[0] == "sqlserver" and E.shown("SELECT * FROM [dbo].[t]")[0] == "sqlserver"
    assert E.shown("SELECT * FROM t WHERE ROWNUM < 5")[0] == "oracle" and E.shown("ORA-00942: table or view does not exist")[0] == "oracle"
    assert E.shown("SELECT NVL(a, 0) FROM t")[0] is None              # NVL alone also works on MariaDB: not enough to say Oracle
    assert E.shown("SELECT * FROM t LIMIT 5")[0] is None and E.shown("SELECT 'a::b' FROM t")[0] is None
    assert E.shown("SELECT `a` FROM t WHERE b ILIKE 'x'")[0] is None   # marks of two databases, equally: don't guess
    assert E.choice("Pata nahi") == E.NOT_SURE and E.choice("नहीं पता") == E.NOT_SURE and E.choice("PostgreSQL") == "postgresql"


# ------------------------------------------------------------------ 3. neither named nor shown: ask first
@pytest.mark.parametrize("text,taps,lang_word", [
    ("SELECT id FROM orders WHERE discount = NULL", ASK_EN, "Which database"),
    ("ye query check karo: SELECT id FROM orders WHERE discount = NULL", ASK_HI, "kis database"),
    ("मेरी query देखो: SELECT id FROM orders WHERE discount = NULL", ASK_HI, "किस database"),
    ("check this SQL query: SELECT id FROM orders WHERE discount = NULL", ASK_EN, "SQL is the language"),
])
def test_neither_named_nor_shown_asks_first(client, ml_mode, text, taps, lang_word):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "agent_plan" and r["options"] == taps and lang_word in r["reply"], r["reply"]
    assert "IS NULL" not in r["reply"]                                 # nothing answered before the choice
    r = say(client, "PostgreSQL")
    assert r["relation"] == "agent_run" and ("IS NULL" in r["reply"] or "NULL" in r["reply"]), r["reply"][:300]
    assert "PostgreSQL" in r["reply"]
    r = say(client, "SELECT id FROM customers WHERE phone = NULL")      # remembered: no second question
    assert r["relation"] == "agent_run" and r["options"] != taps and "PostgreSQL" in r["reply"], r["reply"][:300]


def test_not_sure_answers_for_each_and_is_not_asked_again(client, ml_mode):
    fresh(client)
    r = say(client, "SELECT id, ISNULL(discount, 0) FROM orders")
    assert r["options"] == ASK_EN
    r = say(client, "Not sure")
    assert r["relation"] == "agent_run" and "COALESCE" in r["reply"] or "mistake" in r["reply"].lower(), r["reply"][:300]
    r = say(client, "SELECT id FROM orders WHERE status = 'paid' OR 'shipped'")
    assert r["relation"] == "agent_run" and r["options"] != ASK_EN


# ------------------------------------------------------------------ 1. named: only that database, remembered, switched
@pytest.mark.parametrize("text,want", [
    ("this is a postgres query: SELECT `id` FROM orders LIMIT 0, 5", ["PostgreSQL doesn't accept `backticks`", "PostgreSQL has no LIMIT offset, count"]),
    ("ye mysql ki query hai: SELECT id::text FROM orders", ["MySQL mein :: cast nahi hota"]),
    ("ये PostgreSQL की query है: SELECT id FROM orders ORDER BY id LIMIT 2, 3", ["PostgreSQL mein LIMIT offset, count nahi hota"]),
    ("MariaDB query: SELECT id FROM orders ORDER BY discount DESC NULLS LAST", ["MariaDB has no NULLS LAST"]),
])
def test_named_database_gets_only_its_own_answer(client, ml_mode, text, want):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "agent_run", r["reply"][:300]
    for w in want:
        assert w in r["reply"], (w, r["reply"][:400])
    assert "Detected database" not in r["reply"] and "pehchana" not in r["reply"]


def test_switching_the_database_mid_conversation(client, ml_mode):
    fresh(client)
    r = say(client, "postgres query: SELECT id FROM orders WHERE status = 'paid' OR 'shipped'")
    assert r["relation"] == "agent_run" and "PostgreSQL" in r["reply"]
    r = say(client, "now this one is for mysql: SELECT id::text FROM orders")
    assert "MySQL has no :: cast" in r["reply"], r["reply"][:300]
    r = say(client, "SELECT `id` FROM orders LIMIT 0, 5")                 # backticks are fine on MySQL: no refusal now
    assert "backticks" not in r["reply"] and "Checked for MySQL" in r["reply"], r["reply"][:300]


# ------------------------------------------------------------------ 2. shown: detected, said, one tap to correct
@pytest.mark.parametrize("text,line,tap", [
    ("SELECT `id`, amount FROM orders WHERE status = 'paid' OR 'shipped'", "Detected database: MySQL / MariaDB (from `id`)", "It's PostgreSQL"),
    ("bhai ye meri query check karo: SELECT id, amount::text FROM orders WHERE status = 'paid' OR 'shipped'", "Database pehchana: PostgreSQL", "Ye MySQL hai"),
    ("यह query देखिए: SELECT id FROM orders WHERE amount ILIKE '1%' OR 'x'", "पहचाना गया database: PostgreSQL", "यह MySQL है"),
])
def test_detected_database_is_said_with_a_tap_to_correct(client, ml_mode, text, line, tap):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "agent_run" and line in r["reply"] and tap in r["options"], (r["reply"][:400], r["options"])
    r = say(client, tap)                                                  # the correction: checked again for that database
    assert r["relation"] == "agent_reply" and ("MySQL" in r["reply"] or "PostgreSQL" in r["reply"]), r["reply"][:300]
    if "`id`" in text:
        assert "PostgreSQL doesn't accept `backticks`" in r["reply"], r["reply"][:400]
    else:
        assert ":: cast" in r["reply"] or "ILIKE" in r["reply"], r["reply"][:400]


def test_notes_only_databases_say_what_is_general(client, ml_mode):
    fresh(client)
    r = say(client, "SELECT TOP 5 id FROM orders WHERE status = 'paid' OR 'x'")
    assert "Detected database: SQL Server" in r["reply"] and "general SQL reasoning" in r["reply"], r["reply"][:500]
    assert "TOP" not in " ".join(re.findall(r"has no TOP[^\n]*", r["reply"]))   # TOP is SQL Server's own: not refused
    fresh(client)
    r = say(client, "this is for Oracle: SELECT id FROM orders WHERE ROWNUM <= 5 AND status = NULL")
    assert "Oracle" in r["reply"] and "general SQL reasoning" in r["reply"] and "= NULL' is never true" in r["reply"], r["reply"][:500]


# ------------------------------------------------------------------ errors follow the same rule
def test_errors_named_detected_or_asked(client, ml_mode):
    fresh(client)
    r = say(client, "Too many connections")                              # no database shown: asked first
    assert r["relation"] == "agent_plan" and r["options"] == ASK_EN and "connections" in r["reply"].lower(), r["reply"]
    r = say(client, "MariaDB")
    assert "max_connections" in r["reply"] and "PostgreSQL" not in r["reply"], r["reply"][:500]
    fresh(client)
    r = say(client, "ERROR 1062 (23000): Duplicate entry 'a@b.c' for key 'email'")
    assert "Detected database: MySQL" in r["reply"] and r["options"], r["reply"][-300:]
    fresh(client)
    r = say(client, "postgres pe ye aa raha hai: sorry, too many clients already")
    assert "Detected database" not in r["reply"] and "max_connections" in r["reply"], r["reply"][:400]


# ------------------------------------------------------------------ concept questions: answered directly, for the remembered database
def test_concepts_never_ask_and_use_the_remembered_database(client, ml_mode):
    fresh(client)
    r = say(client, "what is a join")
    assert r["relation"] == "concept" and r["options"] != ASK_EN
    fresh(client)
    say(client, "SELECT id FROM orders WHERE discount = NULL")
    say(client, "PostgreSQL")
    r = say(client, "how to do a full outer join in mysql")              # named in the question: that one, and remembered
    assert r["relation"] == "concept" and "UNION ALL" in r["reply"] and "FULL JOIN suppliers" not in r["reply"], r["reply"][:600]
    r = say(client, "how to delete duplicate rows")                       # MySQL is remembered now
    assert r["relation"] == "concept" and "-- PostgreSQL" not in r["reply"], r["reply"][:600]


# ------------------------------------------------------------------ tonight's concepts
NIGHT2 = ["anti_join", "semi_join", "natural_join", "join_using", "on_vs_where", "join_duplicates", "join_three_tables", "join_two_columns",
          "non_equi_join", "self_join_pairs", "full_join_mysql", "join_order", "join_vs_subquery", "left_join_count", "sql_vs_database",
          "mysql_vs_mariadb", "mysql_vs_postgresql", "which_database"]


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    assert set(NIGHT2) <= ids and len(NIGHT2) >= 15
    for cid in NIGHT2:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert set(c["proven"]) >= {"mariadb", "postgresql"} and all(e.get("result") is not None for e in c["examples"]), cid


@pytest.mark.parametrize("cid", NIGHT2)
def test_every_phrasing_of_tonights_concepts(client, ml_mode, cid):
    c = K.by_id(cid)
    titles = set(c["title"].values())
    for lang, phr in c["phrasings"].items():
        for text in phr:
            fresh(client)
            r = say(client, text)
            assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in titles and not WRONG_PLACES.search(r["reply"]), \
                (lang, text, r["relation"], r["reply"][:200])


@pytest.mark.parametrize("text", [
    "why does my join return duplicate rows: SELECT c.name FROM customers c JOIN orders o ON o.id = c.id",
    "SELECT c.name FROM customers c, orders o",
    "ERROR 1052 (23000): Column 'id' in field list is ambiguous",
    "my database is slow",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] != "concept", r["reply"][:200]
