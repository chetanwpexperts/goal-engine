"""Sprint S3 practice bank: a query's result to Excel (app/agents/sqlexcel.py).

A made-up parcel desk (hubs, statuses, weights, fees, codes with leading zeros, 18-digit tracking numbers, notes that look
like formulas) is created on MariaDB, PostgreSQL and SQLite. Each problem exports a query through the connector's own
export code, builds the workbook, has LibreOffice recalculate every formula, and checks:

* the Data sheet holds every value exactly (tests/fuzz/dbexcel.check);
* the recalculated Pivot equals the engine's own GROUP BY, grouped the way that engine groups text;
* the problem's own point: which formula each group needed, what the notes must say, why there is no pivot.
"""

import datetime as dt
import os
import random
import tempfile
from decimal import Decimal

import pytest

from app.products.dbaba import sqlexcel
from tests.fuzz import dbexcel as DX

HUBS = ["Mohali", "mohali", "MOHALI", "Kharar", "Zirakpur", "Panchkula", "Café Point", "Cafe Point", "Depot ", "Depot",
        "A*1", "B?2", "10", "010", ">5", "=x", "~tilde", "TRUE", "", None]
STATUS = ["booked", "in transit", "delivered", "returned"]
TABLE = "s3_parcels"
COLS = ["id", "hub", "status", "weight_kg", "fee", "sent_at", "post_code", "tracking_no", "note", "customer_id", "long_text", "rate"]


def _rows():
    rnd = random.Random(3)
    out = []
    for k in range(1, 121):
        out.append([k, rnd.choice(HUBS), rnd.choice(STATUS), rnd.randint(1, 40), None if rnd.random() < 0.1 else Decimal(rnd.randint(-2000, 90000)) / 100,
                    dt.datetime(2026, rnd.randint(1, 9), rnd.randint(1, 28), rnd.randint(0, 23), rnd.randint(0, 59)),
                    f"{rnd.randint(0, 99999):06d}", 900000000000000000 + rnd.randint(0, 10 ** 9), rnd.choice(["=SUM(A1)", "+91", "-2", "@x", "fine", None]),
                    rnd.randint(1, 30), ("x" * 300) if k % 40 == 0 else "short", Decimal("1234567890.1234567891") if k % 7 == 0 else Decimal("2.5")])
    for r in out:
        if r[2] == "returned":
            r[4] = None                       # a group with no fee at all: SUM is NULL in the database, 0 in Excel
    return out


ROWS = _rows()
DDL = {"mysql": "CREATE TABLE s3_parcels (id INT PRIMARY KEY, hub VARCHAR(40), status VARCHAR(20), weight_kg INT, fee DECIMAL(10,2), "
                "sent_at DATETIME, post_code VARCHAR(6), tracking_no BIGINT, note VARCHAR(20), customer_id INT, long_text VARCHAR(400), rate DECIMAL(30,10))",
       "postgres": "CREATE TABLE s3_parcels (id INT PRIMARY KEY, hub VARCHAR(40), status VARCHAR(20), weight_kg INT, fee NUMERIC(10,2), "
                   "sent_at TIMESTAMP, post_code VARCHAR(6), tracking_no BIGINT, note VARCHAR(20), customer_id INT, long_text VARCHAR(400), rate NUMERIC(30,10))",
       "sqlite": "CREATE TABLE s3_parcels (id INTEGER PRIMARY KEY, hub VARCHAR(40), status VARCHAR(20), weight_kg INTEGER, fee NUMERIC(10,2), "
                 "sent_at TIMESTAMP, post_code VARCHAR(6), tracking_no INTEGER, note VARCHAR(20), customer_id INTEGER, long_text VARCHAR(400), rate TEXT)"}

# (id, sql, by, measure, words, check)  check(info, engine_id) -> None (asserts)
T = f"FROM {TABLE}"


def kinds(*want):
    def f(info, eng):
        assert set(want) <= set(info["pivot"]["formulas"]), info["pivot"]["formulas"]
    return f


def note(word, engines=None):
    def f(info, eng):
        if engines is None or eng in engines:
            assert any(word.lower() in n.lower() for n in info["notes"]), (word, info["notes"])
        else:
            assert not any(word.lower() in n.lower() for n in info["notes"]), (word, info["notes"])
    return f


def nopivot(why):
    def f(info, eng):
        assert info["pivot"] is None and info["no_pivot"] == why, (info["no_pivot"], info["say"])
    return f


def both(*fs):
    def f(info, eng):
        for g in fs:
            g(info, eng)
    return f


def pv(**want):
    def f(info, eng):
        for k, v in want.items():
            assert info["pivot"][k] == v, (k, info["pivot"][k], v)
    return f


def capitals(info, eng):
    labels = [v["label"] for v in info["pivot"]["values"]]
    mohali = [x for x in labels if isinstance(x, str) and x.lower() == "mohali"]
    if eng in ("mariadb", "mysql"):
        assert len(mohali) == 1 and "ifs" in info["pivot"]["formulas"], labels            # one group, like MariaDB's GROUP BY
    else:
        assert len(mohali) == 3 and "exact" in info["pivot"]["formulas"], labels          # three groups, EXACT tells them apart


P = [
    ("auto", f"SELECT status, fee, weight_kg {T}", None, None, "", pv(by="status", measure="fee", mode="value")),
    ("auto_skips_keys", f"SELECT customer_id, status, weight_kg {T}", None, None, "", pv(by="status", measure="weight_kg")),
    ("named_by", f"SELECT * {T}", "status", "weight_kg", "", pv(by="status", measure="weight_kg", named=True)),
    ("count_only", f"SELECT status, hub {T}", "status", None, "", pv(measure=None)),
    ("month", f"SELECT sent_at, fee {T}", "sent_at", "fee", "", both(pv(mode="month"), kinds("month"))),
    ("capitals", f"SELECT hub, fee {T}", "hub", "fee", "", capitals),
    ("wildcards", f"SELECT hub, fee {T} WHERE hub IN ('A*1', 'B?2', 'Kharar')", "hub", "fee", "", kinds("ifs")),
    ("wildcards_safe", f"SELECT hub, fee {T} WHERE hub IN ('A*1', 'B?2', 'Kharar')", "hub", "fee", "",
     lambda i, e: assert_how(i, {"A*1": ("eq", "exact"), "B?2": ("eq", "exact"), "Kharar": ("ifs",)})),
    ("digits", f"SELECT hub, weight_kg {T} WHERE hub IN ('10', '010')", "hub", "weight_kg", "",
     lambda i, e: assert_how(i, {"10": ("eq", "exact"), "010": ("eq", "exact")})),
    ("operators", f"SELECT hub, weight_kg {T} WHERE hub IN ('>5', '=x', '~tilde', 'TRUE')", "hub", "weight_kg", "",
     lambda i, e: assert_how(i, {">5": ("eq", "exact"), "=x": ("eq", "exact"), "~tilde": ("eq", "exact"), "TRUE": ("eq", "exact")})),
    ("empty_group", f"SELECT hub, fee {T} WHERE hub IS NULL OR hub = '' OR hub = 'Kharar'", "hub", "fee", "",
     both(kinds("empty"), note("NULL and empty text"))),
    ("sum_null", f"SELECT status, fee {T}", "status", "fee", "", note("SUM gives NULL")),
    ("no_sum_null_when_values", f"SELECT status, fee {T} WHERE status <> 'returned' AND fee IS NOT NULL", "status", "fee", "",
     note("SUM gives NULL", engines=())),
    ("accents", f"SELECT hub, fee {T} WHERE hub IN ('Café Point', 'Cafe Point', 'Kharar')", "hub", "fee", "", note("Cafe Point", engines=("mariadb",))),
    ("trailing", f"SELECT hub, fee {T} WHERE hub IN ('Depot', 'Depot ', 'Kharar')", "hub", "fee", "", note("'Depot '", engines=("mariadb",))),
    ("negatives", f"SELECT status, fee {T} WHERE fee < 0 OR status = 'booked'", "status", "fee", "", pv(by="status")),
    ("leading_zeros", f"SELECT post_code, status {T}", "status", None, "", lambda i, e: None),
    ("long_numbers", f"SELECT tracking_no, status {T}", "status", None, "", note("more than 15 digits")),
    ("long_decimal", f"SELECT rate, status {T}", "status", None, "", note("more than 15 digits")),
    ("formula_text", f"SELECT note, status {T}", "status", None, "", lambda i, e: None),
    ("long_label", f"SELECT long_text, weight_kg {T}", "long_text", "weight_kg", "",
     lambda i, e: assert_how(i, {"x" * 300: ("eq", "exact"), "short": ("ifs",)})),
    ("no_rows", f"SELECT status, fee {T} WHERE id < 0", None, None, "", nopivot("no_rows")),
    ("no_repeats", f"SELECT id, tracking_no {T}", None, None, "", nopivot("no_repeats")),
    ("by_missing", f"SELECT status, fee {T}", "colour", None, "", lambda i, e: (i.get("by_not_found") == "colour") or pytest.fail(i)),
    ("measure_text", f"SELECT status, hub {T}", "status", "hub", "", lambda i, e: (i.get("measure_not_number") == "hub") or pytest.fail(i)),
    ("join", f"SELECT p.status, p.fee, q.hub AS from_hub {T} p JOIN {TABLE} q ON q.id = p.customer_id", "from_hub", "fee", "", pv(by="from_hub")),
    ("words_en", f"SELECT * {T}", None, None, "export parcels status wise with total weight_kg", pv(by="status", measure="weight_kg")),
    ("words_hinglish", f"SELECT * {T}", None, None, "status ke hisaab se fee ka total excel mein do", pv(by="status", measure="fee")),
    ("words_by", f"SELECT * {T}", None, None, "pivot by hub, sum of weight_kg", pv(by="hub", measure="weight_kg")),
    ("many_groups_named", f"SELECT customer_id, fee {T}", "customer_id", "fee", "",
     lambda i, e: (i["pivot"]["groups"] > 20 and len(i["chart"]["rows"]) == 20 and "top 20" in i["chart"]["title"]) or pytest.fail(i["chart"])),
    ("small_int_group", f"SELECT weight_kg, fee {T} WHERE weight_kg <= 4", "weight_kg", "fee", "", kinds("ifs")),
    ("dates_as_groups", f"SELECT CAST(sent_at AS DATE) AS sent_on, fee {T} WHERE id <= 12", "sent_on", "fee", "", pv(mode="value")),
]


def assert_how(info, want):
    got = {}
    for e in info["pivot"]["values"]:
        got[e["label"]] = e
    hows = {e["label"]: e["how"] for e in info["_items"]}
    for label, ok in want.items():
        assert label in hows, (label, list(hows))
        assert hows[label] in ok, (label, hows[label], ok)


@pytest.fixture(scope="module")
def lab():
    if not DX.soffice():
        pytest.skip("LibreOffice is not installed")
    tgs = DX.targets()
    if len(tgs) < 3:
        pytest.skip("needs MariaDB, PostgreSQL and SQLite")
    work = tempfile.mkdtemp(prefix="s3_")
    built = []
    orig = sqlexcel.groups_of
    for tg in tgs:
        tg.drop([TABLE])
        tg.run(DDL[tg.kind])
        tg.run(f"INSERT INTO {TABLE} VALUES ({', '.join(['%s'] * len(COLS))})",
               many=[tuple(str(v) if (tg.kind == "sqlite" and isinstance(v, Decimal) and k == 11) else v for k, v in enumerate(r)) for r in ROWS])
        if tg.kind != "sqlite":
            tg.eng.commit()
        for pid, sql, by, measure, words, _ in P:
            gz = os.path.join(work, f"{tg.kind}_{pid}.csv.gz")
            x = tg.export(sql, gz)
            cols, rows, notes = sqlexcel.read_csv(gz)
            if words:
                w = sqlexcel.words_pick(words, cols)
                by, measure = w.get("by"), w.get("measure")
            seen = {}

            def spy(*a, **k):
                G = orig(*a, **k)
                seen["items"] = G["items"]
                return G
            sqlexcel.groups_of = spy
            try:
                xl = os.path.join(work, f"{tg.kind}_{pid}.xlsx")
                info = sqlexcel.build(cols, rows, xl, engine=tg.engine_id, sql=sql, by=by, measure=measure, notes=notes, typed=False,
                                      limited=x.get("limited"))
            finally:
                sqlexcel.groups_of = orig
            info["_items"] = [{"label": (e["label"] if e["how"] != "empty" else sqlexcel.EMPTY_LABEL), "how": e["how"]} for e in seen.get("items", [])]
            raw = tg.run(sql)
            p = info["pivot"]
            eng = DX.truth(tg, sql, p["by"], p["measure"], p["mode"] == "month") if p else None
            built.append((pid, tg, xl, info, raw, cols, eng, {"sql": sql, "by": by, "measure": measure}))
        tg.drop([TABLE])
    sheets = DX.recalc([b[2] for b in built], work)
    yield {(b[0], b[1].engine_id): (b, sheets.get(b[2]) or {}) for b in built}


ENGINES = ["mariadb", "postgresql", "sqlite"]


@pytest.mark.parametrize("pid", [p[0] for p in P])
@pytest.mark.parametrize("engine", ENGINES)
def test_problem(lab, pid, engine):
    (pid_, tg, xl, info, raw, cols, eng, case), sheets = lab[(pid, engine)]
    problems = DX.check(sheets, info, tg, case, raw, cols, eng)
    assert problems == [], problems
    next(p for p in P if p[0] == pid)[5](info, engine)


def test_bank_size():
    assert len(P) >= 30


def test_formula_text_never_runs(lab):
    """'=SUM(A1)' from the database is a text cell in the workbook, not a formula."""
    from openpyxl import load_workbook

    (b, sheets) = lab[("formula_text", "postgresql")]
    ws = load_workbook(b[2])["Data"]
    cells = [c for row in ws.iter_rows(min_row=2) for c in row if c.value == "=SUM(A1)"]
    assert cells and all(c.data_type == "s" for c in cells)
    assert any(r and "=SUM(A1)" in r for r in sheets["Data"])           # LibreOffice shows the text, it didn't run it


def test_codes_and_long_numbers_keep_every_digit(lab):
    (b, sheets) = lab[("long_numbers", "mariadb")]
    raw = b[4]
    assert [str(r[0]) for r in raw[:50]] == [r[0] for r in sheets["Data"][1:51]]
    (b, sheets) = lab[("leading_zeros", "postgresql")]
    assert [r[0] for r in b[4][:50]] == [r[0] for r in sheets["Data"][1:51]] and any(r[0].startswith("0") for r in sheets["Data"][1:])


def test_what_a_spreadsheet_does_to_a_plain_csv():
    """The reason for the typed workbook, lab-run: LibreOffice opening the same values as a CSV changes all three."""
    import shutil
    import subprocess

    from openpyxl import load_workbook

    exe = DX.soffice()
    if not exe:
        pytest.skip("LibreOffice is not installed")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.csv")
    open(p, "w").write("code,big_no,note\n00123,12345678901234567,=1+1\n")
    prof = tempfile.mkdtemp()
    subprocess.run([exe, f"-env:UserInstallation=file://{prof}", "--headless", "--infilter=CSV:44,34,76,1", "--convert-to", "xlsx",
                    "--outdir", d, p], capture_output=True, timeout=120)
    ws = load_workbook(os.path.join(d, "t.xlsx")).active
    code, big, note_ = (c for c in ws[2])
    assert code.value == 123 and big.value != 12345678901234567 and note_.data_type == "f"
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(prof, ignore_errors=True)
    x = os.path.join(tempfile.mkdtemp(), "x.xlsx")
    sqlexcel.build(["code", "big_no", "note"], [["00123", 12345678901234567, "=1+1"]], x, engine="mariadb")
    ws = load_workbook(x)["Data"]
    assert [c.value for c in ws[2]] == ["00123", "12345678901234567", "=1+1"] and ws["C2"].data_type == "s"


def test_typed_rows_from_a_driver():
    """Rows straight from a driver (S4's own screen): Decimal, BIGINT, time zones, bytes, control characters."""
    from openpyxl import load_workbook

    x = os.path.join(tempfile.mkdtemp(), "t.xlsx")
    tz = dt.timezone(dt.timedelta(hours=5, minutes=30))
    rows = [[Decimal("12.50"), 2 ** 62, dt.datetime(2026, 1, 2, 3, 4, tzinfo=tz), b"\x00\x01", "bad\x07char", "a"],
            [Decimal("0.10"), 7, dt.datetime(2026, 1, 3, 3, 4, tzinfo=tz), None, "ok", "a"]]
    info = sqlexcel.build(["amount", "big", "at", "blob", "txt", "g"], rows, x, engine="postgresql")
    ws = load_workbook(x)["Data"]
    assert ws["A2"].value == 12.5 and ws["B2"].value == str(2 ** 62) and isinstance(ws["C2"].value, str) and ws["D2"].value == "<2 bytes>"
    assert ws["E2"].value == "badchar"
    joined = " ".join(info["notes"])
    assert "15 digits" in joined and "time zone" in joined and "control characters" in joined


def test_engine_of_connector():
    class C:
        def __init__(self, t, v):
            self.db_type, self.version = t, v
    assert sqlexcel.engine_of(C("mysql", "10.11.14-MariaDB")) == "mariadb"
    assert sqlexcel.engine_of(C("mysql", "8.0.36")) == "mysql"
    assert sqlexcel.engine_of(C("postgres", "16.1")) == "postgresql"
    assert sqlexcel.engine_of(C("sqlite", "3.45")) == "sqlite"


def test_the_say_in_hinglish():
    x = os.path.join(tempfile.mkdtemp(), "t.xlsx")
    info = sqlexcel.build(["status", "amt"], [["a", 1], ["b", 2], ["a", 3]], x, engine="mariadb", hi=True)
    assert info["say"].startswith("Excel file taiyar") and "Pivot sheet" in info["say"]


def test_grouping_facts_are_lab_marked():
    from app.products.dbaba.dbknow import engines

    for e, case in (("mariadb", "insensitive"), ("postgresql", "sensitive"), ("sqlite", "sensitive"), ("mysql", "insensitive")):
        g = engines.dialect(e)["grouping"]
        assert g["case"] == case and g["proven"], e
