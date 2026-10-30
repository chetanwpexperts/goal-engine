"""Generator for wrong queries: a correct parent-child join query on random real tables (school, shop, clinic, hotel...),
then broken the ways people break joins: a table name used after its alias, a misspelt alias, = NULL, a column without
its table, an ORDER BY name that doesn't exist, FULL OUTER JOIN, the same table joined twice, a total next to plain
columns. Each break must be found, and the corrected query must run on MySQL and return what the correct query returns.
Run: python -m tests.fuzz.sqlbugs [count] [seed]
"""

import random
import sys
from collections import Counter

from tests.fuzz.db import engines_up, make_schema

BUGS = ["alias_as_table", "misspelt_alias", "eq_null", "bare_column", "order_unknown", "full_outer", "dup_join", "group_loose"]
TOPIC = {"alias_as_table": "alias", "misspelt_alias": "alias", "eq_null": "null", "bare_column": "column", "order_unknown": "order",
         "full_outer": "join", "dup_join": "join", "group_loose": "group"}
# corrected query must return the same rows as the intended query (group_loose has no single right answer: found is enough)
SAME_ROWS = {"alias_as_table", "misspelt_alias", "eq_null", "bare_column", "order_unknown", "full_outer", "dup_join"}


def _pair(schema, rnd):
    """A parent table and a child table pointing at it."""
    pairs = []
    for t in schema["tables"].values():
        for c in t["cols"]:
            if c["kind"] == "fk":
                pairs.append((schema["tables"][c["ref"]], t, c["name"]))
    return rnd.choice(pairs) if pairs else None


def _plain(t, rnd, k=2):
    cols = [c["name"] for c in t["cols"] if c["kind"] not in ("pk", "fk")]
    return rnd.sample(cols, min(k, len(cols)))


def make(seed: int):
    rnd = random.Random(seed)
    schema = make_schema(seed)
    got = _pair(schema, rnd)
    if not got:
        return None
    parent, child, fk = got
    p, c = "p", rnd.choice(["c", "ch", "x"])
    pcols, ccols = _plain(parent, rnd), _plain(child, rnd)
    if not pcols or not ccols:
        return None
    label = pcols[0]
    sel = [f"{p}.{parent['pk']} AS parent_key", f"{p}.{label} AS parent_{label.lower()}"] + [f"{c}.{x}" for x in ccols]
    join_kind = rnd.choice(["INNER JOIN", "LEFT JOIN"])
    where_col = ccols[0] if join_kind == "INNER JOIN" else pcols[-1]
    where_alias = c if join_kind == "INNER JOIN" else p
    bug = rnd.choice(BUGS)

    def build(bug, sel=sel):
        s = list(sel)
        joins = [f"{join_kind} {child['name']} {c}\n    ON {c}.{fk} = {p}.{parent['pk']}"]
        where = [f"{where_alias}.{where_col} IS NOT NULL"]
        order = [f"parent_{label.lower()}", f"{p}.{parent['pk']}", f"{c}.{child['pk']}"]
        group = ""
        if bug == "alias_as_table":
            joins[0] = joins[0].replace(f"= {p}.{parent['pk']}", f"= {parent['name']}.{parent['pk']}")
        elif bug == "misspelt_alias":
            joins[0] = joins[0].replace(f"= {p}.{parent['pk']}", f"= {parent['name'][:-1]}.{parent['pk']}")
        elif bug == "eq_null":
            where = [f"{where_alias}.{where_col} != NULL"]
        elif bug == "bare_column":
            where = [f"{where_col} IS NOT NULL"]
            if where_col.lower() in {x["name"].lower() for x in (parent if where_alias == c else child)["cols"]}:
                return None                                   # really ambiguous: the fix can't be known
        elif bug == "order_unknown":
            order[0] = "parent"
        elif bug == "full_outer":
            joins.append(f"FULL OUTER JOIN {child['name']} {c}2\n    ON {c}2.{fk} = {p}.{parent['pk']}")
        elif bug == "dup_join":
            joins.append(f"LEFT JOIN {child['name']} {c}2\n    ON {c}2.{fk} = {p}.{parent['pk']}")
        elif bug == "group_loose":
            s.append(f"COUNT({c}.{child['pk']}) AS lines")
            group = f"\nGROUP BY {p}.{parent['pk']}"
        return ("SELECT\n    " + ",\n    ".join(s) + f"\nFROM {parent['name']} {p}\n" + "\n".join(joins)
                + "\nWHERE " + " AND ".join(where) + group + "\nORDER BY " + ", ".join(order) + ";")

    broken, truth = build(bug), build(None)
    if broken is None:
        return None
    if bug == "eq_null":
        truth = truth                                         # != NULL was meant as IS NOT NULL: the truth already says so
    return {"seed": seed, "bug": bug, "schema": schema, "broken": broken, "truth": truth}


def check(item, eng) -> list[str]:
    from app.agents import sqlcorrect

    res = sqlcorrect.check(item["broken"])
    topics = {f["topic"] for f in res["findings"]}
    bad = []
    tag = f"{item['bug']} seed {item['seed']}"
    if TOPIC[item["bug"]] not in topics:
        bad.append(f"{tag}: not found (found {sorted(topics)})\n{item['broken']}")
    wrong_kinds = [f["title_en"] for f in res["findings"] if f["kind"] in ("error", "wrong") and f["topic"] != TOPIC[item["bug"]]
                   and not (item["bug"] == "group_loose")]
    if wrong_kinds:
        bad.append(f"{tag}: extra findings {wrong_kinds}")
    knowable = not (item["bug"] == "bare_column" and item["truth"].count(item["broken"].split("WHERE ")[1].split()[0]) < 2)
    if res["corrected"]:
        told = {x["title_en"] for x in res.get("left", [])}                # left for the person, and the reply says so
        again = [f["title_en"] for f in sqlcorrect.check(res["corrected"])["findings"] if f["kind"] in ("error", "wrong")
                 and f["title_en"] not in told]
        if again:
            bad.append(f"{tag}: the corrected query still has mistakes {again}\n{res['corrected']}")
    if eng is not None and item["bug"] in SAME_ROWS and knowable:      # a lone bare column: only the structure can say whose
        fixed = res["corrected"]
        if not fixed:
            bad.append(f"{tag}: no corrected query")
        else:
            try:
                got = Counter(map(tuple, eng.run(fixed.split(";")[0])))
                want = Counter(map(tuple, eng.run(item["truth"].split(";")[0])))
                if got != want:
                    bad.append(f"{tag}: corrected query returns other rows\n{fixed}")
            except Exception as exc:  # noqa: BLE001
                bad.append(f"{tag}: corrected query fails: {exc}\n{fixed}")
    return bad


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    eng = engines_up().get("mysql")
    fails, made, by = [], 0, Counter()
    for s in range(seed, seed + count * 3):
        if made >= count:
            break
        item = make(s)
        if item is None:
            continue
        made += 1
        by[item["bug"]] += 1
        if eng is not None:
            eng.create(item["schema"])
        try:
            fails += check(item, eng)
        finally:
            if eng is not None:
                eng.drop(item["schema"]["order"])
    for f in fails[:25]:
        print(f, "\n")
    print(dict(by))
    print(f"{made} queries, {len(fails)} problems{'' if eng else ' (MySQL not running: rows not compared)'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
