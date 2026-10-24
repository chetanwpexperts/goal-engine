"""The Excel agent's test generator: random files from many fields, with the mess real files have, and random questions
people really ask, in English / Hinglish / Hindi. The right answer to every question is worked out here from the
generated data, separately from the agent.

    python -m tests.fuzz.sheets --seeds 50          # a big run, prints the pass rate and every failure
    python -m tests.fuzz.sheets --seed 7 --show     # one file and its questions, with the answers

The normal test suite runs a small fixed set (tests/test_fuzz_sheets.py). The nightly run uses new seeds every night.
"""

import argparse
import datetime as dt
import json
import random
import re
import sys
import tempfile
from collections import Counter, defaultdict

from openpyxl import Workbook

F_NAMES = ["Aditi", "Pooja", "Neha", "Simran", "Kavya", "Riya", "Anjali", "Sneha", "Isha", "Meera", "Tanvi", "Priya", "Divya", "Nisha", "Shreya"]
M_NAMES = ["Arjun", "Rohan", "Rahul", "Vikram", "Aman", "Karan", "Dev", "Ishaan", "Manav", "Yash", "Aarav", "Kabir", "Nikhil", "Sahil", "Varun"]
LAST = ["Sharma", "Verma", "Gill", "Khan", "Singh", "Mehta", "Rana", "Joshi", "Bansal", "Kaur", "Malik", "Gupta"]
CITIES = ["Mohali", "Kharar", "Zirakpur", "Panchkula", "Ludhiana", "Amritsar", "Jalandhar"]

# ---------------------------------------------------------------- fields: what a file in that field has
# column: (key, header variants, kind, params, spoken word people use for it)
FIELDS = {
    "school": {"sheet": ["Students", "Student List", "Class Register", "Data"], "entity": ("students", "bachche"),
               "cols": [("id", ["Roll No", "Roll No.", "ROLL NO", "Adm No"], "id", {"prefix": ""}, None),
                        ("name", ["Student Name", "Name", "NAME OF STUDENT", "Student"], "person", {}, "name"),
                        ("gender", ["Gender", "Sex", "M/F"], "gender", {"values": ["M", "F"]}, None),
                        ("class", ["Class", "Std", "Grade"], "cat", {"values": ["6", "7", "8", "9"]}, "class"),
                        ("section", ["Section", "Sec"], "cat", {"values": ["A", "B", "C"]}, "section"),
                        ("dob", ["DOB", "Date of Birth", "Birth Date"], "date", {"from": (2009, 2015)}, None),
                        ("fee", ["Fee", "Fees (Rs)", "Monthly Fee", "Fee Amount"], "amount", {"choices": [1500, 2000, 2500, 3000]}, "fee"),
                        ("paid", ["Fee Paid", "Paid?", "Fee Status"], "yesno", {}, "paid"),
                        ("city", ["City", "Town", "Location"], "cat", {"values": CITIES[:4]}, "city")]},
    "hr": {"sheet": ["Staff", "Employees", "Emp Master", "HR Data"], "entity": ("employees", "log"),
           "cols": [("id", ["Emp Code", "Employee ID", "EMP ID", "Code"], "id", {"prefix": "E"}, None),
                    ("name", ["Name", "Employee Name", "Emp Name", "Full Name"], "person", {}, "name"),
                    ("dept", ["Department", "Dept", "Dept."], "cat", {"values": ["Sales", "Accounts", "HR", "IT", "Ops"]}, "department"),
                    ("desig", ["Designation", "Role", "Post"], "cat", {"values": ["Executive", "Manager", "Intern", "Lead"]}, "designation"),
                    ("doj", ["Date of Joining", "DOJ", "Joining Date"], "date", {"from": (2015, 2025)}, "joining"),
                    ("salary", ["Salary", "Monthly Salary", "Salary (Rs)", "CTC", "Gross Salary"], "amount", {"lo": 15000, "hi": 120000, "step": 500}, "salary"),
                    ("email", ["Email", "Email ID", "Mail"], "email", {}, None),
                    ("status", ["Status", "Emp Status"], "cat", {"values": ["Active", "Left"]}, "status")]},
    "shop": {"sheet": ["Bills", "Sales", "Invoice List", "Daily Sales"], "entity": ("bills", "bill"),
             "cols": [("id", ["Bill No", "Invoice No", "Bill #"], "id", {"prefix": "B"}, None),
                      ("date", ["Date", "Bill Date", "Invoice Date"], "date", {"from": (2026, 2026)}, "date"),
                      ("cust", ["Customer", "Party", "Customer Name", "Party Name"], "cat", {"values": ["Ramesh", "Gupta Store", "Walk-in", "Jain Traders", "Kumar & Sons"]}, "customer"),
                      ("item", ["Item", "Product", "Item Name"], "cat", {"values": ["Soap", "Rice 5kg", "Oil 1L", "Tea", "Sugar"]}, "item"),
                      ("qty", ["Qty", "Quantity", "Qty."], "qty", {"lo": 1, "hi": 12}, "quantity"),
                      ("amount", ["Amount", "Total", "Bill Amount", "Net Amount", "Amt"], "amount", {"lo": 50, "hi": 9000, "step": 10}, "amount"),
                      ("mode", ["Payment Mode", "Mode", "Paid By"], "cat", {"values": ["Cash", "UPI", "Card"]}, "payment")]},
    "clinic": {"sheet": ["Patients", "OPD Register", "Visits"], "entity": ("patients", "mareez"),
               "cols": [("id", ["Patient ID", "Reg No", "OPD No"], "id", {"prefix": "P"}, None),
                        ("name", ["Patient Name", "Name"], "person", {}, "name"),
                        ("gender", ["Sex", "Gender"], "gender", {"values": ["Male", "Female"]}, None),
                        ("age", ["Age", "Age (Yrs)", "AGE"], "qty", {"lo": 2, "hi": 90}, "age"),
                        ("doctor", ["Doctor", "Consultant", "Dr Name"], "cat", {"values": ["Dr Rao", "Dr Mehta", "Dr Kaur", "Dr Sethi"]}, "doctor"),
                        ("date", ["Visit Date", "Date"], "date", {"from": (2025, 2026)}, "visit"),
                        ("bill", ["Bill Amount", "Charges", "Fee", "Amount"], "amount", {"choices": [300, 500, 800, 1200, 3500, 15000]}, "bill"),
                        ("pay", ["Payment", "Payment Status", "Paid"], "cat", {"values": ["Paid", "Pending"]}, "payment")]},
    "hotel": {"sheet": ["Bookings", "Reservations"], "entity": ("bookings", "booking"),
              "cols": [("id", ["Booking ID", "Booking No"], "id", {"prefix": "BK"}, None),
                       ("name", ["Guest Name", "Guest"], "person", {}, "guest"),
                       ("checkin", ["Check In", "Arrival", "Check-in Date"], "date", {"from": (2026, 2026)}, "check in"),
                       ("room", ["Room Type", "Room", "Category"], "cat", {"values": ["Deluxe", "Suite", "Standard"]}, "room type"),
                       ("nights", ["Nights", "No of Nights", "Stay (Nights)"], "qty", {"lo": 1, "hi": 7}, "nights"),
                       ("amount", ["Amount", "Total Amount", "Bill"], "amount", {"lo": 2000, "hi": 60000, "step": 100}, "amount"),
                       ("source", ["Source", "Booked Via", "Channel"], "cat", {"values": ["Website", "Walk-in", "Agent", "OTA"]}, "source"),
                       ("rating", ["Rating", "Stars", "Guest Rating"], "qty", {"lo": 1, "hi": 5}, "rating")]},
    "transport": {"sheet": ["Trips", "Deliveries", "Trip Log"], "entity": ("trips", "trip"),
                  "cols": [("id", ["Trip ID", "LR No", "Docket No"], "id", {"prefix": "T"}, None),
                           ("date", ["Date", "Trip Date", "Dispatch Date"], "date", {"from": (2026, 2026)}, "date"),
                           ("driver", ["Driver", "Driver Name"], "person", {"male_only": True}, "driver"),
                           ("route", ["Route", "From-To", "Destination"], "cat", {"values": ["Delhi", "Jaipur", "Ambala", "Shimla", "Dehradun"]}, "route"),
                           ("km", ["KM", "Distance (km)", "Kms"], "qty", {"lo": 40, "hi": 600}, "distance"),
                           ("freight", ["Freight", "Freight Amount", "Charges"], "amount", {"lo": 2000, "hi": 40000, "step": 100}, "freight"),
                           ("status", ["Status", "Delivery Status"], "cat", {"values": ["Delivered", "In Transit", "Pending"]}, "status")]},
    "gym": {"sheet": ["Members", "Membership"], "entity": ("members", "member"),
            "cols": [("id", ["Member ID", "ID"], "id", {"prefix": "M"}, None),
                     ("name", ["Member Name", "Name"], "person", {}, "name"),
                     ("plan", ["Plan", "Package", "Membership Type"], "cat", {"values": ["Monthly", "Quarterly", "Yearly"]}, "plan"),
                     ("join", ["Join Date", "Start Date", "Joined On"], "date", {"from": (2022, 2026)}, "joined"),
                     ("fee", ["Fee Paid", "Amount Paid", "Fees"], "amount", {"choices": [1500, 4000, 12000]}, "fee"),
                     ("trainer", ["Trainer", "Coach"], "cat", {"values": ["Raj", "Sunny", "Pinky"]}, "trainer"),
                     ("phone", ["Mobile", "Phone", "Contact No"], "phone", {}, None)]},
    "expenses": {"sheet": ["Expenses", "Kharcha", "Expense Sheet"], "entity": ("expenses", "kharche"), "money_entity": True,
                 "cols": [("date", ["Date", "Expense Date"], "date", {"from": (2026, 2026)}, "date"),
                          ("head", ["Head", "Category", "Expense Head", "Type"], "cat", {"values": ["Rent", "Salary", "Electricity", "Travel", "Food", "Stationery"]}, "category"),
                          ("by", ["Paid By", "Spent By"], "cat", {"values": ["Office", "Ramesh", "Suresh"]}, "paid by"),
                          ("amt", ["Amount", "Amt (Rs)", "Expense Amount", "Rs."], "amount", {"lo": 100, "hi": 50000, "step": 50}, "amount"),
                          ("approved", ["Approved", "Approved?"], "yesno", {}, "approved")]},
    "rent": {"sheet": ["Rent", "Rent Collection", "Kiraya", "Tenants"], "entity": ("tenants", "kirayedar"),
             "cols": [("id", ["Receipt No", "Rcpt No", "S.No"], "id", {"prefix": "R-"}, None),
                      ("name", ["Tenant Name", "Tenant", "Kirayedar"], "person", {}, "tenant"),
                      ("building", ["Building", "Property", "Society"], "cat", {"values": ["Shanti Niwas", "Green Park", "Sai Residency"]}, "building"),
                      ("floor", ["Floor", "Flr"], "cat", {"values": ["Ground", "First", "Second"]}, "floor"),
                      ("rent", ["Rent", "Rent Amount", "Monthly Rent", "Rent (Rs)"], "amount", {"choices": [6500, 8000, 9500, 12000, 15000]}, "rent"),
                      ("on", ["Paid On", "Payment Date", "Date"], "date", {"from": (2025, 2026)}, "date"),
                      ("mode", ["Mode", "Payment Mode", "Paid Via"], "cat", {"values": ["Cash", "UPI", "Cheque"]}, "mode"),
                      ("paid", ["Paid?", "Received", "Rent Received"], "yesno", {}, "received"),
                      ("phone", ["Mobile", "Phone No"], "phone", {}, None)]},
}

MESS = ["title_row", "blank_rows", "totals_row", "date_text", "money_text", "upper_headers", "trailing_spaces", "empty_col"]


# ---------------------------------------------------------------- making a file
def make_book(seed: int, folder: str) -> dict:
    rnd = random.Random(seed)
    field = rnd.choice(sorted(FIELDS))
    spec = FIELDS[field]
    sheet = rnd.choice(spec["sheet"])
    cols = [c for c in spec["cols"] if c[2] in ("id", "person") or rnd.random() > 0.12]
    headers = {c[0]: rnd.choice(c[1]) for c in cols}
    n = rnd.randint(25, 160)
    rows = []
    for k in range(n):
        r = {}
        g = rnd.choice("FM")
        for key, _, kind, p, _ in cols:
            if kind == "id":
                r[key] = f"{p['prefix']}{k + 1:03}" if p["prefix"] else k + 1
            elif kind == "person":
                gg = "M" if p.get("male_only") else g
                first = rnd.choice(F_NAMES if gg == "F" else M_NAMES)
                r[key] = f"{first} {rnd.choice(LAST)}"
                r["_gender"] = gg
            elif kind == "gender":
                r[key] = p["values"][0 if g == "M" else 1]
                r["_gender"] = g
            elif kind == "cat":
                r[key] = rnd.choice(p["values"])
            elif kind == "yesno":
                r[key] = rnd.choice(["Yes", "No"])
            elif kind == "date":
                y0, y1 = p["from"]
                r[key] = dt.date(rnd.randint(y0, y1), rnd.randint(1, 12), rnd.randint(1, 28))
            elif kind == "amount":
                r[key] = rnd.choice(p["choices"]) if "choices" in p else rnd.randrange(p["lo"], p["hi"], p.get("step", 1))
            elif kind == "qty":
                r[key] = rnd.randint(p["lo"], p["hi"])
            elif kind == "email":
                nm = (r.get("name") or "user").split()[0].lower()
                r[key] = f"{nm}{k}@{rnd.choice(['gmail.com', 'yahoo.com', 'company.in'])}"
            elif kind == "phone":
                r[key] = f"98{rnd.randint(10000000, 99999999)}"
        rows.append(r)
    mess = [m for m in MESS if rnd.random() < 0.35]
    path = f"{folder}/fuzz_{seed}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    hdr = [headers[c[0]] for c in cols]
    if "upper_headers" in mess:
        hdr = [h.upper() for h in hdr]
    if "trailing_spaces" in mess:
        hdr = [h + "  " for h in hdr]
    if "empty_col" in mess:
        hdr = hdr[:2] + [None] + hdr[2:]
    if "title_row" in mess:
        ws.append([f"{sheet} - {rnd.choice(['April 2026', 'FY 2025-26', 'Report'])}"])
        ws.append([])
    ws.append(hdr)
    for k, r in enumerate(rows):
        vals = []
        for key, _, kind, _, _ in cols:
            v = r[key]
            if kind == "date" and "date_text" in mess:
                v = v.strftime("%d-%m-%Y")
            if kind == "amount" and "money_text" in mess:
                v = f"₹ {v:,}"
            vals.append(v)
        if "empty_col" in mess:
            vals = vals[:2] + [None] + vals[2:]
        ws.append(vals)
        if "blank_rows" in mess and k % 23 == 11:
            ws.append([])
    if "totals_row" in mess:
        amount = next((c for c in cols if c[2] == "amount"), None)
        if amount is not None:
            tot = [None] * len(hdr)
            tot[0] = "Total"
            idx = [c[0] for c in cols].index(amount[0]) + (1 if "empty_col" in mess and [c[0] for c in cols].index(amount[0]) >= 2 else 0)
            tot[idx] = sum(r[amount[0]] for r in rows)
            ws.append(tot)
    wb.save(path)
    return {"seed": seed, "field": field, "path": path, "sheet": sheet, "cols": cols, "headers": headers, "rows": rows, "mess": mess,
            "entity": spec["entity"], "money_entity": spec.get("money_entity", False)}


# ---------------------------------------------------------------- questions with their right answers
def _sp(c):
    return c[4] or c[1][0].lower()


def questions(book: dict, rnd: random.Random) -> list[dict]:
    rows, cols = book["rows"], book["cols"]
    ent_en, ent_hi = book["entity"]
    by = {c[0]: c for c in cols}
    cats = [c for c in cols if c[2] == "cat"]
    amounts = [c for c in cols if c[2] == "amount"]
    qtys = [c for c in cols if c[2] == "qty"]
    dates = [c for c in cols if c[2] == "date"]
    person = next((c for c in cols if c[2] == "person"), None)
    has_gender = any(c[2] == "gender" for c in cols)
    out = []

    def add(family, q, kind, truth, **kw):
        out.append({"family": family, "q": q, "kind": kind, "truth": truth, **kw})

    add("count_all", rnd.choice([f"how many {ent_en}", f"kitne {ent_hi} hain", f"total {ent_en} count", f"how many {ent_en} are there"]), "number", len(rows))
    for c in rnd.sample(cats, min(2, len(cats))):
        v = rnd.choice(c[3]["values"])
        n = sum(1 for r in rows if r[c[0]] == v)
        short = len(v) <= 2                              # 'C', '7': people say 'section C', 'class 7'
        add("count_where", rnd.choice([f"how many {ent_en} in {_sp(c)} {v}", f"{_sp(c)} {v} mein kitne {ent_hi} hain"] if short else
                                      [f"how many {ent_en} in {v}", f"{v} mein kitne {ent_hi} hain", f"how many {ent_en} with {_sp(c)} {v}",
                                       f"{v} wale kitne hain"]), "number", n)
        cnt = Counter(r[c[0]] for r in rows)
        q = rnd.choice([f"{_sp(c)} wise count", f"{_sp(c)} wise {ent_en}", f"{_sp(c)} ke hisaab se kitne {ent_hi}", f"count of {ent_en} by {_sp(c)}"])
        named = [a for a in amounts if ent_en.rstrip("s") in book["headers"][a[0]].lower()]
        money = (named + (amounts[:1] if book.get("money_entity") else []) + [None])[0] if q.endswith(f"wise {ent_en}") else None
        if money is not None:                            # 'item wise bills' with a 'Bill Amount' column, 'paid by wise expenses': the money added up
            g = defaultdict(int)
            for r in rows:
                g[r[c[0]]] += r[money[0]]
            add("group_sum", q, "pairs", dict(g))
        else:
            add("group_count", q, "pairs", dict(cnt))
    for a in amounts[:1]:
        tot = sum(r[a[0]] for r in rows)
        add("sum", rnd.choice([f"total {_sp(a)}", f"{_sp(a)} ka total", f"total {_sp(a)} kitna hai", f"sum of {_sp(a)}"]), "number", tot)
        add("avg", rnd.choice([f"average {_sp(a)}", f"{_sp(a)} ka average", f"average {_sp(a)} kitna hai"]), "number", round(tot / len(rows), 2))
        if cats:
            c = rnd.choice(cats)
            v = rnd.choice(c[3]["values"])
            pre = f"{_sp(c)} {v}" if len(v) <= 2 else v
            add("sum_where", rnd.choice([f"total {_sp(a)} of {pre}", f"{pre} ka total {_sp(a)}", f"{pre} ki total {_sp(a)}"]), "number",
                sum(r[a[0]] for r in rows if r[c[0]] == v))
            g = defaultdict(int)
            for r in rows:
                g[r[c[0]]] += r[a[0]]
            add("group_sum", rnd.choice([f"{_sp(c)} wise total {_sp(a)}", f"total {_sp(a)} by {_sp(c)}", f"{_sp(c)} ke hisaab se total {_sp(a)}"]),
                "pairs", dict(g))
        vals = sorted(r[a[0]] for r in rows)
        x = vals[len(vals) // 2]
        x = int(round(x, -2)) if x > 1000 else x
        add("above", rnd.choice([f"how many {ent_en} with {_sp(a)} above {x}", f"{_sp(a)} {x} se zyada kitne", f"how many have {_sp(a)} more than {x}"]),
            "number", sum(1 for r in rows if r[a[0]] > x))
        if person is not None:
            top = max(rows, key=lambda r: r[a[0]])
            add("max_who", rnd.choice([f"who has the highest {_sp(a)}", f"sabse zyada {_sp(a)} kiska hai", f"highest {_sp(a)}"]), "has",
                [top[person[0]]] if sum(1 for r in rows if r[a[0]] == top[a[0]]) == 1 else [], number=top[a[0]])
    for q in qtys[:1]:
        add("sum_qty", rnd.choice([f"total {_sp(q)}", f"total {_sp(q)} kitni hai", f"sum of {_sp(q)}"]), "number", sum(r[q[0]] for r in rows))
    for d in dates[:1]:
        years = Counter(r[d[0]].year for r in rows)
        y = rnd.choice(sorted(years))
        add("year", rnd.choice([f"how many {ent_en} in {y}", f"{y} mein kitne {ent_hi}"]), "number", years[y])
        latest = max(rows, key=lambda r: r[d[0]])
        if person is not None and sum(1 for r in rows if r[d[0]] == latest[d[0]]) == 1:
            add("latest", rnd.choice([f"latest {ent_en[:-1] if ent_en.endswith('s') else ent_en}", "most recent one", f"which is the latest {_sp(d)}"]),
                "has", [latest[person[0]]])
    if person is not None and (has_gender or not by.get(person[0], [None, None, None, {}])[3].get("male_only")):
        f = sum(1 for r in rows if r.get("_gender") == "F")
        add("gender", rnd.choice(["how many women", "how many girls", "kitni ladkiyan hain", "how many female", "mahilaayein kitni hain"]),
            "number" if has_gender else "estimate", f)
    missing = [w for w, keys in (("age", ("age", "dob")), ("phone number", ("phone",)), ("profit", ())) if not any(k in by for k in keys)]
    if missing:
        w = rnd.choice(missing)
        add("missing", rnd.choice([f"average {w}", f"what is the {w}", f"{w} batao"]), "missing", None)
    add("show", rnd.choice([f"show {book['sheet']}", f"give me {book['sheet']} data", f"{book['sheet']} dikhao"]), "number", len(rows))
    return out


# ---------------------------------------------------------------- checking an answer
def _numbers(say: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*(?:\.\d+)?", say or "")]


def check(item: dict, say: str, raised: str | None) -> tuple[bool, str]:
    kind = item["kind"]
    if kind == "missing":
        return (raised == "Missing", f"expected 'not in the file', got {raised or say[:120]!r}")
    if raised:
        return False, f"{raised}: {say[:160]!r}"
    nums = _numbers(say)
    if kind in ("number", "estimate"):
        t = item["truth"]
        if kind == "estimate":
            return ("estimate" in say.lower() or "andaza" in say.lower(), f"expected an estimate, got {say[:160]!r}")
        ok = any(abs(n - t) <= max(0.011, abs(t) * 0.0001) for n in nums)
        return ok, f"expected {t}, got {say[:200]!r}"
    if kind == "pairs":
        miss = [f"{k}: {v}" for k, v in item["truth"].items() if not re.search(rf"\b{re.escape(str(k))}\b[^;|]*?{re.escape(_fmt(v))}\b", say)]
        return not miss, f"missing {miss[:4]} in {say[:200]!r}"
    if kind == "has":
        want = item["truth"]
        ok = all(w in say for w in want) and ("number" not in item or any(abs(n - item["number"]) < 0.01 for n in nums))
        return ok, f"expected {want} {item.get('number', '')}, got {say[:200]!r}"
    return False, "unknown check"


def _fmt(v):
    from app.office.ask import _fmt as f

    return f(v)


def run(seeds, show=False) -> dict:
    from app.office import ask
    from app.office import understand as U
    from app.office.sheetdata import load_tables

    folder = tempfile.mkdtemp()
    stats, fails = defaultdict(lambda: [0, 0]), []
    for seed in seeds:
        book = make_book(seed, folder)
        rnd = random.Random(seed * 7919)
        tables = load_tables(book["path"])
        for item in questions(book, rnd):
            say, raised = "", None
            try:
                plan = ask.understand(item["q"], tables)
                res = ask.run(plan, book["path"], f"{folder}/out_{seed}.xlsx", bool(re.search(r"\b(kitne|kitni|ka|ki|mein|wale|hain|batao|dikhao|kiska)\b", item["q"])), 1)
                say = res.get("say", "")
            except U.Missing as m:
                raised, say = "Missing", m.en
            except ask.NeedMore as nm:
                raised, say = "NeedMore", nm.en
            except Exception as e:  # noqa: BLE001
                raised, say = type(e).__name__, str(e)
            ok, why = check(item, say, raised)
            st = stats[item["family"]]
            st[0] += ok
            st[1] += 1
            if show:
                print(("PASS " if ok else "FAIL ") + f"[{book['field']}/{seed}] {item['q']!r} -> {say[:150]!r}")
            if not ok:
                fails.append({"seed": seed, "field": book["field"], "mess": book["mess"], "family": item["family"], "q": item["q"], "why": why,
                              "headers": [book["headers"][c[0]] for c in book["cols"]]})
    total = sum(v[1] for v in stats.values())
    passed = sum(v[0] for v in stats.values())
    return {"passed": passed, "total": total, "rate": round(passed / total, 3) if total else 0,
            "by_family": {k: f"{v[0]}/{v[1]}" for k, v in sorted(stats.items())}, "fails": fails}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--json", help="write the report here")
    a = ap.parse_args(argv)
    seeds = [a.seed] if a.seed is not None else list(range(a.start, a.start + a.seeds))
    rep = run(seeds, show=a.show)
    print(f"passed {rep['passed']}/{rep['total']} ({rep['rate']:.1%})")
    print(json.dumps(rep["by_family"], indent=1))
    for f in rep["fails"][:400]:
        print(f"- [{f['field']}/{f['seed']} {','.join(f['mess'])}] {f['family']}: {f['q']!r} -> {f['why']}")
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1, default=str)
    return 0 if rep["rate"] >= 0.95 else 1


if __name__ == "__main__":
    sys.exit(main())
