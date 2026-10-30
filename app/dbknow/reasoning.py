"""The database agent works out WHY, the same way for every database.

Three levels (app/dbknow/core + app/dbknow/dialects):
  1. fundamentals: the 5 layers (command, rules, capacity, many users, door) and their causes, in our own words;
  2. families: what only some kinds of database have (relational now);
  3. dialects: each database's own error codes, message formats and exact commands.
A new database = a new dialect file (and, for a new kind, a few family fundamentals). The reasoning doesn't change.

Evidence comes from what the person pastes (error text, codes, EXPLAIN, lock reports) and, when their database is
connected, from read-only checks on it. A cause is only stated when the evidence shows it (app/mind/reason.py);
otherwise the reply says what is likely and what to paste or check next. Words are read by meaning
(app/office/meaning.py with the database vocabulary), never by fixed sentences.
"""

import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

from app.mind import reason

HERE = Path(__file__).resolve().parent
CORE = HERE / "core"
DIALECTS = HERE / "dialects"
LEXICON = str(CORE / "lexicon.json")
ENGINE_NAMES = {"mysql": "MySQL", "mariadb": "MariaDB", "postgresql": "PostgreSQL"}


# ------------------------------------------------------------------ knowledge
@lru_cache(maxsize=1)
def fundamentals() -> dict:
    return json.loads((CORE / "fundamentals.json").read_text(encoding="utf-8"))


def _merge(base: dict, extra: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in extra.items():
        if k in ("codes", "fix", "settings", "live"):
            out.setdefault(k, {}).update(v)
        elif k == "messages":
            out["messages"] = v + out.get("messages", [])          # the specific engine's formats are tried first
        elif k == "words":
            out["words"] = v
        else:
            out[k] = v
    return out


@lru_cache(maxsize=None)
def _raw(name: str) -> dict:
    d = json.loads((DIALECTS / f"{name}.json").read_text(encoding="utf-8"))
    if d.get("extends"):
        d = _merge(_raw(d["extends"]), {k: v for k, v in d.items() if k != "extends"})
    return d


@lru_cache(maxsize=None)
def dialect(name: str) -> dict:
    d = json.loads(json.dumps(_raw(name)))
    for m in d.get("messages", []):
        m["_re"] = re.compile(m["re"], re.I)
    d["_codes"] = [re.compile(p, re.I) for p in d.get("code_patterns", [])]   # "error 1062" is as real as "ERROR 1062"
    return d


def engines() -> list[str]:
    return sorted(p.stem for p in DIALECTS.glob("*.json"))


# ------------------------------------------------------------------ reading what was pasted
def which_engine(text: str, found_in: list[str], facts: dict | None = None) -> str | None:
    """Named in the text; otherwise the database whose own message style and codes the text shows most."""
    low = (text or "").lower()
    named = [e for e in engines() if any(re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", low) for w in dialect(e)["words"])]
    if "mariadb" in named:
        return "mariadb"
    if named:
        return named[0]
    if not found_in:
        return None
    score = {}
    for e in found_in:
        d = dialect(e)
        style = sum(1 for rx in d.get("style", []) if re.search(rx, text or ""))
        own = sum(1 / max(1, len(f.get("engines", ()))) for f in (facts or {}).values() if e in f.get("engines", ()))
        score[e] = style * 2 + own
    if "mysql" in score and "mariadb" in score:
        score.pop("mariadb")                              # same formats; MariaDB only when it is named
    has_style = any(re.search(rx, text or "") for e in found_in for rx in dialect(e).get("style", []))
    has_code = any(f.get("codes") for f in (facts or {}).values())
    if not has_style and not has_code and len({e for e in found_in if e != "mariadb"}) <= 1:
        msg = next((f.get("proof", "") for f in (facts or {}).values()), "")
        if not re.search(r"['\"`]", msg) and len(msg) < 40:
            return None                                   # only a few words like "too many connections": not enough to know which database
    return max(sorted(score), key=lambda e: score[e])


def evidence(text: str) -> dict:
    """cause -> fact {seen, proof, entities, engine}. Message formats and codes are the database's own notation."""
    facts: dict[str, dict] = {}
    order = ["postgresql", "mariadb", "mysql"]
    for name in order:
        d = dialect(name)
        for m in d["messages"]:
            hit = m["_re"].search(text or "")
            if not hit:
                continue
            ent = {k: v.strip("`\"' ") for k, v in hit.groupdict().items() if v}
            f = facts.setdefault(m["cause"], {"seen": True, "proof": hit.group(0)[:220], "entities": {}, "engines": set(), "weight": 3})
            for k, v in ent.items():
                f["entities"].setdefault(k, v)
            f["engines"].add(name)
        for rx in d["_codes"]:
            for code in rx.findall(text or ""):
                cause = d["codes"].get(code.upper())
                if cause:
                    f = facts.setdefault(cause, {"seen": True, "proof": f"error code {code}", "entities": {}, "engines": set(), "weight": 2})
                    f["engines"].add(name)
                    f.setdefault("codes", set()).add(code.upper())
    # a bare code from a database whose own messages aren't in the text (SQLSTATE 40001/28000 are shared standards) proves nothing
    by_msg = {e for f in facts.values() if not f.get("codes") or f.get("proof", "").startswith("error code") is False for e in f["engines"]}
    for cid in [c for c, f in facts.items() if f.get("proof", "").startswith("error code")]:
        f = facts[cid]
        f["engines"] = {e for e in f["engines"] if e in by_msg or not by_msg}
        if not f["engines"]:
            facts.pop(cid)
    plan = explain_facts(text)
    if plan:
        facts["full_scan"] = plan
    # a specific reading wins over the general one it is a case of
    if "reserved_word" in facts:
        facts.pop("syntax_error", None)
    if "charset_mismatch" in facts:
        facts.pop("bad_value_type", None)
    if "ssl_required" in facts:
        facts.pop("host_not_allowed", None)
    if "fk_has_children" in facts and "fk_missing_parent" in facts and "is still referenced" in (text or "") or \
            ("fk_has_children" in facts and re.search(r"Cannot delete or update a parent row|update or delete on table", text or "", re.I)):
        facts.pop("fk_missing_parent", None)
    if "permission_denied" in facts and "access_denied" in facts and re.search(r"to database '|command denied", text or "", re.I):
        facts.pop("access_denied", None)
    if "too_many_connections" in facts or "access_denied" in facts:
        facts.pop("server_unreachable", None) if not re.search(r"refused|can't connect|could not connect", text or "", re.I) else None
    return facts


def explain_facts(text: str) -> dict | None:
    """A pasted EXPLAIN that reads a whole big table: MySQL 'type: ALL' / PostgreSQL 'Seq Scan'."""
    t = text or ""
    m = re.search(r"Seq Scan on (?P<table>\w+)[^\n]*?rows=(?P<rows>\d+)", t)
    if m and int(m.group("rows")) >= 1000:
        return {"seen": True, "proof": m.group(0)[:200], "entities": {"table": m.group("table"), "rows": m.group("rows")},
                "engines": {"postgresql"}, "weight": 2}
    rows = re.search(r"\|\s*\d+\s*\|\s*\w+\s*\|\s*(?P<table>\w+)\s*\|[^\n]*?\|\s*ALL\s*\|[^\n]*?\|\s*(?P<rows>\d{4,})\s*\|", t)
    if rows:
        return {"seen": True, "proof": rows.group(0).strip()[:200], "entities": {"table": rows.group("table"), "rows": rows.group("rows")},
                "engines": {"mysql"}, "weight": 2}
    m = re.search(r"table:\s*(?P<table>\w+)[\s\S]{0,200}?type:\s*ALL[\s\S]{0,200}?rows:\s*(?P<rows>\d{4,})", t, re.I)
    if m:
        return {"seen": True, "proof": "type: ALL, rows: " + m.group("rows"), "entities": {"table": m.group("table"), "rows": m.group("rows")},
                "engines": {"mysql"}, "weight": 2}
    return None


def complaints(text: str) -> dict[str, float]:
    """What the person says is wrong, by meaning: any words, any order, Hinglish, typos."""
    from app.office import meaning as M

    r = M.Reading(text, lex_path=LEXICON)
    lex = M.lexicon(LEXICON)
    return {cid: 1.0 for cid, recs in lex["complaints"].items() if any(M.satisfied(r, rec) for rec in recs)}


# ------------------------------------------------------------------ the reasoning
def diagnose(text: str, live: dict | None = None) -> dict:
    """live: facts from read-only checks on the connected database (cause -> fact), proving or ruling out causes."""
    k = fundamentals()
    facts = evidence(text)
    found_in = sorted({e for f in facts.values() for e in f.get("engines", ())})
    engine = which_engine(text, found_in, facts)
    said = complaints(text)
    if facts and not said:
        said = {c: 1.0 for f in facts for c in k["causes"][f]["explains"]}    # the error itself says what kind of problem it is
    all_facts = {cid: None for cid in k["causes"]}
    all_facts.update(facts)
    for cid, f in (live or {}).items():
        if f is None:
            continue
        prev = all_facts.get(cid) or {}
        merged = {**prev, **f, "entities": {**(prev.get("entities") or {}), **(f.get("entities") or {})}, "live": True}
        if prev.get("seen") and not f.get("seen"):
            # the database's own error already proved it; the live check only shows the state now
            merged["seen"] = True
            merged["live_proof"] = prev.get("proof")
        all_facts[cid] = merged
    result = reason.think(k, said, all_facts)
    seen_any = [cid for cid, f in all_facts.items() if f and f.get("seen")]
    # the database's own message is evidence even when the person described it differently
    for cid in seen_any:
        if cid not in {x["cause"] for x in result["found"]}:
            result["found"].append({"cause": cid, "relevance": 1.0, "score": 0.99, "fact": all_facts[cid], "state": "seen"})
    result["also"] = [x for x in result["also"] if x["cause"] not in seen_any]
    result.update({"engine": engine, "complaints": said, "live": bool(live)})
    return result


# ------------------------------------------------------------------ the report
SOFT = {"en": {"value": "that value", "table": "that table", "column": "that column", "parent": "the parent table", "child": "the child table",
               "token": "that point", "host": "the server", "port": "its port", "db": "that database", "key": "a unique key",
               "constraint": "a rule", "rows": "many", "user": "that user", "limit": "its limit", "parent_column": "its key"},
        "hi": {"value": "wo value", "table": "wo table", "column": "wo column", "parent": "parent table", "child": "child table",
               "token": "us jagah", "host": "server", "port": "uske port", "db": "wo database", "key": "ek unique key",
               "constraint": "ek rule", "rows": "bahut saari", "user": "wo user", "limit": "uski limit", "parent_column": "uski key"}}


def _say(tpl: str, ent: dict, hi: bool) -> str:
    """Explanations: a detail the message didn't give becomes plain words ('that value'), never a made-up name."""
    soft = SOFT["hi" if hi else "en"]
    out = re.sub(r"\{(\w+)\}", lambda m: str(ent.get(m.group(1)) or soft.get(m.group(1), "")), tpl)
    out = re.sub(r'"(that value|wo value|that table|wo table|that column|wo column|the parent table|parent table|that point|us jagah|that database|wo database|that user|wo user)"',
                 r"\1", out)
    # "The value that value already exists" / "column that column" -> "That value already exists" / "that column"
    fixed = re.sub(r"\b(?:[Tt]he )?(\w+) (that|wo) \1\b", r"\2 \1", out)
    return fixed[:1].upper() + fixed[1:] if out[:1].isupper() else fixed


def _fill(tpl: str, ent: dict) -> str:
    vals = dict(ent)
    vals.setdefault("word", (ent.get("token") or "").split()[0].lower() if ent.get("token") else "name")
    vals.setdefault("table_hint", (ent.get("table") or "").split(".")[-1][:20] or "name")
    vals.setdefault("host_pattern", ent.get("host") or "%")

    def sub(m):
        v = vals.get(m.group(1))
        return str(v) if v not in (None, "") else f"<{m.group(1).replace('_', ' ')}>"
    return re.sub(r"\{(\w+)\}", sub, tpl)


def _engine_for(cause: str, fact: dict, engine: str | None) -> str:
    eng = sorted(fact.get("engines") or []) if fact else []
    if engine and (not eng or engine in eng or (engine == "mariadb" and "mysql" in eng)):
        return engine
    return eng[0] if eng else (engine or "mysql")


def headline(result: dict, hi: bool) -> str:
    """A short name for what was found (the goal's title), or "" when nothing was pinned down."""
    found = sorted(result.get("found") or [], key=lambda x: (-x["score"], x["cause"]))
    if not found:
        return ""
    short = fundamentals()["causes"][found[0]["cause"]].get("short_" + ("hi" if hi else "en"), "")
    return short[:1].upper() + short[1:] if short else ""


def report(result: dict, hi: bool, signed_in: bool = False) -> str:
    k = fundamentals()
    causes, layers = k["causes"], k["layers"]
    L = "hi" if hi else "en"
    found = sorted(result["found"], key=lambda x: (-x["score"], x["cause"]))[:3]
    lines = []
    if not found:
        likely = [x["cause"] for x in result["open"][:4]]
        if not likely:
            return ""
        lines.append("Iski aam wajah ye hoti hain:" if hi else "This usually comes from one of these:")
        for cid in likely:
            c = causes[cid]
            lines.append(f"- {c['short_' + L]} ({layers[c['layer']][L].split(' (')[0]})")
        lines.append("")
        lines.append(("Pakki wajah ke liye poora error message (code ke saath) paste karo."
                      if hi else "To pin it down, paste the full error message (with its code).")
                     + ("" if signed_in else (" Ya free account banao, database jodo, aur main khud check karke saboot ke saath bataunga."
                                              if hi else " Or create a free account, connect your database, and I'll check it myself and prove it.")))
        return "\n".join(lines)
    eng = result.get("engine")
    for i, x in enumerate(found, start=1):
        c, fact = causes[x["cause"]], x["fact"] or {}
        ent = fact.get("entities") or {}
        e = _engine_for(x["cause"], fact, eng)
        d = dialect(e)
        if i > 1:
            lines.append("")
        lines.append(f"{i}. {_say(c['title_' + L], ent, hi)}")
        lines.append(f"- {'Layer' if not hi else 'Layer'}: {layers[c['layer']][L]}")
        lines.append(f"- {'Kyun' if hi else 'Why'}: {_say(c['why_' + L], ent, hi)}")
        proof = fact.get("live_proof") or fact.get("proof")
        if proof:
            lines.append(f"- {'Saboot' if hi else 'Proof'}: {proof}")
        targets = [e] if eng else ["mysql", "postgresql"]
        for te in targets:                                       # database not known: the fix for each, labelled
            fixes = dialect(te).get("fix", {}).get(x["cause"])
            if fixes:
                lines.append(f"- {'Theek karne ke liye' if hi else 'Fix'} ({ENGINE_NAMES.get(te, te)}{'/MariaDB' if te == 'mysql' and len(targets) > 1 else ''}):")
                lines.append("")
                lines.extend(_fill(fx, ent) + "\n" for fx in fixes)
        lines.append(f"- {'Pakka kaise' if hi else 'Check'}: {_say(c['confirm_' + L], ent, hi)}")
        lines.append(f"- {'Aage se' if hi else 'Prevent'}: {_say(c['prevent_' + L], ent, hi)}")
    lines.append("")
    if result.get("live"):
        lines.append("Aapke database pe check kiya gaya." if hi else "Checked on your database.")
    else:
        lines.append("Ye aapke paste kiye text pe based hai, aapke database pe check nahi kiya." if hi
                     else "Based on what you pasted, not checked on your database.")
        if not signed_in:
            lines.append("Free account banao aur database jodo: main khud check karke saboot ke saath pakka jawab dunga." if hi
                         else "Create a free account to connect your database and get a proven answer.")
        else:
            lines.append("Database area kholo aur database jodo: main khud check karke saboot ke saath bataunga." if hi
                         else "Open your database area and connect your database, and I'll check it there and prove it.")
    return "\n".join(lines).rstrip()


# ------------------------------------------------------------------ checks on a connected database (read-only)
def live_plan(result: dict) -> list[dict]:
    """The read-only checks that would prove (or rule out) the likely causes on the person's own database."""
    eng = result.get("engine") or "mysql"
    d = dialect(eng)
    plan = []
    candidates = result["found"] if result["found"] else result["open"][:3]   # checks for what was found, or else the likely causes
    for x in candidates:
        spec = d.get("live", {}).get(x["cause"])
        if not spec:
            continue
        ent = (x.get("fact") or {}).get("entities") or {}
        res = spec.get("resolve")
        can_resolve = res and all(ent.get(n) for n in res.get("needs", []))
        if any(not ent.get(n) for n in spec.get("needs", []) if not (can_resolve and n in res["sets"])):
            continue
        if spec["type"] == "closest_name" and not ent.get(spec["want"]):
            continue
        plan.append({"cause": x["cause"], "type": spec["type"], "spec": spec, "entities": ent, "engine": eng})
    return plan


def _sql(spec_sql: str, ent: dict) -> tuple[str, dict]:
    """Names go in quoted (only plain identifiers), values as parameters: nothing from the paste is run as SQL."""
    def ident(m):
        v = ent.get(m.group(1), "")
        if not re.fullmatch(r"[A-Za-z_][\w$]{0,63}", v or ""):
            raise ValueError(f"not a plain name: {m.group(1)}")
        return v
    sql = re.sub(r"\{(\w+)\}", ident, spec_sql)
    params = {n: ent.get(n) for n in re.findall(r"(?<!:):(\w+)", sql)}
    return re.sub(r"(?<!:):(\w+)", r"%(\1)s", sql), params          # pyformat: works with both MySQL and PostgreSQL drivers


def run_live(plan: list[dict], run) -> dict:
    """run(sql, params) -> list of rows (read-only). Returns cause -> fact (seen / ruled out) with the proof."""
    out = {}
    for p in plan:
        spec, ent = p["spec"], dict(p["entities"])
        res = spec.get("resolve")
        if res and any(not ent.get(n) for n in res["sets"]):
            try:
                rows = run(*_sql(res["sql"], ent))
            except Exception:  # noqa: BLE001
                rows = []
            if rows:
                for name, val in zip(res["sets"], rows[0]):
                    ent.setdefault(name, str(val).split(".")[-1].strip('"'))
        try:
            if p["type"] == "ratio":
                a = run(*_sql(spec["sql"][0], ent))
                b = run(*_sql(spec["sql"][1], ent))
                used, cap = float(a[0][0]), float(b[0][0])
                seen = cap > 0 and used / cap >= spec.get("above", 0.9)
                out[p["cause"]] = {"seen": seen, "live_proof": f"{int(used)} of {int(cap)} connections in use at the peak", "weight": 3}
                continue
            sql, params = _sql(spec["sql"], ent)
            rows = run(sql, params)
        except Exception as exc:  # noqa: BLE001 - a check that can't run proves nothing
            out[p["cause"]] = None
            continue
        t = p["type"]
        if t == "row_exists":
            out[p["cause"]] = {"seen": bool(rows), "weight": 4, "entities": {"table": ent.get("table"), "column": ent.get("column")},
                               "live_proof": f"the row with {ent.get('column')} = {ent.get('value')} is already in {ent.get('table')}" if rows else ""}
        elif t == "row_missing":
            out[p["cause"]] = {"seen": not rows, "weight": 4, "entities": {"parent": ent.get("parent"), "parent_column": ent.get("parent_column")},
                               "live_proof": f"{ent.get('parent')} has no row with {ent.get('parent_column')} = {ent.get('value')}" if not rows else ""}
        elif t == "closest_name":
            names = [str(r[0]) for r in rows]
            want = ent.get(spec["want"], "")
            exists = any(n.lower() == want.lower() for n in names)
            close = difflib.get_close_matches(want.lower(), [n.lower() for n in names], n=3, cutoff=0.6)
            out[p["cause"]] = {"seen": not exists, "weight": 4, "entities": {"suggest": ", ".join(close)},
                               "live_proof": (f"\"{want}\" isn't there" + (f"; closest real names: {', '.join(close)}" if close else ""))
                               if not exists else ""}
        elif t == "max_length":
            limit = rows[0][0] if rows else None
            out[p["cause"]] = {"seen": True, "weight": 3, "entities": {"limit": limit},
                               "live_proof": f"{ent.get('column')} allows {limit} characters" if limit else ""}
        elif t == "text_contains":
            text = " ".join(str(c) for r in rows for c in r)
            out[p["cause"]] = {"seen": spec["contains"] in text, "weight": 3,
                               "live_proof": "the database's status shows a recent deadlock" if spec["contains"] in text else ""}
        elif t == "count_positive":
            n = int(rows[0][0] or 0) if rows else 0
            out[p["cause"]] = {"seen": n > 0, "weight": 3, "live_proof": f"{n} deadlocks recorded on this database" if n else ""}
        elif t == "rows_present":
            out[p["cause"]] = {"seen": bool(rows), "weight": 3, "live_proof": f"{len(rows)} transaction(s) found waiting or holding locks now" if rows else ""}
    return out
