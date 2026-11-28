"""Generator for night S1: requests in words (English, Hinglish, Hindi) on random made-up schemas (tests/fuzz/db.py),
the query DBaba writes (app/agents/sqlwrite.py) run on MariaDB and PostgreSQL and compared with the generator's own
correct query, written here independently from the same choices.

A wrong result is a problem. An honest "I can't build that one yet" is allowed and counted (it must never be most of a
family). Tables keep their generated names (fz<seed>_orders); the request says the plain name ("orders"), the way a
person would.
Run: python -m tests.fuzz.nl2sql [count] [seed]
"""

from __future__ import annotations

import random
import re
import sys
from collections import Counter

from tests.fuzz.db import _norm_val, engines_up, make_schema

FAMILIES = ["cols_filter", "in_list", "between", "is_null", "not_null", "starts_with", "order_limit", "superlative",
            "distinct", "group_count", "group_agg", "group_having", "child_count", "anti_join", "above_avg", "duplicates",
            "parent_col", "count_rows",
            # night S2 (app/agents/sqlshapes.py)
            "self_cmp", "self_list", "self_none", "self_count", "pairs", "nth_value", "nth_group", "nth_rows", "top_per",
            "group_avg", "pct_group", "pct_count", "rank", "rank_nowin", "running", "prev_row", "update_pct", "update_set",
            "delete_where", "delete_dups"]
S2 = set(FAMILIES[FAMILIES.index("self_cmp"):])
ORDS = {2: ("2nd", "doosri", "दूसरी"), 3: ("3rd", "teesri", "तीसरी"), 4: ("4th", "chauthi", "चौथी"), 1: ("1st", "1st", "1st")}
LANGS = ["en", "en", "hinglish", "hindi"]


def _plain(t: dict) -> str:
    return re.sub(r"^fz\d+_", "", t["name"])


def _cols(t, *kinds):
    return [c for c in t["cols"] if c["kind"] in kinds and c.get("name")]


def _sing(w: str) -> str:
    if w.endswith("ies"):
        return w[:-3] + "y"
    if re.search(r"(?:ch|sh|x|ss)es$", w):
        return w[:-2]
    return w[:-1] if w.endswith("s") and not w.endswith("ss") else w


def _say(c: dict, rnd) -> str:
    n = c["name"]
    return n if rnd.random() < 0.6 or "_" not in n else n.replace("_", " ")


def _vals(t, c):
    i = t["cols"].index(c)
    return [r[i] for r in t["rows"] if r[i] is not None]


def make(seed: int, family: str | None = None):
    rnd = random.Random(seed)
    schema = make_schema(seed)
    if schema["style"] != "snake":
        return None                                   # camelCase names need quoting per engine: a later night
    tables = list(schema["tables"].values())
    fam = family or FAMILIES[seed % len(FAMILIES)]
    lang = rnd.choice(LANGS)
    t = rnd.choice(tables)
    T = _plain(t)
    item = {"seed": seed, "family": fam, "lang": lang, "schema": schema}

    def done(req, truth, cmp="bag", key=None):
        item.update(request=req, truth=truth, cmp=cmp, key=key)
        return item

    nums = _cols(t, "money", "int")
    # category values that are also everyday words ('in', 'out', 'on') are left out: a person would quote them
    cats = [c for c in _cols(t, "cat") if c["params"] and not any(v.lower() in ("in", "out", "on", "is", "or", "and", "no")
                                                                  for v in c["params"])]
    texts = _cols(t, "name", "cat", "email", "text")
    dates = _cols(t, "date", "datetime")
    pk = t["pk"]
    if fam == "cols_filter":
        if not nums or not texts:
            return None
        c, show = rnd.choice(nums), rnd.choice(texts)
        k = rnd.choice(_vals(t, c) or [10])
        k = int(k)
        op, en, hg, hd = rnd.choice([(">", "more than", "se zyada", "से ज़्यादा"), ("<", "less than", "se kam", "से कम"),
                                     (">=", "at least", "ya usse zyada", "या उससे ज़्यादा")])
        req = {"en": f"write a query to list {_say(show, rnd)} of {T} where {_say(c, rnd)} {en} {k}",
               "hinglish": f"{T} ka {_say(show, rnd)} dikhao jinka {_say(c, rnd)} {k} {hg} hai",
               "hindi": f"{T} का {_say(show, rnd)} दिखाओ जिनका {_say(c, rnd)} {k} {hd} है"}[lang]
        return done(req, f"SELECT {show['name']} FROM {t['name']} WHERE {c['name']} {op} {k}")
    if fam == "in_list":
        if not cats:
            return None
        c = rnd.choice(cats)
        vs = rnd.sample(c["params"], min(len(c["params"]), rnd.choice([2, 3])))
        if len(vs) < 2:
            return None
        joined = {"en": ", ".join(vs[:-1]) + " or " + vs[-1], "hinglish": ", ".join(vs[:-1]) + " ya " + vs[-1],
                  "hindi": ", ".join(vs[:-1]) + " या " + vs[-1]}[lang]
        req = {"en": f"show {T} where {_say(c, rnd)} is {joined}", "hinglish": f"{T} dikhao jinka {_say(c, rnd)} {joined} hai",
               "hindi": f"{T} दिखाओ जिनका {_say(c, rnd)} {joined} है"}[lang]
        inl = ", ".join("'" + v.replace("'", "''") + "'" for v in vs)
        return done(req, f"SELECT * FROM {t['name']} WHERE {c['name']} IN ({inl})")
    if fam == "between":
        if not nums:
            return None
        c = rnd.choice(nums)
        lo, hi = sorted(rnd.sample(sorted(set(int(v) for v in _vals(t, c))) or [0, 1], 2)) if len(set(_vals(t, c))) > 1 else (0, 1)
        req = {"en": f"find {T} with {_say(c, rnd)} between {lo} and {hi}",
               "hinglish": f"{T} dikhao jinka {_say(c, rnd)} between {lo} and {hi} hai",
               "hindi": f"{T} दिखाओ जिनका {_say(c, rnd)} between {lo} and {hi} है"}[lang]
        return done(req, f"SELECT * FROM {t['name']} WHERE {c['name']} BETWEEN {lo} AND {hi}")
    if fam in ("is_null", "not_null"):
        cand = [c for c in t["cols"] if c["kind"] not in ("pk",) and c.get("name")]
        if not cand:
            return None
        c = rnd.choice(cand)
        if fam == "is_null":
            req = {"en": f"list {T} where {_say(c, rnd)} is missing", "hinglish": f"{T} dikhao jinka {_say(c, rnd)} khali hai",
                   "hindi": f"{T} दिखाओ जिनका {_say(c, rnd)} खाली है"}[lang]
            return done(req, f"SELECT * FROM {t['name']} WHERE {c['name']} IS NULL")
        req = {"en": f"get {T} that have a non-null {_say(c, rnd)}", "hinglish": f"{T} dikhao jinka {_say(c, rnd)} is not null",
               "hindi": f"{T} दिखाओ जिनका {_say(c, rnd)} is not null"}[lang]
        return done(req, f"SELECT * FROM {t['name']} WHERE {c['name']} IS NOT NULL")
    if fam == "starts_with":
        cand = [c for c in _cols(t, "name", "cat") if _vals(t, c)]
        if not cand:
            return None
        c = rnd.choice(cand)
        word = str(rnd.choice(_vals(t, c)))[:rnd.randint(1, 3)]
        if not re.fullmatch(r"[A-Za-z]+", word):
            return None
        req = {"en": f"show {T} whose {_say(c, rnd)} starts with {word}", "hinglish": f"{T} dikhao jinka {_say(c, rnd)} starts with {word}",
               "hindi": f"{T} दिखाओ जिनका {_say(c, rnd)} starts with {word}"}[lang]
        return done(req, f"SELECT * FROM {t['name']} WHERE {c['name']} LIKE '{word}%'")
    if fam == "order_limit":
        if not nums:
            return None
        c = rnd.choice(nums)
        n = rnd.randint(1, 8)
        desc = rnd.random() < 0.5
        dw = {"en": ("highest first", "lowest first"), "hinglish": ("highest first", "lowest first"), "hindi": ("highest first", "lowest first")}
        req = {"en": f"show top {n} {T} sorted by {_say(c, rnd)} {dw['en'][0 if desc else 1]}",
               "hinglish": f"{T} ke top {n} dikhao, {_say(c, rnd)} ke hisaab se sort karo {dw['en'][0 if desc else 1]}",
               "hindi": f"top {n} {T} दिखाओ sorted by {_say(c, rnd)} {dw['en'][0 if desc else 1]}"}[lang]
        return done(req, f"SELECT * FROM {t['name']} ORDER BY {c['name']} {'DESC' if desc else 'ASC'} LIMIT {n}", "key", c["name"])
    if fam == "superlative":
        if not dates:
            return None
        c = rnd.choice(dates)
        n = rnd.randint(1, 6)
        newest = rnd.random() < 0.5
        word = ("newest", "latest") if newest else ("oldest", "earliest")
        if len(dates) > 1:
            req = {"en": f"list the {n} {word[0]} {T} by {_say(c, rnd)}", "hinglish": f"{n} sabse {'naye' if newest else 'purane'} {T} dikhao sorted by {_say(c, rnd)} {word[1]} first",
                   "hindi": f"top {n} {T} दिखाओ sorted by {_say(c, rnd)} {word[1]} first"}[lang]
            req = req if lang != "en" else f"show top {n} {T} sorted by {_say(c, rnd)} {word[0]} first"
        else:
            req = {"en": f"show the {word[0]} {n} {T}", "hinglish": f"top {n} {T} dikhao, {word[0]} first",
                   "hindi": f"top {n} {T} दिखाओ, {word[0]} first"}[lang]
        return done(req, f"SELECT * FROM {t['name']} ORDER BY {c['name']} {'DESC' if newest else 'ASC'} LIMIT {n}", "key", c["name"])
    if fam == "distinct":
        if not cats:
            return None
        c = rnd.choice(cats)
        up = rnd.random() < 0.4
        req = {"en": f"list all unique {_say(c, rnd)} values from {T}" + (" in uppercase" if up else ""),
               "hinglish": f"{T} se alag alag {_say(c, rnd)} dikhao" + (" in uppercase" if up else ""),
               "hindi": f"{T} से अलग अलग {_say(c, rnd)} दिखाओ" + (" in uppercase" if up else "")}[lang]
        e = f"UPPER({c['name']})" if up else c["name"]
        return done(req, f"SELECT DISTINCT {e} FROM {t['name']}")
    if fam in ("group_count", "group_agg", "group_having"):
        if not cats:
            return None
        g = rnd.choice(cats)
        if fam == "group_count":
            req = {"en": f"count of {T} per {_say(g, rnd)}", "hinglish": f"har {_say(g, rnd)} ke kitne {T} hain",
                   "hindi": f"हर {_say(g, rnd)} के कितने {T} हैं"}[lang]
            return done(req, f"SELECT {g['name']}, COUNT(*) FROM {t['name']} GROUP BY {g['name']}")
        if not nums:
            return None
        m = rnd.choice(nums)
        fn, en, hg, hd = rnd.choice([("AVG", "average", "ausat", "औसत"), ("SUM", "total", "kul", "कुल")])
        if fam == "group_agg":
            req = {"en": f"show the {en} {_say(m, rnd)} of {T} for each {_say(g, rnd)}",
                   "hinglish": f"har {_say(g, rnd)} ka {hg} {_say(m, rnd)} dikhao from {T}",
                   "hindi": f"हर {_say(g, rnd)} का {hd} {_say(m, rnd)} दिखाओ from {T}"}[lang]
            return done(req, f"SELECT {g['name']}, {fn}({m['name']}) FROM {t['name']} GROUP BY {g['name']}", "bag2")
        k = rnd.randint(1, 6)
        req = {"en": f"{en} {_say(m, rnd)} per {_say(g, rnd)} in {T}, only groups with more than {k} rows",
               "hinglish": f"{T} mein har {_say(g, rnd)} ka {hg} {_say(m, rnd)}, only groups with more than {k} rows",
               "hindi": f"{T} में हर {_say(g, rnd)} का {hd} {_say(m, rnd)}, only groups with more than {k} rows"}[lang]
        return done(req, f"SELECT {g['name']}, {fn}({m['name']}) FROM {t['name']} GROUP BY {g['name']} HAVING COUNT(*) > {k}", "bag2")
    pairs = [(schema["tables"][c["ref"]], x, c) for x in tables for c in x["cols"] if c["kind"] == "fk"]
    if fam in ("child_count", "anti_join", "parent_col"):
        if not pairs:
            return None
        p, ch, fk = rnd.choice(pairs)
        P, C = _plain(p), _plain(ch)
        if fam == "anti_join":
            req = {"en": f"list {P} that have no {C}", "hinglish": f"aise {P} dikhao jinke koi {C} nahi hai",
                   "hindi": f"ऐसे {P} दिखाओ जिनके कोई {C} नहीं है"}[lang]
            truth = (f"SELECT * FROM {p['name']} pp WHERE NOT EXISTS (SELECT 1 FROM {ch['name']} cc "
                     f"WHERE cc.{fk['name']} = pp.{p['pk']})")
            return done(req, truth)
        pcats = [c for c in _cols(p, "cat", "name") if c["name"]]
        if not pcats:
            return None
        pc = rnd.choice(pcats)
        if fam == "child_count":
            keep = rnd.random() < 0.6
            req = {"en": f"each {_say(pc, rnd)} of {P} and its {_sing(C)} count" + (", including those with none" if keep else ""),
                   "hinglish": f"har {P} ka {_say(pc, rnd)} aur uske {C} count dikhao" + (", zero wale bhi" if keep else ""),
                   "hindi": f"हर {P} का {_say(pc, rnd)} और उसके {C} count दिखाओ" + (", zero वाले भी" if keep else "")}[lang]
            j = "LEFT JOIN"                           # "each X and its Y count": every X, 0 where it has none
            truth = (f"SELECT pp.{pc['name']}, COUNT(cc.{ch['pk']}) FROM {p['name']} pp {j} {ch['name']} cc "
                     f"ON cc.{fk['name']} = pp.{p['pk']} GROUP BY pp.{p['pk']}, pp.{pc['name']}")
            item["keep_all"] = keep
            return done(req, truth)
        req = {"en": f"list {C} with their {_sing(P)} {_say(pc, rnd)}", "hinglish": f"{C} dikhao unke {_sing(P)} {_say(pc, rnd)} ke saath",
               "hindi": f"{C} दिखाओ उनके {_sing(P)} {_say(pc, rnd)} के साथ"}[lang]
        truth = f"SELECT cc.*, pp.{pc['name']} FROM {ch['name']} cc JOIN {p['name']} pp ON cc.{fk['name']} = pp.{p['pk']}"
        return done(req, truth, "col", pc["name"])
    if fam == "above_avg":
        if not nums:
            return None
        c = rnd.choice(nums)
        req = {"en": f"find {T} whose {_say(c, rnd)} is above the average", "hinglish": f"{T} dikhao jinka {_say(c, rnd)} average se zyada hai",
               "hindi": f"{T} दिखाओ जिनका {_say(c, rnd)} average से ज़्यादा है"}[lang]
        return done(req, f"SELECT * FROM {t['name']} WHERE {c['name']} > (SELECT AVG({c['name']}) FROM {t['name']})")
    if fam == "duplicates":
        cand = _cols(t, "cat", "name", "email")
        if not cand:
            return None
        c = rnd.choice(cand)
        req = {"en": f"find duplicate {_say(c, rnd)} values in {T} with how many times each appears",
               "hinglish": f"{T} mein duplicate {_say(c, rnd)} dikhao, kitni baar aaye",
               "hindi": f"{T} में duplicate {_say(c, rnd)} दिखाओ"}[lang]
        return done(req, f"SELECT {c['name']}, COUNT(*) FROM {t['name']} GROUP BY {c['name']} HAVING COUNT(*) > 1", "bag2")
    if fam == "count_rows":
        if not cats:
            return None
        c = rnd.choice(cats)
        v = rnd.choice(c["params"])
        req = {"en": f"how many {T} have {_say(c, rnd)} = '{v}'", "hinglish": f"kitne {T} hain jinka {_say(c, rnd)} '{v}' hai",
               "hindi": f"कितने {T} हैं जिनका {_say(c, rnd)} '{v}' है"}[lang]
        return done(req, f"SELECT COUNT(*) FROM {t['name']} WHERE {c['name']} = '" + v.replace("'", "''") + "'", "bag")
    if fam in S2:
        return _make_s2(item, fam, rnd, schema, tables, t, T, lang, nums, cats, texts, dates, pk, done)
    return None


def _self_col(t, rnd) -> str:
    """Adds a column pointing at the same table (no constraint: rows are loaded in any order) and fills it."""
    role = rnd.choice(["manager", "lead", "mentor", "supervisor"])
    name = f"{role}_id"
    t["cols"].append({"name": name, "kind": "fk", "params": t["name"], "ref": t["name"]})
    n = len(t["rows"])
    for k, row in enumerate(t["rows"]):
        others = [i for i in range(1, n + 1) if i != k + 1]
        row.append(None if rnd.random() < 0.15 or not others else rnd.choice(others[:max(3, n // 4)]))
    return role


def _make_s2(item, fam, rnd, schema, tables, t, T, lang, nums, cats, texts, dates, pk, done):
    if fam.startswith("delete") and any(p == t["name"] for x in tables for _c, p, _pk in x["fks"]):
        return None                                   # a parent row its children's FOREIGN KEY protects: not this test
    tn = t["name"]
    sing = _sing(T)
    names = [c for c in _cols(t, "name")]
    if fam in ("self_cmp", "self_list", "self_none", "self_count"):
        if not names or not nums:
            return None
        role = _self_col(t, rnd)
        ref = f"{role}_id"
        v, nm = rnd.choice(nums), names[0]
        if fam == "self_cmp":
            more = rnd.random() < 0.6
            d = rnd.choice(dates) if dates and rnd.random() < 0.4 else None
            req = {"en": f"list {T} whose {_say(v, rnd)} is {'more' if more else 'less'} than their {role}'s"
                         + (f" and whose {_say(d, rnd)} is before their {role}'s" if d else ""),
                   "hinglish": f"{T} dikhao jinka {_say(v, rnd)} unke {role} se {'zyada' if more else 'kam'} hai",
                   "hindi": f"{T} दिखाओ जिनका {_say(v, rnd)} उनके {role} से {'ज़्यादा' if more else 'कम'} है"}[lang]
            if lang != "en":
                d = None
            truth = (f"SELECT c.{pk} FROM {tn} c JOIN {tn} p ON c.{ref} = p.{pk} WHERE c.{v['name']} {'>' if more else '<'} p.{v['name']}"
                     + (f" AND c.{d['name']} < p.{d['name']}" if d else ""))
            return done(req, truth, "col", pk)
        if fam == "self_list":
            keep = rnd.random() < 0.5
            req = {"en": f"show {_say(nm, rnd)} of {'each ' + sing if keep else T} and their {role}'s {_say(nm, rnd)}"
                         + (", including those without a " + role if keep and rnd.random() < 0.5 else ""),
                   "hinglish": f"{'har ' + sing if keep else T} ka {_say(nm, rnd)} aur unke {role} ka {_say(nm, rnd)} dikhao",
                   "hindi": f"{'हर ' + sing if keep else T} का {_say(nm, rnd)} और उनके {role} का {_say(nm, rnd)} दिखाओ"}[lang]
            truth = (f"SELECT c.{nm['name']}, p.{nm['name']} FROM {tn} c {'LEFT JOIN' if keep else 'JOIN'} {tn} p "
                     f"ON c.{ref} = p.{pk}")
            return done(req, truth, "bag2")
        if fam == "self_none":
            req = {"en": f"list {T} without a {role}", "hinglish": f"aise {T} dikhao jinka koi {role} nahi hai",
                   "hindi": f"ऐसे {T} दिखाओ जिनका कोई {role} नहीं है"}[lang]
            return done(req, f"SELECT {pk} FROM {tn} WHERE {ref} IS NULL", "col", pk)
        req = {"en": f"count of direct reports for each {role} in {T}", "hinglish": f"count of direct reports for each {role} in {T}",
               "hindi": f"count of direct reports for each {role} in {T}"}[lang]
        return done(req, f"SELECT p.{pk}, COUNT(*) FROM {tn} p JOIN {tn} c ON c.{ref} = p.{pk} GROUP BY p.{pk}", "fl")
    if fam == "pairs":
        if not cats or not names:
            return None
        a = rnd.choice(cats)
        b = rnd.choice([c for c in cats + nums if c is not a]) if len(cats + nums) > 1 and rnd.random() < 0.5 else None
        req = {"en": f"find pairs of {T} with the same {_say(a, rnd)}" + (f" and the same {_say(b, rnd)}" if b else ""),
               "hinglish": f"{T} ke ek jaisi {_say(a, rnd)} wale jode dikhao",
               "hindi": f"{T} के एक जैसी {_say(a, rnd)} वाले जोड़े दिखाओ"}[lang]
        b = b if lang == "en" else None
        on = f"a.{a['name']} = b.{a['name']}" + (f" AND a.{b['name']} = b.{b['name']}" if b else "")
        return done(req, f"SELECT a.*, b.* FROM {tn} a JOIN {tn} b ON {on} AND a.{pk} < b.{pk}", "pairs")
    if fam in ("nth_value", "nth_group", "nth_rows"):
        if not nums:
            return None
        v = rnd.choice(nums)
        n = rnd.choice([1, 2, 2, 3, 3, 4])
        high = rnd.random() < 0.7
        o = ORDS[n]
        hw = {"en": "highest" if high else "lowest", "hinglish": "sabse zyada" if high else "sabse kam",
              "hindi": "सबसे ज़्यादा" if high else "सबसे कम"}[lang]
        rank = f"DENSE_RANK() OVER (ORDER BY {v['name']} {'DESC' if high else 'ASC'})"
        if fam == "nth_value":
            req = {"en": f"find the {o[0]} {hw} distinct {_say(v, rnd)} in {T}", "hinglish": f"{T} mein {o[1]} {hw} {_say(v, rnd)} dikhao",
                   "hindi": f"{T} में {o[2]} {hw} {_say(v, rnd)} दिखाओ"}[lang]
            return done(req, f"SELECT DISTINCT {v['name']} FROM (SELECT {v['name']}, {rank} AS rk FROM {tn} WHERE {v['name']} IS NOT NULL) x "
                             f"WHERE rk = {n}")
        if fam == "nth_rows":
            req = {"en": f"show {T} who have the {o[0]} {hw} {_say(v, rnd)}", "hinglish": f"{o[1]} {hw} {_say(v, rnd)} wale {T} dikhao",
                   "hindi": f"{o[2]} {hw} {_say(v, rnd)} वाले {T} दिखाओ"}[lang]
            return done(req, f"SELECT {pk} FROM (SELECT {pk}, {rank} AS rk FROM {tn} WHERE {v['name']} IS NOT NULL) x WHERE rk = {n}",
                        "col", pk)
        if not cats:
            return None
        g = rnd.choice(cats)
        req = {"en": f"find the {o[0]} {hw} {_say(v, rnd)} in each {_say(g, rnd)} from {T}",
               "hinglish": f"{T} mein har {_say(g, rnd)} ki {o[1]} {hw} {_say(v, rnd)} dikhao",
               "hindi": f"{T} में हर {_say(g, rnd)} की {o[2]} {hw} {_say(v, rnd)} दिखाओ"}[lang]
        rank = f"DENSE_RANK() OVER (PARTITION BY {g['name']} ORDER BY {v['name']} {'DESC' if high else 'ASC'})"
        return done(req, f"SELECT DISTINCT {g['name']}, {v['name']} FROM (SELECT {g['name']}, {v['name']}, {rank} AS rk FROM {tn} "
                         f"WHERE {v['name']} IS NOT NULL AND {g['name']} IS NOT NULL) x WHERE rk = {n}")
    if fam == "top_per":
        if not nums or not cats:
            return None
        v, g, n = rnd.choice(nums), rnd.choice(cats), rnd.randint(1, 4)
        req = {"en": f"top {n} {T} by {_say(v, rnd)} in each {_say(g, rnd)}",
               "hinglish": f"har {_say(g, rnd)} ke top {n} {T} dikhao by {_say(v, rnd)}",
               "hindi": f"हर {_say(g, rnd)} के top {n} {T} दिखाओ by {_say(v, rnd)}"}[lang]
        return done(req, f"SELECT {pk} FROM (SELECT {pk}, RANK() OVER (PARTITION BY {g['name']} ORDER BY {v['name']} DESC) AS rk "
                         f"FROM {tn} WHERE {v['name']} IS NOT NULL AND {g['name']} IS NOT NULL) x WHERE rk <= {n}", "col", pk)
    pairs_ = [(schema["tables"][c["ref"]], x, c) for x in tables for c in x["cols"] if c["kind"] == "fk"]
    if fam == "group_avg":
        cand = [(p, ch, fk, v) for p, ch, fk in pairs_ for v in _cols(ch, "money", "int")
                if not any(c.get("name") == v["name"] for c in p["cols"])]
        if not cand:
            return None
        p, ch, fk, v = rnd.choice(cand)
        P = _plain(p)
        above = rnd.random() < 0.6
        req = {"en": f"{P} whose average {_say(v, rnd)} is {'higher' if above else 'lower'} than the overall average",
               "hinglish": f"{P} dikhao jinka average {_say(v, rnd)} overall average se {'zyada' if above else 'kam'} hai",
               "hindi": f"{P} दिखाओ जिनका average {_say(v, rnd)} overall average से {'ज़्यादा' if above else 'कम'} है"}[lang]
        return done(req, f"SELECT pp.{p['pk']} FROM {p['name']} pp JOIN {ch['name']} cc ON cc.{fk['name']} = pp.{p['pk']} "
                         f"GROUP BY pp.{p['pk']} HAVING AVG(cc.{v['name']}) {'>' if above else '<'} (SELECT AVG({v['name']}) FROM {ch['name']})",
                    "col", p["pk"])
    if fam in ("pct_group", "pct_count"):
        if not cats or (fam == "pct_group" and not _cols(t, "money")):
            return None
        g = rnd.choice(cats)
        if fam == "pct_group":
            v = rnd.choice(_cols(t, "money"))
            req = {"en": f"percentage of total {_say(v, rnd)} per {_say(g, rnd)} in {T}",
                   "hinglish": f"har {_say(g, rnd)} ka total {_say(v, rnd)} mein pratishat dikhao from {T}",
                   "hindi": f"हर {_say(g, rnd)} का total {_say(v, rnd)} में प्रतिशत दिखाओ from {T}"}[lang]
            return done(req, f"SELECT {g['name']}, ROUND(100.0 * SUM({v['name']}) / (SELECT SUM({v['name']}) FROM {tn}), 2) "
                             f"FROM {tn} GROUP BY {g['name']}", "fl")
        req = {"en": f"what percentage of {T} are in each {_say(g, rnd)}", "hinglish": f"har {_say(g, rnd)} ke {T} ka pratishat dikhao",
               "hindi": f"हर {_say(g, rnd)} के {T} का प्रतिशत दिखाओ"}[lang]
        return done(req, f"SELECT {g['name']}, ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM {tn}), 2) FROM {tn} GROUP BY {g['name']}", "fl")
    if fam in ("rank", "rank_nowin"):
        if not nums:
            return None
        v = rnd.choice(nums)
        g = rnd.choice(cats) if cats and rnd.random() < 0.6 else None
        dense = rnd.random() < 0.3
        word = "dense rank" if dense else "rank"
        nowin = " without window functions" if fam == "rank_nowin" else ""
        req = {"en": f"{word} {T} by {_say(v, rnd)}" + (f" within each {_say(g, rnd)}" if g else "") + nowin,
               "hinglish": (f"har {_say(g, rnd)} mein " if g else "") + f"{T} ko {_say(v, rnd)} ke hisaab se {word} karo" + nowin,
               "hindi": (f"हर {_say(g, rnd)} में " if g else "") + f"{T} को {_say(v, rnd)} के हिसाब से {word} करो" + nowin}[lang]
        fn = "DENSE_RANK" if dense else "RANK"
        part = f"PARTITION BY {g['name']} " if g else ""
        return done(req, f"SELECT {pk}, {fn}() OVER ({part}ORDER BY {v['name']} DESC) AS rank_no FROM {tn} WHERE {v['name']} IS NOT NULL"
                         + (f" AND {g['name']} IS NOT NULL" if g else ""),
                    "map", (pk, "rank_no"))
    if fam in ("running", "prev_row"):
        if not nums or not dates:
            return None
        v, d = rnd.choice(nums), rnd.choice(dates)
        if fam == "running":
            req = {"en": f"running total of {_say(v, rnd)} in {T} by {_say(d, rnd)}",
                   "hinglish": f"{T} ka running total of {_say(v, rnd)} by {_say(d, rnd)} dikhao",
                   "hindi": f"{T} का running total of {_say(v, rnd)} by {_say(d, rnd)} दिखाओ"}[lang]
            return done(req, f"SELECT {pk}, SUM({v['name']}) OVER (ORDER BY {d['name']}, {pk} ROWS BETWEEN UNBOUNDED PRECEDING AND "
                             f"CURRENT ROW) AS running_total FROM {tn}", "map", (pk, "running_total"))
        req = {"en": f"each {sing} of {T} with the previous {sing}'s {_say(v, rnd)}, ordered by {_say(d, rnd)}",
               "hinglish": f"{T} dikhao with the previous {sing}'s {_say(v, rnd)}, ordered by {_say(d, rnd)}",
               "hindi": f"{T} दिखाओ with the previous {sing}'s {_say(v, rnd)}, ordered by {_say(d, rnd)}"}[lang]
        return done(req, f"SELECT {pk}, LAG({v['name']}) OVER (ORDER BY {d['name']}, {pk}) AS previous_{v['name']} FROM {tn}",
                    "map", (pk, f"previous_{v['name']}"))
    if fam == "update_pct":
        money = _cols(t, "money")
        if not money or not cats:
            return None
        v, c = rnd.choice(money), rnd.choice(cats)
        val, k, up = rnd.choice(c["params"]), rnd.choice([5, 10, 12.5, 20]), rnd.random() < 0.7
        ks = f"{k:g}"
        req = {"en": f"{'increase' if up else 'decrease'} {_say(v, rnd)} of {T} by {ks}% where {_say(c, rnd)} is '{val}'",
               "hinglish": f"{T} ka {_say(v, rnd)} {ks}% {'badhao' if up else 'ghatao'} jinka {_say(c, rnd)} '{val}' hai",
               "hindi": f"{T} का {_say(v, rnd)} {ks}% {'बढ़ाओ' if up else 'घटाओ'} जिनका {_say(c, rnd)} '{val}' है"}[lang]
        f = 1 + k / 100 if up else 1 - k / 100
        item["state"] = f"SELECT {pk}, {v['name']} FROM {tn}"
        return done(req, f"SELECT {pk}, CASE WHEN {c['name']} = '{val}' THEN ROUND({v['name']} * {f}, 2) ELSE {v['name']} END FROM {tn}",
                    "bag")
    if fam == "update_set":
        if not cats or not nums:
            return None
        c, v = rnd.choice(cats), rnd.choice(nums)
        new = rnd.choice(c["params"])
        k = int(rnd.choice(_vals(t, v) or [10]))
        req = f"set {_say(c, rnd)} to '{new}' for {T} where {_say(v, rnd)} is less than {k}"
        item["state"] = f"SELECT {pk}, {c['name']} FROM {tn}"
        return done(req, f"SELECT {pk}, CASE WHEN {v['name']} < {k} THEN '{new}' ELSE {c['name']} END FROM {tn}", "bag")
    if fam == "delete_where":
        if not cats:
            return None
        c = rnd.choice(cats)
        val = rnd.choice(c["params"])
        req = {"en": f"delete {T} where {_say(c, rnd)} is '{val}'", "hinglish": f"{T} hatao jinka {_say(c, rnd)} '{val}' hai",
               "hindi": f"{T} हटाओ जिनका {_say(c, rnd)} '{val}' है"}[lang]
        item["state"] = f"SELECT {pk} FROM {tn}"
        return done(req, f"SELECT {pk} FROM {tn} WHERE {c['name']} IS NULL OR {c['name']} <> '{val}'", "bag")
    if fam == "delete_dups":
        cand = _cols(t, "cat", "email", "name")
        if not cand:
            return None
        c = rnd.choice(cand)
        newest = rnd.random() < 0.4
        req = f"delete duplicate {T} on {_say(c, rnd)} keeping the {'highest' if newest else 'lowest'} {pk}"
        item["state"] = f"SELECT {pk} FROM {tn}"
        return done(req, f"SELECT {pk} FROM {tn} x WHERE {c['name']} IS NULL OR {pk} = (SELECT {'MAX' if newest else 'MIN'}({pk}) "
                         f"FROM {tn} y WHERE y.{c['name']} = x.{c['name']})", "bag")
    return None


def _run(eng, sql):
    cur = eng.conn.cursor()
    try:
        cur.execute(sql)
        names = [d[0].lower() for d in cur.description]
        return names, [tuple(_norm_val(v) for v in r) for r in cur.fetchall()]
    finally:
        cur.close()


def _same(item, got, want) -> bool:
    (gn, gr), (wn, wr) = got, want
    if item["cmp"] == "key":                             # top N by a column: the same values in the same order (ties free)
        if item["key"].lower() not in gn:
            return False
        gi, wi = gn.index(item["key"].lower()), wn.index(item["key"].lower())
        return [r[gi] for r in gr] == [r[wi] for r in wr]
    if item["cmp"] == "col":                             # the planted column's values, and the same number of rows
        k = item["key"].lower()
        if k not in gn:
            return False
        return Counter(r[gn.index(k)] for r in gr) == Counter(r[wn.index(k)] for r in wr) and len(gr) == len(wr)
    if item["cmp"] == "fl":                              # a group and its number: the first and the last column
        return Counter((r[0], r[-1]) for r in gr) == Counter((r[0], r[-1]) for r in wr)
    if item["cmp"] == "map":                             # row key -> a computed column
        k, v = item["key"]
        if k.lower() not in gn or v.lower() not in gn:
            return False
        return Counter((r[gn.index(k.lower())], r[gn.index(v.lower())]) for r in gr) == \
            Counter((r[wn.index(k.lower())], r[wn.index(v.lower())]) for r in wr)
    if item["cmp"] == "pairs":                           # (a, b) of the column the answer shows, each pair once
        if len(gn) < 2 or not gn[0].endswith("_1"):
            return False
        base = gn[0][:-2]
        idx = [i for i, n in enumerate(wn) if n == base]
        if len(idx) != 2:
            return False
        return Counter((r[0], r[1]) for r in gr) == Counter((r[idx[0]], r[idx[1]]) for r in wr)
    if item["cmp"] == "bag2":                            # group value + total: the first two columns
        return Counter(r[:2] for r in gr) == Counter(r[:2] for r in wr) and all(len(r) >= 2 for r in gr)
    return Counter(gr) == Counter(wr)


def check(item, engines) -> tuple[list[str], bool]:
    from app.products.dbaba import sqlwrite

    res = sqlwrite.build(item["request"], item["schema"])
    tag = f"{item['family']} ({item['lang']}) seed {item['seed']}"
    if not res["ok"]:
        return [], False
    sql = re.sub(r"\n-- check:.*$", "", res["sql"])
    bad = []
    if res.get("guesses"):
        bad.append(f"{tag}: guessed names although the schema was given {res['guesses']}\n{item['request']}\n{sql}")
    for name, eng in engines.items():
        try:
            if item.get("state"):                        # a change: the table after it (inside a transaction, rolled back)
                want = _run(eng, item["truth"])
                cur = eng.conn.cursor()
                cur.execute("BEGIN")
                try:
                    cur.execute(sql)
                    got = _run(eng, item["state"])
                finally:
                    cur.execute("ROLLBACK")
                    cur.close()
            else:
                got, want = _run(eng, sql), _run(eng, item["truth"])
        except Exception as exc:  # noqa: BLE001
            eng.rollback()
            bad.append(f"{tag}: on {name} the query fails: {str(exc).splitlines()[0][:120]}\n{item['request']}\n{sql}")
            continue
        if not _same(item, got, want):
            bad.append(f"{tag}: on {name} other rows ({len(got[1])} vs {len(want[1])})\n{item['request']}\n{sql}\n--\n{item['truth']}")
    return bad, True


def run(count=200, seed=1, engines=None, family=None):
    engines = engines_up() if engines is None else engines
    fails, made, built, by, built_by = [], 0, 0, Counter(), Counter()
    for s in range(seed, seed + count * 8):
        if made >= count:
            break
        item = make(s, family)
        if item is None:
            continue
        made += 1
        by[item["family"]] += 1
        for eng in engines.values():
            eng.create(item["schema"])
        try:
            bad, ok = check(item, engines)
        finally:
            for eng in engines.values():
                eng.drop(item["schema"]["order"])
        fails += bad
        if ok:
            built += 1
            built_by[item["family"]] += 1
    return made, built, fails, by, built_by, engines


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    family = sys.argv[3] if len(sys.argv) > 3 else None
    made, built, fails, by, built_by, engines = run(count, seed, family=family)
    for f in fails[:25]:
        print(f, "\n")
    cant = {f: by[f] - built_by[f] for f in by if by[f] - built_by[f]}
    print("built per family:", {f: f"{built_by[f]}/{by[f]}" for f in sorted(by)})
    print(f"{made} requests, {built} built, {made - built} honest 'can't build yet' {cant}, {len(fails)} problems; "
          f"checked on: {', '.join(engines) or 'no database'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
