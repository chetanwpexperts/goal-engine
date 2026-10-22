"""Read a workbook as tables of real values, so the agent can answer questions about it. No AI.

Formula cells: Excel saves the last result next to each formula; files written by other tools often don't. When the
saved result is missing, a small evaluator works it out (arithmetic, comparisons, IF, SUM/AVERAGE/MIN/MAX/COUNT,
ROUND, ABS, IFERROR, AND/OR/NOT, INDEX/MATCH, cell and range references across sheets). Anything it can't do stays empty.
"""

import re
from datetime import date, datetime

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter

from app import office

MAX_ROWS = 500_000          # per sheet, for questions; bigger sheets are read up to here and the answer says so


class Table:
    def __init__(self, name, header, rows, header_row, truncated=False):
        self.name, self.header, self.rows, self.header_row, self.truncated = name, header, rows, header_row, truncated

    def col(self, i):
        return [r[i] if i < len(r) else None for r in self.rows]

    def kind(self, i) -> str:
        vals = [v for v in self.col(i)[:5000] if v not in (None, "")]
        if not vals:
            return "empty"
        nums = sum(1 for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool))
        dates = sum(1 for v in vals if isinstance(v, (datetime, date)))
        if dates >= 0.8 * len(vals):
            return "date"
        if nums >= 0.8 * len(vals):
            return "number"
        return "text"

    def ref(self, i, absolute=True) -> str:
        """Excel range of column i, e.g. 'Sales Data'!$K$2:$K$61"""
        c = get_column_letter(i + 1)
        first, last = self.header_row + 1, self.header_row + len(self.rows)
        q = f"'{self.name}'!" if re.search(r"\W", self.name) else f"{self.name}!"
        return f"{q}${c}${first}:${c}${last}" if absolute else f"{q}{c}{first}:{c}{last}"


def load_tables(path: str) -> list[Table]:
    if office.kind_of(path) == "csv":
        rows = list(office._csv_rows(path))
        header = [str(h).strip() for h in rows[0]] if rows else []
        data = [[office.text_number(v) if office.text_number(v) is not None else v for v in r] for r in rows[1:MAX_ROWS + 1]]
        return [Table("CSV", header, data, 1, len(rows) - 1 > MAX_ROWS)]
    big = __import__("os").path.getsize(path) > office.FULL_MODE_MAX
    wb_v = load_workbook(path, read_only=True, data_only=True)
    wb_f = None if big else load_workbook(path, read_only=True, data_only=False)
    raw = {}
    for ws in wb_v.worksheets:
        if ws.title in (office.CHANGES_SHEET, "Duplicates (Goal Engine)") or ws.title.startswith("Answer "):
            continue
        vals = [list(r) for r, _ in zip(ws.iter_rows(values_only=True), range(MAX_ROWS + 50))]
        forms = [list(r) for r, _ in zip(wb_f[ws.title].iter_rows(values_only=True), range(MAX_ROWS + 50))] if wb_f else None
        raw[ws.title] = (vals, forms)
    wb_v.close()
    if wb_f:
        wb_f.close()
    ev = _Evaluator(raw)
    tables = []
    for name, (vals, forms) in raw.items():
        h = next((i for i, r in enumerate(vals[:30]) if sum(1 for v in r if v not in (None, "")) >= 2), None)
        if h is None:
            continue
        header = [str(v).strip() if v is not None else "" for v in vals[h]]
        rows = []
        for r_i in range(h + 1, len(vals)):
            row = list(vals[r_i])
            if forms:
                for c_i, v in enumerate(row):
                    f = forms[r_i][c_i] if c_i < len(forms[r_i]) else None
                    if v is None and isinstance(f, str) and f.startswith("="):
                        row[c_i] = ev.cell(name, r_i, c_i)
            if any(v not in (None, "") for v in row):
                rows.append([office.text_number(v) if office.text_number(v) is not None else v for v in row])
        tables.append(Table(name, header, rows[:MAX_ROWS], h + 1, len(rows) > MAX_ROWS))
    return tables


# ------------------------------------------------------------------ a small, safe formula evaluator
_REF = r"(?:(?:'([^']+)'|([A-Za-z_][\w.]*))!)?\$?([A-Z]{1,3})\$?(\d+)"
_RANGE = re.compile(_REF + r":\$?([A-Z]{1,3})\$?(\d+)")
_CELL = re.compile(r"(?<![\w.\"])" + _REF + r"(?![\w(])")
_FUNCS = {"IF", "SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTA", "ROUND", "ABS", "IFERROR", "AND", "OR", "NOT", "INDEX", "MATCH"}


class _Range(list):
    pass


class _Evaluator:
    def __init__(self, raw):
        self.raw, self.memo, self.busy = raw, {}, set()

    def _get(self, sheet, r, c):
        vals, forms = self.raw.get(sheet, ([], None))
        if r >= len(vals) or c >= len(vals[r]):
            return None
        v = vals[r][c]
        f = forms[r][c] if forms and r < len(forms) and c < len(forms[r]) else None
        if v is None and isinstance(f, str) and f.startswith("="):
            return self.cell(sheet, r, c)
        return v

    def cell(self, sheet, r, c):
        key = (sheet, r, c)
        if key in self.memo:
            return self.memo[key]
        if key in self.busy:
            return None                                   # circular reference
        self.busy.add(key)
        try:
            f = self.raw[sheet][1][r][c]
            val = self._eval(f[1:], sheet)
        except Exception:  # noqa: BLE001 - unsupported formula: leave it empty
            val = None
        finally:
            self.busy.discard(key)
        self.memo[key] = val
        return val

    def _eval(self, expr, sheet):
        if "&" in expr or "{" in expr:
            return None
        names = {}

        def rng(m):
            sh = m.group(1) or m.group(2) or sheet
            c1, r1, c2, r2 = column_index_from_string(m.group(3)) - 1, int(m.group(4)) - 1, column_index_from_string(m.group(5)) - 1, int(m.group(6)) - 1
            key = f"_r{len(names)}"
            names[key] = _Range(self._get(sh, r, c) for r in range(r1, min(r2, r1 + 200000) + 1) for c in range(c1, c2 + 1))
            return key

        def cel(m):
            sh = m.group(1) or m.group(2) or sheet
            key = f"_c{len(names)}"
            names[key] = self._get(sh, int(m.group(4)) - 1, column_index_from_string(m.group(3)) - 1)
            return key

        strings = []
        expr = re.sub(r'"[^"]*"', lambda m: (strings.append(m.group(0)), f"_s{len(strings) - 1}")[1], expr)
        expr = _RANGE.sub(rng, expr)
        expr = _CELL.sub(cel, expr)
        for fn in set(re.findall(r"([A-Za-z]+)\s*\(", expr)):
            if fn.upper() not in _FUNCS:
                return None
            expr = re.sub(rf"\b{fn}\s*\(", f"_{fn.upper()}(", expr)
        expr = expr.replace("<>", "!=").replace(">=", "\x01").replace("<=", "\x02")
        expr = re.sub(r"(?<![=!<>])=(?!=)", "==", expr).replace("\x01", ">=").replace("\x02", "<=")
        expr = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", expr).replace("^", "**")
        for i, s in enumerate(strings):
            expr = expr.replace(f"_s{i}", s)
        if re.search(r"[A-Za-z_]\w*", re.sub(r"\b(_\w+)\b", "", re.sub(r'"[^"]*"', "", expr))):
            return None                                   # names we don't know
        env = {k: (0 if v is None else v) if not isinstance(v, _Range) else v for k, v in names.items()}
        env.update(_IF=lambda c, a=0, b=False: a if c else b, _SUM=_agg(sum), _AVERAGE=_agg(lambda x: sum(x) / len(x) if x else 0),
                   _MIN=_agg(lambda x: min(x) if x else 0), _MAX=_agg(lambda x: max(x) if x else 0), _COUNT=_agg(len),
                   _COUNTA=lambda *a: sum(1 for v in _flat(a) if v not in (None, "")), _ROUND=lambda x, n=0: round(x, int(n)),
                   _ABS=abs, _IFERROR=lambda a, b: a, _AND=lambda *a: all(_flat(a)), _OR=lambda *a: any(_flat(a)), _NOT=lambda a: not a,
                   _INDEX=lambda r, n, m=1: list(r)[int(n) - 1], _MATCH=_match)
        out = eval(compile(expr, "<formula>", "eval"), {"__builtins__": {}}, env)
        return out if not isinstance(out, _Range) else None


def _flat(args):
    for a in args:
        if isinstance(a, (list, tuple)):
            yield from a
        else:
            yield a


def _agg(fn):
    return lambda *a: fn([v for v in _flat(a) if isinstance(v, (int, float)) and not isinstance(v, bool)])


def _match(v, rng, kind=1):
    lst = list(rng)
    for i, x in enumerate(lst):
        if x == v or (isinstance(x, str) and isinstance(v, str) and x.strip().lower() == v.strip().lower()):
            return i + 1
    raise ValueError("not found")
