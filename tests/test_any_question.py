"""Questions people really ask, on files the agent has never seen (school, office staff, shop, clinic, hotel).
The right answer is worked out here from the data itself, separately from the agent."""

import datetime
import re
import tempfile
from collections import Counter

import pytest

from app.office import ask
from app.office import understand as U
from app.office.sheetdata import load_tables
from tests.battery_books import make_all


@pytest.fixture(scope="module")
def books(tmp_path_factory):
    return make_all(str(tmp_path_factory.mktemp("battery")))


def answer(books, name, q, hi=False):
    path, _ = books[name]
    plan = ask.understand(q, load_tables(path))
    return ask.run(plan, path, tempfile.mktemp(suffix=".xlsx"), hi, 1)


def rows(books, name, sheet=0):
    return books[name][1][sheet][2]


def fmt(n):
    return ask._fmt(n)


def test_gender_column_is_used(books):
    g = Counter(r[2] for r in rows(books, "school"))
    r = answer(books, "school", "how many girls and how many boys")
    assert f"F: {g['F']}" in r["say"] and f"M: {g['M']}" in r["say"]
    assert f"{g['F']} records" in answer(books, "school", "kitni ladkiyan hain")["say"]


def test_gender_estimated_from_names_when_there_is_no_column(books):
    r = answer(books, "hr", "how many women work here")
    assert "estimated from the names" in r["say"] and r.get("estimate")


def test_conditions_on_numbers(books):
    pats = rows(books, "clinic")
    assert f"{sum(1 for r in pats if r[3] > 60)} rows" in answer(books, "clinic", "patients above 60")["say"]
    assert f"{sum(1 for r in pats if r[3] < 12)} records" in answer(books, "clinic", "how many children below 12")["say"]
    staff = rows(books, "hr")
    n = sum(1 for r in staff if r[5] > 50000)
    assert f"{n} records" in answer(books, "hr", "how many earn more than 50k")["say"]
    assert f"{n} records" in answer(books, "hr", "salary 50000 se zyada kitne logon ki hai")["say"]


def test_age_from_date_of_birth_and_years_since_joining(books):
    today = datetime.date.today()
    age = lambda d: today.year - d.year - ((today.month, today.day) < (d.month, d.day))  # noqa: E731
    n = sum(1 for r in rows(books, "school") if age(r[5]) > 13)
    assert f"{n} records" in answer(books, "school", "how many students are older than 13")["say"]
    m = sum(1 for r in rows(books, "hr") if age(r[4]) > 5)
    assert f"{m} records" in answer(books, "hr", "how many have more than 5 years experience")["say"]


def test_dates(books):
    staff = rows(books, "hr")
    for y in (2020, 2025):
        assert f"{sum(1 for r in staff if r[4].year == y)} records" in answer(books, "hr", f"how many joined in {y}")["say"]
    latest = max(staff, key=lambda r: r[4])
    assert latest[1] in answer(books, "hr", "who joined most recently")["say"]


def test_groups_without_saying_count(books):
    c = Counter(r[4] for r in rows(books, "clinic"))
    r = answer(books, "clinic", "doctor wise patients")
    assert all(f"{d}: {n}" in r["say"] for d, n in c.items())
    cl = Counter(r[3] for r in rows(books, "school"))
    r = answer(books, "school", "class wise students")
    assert all(f"{k}: {v}" in r["say"] for k, v in cl.items())


def test_how_many_of_a_number_column_adds_it_up(books):
    assert fmt(sum(r[5] for r in rows(books, "hotel"))) in answer(books, "hotel", "how many nights in total")["say"]
    assert fmt(sum(r[1] for r in rows(books, "hotel", 1))) in answer(books, "hotel", "how many rooms do we have")["say"]


def test_shop_sales_and_profit_use_quantity(books):
    b = rows(books, "shop")
    assert fmt(sum(r[4] * r[6] for r in b)) in answer(books, "shop", "total sales")["say"]
    assert fmt(sum((r[6] - r[5]) * r[4] for r in b)) in answer(books, "shop", "total profit")["say"]
    assert fmt(sum((r[6] - r[5]) * r[4] for r in b)) in answer(books, "shop", "kitna munafa hua")["say"]
    qty = Counter()
    for r in b:
        qty[r[3]] += r[4]
    assert qty.most_common(1)[0][0] in answer(books, "shop", "which item sells most")["say"]


def test_filled_in_and_short_values(books):
    assert "60 records" in answer(books, "hr", "kitne logon ka mobile hai")["say"]
    s = rows(books, "school")
    assert f"{sum(1 for r in s if r[4] == 'A')} records" in answer(books, "school", "section A mein kitne")["say"]
    assert f"{sum(1 for r in s if r[3] == '8' and r[2] == 'F')} rows" in answer(books, "school", "list of girls in class 8")["say"]


def test_not_in_the_file_is_said_plainly(books):
    path, _ = books["school"]
    with pytest.raises(U.Missing):
        ask.understand("how many students have phone numbers", load_tables(path))


def test_best_rated_uses_the_sheet_with_ratings(books):
    b = rows(books, "hotel")
    avg = {k: sum(r[8] for r in b if r[4] == k) / sum(1 for r in b if r[4] == k) for k in {r[4] for r in b}}
    assert max(avg, key=avg.get) in answer(books, "hotel", "best rated room type")["say"].split(" has the highest")[0]


def test_averages_keep_their_decimals(books):
    b = rows(books, "hotel")
    r = answer(books, "hotel", "average rating")
    assert re.search(r"= \d\.\d", r["say"]), r["say"]


QUESTIONS = {
    "school": ["how many students", "average maths marks of girls", "class wise students", "top 5 in science", "city wise count", "show students sheet",
               "average science marks", "boys ki average maths", "total marks of all students", "fee kitno ne nahi bhari"],
    "hr": ["gender wise count", "department wise salary total", "average salary", "kitne log gmail use karte hain", "how many left the company",
           "active employees in sales", "managers kitne hain", "show me staff data", "email domain wise count", "average salary of men"],
    "shop": ["profit by item", "upi payments total", "kitni bills cash mein", "month wise sales", "top 5 bills", "customer wise total", "give me bills", "best customer"],
    "clinic": ["how many female patients", "average age", "total bill amount", "pending payments", "kitne mareez dr rao ke paas", "month wise visits"],
    "hotel": ["total revenue", "room type wise bookings", "bookings from website", "suite ki total amount", "average stay", "month wise bookings", "kitne guest ladies the"],
}


@pytest.mark.parametrize("name,q", [(n, q) for n, qs in QUESTIONS.items() for q in qs])
def test_every_question_gets_an_answer(books, name, q):
    r = answer(books, name, q)
    assert r["say"] and "didn't fully get" not in r["say"], (q, r["say"])


@pytest.mark.parametrize("name,q,must", [
    ("clinic", "डॉक्टर के हिसाब से मरीज", "Dr Rao"), ("clinic", "60 साल से ज्यादा उम्र वाले मरीज", "Age > 60"),
    ("hr", "विभाग के हिसाब से कुल तनख्वाह", "Accounts"), ("school", "कक्षा 7 में कितने छात्र हैं", "Class = 7"),
    ("school", "कितनी लड़कियां हैं", "Gender = F"), ("shop", "कुल बिक्री कितनी है", "Qty x Selling Price"),
    ("hr", "kitne logon ka mobile hai", "Mobile filled in"), ("school", "maths mein 90 se zyada kitne bachon ke aaye", "Maths > 90"),
])
def test_hindi_and_hinglish(books, name, q, must):
    r = answer(books, name, q, hi=True)
    assert must in r["say"], (q, r["say"])
    assert "Class = 7, Gender" not in r["say"], "'में' is 'in', not 'men'"
