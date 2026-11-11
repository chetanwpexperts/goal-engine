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
        "dialect_limit_pg", "dialect_backticks_pg", "dialect_nulls_mariadb", "dialect_cast_mariadb",
        # night 3: aggregation (what a total sees: non-NULL values of the rows that reach it, once per group)
        "count_case", "count_bool", "sum_bool_pg", "nested_agg", "window_agg", "having_alias_pg", "count_multi_pg", "sum_null",
        "avg_avg", "count_said", "pct_total", "sum_distinct",
        # night 4: subqueries (shape: one value / a set / EXISTS; scope: the inner FROM first; NULLs in IN / NOT IN / ALL)
        "exists_agg", "corr_self", "in_grouped_agg", "sub_columns", "sub_sum_null", "in_limit_mariadb", "derived_alias_mysql",
        "all_null", "in_list_null", "not_in_outer", "semi_join", "exists_uncorr", "sub_outer_col", "scalar_rows", "sub_limit_order",
        # night 5: changing data (which rows, how often, what value, which column) and each engine's change syntax
        "ch_update_join_pg", "ch_update_from_my", "ch_set_qualified_pg", "ch_update_from_self", "ch_join_many_my", "ch_join_many_pg",
        "ch_set_sub_null", "ch_upsert_pg", "ch_conflict_my", "ch_conflict_amb", "ch_insert_order", "ch_insert_count", "ch_limit_pg",
        "ch_dedupe", "ch_delete_no_target", "ch_delete_join_pg"]
TOPIC = {"unquoted_date": "value", "unquoted_word": "value", "scalar_subquery": "subquery", "set_and": "set", "or_value": "or",
         "between_reversed": "between", "agg_in_where": "where", "eq_null_dml": "null", "same_table": "same_table", "per_group": "per_group",
         "plus_concat": "concat", "pipes_mysql": "concat", "isnull_two": "null_fn", "like_star": "like", "like_contains": "like",
         "and_or": "and_or", "division": "division", "double_quotes": "quotes", "limit_no_order": "stable_order", "distinct_paren": "distinct",
         "comma_cartesian": "cartesian", "on_one_side": "cartesian", "pk_pk": "join_key", "anti_nullable": "anti_join",
         "self_pairs": "self_join", "distinct_join": "distinct_join", "natural_join": "natural_join", "dialect_limit_pg": "dialect",
         "dialect_backticks_pg": "dialect", "dialect_nulls_mariadb": "dialect", "dialect_cast_mariadb": "dialect",
         "count_case": "count_case", "count_bool": "count_bool", "sum_bool_pg": "sum_bool", "nested_agg": "nested_agg",
         "window_agg": "window_agg", "having_alias_pg": "having_alias", "count_multi_pg": "count_multi", "sum_null": "sum_null",
         "avg_avg": "avg_avg", "count_said": "count_said", "pct_total": "pct_total", "sum_distinct": "sum_distinct",
         "exists_agg": "exists_agg", "corr_self": "corr_self", "in_grouped_agg": "in_grouped_agg", "sub_columns": "sub_columns",
         "sub_sum_null": "sub_sum_null", "in_limit_mariadb": "in_limit", "derived_alias_mysql": "derived_alias", "all_null": "all_null",
         "in_list_null": "in_list_null", "not_in_outer": "not_in_outer", "semi_join": "semi_join", "exists_uncorr": "exists_uncorr",
         "sub_outer_col": "sub_outer_col", "scalar_rows": "scalar_rows", "sub_limit_order": "sub_limit_order",
         "ch_update_join_pg": "update_join_syntax", "ch_update_from_my": "update_join_syntax", "ch_set_qualified_pg": "set_qualified",
         "ch_update_from_self": "update_from_self", "ch_join_many_my": "join_change_many", "ch_join_many_pg": "join_change_many",
         "ch_set_sub_null": "set_subquery_null", "ch_upsert_pg": "upsert_syntax", "ch_conflict_my": "upsert_syntax",
         "ch_conflict_amb": "conflict_ambiguous", "ch_insert_order": "insert_order", "ch_insert_count": "insert_count",
         "ch_limit_pg": "change_limit", "ch_dedupe": "dedupe_self", "ch_delete_no_target": "delete_no_target",
         "ch_delete_join_pg": "delete_join_syntax"}
NO_FIX = {"agg_in_where", "limit_no_order", "distinct_paren", "comma_cartesian", "on_one_side", "natural_join", "sum_distinct",
          "exists_uncorr", "sub_outer_col", "scalar_rows", "sub_limit_order", "ch_insert_count"}   # found is enough
# a 'check' (depends on engine, data or intent) whose suggested rewrite must do exactly what the person meant
FIX_THE_CHECK = {"same_table", "like_contains", "and_or", "division", "double_quotes", "pk_pk", "anti_nullable", "self_pairs",
                 "distinct_join", "sum_null", "avg_avg", "pct_total", "sub_sum_null", "all_null", "in_list_null", "not_in_outer",
                 "semi_join", "ch_set_sub_null"}


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
    elif bug in AGG_BUGS:
        return _agg_bug(seed, bug, rnd, schema, tables, t)
    elif bug in SUB_BUGS:
        return _sub_bug(seed, bug, rnd, schema, tables, t)
    elif bug.startswith("ch_"):
        return _change_bug(seed, bug, rnd, schema, tables, t)
    else:                                     # eq_null_dml
        if not nums:
            return None
        stmt = rnd.choice(["update", "delete"])
        c = rnd.choice(nums)["name"]
        op, right = rnd.choice([("=", "IS NULL"), ("!=", "IS NOT NULL"), ("<>", "IS NOT NULL")])
        truth, broken = wrap(f"{c} {right}"), wrap(f"{c} {op} NULL")
    return {"seed": seed, "bug": bug, "stmt": stmt, "schema": schema, "table": t["name"], "broken": broken, "truth": truth}



AGG_BUGS = {"count_case", "count_bool", "sum_bool_pg", "nested_agg", "window_agg", "having_alias_pg", "count_multi_pg", "sum_null",
            "avg_avg", "count_said", "pct_total", "sum_distinct"}


def _plural_words(base: str) -> str:
    return base.replace("_", " ")


def _agg_bug(seed, bug, rnd, schema, tables, t):
    """Night 3: aggregation mistakes on a random real table. truth = the statement the person meant."""
    pk, name = t["pk"], t["name"]
    groups = [c for c in t["cols"] if c["kind"] in ("fk", "cat")]
    nums = [c["name"] for c in t["cols"] if c["kind"] in ("int", "money")]
    cats = [c for c in t["cols"] if c["kind"] == "cat" and c["params"]]
    engine, words = None, ""
    if bug in ("count_case", "count_bool", "sum_bool_pg", "pct_total") and not cats:
        return None
    if bug in ("nested_agg", "window_agg", "having_alias_pg", "count_case", "pct_total") and not groups:
        return None
    g = rnd.choice(groups)["name"] if groups else None
    if bug == "count_case":
        c = rnd.choice(cats)
        v = rnd.choice(c["params"])
        cond = f"{c['name']} = '{v}'"
        els = rnd.choice(["0", "0", "'no'", "-1"])
        then = "1" if els != "'no'" else "'yes'"
        truth = f"SELECT {g}, COUNT(CASE WHEN {cond} THEN 1 END) AS n\nFROM {name}\nGROUP BY {g}\nORDER BY {g}"
        broken = f"SELECT {g}, COUNT(CASE WHEN {cond} THEN {then} ELSE {els} END) AS n\nFROM {name}\nGROUP BY {g}\nORDER BY {g}"
    elif bug == "count_bool":
        if nums and rnd.random() < 0.4:
            col = rnd.choice(nums)
            cond = f"{col} > {rnd.randint(1, 50)}"
        else:
            c = rnd.choice(cats)
            cond = f"{c['name']} = '{rnd.choice(c['params'])}'"
        truth = f"SELECT COUNT(*)\nFROM {name}\nWHERE {cond}"
        broken = f"SELECT COUNT({cond})\nFROM {name}"
    elif bug == "sum_bool_pg":
        c = rnd.choice(cats)
        cond = f"{c['name']} = '{rnd.choice(c['params'])}'"
        truth = f"SELECT COUNT(*)\nFROM {name}\nWHERE {cond}"
        broken = f"SELECT SUM({cond})\nFROM {name}"
        engine = "postgresql"
    elif bug == "nested_agg":
        if nums and rnd.random() < 0.5:
            outer, inner = rnd.choice(["AVG", "MAX", "MIN"]), f"SUM({rnd.choice(nums)})"
        else:
            outer, inner = rnd.choice(["MAX", "MIN", "AVG"]), "COUNT(*)"
        truth = f"SELECT {outer}(v)\nFROM (SELECT {inner} AS v FROM {name} GROUP BY {g}) AS x"
        broken = f"SELECT {outer}({inner})\nFROM {name}\nGROUP BY {g}"
    elif bug == "window_agg":
        if not nums:
            return None
        m = rnd.choice(nums)
        fn = rnd.choice(["SUM", "MAX", "MIN"])
        truth = f"SELECT {g}, {fn}({m}) AS v, {fn}({fn}({m})) OVER () AS whole\nFROM {name}\nGROUP BY {g}\nORDER BY {g}"
        broken = f"SELECT {g}, {fn}({m}) AS v, {fn}({m}) OVER () AS whole\nFROM {name}\nGROUP BY {g}\nORDER BY {g}"
    elif bug == "having_alias_pg":
        k = rnd.randint(1, 4)
        if nums and rnd.random() < 0.5:
            m = rnd.choice(nums)
            agg, lim = f"SUM({m})", rnd.randint(10, 500)
        else:
            agg, lim = "COUNT(*)", k
        alias = rnd.choice(["total", "cnt", "n_rows", "how_many"])
        truth = f"SELECT {g}, {agg} AS {alias}\nFROM {name}\nGROUP BY {g}\nHAVING {agg} > {lim}\nORDER BY {g}"
        broken = f"SELECT {g}, {agg} AS {alias}\nFROM {name}\nGROUP BY {g}\nHAVING {alias} > {lim}\nORDER BY {g}"
        engine = "postgresql"
    elif bug == "count_multi_pg":
        plain = [c["name"] for c in t["cols"] if c["kind"] in ("cat", "fk", "int")]
        if len(plain) < 2:
            return None
        a, b = rnd.sample(plain, 2)
        truth = f"SELECT COUNT(*)\nFROM (SELECT DISTINCT {a}, {b} FROM {name} WHERE {a} IS NOT NULL AND {b} IS NOT NULL) AS d"
        broken = f"SELECT COUNT(DISTINCT {a}, {b})\nFROM {name}"
        engine = "postgresql"
    elif bug in ("sum_null", "count_said"):
        kids = [(c, x) for x in tables for c in x["cols"] if c["kind"] == "fk"]
        if not kids:
            return None
        fk, child = rnd.choice(kids)
        parent = schema["tables"][fk["ref"]]
        t = child
        cn = [c["name"] for c in child["cols"] if c["kind"] in ("int", "money")]
        if bug == "sum_null":
            if not cn:
                return None
            m = rnd.choice(cn)
            ppk = parent["pk"]
            base = (f"SELECT p.{ppk}, {{}} AS total\nFROM {parent['name']} p\nLEFT JOIN {child['name']} c ON c.{fk['name']} = p.{ppk}\n"
                    f"GROUP BY p.{ppk}\nORDER BY p.{ppk}")
            truth, broken = base.format(f"COALESCE(SUM(c.{m}), 0)"), base.format(f"SUM(c.{m})")
            words = f"total {m} for every {parent['base']} row, 0 when it has none"
        else:
            from app.agents.sqljoins import _fk

            if _fk(parent["name"]).replace("_", "") != fk["name"].lower().replace("_", ""):
                return None                               # the words name the parent; its key column must follow the usual name
            cond = ""
            ccats = [c for c in child["cols"] if c["kind"] == "cat" and c["params"]]
            if ccats and rnd.random() < 0.5:
                cc = rnd.choice(ccats)
                cond = f"\nWHERE {cc['name']} = '{rnd.choice(cc['params'])}'"
            counted = rnd.choice(["*", fk["name"]])
            truth = f"SELECT COUNT(DISTINCT {fk['name']})\nFROM {child['name']}{cond}"
            broken = f"SELECT COUNT({counted})\nFROM {child['name']}{cond}"
            noun = _plural_words(parent["base"])
            words = rnd.choice([f"how many {noun} have a {_plural_words(child['base'])} row", f"number of {noun} with {_plural_words(child['base'])}",
                                f"kitne {noun} ke {_plural_words(child['base'])} hain"])
    elif bug == "avg_avg":
        if not nums or not groups:
            return None
        m = rnd.choice(nums)
        truth = f"SELECT 1.0 * SUM({m}) / COUNT({m})\nFROM {name}"
        broken = f"SELECT AVG(a)\nFROM (SELECT {g}, AVG({m}) AS a FROM {name} GROUP BY {g}) AS x"
        words = "the average over all rows"
    elif bug == "pct_total":
        c = rnd.choice(cats)
        v = rnd.choice(c["params"])
        if g == c["name"]:
            others = [x["name"] for x in groups if x["name"] != c["name"]]
            if not others:
                return None
            g = rnd.choice(others)
        truth = (f"SELECT {g}, 100.0 * COUNT(*) / (SELECT COUNT(*) FROM {name} WHERE {c['name']} = '{v}') AS pct\nFROM {name}\n"
                 f"WHERE {c['name']} = '{v}'\nGROUP BY {g}\nORDER BY {g}")
        broken = (f"SELECT {g}, 100.0 * COUNT(*) / (SELECT COUNT(*) FROM {name}) AS pct\nFROM {name}\n"
                  f"WHERE {c['name']} = '{v}'\nGROUP BY {g}\nORDER BY {g}")
        words = f"each {g}'s share of the {v} rows"
    else:                                                 # sum_distinct
        if not nums:
            return None
        truth = None
        broken = f"SELECT SUM(DISTINCT {rnd.choice(nums)})\nFROM {name}"
    if words:
        broken += ";\n\n" + words
    item = {"seed": seed, "bug": bug, "stmt": "select", "schema": schema, "table": t["name"], "broken": broken, "truth": truth}
    if engine:
        item["engine"] = engine
    return item


SUB_BUGS = {"exists_agg", "corr_self", "in_grouped_agg", "sub_columns", "sub_sum_null", "in_limit_mariadb", "derived_alias_mysql",
            "all_null", "in_list_null", "not_in_outer", "semi_join", "exists_uncorr", "sub_outer_col", "scalar_rows", "sub_limit_order"}


def _sub_bug(seed, bug, rnd, schema, tables, t):
    """Night 4: subquery mistakes on random real tables. truth = the statement the person meant."""
    from app.agents.sqljoins import _base

    links = [(c, x) for x in tables for c in x["cols"] if c["kind"] == "fk"]       # (fk column, child table)
    if not links:
        return None
    fkc, child = rnd.choice(links)
    parent = schema["tables"][fkc["ref"]]
    fk, cpk, ppk, cn, pn = fkc["name"], child["pk"], parent["pk"], child["name"], parent["name"]
    cnums = [c["name"] for c in child["cols"] if c["kind"] in ("int", "money")]
    ccats = [c for c in child["cols"] if c["kind"] == "cat" and c["params"]]
    tnums = [c["name"] for c in t["cols"] if c["kind"] in ("int", "money")]
    tgroups = [c["name"] for c in t["cols"] if c["kind"] in ("fk", "cat")]
    tcats = [c for c in t["cols"] if c["kind"] == "cat" and c["params"]]
    pk, name = t["pk"], t["name"]
    engine, words, stmt = None, "", "select"

    def cat_cond(alias, cats):
        c = rnd.choice(cats)
        return f"{alias}{c['name']} = '{rnd.choice(c['params'])}'"

    if bug == "exists_agg":
        agg = rnd.choice(["COUNT(*)", f"MAX(c.{cpk})", f"MIN(c.{fk})"] + ([f"SUM(c.{cnums[0]})"] if cnums else []))
        neg = rnd.choice(["", "NOT "])
        extra = f" AND {cat_cond('c.', ccats)}" if ccats and rnd.random() < 0.5 else ""
        truth = f"SELECT p.{ppk}\nFROM {pn} p\nWHERE {neg}EXISTS (SELECT 1 FROM {cn} c WHERE c.{fk} = p.{ppk}{extra})\nORDER BY p.{ppk}"
        broken = truth.replace("SELECT 1 FROM", f"SELECT {agg} FROM")
    elif bug == "corr_self":
        if not tnums or not tgroups:
            return None
        n, g = rnd.choice(tnums), rnd.choice(tgroups)
        if rnd.random() < 0.5:
            truth = f"SELECT a.{pk}\nFROM {name} a\nWHERE a.{n} > (SELECT AVG(b.{n}) FROM {name} b WHERE b.{g} = a.{g})\nORDER BY a.{pk}"
            broken = truth.replace(f"b.{g} = a.{g}", f"b.{g} = b.{g}")
        else:
            truth = f"SELECT a.{pk}\nFROM {name} a\nWHERE a.{n} > (SELECT AVG(b.{n}) FROM {name} b WHERE b.{g} = a.{g})\nORDER BY a.{pk}"
            broken = f"SELECT {pk}\nFROM {name}\nWHERE {n} > (SELECT AVG({n}) FROM {name} WHERE {g} = {name}.{g})\nORDER BY {pk}"
    elif bug == "in_grouped_agg":
        if not tnums or not tgroups:
            return None
        n, g, fn = rnd.choice(tnums), rnd.choice(tgroups), rnd.choice(["MAX", "MIN"])
        broken = f"SELECT {pk}\nFROM {name}\nWHERE {n} IN (SELECT {fn}({n}) FROM {name} GROUP BY {g})\nORDER BY {pk}"
        truth = f"SELECT {pk}\nFROM {name}\nWHERE ({g}, {n}) IN (SELECT {g}, {fn}({n}) FROM {name} GROUP BY {g})\nORDER BY {pk}"
    elif bug == "sub_columns":
        other = rnd.choice([c["name"] for c in child["cols"] if c["name"] and c["name"] != fk])
        where = f" WHERE {cat_cond('', ccats)}" if ccats else ""
        truth = f"SELECT {ppk}\nFROM {pn}\nWHERE {ppk} IN (SELECT {fk} FROM {cn}{where})\nORDER BY {ppk}"
        cols = rnd.choice([f"{fk}, {other}", f"{other}, {fk}"])
        broken = f"SELECT {cpk}\nFROM {cn}\nWHERE {fk} IN (SELECT {cols} FROM {cn}{where})\nORDER BY {cpk}"
        truth = f"SELECT {cpk}\nFROM {cn}\nWHERE {fk} IN (SELECT {fk} FROM {cn}{where})\nORDER BY {cpk}"
    elif bug == "sub_sum_null":
        if not cnums:
            return None
        n = rnd.choice(cnums)
        broken = f"SELECT p.{ppk}, (SELECT SUM(c.{n}) FROM {cn} c WHERE c.{fk} = p.{ppk}) AS total\nFROM {pn} p\nORDER BY p.{ppk}"
        truth = f"SELECT p.{ppk}, COALESCE((SELECT SUM(c.{n}) FROM {cn} c WHERE c.{fk} = p.{ppk}), 0) AS total\nFROM {pn} p\nORDER BY p.{ppk}"
        words = rnd.choice(["", f"total of each {_base(pn)}, 0 when none"])
    elif bug == "in_limit_mariadb":
        if not cnums:
            return None
        k = rnd.randint(2, 6)
        inner = f"SELECT {fk} FROM {cn} ORDER BY {rnd.choice(cnums)} DESC, {cpk} LIMIT {k}"
        broken = f"SELECT {ppk}\nFROM {pn}\nWHERE {ppk} IN ({inner})\nORDER BY {ppk}"
        truth = f"SELECT {ppk}\nFROM {pn}\nWHERE {ppk} IN (SELECT * FROM ({inner}) AS limited)\nORDER BY {ppk}"
        engine = rnd.choice(["mariadb", "mysql"])
    elif bug == "derived_alias_mysql":
        if not tgroups:
            return None
        g = rnd.choice(tgroups)
        fn = rnd.choice(["MAX", "MIN", "AVG"])
        broken = f"SELECT {fn}(n) AS v\nFROM (SELECT {g}, COUNT(*) AS n FROM {name} GROUP BY {g})"
        truth = broken + " AS t"
        engine = rnd.choice(["mariadb", "mysql"])
    elif bug == "all_null":
        if not tnums:
            return None
        n, m = rnd.choice(tnums), rnd.choice(tnums)
        op = rnd.choice([">", ">=", "<", "<="])
        fn = "MAX" if op.startswith(">") else "MIN"
        where = f" WHERE {cat_cond('', tcats)}" if tcats and rnd.random() < 0.7 else ""
        broken = f"SELECT {pk}\nFROM {name}\nWHERE {n} {op} ALL (SELECT {m} FROM {name}{where})\nORDER BY {pk}"
        truth = f"SELECT {pk}\nFROM {name}\nWHERE {n} {op} (SELECT {fn}({m}) FROM {name}{where})\nORDER BY {pk}"
    elif bug == "in_list_null":
        if not tcats:
            return None
        c = rnd.choice(tcats)
        vals = rnd.sample(c["params"], min(len(c["params"]), rnd.randint(1, 2)))
        lst = ", ".join(f"'{v}'" for v in vals)
        neg = rnd.random() < 0.6
        stmt = rnd.choice(["select", "select", "update", "delete"]) if neg else "select"
        if neg:
            cond_b, cond_t = f"{c['name']} NOT IN ({lst}, NULL)", f"{c['name']} NOT IN ({lst})"
        else:
            cond_b, cond_t = f"{c['name']} IN ({lst}, NULL)", f"({c['name']} IN ({lst}) OR {c['name']} IS NULL)"
        if stmt == "update" and tnums:
            broken = f"UPDATE {name}\nSET {tnums[0]} = {tnums[0]}\nWHERE {cond_b}"
            truth = f"UPDATE {name}\nSET {tnums[0]} = {tnums[0]}\nWHERE {cond_t}"
        elif stmt == "delete" and not any(c2.get("ref") == name for x in tables for c2 in x["cols"]):
            broken, truth = f"DELETE FROM {name}\nWHERE {cond_b}", f"DELETE FROM {name}\nWHERE {cond_t}"
        else:
            stmt = "select"
            broken = f"SELECT {pk}\nFROM {name}\nWHERE {cond_b}\nORDER BY {pk}"
            truth = f"SELECT {pk}\nFROM {name}\nWHERE {cond_t}\nORDER BY {pk}"
    elif bug == "not_in_outer":
        pcats = [c for c in parent["cols"] if c["kind"] == "cat" and c["params"]]
        if not pcats:
            return None
        cond = cat_cond("", pcats)
        broken = f"SELECT {cpk}\nFROM {cn}\nWHERE {fk} NOT IN (SELECT {ppk} FROM {pn} WHERE {cond})\nORDER BY {cpk}"
        truth = (f"SELECT c.{cpk}\nFROM {cn} c\nWHERE NOT EXISTS (SELECT 1 FROM {pn} p WHERE p.{ppk} = c.{fk} AND p.{cond})\n"
                 f"ORDER BY c.{cpk}")
    elif bug == "semi_join":
        if not ccats:
            return None
        cond = cat_cond("c.", ccats)
        broken = f"SELECT p.{ppk}\nFROM {pn} p\nJOIN {cn} c ON c.{fk} = p.{ppk}\nWHERE {cond}\nORDER BY p.{ppk}"
        truth = f"SELECT p.{ppk}\nFROM {pn} p\nWHERE EXISTS (SELECT 1 FROM {cn} c WHERE c.{fk} = p.{ppk} AND {cond})\nORDER BY p.{ppk}"
        words = rnd.choice([f"{_base(pn)} that have a matching {_base(cn).replace('_', ' ')}", f"list {_base(pn)} who have one",
                            f"wo {_base(pn)} jinke paas {_base(cn).replace('_', ' ')} hai"])
    elif bug == "exists_uncorr":
        if not ccats:
            return None
        broken = f"SELECT p.{ppk}\nFROM {pn} p\nWHERE {rnd.choice(['', 'NOT '])}EXISTS (SELECT 1 FROM {cn} c WHERE {cat_cond('c.', ccats)})"
        truth = None
    elif bug == "sub_outer_col":
        where = f" WHERE {cat_cond('c.', ccats)}" if ccats else ""
        broken = f"SELECT p.{ppk}\nFROM {pn} p\nWHERE p.{ppk} {rnd.choice(['IN', 'NOT IN'])} (SELECT p.{ppk} FROM {cn} c{where})"
        truth = None
    elif bug == "scalar_rows":
        other = rnd.choice([c["name"] for c in child["cols"] if c["name"] and c["kind"] not in ("pk", "fk")] or [cpk])
        broken = f"SELECT p.{ppk}, (SELECT c.{other} FROM {cn} c WHERE c.{fk} = p.{ppk}) AS v\nFROM {pn} p"
        truth = None
    else:                                                 # sub_limit_order
        other = rnd.choice([c["name"] for c in child["cols"] if c["name"] and c["kind"] not in ("pk", "fk")] or [cpk])
        broken = f"SELECT p.{ppk}, (SELECT c.{other} FROM {cn} c WHERE c.{fk} = p.{ppk} LIMIT 1) AS v\nFROM {pn} p"
        truth = None
    if words:
        broken += ";\n\n" + words
    item = {"seed": seed, "bug": bug, "stmt": stmt, "schema": schema, "table": t["name"] if stmt != "select" else t["name"],
            "broken": broken, "truth": truth}
    if engine:
        item["engine"] = engine
    return item


def _change_bug(seed, bug, rnd, schema, tables, t):
    """Night 5: mistakes in UPDATE / DELETE / INSERT on random real tables. truth = the statement the person meant; 'only' = the
    engine whose syntax it is (the other one refuses both)."""
    links = [(c, x) for x in tables for c in x["cols"] if c["kind"] == "fk"]       # (fk column, child table)
    if not links:
        return None
    fkc, child = rnd.choice(links)
    parent = schema["tables"][fkc["ref"]]
    fk, ppk, cn, pn = fkc["name"], parent["pk"], child["name"], parent["name"]
    cnums = [c["name"] for c in child["cols"] if c["kind"] in ("int", "money")]
    pnums = [c["name"] for c in parent["cols"] if c["kind"] in ("int", "money")]
    pcats = [c for c in parent["cols"] if c["kind"] == "cat" and c["params"]]
    tnums = [c["name"] for c in t["cols"] if c["kind"] in ("int", "money")]
    tcats = [c for c in t["cols"] if c["kind"] == "cat" and c["params"]]
    pk, name = t["pk"], t["name"]
    engine, only, target = None, None, name
    k = rnd.randint(1, len(t["rows"]))

    def pcond(alias):
        c = rnd.choice(pcats)
        return f"{alias}.{c['name']} = '{rnd.choice(c['params'])}'"

    if bug in ("ch_update_join_pg", "ch_update_from_my", "ch_update_from_self", "ch_delete_no_target", "ch_delete_join_pg"):
        if not pcats or (bug.startswith("ch_update") and not cnums):
            return None
        cond, target = pcond("p"), cn
        num = cnums[0] if cnums else None
        my_upd = f"UPDATE {cn} c\nJOIN {pn} p ON p.{ppk} = c.{fk}\nSET c.{num} = 0\nWHERE {cond}"
        pg_upd = f"UPDATE {cn} c\nSET {num} = 0\nFROM {pn} p\nWHERE p.{ppk} = c.{fk} AND {cond}"
        my_del = f"DELETE c FROM {cn} c\nJOIN {pn} p ON p.{ppk} = c.{fk}\nWHERE {cond}"
        pg_del = f"DELETE FROM {cn} c\nUSING {pn} p\nWHERE p.{ppk} = c.{fk} AND {cond}"
        if bug == "ch_update_join_pg":
            broken, truth, engine, only = my_upd, pg_upd, "postgresql", ["postgres"]
        elif bug == "ch_update_from_my":
            broken, truth, engine, only = pg_upd, my_upd, rnd.choice(["mariadb", "mysql"]), ["mysql"]
        elif bug == "ch_update_from_self":
            broken = f"UPDATE {cn}\nSET {num} = 0\nFROM {cn} c\nJOIN {pn} p ON p.{ppk} = c.{fk}\nWHERE {cond}"
            truth, only = pg_upd, ["postgres"]
        elif bug == "ch_delete_no_target":
            broken = f"DELETE FROM {cn} c\nJOIN {pn} p ON p.{ppk} = c.{fk}\nWHERE {cond}"
            engine = rnd.choice(["mariadb", "postgresql"])
            truth, only = (my_del, ["mysql"]) if engine == "mariadb" else (pg_del, ["postgres"])
        else:
            broken, truth, engine, only = my_del, pg_del, "postgresql", ["postgres"]
    elif bug == "ch_set_qualified_pg":
        if not tnums:
            return None
        c = rnd.choice(tnums)
        broken = f"UPDATE {name} x\nSET x.{c} = 7\nWHERE x.{pk} = {k}"
        truth, engine, only = f"UPDATE {name}\nSET {c} = 7\nWHERE {pk} = {k}", "postgresql", ["postgres"]
    elif bug in ("ch_join_many_my", "ch_join_many_pg", "ch_set_sub_null"):
        if not cnums or not pnums:
            return None
        pc, cc, target = rnd.choice(pnums), rnd.choice(cnums), pn
        derived = f"(SELECT {fk}, SUM({cc}) AS {cc} FROM {cn} GROUP BY {fk})"
        if bug == "ch_join_many_my":
            broken = f"UPDATE {pn} p\nJOIN {cn} c ON c.{fk} = p.{ppk}\nSET p.{pc} = p.{pc} + c.{cc}"
            truth = f"UPDATE {pn} p\nJOIN {derived} c ON c.{fk} = p.{ppk}\nSET p.{pc} = p.{pc} + c.{cc}"
            engine, only = rnd.choice(["mariadb", "mysql"]), ["mysql"]
        elif bug == "ch_join_many_pg":
            broken = f"UPDATE {pn} p\nSET {pc} = p.{pc} + c.{cc}\nFROM {cn} c\nWHERE c.{fk} = p.{ppk}"
            truth = f"UPDATE {pn} p\nSET {pc} = p.{pc} + c.{cc}\nFROM {derived} c\nWHERE c.{fk} = p.{ppk}"
            engine, only = "postgresql", ["postgres"]
        else:
            sub = f"(SELECT MAX(c.{cc}) FROM {cn} c WHERE c.{fk} = p.{ppk})"
            broken = f"UPDATE {pn} p\nSET {pc} = {sub}"
            truth = f"UPDATE {pn} p\nSET {pc} = {sub}\nWHERE EXISTS (SELECT 1 FROM {cn} c WHERE c.{fk} = p.{ppk})"
    elif bug in ("ch_upsert_pg", "ch_conflict_my", "ch_conflict_amb"):
        if not tnums:
            return None
        c = rnd.choice(tnums)
        k2 = rnd.choice([k, len(t["rows"]) + 50])
        head = f"INSERT INTO {name} ({pk}, {c})\nVALUES ({k2}, 5)\n"
        if bug == "ch_upsert_pg":
            broken = head + f"ON DUPLICATE KEY UPDATE {c} = VALUES({c})"
            truth, engine, only = head + f"ON CONFLICT ({pk}) DO UPDATE SET {c} = EXCLUDED.{c}", "postgresql", ["postgres"]
        elif bug == "ch_conflict_my":
            broken = head + f"ON CONFLICT ({pk}) DO UPDATE SET {c} = EXCLUDED.{c}"
            truth, engine, only = head + f"ON DUPLICATE KEY UPDATE {c} = VALUES({c})", rnd.choice(["mariadb", "mysql"]), ["mysql"]
        else:
            broken = head + f"ON CONFLICT ({pk}) DO UPDATE SET {c} = {c} + EXCLUDED.{c}"
            truth, only = head + f"ON CONFLICT ({pk}) DO UPDATE SET {c} = {name}.{c} + EXCLUDED.{c}", ["postgres"]
    elif bug in ("ch_insert_order", "ch_insert_count"):
        same = [[c["name"] for c in t["cols"] if c["kind"] == kind] for kind in ("cat", "int", "money", "name")]
        pair = next((g for g in rnd.sample(same, len(same)) if len(g) >= 2), None)
        if not pair:
            return None
        a, b = rnd.sample(pair, 2)
        if bug == "ch_insert_order":
            broken = f"INSERT INTO {name} ({pk}, {a}, {b})\nSELECT {pk} + 100000, {b}, {a}\nFROM {name}\nWHERE {pk} = {k}"
            truth = f"INSERT INTO {name} ({pk}, {a}, {b})\nSELECT {pk} + 100000, {a}, {b}\nFROM {name}\nWHERE {pk} = {k}"
        else:
            broken = rnd.choice([f"INSERT INTO {name} ({pk}, {a}, {b})\nVALUES ({len(t['rows']) + 9}, NULL)",
                                 f"INSERT INTO {name} ({pk}, {a}, {b})\nSELECT {pk} + 100000, {a}\nFROM {name}\nWHERE {pk} = {k}"])
            truth = None
    elif bug == "ch_limit_pg":
        if not tcats:
            return None
        c = rnd.choice(tcats)
        cond = f"{c['name']} = '{rnd.choice(c['params'])}'"
        verb = rnd.choice(["DELETE", "UPDATE"]) if tnums else "DELETE"
        n = rnd.randint(1, 3)
        if verb == "DELETE":
            broken = f"DELETE FROM {name}\nWHERE {cond}\nORDER BY {pk}\nLIMIT {n}"
            truth = f"DELETE FROM {name}\nWHERE {pk} IN (SELECT {pk} FROM {name} WHERE {cond} ORDER BY {pk} LIMIT {n})"
        else:
            broken = f"UPDATE {name}\nSET {tnums[0]} = 0\nWHERE {cond}\nORDER BY {pk}\nLIMIT {n}"
            truth = f"UPDATE {name}\nSET {tnums[0]} = 0\nWHERE {pk} IN (SELECT {pk} FROM {name} WHERE {cond} ORDER BY {pk} LIMIT {n})"
        engine, only = "postgresql", ["postgres"]
    else:                                                 # ch_dedupe: the key named id, as the rewrite assumes
        if pk.lower() != "id" or not tcats:
            return None
        c = rnd.choice(tcats)["name"]
        broken = f"DELETE a FROM {name} a\nJOIN {name} b ON a.{c} = b.{c}"
        truth = f"DELETE a FROM {name} a\nJOIN {name} b ON a.{c} = b.{c} AND a.{pk} > b.{pk}"
        only = ["mysql"]
    item = {"seed": seed, "bug": bug, "stmt": "change", "schema": schema, "table": target, "broken": broken, "truth": truth}
    if engine:
        item["engine"] = engine
    if only:
        item["only"] = only
    return item


def _effect(eng, sql, table, pk):
    """What a statement does: the rows a SELECT returns, or the table after an UPDATE/DELETE (rolled back afterwards)."""
    eng.begin()
    try:
        out = eng.run(sql)
        if not isinstance(out, (list, tuple)):            # a SELECT's rows (a tuple on MySQL/MariaDB), else the changed table
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
