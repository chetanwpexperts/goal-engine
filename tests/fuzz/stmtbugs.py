"""Generator for statement mistakes people make in any query (SELECT, UPDATE, DELETE), on random real tables (school, shop,
clinic, hotel...): a date or a word without quotes, column = (SELECT ...) that returns several rows, SET a = 1 AND b = 2,
status = 'a' OR 'b', BETWEEN high AND low, an aggregate in WHERE, = NULL in an UPDATE/DELETE. Each mistake must be found
under its own topic with no unrelated 'error'/'wrong' findings, and when a database is running, the corrected statement must
do on MariaDB/MySQL and on PostgreSQL exactly what the intended statement does (same rows, or the same rows changed).
Run: python -m tests.fuzz.stmtbugs [count] [seed]
"""

import random
import re
import sys
from collections import Counter

from tests.fuzz.db import engines_up, make_schema

BUGS = ["unquoted_date", "unquoted_word", "scalar_subquery", "set_and", "or_value", "between_reversed", "agg_in_where", "eq_null_dml",
        "same_table", "per_group",
        # night 1: basics (text joining, NULL functions, LIKE, AND/OR order, division, quotes, LIMIT order, DISTINCT)
        "plus_concat", "pipes_mysql", "isnull_two", "like_star", "like_contains", "and_or", "division", "double_quotes",
        "limit_no_order", "distinct_paren",
        # night 2: joins and relationships, and syntax the named database doesn't have (rule A)
        "comma_cartesian", "on_one_side", "pk_pk", "anti_nullable", "self_pairs", "distinct_join", "natural_join",
        "dialect_limit_pg", "dialect_backticks_pg", "dialect_nulls_mariadb", "dialect_cast_mariadb"]
TOPIC = {"unquoted_date": "value", "unquoted_word": "value", "scalar_subquery": "subquery", "set_and": "set", "or_value": "or",
         "between_reversed": "between", "agg_in_where": "where", "eq_null_dml": "null", "same_table": "same_table", "per_group": "per_group",
         "plus_concat": "concat", "pipes_mysql": "concat", "isnull_two": "null_fn", "like_star": "like", "like_contains": "like",
         "and_or": "and_or", "division": "division", "double_quotes": "quotes", "limit_no_order": "stable_order", "distinct_paren": "distinct",
         "comma_cartesian": "cartesian", "on_one_side": "cartesian", "pk_pk": "join_key", "anti_nullable": "anti_join",
         "self_pairs": "self_join", "distinct_join": "distinct_join", "natural_join": "natural_join", "dialect_limit_pg": "dialect",
         "dialect_backticks_pg": "dialect", "dialect_nulls_mariadb": "dialect", "dialect_cast_mariadb": "dialect"}
NO_FIX = {"agg_in_where", "limit_no_order", "distinct_paren", "comma_cartesian", "on_one_side", "natural_join"}   # found is enough
# a 'check' (depends on engine, data or intent) whose suggested rewrite must do exactly what the person meant
FIX_THE_CHECK = {"same_table", "like_contains", "and_or", "division", "double_quotes", "pk_pk", "anti_nullable", "self_pairs",
                 "distinct_join"}


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
    elif bug in ("plus_concat", "pipes_mysql"):   # first_name + ' ' + last_name / first_name || ' ' || last_name
        texts = [c for c in t["cols"] if c["kind"] in ("cat", "name", "text", "email", "phone", "sku")]
        if len(texts) < 2:
            return None
        a, b = rnd.sample(texts, 2)
        sep = rnd.choice(["' '", "' - '", "', '"])
        op = " + " if bug == "plus_concat" else " || "
        tname = f"`{t['name']}`" if bug == "pipes_mysql" else t["name"]        # backticks: the query shows it is MySQL/MariaDB
        truth = f"SELECT {pk}, CONCAT({a['name']}, {sep}, {b['name']}) AS label\nFROM {tname}\nORDER BY {pk}"
        broken = f"SELECT {pk}, {a['name']}{op}{sep}{op}{b['name']} AS label\nFROM {tname}\nORDER BY {pk}"
        stmt = "select"
        item = {"seed": seed, "bug": bug, "stmt": stmt, "schema": schema, "table": t["name"], "broken": broken, "truth": truth}
        if bug == "pipes_mysql":
            item["only"] = ["mysql"]
        return item
    elif bug == "isnull_two":                 # SQL Server's ISNULL(x, 0)
        if not nums:
            return None
        c = rnd.choice(nums)["name"]
        dflt = rnd.choice(["0", "-1", "100"])
        truth = f"SELECT {pk}, COALESCE({c}, {dflt}) AS v\nFROM {t['name']}\nORDER BY {pk}"
        broken = f"SELECT {pk}, ISNULL({c}, {dflt}) AS v\nFROM {t['name']}\nORDER BY {pk}"
        stmt = "select"
    elif bug in ("like_star", "like_contains"):  # LIKE '*abc*' (file wildcards) / LIKE 'abc' meaning 'contains abc'
        cats = [c for c in _cols(t, "cat", "name") if c["params"] or c["kind"] == "name"]
        vals = [v for c in cats for v in ([_pick_value(t, c, rnd)] if c["kind"] == "name" else c["params"]) if isinstance(v, str) and len(v) >= 4
                and not re.search(r"[*%_'?\\]", v)]
        if not vals:
            return None
        v = rnd.choice(vals)
        c = next(c for c in cats if (c["kind"] == "name" or v in c["params"]))
        sub = v[1:-1]
        truth = wrap(f"{c['name']} LIKE '%{sub}%'")
        if bug == "like_star":
            broken = wrap(f"{c['name']} LIKE '*{sub}*'")
        else:
            broken = wrap(f"{c['name']} LIKE '{sub}'") + f";\n\n{rnd.choice(['the ones whose', 'rows where', 'jinke'])} {c['name']} " \
                     + rnd.choice([f"contains {sub}", f"contain '{sub}' anywhere", f"mein {sub} wala ho"])
    elif bug == "and_or":                     # a = 1 OR a = 2 AND b > 3: the AND part only applies to the last OR
        cats = [c for c in _cols(t, "cat") if len(c["params"]) >= 2]
        if not cats or not nums:
            return None
        c = rnd.choice(cats)
        v1, v2 = rnd.sample(c["params"], 2)
        n = rnd.choice(nums)
        k = _pick_value(t, n, rnd)
        if k is None:
            return None
        first = rnd.random() < 0.5
        cond = f"{c['name']} = '{v1}' OR {c['name']} = '{v2}'"
        if first:
            truth, broken = wrap(f"({cond}) AND {n['name']} > {k}"), wrap(f"{cond} AND {n['name']} > {k}")
        else:
            truth, broken = wrap(f"{n['name']} > {k} AND ({cond})"), wrap(f"{n['name']} > {k} AND {cond}")
    elif bug == "division":                   # share of rows: integer division on PostgreSQL
        cats = [c for c in _cols(t, "cat") if c["params"]]
        if not cats:
            return None
        c = rnd.choice(cats)
        v = rnd.choice(c["params"])
        part = f"COUNT(CASE WHEN {c['name']} = '{v}' THEN 1 END) / COUNT(*)"
        scale = rnd.choice(["", " * 100"])
        truth = f"SELECT 1.0 * {part}{scale} AS share\nFROM {t['name']}"
        broken = f"SELECT {part}{scale} AS share\nFROM {t['name']}"
        stmt = "select"
    elif bug == "double_quotes":              # col = "value": text on MySQL, a column name on PostgreSQL
        cats = [c for c in _cols(t, "cat") if c["params"]]
        if not cats:
            return None
        c = rnd.choice(cats)
        v = rnd.choice(c["params"])
        truth, broken = wrap(f"{c['name']} = '{v}'"), wrap(f'{c["name"]} = "{v}"')
    elif bug == "limit_no_order":
        truth = None
        broken = f"SELECT {pk}\nFROM {t['name']}\nLIMIT {rnd.randint(2, 20)}"
        stmt = "select"
    elif bug == "distinct_paren":
        others = [c for c in t["cols"] if c["kind"] not in ("pk",)]
        cats = _cols(t, "cat", "fk")
        if not cats or len(others) < 2:
            return None
        c = rnd.choice(cats)
        o = rnd.choice([x for x in others if x is not c])
        truth = None
        broken = f"SELECT DISTINCT({c['name']}), {o['name']}\nFROM {t['name']}"
        stmt = "select"
    elif bug in ("comma_cartesian", "on_one_side", "pk_pk", "anti_nullable", "distinct_join", "natural_join"):
        kids = [(c, x) for x in tables for c in _cols(x, "fk")]
        if not kids:
            return None
        fk, child = rnd.choice(kids)
        parent = schema["tables"][fk["ref"]]
        ca, pa = rnd.choice([("c", "p"), ("ch", "pr"), ("x", "y")])
        cpk, ppk = child["pk"], parent["pk"]
        shown = [c for c in parent["cols"] if c["kind"] in ("cat", "name")]
        if not shown:
            return None
        sc = rnd.choice(shown)["name"]
        stmt = "select"
        truth = None
        if bug == "comma_cartesian":
            cats = _cols(child, "cat")
            cond = f"\nWHERE {ca}.{cats[0]['name']} = '{rnd.choice(cats[0]['params'])}'" if cats and cats[0]["params"] and rnd.random() < 0.5 else ""
            broken = f"SELECT {ca}.{cpk}, {pa}.{sc}\nFROM {child['name']} {ca}, {parent['name']} {pa}{cond}"
        elif bug == "on_one_side":
            cats = [c for c in _cols(parent, "cat") if c["params"]]
            if not cats:
                return None
            cc = rnd.choice(cats)
            broken = (f"SELECT {ca}.{cpk}, {pa}.{sc}\nFROM {child['name']} {ca}\nJOIN {parent['name']} {pa} ON {pa}.{cc['name']} = "
                      f"'{rnd.choice(cc['params'])}'")
        elif bug == "pk_pk":
            if cpk.lower() != "id" or ppk.lower() != "id":
                return None                               # o.id = c.id: both keys must be called id
            truth = f"SELECT {ca}.{cpk}, {ca}.{fk['name']}, {pa}.{sc}\nFROM {child['name']} {ca}\nJOIN {parent['name']} {pa} ON {ca}.{fk['name']} = {pa}.{ppk}\nORDER BY {ca}.{cpk}"
            broken = truth.replace(f"ON {ca}.{fk['name']} = {pa}.{ppk}", rnd.choice([f"ON {ca}.id = {pa}.id", f"ON {pa}.id = {ca}.id"]))
        elif bug == "anti_nullable":
            opt = [c for c in child["cols"] if c["kind"] not in ("pk", "fk")]
            if not opt:
                return None
            oc = rnd.choice(opt)["name"]
            base = (f"SELECT {pa}.{ppk}\nFROM {parent['name']} {pa}\nLEFT JOIN {child['name']} {ca} ON {ca}.{fk['name']} = {pa}.{ppk}\n"
                    f"WHERE {{}} IS NULL\nORDER BY {pa}.{ppk}")
            truth, broken = base.format(f"{ca}.{fk['name']}"), base.format(f"{ca}.{oc}")
        elif bug == "distinct_join":
            cats = [c for c in _cols(child, "cat") if c["params"]]
            w = f"\nWHERE {ca}.{cats[0]['name']} = '{rnd.choice(cats[0]['params'])}'" if cats and rnd.random() < 0.5 else ""
            keep_id = rnd.random() < 0.5
            cols = f"{pa}.{ppk}, {pa}.{sc}" if keep_id else f"{pa}.{sc}"
            broken = (f"SELECT DISTINCT {cols}\nFROM {parent['name']} {pa}\nJOIN {child['name']} {ca} ON {ca}.{fk['name']} = {pa}.{ppk}{w}")
            inner = f"SELECT 1 FROM {child['name']} {ca} WHERE {ca}.{fk['name']} = {pa}.{ppk}" + (f" AND {w.strip()[6:]}" if w else "")
            truth = f"SELECT {'DISTINCT ' if not keep_id else ''}{cols} FROM {parent['name']} {pa} WHERE EXISTS ({inner})"
        else:                                             # natural_join
            broken = f"SELECT *\nFROM {child['name']}\nNATURAL JOIN {parent['name']}"
    elif bug == "self_pairs":
        cats = [c for c in _cols(t, "cat") if c["params"]]
        if not cats:
            return None
        c = rnd.choice(cats)["name"]
        stmt = "select"
        twice = rnd.random() < 0.4
        on = f"a.{c} = b.{c}" + (f" AND a.{pk} <> b.{pk}" if twice else "")
        words = "\n\npairs of rows with the same " + c if twice else ""
        truth = f"SELECT a.{pk}, b.{pk}\nFROM {t['name']} a\nJOIN {t['name']} b ON a.{c} = b.{c} AND a.{pk} < b.{pk}"
        broken = f"SELECT a.{pk}, b.{pk}\nFROM {t['name']} a\nJOIN {t['name']} b ON {on}" + (";" + words if twice else "")
    elif bug.startswith("dialect_"):
        stmt = "select"
        if bug == "dialect_limit_pg":
            a, b = rnd.randint(0, 10), rnd.randint(1, 10)
            truth = f"SELECT {pk}\nFROM {t['name']}\nORDER BY {pk}\nLIMIT {b} OFFSET {a}"
            broken = f"SELECT {pk}\nFROM {t['name']}\nORDER BY {pk}\nLIMIT {a}, {b}"
            engine = "postgresql"
        elif bug == "dialect_backticks_pg":
            cols = [c["name"] for c in t["cols"] if c["kind"] != "pk"][:2]
            truth = f"SELECT {pk}, {', '.join(cols)}\nFROM {t['name']}\nORDER BY {pk}"
            broken = f"SELECT `{pk}`, {', '.join('`' + x + '`' for x in cols)}\nFROM `{t['name']}`\nORDER BY `{pk}`"
            engine = "postgresql"
        elif bug == "dialect_nulls_mariadb":
            opt = [c["name"] for c in t["cols"] if c["kind"] in ("int", "money", "date", "datetime")]
            if not opt:
                return None
            c = rnd.choice(opt)
            d = rnd.choice(["", " DESC"])
            truth = f"SELECT {pk}, {c}\nFROM {t['name']}\nORDER BY {c} IS NULL, {c}{d}, {pk}"
            broken = f"SELECT {pk}, {c}\nFROM {t['name']}\nORDER BY {c}{d} NULLS LAST, {pk}"
            engine = rnd.choice(["mariadb", "mysql"])
        else:                                             # dialect_cast_mariadb
            ints = [c["name"] for c in t["cols"] if c["kind"] in ("int",)]
            if not ints:
                return None
            c = rnd.choice(ints)
            truth = f"SELECT {pk}, CAST({c} AS SIGNED) AS v\nFROM {t['name']}\nORDER BY {pk}"
            broken = f"SELECT {pk}, {c}::int AS v\nFROM {t['name']}\nORDER BY {pk}"
            engine = rnd.choice(["mariadb", "mysql"])
        item = {"seed": seed, "bug": bug, "stmt": stmt, "schema": schema, "table": t["name"], "broken": broken, "truth": truth,
                "engine": engine}
        if bug == "dialect_cast_mariadb" or bug == "dialect_backticks_pg":
            item["only"] = ["mysql"] if engine != "postgresql" else ["postgres"]
        return item
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

    res = sqlcorrect.check(unwrap(item["broken"]), intent=request_words(item["broken"]), engine=item.get("engine"))
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
    again = [f["title_en"] for f in sqlcorrect.check(fixed, engine=item.get("engine"))["findings"] if f["kind"] in ("error", "wrong")]
    if again:
        bad.append(f"{tag}: the corrected statement still has mistakes {again}\n{fixed}")
    pk = item["schema"]["tables"][item["table"]]["pk"]
    if "/* check:" in fixed:
        return bad                                     # a column name the query doesn't show: the person confirms it
    for name, eng in engines.items():
        if item.get("only") and name not in item["only"]:
            continue                                   # the query shows its database (backticks): run it only there
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
