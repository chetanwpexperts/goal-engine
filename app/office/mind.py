"""The Excel agent's side of the mind: what pattern a question was, what the person will likely ask next (written with the real
sheet and column names of THEIR file, and checked that the agent can really do it), other readings when an answer
missed, and the nearest known question when something isn't understood."""

import json
import re
from collections import Counter

from app.mind import packs
from app.office import ask as asker
from app.office.sheetdata import Table


# ---------------------------------------------------------------- the pattern of a question
def label_of(plan: dict | None) -> str:
    if not plan:
        return "other"
    job = plan.get("job")
    if job == "aggregate":
        agg, g, f = plan.get("agg"), plan.get("group"), plan.get("filters")
        if plan.get("ranked"):
            return "rank"
        if isinstance(g, (list, tuple)):
            return "month"
        if agg in ("max", "min"):
            return "top"
        if agg == "count":
            return "count_g" if g is not None else "count_f" if f else "count"
        if agg == "avg":
            return "avg"
        return "sum_g" if g is not None else "sum_f" if f else "sum"
    if job == "list":
        return "top" if plan.get("topn") else "list_f"
    return {"running": "growth", "variance": "growth", "calc": "calc", "reconcile": "reconcile"}.get(job, job or "other")


# ---------------------------------------------------------------- what each column is, in any file
ID_RE = re.compile(r"\b(id|no|number|code|phone|mobile|pin|zip|year|sr|s\.?no|roll)\b", re.I)
TOTAL_RE = re.compile(r"amount|total|net|sales|revenue|value|salary|fee|bill|paid|balance|profit|tax", re.I)
MONEY_RE = re.compile(r"amount|total|net|sales|salary|price|value|fee|revenue|bill|cost|tax|paid|balance|profit", re.I)
AMOUNT_RE = re.compile(r"amount|total|net|sales|salary|price|value|fee|revenue|bill|cost|tax|paid|balance|profit|qty|quantity|units|marks|score", re.I)


def roles(t: Table) -> dict:
    cats, nums, dates = [], [], []
    n = len(t.rows)
    for i, h in enumerate(t.header):
        if not h:
            continue
        kind = t.kind(i)
        if kind == "date":
            dates.append(i)
        elif kind == "number" and not ID_RE.search(h):
            nums.append(i)
        elif kind == "text":
            vals = [str(v).strip() for v in t.col(i)[:5000] if v not in (None, "")]
            d = len(set(v.lower() for v in vals))
            if vals and 2 <= d <= min(25, max(2, int(0.6 * len(vals)))) and n >= 4:
                cats.append((i, Counter(vals).most_common(1)[0][0]))
    # what people add up: totals and amounts first; a unit price or rate summed down a column means little
    nums.sort(key=lambda i: (0 if TOTAL_RE.search(t.header[i]) else 1 if MONEY_RE.search(t.header[i]) else 2 if AMOUNT_RE.search(t.header[i]) else 3, i))
    return {"cats": cats, "nums": nums, "dates": dates}


def _focus(tables: list[Table], plan: dict | None) -> Table | None:
    if plan and plan.get("table"):
        t = next((x for x in tables if x.name == plan["table"]), None)
        if t is not None:
            return t
    data = [t for t in tables if len(t.rows) >= 3]
    return max(data, key=lambda t: len(t.rows)) if data else None


# ---------------------------------------------------------------- writing a pattern as a question the agent understands
EN = packs.get("office").get("templates", {}).get("en", {})
HI = packs.get("office").get("templates", {}).get("hi", {})
CHAT_KINDS = {"dups", "fix", "formula"}       # handled by the agent's own commands, not the question engine


def _fill(kind: str, t: Table, r: dict, hi: bool, prefer: dict | None = None) -> str | None:
    tpl = (HI if hi else EN).get(kind)
    if not tpl:
        return None
    prefer = prefer or {}
    cats = r["cats"]
    g = prefer.get("g") if prefer.get("g") is not None else (cats[0][0] if cats else None)
    g2 = next((c for c, _ in cats if c != g), None)
    m = prefer.get("m") if prefer.get("m") is not None else (r["nums"][0] if r["nums"] else None)
    fcol, fval = prefer.get("f") or ((cats[0][0], cats[0][1]) if cats else (None, None))
    need = set(re.findall(r"\{(\w+)\}", tpl))
    if ("g" in need and g is None) or ("g2" in need and g2 is None) or ("m" in need and m is None) or ("v" in need and fval is None):
        return None
    if kind == "month" and not r["dates"]:
        return None
    if "g" in need and kind == "count_f":
        g = fcol
    return tpl.format(S=t.name, g=t.header[g] if g is not None else "", g2=t.header[g2] if g2 is not None else "",
                      m=t.header[m] if m is not None else "", v=fval or "")


def _check(kind: str, text: str, tables: list[Table], t: Table) -> bool:
    """Only suggest what the agent will really understand and do the same way."""
    if kind in CHAT_KINDS:
        return True
    try:
        plan = asker.understand(text, tables)
    except asker.NeedMore:
        return False
    except Exception:  # noqa: BLE001
        return False
    got = label_of(plan)
    same = got == kind or (kind == "count" and got == "rows") or (kind == "rows" and got == "rows")
    return same and plan.get("table") in (t.name, None)


def _prefer_from(plan: dict | None, t: Table) -> dict:
    """Keep the person's own focus: the same column, the same value they just asked about."""
    out = {}
    if not plan or plan.get("table") != t.name:
        return out
    if plan.get("measure") is not None and t.kind(plan["measure"]) == "number":
        out["m"] = plan["measure"]
    g = plan.get("group")
    if isinstance(g, int):
        out["g"] = g
    if plan.get("filters"):
        i, v = plan["filters"][0]
        if isinstance(v, str):
            out["f"] = (i, v)
            out.setdefault("g", i)
    return out


def suggestions(scores: dict[str, float], tables: list[Table], plan: dict | None, report: dict | None, hi: bool,
                asked: str = "", limit: int = 3, min_p: float = 0.07) -> list[str]:
    """The likely next questions, best first, in the person's language and with their file's names."""
    t = _focus(tables, plan)
    if t is None:
        return []
    r = roles(t)
    prefer = _prefer_from(plan, t)
    summary = (report or {}).get("summary", {}) or {}
    problems = sum(summary.get(k, 0) or 0 for k in ("spaces", "text_numbers", "broken_refs", "case_mixed", "duplicate_rows"))
    out, seen_kinds = [], set()
    for kind, p in sorted(scores.items(), key=lambda x: -x[1]):
        if p < min_p or len(out) >= limit:
            break
        if kind == "fix" and not problems:
            continue
        if kind == "dups":
            sh = next((x for x in (report or {}).get("sheets", []) if x.get("name") == t.name), None)
            if not sh or not (sh.get("duplicate_rows") or sh.get("duplicate_values")):
                continue                              # only where this sheet really has duplicates
        if kind == "formula" and not plan:
            continue
        text = _fill(kind, t, r, hi, prefer)
        if not text or text.lower() == (asked or "").lower() or kind in seen_kinds:
            continue
        if _check(kind, text, tables, t):
            out.append(text)
            seen_kinds.add(kind)
    return out


# ---------------------------------------------------------------- the nearest known question, when nothing fits
def nearest(text: str, k: int = 3, min_score: float = 0.22) -> list[tuple[str, float]]:
    return packs.nearest("office", text, k, min_score)


def closest_options(text: str, tables: list[Table], hi: bool, limit: int = 3) -> list[str]:
    """'I didn't get that' -> the closest things the agent CAN do with this file, as tappable questions."""
    near = nearest(text, k=6)
    if not near:
        return []
    scores = {label: s for label, s in near}
    plan = None
    t = None
    named = asker.named_sheet(text, tables) or asker.pick_sheet(text, tables)
    if named is not None:
        plan = {"table": named.name}
        t = named
    return suggestions(scores, tables, plan, None, hi, asked=text, limit=limit, min_p=0.0) if (t or tables) else []


# ---------------------------------------------------------------- another reading, when the answer missed
def plan_text(plan: dict, tables: list[Table], hi: bool) -> str | None:
    t = next((x for x in tables if x.name == plan.get("table")), None)
    if t is None:
        return None
    kind = label_of(plan)
    r = roles(t)
    return _fill(kind, t, r, hi, _prefer_from(plan, t))


def _same(a: dict, b: dict) -> bool:
    keys = ("job", "table", "agg", "measure", "group", "filters", "topn")
    norm = lambda p: tuple(json.dumps(p.get(k), default=str) for k in keys)  # noqa: E731
    return norm(a) == norm(b)


def other_readings(text: str, tables: list[Table], plan: dict, hi: bool, limit: int = 3) -> list[str]:
    """The same words read against each sheet on its own, and with / without the value filter: the readings
    that differ from the one that was already given."""
    out = []
    cands = []
    for t in tables:
        if len(t.rows) < 1:
            continue
        try:
            p = asker.understand(text, [t])
        except Exception:  # noqa: BLE001
            continue
        if p.get("table") == t.name and not _same(p, plan):
            cands.append(p)
    if plan.get("job") == "aggregate" and plan.get("filters"):
        cands.append({"job": "rows", "table": plan["table"]})
    if plan.get("job") == "rows" and plan.get("table"):
        for t in tables:
            for i, v in asker.values_in(text, t):
                cands.append({"job": "aggregate", "table": t.name, "agg": "count", "measure": None, "group": None,
                              "filters": [(i, v)], "topn": None})
    for p in cands:
        s = plan_text(p, tables, hi)
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def sheet_or_value(text: str, tables: list[Table], plan: dict, hi: bool) -> str | None:
    """'how many sales records': the Sales sheet, or Department = Sales? Answer the likelier one, offer the other."""
    if asker.named_sheet(text, tables):
        return None
    low = (text or "").lower()
    if plan.get("job") == "aggregate" and plan.get("filters"):
        t0 = next((x for x in tables if x.name == plan["table"]), None)
        if t0 is not None and any(w in low for i, _ in plan["filters"] for w in asker._header_words(t0.header[i]) if len(w) >= 4):
            return None                               # "Sales department": they named the column, so it's the value
    if plan.get("job") == "rows" and plan.get("table"):
        for t in tables:
            for i, v in asker.values_in(text, t):
                if v.lower() in plan["table"].lower():
                    alt = {"job": "aggregate", "table": t.name, "agg": "count", "measure": None, "group": None,
                           "filters": [(i, v)], "topn": None}
                    return plan_text(alt, tables, hi)
    if plan.get("job") == "aggregate" and plan.get("filters"):
        for i, v in plan["filters"]:
            if not isinstance(v, str):
                continue
            sheet = next((t for t in tables if t.name != plan["table"] and v.lower() in t.name.lower()
                          and asker.pick_sheet(v, [t]) is not None), None)
            if sheet is not None:
                return plan_text({"job": "rows", "table": sheet.name}, tables, hi)
    return None


# ---------------------------------------------------------------- the hook the shared mind calls
SUGGEST_MAX_BYTES = 10 * 1024 * 1024       # bigger files: no reading the whole file again just to suggest


def suggest(db, agent, scores: dict, cur: dict, hi: bool, text: str) -> list[str]:
    from app.agents import office_agent
    from app.models import loads
    from app.office.sheetdata import load_tables

    f = office_agent.open_file(db, agent) if agent is not None else None
    if f is None or f.status != "ready" or f.size > SUGGEST_MAX_BYTES:
        return []
    prof = cur.get("profile", {})
    scores = dict(scores)
    if prof.get("level") == "expert" and cur.get("plan"):
        scores["formula"] = max(scores.get("formula", 0), 0.2)    # people who name functions like formulas
    if prof.get("formula") is False:
        scores.pop("formula", None)
    tables = cur.get("tables") or load_tables(f.path)
    plan = cur.get("plan") or (loads(agent.inputs, {}) or {}).get("mind_plan")
    return suggestions(scores, tables, plan, loads(f.report, {}) or {}, hi, asked=text)


def ready(db, agent) -> bool:
    from app.agents import office_agent

    f = office_agent.open_file(db, agent) if agent is not None else None
    return f is not None and f.status == "ready"
