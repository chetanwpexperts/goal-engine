"""Everyday Excel orders the agent carries out on the file itself (a new version, so Undo works):
bold header, freeze the top row, fit column widths, filter buttons, remove blank rows, delete / rename a column,
serial numbers, split text into columns, a dropdown list, find & replace, date / money / decimal formats,
UPPER / Proper / lower case, one sheet per value of a column.

plan_edit(text, tables) -> a plan {"job": "edit", "op": ..., "table": ..., ...} or None (not an order).
run_edit(plan, tables, wb, hi, n) -> {"say", "sheet", "changes"}.
English / Hinglish / Hindi. No AI."""

import re
from datetime import date, datetime

from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

DO = r"(?:make|set|add|put|insert|create|apply|remove|delete|drop|clear|freeze|lock|split|separate|break|rename|change|convert|replace|" \
     r"fit|resize|format|round|turn|give|karo|kar do|kardo|kar dijiye|karein|hatao|hata do|hatado|lagao|laga do|lagado|banao|bana do|" \
     r"badlo|badal do|do|de do|dedo|daalo|daal do|tod do|alag karo)"

ORDERS = [
    ("bold_header", re.compile(r"\b(header|heading|headings|top row|first row|title row|pehli row|upar wali row)\b.{0,25}\b(bold|mota|motaa)\b|"
                               r"\b(bold)\b.{0,25}\b(header|heading|headings|top row|first row|pehli row)\b", re.I)),
    ("freeze", re.compile(r"\b(freeze|lock|fix|jama)\b.{0,25}\b(top row|first row|header|heading|row 1|pehli row|first column|column a|pehla column)\b|"
                          r"\b(header|heading|top row|first row|pehli row)\b.{0,25}\b(freeze|fixed|fix karo|lock|jama do)\b|\bfreeze panes?\b", re.I)),
    ("autofit", re.compile(r"\b(auto ?fit|fit|adjust|resize|theek|set)\b.{0,25}\b(column )?(widths?|chaudai|size of columns|columns? width)\b|"
                           r"\bcolumns? (ki )?(width|chaudai)\b.{0,20}\b(theek|fit|adjust|set|karo)\b|\bautofit\b", re.I)),
    ("filter", re.compile(r"\b(add|put|apply|lagao|laga do|enable|turn on|daalo)\b.{0,20}\b(filters?|filter buttons?|auto ?filter)\b|"
                          r"\b(filters?|filter buttons?|auto ?filter)\b.{0,15}\b(lagao|laga do|daalo|add karo|on karo)\b", re.I)),
    ("remove_blank_rows", re.compile(r"\b(remove|delete|hatao|hata do|clear|drop|nikalo|nikal do)\b.{0,25}\b(blank|empty|khaali|khali)\s*(rows?|lines?|lineen)\b|"
                                     r"\b(blank|empty|khaali|khali)\s*(rows?|lines?)\b.{0,20}\b(hatao|hata do|remove|delete|nikalo)\b", re.I)),
    ("serial", re.compile(r"\b(add|insert|put|lagao|daalo|banao|do)\b.{0,25}\b(serial (no|number|numbers)s?|s\.?\s?no\.?|sr\.?\s?no\.?|sequence numbers?|kram sankhya|numbering)\b|"
                          r"\b(serial (no|number|numbers)s?|s\.?\s?no\.?|sr\.?\s?no\.?)\b.{0,20}\b(lagao|daalo|add|do|banao)\b", re.I)),
    ("split_sheets", re.compile(r"\b(split|separate|divide|break)\b.{0,40}\b(by|per|each|wise)\b.{0,30}\b(sheets|tabs|files)\b|\b(split|separate|divide|break|alag)\b.{0,40}\b(sheet|sheets|tabs?|files?)\b.{0,40}\b(by|per|each|for every|har|wise|ke hisaab)\b|"
                                r"\b(one|alag|separate|ek)\s+(sheet|tab|file)\s+(for|per|har)\s+(each|every|har)?\b|\b(\w+)\s*wise\s+(alag|separate)\s+(sheets?|tabs?|files?)\b", re.I)),
    ("split", re.compile(r"\b(split|separate|break|tod|alag)\b.{0,40}\b(into|in|mein|me)\b.{0,30}\b(first|last|columns?|parts?|alag)\b|"
                         r"\btext to columns?\b|\b(first|pehla)\s+(and|aur)\s+(last|second|doosra)\s+name\b.{0,30}\b(alag|split|separate)\b", re.I)),
    ("dropdown", re.compile(r"\b(dropdown|drop-down|drop down|list box|data validation|pick ?list)\b", re.I)),
    ("rename", re.compile(r"\brename\b|\b(column|heading|header)\b.{0,40}\b(naam|name)\b.{0,30}\b(badlo|badal do|change|karo|kar do|rakho|rakh do)\b|"
                          r"\bchange\b.{0,30}\b(column|heading|header)\b.{0,10}\bname\b", re.I)),
    ("delete_column", re.compile(r"\b(delete|remove|drop|hatao|hata do|hatado|nikal do|nikalo)\b.{0,30}\b(column|col|columns)\b|"
                                 r"\b(column|col)\b.{0,30}\b(delete|remove|hatao|hata do|hatado|nikal do)\b", re.I)),
    ("date_format", re.compile(r"\b(date|dates|tarikh)\b.{0,30}\b(format|dd|mm|yyyy|dd-mm|dd/mm|mm/dd)\b|\b(format)\b.{0,20}\b(date|dates)\b", re.I)),
    ("money_format", re.compile(r"\b(rupees?|rs|₹|inr|currency|indian|lakhs?|commas?)\b.{0,15}\b(format|formatting|style)\b|"
                                r"\b(format|formatting)\b.{0,25}\b(rupees?|₹|currency|lakhs?|indian|commas?)\b", re.I)),
    ("round", re.compile(r"\b(round( off)?|decimals?|decimal places?|dashamlav)\b.{0,25}\b(\d|two|one|zero|no|karo|kar do|off)\b|\bround off\b", re.I)),
    ("case", re.compile(r"\b(upper ?case|capital letters?|all caps|proper case|title case|first letter capital|lower ?case|small letters?|"
                        r"bade akshar|chhote akshar)\b", re.I)),
    ("replace", re.compile(r"\breplace\b.{0,80}\bwith\b|\bchange\s+(?:all\s+(?:the\s+)?\S.{0,38}|['\"][^'\"]{1,40}['\"])\s+to\s+\S|"
                           r"\b(\S+)\s+ko\s+(\S+)\s+(se|mein|me)\s+(badlo|badal do|replace karo|kar do)\b|\bfind and replace\b", re.I)),
]

MACRO_RE = re.compile(r"\b(macros?|vba|visual basic)\b", re.I)
PIVOT_RE = re.compile(r"\bpivot\b", re.I)
QUESTION_RE = re.compile(r"^\s*(how|what|which|who|whose|is|are|was|were|does|did|kitne|kitni|kitna|kya|kaun|kaunse|konse|where|kahan|tell me|batao)\b", re.I)


# Indian grouping (1,23,45,678.00): Excel has no built-in code for it, so three ranges
INDIAN = '[>=10000000]"₹"##\\,##\\,##\\,##0.00;[>=100000]"₹"##\\,##\\,##0.00;"₹"##,##0.00'


class NotAnEdit(Exception):
    pass


def _ask():
    from app.office import ask
    return ask


WHOLE_FILE = ("bold_header", "freeze", "autofit", "filter", "remove_blank_rows", "replace")


def _named(text, tables):
    a = _ask()
    return a.named_sheet(text, tables)


def _target(text, tables):
    a = _ask()
    named = a.named_sheet(text, tables)
    if named is not None:
        return named
    # no sheet named: the sheet that has the column they named ('make region uppercase')
    best, best_sc = None, 0.0
    for t in tables:
        if not t.rows:
            continue
        plain = re.sub(r"\b(columns?|cols?|heading|header|sheets?)\b", " ", text or "", flags=re.I)
        sc = max((x for _, x in a.columns_in(plain, t)), default=0)
        if sc > best_sc:
            best, best_sc = t, sc
    if best is not None and best_sc >= 1:
        return best
    picked = a.pick_sheet(text, tables)
    if picked is not None:
        return picked
    data = [t for t in tables if t.rows]
    return max(data, key=lambda t: len(t.rows)) if data else (tables[0] if tables else None)


def _all_sheets(text):
    return bool(re.search(r"\b(all|every|har|saari|saare|sabhi|sab)\s+(sheets?|tabs?)\b|\b(whole|poori|puri|entire)\s+(file|workbook)\b", text or "", re.I))


def _col(text, t, kinds=None, exclude=(), need=0.5):
    a = _ask()
    q = re.findall(r"['\"“”‘’]([^'\"“”‘’]{1,60})['\"“”‘’]", text or "")
    for name in q:
        for i, h in enumerate(t.header):
            if h and h.strip().lower() == name.strip().lower() and i not in exclude:
                return i
    plain = re.sub(r"\b(columns?|cols?|heading|header|the|wala|wali|ka|ki|ke)\b", " ", text or "", flags=re.I)
    cands = [(sc, i) for i, sc in a.columns_in(plain, t, kinds) if i not in exclude]
    return max(cands)[1] if cands and max(cands)[0] >= need else None


def is_order(text: str) -> bool:
    low = text or ""
    if QUESTION_RE.search(low):
        return False
    return any(rx.search(low) for op, rx in ORDERS)


def _splittable(said, t, col):
    """The column whose values really have two parts ('Full Name', not 'Last Name')."""
    a = _ask()

    def parts(i):
        vals = [v for v in t.col(i)[:500] if isinstance(v, str) and v.strip()]
        return sum(1 for v in vals if len(v.split()) > 1) / len(vals) if vals else 0

    if col is not None and parts(col) >= 0.5:
        return col
    cands = [(sc + parts(i), i) for i, sc in a.columns_in(said, t, {"text"}) if parts(i) >= 0.5]
    if cands:
        return max(cands)[1]
    named = [i for i, h in enumerate(t.header) if h and re.search(r"name|naam", h, re.I) and parts(i) >= 0.5]
    if named:
        return named[0]
    _no_column(said, t, "split")


def _no_column(text, t, what):
    a = _ask()
    cols = ", ".join(h for h in t.header if h)[:300]
    raise a.NeedMore(f"{t.name} mein ye column nahi mila. Columns: {cols}. Kaunsa {what}?",
                     f"I couldn't find that column in {t.name}. It has: {cols}. Which one should I {what}?",
                     [h for h in t.header if h][:8])


def plan_edit(text: str, tables) -> dict | None:
    low = text or ""
    if QUESTION_RE.search(low) or (re.search(r"\?\s*$", low) and not re.search(rf"\b{DO}\b", low, re.I)):
        return None                                          # a question about the file, not an order
    for op, rx in ORDERS:
        if not rx.search(low):
            continue
        t = _target(text, tables)
        if t is None:
            return None
        plan = {"job": "edit", "op": op, "table": t.name,
                "all": _all_sheets(text) or (op in WHOLE_FILE and _named(text, tables) is None and len([x for x in tables if x.rows]) > 1)}
        try:
            plan.update(_details(op, text, t, tables))
        except NotAnEdit:
            continue
        return plan
    return None


def _details(op, text, t, tables) -> dict:
    a = _ask()
    if op == "freeze":
        col = re.search(r"\b(first column|column a|pehla column)\b", text, re.I)
        both = re.search(r"\b(row and column|row aur column|both)\b", text, re.I)
        return {"cell": "B2" if both else ("B1" if col else "A2")}
    if op == "filter" and re.search(r"\b(where|jahan|jinka|jinki|only|sirf|whose|for)\b", text, re.I):
        raise NotAnEdit                                      # "filter rows where...": those rows, not filter buttons
    if op in ("delete_column", "case", "round", "money_format", "date_format", "dropdown", "split_sheets", "split"):
        kinds = {"date_format": {"date"}, "money_format": {"number"}, "round": {"number"}}.get(op)
        said = re.split(r"\b(into|in to|mein|me|by|on|se)\b", text, maxsplit=1, flags=re.I)[0] if op == "split" else text
        col = _col(said, t, kinds, need=1 if op == "delete_column" else 0.5)
        if op == "split":
            col = _splittable(said, t, col)
        if op == "date_format" and col is None:
            col = next((i for i in range(len(t.header)) if t.kind(i) == "date"), None)
        if op in ("money_format", "round") and col is None:
            from app.office.ask import main_amount
            col = main_amount(t)[0]
        if op == "split_sheets":
            g = re.search(r"\b(?:by|per|each|har)\s+([\w ]{2,30}?)(?:\s+(?:wise|into|in|column|ke|$)|$)|\b(\w[\w ]{1,25}?)\s*wise\b", text, re.I)
            if g:
                col = _col(next(x for x in g.groups() if x), t, {"text"}) or col
        if col is None and op == "delete_column" and re.search(r"\b(delete|remove|drop)\b.{0,30}\bcolumn\b|\bcolumn\b.{0,30}\b(hatao|hata do|delete)", text, re.I):
            _no_column(text, t, "delete")
        if col is None and op not in ("date_format",):
            raise NotAnEdit
        out = {"col": col}
        if op == "money_format":
            out["rupee"] = not re.search(r"\bcommas?\b", text, re.I) or bool(re.search(r"rupee|₹|\brs\b|inr|currency", text, re.I))
        if op == "case":
            out["how"] = ("upper" if re.search(r"upper|capital|caps|bade", text, re.I) else
                          "lower" if re.search(r"lower|small|chhote", text, re.I) else "proper")
        if op == "round":
            m = re.search(r"\b(\d)\s*(decimal|dashamlav|places?|digits?)", text, re.I)
            out["places"] = int(m.group(1)) if m else (0 if re.search(r"\b(no|zero|whole|poore)\b", text, re.I) else 2)
        if op == "date_format":
            m = re.search(r"\b(dd[-/. ]mm[-/. ]yyyy|mm[-/. ]dd[-/. ]yyyy|yyyy[-/. ]mm[-/. ]dd|dd[-/. ]mmm[-/. ]yyyy|dd[-/. ]mm[-/. ]yy)\b", text, re.I)
            out["fmt"] = (m.group(1) if m else "dd-mm-yyyy").lower().replace("mmm", "mmm")
        if op == "dropdown":
            m = re.search(r"\b(?:with|values?|options?|:)\s+([^.?!]+)$", text, re.I)
            vals = [v.strip(" '\"") for v in re.split(r",|/|\bor\b|\band\b|\baur\b|\bya\b", m.group(1))] if m else []
            vals = [v for v in vals if v and len(v) <= 40 and not re.search(r"\bcolumn\b", v, re.I)]
            if not vals:
                vals = sorted({str(v).strip() for v in t.col(col)[:5000] if v not in (None, "")})[:50]
            out["values"] = vals[:50]
        if op == "split":
            sep = re.search(r"\b(?:by|on|at|se)\s+(comma|space|hyphen|dash|slash|['\"](.{1,3})['\"])", text, re.I)
            mp = {"comma": ",", "space": " ", "hyphen": "-", "dash": "-", "slash": "/"}
            out["sep"] = (sep.group(2) if sep and sep.group(2) else mp.get(sep.group(1).lower(), " ")) if sep else " "
            out["names"] = bool(re.search(r"\bname", t.header[col], re.I)) and out["sep"] == " "
        return out
    if op == "rename":
        m = re.search(r"\brename\s+(?:the\s+)?(?:column\s+)?['\"]?(.+?)['\"]?\s+(?:column\s+)?(?:to|as|into)\s+['\"]?(.+?)['\"]?\s*$", text, re.I) or \
            re.search(r"['\"]?([^'\"]+?)['\"]?\s+(?:column\s+)?(?:ka|ki)\s+(?:naam|name)\s+['\"]?(.+?)['\"]?\s+(?:karo|kar do|rakho|rakh do)\s*$", text, re.I) or \
            re.search(r"\bchange\s+(?:the\s+)?(?:column\s+)?(?:name\s+)?(?:of\s+)?['\"]?(.+?)['\"]?\s+(?:column\s+)?(?:name\s+)?to\s+['\"]?(.+?)['\"]?\s*$", text, re.I)
        if not m:
            raise NotAnEdit
        col = _col(f'"{m.group(1)}"', t) if any(h and h.strip().lower() == m.group(1).strip().lower() for h in t.header) else _col(m.group(1), t)
        if col is None:
            _no_column(text, t, "rename")
        return {"col": col, "new": m.group(2).strip()[:80]}
    if op == "replace":
        m = re.search(r"\breplace\s+(?:all\s+)?['\"]?(.+?)['\"]?\s+with\s+['\"]?(.*?)['\"]?(?:\s+in\s+(?:the\s+)?(.+?))?\s*$", text, re.I) or \
            re.search(r"\bchange\s+(?:all\s+)?['\"]?(.+?)['\"]?\s+to\s+['\"]?(.*?)['\"]?(?:\s+in\s+(?:the\s+)?(.+?))?\s*$", text, re.I) or \
            re.search(r"['\"]?(\S+?)['\"]?\s+ko\s+['\"]?(\S+?)['\"]?\s+(?:se|mein|me)\s+(?:badlo|badal do|replace karo|kar do)", text, re.I)
        if not m or not m.group(1):
            raise NotAnEdit
        within = m.group(3) if m.lastindex and m.lastindex >= 3 else None
        col = _col(within, t) if within else None
        return {"find": m.group(1), "repl": m.group(2) or "", "col": col}
    return {}


# ------------------------------------------------------------------ doing it
def _sheets(p, wb, tables=None):
    every = p.get("all") and not (p["op"] == "replace" and p.get("col") is not None)
    names = [n for n in wb.sheetnames if tables is None or (tables.get(n) is not None and tables[n].rows)] if every else [p["table"]]
    return [wb[n] for n in names if n in wb.sheetnames and not n.startswith("Answer ") and n not in ("Changes (Goal Engine)",)]


def _hdr(ws, t):
    return t.header_row if t is not None else 1


def _has_formulas(ws) -> bool:
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("="):
                return True
    return False


def run_edit(p, tables, wb, hi, n) -> dict:
    if wb is None:
        raise ValueError("needs the workbook")
    op = p["op"]
    t = tables.get(p["table"])
    done, notes = 0, []
    for ws in _sheets(p, wb, tables):
        tt = tables.get(ws.title) or t
        hr = _hdr(ws, tt)
        last = ws.max_row
        col = p.get("col")
        cidx = (col + 1) if col is not None else None
        if op == "bold_header":
            for c in ws[hr]:
                if c.value not in (None, ""):
                    c.font = Font(bold=True)
                    done += 1
        elif op == "freeze":
            cell = p.get("cell", "A2")
            if cell.endswith("2"):
                cell = f"{cell[0]}{hr + 1}"
            ws.freeze_panes = cell
            done += 1
        elif op == "autofit":
            for ci, column in enumerate(ws.iter_cols(min_row=1, max_row=min(last, 2000)), start=1):
                width = max((len(str(c.value)) for c in column if c.value not in (None, "")), default=6)
                ws.column_dimensions[get_column_letter(ci)].width = min(60, max(8, width + 2))
                done += 1
        elif op == "filter":
            ws.auto_filter.ref = f"A{hr}:{get_column_letter(ws.max_column)}{last}"
            done += 1
        elif op == "remove_blank_rows":
            if _has_formulas(ws):
                notes.append(ws.title)
                continue
            blanks = [r for r in range(last, hr, -1) if all(c.value in (None, "") or (isinstance(c.value, str) and not c.value.strip()) for c in ws[r])]
            for r in blanks:
                ws.delete_rows(r)
            done += len(blanks)
        elif op == "delete_column":
            if _has_formulas(ws):
                notes.append(ws.title)
                continue
            ws.delete_cols(cidx)
            done += 1
        elif op == "rename":
            ws.cell(hr, cidx).value = p["new"]
            done += 1
        elif op == "serial":
            if _has_formulas(ws):
                ci = ws.max_column + 1                      # inserting would shift formulas: add it at the end instead
                notes.append(ws.title)
            else:
                ws.insert_cols(1)
                ci = 1
            ws.cell(hr, ci).value = "S.No"
            ws.cell(hr, ci).font = Font(bold=True)
            k = 0
            for r in range(hr + 1, ws.max_row + 1):
                if any(c.value not in (None, "") for c in ws[r] if c.column != ci):
                    k += 1
                    ws.cell(r, ci).value = k
            done += k
        elif op == "split":
            sep = p.get("sep", " ")
            parts_n = 2 if p.get("names") else max((len(str(ws.cell(r, cidx).value).split(sep)) for r in range(hr + 1, last + 1)
                                                    if ws.cell(r, cidx).value not in (None, "")), default=2)
            parts_n = min(parts_n, 8)
            base = ws.max_column + 1
            heads = ["First Name", "Last Name"] if p.get("names") else [f"{ws.cell(hr, cidx).value} {k + 1}" for k in range(parts_n)]
            for k, h in enumerate(heads):
                ws.cell(hr, base + k).value = h
                ws.cell(hr, base + k).font = Font(bold=True)
            for r in range(hr + 1, last + 1):
                v = ws.cell(r, cidx).value
                if v in (None, ""):
                    continue
                s = str(v).strip()
                bits = s.split(sep, 1) if p.get("names") else s.split(sep)
                bits = [b.strip() for b in bits]
                for k in range(len(heads)):
                    ws.cell(r, base + k).value = bits[k] if k < len(bits) else None
                done += 1
        elif op == "dropdown":
            vals = [str(v).replace(",", " ") for v in p.get("values", [])]
            formula = '"' + ",".join(vals) + '"'
            if len(formula) > 255:                          # Excel's limit for a typed list: put the list on its own sheet
                lst = wb.create_sheet(f"List {ws.cell(hr, cidx).value}"[:31])
                for k, v in enumerate(vals, start=1):
                    lst.cell(k, 1).value = v
                formula = f"='{lst.title}'!$A$1:$A${len(vals)}"
            dv = DataValidation(type="list", formula1=formula, allow_blank=True, showDropDown=False)
            ws.add_data_validation(dv)
            letter = get_column_letter(cidx)
            dv.add(f"{letter}{hr + 1}:{letter}{max(last, hr) + 500}")
            done += 1
        elif op == "replace":
            f, rp = p["find"], p["repl"]
            for row in ws.iter_rows(min_row=hr + 1):
                for c in row:
                    if cidx is not None and c.column != cidx:
                        continue
                    v = c.value
                    if isinstance(v, str) and not v.startswith("=") and f.lower() in v.lower() and rp.lower() not in v.lower():
                        c.value = re.sub(re.escape(f), rp, v, flags=re.I)
                        done += 1
                    elif isinstance(v, (int, float)) and str(v) == f.strip():
                        c.value = rp
                        done += 1
        elif op == "date_format":
            fmt = p.get("fmt", "dd-mm-yyyy")
            cols = [cidx] if cidx else [c for c in range(1, ws.max_column + 1)]
            for r in range(hr + 1, last + 1):
                for ci in cols:
                    c = ws.cell(r, ci)
                    if isinstance(c.value, (datetime, date)):
                        c.number_format = fmt
                        done += 1
        elif op == "money_format":
            for r in range(hr + 1, last + 1):
                c = ws.cell(r, cidx)
                if isinstance(c.value, (int, float)) or (isinstance(c.value, str) and c.value.startswith("=")):
                    c.number_format = INDIAN.replace("₹", "₹" if p.get("rupee", True) else "")
                    done += 1
        elif op == "round":
            places = p.get("places", 2)
            for r in range(hr + 1, last + 1):
                c = ws.cell(r, cidx)
                if isinstance(c.value, str) and c.value.startswith("=") and not c.value.upper().startswith("=ROUND("):
                    c.value = f"=ROUND({c.value[1:]},{places})"
                    c.number_format = "0" if places == 0 else "0." + "0" * places
                    done += 1
                elif isinstance(c.value, float):
                    c.value = round(c.value, places)
                    c.number_format = "0" if places == 0 else "0." + "0" * places
                    done += 1
        elif op == "case":
            how = p["how"]
            for r in range(hr + 1, last + 1):
                c = ws.cell(r, cidx)
                if isinstance(c.value, str) and not c.value.startswith("="):
                    new = c.value.upper() if how == "upper" else c.value.lower() if how == "lower" else c.value.title()
                    if new != c.value:
                        c.value = new
                        done += 1
        elif op == "split_sheets":
            groups = {}
            for r in range(hr + 1, last + 1):
                v = ws.cell(r, cidx).value
                if v in (None, ""):
                    continue
                groups.setdefault(str(v).strip(), []).append(r)
            if len(groups) > 60:
                raise ValueError(f"{len(groups)} different values: too many sheets. Pick a column with fewer groups.")
            header = [c.value for c in ws[hr]]
            for k, (val, rows) in enumerate(sorted(groups.items())):
                name = re.sub(r"[\[\]:*?/\\]", " ", val)[:31] or f"Group {k + 1}"
                if name in wb.sheetnames:
                    name = f"{name[:27]} ({k + 1})"
                new = wb.create_sheet(name)
                new.append(header)
                for c in new[1]:
                    c.font = Font(bold=True)
                for r in rows:
                    new.append([c.value for c in ws[r]])
                done += 1
    return {"say": _say(p, done, notes, tables, hi), "sheet": p["table"], "changes": done}


def _say(p, done, notes, tables, hi):
    op, t = p["op"], tables.get(p["table"])
    where = ("every sheet" if not hi else "har sheet") if p.get("all") and not (op == "replace" and p.get("col") is not None) else p["table"]
    col = t.header[p["col"]] if (t is not None and p.get("col") is not None and p["col"] < len(t.header)) else ""
    en = {
        "bold_header": f"Made the heading row bold in {where}",
        "freeze": f"Froze the {'heading row and first column' if p.get('cell', '').startswith('B2') else 'first column' if p.get('cell') == 'B1' else 'heading row'} in {where}, so it stays visible while scrolling",
        "autofit": f"Fitted the column widths in {where} to their contents",
        "filter": f"Added filter buttons to the headings in {where}",
        "remove_blank_rows": f"Removed {done} blank rows from {where}",
        "delete_column": f"Deleted the column {col} from {where}",
        "rename": f"Renamed the column {col} to {p.get('new', '')} in {where}",
        "serial": f"Added a S.No column numbering {done} rows in {where}",
        "split": f"Split {col} into {'First Name and Last Name' if p.get('names') else 'separate columns'} for {done} rows in {where} (new columns at the end; {col} kept as it was)",
        "dropdown": f"Added a dropdown to {col} in {where} with: {', '.join(p.get('values', [])[:8])}{' …' if len(p.get('values', [])) > 8 else ''}",
        "replace": f"Replaced \"{p.get('find', '')}\" with \"{p.get('repl', '')}\" in {done} cells{' of ' + col if col else ''} in {where}",
        "date_format": f"Changed {done} dates{' in ' + col if col else ''} to {p.get('fmt', 'dd-mm-yyyy')} in {where}",
        "money_format": f"Showed {done} values of {col} in rupee format (₹1,23,456.00) in {where}",
        "round": f"Rounded {done} values of {col} to {p.get('places', 2)} decimal places in {where}",
        "case": f"Changed {done} values of {col} to {p.get('how', 'proper').replace('proper', 'Proper Case').replace('upper', 'UPPER CASE').replace('lower', 'lower case')} in {where}",
        "split_sheets": f"Made {done} new sheets from {where}, one for each {col}",
    }[op]
    if hi:
        hn = {
            "bold_header": f"{where} ki heading row bold kar di",
            "freeze": f"{where} mein {'heading row aur pehla column' if p.get('cell', '').startswith('B2') else 'pehla column' if p.get('cell') == 'B1' else 'heading row'} freeze kar diya; scroll karne pe bhi dikhega",
            "autofit": f"{where} ke columns ki width unke data jitni kar di",
            "filter": f"{where} ki headings pe filter buttons laga diye",
            "remove_blank_rows": f"{where} se {done} khaali rows hata di",
            "delete_column": f"{where} se {col} column hata diya",
            "rename": f"{where} mein {col} column ka naam {p.get('new', '')} kar diya",
            "serial": f"{where} mein S.No column jod diya ({done} rows)",
            "split": f"{where} mein {col} ko {'First Name aur Last Name' if p.get('names') else 'alag columns'} mein tod diya ({done} rows; naye columns aakhir mein, {col} waisa hi hai)",
            "dropdown": f"{where} ke {col} mein dropdown laga diya: {', '.join(p.get('values', [])[:8])}{' …' if len(p.get('values', [])) > 8 else ''}",
            "replace": f"{where} mein {done} jagah \"{p.get('find', '')}\" ko \"{p.get('repl', '')}\" kar diya",
            "date_format": f"{where} mein {done} dates {p.get('fmt', 'dd-mm-yyyy')} format mein kar di",
            "money_format": f"{where} mein {col} ke {done} values rupee format (₹1,23,456.00) mein kar diye",
            "round": f"{where} mein {col} ke {done} values {p.get('places', 2)} decimal tak round kar diye",
            "case": f"{where} mein {col} ke {done} values {p.get('how', 'proper').replace('proper', 'Proper Case').replace('upper', 'UPPER CASE').replace('lower', 'lower case')} mein kar diye",
            "split_sheets": f"{where} se {done} nayi sheets bana di, har {col} ki ek",
        }[op]
        if notes:
            hn += f". {', '.join(notes)} mein formulas hain, isliye wahan rows/columns nahi khiskaye" if op in ("remove_blank_rows", "delete_column") \
                else f". {', '.join(notes)} mein S.No aakhir mein lagaya taaki formulas sahi rahein"
        if done == 0 and op in ("remove_blank_rows", "replace", "case", "round", "date_format", "money_format"):
            hn = f"{where} mein badalne layak kuch nahi mila; kuch nahi badla"
        return hn
    if notes:
        en += (f". On {', '.join(notes)} rows and columns weren't moved because those sheets have formulas that would break"
               if op in ("remove_blank_rows", "delete_column") else f". On {', '.join(notes)} the S.No column went at the end, so the formulas stay correct")
    if done == 0 and op in ("remove_blank_rows", "replace", "case", "round", "date_format", "money_format"):
        any_ = "any sheet" if where == "every sheet" else where
        en = {"remove_blank_rows": f"No blank rows in {any_}; nothing changed",
              "replace": f"\"{p.get('find', '')}\" isn't in {any_}; nothing changed"}.get(op, f"Nothing to change in {where}")
    return en
