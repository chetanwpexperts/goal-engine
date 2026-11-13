"""Reading a query the way a careful person does, stage by stage (app/dbknow/core/query_fundamentals.json, 'checklist'):
what each row shows, which tables, how they are linked (one-to-one or one-to-many), which rows are kept, what is totalled,
what one result row stands for (the grain), group filters, stages, windows and the final cut. Worked out from the query
text alone; nothing is run. Used for 'explain this query', for the card, and to name the fundamental behind a finding."""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.agents import sqlcorrect as C

FUND = Path(__file__).resolve().parent.parent / "dbknow" / "core" / "query_fundamentals.json"


@lru_cache(maxsize=1)
def fundamentals() -> dict:
    return json.loads(FUND.read_text(encoding="utf-8"))


def fundamental_of(finding: dict) -> str | None:
    """The fundamental a finding breaks: its topic, with the LEFT JOIN findings told apart from row multiplication."""
    title = finding.get("title_en", "")
    if finding.get("topic") == "join" and re.search(r"LEFT JOIN|outer", title, re.I):
        return "outer_join"
    if finding.get("topic") == "group" and "multiplied" in title:
        return "cardinality"
    for fid, f in fundamentals()["fundamentals"].items():
        if finding.get("topic") in f["topics"]:
            return fid
    return None


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _is_key(src, col: str) -> bool:
    kb = C._block_key(src, col)
    if kb is not None:
        return kb
    c = col.lower().replace("_", "")
    return c == "id" or bool(src and src.name and c in {x.replace("_", "") + "id" for x in C._singulars(src.name)})


def read_query(sql: str) -> dict:
    s = C._mask(sql)
    dep = C._depths(s)
    low = s.lower()
    kind = (re.match(r"[\s(]*(\w+)", low) or [None, ""])[1]
    out = {"kind": kind, "output": [], "tables": [], "links": [], "row_filters": [], "totals": [], "grain": "",
           "group_filters": "", "stages": [], "windows": [], "final_cut": [], "order": ""}
    for m in re.finditer(r"\bwith\s+(?:recursive\s+)?([a-z_]\w*)\s+as\s*\(|,\s*([a-z_]\w*)\s+as\s*\(", low):
        if dep[m.start()] == 0:
            out["stages"].append(f"CTE {m.group(1) or m.group(2)}")
    subs = [m for m in re.finditer(r"\(\s*select\b", s, re.I)]
    if subs:
        out["stages"].append(f"{len(subs)} subquer{'y' if len(subs) == 1 else 'ies'}")
    if kind in ("update", "delete"):
        tm = re.match(r"\s*(?:update|delete\s+from)\s+[`\"]?([\w$.]+)[`\"]?", low)
        out["tables"].append({"name": tm.group(1) if tm else "?", "alias": "", "join": "changed"})
        sm = re.search(r"\bset\b([\s\S]*?)(?:\bwhere\b|$)", s, re.I) if kind == "update" else None
        if sm:
            out["output"] = [_flat(sql[sm.start(1) + p:sm.start(1) + p + len(t)]) for p, t in C._split_top(s[sm.start(1):sm.end(1)])
                             if t.strip()]
        wm = re.search(r"\bwhere\b", low)
        if wm:
            out["row_filters"] = _and_parts(sql, s, wm.end(), len(s))
        out["grain"] = "each changed row of " + (out["tables"][0]["name"] if out["tables"] else "the table")
        return out
    cl = C._clauses(s, dep)
    if "select" in cl:
        out["output"] = [_flat(sql[cl["select"][0] + p:cl["select"][0] + p + len(t)]) for p, t in C._split_top(s[cl["select"][0]:cl["select"][1]])
                         if t.strip()]
    srcs = C._sources(s, dep, *cl["from"]) if "from" in cl else []
    by = {(x.alias or "").lower(): x for x in srcs}
    for x in srcs:
        out["tables"].append({"name": x.name or "(subquery)", "alias": x.alias, "join": x.kind})
        for m in C.EQ.finditer(x.on or ""):
            la, lc, ra, rc = m.group(1).lower(), m.group(2), m.group(3).lower(), m.group(4)
            lk, rk = _is_key(by.get(la), lc), _is_key(by.get(ra), rc)
            ln, rn = (by[la].name if la in by else la), (by[ra].name if ra in by else ra)
            if lk and rk:
                shape, many, one = "one-to-one", "", ""
            elif rk:
                shape, many, one = "one-to-many", ln, rn
            elif lk:
                shape, many, one = "one-to-many", rn, ln
            else:
                shape, many, one = "unclear (no key on either side)", "", ""
            out["links"].append({"join": x.kind, "on": _flat(m.group(0)), "shape": shape, "many_side": many, "one_side": one})
    if "where" in cl:
        out["row_filters"] = _and_parts(sql, s, *cl["where"])
    aggs = [m.group(0) for m in re.finditer(r"\b(count|sum|avg|min|max|group_concat|string_agg)\s*\([^()]*\)", sql, re.I)
            if dep[m.start()] == 0 and not re.match(r"\s*over\b", sql[m.end():], re.I)]
    out["totals"] = aggs
    group = _flat(sql[cl["group by"][0]:cl["group by"][1]]).rstrip(";") if "group by" in cl else ""
    if group:
        out["grain"] = f"one row per {group}"
    elif aggs:
        out["grain"] = "one row in total"
    else:
        g = C._grain(srcs, lambda q: q.lower(), set()) if srcs else None
        out["grain"] = (f"one row per {by[g].name or 'subquery'} row" if g and g in by else
                        "one row per combination of joined rows" if len(srcs) > 1 else
                        (f"one row per {srcs[0].name or 'subquery'} row" if srcs else ""))
    if "having" in cl:
        out["group_filters"] = _flat(sql[cl["having"][0]:cl["having"][1]]).rstrip(";")
    for m in re.finditer(r"\b(\w+)\s*\([^()]*\)\s+over\s*\(([^()]*)\)", sql, re.I):
        out["windows"].append(_flat(m.group(0)))
    if "order by" in cl:
        out["order"] = _flat(sql[cl["order by"][0]:cl["order by"][1]]).rstrip(";")
    if "limit" in cl:
        out["final_cut"].append("LIMIT " + _flat(sql[cl["limit"][0]:cl["limit"][1]]).rstrip(";"))
    rk = re.search(r"\bwhere\s+(\w+)\s*(<=|<|=)\s*(\d+)\s*(?:order\b|;|$)", low[low.rfind(")"):] if ")" in low else "")
    if out["windows"] or rk:
        if rk:
            out["final_cut"].append(f"{rk.group(1)} {rk.group(2)} {rk.group(3)}")
    if out["group_filters"]:
        out["final_cut"].insert(0, "HAVING " + out["group_filters"])
    return out


def _and_parts(sql: str, s: str, a: int, b: int) -> list[str]:
    stop = re.search(r"\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\blimit\b|;", C._no_subqueries(s[a:b]), re.I)
    b = a + stop.start() if stop else b
    text = s[a:b]
    d = C._depths(text)
    cuts = [0] + [m.end() for m in re.finditer(r"\band\b", text, re.I) if d[m.start()] == 0 and
                  not re.search(r"\bbetween\s+\S+\s*$", text[:m.start()], re.I)] + [len(text)]
    parts = []
    for x, y in zip(cuts, cuts[1:]):
        p = _flat(re.sub(r"^\s*and\b|\band\s*$", "", sql[a + x:a + y], flags=re.I))
        if p:
            parts.append(p[:120])
    return parts


def render(r: dict, hi: bool = False) -> str:
    """The reading as short lines, in the checklist's order."""
    L = []
    if r["kind"] in ("update", "delete"):
        t = r["tables"][0]["name"] if r["tables"] else "?"
        L.append((f"Ye {t} ki rows badalti hai." if r["kind"] == "update" else f"Ye {t} se rows hatati hai.") if hi else
                 (f"It changes rows of {t}." if r["kind"] == "update" else f"It deletes rows from {t}."))
        if r["output"]:
            L.append(("Kya badalta hai: " if hi else "What changes: ") + "; ".join(r["output"][:6]))
        if r["row_filters"]:
            L.append(("Kaunsi rows: " if hi else "Which rows: ") + " AND ".join(r["row_filters"][:6]))
        else:
            L.append("Koi WHERE nahi: SAARI rows!" if hi else "No WHERE: EVERY row!")
        if r["stages"]:
            L.append(("Stages: " if hi else "Stages: ") + ", ".join(r["stages"]))
        return "\n".join(f"- {x}" for x in L)
    if r["output"]:
        L.append(("Dikhata hai: " if hi else "Shows: ") + ", ".join(r["output"][:8]) + (" …" if len(r["output"]) > 8 else ""))
    if r["tables"]:
        L.append(("Tables: " if hi else "Tables: ") + ", ".join(f"{t['name']}" + (f" ({'inner' if t['join'] == 'join' else t['join']} join)" if t["join"] not in ("from",) else "")
                                                             for t in r["tables"]))
    for k in r["links"]:
        if k["many_side"]:
            many = (f" — ek {k['one_side']} ki kai {k['many_side']} rows (column naam se andaza, verified nahi)" if hi else
                    f" — one {k['one_side']} row, many {k['many_side']} rows (guessed from the column names, not verified)")
        else:
            many = f" ({k['shape']})"
        L.append(f"{'Link' if not hi else 'Jod'}: {k['on']}{many}")
    if r["row_filters"]:
        L.append(("Rows rakhi jaati hain jab: " if hi else "Keeps rows where: ") + " AND ".join(r["row_filters"][:6]))
    if r["totals"]:
        L.append(("Totals: " if hi else "Totals: ") + ", ".join(r["totals"][:6]))
    if r["grain"]:
        L.append(("Grain: " if hi else "Grain: ") + r["grain"])
    if r["stages"]:
        L.append("Stages: " + ", ".join(r["stages"]))
    if r["windows"]:
        L.append(("Window: " if hi else "Window: ") + "; ".join(r["windows"][:3]))
    if r["final_cut"]:
        L.append(("Aakhir mein: " if hi else "Final cut: ") + "; ".join(r["final_cut"]))
    if r["order"]:
        L.append(("Order: " if hi else "Order: ") + r["order"])
    return "\n".join(f"- {x}" for x in L)
