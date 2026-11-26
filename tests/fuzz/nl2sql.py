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
            "parent_col", "count_rows"]
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
    if item["cmp"] == "bag2":                            # group value + total: the first two columns
        return Counter(r[:2] for r in gr) == Counter(r[:2] for r in wr) and all(len(r) >= 2 for r in gr)
    return Counter(gr) == Counter(wr)


def check(item, engines) -> tuple[list[str], bool]:
    from app.agents import sqlwrite

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
