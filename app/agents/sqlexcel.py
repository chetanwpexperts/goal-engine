"""S3: a query's result as an Excel workbook that keeps working after it is opened.

Three sheets, built from the rows the database really returned:

* Data     every row, each column in its real type (numbers stay numbers, dates stay dates, codes with leading zeros stay
           text), the header frozen and filterable.
* Pivot    one row per group with LIVE formulas over the Data sheet (COUNTIFS / SUMIFS / AVERAGEIFS), so a corrected
           cell in Data changes the pivot; a total row; and a chart drawn from exactly those cells.
* About    the query, the database, the row count and every place where Excel and the database could disagree.

The reasoning, stage by stage (each stage checked against what Excel and the database really do, lab-run with LibreOffice
recalculating the formulas and the engines' own GROUP BY: tests/fuzz/dbexcel.py):

1. A cell's type. Excel keeps 15 significant digits, so a longer number (a 16+ digit id, a card-like code, a DECIMAL(30,10))
   is written as text, never rounded. A value with a leading zero ('00123') is a code, not a number. Text that starts with
   '=' is written as text, never as a formula (a query result can't run anything in the person's Excel). Excel can't hold a
   time zone or control characters, and a cell holds at most 32,767 characters: each is said in About when it happens.
2. Which column to group by and which to add up. The person's words name them when they can; otherwise a text column whose
   values repeat (2-50 groups), or a date column by month, and the first number column that isn't a key (an *_id adds up to
   nonsense).
3. Which formula is honest for each group. SUMIFS reads its criterion as a pattern: '*' and '?' are wildcards, '<', '>' and
   '=' at the start are comparisons, digits can be read as a number or a date, and the match ignores capitals. So:
   * a plain word (letters, no digits, no * ? ~, at most 255 characters) uses SUMIFS / COUNTIFS / AVERAGEIFS;
   * anything else uses SUMPRODUCT with an exact comparison: --(range=$A2) ignores capitals like SUMIFS, EXACT(range,$A2)
     doesn't;
   * the database decides capitals (app/dbknow/dialects/<engine>.json 'grouping'): MySQL / MariaDB's default collations
     put 'Red' and 'red' in one group, PostgreSQL and SQLite keep them apart; a plain word that has a capitals-twin in a
     case-sensitive database uses EXACT.
   * NULL and empty text are both an empty cell in Excel, so they are one group, "(empty)", counted with "=".
4. What Excel can't copy from the database: trailing spaces and accents that a MySQL / MariaDB collation ignores ('a' and
   'a ', 'e' and 'é' are one group there), and SUM over a group with no values (the database says NULL, SUMIFS says 0).
   These are found in the data and written in About, never hidden.
"""

import csv
import datetime as dt
import decimal
import gzip
import io
import json
import re
import unicodedata
from collections import OrderedDict

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

MAX_ROWS = 1_048_575            # Excel's sheet has 1,048,576 rows; one is the header
MAX_CELL = 32_767               # characters in one cell
MAX_DIGITS = 15                 # Excel keeps 15 significant digits
AUTO_GROUPS = 50                # a pivot chosen on its own: 2..50 groups
NAMED_GROUPS = 2000             # a pivot the person asked for by name
CHART_BARS = 20
ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
KEYLIKE = re.compile(r"(^|_)(id|ids|uuid|guid|code|no|num|number|phone|mobile|email|mail|sku|pin|zip|postcode|pincode|"
                     r"password|token|hash)$|[a-z]Id$|^id[A-Z_]", re.I)
INT_RX = re.compile(r"^-?(0|[1-9]\d*)$")
DEC_RX = re.compile(r"^-?(0|[1-9]\d*)\.(\d+)$")
DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RX = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d{1,6})?$")
TZ_RX = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d{1,6})?([+-]\d{2}(:?\d{2})?|Z)$")
BOOL_TEXT = {"true": True, "false": False}
PLAIN = re.compile(r"^[^\W\d_][^\d*?~]*$")
EMPTY_LABEL = "(empty)"
HEAD_FONT = Font(bold=True)
HEAD_FILL = PatternFill("solid", fgColor="EDEBFB")


class ExcelError(ValueError):
    pass


# ---------------------------------------------------------------- what the database says about grouping
def grouping(engine: str | None) -> dict:
    """How the engine's GROUP BY compares text (lab-proven, dialect file). Unknown engine: compared exactly."""
    from app.dbknow import engines

    try:
        return dict(engines.dialect(engine).get("grouping") or {}) if engine else {}
    except (FileNotFoundError, OSError):
        return {}


# ---------------------------------------------------------------- stage 1: every value in its real type
def _digits(s: str) -> int:
    return len(s.lstrip("-").replace(".", "").lstrip("0")) or 1


def _typed_value(v, notes: dict):
    """A value a database driver returned (Python types) -> what an Excel cell can hold without losing anything."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        if len(str(abs(v))) > MAX_DIGITS:
            notes["long_numbers"] = notes.get("long_numbers", 0) + 1
            return str(v)
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, decimal.Decimal):
        if not v.is_finite():
            return str(v)
        s = format(v, "f")
        if _digits(s) > MAX_DIGITS:
            notes["long_numbers"] = notes.get("long_numbers", 0) + 1
            return s
        return int(v) if v == v.to_integral_value() and "." not in s else float(v)
    if isinstance(v, dt.datetime):
        if v.tzinfo is not None:
            notes["time_zones"] = notes.get("time_zones", 0) + 1
            return v.isoformat(sep=" ")
        return v
    if isinstance(v, (dt.date, dt.time, dt.timedelta)):
        return v
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    return _text(str(v), notes)


def _text(s: str, notes: dict) -> str:
    if ILLEGAL.search(s):
        notes["control_chars"] = notes.get("control_chars", 0) + 1
        s = ILLEGAL.sub("", s)
    if len(s) > MAX_CELL:
        notes["cut_text"] = notes.get("cut_text", 0) + 1
        s = s[:MAX_CELL]
    return s


def _csv_column_kind(values: list[str]) -> str:
    """What a CSV column really holds, from every non-empty value (a CSV has only text)."""
    vals = [v for v in values if v != ""]
    if not vals:
        return "text"
    if all(v.lower() in BOOL_TEXT for v in vals):
        return "bool"
    if all(INT_RX.match(v) for v in vals):
        return "int" if all(_digits(v) <= MAX_DIGITS for v in vals) else "longnum"
    if all(INT_RX.match(v) or DEC_RX.match(v) for v in vals):
        return "dec" if all(_digits(v) <= MAX_DIGITS for v in vals) else "longnum"
    if all(DATE_RX.match(v) and _date(v) for v in vals):
        return "date"
    if all((DATETIME_RX.match(v) or DATE_RX.match(v)) and _datetime(v) for v in vals):
        return "datetime"
    if any(TZ_RX.match(v) for v in vals) and all(TZ_RX.match(v) or DATETIME_RX.match(v) for v in vals):
        return "tz"
    return "text"


def _date(s):
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def _datetime(s):
    try:
        return dt.datetime.fromisoformat(s.replace("T", " "))
    except ValueError:
        return None


def _from_csv_value(s: str, kind: str, notes: dict):
    if s == "":
        return None
    if kind == "bool":
        return BOOL_TEXT[s.lower()]
    if kind == "int":
        return int(s)
    if kind == "dec":
        return float(s)
    if kind == "date":
        return _date(s)
    if kind == "datetime":
        return _datetime(s)
    if kind == "longnum" and _digits(s) > MAX_DIGITS:
        notes["long_numbers"] = notes.get("long_numbers", 0) + 1
    if kind == "tz":
        notes["time_zones"] = notes.get("time_zones", 0) + 1
    return _text(s, notes)


def read_csv(path_or_text, limit: int = MAX_ROWS) -> tuple[list[str], list[list], dict]:
    """An export (the connector's CSV, gzip or plain) -> (columns, typed rows, notes)."""
    if isinstance(path_or_text, str) and "\n" not in path_or_text and (path_or_text.endswith(".gz") or path_or_text.endswith(".csv")):
        opener = gzip.open if path_or_text.endswith(".gz") else open
        with opener(path_or_text, "rt", encoding="utf-8-sig", newline="") as fh:
            raw = list(csv.reader(fh))
    else:
        raw = list(csv.reader(io.StringIO(str(path_or_text).lstrip("﻿"))))
    if not raw:
        raise ExcelError("The export is empty")
    cols, body = raw[0], raw[1:]
    notes: dict = {"from_csv": True}
    if len(body) > limit:
        body, notes["cut_rows"] = body[:limit], len(body) - limit
    kinds = [_csv_column_kind([r[i] if i < len(r) else "" for r in body]) for i in range(len(cols))]
    rows = [[_from_csv_value(r[i] if i < len(r) else "", kinds[i], notes) for i in range(len(cols))] for r in body]
    return cols, rows, notes


# ---------------------------------------------------------------- stage 2: what to group by and what to add up
def _kind(values) -> str:
    vals = [v for v in values if v is not None and v != ""]
    if not vals:
        return "empty"
    if all(isinstance(v, bool) for v in vals):
        return "bool"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "int" if all(isinstance(v, int) for v in vals) else "num"
    if all(isinstance(v, (dt.date, dt.datetime)) and not isinstance(v, dt.time) for v in vals):
        return "date"
    if all(isinstance(v, str) for v in vals):
        return "text"
    return "mixed"


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find_column(columns: list[str], word: str | None) -> int | None:
    """The column the person named ('by status', 'city wise', 'total_amount'), matched on its name."""
    if not word:
        return None
    w = _norm_name(word)
    if not w:
        return None
    exact = [i for i, c in enumerate(columns) if _norm_name(c) == w]
    if exact:
        return exact[0]
    near = [i for i, c in enumerate(columns) if w in _norm_name(c) or (_norm_name(c) and _norm_name(c) in w and len(_norm_name(c)) > 2)]
    return near[0] if len(near) == 1 else None


def _months(vals) -> list:
    return sorted({(v.year, v.month) for v in vals if isinstance(v, (dt.date, dt.datetime))})


def choose(columns, rows, by=None, measure=None) -> dict:
    """{'by': i, 'mode': 'value'|'month', 'measure': i|None, 'why': ...} or {'by': None, 'why': ...}"""
    n = len(rows)
    col = lambda i: [r[i] for r in rows]  # noqa: E731
    kinds = [_kind(col(i)) for i in range(len(columns))]
    out = {"kinds": kinds}
    m = find_column(columns, measure) if measure else None
    if m is not None and kinds[m] not in ("int", "num"):
        out["measure_not_number"] = columns[m]
        m = None
    if m is None and not measure:
        nums = [i for i, k in enumerate(kinds) if k in ("int", "num") and not KEYLIKE.search(columns[i])]
        m = nums[0] if nums else None
    out["measure"] = m
    b = find_column(columns, by) if by else None
    if b is not None:
        vals = col(b)
        if kinds[b] == "date" and len({v for v in vals if v is not None}) > AUTO_GROUPS:
            return {**out, "by": b, "mode": "month", "named": True}
        if kinds[b] in ("num",):
            return {**out, "by": None, "why": "decimal"}
        groups = len({_label_key(v, "sensitive") for v in vals})
        if groups > NAMED_GROUPS:
            return {**out, "by": None, "why": "too_many", "groups": groups, "column": columns[b]}
        return {**out, "by": b, "mode": "value", "named": True}
    if by:
        out["by_not_found"] = by
    best = None
    for i, k in enumerate(kinds):
        if i == m or KEYLIKE.search(columns[i]) or k not in ("text", "bool", "int", "date"):
            continue
        vals = col(i)
        distinct = len({_label_key(v, "sensitive") for v in vals})
        if k == "date":
            months = len({x for x in _months(vals)})
            if distinct > AUTO_GROUPS and 2 <= months <= 60:
                score = (2, months)
            elif 2 <= distinct <= AUTO_GROUPS and distinct < n:
                score = (2, distinct)
            else:
                continue
            mode = "month" if distinct > AUTO_GROUPS else "value"
        else:
            if not (2 <= distinct <= AUTO_GROUPS) or distinct > max(2, n * 0.6):
                continue
            if k == "int" and distinct > 20:
                continue
            score = ({"text": 0, "bool": 3, "int": 4}[k], distinct)
            mode = "value"
        if best is None or score < best[0]:
            best = (score, i, mode)
    if best is None:
        return {**out, "by": None, "why": "no_repeats"}
    return {**out, "by": best[1], "mode": best[2], "named": False}


# ---------------------------------------------------------------- stage 3: the groups, and an honest formula for each
def _label_key(v, case: str):
    if v is None or v == "":
        return ("", "")
    if isinstance(v, str):
        return ("s", v.lower() if case == "insensitive" else v)
    if isinstance(v, bool):
        return ("b", v)
    if isinstance(v, (dt.datetime, dt.date)):
        return ("d", v)
    return ("n", v)


def _fold(s: str) -> str:
    """How a MySQL / MariaDB *_ci collation may see a text: no accents, no capitals, no trailing spaces."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower().rstrip(" ")


def _plain(label) -> bool:
    return isinstance(label, str) and len(label) <= 255 and label.upper() not in ("TRUE", "FALSE") and bool(PLAIN.match(label))


def groups_of(columns, rows, pick: dict, engine: str | None) -> dict:
    """The groups as Excel's formulas will see them, each with its formula kind and its own count / sum / average."""
    b, m, mode = pick["by"], pick.get("measure"), pick["mode"]
    g = grouping(engine)
    case = g.get("case") or "sensitive"
    seen = OrderedDict()
    for r in rows:
        v = r[b]
        if mode == "month":
            key = ("m", (v.year, v.month)) if isinstance(v, (dt.date, dt.datetime)) else ("", "")
            label = dt.date(v.year, v.month, 1) if key[0] == "m" else None
        else:
            key = _label_key(v, case)
            label = v
        e = seen.get(key)
        if e is None:
            e = seen[key] = {"label": label, "key": key, "count": 0, "sum": 0, "values": 0, "forms": set()}
        e["count"] += 1
        if isinstance(v, str):
            e["forms"].add(v)
        x = r[m] if m is not None else None
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            e["sum"] += x
            e["values"] += 1
    lower_twins = {}
    for e in seen.values():
        if e["key"][0] == "s":
            lower_twins.setdefault(e["label"].lower(), []).append(e)
    for e in seen.values():
        k = e["key"][0]
        if k == "":
            e["how"] = "empty"
        elif k == "m":
            e["how"] = "month"
        elif k in ("n", "d"):
            e["how"] = "ifs"
        elif k == "b":
            e["how"] = "eq"
        elif case == "insensitive":
            e["how"] = "ifs" if _plain(e["label"]) else "eq"
        else:
            twins = len(lower_twins.get(e["label"].lower(), [])) > 1
            e["how"] = "exact" if twins or not _plain(e["label"]) else "ifs"
        e["avg"] = (e["sum"] / e["values"]) if e["values"] else None
    items = list(seen.values())
    if mode == "month" or pick.get("kind") == "date" or all(e["key"][0] in ("d", "") for e in items):
        items.sort(key=lambda e: (e["key"][0] == "", e["key"][1] if e["key"][0] else 0))
    else:
        items.sort(key=lambda e: (e["key"][0] == "", -(e["sum"] if m is not None else e["count"]), -e["count"]))
    # what the database may group differently from Excel (stage 4)
    merged = []
    if case == "insensitive":
        folds = {}
        for e in items:
            if e["key"][0] == "s":
                folds.setdefault(_fold(e["label"]), []).append(e)
        merged = [[x["label"] for x in v] for v in folds.values() if len(v) > 1]
    elif not g:
        twins = [[x["label"] for x in v] for v in lower_twins.values() if len(v) > 1]
        merged = twins
    return {"items": items, "case": case, "maybe_merged": merged, "known": bool(g),
            "empty_sums": [e["label"] for e in items if m is not None and e["values"] == 0]}


def _crit(e, A: str, rng: str, lit) -> tuple[str, str]:
    """(criteria for *IFS, condition array for SUMPRODUCT) for one group, by its formula kind."""
    how = e["how"]
    if how == "empty":
        return f'{rng},"="', f'--({rng}="")'
    if how == "month":
        return f'{rng},">="&{A},{rng},"<"&EDATE({A},1)', f'--({rng}>={A}),--({rng}<EDATE({A},1))'
    if how == "exact":
        return "", f"--EXACT({rng},{A})"
    return f"{rng},{A}", f"--({rng}={A})"


def formulas(e, row: int, grng: str, mrng: str | None) -> dict:
    A = f"$A{row}"
    ifs, cond = _crit(e, A, grng, None)
    out = {}
    if e["how"] in ("ifs", "empty", "month"):
        out["count"] = f"=COUNTIFS({ifs})"
        if mrng:
            out["sum"] = f"=SUMIFS({mrng},{ifs})"
            out["avg"] = f'=IFERROR(AVERAGEIFS({mrng},{ifs}),"")'
    else:
        out["count"] = f"=SUMPRODUCT({cond})"
        if mrng:
            out["sum"] = f"=SUMPRODUCT({cond},{mrng})"
            out["avg"] = f'=IFERROR(SUMPRODUCT({cond},{mrng})/SUMPRODUCT({cond},--({mrng}<>"")),"")'
    return out


# ---------------------------------------------------------------- the workbook
def _w(n) -> float:
    return max(8, min(48, n + 2))


def _cell(ws, v, fmt=None, bold=False):
    c = WriteOnlyCell(ws, value=v)
    if isinstance(v, str):
        c.data_type = "s"                       # '=...' from the database stays text: nothing in a result can run
    if fmt:
        c.number_format = fmt
    if bold:
        c.font = HEAD_FONT
        c.fill = HEAD_FILL
    return c


def _fmt_of(values) -> str | None:
    vals = [v for v in values if v is not None]
    if vals and all(isinstance(v, dt.datetime) for v in vals):
        return "yyyy-mm-dd hh:mm:ss"
    if vals and all(isinstance(v, dt.date) and not isinstance(v, dt.datetime) for v in vals):
        return "yyyy-mm-dd"
    floats = [v for v in vals if isinstance(v, float)]
    if floats and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        places = max((len(repr(v).split(".")[1]) if "." in repr(v) and "e" not in repr(v) else 0) for v in floats)
        return "#,##0." + "0" * min(places, 6) if places else "#,##0"
    return None


def build(columns, rows, out_path: str, *, engine: str | None = None, sql: str = "", title: str = "", by: str | None = None,
          measure: str | None = None, hi: bool = False, notes: dict | None = None, typed: bool = True, limited: bool = False) -> dict:
    """Write the workbook to out_path. rows: what the driver returned (typed=True) or read_csv's rows."""
    notes = dict(notes or {})
    columns = [str(c) if c not in (None, "") else f"column_{i + 1}" for i, c in enumerate(columns)]
    if not columns:
        raise ExcelError("The query returned no columns")
    if typed:
        rows = [[_typed_value(v, notes) for v in r] for r in rows]
    if len(rows) > MAX_ROWS:
        notes["cut_rows"] = len(rows) - MAX_ROWS
        rows = rows[:MAX_ROWS]
    n = len(rows)
    wb = Workbook(write_only=True)
    wb.calculation.fullCalcOnLoad = True        # Excel / LibreOffice work every formula out when the file opens

    # Data
    ws = wb.create_sheet("Data")
    fmts = [_fmt_of([r[i] for r in rows[:5000]]) for i in range(len(columns))]
    for i, c in enumerate(columns):
        width = max([len(c)] + [len(str(r[i])) for r in rows[:200] if r[i] is not None])
        ws.column_dimensions[get_column_letter(i + 1)].width = _w(width)
    ws.freeze_panes = "A2"
    last_col = get_column_letter(len(columns))
    ws.auto_filter.ref = f"A1:{last_col}{n + 1}"
    ws.append([_cell(ws, c, bold=True) for c in columns])
    for r in rows:
        ws.append([_cell(ws, v, fmts[i]) if v is not None else None for i, v in enumerate(r)])

    pick = choose(columns, rows, by, measure) if n else {"by": None, "why": "no_rows", "measure": None}
    info = {"rows": n, "columns": columns, "sheets": ["Data"], "pivot": None, "chart": None, "engine": engine}
    G = None
    if pick.get("by") is not None:
        G = groups_of(columns, rows, pick, engine)
        b, m = pick["by"], pick.get("measure")
        items = G["items"]
        grng = f"Data!${get_column_letter(b + 1)}$2:${get_column_letter(b + 1)}${n + 1}"
        mrng = f"Data!${get_column_letter(m + 1)}$2:${get_column_letter(m + 1)}${n + 1}" if m is not None else None
        ps = wb.create_sheet("Pivot")
        gname = columns[b] + (" (month)" if pick["mode"] == "month" else "")
        head = [gname, "Rows"] + ([f"Sum of {columns[m]}", f"Average of {columns[m]}"] if m is not None else [])
        ps.column_dimensions["A"].width = _w(max([len(gname)] + [len(str(e["label"])) for e in items[:300]]))
        for k in range(2, len(head) + 1):
            ps.column_dimensions[get_column_letter(k)].width = _w(len(head[k - 1]))
        ps.freeze_panes = "B2"
        ps.append([_cell(ps, h, bold=True) for h in head])
        sum_fmt = fmts[m] if m is not None and fmts[m] else ("#,##0.00" if m is not None and pick["kinds"][m] == "num" else "#,##0")
        lab_fmt = "mmm yyyy" if pick["mode"] == "month" else fmts[b]
        for k, e in enumerate(items):
            row = k + 2
            f = formulas(e, row, grng, mrng)
            label = EMPTY_LABEL if e["how"] == "empty" else e["label"]
            cells = [_cell(ps, label, None if e["how"] == "empty" else lab_fmt), _cell(ps, f["count"], "#,##0")]
            cells[1].data_type = "f"
            if m is not None:
                cs, ca = _cell(ps, f["sum"], sum_fmt), _cell(ps, f["avg"], "#,##0.00")
                cs.data_type = ca.data_type = "f"
                cells += [cs, ca]
            ps.append(cells)
        last = len(items) + 1
        tot = [_cell(ps, "Total" if not hi else "Kul", bold=True), _cell(ps, f"=SUM(B2:B{last})", "#,##0")]
        tot[1].data_type = "f"
        if m is not None:
            ts, ta = _cell(ps, f"=SUM(C2:C{last})", sum_fmt), _cell(ps, f'=IFERROR(AVERAGE({mrng}),"")', "#,##0.00")
            ts.data_type = ta.data_type = "f"
            tot += [ts, ta]
        ps.append(tot)
        # the chart: drawn from the pivot's own cells, so it moves with them
        line = pick["mode"] == "month" or (pick["kinds"][b] == "date")
        what_col = 3 if m is not None else 2
        bars = min(len(items), CHART_BARS) if not line else len(items)
        ch = LineChart() if line else BarChart()
        ctitle = (f"{head[what_col - 1]} by {gname}") + ("" if bars == len(items) else f" (top {bars})")
        ch.title, ch.height, ch.width, ch.legend = ctitle[:80], 8, 18, None
        ch.add_data(Reference(ps, min_col=what_col, min_row=1, max_row=bars + 1), titles_from_data=True)
        ch.set_categories(Reference(ps, min_col=1, min_row=2, max_row=bars + 1))
        if isinstance(ch, LineChart):
            for s in ch.series:
                s.smooth = False
        ps.add_chart(ch, f"{get_column_letter(len(head) + 2)}2")
        info["sheets"].append("Pivot")

        def show(e):
            if e["how"] == "empty":
                return EMPTY_LABEL
            if e["how"] == "month":
                return e["label"].strftime("%b %Y")
            if isinstance(e["label"], dt.datetime):
                return e["label"].isoformat(sep=" ")
            if isinstance(e["label"], dt.date):
                return e["label"].isoformat()
            return e["label"]
        info["pivot"] = {"by": columns[b], "measure": columns[m] if m is not None else None, "mode": pick["mode"],
                         "groups": len(items), "named": pick.get("named", False),
                         "formulas": sorted({e["how"] for e in items}),
                         "values": [{"label": show(e), "count": e["count"], "sum": e["sum"] if m is not None else None,
                                     "values": e["values"], "avg": e["avg"]} for e in items]}
        info["chart"] = {"title": ctitle, "line": line,
                         "rows": [[show(e), round(e["sum"], 6) if m is not None else e["count"]] for e in items[:bars if not line else 60]]}
    info["no_pivot"] = None if pick.get("by") is not None else pick.get("why")
    for k in ("by_not_found", "measure_not_number"):
        if pick.get(k):
            info[k] = pick[k]
    info["notes"] = _notes(notes, G, pick, columns, engine, hi, limited)
    info["say"] = _say(info, hi)

    # About
    ab = wb.create_sheet("About")
    ab.column_dimensions["A"].width = 22
    ab.column_dimensions["B"].width = 100
    from app.dbknow import engines as _eng

    lines = [("What", title or ("Query ka result" if hi else "Query result")),
             ("Database", _eng.label(engine) if engine else ("Pata nahi" if hi else "Not named")),
             ("Rows", n), ("Made", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))]
    if sql:
        lines.append(("Query", _text(sql, {})))
    if info["pivot"]:
        p = info["pivot"]
        lines.append(("Pivot", (f"{p['groups']} groups by {p['by']}" + (f", adding up {p['measure']}" if p["measure"] else "")
                                + ". The numbers are live formulas over the Data sheet: change a cell there and they follow.")))
    for k, t in enumerate(info["notes"]):
        lines.append(("Note" if k == 0 else "", t))
    ab.append([_cell(ab, "", bold=True), _cell(ab, "", bold=True)])
    for a, t in lines:
        ab.append([_cell(ab, a, bold=bool(a)), _cell(ab, t)])
    info["sheets"].append("About")
    wb.save(out_path)
    info["path"] = out_path
    return info


# ---------------------------------------------------------------- stage 4: everything Excel and the database might disagree on
def _notes(notes, G, pick, columns, engine, hi, limited) -> list[str]:
    T = lambda h, e: h if hi else e  # noqa: E731
    out = []
    if notes.get("cut_rows") or limited:
        out.append(T("Ye sirf pehli rows hain: poora result ek sheet ki seema (ya export ki seema) se bada tha. WHERE se chhota karo.",
                     "These are only the first rows: the whole result was bigger than a sheet (or the export limit). Narrow it with WHERE."))
    if notes.get("long_numbers"):
        out.append(T(f"{notes['long_numbers']} number 15 digit se lambe the: Excel 15 digit hi rakhta hai, isliye ye text mein likhe (koi digit nahi badla).",
                     f"{notes['long_numbers']} numbers had more than 15 digits: Excel keeps only 15, so they are written as text (no digit changed)."))
    if notes.get("time_zones"):
        out.append(T("Excel time zone nahi rakhta: time zone wale samay text mein likhe hain, jaise database ne diye.",
                     "Excel can't hold a time zone: times with a zone are written as text, exactly as the database gave them."))
    if notes.get("control_chars"):
        out.append(T(f"{notes['control_chars']} cells mein control characters the jo Excel nahi rakh sakta; wo hata diye.",
                     f"{notes['control_chars']} cells had control characters Excel can't hold; those characters were left out."))
    if notes.get("cut_text"):
        out.append(T(f"{notes['cut_text']} cells 32,767 characters se lambe the; Excel ki seema tak kaate.",
                     f"{notes['cut_text']} cells were longer than 32,767 characters; cut at Excel's limit."))
    if G is not None:
        if any(e["how"] == "empty" for e in G["items"]) and notes.get("from_csv"):
            out.append(T("\"(empty)\" mein NULL aur khaali text dono hain: export (CSV) mein dono khaali cell bante hain.",
                         "\"(empty)\" holds both NULL and empty text: in the export (CSV) both become an empty cell."))
        if any(e["how"] in ("eq", "exact") for e in G["items"]):
            ex = any(e["how"] == "exact" for e in G["items"])
            out.append(T("Kuch groups SUMIFS ki jagah SUMPRODUCT se gine: SUMIFS * ? ko wildcard, aur digits ko number/date padh leta hai"
                         + (", aur capital/small ek maan leta hai, jabki ye database inhe alag rakhta hai." if ex else "."),
                         "Some groups are counted with SUMPRODUCT instead of SUMIFS: SUMIFS reads * and ? as wildcards and digits as a number or a date"
                         + (", and ignores capitals, while this database keeps 'Red' and 'red' apart." if ex else ".")))
        if G["maybe_merged"]:
            sets = "; ".join(" / ".join(repr(x) for x in s[:4]) for s in G["maybe_merged"][:5])
            if G["known"]:
                out.append(T(f"Dhyan do: {sets} ko database ka GROUP BY ek group maan sakta hai (collation: accents / aakhri space ignore). "
                             "Excel inhe alag ginta hai.",
                             f"Careful: the database's GROUP BY may treat {sets} as one group (its collation can ignore accents and trailing "
                             "spaces); Excel counts them apart."))
            else:
                out.append(T(f"Database ka naam nahi pata: {sets} sirf capital/small mein alag hain. MySQL/MariaDB inhe ek group maante hain, "
                             "PostgreSQL alag. Yahan alag gine hain.",
                             f"The database isn't named: {sets} differ only in capitals. MySQL / MariaDB group them together, PostgreSQL "
                             "keeps them apart. Here they are counted apart."))
        if G["empty_sums"]:
            out.append(T(f"{len(G['empty_sums'])} group(s) mein {columns[pick['measure']]} ki koi value nahi: database ka SUM wahan NULL deta hai, "
                         "Excel ka SUMIFS 0.",
                         f"{len(G['empty_sums'])} group(s) have no {columns[pick['measure']]} values: the database's SUM gives NULL there, "
                         "Excel's SUMIFS gives 0."))
    return out


def _say(info, hi) -> str:
    T = lambda h, e: h if hi else e  # noqa: E731
    n = f"{info['rows']:,}"
    p = info["pivot"]
    if p:
        what = (T(f"{p['by']} ke hisaab se {p['measure']} ka jod aur average", f"{p['measure']} added up and averaged by {p['by']}")
                if p["measure"] else T(f"{p['by']} ke hisaab se rows ki ginti", f"rows counted by {p['by']}"))
        return T(f"Excel file taiyar: {n} rows Data sheet mein, Pivot sheet mein {what} ({p['groups']} groups, live formulas) aur uska chart.",
                 f"Excel file ready: {n} rows in the Data sheet, and a Pivot sheet with {what} ({p['groups']} groups, live formulas) and its chart.")
    why = {"no_rows": T("query ne koi row nahi di", "the query returned no rows"),
           "no_repeats": T("koi column aisa nahi jiski values dohraati hon (2-50 groups)", "no column has repeating values (2-50 groups)"),
           "too_many": T("us column mein bahut zyada alag values hain", "that column has too many different values"),
           "decimal": T("decimal number ke group nahi banate", "decimal numbers don't make groups")}.get(info.get("no_pivot"), "")
    return T(f"Excel file taiyar: {n} rows Data sheet mein. Pivot nahi banaya: {why}. \"status ke hisaab se\" jaisa likho to bana dunga.",
             f"Excel file ready: {n} rows in the Data sheet. No pivot: {why}. Say \"by status\" (any column) and I'll make one.")


# ---------------------------------------------------------------- the person's words: which column to group by / add up
BY_RX = re.compile(r"\b(?:by|per|for each|each|group(?:ed)? by)\s+([A-Za-z_][\w]*)|\b([A-Za-z_][\w]*)\s*(?:-?wise|ke hisaab se|ke hisab se|"
                   r"ke anusaar|ke mutabik|ke according)\b|([A-Za-z_][\w]*)\s+के\s+(?:हिसाब|अनुसार)", re.I)
SUM_RX = re.compile(r"\b(?:sum|total|add up|adding up|jod|jodo|kul)\s+(?:of\s+)?([A-Za-z_][\w]*)|\b([A-Za-z_][\w]*)\s+(?:ka|ki|ke)\s+"
                    r"(?:sum|total|jod|kul|joda)\b", re.I)
STOP = {"the", "a", "an", "of", "all", "each", "every", "row", "rows", "count", "data", "excel", "chart", "pivot", "and", "ka", "ki", "ke",
        "month", "mahine", "sheet", "total", "sum", "number"}


def words_pick(text: str, columns: list[str]) -> dict:
    """'pivot by city with total amount' -> {'by': 'city', 'measure': 'amount'} (only names that are real columns)."""
    out = {}
    for m in BY_RX.finditer(text or ""):
        w = next(g for g in m.groups() if g)
        if w.lower() not in STOP and find_column(columns, w) is not None:
            out.setdefault("by", w)
    for m in SUM_RX.finditer(text or ""):
        w = next(g for g in m.groups() if g)
        if w.lower() not in STOP and find_column(columns, w) is not None:
            out.setdefault("measure", w)
    return out


# ---------------------------------------------------------------- a finished export job -> its workbook
def engine_of(connector) -> str | None:
    """The connector's database, as rule A names it (MariaDB says so in its version string)."""
    t = (getattr(connector, "db_type", "") or "").lower()
    v = (getattr(connector, "version", "") or "").lower()
    if t == "mysql":
        return "mariadb" if "mariadb" in v else "mysql"
    return {"postgres": "postgresql", "postgresql": "postgresql", "sqlite": "sqlite"}.get(t)


def for_job(job, connector, by: str | None = None, measure: str | None = None, hi: bool = False, words: str = "") -> dict:
    """Build (once per choice of columns) the workbook for a finished export job; returns build()'s info."""
    import hashlib
    import os

    if job.kind != "export" or job.status != "done" or not job.file_path or not os.path.exists(job.file_path):
        raise ExcelError("Pehle export poora hone do" if hi else "The export isn't ready yet")
    cols, rows, notes = read_csv(job.file_path)
    if words and not (by or measure):
        w = words_pick(words, cols)
        by, measure = w.get("by"), w.get("measure")
    tag = hashlib.sha256(f"{by}|{measure}|{hi}".encode()).hexdigest()[:10]
    out = f"{job.file_path}.{tag}.xlsx"
    from app.models import loads

    payload = loads(job.payload, {}) or {}
    res = loads(job.result, {}) or {}
    info = build(cols, rows, out, engine=engine_of(connector), sql=payload.get("sql", ""), title=job.summary or "", by=by,
                 measure=measure, hi=hi, notes=notes, typed=False, limited=bool(res.get("limited")))
    info["tag"] = tag
    return info
