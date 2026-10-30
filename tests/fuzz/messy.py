"""Generator for messy everyday workbooks: sales, staff, stock, fees, expenses, rent... built clean (the truth), then
spoiled the ways real files get spoiled, mixed at random. After the clean-up every spoiled cell must be back to the truth
or flagged for the person, every clean cell must be untouched, and nothing clean may be flagged.
Run: python -m tests.fuzz.messy [count] [seed]
"""

import random
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

OPS = ["trim_spaces", "text_to_number", "fix_case", "remove_duplicates", "data_quality"]
FIRST = ["Rohit", "Asha", "Imran", "Neha", "Karan", "Priya", "Vikram", "Sana", "Arjun", "Meera", "Rahul", "Divya", "Aman",
         "Pooja", "Farhan", "Kavya", "Manish", "Ritu", "Sunil", "Anita", "John", "Maria", "David", "Sarah", "Peter", "Grace"]
LAST = ["Sharma", "Verma", "Khan", "Gupta", "Singh", "Iyer", "Nair", "Patel", "Reddy", "Das", "Mehta", "Joshi", "Smith", "Brown"]
DOMAINS = ["gmail.com", "yahoo.in", "outlook.com", "company.co.in", "mail.com"]

# Each column: (heading, kind, details). Totals are written the way people write them, with {r} for the row.
FIELDS = {
    "sales": [("Invoice No", "id", "INV-"), ("Invoice Date", "date", None), ("Customer Name", "name", None), ("Email", "email", None),
              ("Region", "cat", ["North", "South", "East", "West", "Central"]),
              ("Category", "cat", ["Electronics", "Furniture", "Stationery", "Accessories", "Appliances"]),
              ("Quantity", "int", (1, 40)), ("Unit Price", "money", (50, 9000)), ("Discount", "rate", [0, 0.05, 0.1, 0.15]),
              ("Total Amount", "total", "={q}{r}*{p}{r}*(1-{d}{r})"), ("Payment Status", "cat", ["Paid", "Pending", "Overdue", "Partial"])],
    "staff": [("Employee ID", "id", "EMP"), ("Employee Name", "name", None), ("Department", "cat", ["Accounts", "Marketing", "Operations", "Support"]),
              ("Joining Date", "date", None), ("Mobile", "phone", None), ("Email", "email", None), ("Basic Salary", "money", (12000, 90000)),
              ("Bonus", "rate", [0.05, 0.08, 0.1, 0.12]), ("Gross Salary", "total", "={q}{r}*(1+{d}{r})")],
    "stock": [("SKU", "id", "SKU-"), ("Item", "name", None), ("Category", "cat", ["Grocery", "Cleaning", "Beverages", "Snacks"]),
              ("Opening Stock", "int", (0, 300)), ("Received", "int", (0, 200)), ("Issued", "int", (0, 150)),
              ("Closing Stock", "total", "={a}{r}+{b}{r}-{c}{r}"), ("Supplier", "cat", ["Sharma Traders", "Metro Wholesale", "Fresh Foods"]),
              ("Last Updated", "date", None)],
    "fees": [("Roll No", "id", "R"), ("Student Name", "name", None), ("Class", "cat", ["Nursery", "Primary", "Middle", "Senior"]),
             ("Fee", "money", (2000, 15000)), ("Concession", "rate", [0, 0.1, 0.25]), ("Net Fee", "total", "={q}{r}*(1-{d}{r})"),
             ("Paid On", "date", None), ("Payment Mode", "cat", ["Cash", "Cheque", "Online"]), ("Email", "email", None)],
    "expenses": [("Voucher No", "id", "V"), ("Date", "date", None), ("Expense Head", "cat", ["Travel", "Office Rent", "Electricity", "Internet", "Salaries"]),
                 ("Amount", "money", (300, 60000)), ("GST", "rate", [0, 0.05, 0.12, 0.18]), ("Total", "total", "={q}{r}*(1+{d}{r})"),
                 ("Paid By", "cat", ["Company Card", "Petty Cash", "Bank Transfer"])],
    "rent": [("Tenant ID", "id", "T-"), ("Tenant Name", "name", None), ("Property", "cat", ["Green Park", "Lake View", "Hill Top"]),
             ("Due Date", "date", None), ("Rent", "money", (5000, 40000)), ("Late Fee", "money", (0, 2000)),
             ("Total Due", "total", "={q}{r}+{p}{r}"), ("Status", "cat", ["Paid", "Pending", "Partial"]), ("Contact", "phone", None)],
}

# How each kind can be spoiled. "fix": must come back to the truth. A flag name: must be listed for the person.
SPOIL = {
    "name": ["spaces"],
    "cat": ["case", "spaces", "plural_typo"],
    "date": ["date_format", "bad_date", "missing"],
    "email": ["bad_email", "missing"],
    "int": ["text_number", "negative", "word_number"],
    "money": ["text_number", "currency", "missing", "negative"],
    "rate": ["rate_percent_text", "rate_whole"],
    "total": ["wrong_ref", "typed_total", "missing_total"],
    "id": ["repeated_id"],
    "phone": [],
}
EXPECT = {"spaces": "fix", "case": "fix", "plural_typo": "fix", "date_format": "fix", "text_number": "fix", "currency": "fix",
          "rate_percent_text": "fix", "rate_whole": "fix", "wrong_ref": "fix", "typed_total": "fix", "missing_total": "fix",
          "bad_date": "flag_bad_date", "missing": "flag_missing", "bad_email": "flag_bad_email", "negative": "flag_negative",
          "word_number": "flag_not_number", "repeated_id": "flag_repeated_id"}
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def _typo(word: str, rng) -> str:
    if word.endswith("s") and rng.random() < 0.5:
        return word[:-1]
    i = rng.randrange(1, len(word) - 2)
    return word[:i] + word[i + 1] + word[i] + word[i + 2:]          # two letters swapped


def _date_text(d: datetime, rng) -> str:
    return rng.choice([d.strftime("%Y-%m-%d"), d.strftime("%d %b %Y"), d.strftime("%d.%m.%Y"), d.strftime("%d-%b-%y"),
                       f"{d.day}/{d.month}/{d.year}"])


def make(seed: int):
    """Returns (workbook, truth, spoiled) where truth maps cell -> value and spoiled maps cell -> (how, expectation)."""
    rng = random.Random(seed)
    field = rng.choice(sorted(FIELDS))
    cols = FIELDS[field]
    n = rng.randint(14, 45)
    letters = {h: get_column_letter(i + 1) for i, (h, _, _) in enumerate(cols)}
    nums = [h for h, k, _ in cols if k in ("int", "money")]
    rates = [h for h, k, _ in cols if k == "rate"]
    ref = {"q": letters[nums[0]], "p": letters[nums[1 % len(nums)]], "d": letters[(rates or nums)[0]],
           "a": letters[nums[0]], "b": letters[nums[1 % len(nums)]], "c": letters[nums[2 % len(nums)]]}
    dates_as_text = rng.random() < 0.3                               # some files keep every date as dd/mm/yyyy text
    start = datetime(2025, rng.randint(1, 12), rng.randint(1, 28))

    wb = Workbook()
    ws = wb.active
    ws.title = rng.choice(["Sheet1", "Data", field.title(), f"{field.title()} 2026"])
    truth = {}
    for c, (h, _, _) in enumerate(cols, 1):
        ws.cell(1, c, h)
        truth[f"{get_column_letter(c)}1"] = h
    used_names = set()
    for r in range(2, n + 2):
        for c, (h, kind, det) in enumerate(cols, 1):
            cell = f"{get_column_letter(c)}{r}"
            if kind == "id":
                v = f"{det}{1000 + r}"
            elif kind == "name":
                while True:
                    v = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                    if v not in used_names or len(used_names) > 300:
                        break
                used_names.add(v)
            elif kind == "date":
                v = start + timedelta(days=rng.randint(0, 300))
            elif kind == "email":
                v = f"{rng.choice(FIRST).lower()}{rng.randint(1, 999)}@{rng.choice(DOMAINS)}"
            elif kind == "phone":
                v = f"9{rng.randint(100000000, 999999999)}"
            elif kind == "cat":
                v = rng.choice(det[:2]) if rng.random() < 0.5 else rng.choice(det)   # some values much commoner, like real data
            elif kind == "int":
                v = rng.randint(*det)
            elif kind == "money":
                v = rng.randrange(det[0], det[1], 50) if det[1] - det[0] > 100 else rng.randint(*det)
            elif kind == "rate":
                v = rng.choice(det)
            else:
                v = det.format(r=r, **ref)
            truth[cell] = v
            if kind == "date" and dates_as_text:
                ws[cell] = v.strftime("%d/%m/%Y")
            else:
                ws[cell] = v
                if kind == "date":
                    ws[cell].number_format = "DD-MM-YYYY"
                if kind == "rate":
                    ws[cell].number_format = "0%"

    spoiled = {}
    budget = max(1, n // 7)                                           # a column stays mostly right, like real files
    for c, (h, kind, det) in enumerate(cols, 1):
        L = get_column_letter(c)
        kinds = SPOIL[kind]
        if not kinds or rng.random() < 0.25:
            continue
        rows = rng.sample(range(2, n + 2), rng.randint(1, budget))
        for r in rows:
            how = rng.choice(kinds)
            cell, t = f"{L}{r}", truth[f"{L}{r}"]
            if how == "spaces":
                ws[cell] = rng.choice(["  ", " ", ""]) + t + rng.choice(["  ", " "])
            elif how == "case":
                ws[cell] = rng.choice([t.upper(), t.lower()]) if len(t) >= 5 else t.lower()   # 'WEST' could be a code
                if ws[cell].value == t:
                    continue
            elif how == "plural_typo":
                clean_twins = sum(1 for rr in range(2, n + 2) if rr != r and truth[f"{L}{rr}"] == t and f"{L}{rr}" not in spoiled)
                if len(t) < 6 or clean_twins < 2:
                    continue                                  # a misspelling is only knowable next to the right spelling
                ws[cell] = _typo(t, rng)
            elif how == "date_format":
                if dates_as_text:
                    continue                                          # day-first text is this file's own way: fine
                ws[cell] = _date_text(t, rng)
            elif how == "bad_date":
                ws[cell] = rng.choice(["31/02/2026", "30/02/2025", "32/01/2026", "00/05/2026"])
            elif how == "missing":
                ws[cell] = None
            elif how == "bad_email":
                ws[cell] = rng.choice([t.replace("@", ""), t.replace("@", "@@"), t.split("@")[0] + "@", t.replace(".", ",")])
            elif how == "text_number":
                ws[cell] = f"{t:,}" if t >= 1000 else str(t)
            elif how == "currency":
                ws[cell] = rng.choice([f"₹{t:,}", f"Rs. {t}", f"{t}/-", f"INR {t:,}"])
            elif how == "negative":
                if t == 0:
                    continue
                ws[cell] = -t
            elif how == "word_number":
                if t not in WORDS:
                    continue
                ws[cell] = WORDS[t]
            elif how == "rate_percent_text":
                ws[cell] = f"{round(t * 100)}%"
            elif how == "rate_whole":
                if t == 0:
                    continue
                ws[cell] = round(t * 100)
            elif how == "wrong_ref":
                other = r + 1 if r < n + 1 else r - 1
                ws[cell] = t.replace(f"{ref['q']}{r}", f"{ref['q']}{other}", 1)
            elif how == "typed_total":
                ws[cell] = rng.randint(100, 99999)
            elif how == "missing_total":
                ws[cell] = None
            elif how == "repeated_id":
                if r == 2 or f"{L}{r - 1}" in spoiled or f"{L}{r + 1}" in spoiled:
                    continue                                  # copy a clean neighbour, and don't let two copies chain
                ws[cell] = truth[f"{L}{r - 1}"]
            spoiled[cell] = (how, EXPECT[how])
    if rng.random() < 0.4:                                            # a heading spelling mistake
        c = rng.choice([i for i, (h, _, _) in enumerate(cols, 1) if len(h.split()[0]) >= 6])
        h = cols[c - 1][0]
        w = h.split()[0]
        i = rng.randrange(2, len(w) - 1)
        ws.cell(1, c, h.replace(w, w[:i] + w[i + 1:], 1))
        spoiled[f"{get_column_letter(c)}1"] = ("heading", "fix")
    return wb, truth, spoiled, field


def _same(a, b) -> bool:
    if isinstance(a, datetime) or isinstance(b, datetime):
        return isinstance(a, datetime) and isinstance(b, datetime) and a.date() == b.date()
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) < 1e-9
    return a == b


def check(seed: int, workdir: Path) -> list[str]:
    from app import office

    wb, truth, spoiled, field = make(seed)
    src, out = workdir / f"m{seed}.xlsx", workdir / f"m{seed}.out.xlsx"
    wb.save(src)
    office.fix(str(src), str(out), OPS)
    res = load_workbook(out)
    ws = res.worksheets[0]
    label_op = {v: k for k, v in office.LABELS.items()}
    flagged = {}
    if office.CHANGES_SHEET in res.sheetnames:
        log = res[office.CHANGES_SHEET]
        for label, _sheet, cell, *_ in log.iter_rows(min_row=2, values_only=True):
            op = label_op.get(label, label)
            if str(op).startswith("flag_"):
                flagged.setdefault(cell, set()).add(op)
    bad = []
    for cell, t in truth.items():
        got = ws[cell].value
        how, want = spoiled.get(cell, (None, None))
        if how in ("plural_typo", "case", "spaces") and "flag_maybe_same" in flagged.get(cell, set()):
            continue                                   # as rare as the right spelling: asking is the honest answer
        if want and want.startswith("flag_"):
            if want not in flagged.get(cell, set()):
                bad.append(f"{field} seed {seed}: {cell} spoiled by {how}, expected {want}, flags {flagged.get(cell)} value {got!r}")
            continue
        if not _same(got, t):
            what = f"spoiled by {how}, not put back" if how else "was clean but changed"
            bad.append(f"{field} seed {seed}: {cell} {what}: {got!r} (truth {t!r})")
    typo_cols = {c.rstrip("0123456789") for c, (how, _) in spoiled.items() if how == "plural_typo"}
    for cell, ops in flagged.items():
        if ops == {"flag_maybe_same"} and cell.rstrip("0123456789") in typo_cols:
            continue                                   # the other half of 'these two look like one name'
        if cell not in spoiled:
            bad.append(f"{field} seed {seed}: clean cell {cell} flagged {ops} (value {ws[cell].value!r})")
    return bad


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    fails = []
    with tempfile.TemporaryDirectory() as d:
        for s in range(seed, seed + count):
            fails += check(s, Path(d))
    for f in fails[:60]:
        print(f)
    print(f"{count} files, {len(fails)} problems")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
