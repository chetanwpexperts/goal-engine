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
    ("region wise total sales batao", ["South", "12,33,200", "SUMIFS("]),
    ("har department mein kitne employees hain", ["Sales: 10", "HR: 8", "COUNTIFS("]),
    ("average salary kitni hai", ["62,200", "AVERAGE("]),
    ("IT department ki average salary", ["63,625", "AVERAGEIFS("]),
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
    ("pending invoices dikhao", ["17 rows", "FILTER("]),
    ("Purchase Data se unit price Inventory mein laao", ["Unit Price (from Purchase Data)", "INDEX("]),
    ("invoice aging nikalo", ["0-30", "90+"]),
    ("inventory mein highlight karo jahan closing stock reorder level se kam hai", ["2 rows", "Closing Stock < Reorder Level"]),
    ("net sales 100000 se zyada wale orders highlight karo", ["7 rows"]),
    ("month wise sales", ["2026-03"]),
    ("sales data mein kaunse columns hain", ["Order ID", "Net Sales"]),
    ("muje sales sheet me duplicates dhund k do", ["Sales Data mein koi duplicate nahi", "Messy Data: 2"]),
    ("give me duplicate formula", ["COUNTIFS("]),
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
    assert "Sales Data" in r["reply"] and "Formula" in r["reply"] and "jod di" not in r["reply"], r["reply"]
