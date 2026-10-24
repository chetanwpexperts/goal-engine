"""A fixed slice of the database agent's test generator (tests/fuzz/db.py) in every test run, plus every failure the
generator found, kept as a permanent case. The engine parts (rewrites give the same rows, indexes really run) are skipped
when the local test databases are down. The nightly run uses new seeds every night."""

import socket

import pytest

from app.agents import sql as S
from tests.fuzz import db as F


def _up(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


def _report(rep):
    return "\n".join(f"{f['family']}/{f['seed']}: {f['q'][:160]!r} -> {f['why'][:300]}" for f in rep["fails"][:20])


# ---------------------------------------------------------------- a fixed slice of the generator
def test_random_schemas_and_queries():
    rep = F.run(range(1, 9), families=("queries",))
    assert not rep["fails"], _report(rep)


def test_random_follow_ups_through_the_api(client, ml_mode):
    rep = F.run(range(1, 7), families=("followups",), client=client)
    assert not rep["fails"], _report(rep)


def test_new_ways_of_describing_every_playbook():
    rep = F.run(range(1, 4), families=("phrasings",))
    assert not rep["fails"], _report(rep)


def test_random_injected_diagnoses_and_healthy_cases():
    rep = F.run(range(900, 908), families=("diagnosis",))
    assert not rep["fails"], _report(rep)


# ---------------------------------------------------------------- what the generator found (fixed), kept for good
def test_unwrap_every_kind_of_code():
    q = "SELECT a FROM t WHERE x = 'y'\n  ORDER BY a"
    for wrapped in (f"$sql = <<<SQL\n{q}\nSQL;", f'cursor.execute("""{q}""")', 'String sql = "SELECT a FROM t";',
                    '$res = $wpdb->get_results("SELECT a FROM t");', 'cursor.execute("SELECT a FROM t WHERE x = %s", (x,))',
                    'query = f"SELECT a FROM t"', f"const query = `\n    {q}\n`;", f'$sql = "{q}";'):
        assert F._ws(S.unwrap(wrapped)) in (F._ws(q), "SELECT a FROM t", "SELECT a FROM t WHERE x = %s"), wrapped
    assert S.unwrap('SELECT a FROM t WHERE b = \'x\'";') == "SELECT a FROM t WHERE b = 'x'"      # the code before it was cut off
    assert S.unwrap('SELECT a FROM t"""') == "SELECT a FROM t"
    assert S.unwrap("SELECT a FROM t WHERE b = 'it''s'") == "SELECT a FROM t WHERE b = 'it''s'"
    assert S.unwrap("/* don't cache */ SELECT a FROM t WHERE b = 'x'") == "/* don't cache */ SELECT a FROM t WHERE b = 'x'"


def test_rewrite_works_out_the_end_date_for_every_comparison():
    r = S.rewrite
    assert r("SELECT 1 FROM t WHERE DATE(c) = '2026-02-28'") == "SELECT 1 FROM t WHERE c >= '2026-02-28' AND c < '2026-03-01'"
    assert "INTERVAL" not in r("SELECT 1 FROM t WHERE DATE(c) = '2026-12-31'")           # PostgreSQL can't run '...' + INTERVAL 1 DAY
    assert r("SELECT 1 FROM t WHERE DATE(c) > '2026-01-31'") == "SELECT 1 FROM t WHERE c >= '2026-02-01'"
    assert r("SELECT 1 FROM t WHERE DATE(c) <= '2026-01-31'") == "SELECT 1 FROM t WHERE c < '2026-02-01'"
    assert r("SELECT 1 FROM t WHERE DATE (c) BETWEEN '2026-01-01' AND '2026-01-31'") == "SELECT 1 FROM t WHERE c >= '2026-01-01' AND c < '2026-02-01'"
    assert r("SELECT 1 FROM t WHERE YEAR(c) = 2026") == "SELECT 1 FROM t WHERE c >= '2026-01-01' AND c < '2027-01-01'"
    assert r("SELECT 1 FROM t WHERE NOT DATE(c) = '2026-01-01'") == "SELECT 1 FROM t WHERE NOT (c >= '2026-01-01' AND c < '2026-01-02')"
    assert r("SELECT 1 FROM t WHERE DATE(c) = '2026-02-30'") == "SELECT 1 FROM t WHERE DATE(c) = '2026-02-30'"     # not a date: left alone


def _titles(res):
    return [f["title_en"] for f in res["findings"]]


def test_findings_match_what_is_in_the_query():
    res = S.analyze_sql("DELETE FROM bookings WHERE source LIKE '%OTA' AND status = 'booked'")
    assert any(t.startswith("LIKE '%") for t in _titles(res))                                   # upper-case LIKE was missed
    assert all("source" not in i for i in res["indexes"])                                        # LIKE '%x' can't use an index
    res = S.analyze_sql("SELECT a FROM t WHERE LOWER(name) = 'x' AND DATE(c) = '2026-01-01'")
    assert {"LOWER() on a column in WHERE blocks the index", "DATE() on a column in WHERE blocks the index"} <= set(_titles(res))
    low = next(f for f in res["findings"] if f["title_en"].startswith("LOWER"))
    assert "rewritten" not in low["fix_en"]                                                     # nothing rewrites LOWER()
    res = S.analyze_sql("SELECT a FROM t WHERE UPPER(name) = 'X'")
    assert res["rewritten"] is None and "rewritten" not in " ".join(f["fix_en"] for f in res["findings"])
    mixed = lambda q: any(t.startswith("AND and OR mixed") for t in _titles(S.analyze_sql(q)))  # noqa: E731
    assert mixed("SELECT a FROM t WHERE a IN (1, 2) AND b = 1 OR c = 2")                        # an IN list hid it
    assert not mixed("SELECT a FROM t WHERE a BETWEEN 1 AND 5 OR c = 2")                       # BETWEEN's AND isn't one
    assert not mixed("SELECT a FROM t WHERE a = 1 AND (b = 1 OR c = 2)")
    assert any(t.startswith("SELECT *") for t in _titles(S.analyze_sql("SELECT `t`.* FROM `items` t WHERE `t`.price > 5")))
    res = S.analyze_sql("SELECT a FROM fz102319_stock_moves m WHERE m.item_id IN (1, 2) AND m.warehouse_id = 9 AND m.move_type = 'in' "
                        "ORDER BY m.moved_at")
    from connector import goal_connector as gc
    assert all(gc.parse_index(i) for i in res["indexes"])                                      # index names fit in 64 characters
    res = S.analyze_sql("SELECT g, COUNT(*) AS n FROM t WHERE g > 5 GROUP BY g ORDER BY n DESC")
    assert all("(n)" not in i and ", n" not in i for i in res["indexes"])                       # never index a SELECT alias


PG_DUMP = """CREATE TABLE public.fz_staff (
    id integer NOT NULL,
    staffname character varying,
    shift character varying,
    team_id integer
);

ALTER TABLE ONLY public.fz_staff
    ADD CONSTRAINT fz_staff_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.fz_staff
    ADD CONSTRAINT fz_staff_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.fz_team(id);

CREATE INDEX ix0_fz_staff ON public.fz_staff USING btree (shift);"""


def test_postgres_structure_is_read():
    res = S.analyze_sql("SELECT * FROM fz_staff s WHERE s.shift = 'day' AND s.StaffName = 'x'", PG_DUMP)
    assert res["indexes_verified"] and not res["check_query"]
    assert not any("(shift" in i for i in res["indexes"])                                       # CREATE INDEX ... USING btree
    res = S.analyze_sql("SELECT * FROM fz_staff WHERE id = 5", PG_DUMP)
    assert not res["indexes"]                                                                   # ADD CONSTRAINT ... PRIMARY KEY
    res = S.analyze_sql("SELECT * FROM fz_staff WHERE StaffName = 'x'", PG_DUMP)
    assert res["indexes"] == ["ALTER TABLE fz_staff ADD INDEX idx_fz_staff_staffname (staffname);"]  # PostgreSQL's real name
    res = S.analyze_sql("SELECT * FROM fz_staff WHERE team_id = 3", PG_DUMP)
    assert any("(team_id)" in i for i in res["indexes"])                                        # a PostgreSQL FK has no index


@pytest.mark.skipif(not _up(5432), reason="needs the local PostgreSQL test database")
def test_rewrite_runs_on_postgres_with_the_same_rows():
    eng = F.Engine(F.PG).open()
    try:
        eng.drop(["fzt_visits"])
        eng.run("CREATE TABLE fzt_visits (id int PRIMARY KEY, seen timestamp)")
        eng.run("INSERT INTO fzt_visits VALUES (1, '2026-03-01 00:00:00'), (2, '2026-03-01 23:59:59'), (3, '2026-03-02 00:00:00'), (4, NULL)")
        q = "SELECT id FROM fzt_visits WHERE DATE(seen) = '2026-03-01' ORDER BY id"
        better = S.analyze_sql(q)["rewritten"]
        assert better and eng.run(better) == eng.run(q) == [(1,), (2,)]
    finally:
        eng.drop(["fzt_visits"])
        eng.conn.close()


def test_follow_ups_in_every_language_stay_on_the_query(client, ml_mode):
    from tests.test_ml import say

    say(client, "db bahut dhima hai bhai")                                     # the database agent took the chat
    r = say(client, "-- used on dashboard\nselect *  from fz139_staff s  order by s.staff_name, s.staff_id")
    assert r["relation"] == "agent_run", r["reply"]                             # a pasted query is checked at once (was 'hiring staff')
    for text in ("how do I speed this up", "iski wajah kya hai", "क्वेरी धीमी क्यों चल रही है", "कौनसा इंडेक्स चाहिए", "क्या बदला?"):
        r = say(client, text)
        assert r["relation"] == "agent_reply", (text, r["reply"])
    client.post("/api/new-chat")
    client.cookies.clear()
    r = say(client, "मेरा डेटाबेस बहुत धीरे चल रहा है")
    assert any(a["recipe"] in ("sql_review", "database") for a in r["agents"]), r["reply"]
    r = say(client, "$sql = \"SELECT id FROM fz1_rooms WHERE id = 5\";")
    assert r["relation"] == "agent_run"
    r = say(client, "which indexes should I add")
    assert "koi naya index nahi chahiye" in r["reply"] and "Speed index aur" not in r["reply"], r["reply"]   # Hindi chat: Hinglish answer
    r = say(client, "optimise it please")
    assert "koi naya index bhi nahi chahiye" in r["reply"] and "behtar roop" not in r["reply"], r["reply"]


def test_a_known_docker_description_is_not_confidently_wrong():
    from app.agents.database import _sure
    from app.dbknow import brain

    rec = brain.recognize("hi, connection refused pg from. reach databse on host machine")
    assert rec["id"] == "docker-localhost-connection" or not _sure(rec), rec
