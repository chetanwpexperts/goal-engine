"""'Do what I asked': real requests (Hinglish, English, typos) on a real file, each with the exact answer expected.
When someone reports a wrong reply, their sentence goes here so it can never come back."""

import time
from pathlib import Path

import pytest

from tests.test_ml import say
from tests.test_office_agent import _upload, _wait

PRACTICE = Path(__file__).parent / "data" / "practice.xlsx"

CASES = [
    # (what the person types, words that must be in the reply)
    ("total sales kitni hai", ["34,14,200"]),
    ("region wise total sales batao", ["South", "12,33,200"]),
    ("har department mein kitne employees hain", ["Sales: 10", "HR: 8"]),
    ("average salary kitni hai", ["62,200"]),
    ("IT department ki average salary", ["63,625"]),
    ("sabse zyada salary kiski hai", ["Aditi Bansal", "94,000"]),
    ("top 5 orders by net sales", ["ORD-0058", "2,94,000"]),
    ("east region ki total sales", ["8,93,900"]),
    ("Priya ki total sales", ["4,45,100"]),
    ("approved invoices ka total", ["15,30,676"]),
    ("laptop kitne bike", ["46"]),
    ("kaunsa product sabse zyada bika", ["Router", "51"]),
    ("which vendor has highest invoice total", ["Reliable Systems"]),
    ("what is total tax amount", ["4,78,154"]),
    ("HR mein kitne log hain", ["8"]),
    ("delhi mein kitne employees hain", ["14"]),
    ("pending invoices dikhao", ["17 rows"]),
    ("Purchase Data se unit price Inventory mein laao", ["Unit Price (from Purchase Data)"]),
    ("invoice aging nikalo", ["0-30", "90+"]),
    ("inventory mein highlight karo jahan closing stock reorder level se kam hai", ["2 rows", "Closing Stock < Reorder Level"]),
    ("net sales 100000 se zyada wale orders highlight karo", ["7 rows"]),
    ("month wise sales", ["2026-03"]),
    ("sales data mein kaunse columns hain", ["Order ID", "Net Sales"]),
    ("muje sales sheet me duplicates dhund k do", ["Sales Data mein koi duplicate nahi", "Messy Data: 2"]),
    ("give me duplicate formula", ["COUNTIFS("]),
]


ROWS_CASES = [
    ("before make changes tell me how many records in the sales sheet", "Sales Data has 60 records"),
    ("how many rows in employee data", "Employee Data has 40 records"),
    ("sheets name", "7 sheets: Read Me"),
    ("sheet names", "7 sheets: Read Me"),
    ("which sheets are there", "7 sheets: Read Me"),
    ("kitne records hain inventory sheet mein", "Inventory mein 6 records"),
    ("how many employees in Sales department", "Department = Sales"),
    ("how many records of Sales department in messy data sheet", "Messy Data (Department = Sales)"),
]


@pytest.fixture()
def open_file(client, ml_mode):
    r = say(client, "muje excel sheet me kaam krna hai")
    agent = next(a for a in r["agents"] if a.get("office"))
    _upload(client, agent["id"], PRACTICE.read_bytes(), "Excel_Practice.xlsx")
    return r["goal"]["id"]


@pytest.mark.parametrize("text,must", CASES, ids=[c[0][:40] for c in CASES])
def test_does_what_was_asked(client, open_file, text, must):
    r = say(client, text, open_file)
    assert r["goal"]["id"] == open_file, "stayed on the file's conversation"
    for m in must:
        assert m in r["reply"], f"{text!r} -> {r['reply']!r} (missing {m!r})"


def test_asks_instead_of_guessing_then_uses_the_answer(client, open_file):
    r = say(client, "average nikalo", open_file)
    assert "Employee Data" in r["options"]
    r = say(client, "Employee Data", open_file)
    assert "62,200" in r["reply"], r["reply"]


def test_small_talk_and_database_go_elsewhere(client, open_file):
    r = say(client, "thanks", open_file)
    assert "Answer" not in r["reply"]


def test_not_understood_can_be_sent_to_learning(client, open_file):
    r = say(client, "is sheet ka mausam kaisa hai", open_file)
    assert "Agent ko sikhao" in r["options"], r
    r = say(client, "Agent ko sikhao", open_file)
    assert "learning list" in r["reply"]
    from app.db import _SessionLocal
    from app.models import LearnRequest

    with _SessionLocal() as db:
        assert db.query(LearnRequest).filter(LearnRequest.brain == "office", LearnRequest.text.like("%mausam%")).count() == 1


@pytest.mark.parametrize("text", ["capital small letters ek jaise karo", "spelling same kar do", "text wale numbers ko number bana do"])
def test_plain_orders_change_the_file(client, open_file, text):
    r = say(client, text, open_file)
    assert "theek kar raha" in r["reply"] or "Fixing" in r["reply"], (text, r["reply"])


def test_english_by_default(client, ml_mode):
    r = say(client, "I need to work on my excel file")
    assert "Drop the file" in r["reply"], r["reply"]
    agent = next(a for a in r["agents"] if a.get("office"))
    assert agent["lang"] == "en" and agent["name"] == "Excel and file work"
    _upload(client, agent["id"], PRACTICE.read_bytes(), "Excel_Practice.xlsx")
    r = say(client, "total sales by region", r["goal"]["id"])
    assert "Sales Data" in r["reply"] and "Formula" not in r["reply"] and "jod di" not in r["reply"], r["reply"]


@pytest.mark.parametrize("text", ["fix everything", "clean the file", "sab theek karo", "fix all problems"])
def test_fix_everything_in_any_order(client, open_file, text):
    r = say(client, text, open_file)
    assert "Fixing" in r["reply"] or "theek kar raha" in r["reply"] or "Nothing left" in r["reply"] or "kuch nahi bacha" in r["reply"], (text, r["reply"])


@pytest.mark.parametrize("text", ["my excel sheet have issues", "excel me problem hai", "my spreadsheet is broken", "meri sheet kharab hai",
                                  "I need help with a csv file", "एक्सेल फाइल में दिक्कत है"])
def test_any_excel_problem_opens_the_excel_agent(client, ml_mode, text):
    r = say(client, text)
    assert any(a.get("office") for a in r["agents"]), (text, r["reply"])
    assert "no agent" not in r["reply"].lower() and "abhi agent nahi" not in r["reply"].lower()


@pytest.mark.parametrize("text,agent", [("my database has issues", "database"), ("mysql not working properly", "database"),
                                        ("my website has issues", "website_check"), ("website kharab hai", "website_check"),
                                        ("my wordpress site has problems", "website_check")])
def test_vague_problems_open_the_right_agent(client, ml_mode, text, agent):
    r = say(client, text)
    assert agent in [a.get("recipe") for a in r["agents"]], (text, r["reply"])
    assert "????" not in r["reply"], "never names a specific cause it can't know yet"


@pytest.mark.parametrize("text,must", ROWS_CASES, ids=[c[0][:40] for c in ROWS_CASES])
def test_counts_what_was_named(client, open_file, text, must):
    r = say(client, text, open_file)
    assert must in r["reply"], (text, r["reply"])
    assert "Formula" not in r["reply"] and "=COUNT" not in r["reply"], "no formula unless asked"


def test_a_question_does_not_change_the_file_and_the_card_shows_the_answer(client, open_file):
    r = say(client, "before make changes tell me how many records in the sales sheet", open_file)
    assert "Nothing in your file was changed" in r["reply"]
    f = next(a for a in r["agents"] if a.get("office"))["files"][0]
    f = _wait(client, f["id"])
    assert f["steps"] == [], "no new version of the file"
    res = f["result"]
    assert res["value"] == 60 and res["chart"]["highlight"] == "Sales Data"
    assert res["sample"]["total"] == 60 and res["sample"]["rows"][0][0] == "ORD-0001"
    assert res["formula"] and not res.get("show_formula")      # in Details only


def test_formula_only_when_asked(client, open_file):
    r = say(client, "region wise total sales ka formula bhi do", open_file)
    assert "SUMIFS(" in r["reply"]
