"""Night 10B: tonight's concept questions (a table's structure, its CREATE statement, its indexes, the list of tables,
information_schema, whether a column exists, schema dumps, NULL in Laravel migrations, schema drift, rolling back and the order
of migrations, a NOT NULL column on a table with rows, changing a column's NULL rule, joins on different types, what a schema
is, UNIQUE constraint vs unique index, orphan rows) through the real chat endpoint, in English, Hinglish and Hindi. Then a
CREATE TABLE pasted together with a query: the query is checked against those tables (a risk they rule out is said as ruled
out, one they prove is verified, a name they don't have is an error), while a design alone, a plain query, an error or a
complaint still go where they went before. The learning feed's 'what is an index' (asked by a real user) is answered."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say, say_sql

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT10B = ["describe_table", "show_create_table", "list_indexes", "list_tables", "information_schema", "column_exists", "schema_dump",
            "laravel_nullable", "schema_drift", "down_migration", "migration_order", "add_not_null_column", "change_nullability",
            "join_type_mismatch", "what_is_schema", "unique_constraint_vs_index", "orphan_rows"]
OFFICIAL = ("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT10B) <= ids and len(NIGHT10B) >= 15 and "schema_facts" in funds
    for cid in NIGHT10B:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"}, cid
        assert c["examples"] and all(e.get("result") is not None or e.get("changed") is not None for e in c["examples"]), cid
        assert c["source"].startswith(OFFICIAL), cid
        assert set(c["engines"]) >= {"mysql", "postgresql"}, cid
        assert "schema_facts" in c["fundamentals"] and set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid


@pytest.mark.parametrize("cid", NIGHT10B)
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
    ("what is an index", "index"),                                 # the learning feed: a real user's question
    ("how do i see the columns of a table in postgres", "describe_table"),
    ("does a laravel column allow null by default", "laravel_nullable"),
    ("table ke saare index kaise dekhe", "list_indexes"),
    ("what is pg_dump schema only", "schema_dump"),
    ("how to find rows whose parent is missing", "orphan_rows"),
    ("what is a foreign key", "foreign_key"),                      # earlier nights keep their words
    ("what are migrations", "db_migration"),
    ("how to add a column", "alter_table"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


@pytest.mark.parametrize("text", [
    "SELECT id FROM customers WHERE emial = 'a@b.test'",
    "ERROR 1054 (42S22): Unknown column 'emial' in 'where clause'",
    "my query says column does not exist on postgres",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


# ---------------------------------------------------------------- tables pasted with a query
TABLES = ("CREATE TABLE customers (id INT PRIMARY KEY, email VARCHAR(80) NOT NULL, city VARCHAR(40));\n"
          "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT NOT NULL, amount DECIMAL(10,2) NOT NULL, discount DECIMAL(10,2),\n"
          "  order_date DATE NOT NULL);\n")


def test_tables_rule_out_a_not_in_risk(client, ml_mode):
    fresh(client)
    r = say_sql(client, TABLES + "SELECT id FROM customers WHERE id NOT IN (SELECT customer_id FROM orders);", "MySQL")
    assert "NOT NULL" in r["reply"] and ("rules these out" in r["reply"] or "risk yahan nahi" in r["reply"]), r["reply"]
    assert not re.search(r"\? NOT IN \(SELECT customer_id", r["reply"]), r["reply"]


def _result(r):
    return r["agents"][-1]["runs"][-1]["result"]


def test_tables_prove_a_null_risk(client, ml_mode):
    fresh(client)
    r = say_sql(client, TABLES + "SELECT o.id, o.amount - o.discount AS net FROM orders o;", "PostgreSQL")
    fs = _result(r)["correct"]["findings"]
    assert any(f["topic"] == "null_math" and f.get("schema") == "verified" and "tables you gave" in f["fix_en"] for f in fs), fs


def test_tables_catch_a_column_that_does_not_exist(client, ml_mode):
    fresh(client)
    r = say_sql(client, TABLES + "SELECT id, emial FROM customers WHERE city = 'Pune';", "MariaDB")
    assert "emial" in r["reply"], r["reply"]
    assert _result(r)["correct"]["corrected"].strip().rstrip(";") == "SELECT id, email FROM customers WHERE city = 'Pune'", _result(r)


def test_a_design_alone_is_still_a_design_check(client, ml_mode):
    fresh(client)
    r = say_sql(client, "CREATE TABLE payments (id INT PRIMARY KEY, amount FLOAT);", "MySQL")
    assert "FLOAT" in r["reply"] or "DECIMAL" in r["reply"] or "money" in r["reply"].lower(), r["reply"]
