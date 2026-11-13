"""Night 10: tonight's concept questions (LIMIT / TOP / FETCH FIRST, IFNULL / COALESCE / NVL / ISNULL, today's date, adding to a
date, days between dates, formatting a date, a date's parts, finding text in text, LENGTH vs CHAR_LENGTH, IF / IIF / CASE,
quoting names, case-insensitive search, the id of an inserted row, DUAL, ROWNUM, MERGE, SQLite, SQL Server, Oracle, converting a
query between databases, CAST / CONVERT / ::, random rows) through the real chat endpoint, in English, Hinglish and Hindi.
Then translation in the chat ('convert this query to PostgreSQL'), and the final exam of nights 1-10: 100 basic questions
drawn from every night's concepts, each answered with its own concept, never the checker / diagnoser / 'no specific answer'."""

import json
import re

import pytest

from app.dbknow import concepts as K
from tests.test_ml import say, say_sql

WRONG_PLACES = re.compile(r"I don't have a specific answer|paste (the|your|a) (sql )?query|query paste karo|Just tell me where your database is|"
                          r"connect your database|This usually comes from one of these|Bas batao database kahan hai", re.I)

NIGHT10 = ["top_vs_limit", "null_function_names", "current_datetime", "add_to_date", "date_difference", "format_date", "date_parts",
           "find_in_text", "text_length", "if_in_sql", "quote_names", "case_insensitive_search", "get_inserted_id", "dual_table", "rownum",
           "merge_statement", "sqlite_intro", "sqlserver_intro", "oracle_intro", "convert_between_databases", "cast_convert", "random_rows"]
OFFICIAL = ("https://dev.mysql.com/", "https://www.postgresql.org/docs/", "https://mariadb.com/kb/")


def fresh(client):
    client.post("/api/new-chat")
    client.cookies.clear()


def test_tonights_concepts_are_complete():
    ids = {c["id"] for c in K.concepts()}
    funds = json.load(open("app/dbknow/core/query_fundamentals.json"))["fundamentals"]
    assert set(NIGHT10) <= ids and len(NIGHT10) >= 15
    for cid in NIGHT10:
        c = K.by_id(cid)
        assert all(len(c["phrasings"][k]) >= 1 for k in ("en", "hinglish", "hindi")) and sum(len(v) for v in c["phrasings"].values()) >= 3
        assert all(c["answer"][k] and c["mistake"][k] for k in ("en", "hinglish", "hindi")), cid
        assert set(c["proven"]) >= {"mariadb", "postgresql"}, cid
        assert c["examples"] and all(e.get("result") is not None or e.get("changed") is not None for e in c["examples"]), cid
        assert c["source"].startswith(OFFICIAL), cid
        assert set(c["engines"]) >= {"mysql", "postgresql"}, cid
        assert "portability" in c["fundamentals"] and set(c["fundamentals"]) <= set(funds), cid
        assert all(K.by_id(r) for r in c["related"]), cid


@pytest.mark.parametrize("cid", NIGHT10)
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
    ("how many days between two dates", "date_difference"),
    ("how to add 7 days to the current date in mysql", "add_to_date"),
    ("top 10 rows in oracle", "top_vs_limit"),
    ("what is getdate", "current_datetime"),
    ("what is nvl", "coalesce"),                                 # earlier nights' concepts keep their words
    ("is postgres case sensitive", "collation"),
    ("ifnull in postgres", "coalesce"),
    ("what is mssql", "sqlserver_intro"),
    ("sql server aur mysql mein kya farak hai", "sqlserver_intro"),
    ("how to convert string to date in postgres", "cast_convert"),
])
def test_other_wordings_reach_the_concept(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()), (text, r["reply"][:200])


def test_the_named_database_gets_its_own_note(client, ml_mode):
    fresh(client)
    r = say(client, "how to add days to a date in sql server")
    assert r["relation"] == "concept" and "SQL Server" in r["reply"] and "DATEADD" in r["reply"] and "not lab-tested" in r["reply"], r["reply"][-400:]
    fresh(client)
    r = say(client, "how to add days to a date in postgresql")
    assert "INTERVAL" in r["reply"] and "DATE_ADD(order_date" not in r["reply"], r["reply"]


@pytest.mark.parametrize("text", [
    "SELECT TOP 5 id FROM orders ORDER BY amount DESC",
    "ERROR 1305 (42000): FUNCTION shop_db.DATEADD does not exist",
    "ERROR:  function ifnull(numeric, integer) does not exist",
    "my query with GETDATE() fails on mysql",
])
def test_real_work_still_goes_to_the_checker_or_diagnoser(client, ml_mode, text):
    fresh(client)
    r = say(client, text)
    assert r["relation"] not in ("concept", "translate"), (text, r["reply"][:200])


# ---------------------------------------------------------------- translation in the chat
def test_convert_a_mysql_query_to_postgresql(client, ml_mode):
    fresh(client)
    r = say(client, "convert this mysql query to postgresql:\nSELECT `id`, IFNULL(discount, 0) AS d FROM orders WHERE city = 'Pune' "
                    "ORDER BY id LIMIT 5, 10")
    assert r["relation"] == "translate", r
    assert "COALESCE(discount, 0)" in r["reply"] and "LIMIT 10 OFFSET 5" in r["reply"] and "`" not in r["reply"].split("What changed")[0]
    assert "ignores case" in r["reply"], r["reply"]
    # the target is remembered for the conversation (rule A): a query pasted next is checked for PostgreSQL
    r = say(client, "SELECT id, IFNULL(discount, 0) FROM orders")
    assert "IFNULL() doesn't exist on PostgreSQL" in r["reply"] or "COALESCE" in r["reply"], r["reply"][:300]


def test_hinglish_and_hindi_requests(client, ml_mode):
    fresh(client)
    r = say(client, "ye query postgres mein badlo: SELECT id, IF(amount > 1000, 'big', 'small') AS size, DATE_FORMAT(order_date, '%d/%m/%Y') FROM orders")
    assert r["relation"] == "translate" and "CASE WHEN amount > 1000" in r["reply"] and "to_char(order_date, 'DD/MM/YYYY')" in r["reply"], r["reply"]
    assert "Kya badla" in r["reply"]
    fresh(client)
    r = say(client, "इस query को MySQL में बदलो: SELECT id, strpos(email, '@') FROM customers WHERE created_at > now() - INTERVAL '7 days'")
    assert r["relation"] == "translate" and "LOCATE('@', email)" in r["reply"] and "INTERVAL 7 DAY" in r["reply"] and "क्या बदला" in r["reply"], r["reply"]


def test_convert_to_a_notes_only_database(client, ml_mode):
    fresh(client)
    r = say(client, "convert this to sql server: SELECT id, IFNULL(discount, 0) FROM orders ORDER BY id LIMIT 5")
    assert r["relation"] == "translate" and "won't rewrite it blind" in r["reply"] and "TOP" in r["reply"], r["reply"]


def test_convert_the_checked_query_as_a_follow_up(client, ml_mode):
    fresh(client)
    r = say_sql(client, "SELECT TOP 3 id, amount FROM orders ORDER BY amount DESC", "SQL Server")
    r = say(client, "now convert it to postgresql")
    assert r["relation"] in ("translate", "agent_reply") and "LIMIT 3" in r["reply"] and "TOP" in r["reply"], r


# ---------------------------------------------------------------- the final exam of nights 1-10: 100 basic questions
def _exam() -> list[tuple[str, str]]:
    """100 questions spread over every concept of nights 1-10 (every 2nd concept's phrasings in turn), the same each run."""
    out, cs = [], K.concepts()
    k = 0
    while len(out) < 100:
        for c in cs:
            flat = [t for lang in ("en", "hinglish", "hindi") for t in c["phrasings"][lang]]
            if k < len(flat) and (cs.index(c) + k) % 2 == 0:
                out.append((flat[k], c["id"]))
                if len(out) == 100:
                    break
        k += 1
    return out


EXAM = _exam()


def test_the_exam_covers_every_night():
    nights = {cid for _t, cid in EXAM}
    assert len(EXAM) == 100 and len(nights) >= 60
    for cid in ("join", "group_by", "count_distinct", "exists", "upsert", "data_types", "window_function", "explain", "deadlock", "top_vs_limit"):
        assert any(K.by_id(c)["id"] == c for c in nights) and cid in {c["id"] for c in K.concepts()}


@pytest.mark.parametrize("text,cid", EXAM)
def test_final_exam_100_basic_questions(client, ml_mode, text, cid):
    fresh(client)
    r = say(client, text)
    assert r["relation"] == "concept" and r["reply"].split("\n", 1)[0] in set(K.by_id(cid)["title"].values()) and \
        not WRONG_PLACES.search(r["reply"]), (text, cid, r["relation"], r["reply"][:200])
