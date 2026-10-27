"""Generator for the Excel orders: thousands of random ways of asking (any word order, synonyms, Hinglish, Hindi script,
typos, numbers written 1L / 1,00,000 / 100k), and questions using the same words that must NOT become orders.
Run: python -m tests.fuzz.orders [count] [seed]
"""

import random
import sys
from pathlib import Path

PRACTICE = Path(__file__).resolve().parents[1] / "data" / "practice.xlsx"

ACT = ["karo", "kar do", "kardo", "make", "set", "apply", "lagao", "laga do", "please", "pls", "kijiye", "bana do", "do", "karna hai",
       "chahiye", "can you", "kindly", "zara", "करो", "बना दो", "लगाओ"]
FILL = ["", "", "", "the", "sheet mein", "ji", "jaldi", "for me", "abhi", "yaar", "in the file", "isme"]
HINDI = {"hara": "हरा", "lal": "लाल", "hatao": "हटाओ", "banao": "बनाओ", "column": "कॉलम", "chart": "चार्ट", "khali": "खाली",
         "peela": "पीला", "neela": "नीला"}
COLOURS = {"red": ["red", "laal", "lal", "लाल"], "green": ["green", "hara", "हरा"], "yellow": ["yellow", "peela", "पीला"],
           "blue": ["blue", "neela", "नीला"], "orange": ["orange", "narangi"]}


def typo(w: str, rng: random.Random) -> str:
    if len(w) < 6 or not w.isalpha() or not w.isascii() or rng.random() > 0.15:
        return w
    i = rng.randrange(1, len(w) - 1)
    return w[:i] + w[i + 1:] if rng.random() < 0.5 else w[:i] + w[i + 1] + w[i] + w[i + 2:]


def money(n: int, rng: random.Random) -> str:
    forms = [f"{n}", f"{n:,}"]
    if n % 100000 == 0:
        forms += [f"{n // 100000} lakh", f"{n // 100000}L", f"{n // 100000} lac"]
    if n % 1000 == 0:
        forms += [f"{n // 1000}k", f"{n // 1000} thousand", f"{n // 1000} hazar"]
    return rng.choice(forms)


def say(chunks: list[str], rng: random.Random, keep_order=False, exact=()) -> str:
    """Chunks in random order, small typos in the words (never inside the values the person dictates)."""
    chunks = [c for c in chunks if c]
    if not keep_order:
        rng.shuffle(chunks)
    return " ".join(c if c in exact else " ".join(typo(w, rng) for w in c.split()) for c in chunks)


def P(*opts):
    return lambda rng: rng.choice(opts)


def case_order(rng):
    k = rng.choice(["upper", "lower", "proper"])
    word = {"upper": P("uppercase", "capital letters", "all caps", "bade akshar", "upper case", "CAPITAL"),
            "lower": P("lowercase", "small letters", "chhote akshar", "lower case"),
            "proper": P("proper case", "title case", "first letter capital", "capitalize")}[k](rng)
    col = rng.choice(["Region", "Product", "Sales Rep"])
    return say([word, col, rng.choice(ACT), rng.choice(FILL)], rng), {"op": "case", "how": k, "colname": col}


def cf_order(rng):
    kind = rng.choice(["above", "below", "between", "top", "duplicate", "blank", "text", "scale"])
    colour = rng.choice(list(COLOURS))
    cw = rng.choice(COLOURS[colour])
    hl = rng.choice(["highlight", "colour", "mark", "rang do", "highlite", "color", "shade"])
    if kind in ("above", "below"):
        n = rng.choice([50000, 100000, 200000, 25000, 1000])
        w = rng.choice(["above", "more than", "greater than", "upar", "zyada", "se zyada", "over", ">"]) if kind == "above" else \
            rng.choice(["below", "less than", "kam", "se kam", "under", "neeche", "<"])
        num = money(n, rng)
        cond = f"{w} {num}" if w in (">", "<") or rng.random() < 0.5 else f"{num} {w}"
        return say([hl, "Net Sales", cond, cw, rng.choice(FILL)], rng), {"op": "conditional_format", "rule": kind, "value": n,
                                                                         "colour": colour, "colname": "Net Sales"}
    if kind == "between":
        a, b = 10000, 50000
        return say([hl, "Net Sales", f"between {money(a, rng)} and {money(b, rng)}", cw], rng), \
            {"op": "conditional_format", "rule": "between", "values": [a, b], "colour": colour}
    if kind == "top":
        n = rng.choice([5, 10, 3])
        return say([hl, f"top {n}", "Net Sales", cw], rng), {"op": "conditional_format", "rule": "top10", "rank": n, "colour": colour}
    if kind == "duplicate":
        return say([hl, rng.choice(["duplicate", "duplicates", "repeated", "dupliket", "twice wale"]), "Invoice No.", cw], rng), \
            {"op": "conditional_format", "rule": "duplicate", "colour": colour, "colname": "Invoice No."}
    if kind == "blank":
        return say([hl, rng.choice(["blank", "empty", "khali", "khaali", "खाली"]), rng.choice(["cells", ""]), cw], rng), \
            {"op": "conditional_format", "rule": "blank", "colour": colour}
    if kind == "text":
        return say(["Approval Status", "Pending", rng.choice(["wale", "jahan", ""]), cw, rng.choice([hl, "karo", ""])], rng), \
            {"op": "conditional_format", "rule": "text_equal", "value": "Pending", "colour": colour}
    return say([rng.choice(["colour scale", "color scale", "heatmap", "gradient"]), rng.choice(["on", "lagao", "for"]), "Net Sales"], rng), \
        {"op": "conditional_format", "rule": "color_scale"}


def chart_order(rng):
    kind = rng.choice(["pie", "line", "bar"])
    kw = {"pie": P("pie", "donut", "pie chart"), "line": P("line", "trend"), "bar": P("bar", "column", "")}[kind](rng)
    noun = rng.choice(["chart", "graph", "diagram", "plot", "चार्ट"])
    group = rng.choice(["region wise", "by region", "per region", "Region ke hisaab se"]) if kind != "line" else \
        rng.choice(["month wise", "monthly", "by month", "mahine ke hisaab se"])
    expect = {"op": "chart", "chart_type": "bar" if kind == "bar" else kind}
    return say([kw, noun, group, rng.choice(["Net Sales", "sales", ""]), rng.choice(ACT)], rng), expect


def simple(rng):
    op = rng.choice(["bold_header", "freeze", "autofit", "filter", "remove_blank_rows", "serial", "delete_column", "money_format",
                     "round", "date_format", "split_sheets", "dropdown"])
    if op == "bold_header":
        return say([rng.choice(["bold", "mota", "thick"]), rng.choice(["header", "heading", "headings", "top row", "pehli row", "first row"]),
                    rng.choice(ACT)], rng), {"op": op}
    if op == "freeze":
        return say([rng.choice(["freeze", "freez", "jama do", "pin", "stick"]), rng.choice(["header", "top row", "first row", "pehli row", "heading"]),
                    rng.choice(ACT + [""])], rng), {"op": op, "cell": "A2"}
    if op == "autofit":
        return say([rng.choice(["fit", "adjust", "autofit", "resize", "theek"]), rng.choice(["column width", "columns width", "column ki chaudai",
                                                                                         "widths of columns"]), rng.choice(ACT + [""])], rng), {"op": op}
    if op == "filter":
        return say([rng.choice(["filter", "filters", "autofilter", "filter buttons"]), rng.choice(["add", "lagao", "laga do", "apply", "daalo", "put"])],
                   rng), {"op": op}
    if op == "remove_blank_rows":
        return say([rng.choice(["remove", "delete", "hatao", "hata do", "nikalo", "हटाओ", "clear"]), rng.choice(["blank", "empty", "khali", "खाली"]),
                    rng.choice(["rows", "lines", "row", "panktiyan"])], rng), {"op": op}
    if op == "serial":
        return say([rng.choice(["serial number", "s no", "sr no", "numbering", "serial no"]), rng.choice(["add", "lagao", "daalo", "insert", "put"])],
                   rng), {"op": op}
    if op == "delete_column":
        col = rng.choice(["Order Category", "Discount %", "Sales Rep"])
        return say([rng.choice(["delete", "remove", "hatao", "hata do", "drop", "nikal do"]), col, rng.choice(["column", "col", "कॉलम"])], rng), \
            {"op": op, "colname": col}
    if op == "money_format":
        return say([rng.choice(["rupee", "rupees", "₹", "currency", "indian", "rs"]), rng.choice(["format", "formatting", "style"]), "Net Sales",
                    rng.choice(ACT)], rng), {"op": op, "colname": "Net Sales"}
    if op == "round":
        n = rng.choice([0, 1, 2, 3])
        return say([rng.choice(["round", "round off", "rounding"]), "Net Sales", f"{n} decimal" if n else rng.choice(["no decimal", "zero decimal",
                    "0 decimal"]), rng.choice(ACT)], rng), {"op": op, "places": n}
    if op == "date_format":
        mask = rng.choice(["dd-mm-yyyy", "dd/mm/yyyy", "mm/dd/yyyy", "yyyy-mm-dd"])
        return say([rng.choice(["date", "dates", "tarikh"]), rng.choice(["format", "formatting"]), mask, rng.choice(ACT + ["change", "badlo"])],
                   rng), {"op": op, "fmt": mask}
    if op == "split_sheets":
        return say([rng.choice(["split", "separate", "alag", "divide"]), rng.choice(["sheets", "tabs", "sheet"]),
                    rng.choice(["by Region", "region wise", "per Region", "har Region"])], rng), {"op": op, "colname": "Region"}
    return say(["Status", rng.choice(["dropdown", "drop down", "picklist", "data validation"]), rng.choice(ACT + ["add"]),
                "with Done, Pending, Started"], rng, keep_order=True, exact=("with Done, Pending, Started",)), {"op": "dropdown", "colname": "Status",
                                                                           "values": ["Done", "Pending", "Started"]}


def ordered(rng):
    """Orders whose meaning needs word order (old -> new)."""
    k = rng.choice(["rename", "replace"])
    if k == "rename":
        return rng.choice([f"rename Net Sales to Net Amount", "Net Sales ka naam Net Amount karo", "change the column name of Net Sales to Net Amount",
                           "Net Sales column ka naam Net Amount rakh do", "rename column 'Net Sales' as 'Net Amount'"]), \
            {"op": "rename", "colname": "Net Sales", "new": "Net Amount"}
    city = rng.choice(["East", "North", "West"])
    return rng.choice([f"replace {city} with Eastern Zone", f"change all {city} to Eastern Zone", f"{city} ko Eastern Zone se badlo",
                       f"{city} ki jagah Eastern Zone likho", f"'{city}' ko 'Eastern Zone' kar do"]), {"op": "replace", "find": city,
                                                                                                    "repl": "Eastern Zone"}


QUESTION_START = ["how many", "which", "what", "kitne", "kitni", "kya", "kaunse", "is", "are", "does", "batao", "tell me"]


def question(rng):
    body = rng.choice(["Net Sales above 1 lakh", "duplicate invoices", "blank cells", "rows are empty", "header bold", "top row frozen",
                       "Net Sales in rupee format", "dates in dd-mm-yyyy", "region wise chart", "highlighted cells", "columns in this sheet",
                       "sales below 50000", "Pending status wale", "orders in East", "top 10 sales"])
    end = rng.choice(["?", "", " hai", " hain?", " are there", " in this file"])
    return f"{rng.choice(QUESTION_START)} {body}{end}", {"op": None}


GENS = [(case_order, 1), (cf_order, 3), (chart_order, 2), (simple, 5), (ordered, 1), (question, 3)]


def case(i: int, seed: int):
    rng = random.Random(seed * 100003 + i)
    gen = rng.choices([g for g, _ in GENS], weights=[w for _, w in GENS])[0]
    return gen(rng)


def check(text: str, want: dict, tables) -> str | None:
    from app.office import ask, edits

    try:
        p = edits.plan_edit(text, tables)
    except ask.NeedMore as e:
        return None if want["op"] is None else f"{text!r}: asked back ({str(e)[:60]}) instead of {want['op']}"
    if want["op"] is None:
        return None if p is None else f"{text!r}: question became order {p.get('op')}"
    if p is None or p.get("op") != want["op"]:
        return f"{text!r}: got {p and p.get('op')}, want {want['op']}"
    t = next(x for x in tables if x.name == p["table"])
    for k, v in want.items():
        if k == "op":
            continue
        if k == "colname":
            got = t.header[p["col"]] if isinstance(p.get("col"), int) else p.get("col")
            if got != v:
                return f"{text!r}: column {got}, want {v}"
            continue
        if p.get(k) != v:
            return f"{text!r}: {k}={p.get(k)!r}, want {v!r}"
    return None


def run(count=2000, seed=1):
    from app.office.sheetdata import load_tables

    tables = load_tables(str(PRACTICE))
    errs = []
    for i in range(count):
        text, want = case(i, seed)
        e = check(text, want, tables)
        if e:
            errs.append(e)
    return count - len(errs), errs


if __name__ == "__main__":
    c = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    s = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    ok, errs = run(c, s)
    print(f"{ok}/{c} right")
    for e in errs[:40]:
        print(" ", e)
    sys.exit(1 if errs else 0)
