"""Generator for statement mistakes people make in any query (SELECT, UPDATE, DELETE), on random real tables (school, shop,
clinic, hotel...): a date or a word without quotes, column = (SELECT ...) that returns several rows, SET a = 1 AND b = 2,
status = 'a' OR 'b', BETWEEN high AND low, an aggregate in WHERE, = NULL in an UPDATE/DELETE. Each mistake must be found
under its own topic with no unrelated 'error'/'wrong' findings, and when a database is running, the corrected statement must
do on MariaDB/MySQL and on PostgreSQL exactly what the intended statement does (same rows, or the same rows changed).
Run: python -m tests.fuzz.stmtbugs [count] [seed]
"""

import random
import sys
from collections import Counter

from tests.fuzz.db import engines_up, make_schema

BUGS = ["unquoted_date", "unquoted_word", "scalar_subquery", "set_and", "or_value", "between_reversed", "agg_in_where", "eq_null_dml",
        "same_table", "per_group"]
TOPIC = {"unquoted_date": "value", "unquoted_word": "value", "scalar_subquery": "subquery", "set_and": "set", "or_value": "or",
         "between_reversed": "between", "agg_in_where": "where", "eq_null_dml": "null", "same_table": "same_table", "per_group": "per_group"}
NO_FIX = {"agg_in_where"}                    # there is no single right rewrite: found is enough
FIX_THE_CHECK = {"same_table"}               # a 'check' (fine on MariaDB/PostgreSQL) whose rewrite must still do the same


def _cols(t, *kinds):
    return [c for c in t["cols"] if c["kind"] in kinds]


def _pick_value(t, c, rnd):
    vals = [r[t["cols"].index(c)] for r in t["rows"] if r[t["cols"].index(c)] is not None]
    return rnd.choice(vals) if vals else None


def make(seed: int):
    rnd = random.Random(seed)
    schema = make_schema(seed)
    tables = list(schema["tables"].values())
    bug = BUGS[seed % len(BUGS)] if rnd.random() < 0.5 else rnd.choice(BUGS)
    t = rnd.choice(tables)
    pk = t["pk"]
    stmt = rnd.choice(["select", "select", "update", "delete"])
    nums = _cols(t, "int", "money")

    def wrap(cond, set_part=None):
        """The same condition as a SELECT, an UPDATE or a DELETE."""
        if stmt == "update" and nums:
            target = set_part or f"{nums[0]['name']} = {nums[0]['name']}"
            return f"UPDATE {t['name']}\nSET {target}\nWHERE {cond}"
        if stmt == "delete":
            return f"DELETE FROM {t['name']}\nWHERE {cond}"
        return f"SELECT {pk}\nFROM {t['name']}\nWHERE {cond}\nORDER BY {pk}"

    if bug == "unquoted_date":
        dates = _cols(t, "date")
        if not dates:
            return None
        c = dates[0]["name"]
        y = rnd.randint(*dates[0]["params"])
        d = f"{y}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
        truth, broken = wrap(f"{c} < '{d}'"), wrap(f"{c} < {d}")
    elif bug == "unquoted_word":
        cats = [c for c in _cols(t, "cat") if all(isinstance(v, str) and v.isalpha() and v.lower() not in
                                                   ("left", "in", "out", "on", "a", "p", "l", "m", "f", "is") for v in c["params"])]
        if not cats:
            return None
        c = rnd.choice(cats)
        v = rnd.choice(c["params"])
        truth, broken = wrap(f"{c['name']} = '{v}'"), wrap(f"{c['name']} = {v}")
    elif bug == "scalar_subquery":
        fks = _cols(t, "fk")
        if not fks:
            return None
        fk = rnd.choice(fks)
        parent = schema["tables"][fk["ref"]]
        cats = _cols(parent, "cat")
        if not cats:
            return None
        pc = rnd.choice(cats)
        counts = Counter(r[parent["cols"].index(pc)] for r in parent["rows"])
        many = [v for v, n in counts.items() if v is not None and n > 1]
        if not many:
            return None
        v = rnd.choice(many)
        sub = f"(\n    SELECT {parent['pk']}\n    FROM {parent['name']}\n    WHERE {pc['name']} = '{v}'\n)"
        truth, broken = wrap(f"{fk['name']} IN {sub}"), wrap(f"{fk['name']} = {sub}")
    elif bug == "set_and":
        if len(nums) < 2:
            return None
        stmt = "update"
        a, b = rnd.sample(nums, 2)
        k = rnd.randint(1, len(t["rows"]))
        truth = f"UPDATE {t['name']}\nSET {a['name']} = 7, {b['name']} = 9\nWHERE {pk} = {k}"
        broken = f"UPDATE {t['name']}\nSET {a['name']} = 7 AND {b['name']} = 9\nWHERE {pk} = {k}"
    elif bug == "or_value":
        cats = [c for c in _cols(t, "cat") if len(c["params"]) >= 2]
        if not cats:
            return None
        c = rnd.choice(cats)
        v1, v2 = rnd.sample(c["params"], 2)
        truth, broken = wrap(f"{c['name']} IN ('{v1}', '{v2}')"), wrap(f"{c['name']} = '{v1}' OR '{v2}'")
    elif bug == "between_reversed":
        ints = _cols(t, "int")
        if not ints:
            return None
        c = rnd.choice(ints)
        lo, hi = sorted(rnd.sample(range(c["params"][0], c["params"][1] + 1), 2)) if c["params"][1] > c["params"][0] else (0, 1)
        truth, broken = wrap(f"{c['name']} BETWEEN {lo} AND {hi}"), wrap(f"{c['name']} BETWEEN {hi} AND {lo}")
    elif bug == "agg_in_where":
        if not nums:
            return None
        c = rnd.choice(nums)["name"]
        fn = rnd.choice(["SUM", "AVG", "MAX", "COUNT"])
        truth = None
        broken = (f"SELECT {pk}, {c}\nFROM {t['name']}\nWHERE {fn}({c}) > 10" if stmt == "select" else
                  wrap(f"{c} > 0 AND {fn}({c}) > 10"))
    elif bug == "same_table":                 # valid on MariaDB and PostgreSQL, refused by MySQL (1093): the rewrite must do the same
        if not nums:
            return None
        stmt = rnd.choice(["update", "delete"])
        c = rnd.choice(nums)["name"]
        sub = rnd.choice([f"(SELECT MAX({c}) FROM {t['name']})", f"(SELECT AVG({c}) FROM {t['name']})"])
        op = rnd.choice(["=", "<", ">="])
        truth = broken = wrap(f"{c} {op} {sub}", f"{c} = {c}")
    elif bug == "per_group":                  # the person says "within each <group>", the subquery takes the whole table
        fks = _cols(t, "fk")
        if not fks or not nums:
            return None
        g = rnd.choice(fks)["name"]
        noun = g[:-3] if g.lower().endswith("_id") else (g[:-2] if g.lower().endswith("id") else g)
        c = rnd.choice(nums)["name"]
        fn, op = rnd.choice([("MAX", "="), ("MIN", "="), ("AVG", ">"), ("AVG", "<")])
        word = {"MAX": "highest", "MIN": "lowest", "AVG": "average"}[fn]
        intent = rnd.choice([f"the {word} {c} within their own {noun.lower()}", f"{word} {c} per {noun.lower()}",
                             f"har {noun.lower()} mein {word} {c}", f"{noun.lower()}-wise {word} {c}"])
        named = f"{g} IS NOT NULL AND " if rnd.random() < 0.6 else ""     # the group column written in the query, or not
        broken = wrap(f"{named}{c} {op} (SELECT {fn}({c}) FROM {t['name']})", f"{c} = {c}") + ";\n\n" + intent
        truth = wrap(f"{named}{c} {op} (SELECT {fn}(i2.{c}) FROM {t['name']} i2 WHERE i2.{g} = {t['name']}.{g})", f"{c} = {c}")
        return {"seed": seed, "bug": bug, "stmt": stmt, "schema": schema, "table": t["name"], "broken": broken, "truth": truth}
    else:                                     # eq_null_dml
        if not nums:
            return None
        stmt = rnd.choice(["update", "delete"])
        c = rnd.choice(nums)["name"]
        op, right = rnd.choice([("=", "IS NULL"), ("!=", "IS NOT NULL"), ("<>", "IS NOT NULL")])
        truth, broken = wrap(f"{c} {right}"), wrap(f"{c} {op} NULL")
    return {"seed": seed, "bug": bug, "stmt": stmt, "schema": schema, "table": t["name"], "broken": broken, "truth": truth}


def _effect(eng, sql, table, pk):
    """What a statement does: the rows a SELECT returns, or the table after an UPDATE/DELETE (rolled back afterwards)."""
    eng.begin()
    try:
        out = eng.run(sql)
        if not isinstance(out, list):
            out = eng.run(f"SELECT * FROM {table} ORDER BY {pk}")
        return Counter(tuple(str(x) for x in r) for r in out)
    except Exception as exc:  # noqa: BLE001 - a refusal the intended statement gets too (a foreign key) is the same effect
        return ("refused", type(exc).__name__, str(getattr(exc, "sqlstate", "") or (exc.args[0] if exc.args else "")))
    finally:
        eng.rollback()


def check(item, engines: dict) -> list[str]:
    from app.agents import sqlcorrect

    from app.agents.sql import request_words, unwrap

    res = sqlcorrect.check(unwrap(item["broken"]), intent=request_words(item["broken"]))
    tag = f"{item['bug']} ({item['stmt']}) seed {item['seed']}"
    topics = {f["topic"] for f in res["findings"]}
    bad = []
    want = TOPIC[item["bug"]]
    if want not in topics:
        bad.append(f"{tag}: not found (found {sorted(topics)})\n{item['broken']}")
    extra = [f["title_en"] for f in res["findings"] if f["kind"] in ("error", "wrong") and f["topic"] != want]
    if extra:
        bad.append(f"{tag}: extra findings {extra}\n{item['broken']}")
    sure = any(f["topic"] == want and f["kind"] in ("error", "wrong") for f in res["findings"])
    if item["bug"] in FIX_THE_CHECK:
        sure = True
    if item["bug"] in NO_FIX or not sure:
        return bad
    fixed = res["corrected"]
    if not fixed:
        return bad + [f"{tag}: no corrected statement\n{item['broken']}"]
    again = [f["title_en"] for f in sqlcorrect.check(fixed)["findings"] if f["kind"] in ("error", "wrong")]
    if again:
        bad.append(f"{tag}: the corrected statement still has mistakes {again}\n{fixed}")
    pk = item["schema"]["tables"][item["table"]]["pk"]
    if "/* check:" in fixed:
        return bad                                     # a column name the query doesn't show: the person confirms it
    for name, eng in engines.items():
        try:
            if _effect(eng, fixed, item["table"], pk) != _effect(eng, item["truth"], item["table"], pk):
                bad.append(f"{tag}: on {name} the corrected statement does something else\n{fixed}")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{tag}: on {name} the corrected statement fails: {exc}\n{fixed}")
    return bad


def run(count=200, seed=1, engines=None):
    engines = engines_up() if engines is None else engines
    fails, made, by = [], 0, Counter()
    for s in range(seed, seed + count * 6):
        if made >= count:
            break
        item = make(s)
        if item is None:
            continue
        made += 1
        by[item["bug"]] += 1
        for eng in engines.values():
            eng.create(item["schema"])
        try:
            fails += check(item, engines)
        finally:
            for eng in engines.values():
                eng.drop(item["schema"]["order"])
    return made, fails, by, engines


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    made, fails, by, engines = run(count, seed)
    for f in fails[:25]:
        print(f, "\n")
    print(dict(by))
    print(f"{made} statements, {len(fails)} problems; checked on: {', '.join(engines) or 'no database (rows not compared)'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
