"""The shared mind: reading the person, predicting the next question with the file's own names, checking answers,
and never suggesting something the agent can't do."""

import random

import pytest
from openpyxl import Workbook

from app.mind import office as mo
from app.mind import predict, signals
from app.office import ask
from app.office.sheetdata import load_tables
from tests.test_follow_orders import PRACTICE, open_file  # noqa: F401 - fixture
from tests.test_ml import say
from tests.test_office_agent import _upload, _wait


# ---------------------------------------------------------------- reading the person
@pytest.mark.parametrize("text,key", [
    ("I told you, the sales sheet!", "frustrated"), ("maine kaha tha sales sheet", "frustrated"), ("why is it giving me formula", "frustrated"),
    ("jaldi batao total", "urgent"), ("urgent: total sales", "urgent"), ("before you make changes tell me", "ask_first"),
    ("pehle pucho phir badlo", "ask_first"), ("just do it", "just_do"), ("use XLOOKUP for this", "expert"), ("mujhe kuch samajh nahi aata excel", "beginner"),
    ("formula mat do", "no_formula"), ("always give me the formula", "always_formula"),
])
def test_signals(text, key):
    assert signals.read(text)[key], (text, key)


@pytest.mark.parametrize("text", ["total sales by region", "show pending invoices", "focus on the east region numbers", "fast moving items list"])
def test_ordinary_questions_carry_no_mood(text):
    s = signals.read(text)
    assert not (s["frustrated"] or s["urgent"] or s["ask_first"])


def test_the_same_words_again_is_a_repeat():
    assert signals.read("how many records in sales sheet", ["how many records in the sales sheet"])["repeat"]
    assert not signals.read("total by region", ["how many records in the sales sheet"])["repeat"]


# ---------------------------------------------------------------- predicting
@pytest.fixture()
def db_session(fresh_db):
    from sqlalchemy.orm import Session

    from app.db import get_engine

    with Session(get_engine()) as s:
        yield s


def test_first_visitor_gets_the_starting_chains(db_session):
    s = predict.scores(db_session, "office", ["sum"])
    assert max(s, key=s.get) == "sum_g"


def test_real_sessions_take_over_from_the_starting_chains(db_session):
    for _ in range(12):
        predict.record(db_session, "office", ["rows"], "dups")
    db_session.flush()
    s = predict.scores(db_session, "office", ["rows"])
    assert max(s, key=s.get) == "dups"
    # two steps back is used when it has data
    for _ in range(10):
        predict.record(db_session, "office", ["sum_g", "rank"], "highlight")
    db_session.flush()
    s2 = predict.scores(db_session, "office", ["sum_g", "rank"])
    assert max(s2, key=s2.get) == "highlight"


def test_old_habits_fade_once_a_day(db_session):
    predict.record(db_session, "office", ["rows"], "sort")
    db_session.flush()
    from app.models import PatternStep

    w0 = sum(r.weight for r in db_session.query(PatternStep).filter_by(brain="office", nxt="sort"))
    predict.decay(db_session)
    predict.decay(db_session)                       # a second clean-up the same day does nothing
    w1 = sum(r.weight for r in db_session.query(PatternStep).filter_by(brain="office", nxt="sort"))
    assert w1 == pytest.approx(w0 * predict.DECAY)


# ---------------------------------------------------------------- suggestions are real and doable, on any file
def _book(path, seed):
    rnd = random.Random(seed)
    kinds = [
        ("Fees", ["Receipt No", "Student", "Class", "Month", "Fee", "Mode"],
         lambda i: [f"R{i}", rnd.choice(["Anu", "Bala", "Chiku", "Dev"]), rnd.choice(["5A", "5B", "6A"]), rnd.choice(["Jan", "Feb", "Mar"]),
                    rnd.choice([1500, 2000, 2500]), rnd.choice(["Cash", "UPI"])]),
        ("Orders", ["Order", "Date", "Party", "City", "Amount", "Status"],
         lambda i: [f"O-{i}", __import__("datetime").date(2026, rnd.randint(1, 9), rnd.randint(1, 28)), rnd.choice(["Ram & Co", "Shiv", "Gupta"]),
                    rnd.choice(["Pune", "Agra", "Surat"]), rnd.randint(500, 90000), rnd.choice(["Open", "Closed"])]),
        ("Attendance", ["Emp Code", "Name", "Team", "Days Present", "Leaves"],
         lambda i: [f"E{i}", rnd.choice(["Asha", "Ravi", "Neha", "Om"]), rnd.choice(["Ops", "HR", "Tech"]), rnd.randint(15, 26), rnd.randint(0, 5)]),
    ]
    wb = Workbook()
    wb.remove(wb.active)
    for title, header, mk in kinds:
        ws = wb.create_sheet(title)
        ws.append(header)
        for i in range(1, rnd.randint(30, 80)):
            ws.append(mk(i))
    wb.save(path)


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("hi", [False, True])
def test_every_suggestion_is_understood_and_answered(tmp_path, seed, hi):
    p = tmp_path / f"b{seed}.xlsx"
    _book(p, seed)
    tables = load_tables(str(p))
    history = ["upload"]
    plan = None
    rnd = random.Random(seed)
    for step in range(5):
        sugg = mo.suggestions(predict.prior("office", history[-1]), tables, plan, None, hi)
        assert sugg, (seed, history)
        for s in sugg:
            if s in (mo.EN["fix"], mo.HI["fix"], mo.EN["formula"], mo.HI["formula"]) or "duplicate" in s:
                continue
            got = ask.understand(s, tables)                               # never a "didn't understand"
            res = ask.run(got, str(p), str(tmp_path / f"o{step}.xlsx"), hi, 1)
            assert res["say"], s
        pick = rnd.choice([s for s in sugg if "duplicate" not in s and s not in (mo.EN["formula"], mo.HI["formula"], mo.EN["fix"], mo.HI["fix"])] or sugg)
        plan = ask.understand(pick, tables)
        history.append(mo.label_of(plan))


def test_suggestions_keep_the_persons_focus():
    tables = load_tables(str(PRACTICE))
    plan = ask.understand("how many employees in Sales department", tables)
    sugg = mo.suggestions(predict.prior("office", mo.label_of(plan)), tables, plan, None, False)
    assert all("Employee Data" in s for s in sugg), sugg
    assert any("Sales" in s for s in sugg), sugg


def test_not_understood_gets_the_closest_things_it_can_do():
    tables = load_tables(str(PRACTICE))
    opts = mo.closest_options("mujhe region ke hisaab se kuch summary chahiye", tables, True)
    assert opts and all(ask.understand(o, tables) for o in opts)


# ---------------------------------------------------------------- whole conversations
def test_after_an_answer_the_next_questions_are_offered_and_work(client, open_file):  # noqa: F811
    r = say(client, "before make changes tell me how many records in the sales sheet", open_file)
    assert "60" in r["reply"]
    assert 1 <= len(r["next"]) <= 3 and all("Sales Data" in n for n in r["next"]), r["next"]
    for _ in range(4):                                # follow the chips: each one must give a real answer
        q = r["next"][0]
        r = say(client, q, open_file)
        assert r["goal"]["id"] == open_file
        assert "didn't fully get" not in r["reply"] and "Which sheet" not in r["reply"], (q, r["reply"])
        if not r["next"]:
            break


def test_ask_first_is_remembered_for_the_whole_conversation(client, open_file):  # noqa: F811
    say(client, "before you change anything, how many records in the sales sheet", open_file)
    r = say(client, "pending invoices dikhao", open_file)       # a list normally goes into a new sheet
    f = _wait(client, next(a for a in r["agents"] if a.get("office"))["files"][0]["id"])
    assert f["steps"] == [], "nothing written to the file"


def test_same_question_again_gets_other_readings_not_the_same_answer(client, open_file):  # noqa: F811
    first = say(client, "how many employees in Sales department", open_file)
    again = say(client, "I told you, how many employees in Sales department", open_file)
    assert again["options"] and again["reply"] != first["reply"], again
    assert "Which of these" in again["reply"]


def test_sheet_or_value_answers_and_offers_the_other(client, open_file):  # noqa: F811
    r = say(client, "how many sales records", open_file)
    assert "60" in r["reply"] and r["options"], r
    assert "Department" in r["options"][0] or "Sales" in r["options"][0]


def test_formula_for_the_last_answer(client, open_file):  # noqa: F811
    say(client, "total sales by region", open_file)
    r = say(client, "give me the formula", open_file)
    assert "SUMIFS(" in r["reply"]


def test_annoyed_or_in_a_hurry_gets_no_suggestions(client, open_file):  # noqa: F811
    r = say(client, "urgent: total sales by region", open_file)
    assert r["next"] == []


def test_returning_visitor_keeps_their_style(client, ml_mode):
    r = say(client, "I need to work on my excel file. always give me the formula too")
    agent = next(a for a in r["agents"] if a.get("office"))
    _upload(client, agent["id"], PRACTICE.read_bytes(), "x.xlsx")
    say(client, "total sales by region", r["goal"]["id"])
    r2 = say(client, "new excel work")                           # a new conversation, same person
    agent2 = next((a for a in r2["agents"] if a.get("office")), None)
    if agent2 is None:
        pytest.skip("new conversation went to the same goal")
    _upload(client, agent2["id"], PRACTICE.read_bytes(), "y.xlsx")
    r3 = say(client, "average salary kitni hai", r2["goal"]["id"])
    assert "AVERAGE(" in r3["reply"], r3["reply"]


def test_privacy_delete_removes_the_working_style(client, open_file):  # noqa: F811
    say(client, "always give me the formula", open_file)
    from sqlalchemy.orm import Session

    from app.db import get_engine
    from app.models import MindSession, VisitorStyle

    with Session(get_engine()) as db:
        assert db.query(VisitorStyle).count() >= 1
    client.post("/api/privacy/delete-all")
    with Session(get_engine()) as db:
        assert db.query(VisitorStyle).count() == 0 and db.query(MindSession).count() == 0


def test_database_and_website_next_steps_are_the_real_next_step(client, ml_mode):
    r = say(client, "my database is very slow")
    assert r["next"] == ["How do I connect my database?"], r["next"]       # nothing else works before it's connected
    r = say(client, "my website has issues")
    assert r["next"] == [], "the next step is the address, already asked"
    r = say(client, "check example.com", r["goal"]["id"])
    assert "Check another website" in r["next"]


def test_a_named_column_value_gets_no_other_reading(client, open_file):  # noqa: F811
    r = say(client, "how many employees in Sales department", open_file)
    assert "10" in r["reply"] and r["options"] == [], r


@pytest.mark.parametrize("first", ["sales sheet mein kitne records hain", "region wise total sales batao", "pending invoices dikhao",
                                   "HR mein kitne log hain", "kya galat hai is file mein"])
def test_hinglish_conversations_follow_the_chips(client, open_file, first):  # noqa: F811
    r = say(client, first, open_file)
    for _ in range(3):
        if not r["next"]:
            break
        q = r["next"][-1]
        r = say(client, q, open_file)
        assert "samajh nahi aaya" not in r["reply"] and "didn't fully get" not in r["reply"], (q, r["reply"])
