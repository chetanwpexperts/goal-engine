"""The Microsoft agent's hands: it works on the uploaded file itself. No AI.

Built for big files: everything reads the workbook as a stream (row by row), so a sheet with lakhs of rows
uses a few hundred MB of memory at most, whatever the file size. Comparing sheets goes through a temporary
SQLite file on disk, never through memory.

  scan(path)            what's wrong: formula errors, broken references, the odd formula out in a column,
                        numbers saved as text, hidden spaces, duplicates
  fix(path, out, ops)   a fixed COPY (the original is never touched) + a "Changes" sheet listing every change
  compare(a, b, out)    rows only in A, only in B, and changed values, as a report workbook

Supported: .xlsx / .xlsm (openpyxl, MIT) and .csv. Files are deleted by the privacy clean-up after 1 hour.
"""

import csv
import io
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

ERRORS = ("#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!", "#SPILL!", "#CALC!")
FAST_READ_BUDGET = 1200 * 1024 * 1024   # memory the fast reader may use for one sheet; bigger sheets use the slow, flat-memory stream
FULL_MODE_MAX = 25 * 1024 * 1024        # below this, fixes keep all formatting; above, values + formulas are streamed
MAX_LISTED = 50                        # cell addresses listed per finding
MAX_CHANGES_LISTED = 5000

_NUM_TEXT = re.compile(r"^[₹$€£]?\s*[-+]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?\s*%?$")
_REF = re.compile(r"(?<![A-Za-z_!.])(\$?)([A-Z]{1,3})(\$?)(\d{1,7})(?![\d(A-Za-z_])")


class OfficeError(Exception):
    pass


# ------------------------------------------------------------------ reading
def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        with open(path, "rb") as f:
            if f.read(2) != b"PK":
                raise OfficeError("This isn't a real .xlsx file (it may be a renamed CSV or old .xls).")
        return "xlsx"
    if ext in (".csv", ".txt"):
        return "csv"
    if ext == ".xls":
        raise OfficeError("Old .xls files: open it in Excel and Save As .xlsx, then upload again.")
    raise OfficeError("Upload an Excel (.xlsx) or CSV file.")


def _csv_rows(path: str):
    raw = open(path, "rb").read(65536)
    enc = "utf-8-sig"
    try:
        raw.decode(enc)
    except UnicodeDecodeError:
        enc = "cp1252"
    sample = raw.decode(enc, errors="replace")
    try:
        dialect = csv.Sniffer().sniff(sample.split("\n", 20)[-1] if False else sample[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    with open(path, newline="", encoding=enc, errors="replace") as f:
        yield from csv.reader(f, dialect)


def sheets(path: str, formulas: bool = False):
    """Yield (sheet_name, row_iterator). Rows are tuples of values, read as a stream."""
    k = kind_of(path)
    if k == "csv":
        yield "CSV", (tuple(r) for r in _csv_rows(path))
        return
    wb = load_workbook(path, read_only=True, data_only=not formulas)
    try:
        for ws in wb.worksheets:
            yield ws.title, ws.iter_rows(values_only=True)
    finally:
        wb.close()


def _fast_ok(path: str) -> bool:
    """The Rust reader (python-calamine, MIT) is ~15x faster but holds a sheet in memory (~1.7x its unpacked XML)."""
    try:
        import zipfile

        import python_calamine  # noqa: F401
    except ImportError:
        return False
    with zipfile.ZipFile(path) as z:
        unpacked = max((i.file_size for i in z.infolist() if i.filename.startswith("xl/worksheets/")), default=0)
        unpacked += sum(i.file_size for i in z.infolist() if i.filename == "xl/sharedStrings.xml")
    return unpacked * 1.7 <= FAST_READ_BUDGET


def value_rows(path: str, sheet: str):
    """Values of one sheet, fastest safe way."""
    if kind_of(path) == "xlsx" and _fast_ok(path):
        from python_calamine import CalamineWorkbook

        wb = CalamineWorkbook.from_path(path)
        for row in wb.get_sheet_by_name(sheet).iter_rows():
            yield tuple(int(v) if isinstance(v, float) and v.is_integer() else v for v in row)
        return
    for name, rows in sheets(path, formulas=False):
        if name == sheet:
            yield from rows
            return


def sheet_names(path: str) -> list[str]:
    if kind_of(path) == "csv":
        return ["CSV"]
    wb = load_workbook(path, read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


# ------------------------------------------------------------------ small helpers
def text_number(v):
    """'1,20,000' / '₹ 450' / ' 12.5 ' / '15%' -> number; None if it shouldn't be converted (IDs, phones, PINs)."""
    if not isinstance(v, str):
        return None
    s = v.replace("\xa0", " ").strip()
    if not s or not _NUM_TEXT.match(s):
        return None
    digits = re.sub(r"\D", "", s.split(".")[0])
    if (len(digits) > 1 and digits.startswith("0") and "." not in s) or len(digits) >= 12:
        return None                               # leading zero or long code: an ID, keep as text
    pct = s.endswith("%")
    s = re.sub(r"[₹$€£,%\s]", "", s)
    try:
        n = float(s)
    except ValueError:
        return None
    if pct:
        n /= 100
    return int(n) if n.is_integer() and not pct and "." not in s else n


def has_bad_spaces(v) -> bool:
    return isinstance(v, str) and not v.startswith("=") and (v != v.strip() or "  " in v or "\xa0" in v)


def clean_spaces(v: str) -> str:
    return " ".join(v.replace("\xa0", " ").split())


def _addr(col: int, row: int) -> str:
    return f"{get_column_letter(col)}{row}"


def formula_shape(formula: str, row: int, col: int) -> str:
    """The formula written relative to its own cell, so =B2*C2 in row 2 and =B3*C3 in row 3 look the same."""
    def rel(m):
        cabs, c, rabs, r = m.groups()
        from openpyxl.utils import column_index_from_string

        cc = c if cabs else f"C[{column_index_from_string(c) - col}]"
        rr = r if rabs else f"R[{int(r) - row}]"
        return f"{cabs}{cc}{rabs}{rr}"
    return _REF.sub(rel, formula)


def formula_from_shape(shape: str, row: int, col: int) -> str:
    def back(m):
        cabs, c, rabs, r = m.groups()
        if c.startswith("C["):
            c = get_column_letter(col + int(c[2:-1]))
        if r.startswith("R["):
            r = str(row + int(r[2:-1]))
        return f"{cabs}{c}{rabs}{r}"
    return re.sub(r"(\$?)(C\[-?\d+\]|[A-Z]{1,3})(\$?)(R\[-?\d+\]|\d+)", back, shape)


# ------------------------------------------------------------------ scan
def scan(path: str) -> dict:
    """What's wrong with this file. Streams every sheet twice (formulas, then the values Excel saved)."""
    report = {"kind": kind_of(path), "size": os.path.getsize(path), "sheets": []}
    shapes: dict = {}
    for name, rows in sheets(path, formulas=True):
        info = {"name": name, "rows": 0, "columns": 0, "header": [], "formulas": 0, "broken_refs": [],
                "odd_formulas": [], "errors": {}, "text_numbers": 0, "text_number_cells": [], "spaces": 0,
                "space_cells": [], "duplicate_rows": 0, "columns_info": []}
        col_shapes = defaultdict(Counter)
        col_cells = defaultdict(list)
        col_types = defaultdict(Counter)
        seen_rows = set()
        for r_i, row in enumerate(rows, start=1):
            if row is None:
                continue
            info["rows"] = r_i
            info["columns"] = max(info["columns"], len(row))
            if r_i == 1:
                info["header"] = [str(v) if v is not None else "" for v in row]
            elif any(v not in (None, "") for v in row):
                h = hash(tuple(clean_spaces(v).lower() if isinstance(v, str) else v for v in row))
                if h in seen_rows:
                    info["duplicate_rows"] += 1
                else:
                    seen_rows.add(h)
            for c_i, v in enumerate(row, start=1):
                if v is None or v == "":
                    col_types[c_i]["empty"] += 1
                    continue
                if isinstance(v, str) and v.startswith("="):
                    info["formulas"] += 1
                    if "#REF!" in v:
                        if len(info["broken_refs"]) < MAX_LISTED:
                            info["broken_refs"].append(_addr(c_i, r_i))
                        info.setdefault("broken_ref_count", 0)
                        info["broken_ref_count"] += 1
                    else:
                        shape = formula_shape(v, r_i, c_i)
                        col_shapes[c_i][shape] += 1
                        if len(col_cells[c_i]) < 200000:
                            col_cells[c_i].append((r_i, shape))
                    col_types[c_i]["formula"] += 1
                    continue
                if r_i == 1:
                    continue
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    col_types[c_i]["number"] += 1
                elif isinstance(v, (datetime, date)):
                    col_types[c_i]["date"] += 1
                elif text_number(v) is not None:
                    col_types[c_i]["text_number"] += 1
                    info["text_numbers"] += 1
                    if len(info["text_number_cells"]) < MAX_LISTED:
                        info["text_number_cells"].append(_addr(c_i, r_i))
                else:
                    col_types[c_i]["text"] += 1
                if has_bad_spaces(v):
                    info["spaces"] += 1
                    if len(info["space_cells"]) < MAX_LISTED:
                        info["space_cells"].append(_addr(c_i, r_i))
        # the odd formula out: one row whose formula differs from the 80%+ of its column
        for c_i, counter in col_shapes.items():
            total = sum(counter.values())
            main, n = counter.most_common(1)[0]
            if total >= 5 and n / total >= 0.8 and n < total:
                for r_i, shape in col_cells[c_i]:
                    if shape != main and len(info["odd_formulas"]) < MAX_LISTED:
                        info["odd_formulas"].append(_addr(c_i, r_i))
            shapes[(name, c_i)] = main if total and n / total >= 0.6 else None
        for c_i in sorted(col_types):
            t = col_types[c_i]
            head = info["header"][c_i - 1] if c_i - 1 < len(info["header"]) else ""
            info["columns_info"].append({"column": get_column_letter(c_i), "header": head, **dict(t)})
        report["sheets"].append(info)
    # values Excel saved: error results like #N/A, #DIV/0!
    if report["kind"] == "xlsx":
        by_name = {s["name"]: s for s in report["sheets"]}
        for name, rows in sheets(path, formulas=False):
            errs = by_name[name]["errors"]
            for r_i, row in enumerate(rows, start=1):
                for c_i, v in enumerate(row or (), start=1):
                    if isinstance(v, str) and v in ERRORS:
                        e = errs.setdefault(v, {"count": 0, "cells": []})
                        e["count"] += 1
                        if len(e["cells"]) < MAX_LISTED:
                            e["cells"].append(_addr(c_i, r_i))
    report["summary"] = _summary(report)
    return report


def _summary(report: dict) -> dict:
    s = Counter()
    for sh in report["sheets"]:
        s["rows"] += max(sh["rows"] - 1, 0)
        s["formulas"] += sh["formulas"]
        s["broken_refs"] += sh.get("broken_ref_count", 0)
        s["odd_formulas"] += len(sh["odd_formulas"])
        s["errors"] += sum(e["count"] for e in sh["errors"].values())
        s["text_numbers"] += sh["text_numbers"]
        s["spaces"] += sh["spaces"]
        s["duplicate_rows"] += sh["duplicate_rows"]
    s["sheets"] = len(report["sheets"])
    return dict(s)


# ------------------------------------------------------------------ fix (always a copy)
FIX_OPS = {"trim_spaces", "text_to_number", "fix_broken_refs", "remove_duplicates"}


def fix(path: str, out: str, ops: list[str]) -> dict:
    ops = [o for o in ops if o in FIX_OPS]
    if not ops:
        raise OfficeError("Nothing to fix.")
    k = kind_of(path)
    if k == "csv":
        return _fix_csv(path, out, ops)
    if os.path.getsize(path) <= FULL_MODE_MAX:
        return _fix_full(path, out, ops)
    return _fix_stream(path, out, ops)


class _Log:
    def __init__(self):
        self.counts = Counter()
        self.rows = []

    def add(self, op, sheet, cell, old, new):
        self.counts[op] += 1
        if len(self.rows) < MAX_CHANGES_LISTED:
            self.rows.append((op, sheet, cell, "" if old is None else str(old)[:200], "" if new is None else str(new)[:200]))


def _new_value(v, ops, log, sheet, cell):
    if isinstance(v, str) and not v.startswith("="):
        if "trim_spaces" in ops and has_bad_spaces(v):
            new = clean_spaces(v)
            log.add("trim_spaces", sheet, cell, v, new)
            v = new
        if "text_to_number" in ops:
            n = text_number(v)
            if n is not None:
                log.add("text_to_number", sheet, cell, v, n)
                v = n
    return v


def _fix_full(path, out, ops):
    """Keeps all formatting: the workbook is opened normally and changed cell by cell."""
    keep_vba = path.lower().endswith(".xlsm")
    wb = load_workbook(path, keep_vba=keep_vba)
    log = _Log()
    shapes = {}
    if "fix_broken_refs" in ops:
        for ws in wb.worksheets:
            col_shapes = defaultdict(Counter)
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if isinstance(v, str) and v.startswith("=") and "#REF!" not in v:
                        col_shapes[cell.column][formula_shape(v, cell.row, cell.column)] += 1
            for c, counter in col_shapes.items():
                main, n = counter.most_common(1)[0]
                if n >= 3 and n / sum(counter.values()) >= 0.6:
                    shapes[(ws.title, c)] = main
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if v is None:
                    continue
                if "fix_broken_refs" in ops and isinstance(v, str) and v.startswith("=") and "#REF!" in v:
                    shape = shapes.get((ws.title, cell.column))
                    if shape:
                        new = formula_from_shape(shape, cell.row, cell.column)
                        log.add("fix_broken_refs", ws.title, cell.coordinate, v, new)
                        cell.value = new
                    else:
                        log.counts["broken_refs_left"] += 1
                    continue
                new = _new_value(v, ops, log, ws.title, cell.coordinate)
                if new is not v:
                    cell.value = new
                    if isinstance(new, float) and isinstance(v, str) and v.strip().endswith("%"):
                        cell.number_format = "0%"
        if "remove_duplicates" in ops:
            _dedupe_sheet(wb, ws, log)
    _changes_sheet(wb.create_sheet("Changes by Goal Engine"), log)
    wb.save(out)
    return {"changes": dict(log.counts), "formatting_kept": True}


def _dedupe_sheet(wb, ws, log):
    """Delete repeated rows (keeping the first) only where it can't break a formula."""
    has_formulas = any(isinstance(c.value, str) and str(c.value).startswith("=") for row in ws.iter_rows() for c in row)
    referenced = any(isinstance(c.value, str) and str(c.value).startswith("=") and ws.title in str(c.value)
                     for other in wb.worksheets if other is not ws for row in other.iter_rows() for c in row)
    if has_formulas or referenced:
        log.counts["duplicates_skipped_sheets"] += 1
        return
    seen, dupes = set(), []
    for row in ws.iter_rows(min_row=2):
        vals = tuple(clean_spaces(c.value).lower() if isinstance(c.value, str) else c.value for c in row)
        if all(v in (None, "") for v in vals):
            continue
        if vals in seen:
            dupes.append(row[0].row)
        else:
            seen.add(vals)
    for r in reversed(dupes):
        log.add("remove_duplicates", ws.title, f"row {r}", "", "")
        ws.delete_rows(r)


def _fix_stream(path, out, ops):
    """Big files: rewritten row by row (values and formulas kept, cell formatting not)."""
    wb_out = Workbook(write_only=True)
    log = _Log()
    for name, rows in sheets(path, formulas=True):
        ws = wb_out.create_sheet(name)
        seen = set()
        for r_i, row in enumerate(rows, start=1):
            row = [_new_value(v, ops, log, name, _addr(c_i, r_i)) for c_i, v in enumerate(row or (), start=1)]
            if "remove_duplicates" in ops and r_i > 1 and not any(isinstance(v, str) and v.startswith("=") for v in row):
                h = hash(tuple(v.lower() if isinstance(v, str) else v for v in row))
                if any(v not in (None, "") for v in row) and h in seen:
                    log.add("remove_duplicates", name, f"row {r_i}", "", "")
                    continue
                seen.add(h)
            ws.append(row)
    if "fix_broken_refs" in ops:
        log.counts["broken_refs_need_small_file"] += 1
    _changes_sheet(wb_out.create_sheet("Changes by Goal Engine"), log)
    wb_out.save(out)
    return {"changes": dict(log.counts), "formatting_kept": False}


def _fix_csv(path, out, ops):
    log = _Log()
    seen = set()
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for r_i, row in enumerate(_csv_rows(path), start=1):
            row = [_new_value(v, ops, log, "CSV", _addr(c_i, r_i)) for c_i, v in enumerate(row, start=1)]
            if "remove_duplicates" in ops and r_i > 1:
                h = hash(tuple(str(v).lower() for v in row))
                if h in seen:
                    log.add("remove_duplicates", "CSV", f"row {r_i}", "", "")
                    continue
                seen.add(h)
            w.writerow(row)
    return {"changes": dict(log.counts), "formatting_kept": True}


def _changes_sheet(ws, log):
    ws.append(["What", "Sheet", "Cell", "Before", "After"])
    for r in log.rows:
        ws.append(list(r))


# ------------------------------------------------------------------ compare two sheets / files
def _norm(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return repr(float(v))
    if isinstance(v, (datetime, date)):
        return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
    s = clean_spaces(str(v))
    n = text_number(s)
    if n is not None:
        return repr(float(n))
    return s.lower()


def _pick_sheet(path, sheet):
    for name in sheet_names(path):
        if sheet in (None, "", name):
            return name
    raise OfficeError(f"Sheet '{sheet}' not found.")


def _load(db, table, path, sheet):
    db.execute(f"CREATE TABLE {table} (rn INTEGER, k TEXT, vals TEXT, raw TEXT)")
    header = None
    for _ in (1,):
        batch = []
        for r_i, row in enumerate(value_rows(path, sheet), start=1):
            row = list(row or ())
            if header is None:
                header = [clean_spaces(str(h)) if h is not None else f"Column {i + 1}" for i, h in enumerate(row)]
                continue
            if not any(v not in (None, "") for v in row):
                continue
            batch.append((r_i, json.dumps([_norm(v) for v in row]), json.dumps([None if v is None else str(v) for v in row], default=str)))
            if len(batch) >= 5000:
                db.executemany(f"INSERT INTO {table} (rn, vals, raw) VALUES (?,?,?)", batch)
                batch = []
        db.executemany(f"INSERT INTO {table} (rn, vals, raw) VALUES (?,?,?)", batch)
    return header or []


def _key_column(db, header_a, header_b):
    common = [h for h in header_a if h and h in header_b]
    if not common:
        raise OfficeError("The two sheets have no column with the same name. Rename the matching column (e.g. 'Invoice No') in both.")
    best, best_score = None, -1.0
    total = db.execute("SELECT COUNT(*) FROM a").fetchone()[0] or 1
    for h in common:
        i = header_a.index(h)
        distinct, filled = db.execute(f"SELECT COUNT(DISTINCT json_extract(vals,'$[{i}]')), "
                                      f"SUM(json_extract(vals,'$[{i}]') != '') FROM a").fetchone()
        score = (distinct or 0) / total * ((filled or 0) / total)
        if re.search(r"\b(id|code|no|number|email|invoice|sku|roll|emp)\b", h, re.I):
            score += 0.05
        if score > best_score:
            best, best_score = h, score
    return best


def compare(path_a: str, path_b: str, out: str, sheet_a: str = "", sheet_b: str = "", key: str = "") -> dict:
    """Rows only in A, only in B, and changed values, matched on a key column (found automatically)."""
    sa, sb = _pick_sheet(path_a, sheet_a), _pick_sheet(path_b, sheet_b)
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db = sqlite3.connect(tmp)
    try:
        ha = _load(db, "a", path_a, sa)
        hb = _load(db, "b", path_b, sb)
        key = key if key in ha and key in hb else _key_column(db, ha, hb)
        ia, ib = ha.index(key), hb.index(key)
        db.execute(f"UPDATE a SET k = json_extract(vals,'$[{ia}]')")
        db.execute(f"UPDATE b SET k = json_extract(vals,'$[{ib}]')")
        db.execute("CREATE INDEX ak ON a(k)")
        db.execute("CREATE INDEX bk ON b(k)")
        common = [(h, ha.index(h), hb.index(h)) for h in ha if h and h in hb and h != key]

        wb = Workbook(write_only=True)
        summary = wb.create_sheet("Summary")
        only_a = wb.create_sheet(f"Only in A")
        only_b = wb.create_sheet(f"Only in B")
        changed = wb.create_sheet("Changed")
        only_a.append(["Row in A"] + ha)
        only_b.append(["Row in B"] + hb)
        changed.append([key, "Column", "Value in A", "Value in B", "Row in A", "Row in B"])
        counts = Counter()
        for rn, raw in db.execute("SELECT rn, raw FROM a WHERE k != '' AND k NOT IN (SELECT k FROM b WHERE k != '') ORDER BY rn"):
            counts["only_in_a"] += 1
            only_a.append([rn] + json.loads(raw))
        for rn, raw in db.execute("SELECT rn, raw FROM b WHERE k != '' AND k NOT IN (SELECT k FROM a WHERE k != '') ORDER BY rn"):
            counts["only_in_b"] += 1
            only_b.append([rn] + json.loads(raw))
        for rn_a, va, ra, rn_b, vb, rb in db.execute(
                "SELECT a.rn, a.vals, a.raw, b.rn, b.vals, b.raw FROM a JOIN b ON a.k = b.k WHERE a.k != '' AND a.vals != b.vals ORDER BY a.rn"):
            va, vb, ra, rb = json.loads(va), json.loads(vb), json.loads(ra), json.loads(rb)
            row_changed = False
            for h, i, j in common:
                x = va[i] if i < len(va) else ""
                y = vb[j] if j < len(vb) else ""
                if x != y:
                    row_changed = True
                    counts["changed_cells"] += 1
                    changed.append([ra[ia], h, ra[i] if i < len(ra) else "", rb[j] if j < len(rb) else "", rn_a, rn_b])
            counts["changed_rows"] += row_changed
        counts["matched"] = db.execute("SELECT COUNT(DISTINCT a.k) FROM a JOIN b ON a.k = b.k WHERE a.k != ''").fetchone()[0]
        counts["dup_keys_a"] = db.execute("SELECT COUNT(*) FROM (SELECT k FROM a WHERE k!='' GROUP BY k HAVING COUNT(*)>1)").fetchone()[0]
        counts["dup_keys_b"] = db.execute("SELECT COUNT(*) FROM (SELECT k FROM b WHERE k!='' GROUP BY k HAVING COUNT(*)>1)").fetchone()[0]
        for label, val in (("Matched on column", key), ("Rows matched", counts["matched"]), ("Only in A", counts["only_in_a"]),
                           ("Only in B", counts["only_in_b"]), ("Rows with changed values", counts["changed_rows"]),
                           ("Changed cells", counts["changed_cells"]),
                           ("Repeated keys in A", counts["dup_keys_a"]), ("Repeated keys in B", counts["dup_keys_b"]),
                           ("Ignored", "extra spaces, capital/small letters, numbers saved as text")):
            summary.append([label, val])
        wb.save(out)
        return {"key": key, "sheet_a": sa, "sheet_b": sb, **dict(counts)}
    finally:
        db.close()
        os.remove(tmp)
