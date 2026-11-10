"""Dates the way real files have them: Excel day numbers (46023) left in a date column, two-digit years, and a
question naming a value the file doesn't have. Every answer is checked against the data written here."""

import datetime as dt

from openpyxl import Workbook, load_workbook

from app import office
from app.office import ask, quality
from app.office.sheetdata import load_tables

EPOCH = dt.date(1899, 12, 30)


def _book(path, rows, header):
    wb = Workbook()
    ws = wb.active
    ws.title = "Bookings"
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def _ask(q, path, hi=False):
    plan = ask.understand(q, load_tables(path))
    return ask.run(plan, path, path + ".out.xlsx", hi, 1)["say"]


def _rows():
    days = [dt.date(2025, 3, 4), dt.date(2025, 11, 30), dt.date(2026, 1, 2), dt.date(2026, 6, 15), dt.date(2026, 8, 9), dt.date(2026, 2, 1)]
    names = ["Aditi Sharma", "Rohan Gill", "Neha Khan", "Karan Mehta", "Isha Rana", "Dev Joshi"]
    dues = [25000, 31000, 47000, 52000, 28000, 39000]           # amounts in the same range as day numbers
    return [[f"BK{k + 1:03}", n, (d - EPOCH).days, due] for k, (n, d, due) in enumerate(zip(names, days, dues))], days


def test_day_numbers_under_a_date_heading_are_read_as_dates(tmp_path):
    rows, days = _rows()
    p = _book(str(tmp_path / "b.xlsx"), rows, ["Booking ID", "Guest Name", "Check In", "Due Amount"])
    t = load_tables(p)[0]
    assert t.kind(2) == "date" and t.kind(3) == "number"          # 'Due Amount' stays money
    assert "4 records" in _ask("how many bookings in 2026", p)
    assert "Isha Rana" in _ask("which is the latest check in", p)
    assert "2,22,000" in _ask("total due amount", p) or "222000" in _ask("total due amount", p)


def test_fix_turns_day_numbers_into_dates_and_never_touches_amounts(tmp_path):
    rows, days = _rows()
    p = _book(str(tmp_path / "b.xlsx"), rows, ["Booking ID", "Guest Name", "Check In", "Due Amount"])
    out = str(tmp_path / "fixed.xlsx")
    office.fix(p, out, ["data_quality"])
    ws = load_workbook(out).active
    got = [ws.cell(r, 3).value for r in range(2, 8)]
    assert [g.date() if isinstance(g, dt.datetime) else g for g in got] == days
    assert [ws.cell(r, 4).value for r in range(2, 8)] == [r[3] for r in rows]


def test_due_amount_is_not_a_date_column():
    log = type("L", (), {"rows": [], "add": lambda self, *a: self.rows.append(a)})()
    wb = Workbook()
    ws = wb.active
    ws.append(["Customer", "Due Amount", "Days Overdue", "Paid On"])
    for k in range(8):
        ws.append([f"C{k}", 21000 + 3000 * k, 30 + k, 46000 + k])
    kinds = quality.check_sheet(ws, log)
    assert kinds[2] == "number" and kinds[3] == "number" and kinds[4] == "date"
    assert all(a[0] != "fix_dates" or a[2].startswith("D") for a in log.rows)
    assert isinstance(ws["D2"].value, dt.datetime) and ws["B2"].value == 21000


def test_headings_that_name_a_date():
    yes = ["Date", "Bill Date", "Check In", "Check-in Date", "Arrival", "DOJ", "Joined On", "Paid On", "Start Date", "Visit Date", "दिनांक"]
    no = ["Due Amount", "Days Overdue", "No of Nights", "Fee Paid", "Amount", "Age", "Rent (Rs)", "Days since due date"]
    assert all(quality.serial_date_heading(h) for h in yes), [h for h in yes if not quality.serial_date_heading(h)]
    assert not any(quality.serial_date_heading(h) for h in no), [h for h in no if quality.serial_date_heading(h)]


def test_two_digit_years_follow_excels_rule():
    # learn.microsoft.com: 00-29 -> 2000-2029, 30-99 -> 1930-1999
    assert quality.parse_date("15/08/35")[0].year == 1935
    assert quality.parse_date("15/08/29")[0].year == 2029
    assert quality.parse_date("01/01/30")[0].year == 1930
    assert quality.parse_date("05-03-05")[0].year == 2005


def test_a_value_the_file_does_not_have_is_zero_not_every_row(tmp_path):
    rows, _ = _rows()
    p = _book(str(tmp_path / "b.xlsx"), rows, ["Booking ID", "Guest Name", "Check In", "Due Amount"])
    say = _ask("how many bookings in Goa", p)
    assert "0" in say and "Goa" in say and "not anywhere" in say
    say = _ask("Goa mein kitni booking hain", p, hi=True)
    assert "Goa" in say and "kahin nahi" in say
    assert "Neha Khan" not in _ask("how many bookings in 2026", p)
    assert "not anywhere" not in _ask("how many bookings in total", p)
    assert "not anywhere" not in _ask("how many bookings in this sheet", p)
