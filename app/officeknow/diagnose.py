"""The Excel agent works out WHY: what the person describes + the facts in their own file -> the proven cause(s),
explained from how Excel works, with the proof, the fix, how to confirm it and how to stop it happening again.

Never confidently wrong: a cause is only stated when the file proves it. When nothing proves a cause, the reply says
what was checked and what to look at next.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.mind import reason

HERE = Path(__file__).resolve().parent
NEW_OPS = {"extend_ranges", "exact_lookup", "auto_calc", "restore_formulas"}


@lru_cache(maxsize=1)
def knowledge() -> dict:
    k = json.loads((HERE / "fundamentals.json").read_text(encoding="utf-8"))
    for c in k["complaints"].values():
        c["re"] = re.compile(c["cues"], re.I)
    return k


def complaints(text: str) -> dict[str, float]:
    """What the person says is wrong, from their words; the issue the ears recognised adds to it."""
    k = knowledge()
    out = {cid: 1.0 for cid, c in k["complaints"].items() if c["re"].search(text or "")}
    try:
        from app.officeknow import brain

        rec = brain.recognize(text)
        if rec.get("kind") == "issue" and rec.get("confidence", 0) >= 0.5:
            for cid, w in k["issues"].get(rec["id"], {}).items():
                out[cid] = max(out.get(cid, 0), w * rec["confidence"])
    except Exception:  # noqa: BLE001 - understanding helps, never breaks
        pass
    return out


def describes_problem(text: str) -> bool:
    return bool(complaints(text))


def _fill(tpl: str, fact: dict) -> str:
    items = fact.get("items") or []
    first = items[0] if items else {}
    cells = fact.get("cells") or []
    vals = {"count": fact.get("count", 0), "impact_text": fact.get("impact_text", ""),
            "cells": ", ".join((c["cell"] if isinstance(c, dict) else c) for c in cells[:6]) + (" …" if len(cells) > 6 else ""),
            "first": first.get("cell", ""), "range": first.get("range", ""), "left_out": first.get("left_out", ""),
            "rows": first.get("rows", ""), "fixed": first.get("fixed", ""), "looks_for": first.get("looks_for", ""),
            "there": first.get("there", ""), "in": first.get("in", ""), "hidden_rows": first.get("hidden_rows", ""),
            "are": "is" if fact.get("count", 0) == 1 else "are", "were": "was" if fact.get("count", 0) == 1 else "were"}
    if not vals["impact_text"] and first.get("impact_text"):
        vals["impact_text"] = first["impact_text"]
    try:
        out = tpl.format(**vals)
    except (KeyError, IndexError, ValueError):
        out = tpl
    one = vals["count"] == 1
    return re.sub(r"(\w)\(s\)", lambda m: m.group(1) + ("" if one else "s"), out)


def _proof(fact: dict, hi: bool) -> str:
    items = fact.get("items") or []
    if items:
        bits = []
        for it in items[:3]:
            if "left_out" in it:
                bits.append(f"{it['cell']} {it['formula']} -> {'chhoota' if hi else 'leaves out'} {it['left_out']}")
            elif "looks_for" in it:
                bits.append(f"{it['cell']} {'dhoondta hai' if hi else 'looks for'} \"{it['looks_for']}\"" +
                            ((f", list mein \"{it['there']}\"" if hi else f", the list has \"{it['there']}\"") if "there" in it else ""))
            elif "formula" in it:
                bits.append(f"{it['cell']} {it['formula']}")
            elif "hidden_rows" in it:
                bits.append(f"{it['cell']}: {it['hidden_rows']} " + ("chhupi row" if hi else "hidden row(s)"))
        return "; ".join(bits)
    cells = fact.get("cells") or []
    shown = []
    for c in cells[:5]:
        shown.append(f"{c['cell']} = \"{c['value']}\"" if isinstance(c, dict) else c)
    return ", ".join(shown) + (" …" if fact.get("count", 0) > len(shown) else "")


def diagnose(text: str, facts: dict, outcomes: dict | None = None) -> dict:
    k = knowledge()
    said = complaints(text)
    result = reason.think(k, said, facts, outcomes)
    result["complaints"] = said
    return result


def _line(c: dict, fact: dict, key: str, hi: bool) -> str:
    return _fill(c[f"{key}_{'hi' if hi else 'en'}"], fact)


def report(result: dict, hi: bool, file_name: str = "") -> tuple[str, list[str]]:
    """The full report in plain words, and the fixes the agent can do on the file (offered, done only on 'yes')."""
    k = knowledge()
    causes = k["causes"]
    found, also = result["found"], result["also"]
    said = [k["complaints"][c]["hi" if hi else "en"] for c in result["complaints"]]
    ops = []
    if not found:
        names = [x["cause"] for x in result["ruled_out"]]
        lines = [("Maine file mein ye sab check kiya, inmein se koi wajah nahi mili:" if hi else
                  "I checked your file for every known cause of this, and none of them is there:")]
        lines += [f"- {_no(n, hi)}" for n in names][:8]
        if result["open"]:
            lines.append("")
            lines.append(("Ye abhi check nahi ho paya: " if hi else "Not checked yet: ") +
                         ", ".join(_no(x["cause"], hi, plain=True) for x in result["open"][:4]) + ".")
        lines.append("")
        lines.append("Kaunsa cell ya total galat hai, aur kitna aana chahiye? Woh bataoge to main wahi cell trace karke wajah dhoondunga."
                     if hi else "Which cell or total is wrong, and what should it be? Tell me and I'll trace that cell back to the cause.")
        if also:
            lines.append("")
            lines.append(("Waise file mein ye bhi mila: " if hi else "I also found: ") +
                         "; ".join(_fill(causes[x["cause"]]["title_" + ("hi" if hi else "en")], x["fact"]) for x in also[:3]) + ".")
        return "\n".join(lines), ops
    many = len(found) > 1
    if hi:
        head = f"{file_name} mein wajah mil gayi" if file_name else "Wajah mil gayi"
    else:
        head = ("Found the causes" if many else "Found the cause") + (f" in {file_name}" if file_name else "")
    lines = [head + (f" ({', '.join(said)})." if said else ".")]
    for i, x in enumerate(found[:4], start=1):
        c, fact = causes[x["cause"]], x["fact"]
        lines.append("")
        lines.append(f"{i}. {_line(c, fact, 'title', hi)}")
        lines.append(f"- {'Kyun' if hi else 'Why'}: {_line(c, fact, 'why', hi)}")
        proof = _proof(fact, hi)
        if proof:
            lines.append(f"- {'Saboot' if hi else 'Proof'}: {proof}")
        lines.append(f"- {'Theek' if hi else 'Fix'}: {_line(c, fact, 'fix', hi)}")
        lines.append(f"- {'Pakka kaise' if hi else 'Check'}: {_line(c, fact, 'confirm', hi)}")
        lines.append(f"- {'Aage se' if hi else 'Prevent'}: {_line(c, fact, 'prevent', hi)}")
        if c.get("fix") and c["fix"] not in ops:
            ops.append(c["fix"])
    also = [x for x in also if causes[x["cause"]].get("fix") not in ops]      # the same cells as a cause above: said once
    if also:
        lines.append("")
        lines.append(("Iske alawa (is problem ki wajah nahi, par theek karne layak): " if hi else
                      "Also in the file (not the cause of this, but worth fixing): ") +
                     "; ".join(_fill(causes[x["cause"]]["title_" + ("hi" if hi else "en")], x["fact"]) for x in also[:3]) + ".")
    lines.append("")
    if ops:
        lines.append("Theek kar doon? 'haan' likho. Main nayi copy banata hoon, har badlav Changes sheet mein, aur Undo hamesha chalega."
                     if hi else "Shall I fix it? Type 'yes'. I make a new copy, every change is listed in the Changes sheet, and Undo always works.")
    else:
        lines.append("Ye theek karna aapke haath mein hai; upar steps hain. Bolo to main saath mein karwa doon."
                     if hi else "This one needs a change only you can make; the steps are above. Ask and I'll walk you through it.")
    return "\n".join(lines), ops


NO = {
    "text_in_totals": ("Total ke andar text wale number", "numbers saved as text inside your totals"),
    "short_range": ("Total ki range jo data se pehle ruk jaaye", "a total whose range stops before the data ends"),
    "hidden_in_totals": ("Total mein chhupi rows", "hidden rows inside a total"),
    "typed_over_formula": ("Formula column mein type kiya number", "typed numbers inside a formula column"),
    "manual_calc": ("Manual calculation", "calculation set to Manual"),
    "lookup_spaces": ("Lookup value mein extra space / alag spelling", "extra spaces or different spelling in lookup values"),
    "lookup_type": ("Number vs text wala number", "numbers compared with numbers saved as text"),
    "lookup_missing": ("List mein missing values", "values missing from the lookup list"),
    "approx_lookup": ("Approximate match wale lookup", "lookups using approximate match"),
    "broken_refs": ("Toote reference (#REF!)", "broken references (#REF!)"),
    "div0": ("Zero se divide (#DIV/0!)", "division by zero (#DIV/0!)"),
    "value_err": ("Text pe hisaab (#VALUE!)", "maths on text (#VALUE!)"),
    "name_err": ("Galat function naam (#NAME?)", "unknown function names (#NAME?)"),
    "duplicate_rows": ("Duplicate rows", "duplicate rows"),
    "text_dates": ("Text wali dates", "dates saved as text"),
    "spaces": ("Extra space", "extra spaces"),
    "text_numbers": ("Text wale number", "numbers saved as text"),
}


def _no(cause: str, hi: bool, plain: bool = False) -> str:
    name = NO.get(cause, (cause, cause))[0 if hi else 1]
    if plain:
        return name
    return (name + ": nahi mila") if hi else ("no " + name)


def likely(text: str, hi: bool) -> str:
    """No file yet: what usually causes this (from how Excel works), and that the file will tell which one it is."""
    k = knowledge()
    said = complaints(text)
    if not said:
        return ""
    res = reason.think(k, said, {})
    top = [x["cause"] for x in res["open"][:3]]
    if not top:
        return ""
    what = ", ".join(k["complaints"][c]["hi" if hi else "en"] for c in said)
    lines = [(f"Samjha: {what}. Excel mein iski aam wajah ye hoti hain:" if hi else
              f"Understood: {what}. In Excel this usually comes from one of these:")]
    lines += [f"- {_cap(NO.get(c, (c, c))[0 if hi else 1])}" for c in top]
    lines.append("")
    lines.append("File card mein daalo. Main aapki file mein check karke bataunga kaunsi wajah hai, saboot ke saath, aur theek kar dunga."
                 if hi else "Drop the file on the card. I'll check which one it is in your file, show you the proof, and fix it.")
    return "\n".join(lines)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]
