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
CONVERT = (".xls", ".ods", ".xlsb")          # turned into .xlsx when they arrive


def _head(path: str, n: int = 65536) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def is_protected(path: str) -> bool:
    """A password-protected Excel file is an encrypted container, not a normal workbook."""
    head = _head(path)
    return head[:4] == b"\xd0\xcf\x11\xe0" and "EncryptionInfo".encode("utf-16-le") in head


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    head = _head(path, 8)
    if ext in (".xlsx", ".xlsm") or ext in CONVERT:
        if is_protected(path):
            raise OfficeError("This file has a password. Open it in Excel, remove the password (File > Info > Protect Workbook > "
                              "Encrypt with Password, clear it), save, and upload it again.")
        if ext in (".xlsx", ".xlsm") and head[:2] != b"PK":
            if head[:4] == b"\xd0\xcf\x11\xe0":
                return "convert"                  # an old .xls saved with a .xlsx name: convert it like one
            raise OfficeError("This isn't a real Excel file (it may be a renamed CSV or another kind of file).")
        return "convert" if ext in CONVERT else "xlsx"
    if ext in (".csv", ".txt"):
        return "csv"
    if ext == ".docx":
        if head[:2] != b"PK":
            if head[:4] == b"\xd0\xcf\x11\xe0":
                raise OfficeError("This Word file has a password or is an old .doc saved with a .docx name. Open it in Word, remove any "
                                  "password, Save As .docx, and upload it again.")
            raise OfficeError("This isn't a real Word file (it may be another kind of file renamed to .docx).")
        return "docx"
    if ext == ".doc":
        raise OfficeError("This is an old Word file (.doc). Open it in Word, choose Save As > Word Document (.docx), and upload that.")
    raise OfficeError("Upload an Excel (.xlsx, .xls, .xlsm, .xlsb), OpenDocument (.ods), CSV or Word (.docx) file.")


def convert_to_xlsx(path: str) -> tuple[str, dict]:
    """Old Excel (.xls), OpenDocument (.ods) and binary (.xlsb) -> a normal .xlsx with the same sheets and values.
    Returns (new path, notes). Formulas come over as their results (those formats can't be rewritten safely)."""
    from openpyxl import Workbook
    from python_calamine import CalamineWorkbook

    real = path
    if os.path.splitext(path)[1].lower() in (".xlsx", ".xlsm") and _head(path, 4) == b"\xd0\xcf\x11\xe0":
        real = os.path.splitext(path)[0] + ".as-xls.xls"     # an old .xls with a .xlsx name: read it as what it is
        os.replace(path, real)
    src = CalamineWorkbook.from_path(real)
    out = os.path.splitext(path)[0] + ".converted.xlsx"
    wb = Workbook(write_only=True)
    sheets = 0
    for name in src.sheet_names:
        try:
            rows = src.get_sheet_by_name(name).to_python(skip_empty_area=False)
        except Exception:  # noqa: BLE001 - a chart sheet or an unreadable one: skip it, the rest still come over
            continue
        ws = wb.create_sheet(title=name[:31])
        for r in rows:
            ws.append([None if v == "" else v for v in r])
        sheets += 1
    wb.save(out)
    if real != path and os.path.exists(real):
        os.remove(real)
    return out, {"converted_from": os.path.splitext(real)[1].lower(), "sheets": sheets}


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
CHANGES_SHEET = "Changes by Goal Engine"
_ROWNO = re.compile(r"^\s*(record\s*id|s\.?\s*no\.?|sr\.?\s*no\.?|sl\.?\s*no\.?|serial(\s*no\.?)?|row(\s*no\.?)?|#|no\.?|id)\s*$", re.I)


def row_key(header, row):
    """What makes two rows 'the same': every value, ignoring spaces and capitals, and ignoring a plain row-number column
    (Record ID 1 and Record ID 5 with the same person are still a duplicate)."""
    skip = {i for i, h in enumerate(header or ()) if isinstance(h, str) and _ROWNO.match(h)}
    return tuple(clean_spaces(v).lower() if isinstance(v, str) else v
                 for i, v in enumerate(row) if i not in skip or not isinstance(v, (int, float)))
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
    if path.lower().endswith(".docx"):
        from app.office import word

        return word.scan(path)
    report = {"kind": kind_of(path), "size": os.path.getsize(path), "sheets": []}
    shapes: dict = {}
    for name, rows in sheets(path, formulas=True):
        if name in (CHANGES_SHEET, "Duplicates (Goal Engine)"):
            continue
        info = {"name": name, "rows": 0, "columns": 0, "header": [], "formulas": 0, "broken_refs": [],
                "odd_formulas": [], "errors": {}, "text_numbers": 0, "text_number_cells": [], "spaces": 0,
                "space_cells": [], "duplicate_rows": 0, "columns_info": [], "case_mixed": 0, "case_cells": [], "duplicate_list": [],
                "samples": {"trim_spaces": [], "text_to_number": [], "fix_case": [], "fix_broken_refs": []}}
        col_shapes = defaultdict(Counter)
        col_words = defaultdict(lambda: defaultdict(Counter))   # column -> "finance" -> {"Finance": 3, "finance": 1}
        col_word_cells = defaultdict(list)
        col_cells = defaultdict(list)
        col_types = defaultdict(Counter)
        seen_rows = {}
        for r_i, row in enumerate(rows, start=1):
            if row is None:
                continue
            info["rows"] = r_i
            info["columns"] = max(info["columns"], len(row))
            if r_i == 1:
                info["header"] = [str(v) if v is not None else "" for v in row]
            elif any(v not in (None, "") for v in row):
                h = hash(row_key(info["header"], row))
                if h in seen_rows:
                    info["duplicate_rows"] += 1
                    if len(info["duplicate_list"]) < MAX_LISTED:
                        info["duplicate_list"].append([r_i, seen_rows[h]])     # this row repeats that one
                    if len(info.setdefault("dup_samples", [])) < 3:
                        info["dup_samples"].append([r_i, seen_rows[h], " · ".join(str(v) for v in row if v not in (None, ""))[:90]])
                elif len(seen_rows) < 3_000_000:
                    seen_rows[h] = r_i
            for c_i, v in enumerate(row, start=1):
                if v is None or v == "":
                    col_types[c_i]["empty"] += 1
                    continue
                if isinstance(v, str) and v.startswith("="):
                    info["formulas"] += 1
                    if "#REF!" in v:
                        if len(info["broken_refs"]) < MAX_LISTED:
                            info["broken_refs"].append(_addr(c_i, r_i))
                        if len(info["samples"]["fix_broken_refs"]) < 4:
                            info["samples"]["fix_broken_refs"].append([_addr(c_i, r_i), v, ""])
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
                    if len(info["samples"]["text_to_number"]) < 4:
                        info["samples"]["text_to_number"].append([_addr(c_i, r_i), v, text_number(v)])
                else:
                    col_types[c_i]["text"] += 1
                    w = clean_spaces(v)
                    if len(w) <= 60 and len(col_words[c_i]) < 5000:
                        col_words[c_i][w.lower()][w] += 1
                        if len(col_word_cells[c_i]) < 50000:
                            col_word_cells[c_i].append((r_i, w))
                if has_bad_spaces(v):
                    info["spaces"] += 1
                    if len(info["space_cells"]) < MAX_LISTED:
                        info["space_cells"].append(_addr(c_i, r_i))
                    if len(info["samples"]["trim_spaces"]) < 4:          # the real value and what the fix makes of it
                        info["samples"]["trim_spaces"].append([_addr(c_i, r_i), v, clean_spaces(v)])
        # the odd formula out: one row whose formula differs from the 80%+ of its column
        for c_i, counter in col_shapes.items():
            total = sum(counter.values())
            main, n = counter.most_common(1)[0]
            if total >= 5 and n / total >= 0.8 and n < total:
                for r_i, shape in col_cells[c_i]:
                    if shape != main and len(info["odd_formulas"]) < MAX_LISTED:
                        info["odd_formulas"].append(_addr(c_i, r_i))
            shapes[(name, c_i)] = main if total and n / total >= 0.6 else None
        # same word written with different capital letters in one column ("Finance" / "finance")
        for c_i, groups in col_words.items():
            main = {k: c.most_common(1)[0][0] for k, c in groups.items() if len(c) > 1}
            for r_i, w in col_word_cells[c_i]:
                m = main.get(w.lower())
                if m is not None and w != m:
                    info["case_mixed"] += 1
                    if len(info["case_cells"]) < MAX_LISTED:
                        info["case_cells"].append(_addr(c_i, r_i))
                    if len(info["samples"]["fix_case"]) < 4:
                        info["samples"]["fix_case"].append([_addr(c_i, r_i), w, m])
        for c_i in sorted(col_types):
            t = col_types[c_i]
            head = info["header"][c_i - 1] if c_i - 1 < len(info["header"]) else ""
            info["columns_info"].append({"column": get_column_letter(c_i), "header": head, **dict(t)})
        report["sheets"].append(info)
    # values Excel saved: error results like #N/A, #DIV/0!
    if report["kind"] == "xlsx":
        by_name = {s["name"]: s for s in report["sheets"]}
        for name, rows in sheets(path, formulas=False):
            if name not in by_name:
                continue
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
        s["case_mixed"] += sh.get("case_mixed", 0)
    s["sheets"] = len(report["sheets"])
    return dict(s)


# ------------------------------------------------------------------ fix (always a copy)
FIX_OPS = {"trim_spaces", "text_to_number", "fix_broken_refs", "remove_duplicates", "fix_case"}
LABELS = {"trim_spaces": "Extra space hataya (removed extra spaces)", "text_to_number": "Text ko number banaya (text turned into a number)",
          "fix_broken_refs": "Toota formula theek kiya (broken formula rebuilt)", "remove_duplicates": "Duplicate row hatayi (repeated row removed)",
          "fix_case": "Capital/small letters ek jaise kiye (same spelling everywhere)"}


def case_maps(rows) -> dict:
    """Per column: lower-case word -> the spelling used most often. Only words written more than one way."""
    groups = defaultdict(lambda: defaultdict(Counter))
    for r_i, row in enumerate(rows, start=1):
        if r_i == 1:
            continue
        for c_i, v in enumerate(row or (), start=1):
            if isinstance(v, str) and not v.startswith("=") and text_number(v) is None:
                w = clean_spaces(v)
                if 0 < len(w) <= 60 and len(groups[c_i]) < 5000:
                    groups[c_i][w.lower()][w] += 1
    return {c: {k: cnt.most_common(1)[0][0] for k, cnt in g.items() if len(cnt) > 1} for c, g in groups.items()}


def _cased(v, maps, c_i, r_i, log, sheet, cell):
    if r_i == 1 or not isinstance(v, str) or v.startswith("="):
        return v
    m = maps.get(c_i, {}).get(clean_spaces(v).lower())
    if m is not None and clean_spaces(v) != m:
        log.add("fix_case", sheet, cell, v, m)
        return m
    return v


def fix(path: str, out: str, ops: list[str], only_sheets=None) -> dict:
    """only_sheets: change just these sheets ("Sales Data"); the others are copied as they are."""
    ops = [o for o in ops if o in FIX_OPS]
    if not ops:
        raise OfficeError("Nothing to fix.")
    k = kind_of(path)
    if k == "csv":
        return _fix_csv(path, out, ops)
    if os.path.getsize(path) <= FULL_MODE_MAX:
        return _fix_full(path, out, ops, only_sheets)
    return _fix_stream(path, out, ops, only_sheets)


class _Log:
    def __init__(self):
        self.counts = Counter()
        self.rows = []

    def examples(self, per_op: int = 4) -> dict:
        out = {}
        for op, sheet, cell, old, new in self.rows:
            if len(out.setdefault(op, [])) < per_op:
                out[op].append([sheet, cell, old, new])
        return out

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


def _fix_full(path, out, ops, only_sheets=None):
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
        if ws.title in (CHANGES_SHEET, "Duplicates (Goal Engine)") or (only_sheets and ws.title not in only_sheets):
            continue
        maps = case_maps(ws.iter_rows(values_only=True)) if "fix_case" in ops else {}
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
                if maps:
                    new = _cased(new, maps, cell.column, cell.row, log, ws.title, cell.coordinate)
                if new is not v:
                    cell.value = new
                    if isinstance(new, float) and isinstance(v, str) and v.strip().endswith("%"):
                        cell.number_format = "0%"
        if "remove_duplicates" in ops:
            _dedupe_sheet(wb, ws, log)
    if "remove_duplicates" in ops and "Duplicates (Goal Engine)" in wb.sheetnames and log.counts.get("remove_duplicates"):
        del wb["Duplicates (Goal Engine)"]               # the list is out of date once they're removed
    had = CHANGES_SHEET in wb.sheetnames
    _changes_sheet(wb[CHANGES_SHEET] if had else wb.create_sheet(CHANGES_SHEET), log, fresh=not had)
    wb.save(out)
    return {"changes": dict(log.counts), "formatting_kept": True, "examples": log.examples()}


def _dedupe_sheet(wb, ws, log):
    """Delete repeated rows (keeping the first) only where it can't break a formula."""
    has_formulas = any(isinstance(c.value, str) and str(c.value).startswith("=") for row in ws.iter_rows() for c in row)
    referenced = any(isinstance(c.value, str) and str(c.value).startswith("=") and ws.title in str(c.value)
                     for other in wb.worksheets if other is not ws for row in other.iter_rows() for c in row)
    if has_formulas or referenced:
        log.counts["duplicates_skipped_sheets"] += 1
        return
    seen, dupes = {}, []
    header = [c.value for c in next(ws.iter_rows(max_row=1), [])]
    for row in ws.iter_rows(min_row=2):
        vals = row_key(header, [c.value for c in row])
        if all(v in (None, "") for v in vals):
            continue
        if vals in seen:
            dupes.append((row[0].row, seen[vals], " · ".join(str(c.value) for c in row if c.value not in (None, ""))[:90]))
        else:
            seen[vals] = row[0].row
    for r, same, text in dupes:
        log.add("remove_duplicates", ws.title, f"row {r}", text, f"same as row {same}")
    for r, _, _ in reversed(dupes):
        ws.delete_rows(r)


def _fix_stream(path, out, ops, only_sheets=None):
    """Big files: rewritten row by row (values and formulas kept, cell formatting not)."""
    wb_out = Workbook(write_only=True)
    log = _Log()
    all_maps = {}
    if "fix_case" in ops:
        for name, rows in sheets(path, formulas=True):
            all_maps[name] = case_maps(rows)
    for name, rows in sheets(path, formulas=True):
        if name == CHANGES_SHEET:
            continue
        ws = wb_out.create_sheet(name)
        if name == "Duplicates (Goal Engine)":
            for row in rows:
                ws.append(list(row or ()))
            continue
        if only_sheets and name not in only_sheets:
            for row in rows:
                ws.append(list(row or ()))
            continue
        seen = set()
        maps = all_maps.get(name, {})
        header = None
        for r_i, row in enumerate(rows, start=1):
            row = [_cased(_new_value(v, ops, log, name, _addr(c_i, r_i)), maps, c_i, r_i, log, name, _addr(c_i, r_i))
                   for c_i, v in enumerate(row or (), start=1)]
            if header is None:
                header = row
            if "remove_duplicates" in ops and r_i > 1 and not any(isinstance(v, str) and v.startswith("=") for v in row):
                h = hash(row_key(header, row))
                if any(v not in (None, "") for v in row) and h in seen:
                    log.add("remove_duplicates", name, f"row {r_i}", "", "")
                    continue
                seen.add(h)
            ws.append(row)
    if "fix_broken_refs" in ops:
        log.counts["broken_refs_need_small_file"] += 1
    earlier = []
    for name, rows in sheets(path, formulas=False):
        if name == CHANGES_SHEET:
            earlier = [r for i, r in enumerate(rows) if i > 0]
    _changes_sheet(wb_out.create_sheet(CHANGES_SHEET), log, earlier)
    wb_out.save(out)
    return {"changes": dict(log.counts), "formatting_kept": False, "examples": log.examples()}


def _fix_csv(path, out, ops):
    log = _Log()
    seen = set()
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        maps = case_maps(_csv_rows(path)) if "fix_case" in ops else {}
        header = None
        for r_i, row in enumerate(_csv_rows(path), start=1):
            row = [_cased(_new_value(v, ops, log, "CSV", _addr(c_i, r_i)), maps, c_i, r_i, log, "CSV", _addr(c_i, r_i))
                   for c_i, v in enumerate(row, start=1)]
            if header is None:
                header = row
            if "remove_duplicates" in ops and r_i > 1:
                h = hash(row_key(header, [text_number(v) if text_number(v) is not None else v for v in row]))
                if h in seen:
                    log.add("remove_duplicates", "CSV", f"row {r_i}", "", "")
                    continue
                seen.add(h)
            w.writerow(row)
    return {"changes": dict(log.counts), "formatting_kept": True, "examples": log.examples()}


def _changes_sheet(ws, log, earlier=(), fresh=True):
    """The list of every change; a file fixed again keeps the earlier steps at the top."""
    if fresh:
        ws.append(["Kya kiya (what)", "Sheet", "Cell", "Pehle (before)", "Ab (after)"])
    for r in earlier:
        ws.append(list(r))
    for op, *rest in log.rows:
        ws.append([LABELS.get(op, op), *rest])


# ------------------------------------------------------------------ find duplicates and put them in their own sheet (changes nothing)
DUP_SHEET = "Duplicates (Goal Engine)"
_KEYCOL = re.compile(r"(\bid\b|\bno\.?$|\bnumber\b|\bcode\b|e-?mail|invoice|phone|mobile|sku|pan\b|gst|aadhaar|roll)", re.I)


def find_duplicates(path: str, only_sheets=None) -> list[dict]:
    """Two kinds: the same whole row twice, and the same ID / invoice / email used on more than one row."""
    found = []
    for name, rows in sheets(path, formulas=False):
        if name in (CHANGES_SHEET, DUP_SHEET) or (only_sheets and name not in only_sheets):
            continue
        header, seen, keys, keycols, vals = None, {}, defaultdict(dict), [], defaultdict(list)
        for r_i, row in enumerate(rows, start=1):
            row = list(row or ())
            if header is None:
                header = [clean_spaces(str(h)) if h is not None else "" for h in row]
                keycols = [i for i, h in enumerate(header) if h and _KEYCOL.search(h) and not _ROWNO.match(h)
                           and not re.search(r"date|time|status|type|category", h, re.I)]
                continue
            if not any(v not in (None, "") for v in row):
                continue
            h = hash(row_key(header, row))
            if h in seen:
                found.append({"sheet": name, "row": r_i, "same_as": seen[h], "kind": "row", "column": "", "value": ""})
                continue
            seen[h] = r_i
            for i in keycols:
                v = row[i] if i < len(row) else None
                if v not in (None, "") and len(vals[i]) < 2_000_000:
                    vals[i].append((r_i, v))
        # an ID column is one where almost every value is different; a few repeats there are real duplicates.
        # (Item Code repeating on every purchase is normal, so it's not checked.)
        for i, items in vals.items():
            distinct = len({clean_spaces(str(v)).lower() for _, v in items})
            if not items or distinct / len(items) < 0.9:
                continue
            first = {}
            for r_i, v in items:
                k = clean_spaces(str(v)).lower()
                if k in first:
                    found.append({"sheet": name, "row": r_i, "same_as": first[k], "kind": "value", "column": header[i], "value": str(v)})
                else:
                    first[k] = r_i
    return found


def extract_duplicates(path: str, out: str, only_sheets=None) -> dict:
    """A copy of the file with one new sheet listing every duplicate. Nothing else changes."""
    found = find_duplicates(path, only_sheets)
    if kind_of(path) == "xlsx" and os.path.getsize(path) <= FULL_MODE_MAX:
        wb = load_workbook(path, keep_vba=path.lower().endswith(".xlsm"))
        if DUP_SHEET in wb.sheetnames:
            del wb[DUP_SHEET]
        ws = wb.create_sheet(DUP_SHEET, 0)
    else:
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(DUP_SHEET)
        if kind_of(path) == "xlsx":
            for name, rows in sheets(path, formulas=True):
                if name == DUP_SHEET:
                    continue
                copy = wb.create_sheet(name)
                for row in rows:
                    copy.append(list(row or ()))
        else:
            copy = wb.create_sheet("Data")
            for row in _csv_rows(path):
                copy.append(row)
        out = os.path.splitext(out)[0] + ".xlsx"
    ws.append(["Sheet", "Row", "Same as row", "Kya same hai (what repeats)", "Column", "Value"])
    for d in found:
        what = "Poori row (whole row)" if d["kind"] == "row" else f"Same {d['column']}"
        ws.append([d["sheet"], d["row"], d["same_as"], what, d["column"], d["value"]])
    if not found:
        ws.append(["Koi duplicate nahi mila (no duplicates found)"])
    wb.save(out)
    by = Counter((d["sheet"], d["kind"] if d["kind"] == "row" else d["column"]) for d in found)
    return {"out": out, "count": len(found), "rows": sum(1 for d in found if d["kind"] == "row"),
            "values": sum(1 for d in found if d["kind"] == "value"),
            "groups": [{"sheet": sh, "what": w, "count": n} for (sh, w), n in by.most_common(12)]}


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
