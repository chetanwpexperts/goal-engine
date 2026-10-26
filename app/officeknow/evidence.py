"""The facts in a person's own workbook that prove (or rule out) each cause in the Excel knowledge map.

Every fact is checked on the file itself and carries its proof: the cells, and where it can be worked out, how much
it changes the answer ("the total is 4,500 short"). A fact that can't be checked (file too big, not a workbook) is
None, so the reasoning says "not checked" instead of guessing.
"""

import os
import re
from collections import defaultdict
from datetime import date, datetime

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries

from app.office import CHANGES_SHEET, FULL_MODE_MAX, clean_spaces, has_bad_spaces, kind_of, text_number

MAX_CELLS = 12
EVIDENCE_MAX = 8 * 1024 * 1024      # bigger files: the formula checks run as a job (not yet), so they say "not checked"
SKIP = {CHANGES_SHEET, "Duplicates (Goal Engine)"}
REF = r"(?:(?:'[^']+'|[A-Za-z0-9_.]+)!)?\$?[A-Z]{1,3}\$?\d*(?::\$?[A-Z]{1,3}\$?\d*)?"
DATE_TEXT = re.compile(r"^\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[ \-][A-Za-z]{3,9}[ \-,]+\d{2,4})\s*$")


# ------------------------------------------------------------------ reading formulas
def calls(formula: str, name: str) -> list[list[str]]:
    """Every NAME(...) in the formula, as its top-level arguments."""
    out, up = [], formula.upper()
    for m in re.finditer(r"(?<![A-Z0-9_.])" + name + r"\(", up):
        i, depth, args, cur, q = m.end(), 1, [], "", False
        while i < len(formula) and depth:
            ch = formula[i]
            if ch == '"':
                q = not q
            elif not q and ch == "(":
                depth += 1
            elif not q and ch == ")":
                depth -= 1
                if not depth:
                    break
            elif not q and ch == "," and depth == 1:
                args.append(cur.strip())
                cur = ""
                i += 1
                continue
            cur += ch
            i += 1
        args.append(cur.strip())
        out.append(args)
    return out


def _sheet_and_range(ref: str, here: str):
    ref = ref.replace("$", "").strip()
    sheet = here
    if "!" in ref:
        sheet, ref = ref.rsplit("!", 1)
        sheet = sheet.strip("'")
    if not re.fullmatch(r"[A-Z]{1,3}\d*(?::[A-Z]{1,3}\d*)?", ref):
        return None
    try:
        c1, r1, c2, r2 = range_boundaries(ref if ":" in ref else f"{ref}:{ref}")
    except ValueError:
        return None
    return sheet, c1, r1 or 1, c2, r2 or 10 ** 7


class Book:
    """Formulas and the values Excel saved, side by side."""

    def __init__(self, path: str):
        self.f = load_workbook(path, read_only=False, data_only=False)
        self.v = load_workbook(path, read_only=False, data_only=True)
        self.names = [n for n in self.f.sheetnames if n not in SKIP]

    def value(self, sheet, col, row):
        try:
            return self.v[sheet].cell(row=row, column=col).value
        except KeyError:
            return None

    def cells(self, sheet, c1, r1, c2, r2):
        if sheet not in self.f.sheetnames:
            return
        ws = self.v[sheet]
        r2 = min(r2, ws.max_row)
        c2 = min(c2, ws.max_column)
        for row in ws.iter_rows(min_row=r1, max_row=r2, min_col=c1, max_col=c2):
            for cell in row:
                yield cell


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _amount(v):
    """A number in the data, also one saved as text (it is still part of the column's data)."""
    if _num(v):
        return v
    return text_number(v) if isinstance(v, str) else None


def _fmt(n) -> str:
    n = round(n, 2)
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,.2f}"


# ------------------------------------------------------------------ the checks
def _sums(book: Book):
    """Every SUM/AVERAGE/SUBTOTAL over a range: what it covers and what it leaves out."""
    for sheet in book.names:
        for row in book.f[sheet].iter_rows():
            for cell in row:
                v = cell.value
                if not (isinstance(v, str) and v.startswith("=")):
                    continue
                for fn in ("SUM", "AVERAGE", "SUBTOTAL", "SUMIF", "SUMIFS"):
                    for args in calls(v, fn):
                        if fn == "SUBTOTAL":
                            refs = args[1:]
                        elif fn == "SUMIF":
                            refs = args[2:3] if len(args) >= 3 else args[:1]
                        elif fn == "SUMIFS":
                            refs = args[:1]
                        else:
                            refs = args
                        for a in refs:
                            if re.fullmatch(REF, a.strip()):
                                rng = _sheet_and_range(a, sheet)
                                if rng:
                                    yield sheet, cell, fn, rng


def check_text_numbers_in_totals(book: Book, short: dict | None = None) -> dict:
    """Text numbers inside a total's range, or in the rows its range should have covered (short ranges)."""
    cells, impact, totals = [], 0.0, set()
    ranges = list(_sums(book))
    for h in (short or {}).get("all", []):
        ranges.append((h["cell"].rsplit("!", 1)[0], _Cell(h["cell"]), "SUM", h["intended"]))
    for sheet, cell, fn, (s, c1, r1, c2, r2) in ranges:
        for c in book.cells(s, c1, r1, c2, r2):
            n = text_number(c.value)
            if n is not None:
                key = f"{s}!{c.coordinate}"
                if key not in {x["cell"] for x in cells}:
                    cells.append({"cell": key, "value": c.value})
                    impact += n
                totals.add(f"{sheet}!{cell.coordinate}")
    return {"seen": bool(cells), "count": len(cells), "cells": cells[:MAX_CELLS], "impact": impact,
            "impact_text": _fmt(impact), "totals": sorted(totals)[:6], "weight": len(cells)}


class _Cell:
    def __init__(self, ref):
        self.coordinate = ref.rsplit("!", 1)[1]


def check_short_ranges(book: Book) -> dict:
    """=SUM(B2:B50) while the numbers in B carry on below row 50 (or start above row 2)."""
    hits = []
    for sheet, cell, fn, (s, c1, r1, c2, r2) in _sums(book):
        if c1 != c2 or r2 >= 10 ** 7 or s not in book.f.sheetnames:
            continue
        ws = book.v[s]
        left, missed, r = [], 0.0, r2 + 1
        while r <= ws.max_row:
            if s == sheet and r == cell.row and c1 == cell.column:
                break
            x = _amount(ws.cell(row=r, column=c1).value)
            if x is None:
                break
            left.append(r)
            missed += x
            r += 1
        r = r1 - 1
        while r >= 2:
            x = _amount(ws.cell(row=r, column=c1).value)
            if x is None or (s == sheet and r == cell.row and c1 == cell.column):
                break
            left.append(r)
            missed += x
            r -= 1
        if left:
            col = get_column_letter(c1)
            rows = sorted(left)
            hits.append({"cell": f"{sheet}!{cell.coordinate}", "formula": cell.value, "column": col,
                         "range": f"{col}{r1}:{col}{r2}", "left_out": f"{col}{rows[0]}:{col}{rows[-1]}",
                         "rows": len(rows), "impact": missed, "impact_text": _fmt(missed),
                         "intended": (s, c1, min(rows + [r1]), c1, max(rows + [r2])),
                         "fixed": _extend(cell.value, col, r1, r2, min(rows + [r1]), max(rows + [r2]))})
    impact = sum(h["impact"] for h in hits)
    return {"seen": bool(hits), "count": len(hits), "items": hits[:MAX_CELLS], "all": hits,
            "impact": impact, "impact_text": _fmt(impact), "weight": len(hits) * 3}


def _extend(formula: str, col: str, r1: int, r2: int, n1: int, n2: int) -> str:
    pat = re.compile(r"(\$?)" + col + r"(\$?)" + str(r1) + r":(\$?)" + col + r"(\$?)" + str(r2) + r"(?!\d)")
    return pat.sub(lambda m: f"{m.group(1)}{col}{m.group(2)}{n1}:{m.group(3)}{col}{m.group(4)}{n2}", formula, count=1)


def _lookups(book: Book):
    """(sheet, cell, kind, lookup value ref, table ref, exact?) for VLOOKUP / XLOOKUP / MATCH."""
    for sheet in book.names:
        for row in book.f[sheet].iter_rows():
            for cell in row:
                v = cell.value
                if not (isinstance(v, str) and v.startswith("=")):
                    continue
                for args in calls(v, "VLOOKUP") + calls(v, "HLOOKUP"):
                    if len(args) >= 3:
                        exact = len(args) >= 4 and args[3].strip().upper() in ("FALSE", "0")
                        yield sheet, cell, "VLOOKUP", args[0], args[1], exact
                for args in calls(v, "XLOOKUP"):
                    if len(args) >= 3:
                        yield sheet, cell, "XLOOKUP", args[0], args[1], len(args) < 5 or args[4].strip() in ("0", "")
                for args in calls(v, "MATCH"):
                    if len(args) >= 2:
                        exact = len(args) >= 3 and args[2].strip() == "0"
                        yield sheet, cell, "MATCH", args[0], args[1], exact


def _key(v):
    if isinstance(v, str):
        return clean_spaces(v).casefold()
    if _num(v):
        return float(v)
    return v


def check_lookups(book: Book) -> dict:
    """Why each lookup that shows #N/A (or uses approximate match) can't find its value."""
    out = {k: {"seen": False, "count": 0, "items": []} for k in ("approx", "spaces", "type", "missing")}
    for sheet, cell, kind, val_ref, table_ref, exact in _lookups(book):
        shown = book.value(sheet, cell.column, cell.row)
        if not exact:
            o = out["approx"]
            o["seen"] = True
            o["count"] += 1
            fixed = cell.value
            if kind == "VLOOKUP":
                fixed = _exact_vlookup(cell.value)
            elif kind == "MATCH":
                fixed = _exact_match(cell.value)
            item = {"cell": f"{sheet}!{cell.coordinate}", "formula": cell.value, "fixed": fixed}
            o.setdefault("all", []).append(item)
            if len(o["items"]) < MAX_CELLS:
                o["items"].append(item)
        if shown != "#N/A":
            continue
        vr = _sheet_and_range(val_ref, sheet)
        tr = _sheet_and_range(table_ref, sheet)
        if not vr or not tr:
            continue
        want = book.value(vr[0], vr[1], vr[2])
        if want in (None, ""):
            continue
        s, c1, r1, c2, r2 = tr
        col = [c.value for c in book.cells(s, c1, r1, c1, r2)]
        exact_vals = {v for v in col if v is not None}
        if want in exact_vals:
            continue
        loose = {_key(v): v for v in col if v is not None}
        k = _key(want)
        tn = text_number(want) if isinstance(want, str) else None
        item = {"cell": f"{sheet}!{cell.coordinate}", "looks_for": want, "in": f"{s}!{get_column_letter(c1)}"}
        if isinstance(want, str) and k in loose and (has_bad_spaces(want) or has_bad_spaces(loose[k]) or want != loose[k]):
            reason = "spaces"
            item["there"] = loose[k]
        elif tn is not None and float(tn) in loose:
            reason = "type"
            item["there"] = loose[float(tn)]
        elif _num(want) and str(int(want) if float(want).is_integer() else want) in {str(x).strip() for x in col if isinstance(x, str)}:
            reason = "type"
            item["there"] = str(want)
        else:
            reason = "missing"
        o = out[reason]
        o["seen"] = True
        o["count"] += 1
        if len(o["items"]) < MAX_CELLS:
            o["items"].append(item)
    for o in out.values():
        o["weight"] = o["count"]
    return out


def _exact_vlookup(formula: str) -> str:
    def fix(args):
        return args[:3] + ["FALSE"] + args[4:]
    return _rewrite(formula, "VLOOKUP", fix)


def _exact_match(formula: str) -> str:
    def fix(args):
        return args[:2] + ["0"] + args[3:]
    return _rewrite(formula, "MATCH", fix)


def _rewrite(formula: str, name: str, fn) -> str:
    """Rewrite the first call of NAME whose arguments need it."""
    up = formula.upper()
    m = re.search(r"(?<![A-Z0-9_.])" + name + r"\(", up)
    if not m:
        return formula
    args = calls(formula[m.start():], name)[0]
    i, depth, q = m.end(), 1, False
    while i < len(formula) and depth:
        ch = formula[i]
        if ch == '"':
            q = not q
        elif not q and ch == "(":
            depth += 1
        elif not q and ch == ")":
            depth -= 1
        i += 1
    return formula[:m.start()] + formula[m.start():m.end()] + ",".join(fn(args)) + ")" + formula[i:]


def check_calc_mode(book: Book) -> dict:
    mode = getattr(getattr(book.f, "calculation", None), "calcMode", None) or "auto"
    return {"seen": mode == "manual", "mode": mode, "weight": 3}


def check_errors(report: dict) -> dict:
    out = {}
    for code, key in (("#DIV/0!", "div0"), ("#VALUE!", "value_err"), ("#NAME?", "name_err"), ("#N/A", "na")):
        cells = [f"{s['name']}!{c}" for s in report.get("sheets", []) for c in s.get("errors", {}).get(code, {}).get("cells", [])]
        n = sum(s.get("errors", {}).get(code, {}).get("count", 0) for s in report.get("sheets", []))
        out[key] = {"seen": bool(n), "count": n, "cells": cells[:MAX_CELLS], "weight": n}
    return out


def check_typed_over_formulas(book: Book) -> dict:
    """A column of formulas with a few typed numbers in it: those rows don't update."""
    items = []
    for sheet in book.names:
        ws = book.f[sheet]
        cols = defaultdict(lambda: {"f": 0, "typed": []})
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                v = cell.value
                if isinstance(v, str) and v.startswith("="):
                    cols[cell.column]["f"] += 1
                elif _num(v):
                    cols[cell.column]["typed"].append(cell.coordinate)
        for c, x in cols.items():
            if x["f"] >= 4 and x["typed"] and len(x["typed"]) <= max(2, x["f"] // 5):
                items += [f"{sheet}!{a}" for a in x["typed"]]
    return {"seen": bool(items), "count": len(items), "cells": items[:MAX_CELLS], "all": items, "weight": len(items)}


def check_text_dates(book: Book) -> dict:
    items = []
    for sheet in book.names:
        ws = book.v[sheet]
        cols = defaultdict(lambda: {"d": 0, "t": []})
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                v = cell.value
                if isinstance(v, (datetime, date)):
                    cols[cell.column]["d"] += 1
                elif isinstance(v, str) and DATE_TEXT.match(v):
                    cols[cell.column]["t"].append(f"{sheet}!{cell.coordinate}")
        for c, x in cols.items():
            head = str(ws.cell(row=1, column=c).value or "").lower()
            if x["t"] and (x["d"] >= len(x["t"]) or "date" in head or "tarikh" in head):
                items += x["t"]
    return {"seen": bool(items), "count": len(items), "cells": items[:MAX_CELLS], "weight": len(items)}


def check_hidden_in_totals(book: Book) -> dict:
    hits = []
    for sheet, cell, fn, (s, c1, r1, c2, r2) in _sums(book):
        if fn == "SUBTOTAL" or s not in book.f.sheetnames:
            continue
        ws = book.f[s]
        hidden = [r for r, d in ws.row_dimensions.items() if d.hidden and r1 <= r <= min(r2, ws.max_row)]
        if hidden:
            hits.append({"cell": f"{sheet}!{cell.coordinate}", "hidden_rows": len(hidden), "first": min(hidden)})
    return {"seen": bool(hits), "count": len(hits), "items": hits[:MAX_CELLS], "weight": len(hits)}


def _from_report(report: dict, key: str, cells_key: str) -> dict:
    n = report.get("summary", {}).get(key, 0)
    cells = [f"{s['name']}!{c}" for s in report.get("sheets", []) for c in s.get(cells_key, [])]
    return {"seen": bool(n), "count": n, "cells": cells[:MAX_CELLS], "weight": n}


def collect(path: str, report: dict) -> dict:
    """fact id -> fact (or None when it couldn't be checked on this file)."""
    facts = {
        "spaces": _from_report(report, "spaces", "space_cells"),
        "text_numbers": _from_report(report, "text_numbers", "text_number_cells"),
        "broken_refs": _from_report(report, "broken_refs", "broken_refs"),
        "odd_formulas": _from_report(report, "odd_formulas", "odd_formulas"),
        "duplicate_rows": {"seen": bool(report.get("summary", {}).get("duplicate_rows")),
                           "count": report.get("summary", {}).get("duplicate_rows", 0),
                           "weight": report.get("summary", {}).get("duplicate_rows", 0)},
        **check_errors(report),
    }
    formula_facts = ("text_in_totals", "short_range", "approx_lookup", "lookup_spaces", "lookup_type", "lookup_missing",
                     "manual_calc", "typed_over_formula", "text_dates", "hidden_in_totals")
    k = kind_of(path) if path and os.path.exists(path) else ""
    if k not in ("xlsx", "xlsm") or os.path.getsize(path) > min(FULL_MODE_MAX, EVIDENCE_MAX):
        no_formulas = k == "csv"                     # a CSV has no formulas: those causes are ruled out, not unknown
        for f in formula_facts:
            facts[f] = {"seen": False, "count": 0, "weight": 0} if no_formulas else None
        return facts
    try:
        book = Book(path)
    except Exception:  # noqa: BLE001 - an unreadable file: say "not checked", never guess
        for f in formula_facts:
            facts[f] = None
        return facts
    facts["short_range"] = check_short_ranges(book)
    facts["text_in_totals"] = check_text_numbers_in_totals(book, facts["short_range"])
    lk = check_lookups(book)
    facts["approx_lookup"], facts["lookup_spaces"] = lk["approx"], lk["spaces"]
    facts["lookup_type"], facts["lookup_missing"] = lk["type"], lk["missing"]
    facts["manual_calc"] = check_calc_mode(book)
    facts["typed_over_formula"] = check_typed_over_formulas(book)
    facts["text_dates"] = check_text_dates(book)
    facts["hidden_in_totals"] = check_hidden_in_totals(book)
    return facts
