"""Night 10A: tonight's concept questions (SQL injection, prepared statements, placeholder styles, a placeholder inside quotes,
a name as a placeholder, IN lists and LIKE with placeholders, ORM, query builders, raw SQL in Laravel, $wpdb->prepare, PDO vs
mysqli, escaping vs binding, migrations, dynamic SQL, finding the SQL in a project, connection strings, SQL comments) through
the real chat endpoint, in English, Hinglish and Hindi. Then code pasted in the chat: every query in it is checked at its line
for the person's database (rule A: named, shown by the driver the code uses, or asked), while a single plain query, an error
or a complaint still go where they went before."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT10A = ["sql_injection", "prepared_statement", "placeholders", "quoted_placeholder", "placeholder_table_name", "in_list_parameters",
            "like_with_parameter", "orm", "query_builder", "raw_query_laravel", "wpdb_prepare", "pdo_vs_mysqli", "escape_vs_prepare",
            "db_migration", "dynamic_sql", "find_queries_in_code", "connection_string", "sql_comments"]
OFFICIAL = ("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT10A) <= ids and len(NIGHT10A) >= 15
    for cid in NIGHT10A:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"}, cid
        assert c["examples"] and all(e.get("result") is not None or e.get("changed") is not None for e in c["examples"]), cid
        assert c["source"].startswith(OFFICIAL), cid
        assert set(c["engines"]) >= {"mysql", "postgresql"}, cid
        assert "code_parameters" in c["fundamentals"] and set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid


@pytest.mark.parametrize("cid", NIGHT10A)
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
    ("sql injection kaise rokein", "sql_injection"),
    ("what is a parameterised query", "prepared_statement"),
    ("is mysqli_real_escape_string enough to stop sql injection", "escape_vs_prepare"),
    ("what is the difference between pdo and mysqli", "pdo_vs_mysqli"),
    ("what is a dsn", "connection_string"),
    ("what are migrations", "db_migration"),
    ("what is eloquent", "orm"),
    ("how to write a comment in mysql", "sql_comments"),
    ("what is a primary key", "primary_key"),                     # earlier nights keep their words
    ("what is the n+1 query problem", "n_plus_one"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


@pytest.mark.parametrize("text", [
    "SELECT id FROM customers WHERE email = NULL",
    "ERROR 1210 (HY000): Incorrect arguments to EXECUTE",
    "my query with a placeholder fails on mysql",
    "$res = mysqli_query($link, \"SELECT id FROM orders WHERE amount = NULL\");",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


# ---------------------------------------------------------------- code pasted in the chat
PHP = ('$stmt = $pdo->prepare("SELECT id, first_name FROM customers WHERE email LIKE \'%?%\' ORDER BY id");\n'
       '$stmt->execute([$term]);\n'
       '$n = $pdo->query("SELECT COUNT(*) FROM orders WHERE amount = NULL");\n')


def test_code_with_two_queries_named_database(client, ml_mode):
    fresh(client)
    r = say(client, "this is mysql code, check it:\n" + PHP)
    assert r["relation"] == "agent_run", r
    assert "Found 2 SQL queries" in r["reply"] and "Line 2:" in r["reply"] and "Line 4:" in r["reply"], r["reply"]
    assert "CONCAT('%', ?, '%')" in r["reply"] and "IS NULL" in r["reply"] and "Checked for MySQL" in r["reply"], r["reply"]
    assert "PostgreSQL runs it" not in r["reply"]            # only the named database's behaviour (rule A)


def test_code_without_a_database_asks_first_then_answers(client, ml_mode):
    fresh(client)
    r = say(client, PHP)
    assert r["relation"] == "agent_plan" and "PostgreSQL" in (r.get("options") or []), r
    r = say(client, "PostgreSQL")
    assert "Found 2 SQL queries" in r["reply"] and "'%' || ? || '%'" in r["reply"] and "Checked for PostgreSQL" in r["reply"], r["reply"]


def test_the_driver_in_the_code_shows_the_database(client, ml_mode):
    fresh(client)
    code = ("const { Pool } = require('pg');\nconst pool = new Pool();\n"
            "const a = await pool.query(`SELECT id FROM customers WHERE city = '${city}' ORDER BY id`);\n"
            "const b = await pool.query(\"SELECT id FROM customers WHERE email LIKE '%$1%'\", [text]);\n")
    r = say(client, code)
    assert r["relation"] == "agent_run", r
    assert "PostgreSQL" in r["reply"] and "Line 3:" in r["reply"] and "Line 4:" in r["reply"], r["reply"]
    assert "$1" in r["reply"] and "'%' || $1 || '%'" in r["reply"] and "SQL injection" in r["reply"], r["reply"]


def test_hinglish_code_paste(client, ml_mode):
    fresh(client)
    code = ("import pymysql\n"
            "cur.execute(\"SELECT id FROM orders WHERE status = '%s' ORDER BY id\", (status,))\n"
            "cur.execute(\"SELECT id FROM orders WHERE customer_id = %s ORDER BY id\" % cid)\n")
    r = say(client, "is mysql code ko check karo:\n" + code)
    assert r["relation"] == "agent_run" and "Code mein 2 SQL query mili" in r["reply"] and "Sahi query" in r["reply"], r["reply"]


def test_one_plain_query_in_code_still_uses_the_checker(client, ml_mode):
    fresh(client)
    r = say(client, '$sql = "SELECT id FROM orders WHERE amount = NULL";')
    assert "Found" not in r["reply"].split("\n", 1)[0] or "SQL quer" not in r["reply"].split("\n", 1)[0], r["reply"]
