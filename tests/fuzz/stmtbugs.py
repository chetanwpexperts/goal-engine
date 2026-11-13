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
        "ch_dedupe", "ch_delete_no_target", "ch_delete_join_pg",
        # night 6: table design (keys, types, one fact in one place) and each engine's own CREATE TABLE words and limits
        "sd_money_float", "sd_date_text", "sd_number_text", "sd_code_number", "sd_no_pk", "sd_fk_index_pg", "sd_utf8", "sd_myisam_fk",
        "sd_dialect_pg", "sd_dialect_my", "sd_two_pk", "sd_fk_type", "sd_varchar_len", "sd_reserved", "sd_row_big", "sd_fk_order",
        "sd_over_wide", "sd_list_col", "sd_repeat", "sd_parent_copy", "sd_timestamp",
        # night 7: CASE (first true WHEN wins, = NULL), WITH RECURSIVE (the keyword, something that ends it), windows (where they
        # stand, frames, ties, PARTITION BY said in words, ORDER BY they need, nesting)
        "wf_case_order", "wf_case_and", "wf_case_null", "wf_case_else_sort", "wf_rec_keyword", "wf_rec_no_stop", "wf_win_where",
        "wf_win_alias", "wf_last_value", "wf_running_ties", "wf_partition_said", "wf_nth_rank", "wf_ties_rank", "wf_no_order",
        "wf_nested", "wf_cte_cols",
        # night 8: speed rewrites that must return exactly the same rows (a condition on the bare column, OR across two tables
        # as UNION ALL, deep OFFSET as keyset paging)
        "ix_year", "ix_year_month", "ix_day", "ix_cast_day", "ix_format", "ix_arith", "ix_coalesce", "ix_left", "ix_or_join",
        "ix_keyset",
        # night 9: locks and big data (a huge change said in words -> batches; queue workers -> SKIP LOCKED; N+1 -> one statement)
        "lk_batch_delete", "lk_batch_update", "lk_skip_locked", "lk_nplus",
        # night 10: another database's words in the chosen one (rewritten in its own words, same values on that engine), and
        # spellings that run but mean something else there (date - date, INTERVAL '2' WEEK, CAST AS CHAR, ^)
        "pt_top", "pt_rownum", "pt_dateadd", "pt_datediff_ss", "pt_ss_funcs", "pt_brackets", "pt_my_dates_pg", "pt_my_funcs_pg",
        "pt_pg_funcs_my", "pt_date_minus", "pt_interval_week", "pt_cast_char", "pt_power",
        # night 10A: a query from code pasted alone: a placeholder inside quotes (the value never goes in) and a placeholder
        # where a name goes (FROM ? is refused, ORDER BY ? sorts by a constant); the fix is run with the value bound
        "cp_like_quoted", "cp_dollar_quoted", "cp_order_param", "cp_from_param",
        # night 10B: the project's own schema decides (the same tables, created on the test databases, given as the project's
        # CREATE TABLE script): a column the table doesn't have (fixed to the close one), a text column compared with a number,
        # a number joined to text, and findings the schema proves (a nullable column in NOT IN, a DATETIME in BETWEEN, a filter
        # no index serves) or rules out (a NOT NULL column in NOT IN, a DATE in BETWEEN, a join on the parent's key under DISTINCT)
        "ps_unknown_col", "ps_text_number", "ps_join_types", "ps_not_in_nullable", "ps_not_in_notnull", "ps_between_time",
        "ps_between_date", "ps_distinct_parent", "ps_index_missing",
        # night 11: query-only reasoning: every finding says how sure it is (proven / likely risk / needs schema / needs
        # execution) and the schema read from the query never names a table or column the database doesn't have; planted: a
        # parent total with one child table joined (a risk: offered EXISTS / no join, proven to give the intended totals),
        # NOT IN over a nullable column (needs schema), '= NULL' (proven), ties before LIMIT (needs schema), an alias never
        # defined (proven)
        "qo_fanout", "qo_fanout_left", "qo_not_in", "qo_eq_null", "qo_ties", "qo_unknown_alias",
        # night 12: blocks and the grain at every stage: a parent total multiplied by a child join INSIDE a CTE or a derived
        # table (found in its block, EXISTS offered and proven), a CTE that reads one written after it (reordered, run), a
        # block's total joined back at a finer grain (found), '= NULL' inside a CTE (fixed in place), and a join on the key a
        # CTE's own GROUP BY proves (quiet: no multiplication may be claimed)
        "gr_cte_fanout", "gr_derived_fanout", "gr_cte_order", "gr_cross", "gr_inner_eq_null", "gr_block_key",
        # night 13: the remaining structural checks: a total in SELECT that never names the outer row, a correlation that compares
        # the inner table with itself or a link with the wrong outer table (the link offered, proven to give the intended rows
        # when the names follow the usual pattern), the same subquery written twice (worked out once: the same rows), UNION where
        # the parts can't share a row and list a key (UNION ALL: the same rows), UNION ALL when each row was wanted once, a
        # HAVING condition on a GROUP BY column (WHERE: the same rows), a JOIN with no ON (an error on PostgreSQL), and a
        # correct correlated subquery (quiet)
        "st_uncorr", "st_inner", "st_corr_key", "st_repeat", "st_same", "st_union_all", "st_union_dups", "st_having_key",
        "st_no_on", "st_quiet_corr"]
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
         "ch_delete_join_pg": "delete_join_syntax",
         "sd_money_float": "money_float", "sd_date_text": "date_as_text", "sd_number_text": "number_as_text",
         "sd_code_number": "code_as_number", "sd_no_pk": "no_primary_key", "sd_fk_index_pg": "fk_no_index", "sd_utf8": "utf8mb3",
         "sd_myisam_fk": "myisam_fk", "sd_dialect_pg": "ddl_dialect", "sd_dialect_my": "ddl_dialect", "sd_two_pk": "two_primary_keys",
         "sd_fk_type": "fk_type", "sd_varchar_len": "varchar_length", "sd_reserved": "reserved_name", "sd_row_big": "row_too_big",
         "sd_fk_order": "fk_order", "sd_over_wide": "over_wide", "sd_list_col": "list_in_column", "sd_repeat": "repeating_group",
         "sd_parent_copy": "parent_copy", "sd_timestamp": "timestamp_range",
         "wf_case_order": "case_unreachable", "wf_case_and": "case_unreachable", "wf_case_null": "case_null",
         "wf_case_else_sort": "case_no_else", "wf_rec_keyword": "cte_recursive_kw", "wf_rec_no_stop": "cte_no_stop",
         "wf_win_where": "win_where", "wf_win_alias": "win_alias_where", "wf_last_value": "win_last_value",
         "wf_running_ties": "win_running_ties", "wf_partition_said": "win_partition_said", "wf_nth_rank": "win_rank_ties",
         "wf_ties_rank": "ties", "wf_no_order": "win_no_order", "wf_nested": "win_nested", "wf_cte_cols": "cte_columns",
         "ix_year": "sargable", "ix_year_month": "sargable", "ix_day": "sargable", "ix_cast_day": "sargable", "ix_format": "sargable",
         "ix_arith": "sargable", "ix_coalesce": "sargable", "ix_left": "sargable", "ix_or_join": "or_union", "ix_keyset": "keyset",
         "lk_batch_delete": "batch_change", "lk_batch_update": "batch_change", "lk_skip_locked": "skip_locked", "lk_nplus": "n_plus_one",
         "pt_top": "dialect", "pt_rownum": "dialect", "pt_dateadd": "dialect", "pt_datediff_ss": "dialect", "pt_ss_funcs": "dialect",
         "pt_brackets": "dialect", "pt_my_dates_pg": "dialect", "pt_my_funcs_pg": "dialect", "pt_pg_funcs_my": "dialect",
         "pt_date_minus": "date_math", "pt_interval_week": "interval_unit", "pt_cast_char": "cast_char", "pt_power": "power_xor",
         "cp_like_quoted": "quoted_placeholder", "cp_dollar_quoted": "quoted_placeholder", "cp_order_param": "placeholder_name",
         "cp_from_param": "placeholder_name",
         "ps_unknown_col": "schema_column", "ps_text_number": "implicit_cast", "ps_join_types": "join_types",
         "ps_not_in_nullable": "not_in", "ps_not_in_notnull": "not_in", "ps_between_time": "date_range",
         "ps_between_date": "date_range", "ps_distinct_parent": "distinct_join", "ps_index_missing": "index_missing",
         "qo_fanout": "join_fanout", "qo_fanout_left": "join_fanout", "qo_not_in": "not_in", "qo_eq_null": "null",
         "qo_ties": "stable_order", "qo_unknown_alias": "alias",
         "gr_cte_fanout": "join_fanout", "gr_derived_fanout": "join_fanout", "gr_cte_order": "cte_order", "gr_cross": "join_fanout",
         "gr_inner_eq_null": "null", "gr_block_key": "join_fanout",
         "st_uncorr": "scalar_uncorr", "st_inner": "corr_inner", "st_corr_key": "corr_key", "st_repeat": "repeat_subquery",
         "st_same": "same_subquery", "st_union_all": "union_all", "st_union_dups": "union_all_dups", "st_having_key": "having_key",
         "st_no_on": "join", "st_quiet_corr": "corr_key"}
# night 12: a correct query: the planted topic must NOT be said, and no error / wrong at all
QUIET = {"gr_block_key", "st_quiet_corr"}
# night 11: the class the planted finding must carry (sqlconfidence.classify); and for every statement, every finding has one
CLASS = {"st_uncorr": "likely_risk", "st_inner": "likely_risk", "st_corr_key": "likely_risk", "st_repeat": "needs_execution",
         "st_same": "needs_execution", "st_union_all": "needs_schema", "st_union_dups": "likely_risk", "st_having_key": "needs_execution",
         "gr_cte_fanout": "likely_risk", "gr_derived_fanout": "likely_risk", "gr_cte_order": "proven", "gr_cross": "likely_risk",
         "gr_inner_eq_null": "proven",
         "qo_fanout": "likely_risk", "qo_fanout_left": "likely_risk", "qo_not_in": "needs_schema", "qo_eq_null": "proven",
         "qo_ties": "needs_schema", "qo_unknown_alias": "proven",
         "eq_null_dml": "proven", "agg_in_where": "proven", "between_reversed": "proven", "or_value": "proven", "like_star": "proven",
         "count_case": "proven", "wf_win_where": "proven", "wf_rec_keyword": "proven", "nested_agg": "proven",
         "anti_nullable": "needs_schema", "not_in_outer": "needs_schema", "scalar_rows": "needs_schema", "limit_no_order": "needs_schema",
         "distinct_join": "likely_risk", "semi_join": "likely_risk", "and_or": "likely_risk", "avg_avg": "likely_risk",
         "self_pairs": "likely_risk", "like_contains": "likely_risk",
         "ix_year": "needs_execution", "ix_year_month": "needs_execution", "ix_day": "needs_execution", "ix_left": "needs_execution",
         "ix_arith": "needs_execution", "ix_coalesce": "needs_execution", "ix_format": "needs_execution", "ix_cast_day": "needs_execution",
         "ix_or_join": "needs_execution", "ix_keyset": "needs_execution", "lk_batch_delete": "needs_execution",
         "ps_not_in_nullable": "proven", "ps_between_time": "proven", "ps_unknown_col": "proven", "ps_index_missing": "needs_execution"}
# night 10B: the planted finding must be RULED OUT by the project's schema (absent, and listed as dropped with the reason)
RULED_OUT = {"ps_not_in_notnull", "ps_between_date", "ps_distinct_parent"}
NO_FIX = {"agg_in_where", "limit_no_order", "distinct_paren", "comma_cartesian", "on_one_side", "natural_join", "sum_distinct",
          "exists_uncorr", "sub_outer_col", "scalar_rows", "sub_limit_order", "ch_insert_count",
          "sd_two_pk", "sd_list_col", "sd_repeat", "sd_parent_copy", "wf_no_order", "wf_nested", "wf_cte_cols",
          "ix_keyset", "cp_order_param", "cp_from_param", "ps_join_types", "ps_index_missing", "qo_ties", "qo_unknown_alias", "gr_cross", "gr_block_key"}   # found is enough
# a 'check' (depends on engine, data or intent) whose suggested rewrite must do exactly what the person meant
FIX_THE_CHECK = {"same_table", "like_contains", "and_or", "division", "double_quotes", "pk_pk", "anti_nullable", "self_pairs",
                 "distinct_join", "sum_null", "avg_avg", "pct_total", "sub_sum_null", "all_null", "in_list_null", "not_in_outer",
                 "semi_join", "ch_set_sub_null",
                 "sd_date_text", "sd_number_text", "sd_code_number", "sd_no_pk", "sd_fk_index_pg", "sd_over_wide", "sd_timestamp",
                 "sd_reserved", "wf_case_else_sort", "wf_running_ties",
                 "ix_year", "ix_year_month", "ix_day", "ix_cast_day", "ix_format", "ix_arith", "ix_coalesce", "ix_left", "ix_or_join",
                 "lk_skip_locked", "pt_power", "ps_text_number", "ps_not_in_nullable", "ps_between_time",
                 "qo_fanout", "qo_fanout_left", "qo_not_in", "gr_cte_fanout", "gr_derived_fanout",
                 "st_uncorr", "st_inner", "st_corr_key", "st_repeat", "st_same", "st_union_all", "st_union_dups", "st_having_key"}


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
    elif bug.startswith("sd_"):
        return _schema_bug(seed, bug, rnd, schema, tables, t)
    elif bug.startswith("wf_"):
        return _win_bug(seed, bug, rnd, schema, tables, t)
    elif bug.startswith("ix_"):
        return _speed_bug(seed, bug, rnd, schema, tables, t, wrap, stmt)
    elif bug.startswith("lk_"):
        return _lock_bug(seed, bug, rnd, schema, tables, t)
    elif bug.startswith("pt_"):
        return _port_bug(seed, bug, rnd, schema, tables, t)
    elif bug.startswith("cp_"):
        return _param_bug(seed, bug, rnd, schema, tables, t)
    elif bug.startswith("ps_"):
        return _project_bug(seed, bug, rnd, schema, tables, t, wrap, stmt)
    elif bug.startswith("qo_"):
        return _query_only_bug(seed, bug, rnd, schema, tables, t, wrap, stmt)
    elif bug.startswith("gr_"):
        return _grain_bug(seed, bug, rnd, schema, tables, t)
    elif bug.startswith("st_"):
        return _struct_bug(seed, bug, rnd, schema, tables, t)
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


SD_TYPES = {"mysql": {"datetime": "DATETIME"}, "postgresql": {"datetime": "TIMESTAMP"}, None: {"datetime": "TIMESTAMP"}}
SD_RESERVED = {"order": "sort_order", "desc": "description", "group": "group_name", "check": "check_value", "limit": "limit_value",
               "default": "default_value"}


def _sd_type(c, fam):
    k = c["kind"]
    return {"pk": "INT NOT NULL", "fk": "INT", "int": "INT", "money": "DECIMAL(12,2)", "date": "DATE"}.get(k) or \
        (SD_TYPES[fam]["datetime"] if k == "datetime" else "VARCHAR(80)")


def _sd_design(t, name, fam, types=None, names=None, extra=None, pk=True, fks=True, tail="", engine_opt=None, fk_index=None):
    """CREATE TABLE <name> with t's columns (same order as the real table, so INSERT ... SELECT * fills it)."""
    types, names = types or {}, names or {}
    cols = []
    for c in t["cols"]:
        nm = names.get(c["name"], c["name"])
        cols.append(f"{nm} {types.get(c['name'], _sd_type(c, fam))}")
    cols += extra or []
    if pk:
        cols.append(f"PRIMARY KEY ({names.get(t['pk'], t['pk'])})")
    if fks:
        cols += [f"FOREIGN KEY ({c}) REFERENCES {p} ({ppk})" for c, p, ppk in t["fks"]]
    body = ",\n  ".join(cols)
    out = f"CREATE TABLE {name} (\n  {body}\n)"
    if engine_opt:
        out += " " + engine_opt
    if fk_index is None:
        fk_index = fam != "mysql"                       # a good design indexes its links where the engine doesn't (PostgreSQL)
    if fks and fk_index:
        tail = "".join(f"\nCREATE INDEX ix_{name}_{c} ON {name} ({c});" for c, _p, _k in t["fks"]) + tail
    return out + ";" + tail


def _schema_bug(seed, bug, rnd, schema, tables, t):
    """Night 6: a table design with one planted mistake, made from a random real table (same columns and data). truth = the
    design the person meant; its rows (the real table's, copied in) and its catalog (types, keys, indexes) are the answer."""
    from app.agents import sqlschema as SS

    if schema["style"] != "snake":
        return None                                      # the names must read the same on both engines
    name = f"n6{t['name']}"                       # a new table, named like the real one (its singular still reads)
    fam = rnd.choice(["mariadb", "postgresql", None])
    fk_of = {c["name"]: c for c in t["cols"] if c["kind"] == "fk"}
    engine, only, extra_drop = fam, None, []
    fmy = {"mariadb": "mysql", "mysql": "mysql", "postgresql": "postgresql", None: None}[fam]

    def col(kinds, test=lambda c: True):
        cs = [c for c in t["cols"] if c["kind"] in kinds and test(c)]
        return rnd.choice(cs) if cs else None

    if bug == "sd_money_float":
        c = col(("money",), lambda c: SS.MONEY.search(c["name"]) and not re.search(r"(^|_)rate$", c["name"]))
        if not c:
            return None
        bad = rnd.choice({"mysql": ["FLOAT", "DOUBLE", "FLOAT(10,2)"], "postgresql": ["REAL", "DOUBLE PRECISION", "FLOAT"],
                          None: ["REAL", "FLOAT", "DOUBLE PRECISION"]}[fmy])
        good = "DECIMAL(10,2)" if bad == "FLOAT(10,2)" else "DECIMAL(12,2)"
        broken, truth = _sd_design(t, name, fmy, {c["name"]: bad}), _sd_design(t, name, fmy, {c["name"]: good})
    elif bug == "sd_date_text":
        c = col(("date", "datetime"), lambda c: SS.DATEISH.search(c["name"]) and
                (c["kind"] == "date") == bool(SS.DAY_ONLY.search(c["name"]) and not re.search(r"(^|_)(at|timestamp|datetime)$", c["name"])))
        if not c or (c["kind"] == "datetime" and fmy is None):
            return None
        broken = _sd_design(t, name, fmy, {c["name"]: rnd.choice(["VARCHAR(30)", "CHAR(19)", "TEXT"])})
        truth = _sd_design(t, name, fmy)
    elif bug == "sd_number_text":
        c = col(("money", "int"), lambda c: (SS.MONEY.search(c["name"]) or SS.COUNTISH.search(c["name"])) and
                (c["kind"] == "money") == bool(SS.MONEY.search(c["name"])))
        if not c:
            return None
        broken, truth = _sd_design(t, name, fmy, {c["name"]: rnd.choice(["VARCHAR(20)", "TEXT", "CHAR(12)"])}), _sd_design(t, name, fmy)
    elif bug == "sd_code_number":
        c = col(("phone",))
        if not c:
            return None
        broken = _sd_design(t, name, fmy, {c["name"]: "BIGINT"})
        truth = _sd_design(t, name, fmy, {c["name"]: "VARCHAR(20)"})
    elif bug == "sd_no_pk":
        if not (t["pk"] == "id" or t["pk"].endswith("_id")):
            return None
        broken, truth = _sd_design(t, name, fmy, pk=False), _sd_design(t, name, fmy)
    elif bug == "sd_fk_index_pg":
        if not t["fks"]:
            return None
        engine, only = "postgresql", ["postgres"]
        broken = _sd_design(t, name, "postgresql", fk_index=False)
        truth = _sd_design(t, name, "postgresql")
    elif bug == "sd_utf8":
        engine, only = rnd.choice(["mariadb", "mysql"]), ["mysql"]
        cs = rnd.choice(["utf8", "utf8mb3"])
        broken = _sd_design(t, name, "mysql", engine_opt=f"ENGINE=InnoDB DEFAULT CHARSET={cs}")
        truth = _sd_design(t, name, "mysql", engine_opt="ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
    elif bug == "sd_myisam_fk":
        if not t["fks"]:
            return None
        engine, only = rnd.choice(["mariadb", "mysql"]), ["mysql"]
        broken = _sd_design(t, name, "mysql", engine_opt="ENGINE=MyISAM")
        truth = _sd_design(t, name, "mysql", engine_opt="ENGINE=InnoDB")
    elif bug == "sd_dialect_pg":
        engine, only = "postgresql", ["postgres"]
        cat = col(("cat",))
        my_types = {t["pk"]: "INT(11) NOT NULL AUTO_INCREMENT"}
        pg_types = {t["pk"]: "INTEGER NOT NULL GENERATED BY DEFAULT AS IDENTITY"}
        for c in t["cols"]:
            if c["kind"] == "datetime":
                my_types[c["name"]], pg_types[c["name"]] = "DATETIME", "TIMESTAMP"
            elif c["kind"] == "int" and rnd.random() < 0.5:
                my_types[c["name"]], pg_types[c["name"]] = "INT(11) UNSIGNED", "INTEGER"
        extra_my = [f"KEY ix_{cat['name']} ({cat['name']})"] if cat else []
        broken = _sd_design(t, name, "mysql", my_types, extra=extra_my, engine_opt="ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        if rnd.random() < 0.5:
            broken = re.sub(r"\b(" + "|".join(re.escape(c["name"]) for c in t["cols"]) + r")\b(?=\s+[A-Z])", r"`\1`", broken)
        truth = _sd_design(t, name, "postgresql", pg_types, fk_index=False) + (f"\nCREATE INDEX ix_{name}_{cat['name']} ON {name} ({cat['name']});" if cat else "")
    elif bug == "sd_dialect_my":
        engine, only = rnd.choice(["mariadb", "mysql"]), ["mysql"]
        pg_types = {t["pk"]: "INT GENERATED BY DEFAULT AS IDENTITY"}
        my_types = {t["pk"]: "INT AUTO_INCREMENT"}
        dts = [c for c in t["cols"] if c["kind"] == "datetime"]
        for c in dts:
            pg_types[c["name"]], my_types[c["name"]] = rnd.choice(["TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"]), "DATETIME"
        if not dts:
            return None
        broken, truth = _sd_design(t, name, "postgresql", pg_types), _sd_design(t, name, "mysql", my_types)
    elif bug == "sd_two_pk":
        c = col(("cat", "name", "email"))
        if not c:
            return None
        broken = _sd_design(t, name, fmy, {c["name"]: "VARCHAR(80) PRIMARY KEY"})
        truth = None
    elif bug == "sd_fk_type":
        if not t["fks"]:
            return None
        engine, only = rnd.choice(["mariadb", "mysql"]), ["mysql"]
        c = rnd.choice(t["fks"])[0]
        broken = _sd_design(t, name, "mysql", {c: rnd.choice(["BIGINT", "INT UNSIGNED", "SMALLINT"])})
        truth = _sd_design(t, name, "mysql")
        # the parent's key is in the script (a real table's key is outside the text, so its type isn't known)
        p = schema["tables"][fk_of[c]["ref"]]
        pdesign = _sd_design(p, f"n6p{p['name']}", "mysql", fks=False)
        broken = pdesign + "\n" + broken.replace(f"REFERENCES {p['name']} ", f"REFERENCES n6p{p['name']} ")
        truth = pdesign + "\n" + truth.replace(f"REFERENCES {p['name']} ", f"REFERENCES n6p{p['name']} ")
        extra_drop = [f"n6p{p['name']}"]
    elif bug == "sd_varchar_len":
        c = col(("cat",))
        if not c:
            return None
        engine, only = rnd.choice(["mariadb", "mysql"]), ["mysql"]
        broken = _sd_design(t, name, "mysql", {c["name"]: "VARCHAR"})
        truth = _sd_design(t, name, "mysql", {c["name"]: "VARCHAR(255)"})
    elif bug == "sd_reserved":
        c = col(("cat", "int", "text"))
        if not c:
            return None
        w = rnd.choice(sorted(SD_RESERVED))
        engine, fmy = None, None
        broken = _sd_design(t, name, None, names={c["name"]: w})
        truth = _sd_design(t, name, None, names={c["name"]: SD_RESERVED[w]})
    elif bug == "sd_row_big":
        cs = [c for c in t["cols"] if c["kind"] in ("cat", "name", "text", "email")]
        if len(cs) < 2:
            return None
        a, b = cs[:2]
        engine, only = rnd.choice(["mariadb", "mysql"]), ["mysql"]
        broken = _sd_design(t, name, "mysql", {a["name"]: "VARCHAR(10000)", b["name"]: "VARCHAR(9000)"})
        truth = _sd_design(t, name, "mysql", {a["name"]: "TEXT", b["name"]: "VARCHAR(9000)"})
    elif bug == "sd_fk_order":
        if not t["fks"]:
            return None
        c, pname, ppk = rnd.choice(t["fks"])
        p = schema["tables"][pname]
        pdesign = _sd_design(p, f"n6p{pname}", fmy, fks=False)
        child_b = _sd_design(t, name, fmy).replace(f"REFERENCES {pname} ", f"REFERENCES n6p{pname} ")
        broken, truth = child_b + "\n" + pdesign, pdesign + "\n" + child_b
        extra_drop = [f"n6p{pname}"]
    elif bug == "sd_over_wide":
        c = col(("email", "cat", "name"), lambda c: c["name"] in SS.SHORT_FIELD)
        if not c:
            return None
        broken = _sd_design(t, name, fmy, {c["name"]: f"VARCHAR({rnd.choice([1000, 2000, 5000])})"})
        truth = _sd_design(t, name, fmy, {c["name"]: f"VARCHAR({SS.SHORT_FIELD[c['name']]})"})
    elif bug == "sd_list_col":
        broken = _sd_design(t, name, fmy, extra=[f"{rnd.choice(['tag_ids', 'tags', 'skills', 'product_ids'])} VARCHAR(200)"])
        truth = None
    elif bug == "sd_repeat":
        stem = rnd.choice(["phone", "email", "item", "alt_mobile"])
        broken = _sd_design(t, name, fmy, extra=[f"{stem}1 VARCHAR(40)", f"{stem}2 VARCHAR(40)"] + ([f"{stem}3 VARCHAR(40)"] if rnd.random() < .5 else []))
        truth = None
    elif bug == "sd_parent_copy":
        fks = [c for c in fk_of if c.endswith("_id")]
        if not fks:
            return None
        stem = rnd.choice(fks)[:-3]
        broken = _sd_design(t, name, fmy, extra=[f"{stem}_{rnd.choice(['name', 'email', 'city', 'phone'])} VARCHAR(80)"])
        truth = None
    else:                                                 # sd_timestamp: a birth date or expiry in a MySQL/MariaDB TIMESTAMP
        c = col(("date",), lambda c: SS.OLD_OR_FAR.search(c["name"]))
        if not c:
            return None
        engine, only = rnd.choice(["mariadb", "mysql"]), ["mysql"]
        broken = _sd_design(t, name, "mysql", {c["name"]: "TIMESTAMP NULL"})
        truth = _sd_design(t, name, "mysql", {c["name"]: "DATE NULL"})
    if only is None and fmy:
        only = ["mysql"] if fmy == "mysql" else ["postgres"]
    item = {"seed": seed, "bug": bug, "stmt": "ddl", "schema": schema, "table": name, "source": t["name"], "broken": broken,
            "truth": truth, "drop": [name] + extra_drop}
    if engine:
        item["engine"] = engine
    if only:
        item["only"] = only
    return item


def _drop_ddl(eng, names):
    eng.rollback()
    for n in names:
        try:
            eng.run(f"DROP TABLE IF EXISTS {n}" + (" CASCADE" if eng.kind == "postgres" else ""))
        except Exception:  # noqa: BLE001
            eng.rollback()


def _ddl_effect(eng, script, item):
    """What a design makes: its columns and types, primary key, indexes, foreign keys, engine and charset, and the real table's
    rows copied into it. ('refused', ...) when the design doesn't run."""
    from app.agents.sqlroutine import split_script

    name = item["table"]
    _drop_ddl(eng, item["drop"])
    try:
        try:
            for st in split_script(script):
                eng.run(st)
        except Exception as exc:  # noqa: BLE001
            eng.rollback()
            return ("refused", str(exc)[:80])
        if eng.kind == "mysql":
            cat = eng.run("SELECT column_name, column_type, is_nullable, column_key, character_set_name FROM information_schema.columns "
                          f"WHERE table_schema = DATABASE() AND table_name = '{name}' ORDER BY ordinal_position")
            idx = eng.run("SELECT column_name, seq_in_index, non_unique FROM information_schema.statistics WHERE table_schema = DATABASE() "
                          f"AND table_name = '{name}' ORDER BY 1, 2")
            more = eng.run(f"SELECT engine FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = '{name}'")
            fks = eng.run("SELECT column_name, referenced_table_name FROM information_schema.key_column_usage WHERE "
                          f"table_schema = DATABASE() AND table_name = '{name}' AND referenced_table_name IS NOT NULL ORDER BY 1")
        else:
            cat = eng.run("SELECT column_name, data_type, character_maximum_length, numeric_precision, numeric_scale, is_nullable, "
                          f"is_identity FROM information_schema.columns WHERE table_name = '{name}' ORDER BY ordinal_position")
            idx = [(re.sub(r"INDEX \S+ ON", "INDEX ON", r[0]),) for r in
                   eng.run(f"SELECT indexdef FROM pg_indexes WHERE tablename = '{name}' ORDER BY indexdef")]
            idx.sort()
            more = []
            fks = eng.run("SELECT kcu.column_name FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu "
                          "ON kcu.constraint_name = tc.constraint_name WHERE tc.constraint_type = 'FOREIGN KEY' AND "
                          f"tc.table_name = '{name}' ORDER BY 1")
        try:
            eng.run(f"INSERT INTO {name} SELECT * FROM {item['source']}")
            rows = Counter(tuple(str(x) for x in r) for r in eng.run(f"SELECT * FROM {name}"))
        except Exception as exc:  # noqa: BLE001
            eng.rollback()
            rows = ("insert refused", type(exc).__name__)
        return (tuple(map(tuple, cat)), tuple(map(tuple, idx)), tuple(map(tuple, more)), tuple(map(tuple, fks)), rows)
    finally:
        _drop_ddl(eng, item["drop"])


def _check_ddl(item, engines, res, tag) -> list[str]:
    from app.agents import sqlcorrect

    bad = []
    if item["truth"] is not None:
        tf = [f["title_en"] for f in sqlcorrect.check(item["truth"], engine=item.get("engine"))["findings"] if f["kind"] in ("error", "wrong")]
        if tf:
            bad.append(f"{tag}: false alarm on the intended design {tf}\n{item['truth']}")
    want = TOPIC[item["bug"]]
    sure = any(f["topic"] == want and f["kind"] in ("error", "wrong") for f in res["findings"]) or item["bug"] in FIX_THE_CHECK
    if item["bug"] in NO_FIX or item.get("no_fix") or not sure:
        return bad
    fixed = res["corrected"]
    if not fixed:
        return bad + [f"{tag}: no corrected design\n{item['broken']}"]
    again = [f["title_en"] for f in sqlcorrect.check(fixed, engine=item.get("engine"))["findings"] if f["kind"] in ("error", "wrong")]
    if again:
        bad.append(f"{tag}: the corrected design still has mistakes {again}\n{fixed}")
    for name, eng in engines.items():
        if item.get("only") and name not in item["only"]:
            continue
        got, exp = _ddl_effect(eng, fixed, item), _ddl_effect(eng, item["truth"], item)
        if exp[0] == "refused":
            bad.append(f"{tag}: on {name} the intended design is refused {exp}\n{item['truth']}")
        elif got != exp:
            bad.append(f"{tag}: on {name} the corrected design makes something else\n{fixed}\n--- got {str(got)[:300]}\n--- want {str(exp)[:300]}")
    return bad


def _win_bug(seed, bug, rnd, schema, tables, t):
    """Night 7: CASE, WITH RECURSIVE and window mistakes on a random real table. truth = the statement the person meant."""
    pk, name = t["pk"], t["name"]
    nums = [c["name"] for c in t["cols"] if c["kind"] in ("int", "money")]
    groups = [c for c in t["cols"] if c["kind"] in ("fk", "cat")]
    cats = [c for c in t["cols"] if c["kind"] == "cat" and c["params"] and all(isinstance(v, str) for v in c["params"])]
    words, engine = "", None
    if bug in ("wf_case_order", "wf_case_and", "wf_case_null", "wf_win_where", "wf_win_alias", "wf_last_value", "wf_running_ties",
               "wf_partition_said", "wf_nth_rank", "wf_ties_rank", "wf_no_order", "wf_nested") and not nums:
        return None
    if bug in ("wf_last_value", "wf_running_ties", "wf_partition_said", "wf_ties_rank") and not groups:
        return None
    if bug in ("wf_case_and", "wf_case_else_sort") and not cats:
        return None
    m = rnd.choice(nums) if nums else None
    g = rnd.choice(groups)["name"] if groups else None
    if bug == "wf_case_order":
        vals = sorted(r[t["cols"].index(next(c for c in t["cols"] if c["name"] == m))] for r in t["rows"]
                      if r[t["cols"].index(next(c for c in t["cols"] if c["name"] == m))] is not None)
        if len(vals) < 4:
            return None
        lo, hi = int(vals[len(vals) // 4]), int(vals[3 * len(vals) // 4])
        if lo >= hi:
            return None
        op = rnd.choice([">", ">="])
        truth = (f"SELECT {pk}, CASE WHEN {m} {op} {hi} THEN 'high' WHEN {m} {op} {lo} THEN 'mid' ELSE 'low' END AS band\n"
                 f"FROM {name}\nORDER BY {pk}")
        broken = (f"SELECT {pk}, CASE WHEN {m} {op} {lo} THEN 'mid' WHEN {m} {op} {hi} THEN 'high' ELSE 'low' END AS band\n"
                  f"FROM {name}\nORDER BY {pk}")
    elif bug == "wf_case_and":
        c = rnd.choice(cats)
        v = rnd.choice(c["params"])
        lim = rnd.randint(1, 50)
        cond = f"{m} > {lim}" if nums and rnd.random() < 0.5 else f"{pk} > {lim}"
        truth = (f"SELECT {pk}, CASE WHEN {cond} AND {c['name']} = '{v}' THEN 'both' WHEN {cond} THEN 'one' ELSE 'none' END AS tag\n"
                 f"FROM {name}")
        broken = (f"SELECT {pk}, CASE WHEN {cond} THEN 'one' WHEN {cond} AND {c['name']} = '{v}' THEN 'both' ELSE 'none' END AS tag\n"
                  f"FROM {name}")
    elif bug == "wf_case_null":
        truth = f"SELECT {pk}, CASE WHEN {m} IS NULL THEN 'missing' ELSE 'given' END AS state\nFROM {name}"
        broken = f"SELECT {pk}, CASE {m} WHEN NULL THEN 'missing' ELSE 'given' END AS state\nFROM {name}"
    elif bug == "wf_case_else_sort":
        c = rnd.choice(cats)
        if len(c["params"]) < 3:
            return None
        a, b = rnd.sample(c["params"], 2)
        truth = f"SELECT {pk}\nFROM {name}\nORDER BY CASE {c['name']} WHEN '{a}' THEN 1 WHEN '{b}' THEN 2 ELSE 3 END, {pk}"
        broken = f"SELECT {pk}\nFROM {name}\nORDER BY CASE {c['name']} WHEN '{a}' THEN 1 WHEN '{b}' THEN 2 END, {pk}"
    elif bug == "wf_rec_keyword":
        k = rnd.randint(3, 12)
        body = f"n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n WHERE x < {k})\nSELECT t.{pk}\nFROM {name} t JOIN n ON n.x = t.{pk}"
        truth, broken = f"WITH RECURSIVE {body}", f"WITH {body}"
    elif bug == "wf_rec_no_stop":
        k = rnd.randint(3, 12)
        words = rnd.choice([f"the rows with ids 1 to {k}", f"numbers 1 to {k}", f"1 se {k} tak wale ids"])
        head = "n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n{})\nSELECT t.{} FROM {} t JOIN n ON n.x = t.{}"
        truth = "WITH RECURSIVE " + head.format(f" WHERE x < {k}", pk, name, pk)
        broken = "WITH RECURSIVE " + head.format("", pk, name, pk)
    elif bug == "wf_win_where":
        truth = f"SELECT {pk}\nFROM (SELECT {pk}, ROW_NUMBER() OVER (ORDER BY {m} DESC, {pk}) AS w FROM {name}) AS x\nWHERE w <= 3"
        broken = f"SELECT {pk}\nFROM {name}\nWHERE ROW_NUMBER() OVER (ORDER BY {m} DESC, {pk}) <= 3"
    elif bug == "wf_win_alias":
        fn = rnd.choice(["RANK", "DENSE_RANK"])
        truth = f"SELECT {pk}, r\nFROM (SELECT {pk}, {fn}() OVER (ORDER BY {m} DESC) AS r FROM {name}) AS x\nWHERE r <= 2"
        broken = f"SELECT {pk}, {fn}() OVER (ORDER BY {m} DESC) AS r\nFROM {name}\nWHERE r <= 2"
    elif bug == "wf_last_value":
        truth = (f"SELECT {pk}, LAST_VALUE({m}) OVER (PARTITION BY {g} ORDER BY {m} ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED "
                 f"FOLLOWING) AS v\nFROM {name}")
        broken = f"SELECT {pk}, LAST_VALUE({m}) OVER (PARTITION BY {g} ORDER BY {m}) AS v\nFROM {name}"
    elif bug == "wf_running_ties":
        truth = f"SELECT {pk}, SUM({m}) OVER (ORDER BY {g}, {pk} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS run\nFROM {name}"
        broken = f"SELECT {pk}, SUM({m}) OVER (ORDER BY {g}) AS run\nFROM {name}"
    elif bug == "wf_partition_said":
        noun = g[:-3] if g.endswith("_id") else g
        words = rnd.choice([f"running total of {m} per {noun}", f"har {noun} ka running total", f"a running {m} total for each {noun}"])
        truth = f"SELECT {pk}, {g}, SUM({m}) OVER (PARTITION BY {g} ORDER BY {pk}) AS run\nFROM {name}"
        broken = f"SELECT {pk}, {g}, SUM({m}) OVER (ORDER BY {pk}) AS run\nFROM {name}"
    elif bug == "wf_nth_rank":
        k = rnd.choice(["second", "2nd", "third", "3rd"])
        n = 2 if k in ("second", "2nd") else 3
        words = f"the rows with the {k} highest {m}"
        truth = f"SELECT {pk}\nFROM (SELECT {pk}, DENSE_RANK() OVER (ORDER BY {m} DESC) AS rn FROM {name} WHERE {m} IS NOT NULL) AS x\nWHERE rn = {n}"
        broken = f"SELECT {pk}\nFROM (SELECT {pk}, ROW_NUMBER() OVER (ORDER BY {m} DESC) AS rn FROM {name} WHERE {m} IS NOT NULL) AS x\nWHERE rn = {n}"
    elif bug == "wf_ties_rank":
        words = rnd.choice([f"the top {m} in each group, all of them if tied", f"highest {m} rows, keep ties"])
        truth = f"SELECT {pk}\nFROM (SELECT {pk}, RANK() OVER (PARTITION BY {g} ORDER BY {m} DESC) AS rn FROM {name} WHERE {m} IS NOT NULL) AS x\nWHERE rn = 1"
        broken = f"SELECT {pk}\nFROM (SELECT {pk}, ROW_NUMBER() OVER (PARTITION BY {g} ORDER BY {m} DESC) AS rn FROM {name} WHERE {m} IS NOT NULL) AS x\nWHERE rn = 1"
    elif bug == "wf_no_order":
        fn = rnd.choice(["LAG", "LEAD", "RANK", "DENSE_RANK", "FIRST_VALUE", "NTILE"])
        arg = {"RANK": "", "DENSE_RANK": "", "NTILE": "4"}.get(fn, m)
        truth = f"SELECT {pk}, {fn}({arg}) OVER (ORDER BY {pk}) AS v\nFROM {name}"
        broken = f"SELECT {pk}, {fn}({arg}) OVER () AS v\nFROM {name}"
        engine = rnd.choice([None, "mariadb", "postgresql"])
    elif bug == "wf_nested":
        truth = f"SELECT {pk}, SUM(r) OVER () AS v\nFROM (SELECT {pk}, ROW_NUMBER() OVER (ORDER BY {pk}) AS r FROM {name}) AS x"
        broken = f"SELECT {pk}, SUM(ROW_NUMBER() OVER (ORDER BY {pk})) OVER () AS v\nFROM {name}"
    else:                                                 # wf_cte_cols
        truth = f"WITH c ({pk}) AS (SELECT {pk} FROM {name})\nSELECT {pk} FROM c"
        broken = f"WITH c ({pk}, extra) AS (SELECT {pk} FROM {name})\nSELECT {pk} FROM c"
    if words:
        broken += ";\n\n" + words
    item = {"seed": seed, "bug": bug, "stmt": "select", "schema": schema, "table": name, "broken": broken, "truth": truth}
    if engine:
        item["engine"] = engine
    return item


def _speed_bug(seed, bug, rnd, schema, tables, t, wrap, stmt):
    """Night 8: the same rows, written so no index can serve them. truth = the form on the bare column (or the same query: the
    rewrite must give exactly its rows)."""
    import datetime as dt

    pk, name = t["pk"], t["name"]

    def col(*kinds):
        cs = [c for c in t["cols"] if c["kind"] in kinds]
        return rnd.choice(cs) if cs else None

    def vals(c):
        k = t["cols"].index(c)
        return [r[k] for r in t["rows"] if r[k] is not None]

    engine, only = None, None
    if bug in ("ix_year", "ix_year_month", "ix_format"):
        c = col("date", "datetime")
        if c is None or not vals(c):
            return None
        d = rnd.choice(vals(c))
        y, m, cn = d.year, d.month, c["name"]
        nxt = f"{y + (m == 12)}-{m % 12 + 1:02d}-01"
        if bug == "ix_year":
            op = rnd.choice(["=", ">=", "<", ">", "<="])
            form = rnd.choice(["YEAR({c})", "EXTRACT(YEAR FROM {c})"]).format(c=cn)
            truth_c = {"=": f"{cn} >= '{y}-01-01' AND {cn} < '{y + 1}-01-01'", ">=": f"{cn} >= '{y}-01-01'",
                       "<": f"{cn} < '{y}-01-01'", ">": f"{cn} >= '{y + 1}-01-01'", "<=": f"{cn} < '{y + 1}-01-01'"}[op]
            broken_c = f"{form} {op} {y}"
        elif bug == "ix_year_month":
            a, b = rnd.choice([("YEAR({c})", "MONTH({c})"), ("EXTRACT(YEAR FROM {c})", "EXTRACT(MONTH FROM {c})")])
            parts = [f"{a.format(c=cn)} = {y}", f"{b.format(c=cn)} = {m}"]
            rnd.shuffle(parts)
            broken_c = " AND ".join(parts)
            truth_c = f"{cn} >= '{y}-{m:02d}-01' AND {cn} < '{nxt}'"
        else:
            if rnd.random() < 0.5:
                broken_c, engine = f"DATE_FORMAT({cn}, '%Y-%m') = '{y}-{m:02d}'", "mariadb"
            else:
                broken_c, engine = f"TO_CHAR({cn}, 'YYYY-MM') = '{y}-{m:02d}'", "postgresql"
            truth_c = f"{cn} >= '{y}-{m:02d}-01' AND {cn} < '{nxt}'"
        truth, broken = wrap(truth_c), wrap(broken_c)
    elif bug in ("ix_day", "ix_cast_day"):
        c = col("datetime", "date")
        if c is None or not vals(c):
            return None
        d = rnd.choice(vals(c))
        day = d.date() if isinstance(d, dt.datetime) else d
        cn = c["name"]
        one = dt.timedelta(days=1)
        form = ("DATE({c})" if bug == "ix_day" else rnd.choice(["CAST({c} AS DATE)", "{c}::date"])).format(c=cn)
        if "::" in form:
            engine = "postgresql"
        if rnd.random() < 0.3:
            d2 = day + dt.timedelta(days=rnd.randint(1, 60))
            broken_c = f"{form} BETWEEN '{day}' AND '{d2}'"
            truth_c = f"{cn} >= '{day}' AND {cn} < '{d2 + one}'"
        else:
            op = rnd.choice(["=", ">=", "<", ">", "<="])
            broken_c = f"{form} {op} '{day}'"
            truth_c = {"=": f"{cn} >= '{day}' AND {cn} < '{day + one}'", ">=": f"{cn} >= '{day}'", "<": f"{cn} < '{day}'",
                       ">": f"{cn} >= '{day + one}'", "<=": f"{cn} < '{day + one}'"}[op]
        truth, broken = wrap(truth_c), wrap(broken_c)
    elif bug == "ix_arith":
        c = col("int")
        if c is None or not vals(c):
            return None
        v, k, cn = rnd.choice(vals(c)), rnd.randint(1, 20), c["name"]
        op = rnd.choice(["=", ">", "<", ">=", "<=", "<>"])
        sign = rnd.choice(["+", "-", "*", "lead"])
        broken_c = {"+": f"{cn} + {k} {op} {v + k}", "-": f"{cn} - {k} {op} {v - k}", "*": f"{cn} * {k} {op} {v * k}",
                    "lead": f"{k} + {cn} {op} {v + k}"}[sign]
        truth, broken = wrap(f"{cn} {op} {v}"), wrap(broken_c)
    elif bug == "ix_coalesce":
        c = col("int", "money")
        if c is None or not vals(c):
            return None
        v, cn = rnd.choice(vals(c)), c["name"]
        d = rnd.choice([0, v])
        op = rnd.choice(["=", ">", "<", ">=", "<=", "<>"])
        fn = rnd.choice(["COALESCE", "IFNULL"])
        if fn == "IFNULL":
            engine, only = "mariadb", None
        truth = wrap(f"({cn} {op} {v} OR ({cn} IS NULL AND {d} {op} {v}))")
        broken = wrap(f"{fn}({cn}, {d}) {op} {v}")
    elif bug == "ix_left":
        c = col("name", "cat", "text")
        if c is None:
            return None
        vs = [v for v in vals(c) if isinstance(v, str) and re.fullmatch(r"[A-Za-z][A-Za-z ]*", v)]
        if not vs:
            return None
        v = rnd.choice(vs)
        n = rnd.randint(1, min(5, len(v)))
        pre = v[:n]
        if pre.endswith(" "):
            return None
        form = rnd.choice(["LEFT({c}, {n})", "SUBSTRING({c}, 1, {n})", "SUBSTR({c}, 1, {n})"]).format(c=c["name"], n=n)
        truth, broken = wrap(f"{c['name']} LIKE '{pre}%'"), wrap(f"{form} = '{pre}'")
    elif bug == "ix_or_join":
        fks = [c for c in t["cols"] if c["kind"] == "fk"]
        if not fks:
            return None
        fk = rnd.choice(fks)
        parent = schema["tables"][fk["ref"]]
        mine = [c for c in t["cols"] if c["kind"] in ("cat", "int") and vals(c)]
        theirs = [c for c in parent["cols"] if c["kind"] in ("cat", "int", "name")]
        if not mine or not theirs:
            return None
        a, b = rnd.choice(mine), rnd.choice(theirs)
        pv = [r[parent["cols"].index(b)] for r in parent["rows"] if r[parent["cols"].index(b)] is not None]
        if not pv:
            return None
        lit = lambda x: f"'{x}'" if isinstance(x, str) else str(x)  # noqa: E731
        sides = [f"t.{a['name']} = {lit(rnd.choice(vals(a)))}", f"p.{b['name']} = {lit(rnd.choice(pv))}"]
        rnd.shuffle(sides)
        join = rnd.choice(["JOIN", "LEFT JOIN"])
        truth = broken = (f"SELECT t.{pk}, p.{parent['pk']} AS parent_key\nFROM {name} t\n{join} {parent['name']} p ON p.{parent['pk']} = t.{fk['name']}\n"
                          f"WHERE {sides[0]} OR {sides[1]}")
        stmt = "select"
    else:                                                      # ix_keyset
        off = rnd.choice([1000, 2500, 10000, 50000])
        n = rnd.choice([10, 20, 25, 50])
        desc = rnd.random() < 0.4
        ordered = [c for c in t["cols"] if c["kind"] in ("date", "datetime", "int")]
        by = [pk] if rnd.random() < 0.5 or not ordered else [rnd.choice(ordered)["name"], pk]
        order = ", ".join(f"{x} DESC" if desc else x for x in by)
        form = rnd.choice(["LIMIT {n} OFFSET {o}", "LIMIT {o}, {n}"]).format(n=n, o=off)
        if "," in form:
            engine = "mariadb"
        truth = broken = f"SELECT {', '.join(dict.fromkeys([pk] + by))}\nFROM {name}\nORDER BY {order}\n{form}"
        item = {"seed": seed, "bug": bug, "stmt": "select", "schema": schema, "table": name, "broken": broken, "truth": truth,
                "keyset": {"by": by, "order": order, "n": n}}
        if engine:
            item["engine"] = engine
        return item
    item = {"seed": seed, "bug": bug, "stmt": stmt, "schema": schema, "table": name, "broken": broken, "truth": truth}
    if engine:
        item["engine"] = engine
    if only:
        item["only"] = only
    return item


def _check_keyset(item, engines, tag) -> list[str]:
    """Keyset gives the page OFFSET gives: the same query with a small OFFSET (the tables are small), rewritten by the same code,
    with the last row of the page before it put in place of the placeholders (what the app remembers)."""
    from app.agents import sqlspeed

    ks, pk, table = item["keyset"], item["schema"]["tables"][item["table"]]["pk"], item["table"]
    eng_word = {"postgresql": "postgresql", "mariadb": "mysql"}.get(item.get("engine") or "")
    where = " AND ".join(f"{c} IS NOT NULL" for c in ks["by"])
    cols = ", ".join(dict.fromkeys([pk] + ks["by"]))
    bad = []
    for off in (7, 20):
        q = f"SELECT {cols} FROM {table} WHERE {where} ORDER BY {ks['order']} LIMIT {ks['n']} OFFSET {off}"
        got = sqlspeed.keyset_sql(q, eng_word, min_offset=0)
        if not got:
            return [f"{tag}: no keyset rewrite for\n{q}"]
        for name, eng in engines.items():
            page = [tuple(map(str, r)) for r in eng.run(q)]
            last = eng.run(f"SELECT {', '.join(ks['by'])} FROM {table} WHERE {where} ORDER BY {ks['order']} LIMIT 1 OFFSET {off - 1}")
            if not last:
                continue
            sql = got[0]
            for c, v in sorted(zip(ks["by"], last[0]), key=lambda x: -len(x[0])):
                sql = sql.replace(f":last_{c.lower()}", str(v) if isinstance(v, int) else f"'{v}'")
            try:
                if [tuple(map(str, r)) for r in eng.run(sql)] != page:
                    bad.append(f"{tag}: on {name} keyset gives another page\n{sql}")
            except Exception as exc:  # noqa: BLE001
                eng.rollback()
                bad.append(f"{tag}: on {name} keyset fails: {exc}\n{sql}")
    return bad


def _lock_bug(seed, bug, rnd, schema, tables, t):
    """Night 9: a huge change said in words (batches), queue workers (SKIP LOCKED), the same statement per value (N+1)."""
    pk, name = t["pk"], t["name"]
    rows = t["rows"]

    def vals(c):
        k = t["cols"].index(c)
        return [r[k] for r in rows if r[k] is not None]

    lit = lambda x: f"'{x}'" if not isinstance(x, (int, float)) and not hasattr(x, "as_tuple") else str(x)  # noqa: E731
    conds = []
    for c in t["cols"]:
        if c["kind"] in ("cat", "int", "date", "money") and vals(c):
            v = rnd.choice(vals(c))
            op = "=" if c["kind"] == "cat" else rnd.choice(["<", ">=", "="])
            conds.append(f"{c['name']} {op} {lit(v)}")
    if not conds:
        return None
    cond = rnd.choice(conds)
    engine = rnd.choice(["mariadb", "postgresql"])
    only = ["mysql"] if engine == "mariadb" else ["postgres"]
    words = rnd.choice(["the table has 20 million rows and this times out", "huge table, it locks everything", "lakhs of rows, ye atak jata hai",
                        "very big table", "takes hours on production"])
    parent = any(c.get("ref") == name for tt in tables for c in tt["cols"])   # rows other tables point at: a DELETE is refused
    if bug == "lk_batch_delete":
        if parent:
            return None
        truth = f"DELETE FROM {name}\nWHERE {cond}"
        broken = f"{truth};\n\n{words}"
        stmt = "delete"
    elif bug == "lk_batch_update":
        sets = [c for c in t["cols"] if c["kind"] in ("cat", "int") and c["name"] not in cond and vals(c)]
        if not sets:
            return None
        c = rnd.choice(sets)
        v = rnd.choice(vals(c)) if rnd.random() < 0.8 else None
        truth = f"UPDATE {name}\nSET {c['name']} = {'NULL' if v is None else lit(v)}\nWHERE {cond}"
        broken = f"{truth};\n\n{words}"
        stmt = "update"
    elif bug == "lk_skip_locked":
        n = rnd.choice([1, 1, 2, 5])
        truth = f"SELECT {pk}\nFROM {name}\nWHERE {cond}\nORDER BY {pk}\nLIMIT {n}\nFOR UPDATE"
        broken = truth + ";\n\n" + rnd.choice(["several workers pick the next job", "job queue with parallel workers", "do worker ek saath agla task lete hain"])
        stmt, only = "select", None
        engine = rnd.choice([None, "mariadb", "postgresql"])
    else:                                                      # lk_nplus
        k = rnd.randint(3, 6)
        ids = rnd.sample(range(1, len(rows) + 1), min(k, len(rows)))
        cols = [c["name"] for c in t["cols"] if c["kind"] != "pk"][:2] or [pk]
        form = rnd.choice(["select", "select", "update"] + ([] if parent else ["delete"]))
        if form == "select":
            parts = [f"SELECT {', '.join(cols)} FROM {name} WHERE {pk} = {i}" for i in ids]
        elif form == "update":
            sets = [c for c in t["cols"] if c["kind"] in ("cat", "int") and vals(c)]
            if not sets:
                return None
            c = rnd.choice(sets)
            v = lit(rnd.choice(vals(c)))
            parts = [f"UPDATE {name} SET {c['name']} = {v} WHERE {pk} = {i}" for i in ids]
        else:
            parts = [f"DELETE FROM {name} WHERE {pk} = {i}" for i in ids]
        truth = broken = ";\n".join(parts) + ";"
        return {"seed": seed, "bug": bug, "stmt": form, "schema": schema, "table": name, "broken": broken, "truth": truth, "parts": parts}
    item = {"seed": seed, "bug": bug, "stmt": stmt, "schema": schema, "table": name, "broken": broken, "truth": truth}
    if engine:
        item["engine"] = engine
    if only:
        item["only"] = only
    return item


def _check_lock(item, engines, res, tag) -> list[str]:
    """Batches: run the batched form with 3 rows a batch until it changes nothing: the same table as the single statement.
    N+1: the combined statement returns / changes exactly what the separate ones did."""
    import re as _re

    from app.agents import sqllock

    pk, table = item["schema"]["tables"][item["table"]]["pk"], item["table"]
    bad = []
    fixed = res.get("corrected")
    if not fixed:
        return [f"{tag}: nothing offered\n{item['broken']}"]
    for name, eng in engines.items():
        if item.get("only") and name not in item["only"]:
            continue
        eng.begin()
        try:
            if item["bug"].startswith("lk_batch"):
                eng.run(item["truth"])
                want = Counter(tuple(map(str, r)) for r in eng.run(f"SELECT * FROM {table} ORDER BY {pk}"))
                eng.rollback()
                eng.begin()
                sql = _re.sub(rf"LIMIT {sqllock.BATCH}\b", "LIMIT 3", fixed.rstrip().rstrip(";"))
                for _ in range(200):
                    cur = eng.conn.cursor()
                    cur.execute(sql)
                    n = cur.rowcount
                    cur.close()
                    if not n:
                        break
                else:
                    bad.append(f"{tag}: on {name} the batches never end\n{sql}")
                got = Counter(tuple(map(str, r)) for r in eng.run(f"SELECT * FROM {table} ORDER BY {pk}"))
            elif item["stmt"] == "select":
                want = set()
                for p in item["parts"]:
                    want |= {tuple(map(str, r)) for r in eng.run(p)}
                rows = [tuple(map(str, r)) for r in eng.run(fixed)]
                width = len(next(iter(want))) if want else 0
                got = {r[-width:] for r in rows} if width else set(rows)
            else:
                for p in item["parts"]:
                    eng.run(p)
                want = Counter(tuple(map(str, r)) for r in eng.run(f"SELECT * FROM {table} ORDER BY {pk}"))
                eng.rollback()
                eng.begin()
                eng.run(fixed)
                got = Counter(tuple(map(str, r)) for r in eng.run(f"SELECT * FROM {table} ORDER BY {pk}"))
            if got != want:
                bad.append(f"{tag}: on {name} the offer does something else\n{fixed}")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{tag}: on {name} the offer fails: {exc}\n{fixed}")
        finally:
            eng.rollback()
    return bad


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
    from app.agents import sqlcorrect, sqlproject, sqlverify

    from app.agents.sql import request_words, unwrap

    if item.get("project"):                       # night 10B: checked against the project's CREATE TABLE script
        res = sqlverify.check(unwrap(item["broken"]), sqlproject.from_text(item["project"]), intent=request_words(item["broken"]),
                              engine=item.get("engine"))
    else:
        res = sqlcorrect.check(unwrap(item["broken"]), intent=request_words(item["broken"]), engine=item.get("engine"))
    tag = f"{item['bug']} ({item['stmt']}) seed {item['seed']}"
    topics = {f["topic"] for f in res["findings"]}
    bad = _classes(item, res, tag)
    want = TOPIC[item["bug"]]
    if item["bug"] in QUIET:
        said = [f["title_en"] for f in res["findings"] if f["kind"] in ("error", "wrong") or f["topic"] == TOPIC[item["bug"]]]
        return bad + ([f"{tag}: said about a correct query: {said}\n{item['broken']}"] if said else [])
    if item["bug"] in RULED_OUT:
        if want in topics or want not in {d["topic"] for d in res.get("dropped", [])}:
            bad.append(f"{tag}: not ruled out by the schema (found {sorted(topics)}, dropped {res.get('dropped')})\n{item['broken']}")
        extra = [f["title_en"] for f in res["findings"] if f["kind"] in ("error", "wrong")]
        return bad + ([f"{tag}: extra findings {extra}\n{item['broken']}"] if extra else [])
    if want not in topics:
        bad.append(f"{tag}: not found (found {sorted(topics)})\n{item['broken']}")
    elif item.get("project") and not any(f["topic"] == want and f.get("schema") == "verified" for f in res["findings"]):
        bad.append(f"{tag}: found but not verified with the project's schema\n{item['broken']}")
    extra = [f["title_en"] for f in res["findings"] if f["kind"] in ("error", "wrong") and f["topic"] != want]
    if extra:
        bad.append(f"{tag}: extra findings {extra}\n{item['broken']}")
    if item["stmt"] == "ddl":
        return bad + _check_ddl(item, engines, res, tag)
    sure = any(f["topic"] == want and f["kind"] in ("error", "wrong") for f in res["findings"])
    if item["bug"] in FIX_THE_CHECK:
        sure = True
    if item["bug"] in ("lk_batch_delete", "lk_batch_update", "lk_nplus"):
        return bad + ([] if bad else _check_lock(item, engines, res, tag))
    if item["bug"] == "ix_keyset" and not bad:
        return bad + _check_keyset(item, engines, tag)
    if item["bug"] in NO_FIX or item.get("no_fix") or not sure:
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
        run_fixed = _bind(fixed, item["bind"]) if item.get("bind") else fixed
        try:
            if _effect(eng, run_fixed, item["table"], pk) != _effect(eng, item["truth"], item["table"], pk):
                bad.append(f"{tag}: on {name} the corrected statement does something else\n{fixed}")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{tag}: on {name} the corrected statement fails: {exc}\n{fixed}")
    return bad


def _port_bug(seed, bug, rnd, schema, tables, t):
    """Night 10: another database's words in the chosen database (truth = the same statement in its own words, run only on
    that engine), and spellings that run there but mean something else."""
    pk, name = t["pk"], t["name"]
    col = lambda *kinds: [c["name"] for c in t["cols"] if c["kind"] in kinds]   # noqa: E731
    nums, dates, texts = col("int", "money"), col("date", "datetime"), col("cat", "name", "email", "sku")
    days = col("date")
    my = rnd.choice(["mariadb", "mysql"])
    target = rnd.choice([my, "postgresql"])
    words = ""
    unit_my = {"day": "DAY", "month": "MONTH", "year": "YEAR", "week": "WEEK"}

    def pg_iv(n, u):
        return f"INTERVAL '{n * 7}' DAY" if u == "week" else f"INTERVAL '{n}' {unit_my[u]}"

    def iv(n, u, eng):
        return f"INTERVAL {n} {unit_my[u]}" if eng != "postgresql" else pg_iv(n, u)

    if bug == "pt_top":
        if not nums:
            return None
        n, m = rnd.randint(1, 6), rnd.choice(nums)
        d = rnd.choice(["", " DESC"])
        truth = f"SELECT {pk}, {m}\nFROM {name}\nORDER BY {m}{d}, {pk}\nLIMIT {n}"
        broken = f"SELECT TOP {n} {pk}, {m}\nFROM {name}\nORDER BY {m}{d}, {pk}"
        if rnd.random() < 0.3:
            broken = f"SELECT TOP ({n}) {pk}, {m}\nFROM {name}\nORDER BY {m}{d}, {pk};"
    elif bug == "pt_rownum":
        if not nums:
            return None
        n, m = rnd.randint(1, 6), rnd.choice(nums)
        op, k = rnd.choice([("<=", n), ("<", n + 1)])
        extra = rnd.random() < 0.5
        cond = f"{m} > 0" if extra else ""
        truth = f"SELECT {pk}, {m}\nFROM {name}\n" + (f"WHERE {cond}\n" if extra else "") + f"ORDER BY {m} DESC, {pk}\nLIMIT {n}"
        broken = (f"SELECT {pk}, {m}\nFROM {name}\nWHERE " + (f"{cond} AND " if extra else "") + f"ROWNUM {op} {k}\nORDER BY {m} DESC, {pk}")
    elif bug == "pt_dateadd":
        if not dates:
            return None
        c, u, n = rnd.choice(dates), rnd.choice(["day", "month", "year", "week"]), rnd.randint(1, 40)
        ss_u = rnd.choice({"day": ["day", "dd", "d"], "month": ["month", "mm"], "year": ["year", "yyyy"], "week": ["week", "wk"]}[u])
        if target == "postgresql":
            new = f"({c} + {pg_iv(n, u)})"
        else:
            new = f"DATE_ADD({c}, {iv(n, u, target)})"
        if rnd.random() < 0.5:
            truth = f"SELECT {pk}, {new} AS due\nFROM {name}\nORDER BY {pk}"
            broken = f"SELECT {pk}, DATEADD({ss_u}, {n}, {c}) AS due\nFROM {name}\nORDER BY {pk}"
        else:
            truth = f"SELECT {pk}\nFROM {name}\nWHERE {new} > '2026-03-01'\nORDER BY {pk}"
            broken = f"SELECT {pk}\nFROM {name}\nWHERE DATEADD({ss_u}, {n}, {c}) > '2026-03-01'\nORDER BY {pk}"
    elif bug == "pt_datediff_ss":
        if not dates:
            return None
        c = rnd.choice(dates)
        ref = "'2026-12-31'"
        new = (f"DATEDIFF({ref}, {c})" if target != "postgresql" else f"(CAST({ref} AS DATE) - CAST({c} AS DATE))")
        truth = f"SELECT {pk}, {new} AS age_days\nFROM {name}\nORDER BY {pk}"
        broken = f"SELECT {pk}, DATEDIFF(day, {c}, {ref}) AS age_days\nFROM {name}\nORDER BY {pk}"
    elif bug == "pt_ss_funcs":
        kind = rnd.choice(["len", "charindex", "iif", "datepart", "getdate", "convert"])
        if kind in ("len", "charindex") and not texts or kind in ("iif", "convert") and not nums or kind in ("datepart", "getdate") and not dates:
            return None
        if kind == "len":
            c = rnd.choice(texts)
            new = f"CHAR_LENGTH({c})" if target != "postgresql" else f"LENGTH({c})"
            broken_e, truth_e = f"LEN({c})", new
        elif kind == "charindex":
            c = rnd.choice(texts)
            ch = rnd.choice(["a", "e", "i", "o", "-"])
            truth_e = f"LOCATE('{ch}', {c})" if target != "postgresql" else f"POSITION('{ch}' IN {c})"
            broken_e = f"CHARINDEX('{ch}', {c})"
        elif kind == "iif":
            m = rnd.choice(nums)
            lim = rnd.randint(1, 500)
            truth_e = f"IF({m} > {lim}, 'big', 'small')" if target != "postgresql" else f"CASE WHEN {m} > {lim} THEN 'big' ELSE 'small' END"
            broken_e = f"IIF({m} > {lim}, 'big', 'small')"
        elif kind == "datepart":
            c = rnd.choice(dates)
            u = rnd.choice(["year", "month", "day"])
            truth_e = f"EXTRACT({u.upper()} FROM {c})"
            broken_e = f"DATEPART({rnd.choice({'year': ['year', 'yyyy'], 'month': ['month', 'mm'], 'day': ['day', 'dd']}[u])}, {c})"
        elif kind == "getdate":
            c = rnd.choice(dates)
            truth = f"SELECT {pk}\nFROM {name}\nWHERE {c} < {'NOW()' if target != 'postgresql' else 'now()'}\nORDER BY {pk}"
            broken = f"SELECT {pk}\nFROM {name}\nWHERE {c} < GETDATE()\nORDER BY {pk}"
        else:
            m = rnd.choice(nums)
            truth_e = f"CAST({m} AS SIGNED)" if target != "postgresql" else f"CAST({m} AS INTEGER)"
            broken_e = f"CONVERT(INT, {m})"
        if kind != "getdate":
            truth = f"SELECT {pk}, {truth_e} AS v\nFROM {name}\nORDER BY {pk}"
            broken = f"SELECT {pk}, {broken_e} AS v\nFROM {name}\nORDER BY {pk}"
    elif bug == "pt_brackets":
        cs = [c["name"] for c in t["cols"] if c["kind"] != "pk"][:2]
        if not cs:
            return None
        q = (lambda x: f"`{x}`") if target != "postgresql" else (lambda x: x)
        truth = f"SELECT {q(pk)}, {', '.join(q(x) for x in cs)}\nFROM {q(name)}\nORDER BY {q(pk)}"
        broken = f"SELECT [{pk}], {', '.join('[' + x + ']' for x in cs)}\nFROM [{name}]\nORDER BY [{pk}]"
    elif bug == "pt_my_dates_pg":
        target = "postgresql"
        if not dates:
            return None
        c = rnd.choice(dates)
        kind = rnd.choice(["date_add", "date_sub", "interval", "datediff", "year", "month", "day"])
        n, u = rnd.randint(1, 60), rnd.choice(["day", "month", "year"])
        if kind == "date_add":
            truth_e, broken_e = f"({c} + {pg_iv(n, u)})", f"DATE_ADD({c}, INTERVAL {n} {unit_my[u]})"
        elif kind == "date_sub":
            truth_e, broken_e = f"({c} - {pg_iv(n, u)})", f"DATE_SUB({c}, INTERVAL {n} {unit_my[u]})"
        elif kind == "interval":
            truth_e, broken_e = f"{c} - {pg_iv(n, u)}", f"{c} - INTERVAL {n} {unit_my[u]}"
        elif kind == "datediff":
            truth_e, broken_e = f"(CAST('2026-12-31' AS DATE) - CAST({c} AS DATE))", f"DATEDIFF('2026-12-31', {c})"
        else:
            truth_e, broken_e = f"EXTRACT({kind.upper()} FROM {c})", f"{kind.upper()}({c})"
        if kind in ("year", "month") and rnd.random() < 0.5:
            v = 2025 if kind == "year" else rnd.randint(1, 12)
            truth = f"SELECT {pk}\nFROM {name}\nWHERE {truth_e} = {v}\nORDER BY {pk}"
            broken = f"SELECT {pk}\nFROM {name}\nWHERE {broken_e} = {v}\nORDER BY {pk}"
        else:
            truth = f"SELECT {pk}, {truth_e} AS v\nFROM {name}\nORDER BY {pk}"
            broken = f"SELECT {pk}, {broken_e} AS v\nFROM {name}\nORDER BY {pk}"
    elif bug == "pt_my_funcs_pg":
        target = "postgresql"
        kind = rnd.choice(["if", "locate", "instr", "ucase", "substring_index", "group_concat", "date_format", "convert", "div",
                           "regexp", "dual"])
        need = {"if": nums, "locate": texts, "instr": texts, "ucase": texts, "substring_index": texts, "group_concat": texts,
                "date_format": dates, "convert": nums, "div": col("int"), "regexp": texts, "dual": [1]}[kind]
        if not need:
            return None
        x = rnd.choice(need)
        if kind == "if":
            truth_e, broken_e = f"CASE WHEN {x} > 100 THEN 1 ELSE 0 END", f"IF({x} > 100, 1, 0)"
        elif kind == "locate":
            truth_e, broken_e = f"POSITION('a' IN {x})", f"LOCATE('a', {x})"
        elif kind == "instr":
            truth_e, broken_e = f"POSITION('e' IN {x})", f"INSTR({x}, 'e')"
        elif kind == "ucase":
            truth_e, broken_e = f"UPPER({x})", f"UCASE({x})"
        elif kind == "substring_index":
            sep = rnd.choice(["@", " ", "-"])
            truth_e, broken_e = f"SPLIT_PART({x}, '{sep}', 1)", f"SUBSTRING_INDEX({x}, '{sep}', 1)"
        elif kind == "date_format":
            f_my, f_pg = rnd.choice([("%Y-%m", "YYYY-MM"), ("%d/%m/%Y", "DD/MM/YYYY"), ("%b %Y", "Mon YYYY"), ("%Y", "YYYY")])
            truth_e, broken_e = f"to_char({x}, '{f_pg}')", f"DATE_FORMAT({x}, '{f_my}')"
        elif kind == "convert":
            truth_e, broken_e = f"CAST({x} AS BIGINT)", f"CONVERT({x}, SIGNED)"
        elif kind == "div":
            truth_e, broken_e = f"DIV({x}, 3)", f"{x} DIV 3"
        if kind == "group_concat":
            g = rnd.choice([c["name"] for c in t["cols"] if c["kind"] in ("cat", "fk")] or [pk])
            sep = rnd.choice(["", " SEPARATOR '; '"])
            truth = (f"SELECT {g}, string_agg(CAST({x} AS TEXT), {repr(sep[12:-1]) if sep else repr(',')} ORDER BY {x})\nFROM {name}\n"
                     f"GROUP BY {g}\nORDER BY {g}").replace('"', "'")
            broken = f"SELECT {g}, GROUP_CONCAT({x} ORDER BY {x}{sep})\nFROM {name}\nGROUP BY {g}\nORDER BY {g}"
            if g == x:
                return None
        elif kind == "regexp":
            truth = f"SELECT {pk}\nFROM {name}\nWHERE {x} ~* '^[a-m]'\nORDER BY {pk}"
            broken = f"SELECT {pk}\nFROM {name}\nWHERE {x} REGEXP '^[a-m]'\nORDER BY {pk}"
        elif kind == "dual":
            n = rnd.randint(1, 99)
            truth, broken = f"SELECT {n} AS one", f"SELECT {n} AS one FROM DUAL"
        else:
            truth = f"SELECT {pk}, {truth_e} AS v\nFROM {name}\nORDER BY {pk}"
            broken = f"SELECT {pk}, {broken_e} AS v\nFROM {name}\nORDER BY {pk}"
    elif bug == "pt_pg_funcs_my":
        target = my
        kind = rnd.choice(["strpos", "split_part", "date_trunc", "string_agg", "interval", "cast_text", "regex", "to_date"])
        need = {"strpos": texts, "split_part": texts, "date_trunc": dates, "string_agg": texts, "interval": dates, "cast_text": nums,
                "regex": texts, "to_date": [1]}[kind]
        if not need:
            return None
        x = rnd.choice(need)
        if kind == "strpos":
            truth_e, broken_e = f"LOCATE('a', {x})", f"STRPOS({x}, 'a')"
        elif kind == "split_part":
            truth_e, broken_e = f"SUBSTRING_INDEX({x}, '@', 1)", f"SPLIT_PART({x}, '@', 1)"
        elif kind == "date_trunc":
            u, f = rnd.choice([("month", "%Y-%m-01"), ("year", "%Y-01-01")])
            truth_e, broken_e = f"CAST(DATE_FORMAT({x}, '{f}') AS DATE)", f"DATE_TRUNC('{u}', {x})"
        elif kind == "interval":
            n, u = rnd.randint(1, 60), rnd.choice(["day", "month", "year"])
            truth_e, broken_e = f"{x} + INTERVAL {n} {unit_my[u]}", f"{x} + INTERVAL '{n} {u}s'"
        elif kind == "cast_text":
            truth_e, broken_e = f"CAST({x} AS CHAR)", f"CAST({x} AS TEXT)"
        elif kind == "to_date":
            d = f"{rnd.randint(1, 28):02d}-{rnd.randint(1, 12):02d}-2025"
            truth_e, broken_e = f"STR_TO_DATE('{d}', '%d-%m-%Y')", f"TO_DATE('{d}', 'DD-MM-YYYY')"
        if kind == "string_agg":
            g = rnd.choice([c["name"] for c in t["cols"] if c["kind"] in ("cat", "fk")] or [pk])
            if g == x:
                return None
            truth = f"SELECT {g}, GROUP_CONCAT({x} ORDER BY {x} SEPARATOR '; ')\nFROM {name}\nGROUP BY {g}\nORDER BY {g}"
            broken = f"SELECT {g}, string_agg({x}, '; ' ORDER BY {x})\nFROM {name}\nGROUP BY {g}\nORDER BY {g}"
        elif kind == "regex":
            truth = f"SELECT {pk}\nFROM {name}\nWHERE {x} REGEXP '^[a-m]'\nORDER BY {pk}"
            broken = f"SELECT {pk}\nFROM {name}\nWHERE {x} ~* '^[a-m]'\nORDER BY {pk}"
        else:
            truth = f"SELECT {pk}, {truth_e} AS v\nFROM {name}\nORDER BY {pk}"
            broken = f"SELECT {pk}, {broken_e} AS v\nFROM {name}\nORDER BY {pk}"
    elif bug == "pt_date_minus":
        target = my
        dd = [c for c in days if re.search(r"(?:^|_)(?:date|dob|birthday)$|_on$|date$|[a-z](?:On|Date)$", c, re.I)]
        if not dd:
            return None
        c = rnd.choice(dd)
        if rnd.random() < 0.5:
            truth = f"SELECT {pk}, DATEDIFF('2026-12-31', {c}) AS days_left\nFROM {name}\nORDER BY {pk}"
            broken = f"SELECT {pk}, CAST('2026-12-31' AS DATE) - {c} AS days_left\nFROM {name}\nORDER BY {pk}"
            truth = truth.replace("DATEDIFF('2026-12-31'", "DATEDIFF(CAST('2026-12-31' AS DATE)")
        else:
            n = rnd.randint(1, 90)
            op = rnd.choice(["-", "+"])
            truth = f"SELECT {pk}, {c} {op} INTERVAL {n} DAY AS d\nFROM {name}\nORDER BY {pk}"
            broken = f"SELECT {pk}, {c} {op} {n} AS d\nFROM {name}\nORDER BY {pk}"
    elif bug == "pt_interval_week":
        target = "postgresql"
        if not dates:
            return None
        c, n, u = rnd.choice(dates), rnd.randint(1, 9), rnd.choice(["WEEK", "QUARTER"])
        good = f"INTERVAL '{n * 7}' DAY" if u == "WEEK" else f"INTERVAL '{n * 3}' MONTH"
        truth = f"SELECT {pk}, {c} + {good} AS d\nFROM {name}\nORDER BY {pk}"
        broken = f"SELECT {pk}, {c} + INTERVAL '{n}' {u} AS d\nFROM {name}\nORDER BY {pk}"
    elif bug == "pt_cast_char":
        target = "postgresql"
        if not texts + dates + nums:
            return None
        x = rnd.choice(texts + dates + nums)
        truth = f"SELECT {pk}, CAST({x} AS VARCHAR) AS v\nFROM {name}\nORDER BY {pk}"
        broken = f"SELECT {pk}, CAST({x} AS CHAR) AS v\nFROM {name}\nORDER BY {pk}"
    else:                                                 # pt_power
        target = my
        ints = col("int")
        if not ints:
            return None
        x, n = rnd.choice(ints), rnd.randint(2, 3)
        truth = f"SELECT {pk}, POWER({x}, {n}) AS p\nFROM {name}\nORDER BY {pk}"
        broken = f"SELECT {pk}, {x} ^ {n} AS p\nFROM {name}\nORDER BY {pk}"
        words = f";\n\n{x} to the power {n}"
    return {"seed": seed, "bug": bug, "stmt": "select", "schema": schema, "table": name, "broken": broken + words, "truth": truth,
            "engine": target, "only": ["postgres"] if target == "postgresql" else ["mysql"]}


def _param_bug(seed, bug, rnd, schema, tables, t):
    """Night 10A: a query copied out of code with its placeholders. The value the code binds is known, so the corrected query
    is run with it put in, and must return what the intended query returns."""
    pk, name = t["pk"], t["name"]
    texts = [c for c in t["cols"] if c["kind"] in ("cat", "name", "email", "sku")]
    if bug == "cp_order_param":
        cols = [c["name"] for c in t["cols"] if c["kind"] != "pk"][:2]
        broken = f"SELECT {pk}, {', '.join(cols)}\nFROM {name}\nORDER BY {rnd.choice(['?', '$1', ':sort'])}"
        return {"seed": seed, "bug": bug, "stmt": "select", "schema": schema, "table": name, "broken": broken, "truth": broken}
    if bug == "cp_from_param":
        broken = f"SELECT *\nFROM {rnd.choice(['?', '$1'])}\nWHERE {pk} = 1"
        return {"seed": seed, "bug": bug, "stmt": "select", "schema": schema, "table": name, "broken": broken, "truth": broken}
    if not texts:
        return None
    c = rnd.choice(texts)
    vals = [r[t["cols"].index(c)] for r in t["rows"] if r[t["cols"].index(c)]]
    if not vals:
        return None
    v = str(rnd.choice(vals))
    piece = v[1:4] if len(v) > 4 else v
    if "'" in piece or "%" in piece or "_" in piece or not piece.strip():
        return None
    target = rnd.choice(["mysql", "postgresql", None]) if bug == "cp_like_quoted" else rnd.choice(["postgresql", None])
    ph = "?" if bug == "cp_like_quoted" else "$1"
    # '%?' alone is how a query looks with its values hidden ('%?'), so only the shapes that can't be that are planted for ?
    pat = rnd.choice(["%{}%", "{}%"] if ph == "?" else ["%{}%", "{}%", "%{}"])
    broken = f"SELECT {pk}, {c['name']}\nFROM {name}\nWHERE {c['name']} LIKE '{pat.format(ph)}'\nORDER BY {pk}"
    truth = f"SELECT {pk}, {c['name']}\nFROM {name}\nWHERE {c['name']} LIKE '{pat.format(piece)}'\nORDER BY {pk}"
    out = {"seed": seed, "bug": bug, "stmt": "select", "schema": schema, "table": name, "broken": broken, "truth": truth,
           "engine": target, "bind": [piece]}
    if target:
        out["only"] = ["postgres"] if target == "postgresql" else ["mysql"]
    elif bug == "cp_dollar_quoted" or ph == "?":
        out["only"] = ["postgres"] if ph == "$1" else ["mysql"]   # unknown database: the fix is written for the placeholder's own family
    return out


def _project_ddl(schema: dict, kind: str) -> str:
    from tests.fuzz.db import _ddl

    return ";\n".join(x for n in schema["order"] for x in _ddl(schema["tables"][n], kind)) + ";"


def _typo(name: str, others: set, rnd):
    """A misspelling of name that is close to it and to no other column (a swapped pair or a dropped letter)."""
    import difflib

    for _ in range(12):
        i = rnd.randrange(1, len(name) - 1) if len(name) > 3 else 1
        cand = name[:i] + name[i + 1] + name[i] + name[i + 2:] if rnd.random() < 0.5 and i + 1 < len(name) else name[:i] + name[i + 1:]
        if cand.lower() == name.lower() or cand.lower() in {o.lower() for o in others} or not re.fullmatch(r"[A-Za-z_]\w*", cand):
            continue
        close = difflib.get_close_matches(cand.lower(), [o.lower() for o in others | {name}], n=2, cutoff=0.6)
        if close[:1] == [name.lower()] and (len(close) == 1 or difflib.SequenceMatcher(None, cand.lower(), close[1]).ratio() <
                                            difflib.SequenceMatcher(None, cand.lower(), close[0]).ratio()):
            return cand
    return None


def _project_bug(seed, bug, rnd, schema, tables, t, wrap, stmt):
    """Night 10B: the statement is checked with the project's own schema (the CREATE TABLE script of these very tables, as
    the test databases have them). A fact the schema proves turns a 'check' into a verified one, a fact it rules out drops
    the finding, and a name the schema doesn't have is an error with the close real name as the fix."""
    pk, name = t["pk"], t["name"]
    kind = rnd.choice(["mysql", "postgres"])
    engine = rnd.choice(["mariadb", "postgresql"]) if bug != "ps_text_number" else rnd.choice(["mariadb", "postgresql", "mysql"])

    def item(broken, truth, **kw):
        out = {"seed": seed, "bug": bug, "stmt": kw.pop("stmt", "select"), "schema": schema, "table": kw.pop("table", name),
               "broken": broken, "truth": truth, "engine": engine, "project": _project_ddl(schema, kind)}
        out.update(kw)
        return out

    if bug == "ps_unknown_col":
        cols = [c for c in t["cols"] if c["kind"] not in ("pk",) and len(c["name"]) >= 5]
        if not cols:
            return None
        c = rnd.choice(cols)
        typo = _typo(c["name"], {x["name"] for x in t["cols"] if x is not c}, rnd)
        if not typo:
            return None
        return item(wrap(f"{typo} IS NOT NULL"), wrap(f"{c['name']} IS NOT NULL"), stmt=stmt)
    if bug == "ps_text_number":
        ph = [c for c in t["cols"] if c["kind"] == "phone"]
        if not ph:
            return None
        c = ph[0]
        v = _pick_value(t, c, rnd)
        if v is None or str(v).startswith("0"):
            return None
        out = item(wrap(f"{c['name']} = {v}"), wrap(f"{c['name']} = '{v}'"), stmt=stmt)
        if engine == "postgresql":
            out["only"] = ["postgres"]
        return out
    if bug == "ps_join_types":
        ph = [c for c in t["cols"] if c["kind"] in ("phone", "sku", "email", "name")]
        if not ph:
            return None
        c = rnd.choice(ph)
        other = rnd.choice([x for x in tables if x is not t] or [t])
        broken = f"SELECT a.{pk}\nFROM {name} a\nJOIN {other['name']} b ON a.{c['name']} = b.{other['pk']}\nORDER BY a.{pk}"
        return item(broken, broken)
    if bug in ("ps_not_in_nullable", "ps_not_in_notnull"):
        kids = [(x, c) for x in tables for c in x["cols"] if c["kind"] == "fk" and c.get("ref") == name and c["name"].lower() != "id"]
        if not kids:
            return None
        child, fk = rnd.choice(kids)
        if bug == "ps_not_in_notnull":
            fk["not_null"] = True
            k = child["cols"].index(fk)
            for r in child["rows"]:
                if r[k] is None:
                    r[k] = 1
        broken = f"SELECT {pk}\nFROM {name}\nWHERE {pk} NOT IN (SELECT {fk['name']} FROM {child['name']})\nORDER BY {pk}"
        truth = (f"SELECT {pk}\nFROM {name}\nWHERE NOT EXISTS (SELECT 1 FROM {child['name']} x WHERE x.{fk['name']} = {name}.{pk})\n"
                 f"ORDER BY {pk}")
        return item(broken, truth)
    if bug in ("ps_between_time", "ps_between_date"):
        want = "datetime" if bug == "ps_between_time" else "date"
        from app.agents.sqlcorrect import TIME_COL

        cols = [c for c in t["cols"] if c["kind"] == want and (want == "datetime" or TIME_COL.search(c["name"]))]
        if not cols:
            return None
        c = rnd.choice(cols)
        v = _pick_value(t, c, rnd)
        if v is None:
            return None
        import datetime as _d

        day = v.date() if isinstance(v, _d.datetime) else v
        start = day - _d.timedelta(days=rnd.randint(5, 60))
        nxt = day + _d.timedelta(days=1)
        broken = wrap(f"{c['name']} BETWEEN '{start}' AND '{day}'")
        truth = wrap(f"{c['name']} >= '{start}' AND {c['name']} < '{nxt}'") if want == "datetime" else broken
        return item(broken, truth, stmt=stmt)
    if bug == "ps_distinct_parent":
        fks = [c for c in t["cols"] if c["kind"] == "fk" and c.get("ref")]
        if not fks:
            return None
        fk = rnd.choice(fks)
        parent = schema["tables"][fk["ref"]]
        shown = [c["name"] for c in t["cols"] if c["kind"] in ("cat", "int", "money")][:2] or [pk]
        broken = (f"SELECT DISTINCT c.{pk}, {', '.join('c.' + x for x in shown if x != pk)}".rstrip(", ") +
                  f"\nFROM {name} c\nJOIN {parent['name']} p ON p.{parent['pk']} = c.{fk['name']}\nORDER BY c.{pk}")
        broken = broken.replace(f"c.{pk}, \n", f"c.{pk}\n")
        return item(broken, broken)
    if bug == "ps_index_missing":
        indexed = {c for ix in t["indexes"] for c in ix} | {pk}
        if kind == "mysql":
            indexed |= {c for c, _p, _k in t["fks"]}
        cols = [c for c in t["cols"] if c["kind"] in ("cat", "int", "email", "sku") and c["name"] not in indexed]
        if not cols:
            return None
        c = rnd.choice(cols)
        v = _pick_value(t, c, rnd)
        if v is None:
            return None
        lit = str(v) if c["kind"] == "int" else "'" + str(v).replace("'", "''") + "'"
        broken = f"SELECT {pk}\nFROM {name}\nWHERE {c['name']} = {lit}\nORDER BY {pk}"
        out = item(broken, broken)
        out["engine"] = "mariadb" if kind == "mysql" else "postgresql"
        return out
    return None


def _classes(item, res, tag) -> list[str]:
    """Night 11: every finding says how sure it is, the planted one with its expected class, and the schema read from the query
    names only real tables and columns (a CTE / derived table is not a table; a bare column in a nested block may belong to
    an outer table, so that one only needs to be a column of one of the query's tables)."""
    from app.agents import sqlconfidence as K
    from app.agents.sql import unwrap

    sql = unwrap(item["broken"])
    K.annotate_findings(res["findings"], sql)
    bad = []
    for f in res["findings"]:
        c = (f.get("confidence") or {}).get("class")
        if c not in ("proven", "likely_risk", "needs_schema", "needs_execution") or (f["kind"] == "error" and c != "proven"):
            bad.append(f"{tag}: finding {f['title_en']!r} ({f['kind']}) has class {c}\n{item['broken']}")
    want = CLASS.get(item["bug"])
    planted = [f for f in res["findings"] if f["topic"] == TOPIC[item["bug"]]]
    if want and planted and not any(f["confidence"]["class"] == want for f in planted):
        bad.append(f"{tag}: planted {TOPIC[item['bug']]} has class {[f['confidence']['class'] for f in planted]}, want {want}\n{item['broken']}")
    if item["stmt"] == "ddl" or re.match(r"\s*(create|delimiter|do)\b", sql, re.I):
        return bad
    real = {n.lower(): {c["name"].lower() for c in tt["cols"]} for n, tt in item["schema"]["tables"].items()}
    inf = K.infer_schema(sql)
    names = [x["name"] for x in inf["tables"]]
    for x in inf["tables"]:
        if x["name"] not in real:
            bad.append(f"{tag}: the inferred schema invents table {x['name']}\n{item['broken']}")
            continue
        for c, info in x["columns"].items():
            names_in_truth = re.sub(r"'(?:[^']|'')*'", "''", item["truth"] or "")
            planted = not re.search(rf"(?<![\w$]){re.escape(c)}(?![\w$])", names_in_truth, re.I)    # the misspelt name / bare value itself
            if info["verified"] or not (c in real[x["name"]] or planted or info.get("owner_guess") and any(c in real.get(n, ()) for n in names)):
                bad.append(f"{tag}: the inferred schema invents {x['name']}.{c}\n{item['broken']}")
    return bad


def _query_only_bug(seed, bug, rnd, schema, tables, t, wrap, stmt):
    """Night 11: mistakes judged from the query alone, each with the class it must carry."""
    pk, name = t["pk"], t["name"]
    engine = rnd.choice(["mariadb", "postgresql", None])

    def item(broken, truth, **kw):
        out = {"seed": seed, "bug": bug, "stmt": kw.pop("stmt", "select"), "schema": schema, "table": kw.pop("table", name),
               "broken": broken, "truth": truth, "engine": engine}
        out.update(kw)
        return out

    if bug in ("qo_fanout", "qo_fanout_left"):
        pairs = [(schema["tables"][c["ref"]], x, c) for x in tables for c in x["cols"] if c["kind"] == "fk" and c.get("ref") != x["name"]]
        pairs = [(p, x, c) for p, x, c in pairs if _cols(p, "int", "money")]
        if not pairs:
            return None
        p, x, fk = rnd.choice(pairs)
        m = rnd.choice(_cols(p, "int", "money"))["name"]
        g = rnd.choice([c for c in _cols(p, "cat", "int") + [c for c in p["cols"] if c["kind"] == "pk"] if c["name"] != m])["name"]
        agg = rnd.choice(["SUM", "SUM", "COUNT"])          # (AVG over a nullable column is its own check: night 3)
        kind = "LEFT JOIN" if bug == "qo_fanout_left" else "JOIN"
        filt = rnd.random() < 0.4 and _cols(p, "int", "money")
        where = f"\nWHERE p.{m} > 0" if filt else ""
        broken = (f"SELECT p.{g}, {agg}(p.{m}) AS total\nFROM {p['name']} p\n{kind} {x['name']} c ON c.{fk['name']} = p.{p['pk']}"
                  f"{where}\nGROUP BY p.{g}\nORDER BY p.{g}")
        if bug == "qo_fanout":
            cond = f"EXISTS (SELECT 1 FROM {x['name']} c WHERE c.{fk['name']} = p.{p['pk']})" + (f" AND p.{m} > 0" if filt else "")
            truth = f"SELECT p.{g}, {agg}(p.{m}) AS total\nFROM {p['name']} p\nWHERE {cond}\nGROUP BY p.{g}\nORDER BY p.{g}"
        else:
            truth = f"SELECT p.{g}, {agg}(p.{m}) AS total\nFROM {p['name']} p{where}\nGROUP BY p.{g}\nORDER BY p.{g}"
        return item(broken, truth, table=p["name"])
    if bug == "qo_not_in":
        kids = [(x, c) for x in tables for c in x["cols"] if c["kind"] == "fk" and c.get("ref") == name]
        if not kids:
            return None
        child, fk = rnd.choice(kids)
        broken = f"SELECT {pk}\nFROM {name}\nWHERE {pk} NOT IN (SELECT {fk['name']} FROM {child['name']})\nORDER BY {pk}"
        truth = (f"SELECT {pk}\nFROM {name}\nWHERE NOT EXISTS (SELECT 1 FROM {child['name']} x WHERE x.{fk['name']} = {name}.{pk})\n"
                 f"ORDER BY {pk}")
        return item(broken, truth)
    if bug == "qo_eq_null":
        cols = [c for c in t["cols"] if c["kind"] not in ("pk",)]
        if not cols:
            return None
        c = rnd.choice(cols)["name"]
        op, right = rnd.choice([("=", "IS NULL"), ("!=", "IS NOT NULL"), ("<>", "IS NOT NULL")])
        return item(f"SELECT {pk}\nFROM {name}\nWHERE {c} {op} NULL\nORDER BY {pk}", f"SELECT {pk}\nFROM {name}\nWHERE {c} {right}\nORDER BY {pk}")
    if bug == "qo_ties":
        cols = _cols(t, "cat", "int", "date")
        if not cols:
            return None
        c = rnd.choice(cols)["name"]
        broken = f"SELECT {pk}, {c}\nFROM {name}\nORDER BY {c} DESC\nLIMIT {rnd.randint(2, 5)}"
        return item(broken, broken)
    if bug == "qo_unknown_alias":
        pairs = [(schema["tables"][c["ref"]], x, c) for x in tables for c in x["cols"] if c["kind"] == "fk" and c.get("ref") != x["name"]]
        if not pairs:
            return None
        p, x, fk = rnd.choice(pairs)
        broken = (f"SELECT c.{x['pk']}, p.{p['pk']}\nFROM {x['name']} c\nJOIN {p['name']} p ON p.{p['pk']} = c.{fk['name']}\n"
                  f"WHERE z.{fk['name']} IS NOT NULL\nORDER BY c.{x['pk']}")
        return item(broken, broken)
    return None


def _grain_bug(seed, bug, rnd, schema, tables, t):
    """Night 12: mistakes that live in one block of a query with CTEs / derived tables, or between blocks."""
    engine = rnd.choice(["mariadb", "postgresql", None])
    pairs = [(schema["tables"][c["ref"]], x, c) for x in tables for c in x["cols"] if c["kind"] == "fk" and c.get("ref") != x["name"]]

    def item(broken, truth, table):
        return {"seed": seed, "bug": bug, "stmt": "select", "schema": schema, "table": table, "broken": broken, "truth": truth,
                "engine": engine}

    if bug in ("gr_cte_fanout", "gr_derived_fanout"):
        pairs = [(p, x, c) for p, x, c in pairs if _cols(p, "int", "money")]
        if not pairs:
            return None
        p, x, fk = rnd.choice(pairs)
        m = rnd.choice(_cols(p, "int", "money"))["name"]
        g = rnd.choice([c for c in _cols(p, "cat", "int") + [c for c in p["cols"] if c["kind"] == "pk"] if c["name"] != m])["name"]
        agg = rnd.choice(["SUM", "COUNT"])
        inner = f"SELECT p.{g}, {agg}(p.{m}) AS total FROM {p['name']} p JOIN {x['name']} c ON c.{fk['name']} = p.{p['pk']} GROUP BY p.{g}"
        fixed = (f"SELECT p.{g}, {agg}(p.{m}) AS total FROM {p['name']} p WHERE EXISTS (SELECT 1 FROM {x['name']} c "
                 f"WHERE c.{fk['name']} = p.{p['pk']}) GROUP BY p.{g}")
        if bug == "gr_cte_fanout":
            wrap = lambda body: f"WITH per_group AS (\n  {body}\n)\nSELECT {g}, total\nFROM per_group\nORDER BY {g}"  # noqa: E731
        else:
            wrap = lambda body: f"SELECT t.{g}, t.total\nFROM ({body}) t\nORDER BY t.{g}"  # noqa: E731
        return item(wrap(inner), wrap(fixed), p["name"])
    if bug == "gr_cte_order":
        cols = [c for c in t["cols"] if c["kind"] in ("cat", "int", "money")]
        if not cols:
            return None
        c = rnd.choice(cols)["name"]
        pk = t["pk"]
        a_def = f"a AS (SELECT {pk}, {c} FROM b)"
        b_def = f"b AS (SELECT {pk}, {c} FROM {t['name']} WHERE {c} IS NOT NULL)"
        main = f"SELECT {pk} FROM a ORDER BY {pk}"
        return item(f"WITH {a_def},\n{b_def}\n{main}", f"WITH {b_def},\n{a_def}\n{main}", t["name"])
    if bug == "gr_cross":
        pairs = [(p, x, c) for p, x, c in pairs if _cols(p, "int", "money") and _cols(x, "cat")]
        if not pairs:
            return None
        p, x, fk = rnd.choice(pairs)
        m = rnd.choice(_cols(p, "int", "money"))["name"]
        cat = rnd.choice(_cols(x, "cat"))["name"]
        broken = (f"WITH per_parent AS (SELECT {p['pk']} AS pid, SUM({m}) AS total FROM {p['name']} GROUP BY {p['pk']})\n"
                  f"SELECT c.{cat}, SUM(pp.total) AS total\nFROM {x['name']} c\nJOIN per_parent pp ON pp.pid = c.{fk['name']}\n"
                  f"GROUP BY c.{cat}\nORDER BY c.{cat}")
        return item(broken, broken, x["name"])
    if bug == "gr_inner_eq_null":
        cols = [c for c in t["cols"] if c["kind"] not in ("pk",)]
        if not cols:
            return None
        c = rnd.choice(cols)["name"]
        pk = t["pk"]
        op, right = rnd.choice([("=", "IS NULL"), ("!=", "IS NOT NULL"), ("<>", "IS NOT NULL")])
        body = f"SELECT {pk} FROM {t['name']} WHERE {c} {{}}"
        return item(f"WITH picked AS (\n  {body.format(op + ' NULL')}\n)\nSELECT {pk} FROM picked ORDER BY {pk}",
                    f"WITH picked AS (\n  {body.format(right)}\n)\nSELECT {pk} FROM picked ORDER BY {pk}", t["name"])
    if bug == "gr_block_key":
        pairs = [(p, x, c) for p, x, c in pairs if _cols(x, "int", "money")]
        if not pairs:
            return None
        p, x, fk = rnd.choice(pairs)
        m = rnd.choice(_cols(x, "int", "money"))["name"]
        pm = (_cols(p, "int", "money") or [None])[0]
        extra = f", SUM(p.{pm['name']}) AS parent_total" if pm and rnd.random() < 0.5 else ""
        broken = (f"WITH per_parent AS (\n  SELECT {fk['name']}, SUM({m}) AS child_total, COUNT(*) AS n\n  FROM {x['name']}\n"
                  f"  GROUP BY {fk['name']}\n)\nSELECT p.{p['pk']}, SUM(a.child_total) AS child_total{extra}\nFROM {p['name']} p\n"
                  f"JOIN per_parent a ON a.{fk['name']} = p.{p['pk']}\nGROUP BY p.{p['pk']}\nORDER BY p.{p['pk']}")
        return item(broken, broken, p["name"])
    return None


def _struct_bug(seed, bug, rnd, schema, tables, t):
    """Night 13: structural mistakes (correlation, repeats, UNION, HAVING on a key, JOIN without ON) on random real tables."""
    from app.agents.sqljoins import _fk

    engine = rnd.choice(["mariadb", "postgresql", None])
    pairs = [(schema["tables"][c["ref"]], x, c) for x in tables for c in x["cols"] if c["kind"] == "fk" and c.get("ref") != x["name"]]

    def item(broken, truth, table, **kw):
        out = {"seed": seed, "bug": bug, "stmt": "select", "schema": schema, "table": table, "broken": broken, "truth": truth,
               "engine": engine}
        out.update(kw)
        return out

    def usual(p, fk):                                  # the link is named the way the guess expects: child.<parent>_id = parent.id
        return p["pk"] == "id" and fk["name"] == _fk(p["name"])

    if bug in ("st_uncorr", "st_inner", "st_quiet_corr"):
        pairs = [(p, x, c) for p, x, c in pairs if _cols(x, "int", "money")]
        if bug == "st_inner":
            pairs = [(p, x, c) for p, x, c in pairs if usual(p, c) and x["pk"] == "id"]
        if not pairs:
            return None
        p, x, fk = rnd.choice(pairs)
        m = rnd.choice(_cols(x, "int", "money"))["name"]
        agg = rnd.choice(["COUNT(*)", f"MAX(k.{m})", f"MIN(k.{m})"])
        good = f"(SELECT {agg} FROM {x['name']} k WHERE k.{fk['name']} = p.{p['pk']})"
        bad = {"st_uncorr": f"(SELECT {agg} FROM {x['name']} k)", "st_inner": f"(SELECT {agg} FROM {x['name']} k WHERE k.{fk['name']} = k.id)",
               "st_quiet_corr": good}[bug]
        q = f"SELECT p.{p['pk']}, {{}} AS v\nFROM {p['name']} p\nORDER BY p.{p['pk']}"
        return item(q.format(bad), q.format(good), p["name"], no_fix=not usual(p, fk))
    if bug == "st_corr_key":
        chains = [(g, p, x, f1, f2) for g, p, f1 in pairs for p2, x, f2 in pairs if p2 is p and g is not p and x is not g and x is not p
                  and usual(p, f2) and usual(g, f1) and _fk(g["name"]) not in {c["name"] for c in x["cols"]}]
        if not chains:
            return None
        g, p, x, f1, f2 = rnd.choice(chains)
        broken = (f"SELECT p.{p['pk']}, (SELECT COUNT(*) FROM {x['name']} k WHERE k.{f2['name']} = g.{g['pk']}) AS n\n"
                  f"FROM {p['name']} p\nJOIN {g['name']} g ON g.{g['pk']} = p.{f1['name']}\nORDER BY p.{p['pk']}")
        truth = broken.replace(f"k.{f2['name']} = g.{g['pk']}", f"k.{f2['name']} = p.{p['pk']}")
        return item(broken, truth, p["name"])
    if bug == "st_repeat":
        pairs = [(p, x, c) for p, x, c in pairs if _cols(x, "int", "money")]
        if not pairs:
            return None
        p, x, fk = rnd.choice(pairs)
        m = rnd.choice(_cols(x, "int", "money"))["name"]
        sub = f"(SELECT {rnd.choice(['COUNT(*)', f'MAX(k.{m})'])} FROM {x['name']} k WHERE k.{fk['name']} = p.{p['pk']})"
        where = rnd.choice([f"\nWHERE {sub} > {rnd.randint(0, 2)}", f"\nWHERE {sub} IS NOT NULL", ""])
        order = f"\nORDER BY {sub} DESC, p.{p['pk']}" if not where or rnd.random() < 0.6 else f"\nORDER BY p.{p['pk']}"
        broken = f"SELECT p.{p['pk']}, {sub} AS v\nFROM {p['name']} p{where}{order}"
        return item(broken, broken, p["name"])
    if bug == "st_same":
        nums = _cols(t, "int", "money")
        if not nums:
            return None
        a, b = rnd.choice(nums)["name"], rnd.choice(nums)["name"]
        fn = rnd.choice(["AVG", "MAX", "MIN"])
        sub = f"(SELECT {fn}({a}) FROM {t['name']})"
        broken = f"SELECT {t['pk']}\nFROM {t['name']}\nWHERE {a} <= {sub} AND {b} * 2 >= {sub} / 10\nORDER BY {t['pk']}"
        return item(broken, broken, t["name"])
    if bug in ("st_union_all", "st_union_dups"):
        if len(tables) < 2:
            return None
        t1, t2 = rnd.sample(tables, 2)
        if bug == "st_union_all":
            broken = f"SELECT {t1['pk']} AS k, 'one' AS part FROM {t1['name']}\nUNION\nSELECT {t2['pk']}, 'two' FROM {t2['name']}"
            return item(broken, broken, t1["name"])
        c1, c2 = (rnd.choice([c for c in tt["cols"] if c["kind"] in ("cat", "int")] or [None]) for tt in (t1, t2))
        if not c1 or not c2 or c1["kind"] != c2["kind"]:
            return None
        said = rnd.choice(["each value only once", "without duplicates", "bina duplicate ke", "unique values"])
        broken = (f"SELECT {c1['name']} AS v FROM {t1['name']}\nUNION ALL\nSELECT {c2['name']} FROM {t2['name']};\n\n"
                  f"all the values of both, {said}")
        truth = f"SELECT {c1['name']} AS v FROM {t1['name']}\nUNION\nSELECT {c2['name']} FROM {t2['name']}"
        return item(broken, truth, t1["name"])
    if bug == "st_having_key":
        gs = _cols(t, "cat", "int", "fk")
        if not gs:
            return None
        g = rnd.choice(gs)
        v = _pick_value(t, g, rnd)
        if v is None:
            return None
        lit = f"'{v}'" if isinstance(v, str) else str(v)
        where = ""
        nums = [c for c in _cols(t, "int", "money") if c is not g]
        if nums and rnd.random() < 0.5:
            n = rnd.choice(nums)["name"]
            where = f"\nWHERE {n} > 0 OR {n} IS NULL"
        broken = f"SELECT {g['name']}, COUNT(*) AS n\nFROM {t['name']}{where}\nGROUP BY {g['name']}\nHAVING {g['name']} = {lit}"
        return item(broken, broken, t["name"])
    if bug == "st_no_on":
        pairs = [(p, x, c) for p, x, c in pairs if usual(p, c)]
        if not pairs:
            return None
        p, x, fk = rnd.choice(pairs)
        engine = "postgresql"
        broken = f"SELECT k.{x['pk']}, p.{p['pk']}\nFROM {x['name']} k\nJOIN {p['name']} p\nORDER BY k.{x['pk']}"
        truth = f"SELECT k.{x['pk']}, p.{p['pk']}\nFROM {x['name']} k\nJOIN {p['name']} p ON k.{fk['name']} = p.{p['pk']}\nORDER BY k.{x['pk']}"
        return item(broken, truth, x["name"], engine=engine)
    return None


def _bind(sql: str, values: list) -> str:
    """The values the code binds, put where the placeholders are (outside quotes), in order."""
    out, k, q, i = [], 0, False, 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            q = not q
        m = None if q else re.match(r"\?|\$\d+|%s|:[A-Za-z_]\w*", sql[i:])
        if m and k < len(values):
            out.append("'" + values[k].replace("'", "''") + "'")
            k += 1
            i += m.end()
            continue
        out.append(ch)
        i += 1
    return "".join(out)


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
