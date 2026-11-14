"""Reading an EXPLAIN plan (night 8 of the curriculum): MySQL / MariaDB tables (| id | select_type | table | type | ...,
tab-separated, or the vertical \\G form, with ANALYZE's r_rows / r_filtered), MySQL's tree form (-> Table scan on t ...) and
PostgreSQL's text plans (Seq Scan on t (cost=... rows=...) (actual ... rows=... loops=...), Filter, Rows Removed by Filter,
Sort Method, Heap Fetches ...). The parsing is shared; what each access type / plan node MEANS is the engine's own and lives
in its dialect file ('explain' in app/dbknow/dialects/mysql.json and postgresql.json, rule B).

Fundamental 'index_use': a plan says, table by table, whether rows are found through an index (a jump to the matching
entries) or by reading everything and checking each row. read() returns the steps in plain words and the findings: a full
scan of a big table (and why no index was used, when the plan shows it: an index existed but wasn't picked, or the filter
wraps the column in a function), a join that reads the inner table again for every outer row, a sort or hash that spilled to
disk, estimates far from the real row counts (statistics), an index that doesn't cover the filter, a full index scan.
Everything here was checked against plans the lab produced on MariaDB 10.11 and PostgreSQL 16 (tests/practice/test_night8.py).
"""

import json
import re
from functools import lru_cache
from pathlib import Path

PROVEN = {k: ["mariadb", "postgresql"] for k in ("plan_full_scan", "plan_index_ignored", "plan_function", "plan_join_scan",
                                                  "plan_filesort", "plan_temporary", "plan_spill", "plan_estimate", "plan_filter_after_index",
                                                  "plan_full_index", "plan_heap_fetches", "plan_dependent", "plan_impossible")}
BIG = 1000                                     # rows: below this a full read is cheap and nothing is said

FUNC_IN_FILTER = re.compile(r"\b(extract|date_part|date|year|month|lower|upper|\"left\"|left|substring|substr|coalesce|ifnull|trim|"
                            r"to_char|date_format|date_trunc|cast)\s*\(|\([a-z_][\w.]*\)::(?:date|numeric|integer|bigint|timestamp)\b|"
                            r"\(\(?[a-z_][\w.]*\)?\s*[+\-*/]\s*\d", re.I)


@lru_cache(maxsize=None)
def _meanings(family: str) -> dict:
    d = json.loads((Path(__file__).resolve().parents[1] / "dbknow" / "dialects" / f"{family}.json").read_text(encoding="utf-8"))
    return d.get("explain", {})


def _int(v) -> int | None:
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


# ------------------------------------------------------------------------------------------------ MySQL / MariaDB tables
_MY_COLS = {"id", "select_type", "table", "partitions", "type", "possible_keys", "key", "key_len", "ref", "rows", "r_rows",
            "filtered", "r_filtered", "extra"}


def _my_rows(text: str) -> list[dict]:
    lines = text.splitlines()
    # | id | select_type | table | ... |   or   id<TAB>select_type<TAB>table ...
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "select_type" in low and re.search(r"\btable\b", low) and re.search(r"\btype\b", low):
            sep = "|" if "|" in ln else "\t" if "\t" in ln else None
            if sep is None:
                cells = ln.split()
                if not {"id", "select_type", "table", "type"} <= {c.lower() for c in cells}:
                    continue
                sep = " "
            head = [c.strip().lower() for c in (ln.strip().strip("|").split(sep) if sep != " " else ln.split())]
            out = []
            for row in lines[i + 1:]:
                if re.match(r"^\s*\+[-+]+\+\s*$", row) or not row.strip():
                    if out and not row.strip():
                        break
                    continue
                if row.lower().strip().startswith(("rows in set", "row in set")) or re.match(r"^\s*\d+\s+rows?\s+in\s+set", row, re.I):
                    break
                cells = [c.strip() for c in (row.strip().strip("|").split(sep) if sep != " " else row.split(None, len(head) - 1))]
                if len(cells) < 4:
                    continue
                r = {head[k]: cells[k] for k in range(min(len(head), len(cells)))}
                if not re.match(r"^\d+$|^NULL$", r.get("id", ""), re.I):
                    continue
                out.append(r)
            if out:
                return out
    # vertical: *************************** 1. row ***************************
    if re.search(r"\*+\s*\d+\.\s*row\s*\*+", text):
        out, cur = [], None
        for ln in lines:
            if re.search(r"\*+\s*\d+\.\s*row\s*\*+", ln):
                cur = {}
                out.append(cur)
                continue
            m = re.match(r"^\s*(\w+):\s?(.*)$", ln)
            if m and cur is not None and m.group(1).lower() in _MY_COLS:
                cur[m.group(1).lower()] = m.group(2).strip()
        return [r for r in out if r.get("table") or r.get("type")]
    return []


def _nul(v) -> str:
    v = (v or "").strip()
    return "" if v.upper() in ("NULL", "") else v


def _read_mysql(rows: list[dict]) -> dict:
    M = _meanings("mysql")
    steps, finds = [], []
    for r in rows:
        table, typ, key, poss = _nul(r.get("table")), _nul(r.get("type")).upper(), _nul(r.get("key")), _nul(r.get("possible_keys"))
        extra = r.get("extra", "") or ""
        est, real = _int(r.get("rows")), _int(r.get("r_rows"))
        n = real if real is not None else est
        acc = M.get("access", {}).get(typ, {})
        st = {"table": table, "access": typ or "-", "key": key, "rows": n, "en": "", "hi": ""}
        rows_en = f"about {n:,} rows" if n is not None else "rows"
        st["en"] = f"{table or '(derived)'}: {typ or '-'}" + (f" via {key}" if key else "") + f" - {acc.get('en', 'access type ' + typ)} ({rows_en})"
        st["hi"] = f"{table or '(derived)'}: {typ or '-'}" + (f" ({key} se)" if key else "") + f" - {acc.get('hi', acc.get('en', typ))} ({n if n is not None else '?'} rows)"
        for word, mean in M.get("extra", {}).items():
            if re.search(re.escape(word) + (r"(?!\s*(?:condition|for))" if word == "Using index" else ""), extra, re.I):
                st["en"] += f"; {word}: {mean['en']}"
                st["hi"] += f"; {word}: {mean['hi']}"
        steps.append(st)
        sel = (r.get("select_type") or "").upper()
        if "impossible where" in extra.lower():
            finds.append(_f(3, "plan_impossible", "The plan says the WHERE can never be true (Impossible WHERE)",
                            "Plan keh raha hai WHERE kabhi true nahi hoga (Impossible WHERE)", "Impossible WHERE",
                            "The optimizer proved from the conditions (or the keys) that no row can match, so the query returns nothing. "
                            "Usually two conditions on one column contradict each other (x = 1 AND x = 2, a range written backwards) or a "
                            "value is compared with a column that can't hold it. Check the WHERE.",
                            "Optimizer ne conditions se saabit kiya ki koi row match nahi ho sakti, isliye result khaali hai. Aksar ek column "
                            "pe do ulti conditions hoti hain (x = 1 AND x = 2, ulti range). WHERE check karo."))
        if typ == "ALL" and (n or 0) >= BIG:
            if "join buffer" in extra.lower():
                finds.append(_f(3, "plan_join_scan", f"Join without an index on {table} (Using join buffer)",
                                f"{table} pe bina index ke join (Using join buffer)", f"{table}: type ALL, {extra}",
                                f"For every batch of rows from the tables before it, {table} is read in full (about {n:,} rows) because "
                                f"the column it is joined on has no usable index. Index {table}'s join column (the column of {table} in "
                                f"the ON); then this step becomes 'ref' or 'eq_ref'.",
                                f"Pichli tables ki har batch ke liye {table} poori padhi jaati hai (lagbhag {n:,} rows), kyunki jis column "
                                f"pe join hai uska index nahi. ON mein {table} ke column pe index lagao; tab ye 'ref' ya 'eq_ref' banega."))
            elif poss:
                finds.append(_f(3, "plan_index_ignored", f"EXPLAIN shows a full table scan (type: ALL) on {table}: an index exists but wasn't used",
                                f"EXPLAIN mein {table} ka poora scan (type: ALL): index hai par use nahi hua", f"{table}: type ALL, possible_keys {poss}, key NULL",
                                f"possible_keys lists {poss}, yet key is NULL: the optimizer judged reading all {n:,} rows cheaper. That "
                                f"happens when the condition matches a large share of the table, or when the column is wrapped in a function or "
                                f"compared with another type (text = number), so the index can only be scanned, not searched. Make the "
                                f"condition compare the bare column with a value of its own type, or narrow it; a composite index that "
                                f"also covers the other filters can make it selective.",
                                f"possible_keys mein {poss} hai par key NULL: optimizer ko saari {n:,} rows padhna sasta laga. Aisa tab hota "
                                f"hai jab condition table ka bada hissa match kare, ya column pe function ho / alag type se compare ho. "
                                f"Column ko seedha uske type ki value se compare karo, ya condition chhoti karo."))
            else:
                finds.append(_f(3, "plan_full_scan", f"EXPLAIN shows a full table scan (type: ALL) on {table}",
                                f"EXPLAIN mein {table} ka poora table scan (type: ALL)", f"{table}: type ALL, rows {n:,}",
                                f"Every row of {table} (about {n:,}) is read and checked, and no index was even a candidate (possible_keys "
                                f"is NULL). Index the columns this table is filtered or joined by, equality columns first (see the "
                                f"indexes above); then this step becomes ref / range.",
                                f"{table} ki har row (lagbhag {n:,}) padh ke check hoti hai, aur koi index candidate bhi nahi tha. Is table "
                                f"ke filter/join columns pe index lagao, '=' wale pehle; tab ye ref / range banega."))
        elif typ == "INDEX" and (n or 0) >= BIG and "using index for group-by" not in extra.lower():
            finds.append(_f(2, "plan_full_index", f"{table} is read through its whole index {key} (type: index)",
                            f"{table} ka poora index {key} padha ja raha hai (type: index)", f"{table}: type index, rows {n:,}",
                            f"type 'index' means every entry of {key} is read, in index order: cheaper than the table when the index "
                            f"holds all the columns needed ('Using index'), but still all {n:,} entries. The condition can't jump into "
                            f"the index: usually the filtered column is not the index's first column, or it is inside a function.",
                            f"'index' ka matlab {key} ki har entry padhi jaati hai: table se sasta par phir bhi saari {n:,}. Condition index "
                            f"mein seedha nahi jaa sakti: aksar filter wala column index ka pehla column nahi hai, ya function ke andar hai."))
        if "filesort" in extra.lower():
            finds.append(_f(2, "plan_filesort", "Sorting without an index (Using filesort)", "Bina index ke sorting (Using filesort)",
                            f"{table}: Using filesort",
                            "The rows are sorted after they are found. With ORDER BY ... LIMIT that means finding all of them first. An "
                            "index whose columns are the WHERE's '=' columns followed by the ORDER BY columns returns them already in "
                            "order and stops at the LIMIT.",
                            "Rows milne ke baad sort hoti hain; ORDER BY ... LIMIT mein pehle saari dhoondhni padti hain. WHERE ke '=' "
                            "columns aur phir ORDER BY columns wala index rows kram mein deta hai aur LIMIT pe ruk jaata hai."))
        if "using temporary" in extra.lower():
            finds.append(_f(2, "plan_temporary", "Temporary table for GROUP BY/DISTINCT", "GROUP BY/DISTINCT ke liye temporary table",
                            f"{table}: Using temporary",
                            "The groups (or distinct rows) are built in a temporary table. An index on the GROUP BY columns lets the "
                            "database read them already grouped; for a report over many rows, a summary table kept up to date is the "
                            "bigger win.",
                            "Groups temporary table mein bante hain. GROUP BY columns pe index se rows pehle se grouped milti hain; badi "
                            "report ke liye summary table zyada fayda deti hai."))
        if "DEPENDENT" in sel and (n or 0) >= 1:
            finds.append(_f(2, "plan_dependent", f"A subquery runs again for every outer row ({sel})",
                            f"Subquery har bahar wali row ke liye dobara chalti hai ({sel})", f"{table}: {sel}",
                            "DEPENDENT SUBQUERY means the subquery is tied to the outer row and is run once per outer row. Make sure the "
                            "column it looks up by is indexed, or total it once in a derived table (GROUP BY the link column) and join to "
                            "that.",
                            "DEPENDENT SUBQUERY har bahar wali row ke liye chalti hai. Jis column se dhoondhti hai uspe index rakho, ya ek "
                            "baar derived table mein total karke (link column se GROUP BY) join karo."))
        filt = r.get("r_filtered") or r.get("filtered")
        try:
            fv = float(filt) if filt not in (None, "", "NULL") else None
        except ValueError:
            fv = None
        if fv is not None and fv < 10 and (n or 0) >= BIG and typ in ("REF", "RANGE", "INDEX_MERGE"):
            finds.append(_f(2, "plan_filter_after_index", f"{table}: the index finds {n:,} rows but only {fv:g}% are kept",
                            f"{table}: index {n:,} rows deta hai par sirf {fv:g}% bachti hain", f"{table}: rows {n:,}, filtered {fv:g}",
                            f"The index ({key}) narrows the search, but most of the rows it finds are thrown away by the rest of the WHERE. "
                            f"A composite index with {key}'s columns plus the other filtered columns ('=' ones first) finds only the rows "
                            f"that are kept.",
                            f"Index ({key}) search chhota karta hai par mili hui zyada rows baaki WHERE se hat jaati hain. {key} ke columns "
                            f"aur baaki filter columns ('=' wale pehle) ka composite index sirf kaam ki rows dhoondhega."))
        if est is not None and real is not None and max(est, real) >= 100 and (max(est, real) / max(1, min(est, real))) >= 10:
            finds.append(_f(2, "plan_estimate", f"{table}: the optimizer expected {est:,} rows, ANALYZE found {real:,}",
                            f"{table}: optimizer ne {est:,} rows socha, asal mein {real:,}", f"{table}: rows {est}, r_rows {real}",
                            "A plan is chosen from estimates. When they are this far off, run ANALYZE TABLE on the table so its "
                            "statistics are fresh (and on MariaDB consider histograms: ANALYZE TABLE t PERSISTENT FOR ALL); a condition "
                            "that wraps the column in a function can't be estimated at all.",
                            "Plan andaaze se chuna jaata hai; itna farak ho to ANALYZE TABLE chalao taaki statistics taaze hon. Column pe "
                            "function wali condition ka andaaza lag hi nahi sakta."))
    return {"engine": "mysql", "analyzed": any(r.get("r_rows") not in (None, "") for r in rows), "steps": steps, "findings": finds}


# ------------------------------------------------------------------------------------------------ PostgreSQL / MySQL tree
_PG_NODE = re.compile(r"^(?P<indent>\s*)(?:->\s+)?(?P<name>[A-Z][A-Za-z ]+?)(?:\s+(?:using|on)\s+[^\s(]+(?:\s+using\s+\S+)?"
                      r"(?:\s+on\s+\S+)?(?:\s+\w+)?)?\s*\((?:cost=(?P<c1>[\d.]+)\.\.(?P<c2>[\d.]+)\s+rows=(?P<rows>\d+)\s+width=\d+)\)"
                      r"(?:\s*\(actual(?: time=[\d.]+\.\.[\d.]+)? rows=(?P<arows>[\d.]+) loops=(?P<loops>\d+)\))?", re.M)
_PG_ACTUAL_ONLY = re.compile(r"^(?P<indent>\s*)(?:->\s+)?(?P<name>[A-Z][A-Za-z ]+?)(?:\s+(?:using|on)\s+.*?)?\s*\(actual(?: time=[\d.]+\.\.[\d.]+)?"
                             r" rows=(?P<arows>[\d.]+) loops=(?P<loops>\d+)\)", re.M)


def _pg_nodes(text: str) -> list[dict]:
    out = []
    for ln in text.splitlines():
        m = _PG_NODE.match(ln) or _PG_ACTUAL_ONLY.match(ln)
        if m and (m.groupdict().get("rows") or m.groupdict().get("arows")) is not None:
            raw = ln.strip()
            raw = re.sub(r"^->\s+", "", raw)
            head = re.split(r"\s+\((?:cost|actual)", raw)[0]
            tm = re.search(r"\bon\s+([\w.\"]+)(?:\s+(\w+))?\s*$", head)
            im = re.search(r"\busing\s+([\w.\"]+)", head)
            out.append({"indent": len(m.group("indent")), "name": m.group("name").strip(), "head": head,
                        "table": (tm.group(1).strip('"') if tm else ""), "index": (im.group(1).strip('"') if im else ""),
                        "alias": (tm.group(2) or "") if tm else "",
                        "rows": _int(m.groupdict().get("rows")), "arows": _int(m.groupdict().get("arows")),
                        "cost": float(m.groupdict().get("c2") or 0),
                        "loops": _int(m.groupdict().get("loops")), "attrs": {}})
            continue
        am = re.match(r"^\s*([A-Z][A-Za-z ]+?):\s*(.*)$", ln)
        if am and out:
            out[-1]["attrs"][am.group(1).strip()] = am.group(2).strip()
    return out


def _pg_children(nodes, i):
    """Direct children of node i (deeper indent, until a node at i's indent or shallower)."""
    out, base = [], nodes[i]["indent"]
    child = None
    for j in range(i + 1, len(nodes)):
        if nodes[j]["indent"] <= base:
            break
        if child is None or nodes[j]["indent"] <= child:
            child = nodes[j]["indent"]
            out.append(j)
    return out


def _read_pg(text: str) -> dict | None:
    nodes = _pg_nodes(text)
    if not nodes:
        return None
    M = _meanings("postgresql")
    analyzed = any(n["arows"] is not None for n in nodes)
    steps, finds = [], []
    for n in reversed(nodes):                                    # the deepest steps run first
        mean = next((v for k, v in M.get("nodes", {}).items() if n["name"].lower() == k.lower()), None) or \
            next((v for k, v in M.get("nodes", {}).items() if n["name"].lower().endswith(k.lower())), {})
        rows = n["arows"] if n["arows"] is not None else n["rows"]
        where = f" {n['table']}" if n["table"] else ""
        st = {"table": n["table"], "alias": n.get("alias", ""), "access": n["name"], "key": n["index"], "rows": rows,
              "en": f"{n['head']}: {mean.get('en', n['name'])} ({'' if n['arows'] is not None else 'about '}{rows:,} rows"
                    + (f", {n['loops']} times" if (n["loops"] or 1) > 1 else "") + ")",
              "hi": f"{n['head']}: {mean.get('hi', mean.get('en', n['name']))} ({rows:,} rows" + (f", {n['loops']} baar" if (n["loops"] or 1) > 1 else "") + ")"}
        for k in ("Filter", "Index Cond", "Hash Cond", "Join Filter", "Sort Key", "Sort Method"):
            if k in n["attrs"]:
                st["en"] += f"; {k}: {n['attrs'][k]}"
                st["hi"] += f"; {k}: {n['attrs'][k]}"
        steps.append(st)
        removed = _int((n["attrs"].get("Rows Removed by Filter") or "").split()[0] if n["attrs"].get("Rows Removed by Filter") else None)
        filt = n["attrs"].get("Filter", "")
        big = max(rows or 0, (removed or 0) + (rows or 0))
        guess = n["arows"] is None and n["cost"] >= 200    # plain EXPLAIN: the rows read aren't shown; the cost says it isn't tiny
        if n["name"] in ("Seq Scan", "Parallel Seq Scan") and (big >= BIG or guess) and (filt or removed):
            func = FUNC_IN_FILTER.search(filt or "")
            if func:
                finds.append(_f(3, "plan_function", f"EXPLAIN shows a full table scan (Seq Scan) on {n['table']}: the filter wraps the column",
                                f"EXPLAIN mein {n['table']} ka poora scan (Seq Scan): filter column ko function mein lapet-ta hai",
                                f"Filter: {_flat(filt)[:120]}",
                                f"The filter {_flat(filt)[:80]} works out a new value for every row, so no plain index on the column can find "
                                f"them: every row of {n['table']} is read{f' ({removed:,} thrown away)' if removed else ''}. Compare the "
                                f"bare column instead (a date range, the number moved to the other side, a LIKE prefix); or, if the "
                                f"expression must stay, index that same expression: CREATE INDEX ... ON {n['table']} ((expression)).",
                                f"Filter har row ke liye nayi value banata hai, isliye column ka index kaam nahi aata aur {n['table']} ki har "
                                f"row padhi jaati hai. Seedha column compare karo (date range, number doosri taraf, LIKE prefix); ya wahi "
                                f"expression index karo: CREATE INDEX ... ON {n['table']} ((expression))."))
            else:
                kept = rows or 0
                finds.append(_f(3, "plan_full_scan", f"EXPLAIN shows a full table scan (Seq Scan) on {n['table']}",
                                f"EXPLAIN mein {n['table']} ka poora table scan (Seq Scan)",
                                f"Seq Scan on {n['table']}" + (f", Rows Removed by Filter: {removed:,}" if removed else f", rows={rows}"),
                                f"Every row of {n['table']} is read and checked against {_flat(filt)[:80] or 'the filter'}"
                                + (f": {removed:,} rows thrown away to keep {kept:,}" if removed else "")
                                + ". If the filter keeps a small share of the table, an index on the filtered columns ('=' ones first) "
                                  "lets PostgreSQL go straight to them; if it keeps most rows, a Seq Scan is the right plan.",
                                f"{n['table']} ki har row padh ke filter se check hoti hai" + (f": {removed:,} rows phenki, {kept:,} rakhi" if removed else "")
                                + ". Filter table ka chhota hissa rakhta ho to filter columns pe index ('=' wale pehle) lagao; zyada rows "
                                  "rakhta ho to Seq Scan hi sahi hai."))
        if n["name"] in ("Index Scan", "Index Only Scan", "Index Scan Backward", "Bitmap Heap Scan") and removed and removed >= BIG and \
                removed > 2 * max(1, rows or 0):
            finds.append(_f(2, "plan_filter_after_index", f"{n['table']}: the index finds rows that the Filter then throws away ({removed:,})",
                            f"{n['table']}: index jo rows deta hai unme se {removed:,} Filter phenk deta hai", f"Filter: {_flat(filt)[:100]}",
                            f"The index ({n['index'] or 'used here'}) narrows the search, but {removed:,} of the rows it returns fail "
                            f"{_flat(filt)[:60]}. An index that also holds the filtered columns ('=' ones first) finds only the rows kept.",
                            f"Index search chhota karta hai, par uski {removed:,} rows filter mein fail hoti hain. Filter wale columns bhi "
                            f"index mein rakho ('=' wale pehle)."))
        jr = _int((n["attrs"].get("Rows Removed by Join Filter") or "").split()[0] if n["attrs"].get("Rows Removed by Join Filter") else None)
        if n["name"] == "Nested Loop" and jr and jr >= BIG:
            finds.append(_f(3, "plan_join_scan", f"Nested Loop compares every pair of rows ({jr:,} pairs thrown away)",
                            f"Nested Loop har jodi compare karta hai ({jr:,} jodiyan phenki)", f"Join Filter: {_flat(n['attrs'].get('Join Filter', ''))[:100]}",
                            f"The join condition {_flat(n['attrs'].get('Join Filter', ''))[:80]} is checked for every pair of rows from the two "
                            "sides: no index lets PostgreSQL look up the matching rows directly. Index the join column of the larger table "
                            "(and keep the join a plain equality of two columns of the same type), so each row is a short index lookup.",
                            "Join condition dono taraf ki har jodi pe check hoti hai: koi index seedha matching rows nahi dhoondhta. Badi table "
                            "ke join column pe index lagao (aur join do same-type columns ki seedhi barabari rakho)."))
        if n["name"] in ("Nested Loop",):
            kids = _pg_children(nodes, nodes.index(n))
            if len(kids) == 2:
                inner = nodes[kids[1]]
                if inner["name"] in ("Seq Scan", "Parallel Seq Scan") and (inner["loops"] or 0) > 1 and \
                        ((inner["loops"] or 0) * max(1, (inner["arows"] or 0) + (_int((inner["attrs"].get("Rows Removed by Filter") or "0").split()[0]) or 0))) >= BIG:
                    finds.append(_f(3, "plan_join_scan", f"Nested Loop reads {inner['table']} in full {inner['loops']:,} times",
                                    f"Nested Loop {inner['table']} ko {inner['loops']:,} baar poora padhta hai", f"Seq Scan on {inner['table']}, loops={inner['loops']}",
                                    f"For each row from the outer side, {inner['table']} is scanned from start to end. Index the column of "
                                    f"{inner['table']} used in the join condition; the inner step becomes an Index Scan.",
                                    f"Bahar ki har row ke liye {inner['table']} shuru se aakhir tak padhi jaati hai. Join wale column pe index "
                                    f"lagao; andar ka step Index Scan ban jaayega."))
        sm = n["attrs"].get("Sort Method", "")
        if "external" in sm.lower() or re.search(r"\bDisk:", sm):
            finds.append(_f(2, "plan_spill", "The sort spilled to disk", "Sort disk pe chala gaya", f"Sort Method: {sm}",
                            "The rows to sort didn't fit in work_mem, so PostgreSQL sorted them in temporary files. Sort fewer rows "
                            "(filter first, or an index on the ORDER BY columns so no sort is needed), or raise work_mem for this query "
                            "only: SET LOCAL work_mem = '64MB' inside its transaction.",
                            "Sort ki rows work_mem mein nahi samaayi, isliye temporary files mein sort hua. Kam rows sort karo (pehle "
                            "filter, ya ORDER BY columns pe index), ya sirf is query ke liye work_mem badhao: SET LOCAL work_mem = '64MB'."))
        bm = n["attrs"].get("Buckets", "")
        bt = re.search(r"Batches:\s*(\d+)", bm + " " + n["attrs"].get("Batches", ""))
        if n["name"] == "Hash" and bt and int(bt.group(1)) > 1:
            finds.append(_f(2, "plan_spill", "The hash table spilled to disk (Batches > 1)", "Hash table disk pe gaya (Batches > 1)",
                            f"Batches: {bt.group(1)}", "The hash of the smaller join side didn't fit in work_mem and was split into "
                            "batches on disk. Join fewer rows (filter first) or raise work_mem for this query.",
                            "Join ki chhoti taraf ka hash work_mem mein nahi samaya. Pehle filter karo ya is query ke liye work_mem badhao."))
        hf = _int((n["attrs"].get("Heap Fetches") or "").split()[0] if n["attrs"].get("Heap Fetches") else None)
        if n["name"] == "Index Only Scan" and hf and hf >= BIG and hf >= (rows or 0) * (n["loops"] or 1) * 0.5:
            finds.append(_f(2, "plan_heap_fetches", f"Index Only Scan still visits the table ({hf:,} heap fetches)",
                            f"Index Only Scan phir bhi table padhta hai ({hf:,} heap fetches)", f"Heap Fetches: {hf}",
                            "An index-only scan skips the table only for pages the visibility map marks all-visible. Many heap fetches "
                            f"mean {n['table']} changed since its last VACUUM: run VACUUM (ANALYZE) {n['table']} (autovacuum may need tuning).",
                            f"Index-only scan table tabhi chhodta hai jab pages all-visible hon. Zyada heap fetches ka matlab {n['table']} "
                            f"pichle VACUUM ke baad badli hai: VACUUM (ANALYZE) {n['table']} chalao."))
        if n["name"] in ("Hash", "Sort", "Materialize", "Memoize", "Limit", "Gather", "Gather Merge", "Incremental Sort"):
            continue                                              # a pass-through step repeats its input's estimate
        if n["arows"] is not None and n["rows"] is not None and max(n["rows"], n["arows"]) >= 100 and \
                max(n["rows"], n["arows"]) / max(1, min(n["rows"], n["arows"])) >= 10:
            why_fn = FUNC_IN_FILTER.search(filt or n["attrs"].get("Index Cond", "") or "")
            finds.append(_f(2, "plan_estimate", f"{n['head']}: planned {n['rows']:,} rows, got {n['arows']:,}",
                            f"{n['head']}: {n['rows']:,} rows socha, mili {n['arows']:,}", f"rows={n['rows']} vs actual rows={n['arows']}",
                            ("The planner can't estimate a condition that wraps the column in a function (it guesses about 0.5% of "
                             "the rows), so the plan above it is chosen blind. Compare the bare column (then the statistics apply), or "
                             "index the expression (PostgreSQL keeps statistics for an expression index after ANALYZE)." if why_fn else
                             f"The plan was chosen from estimates this far off. Run ANALYZE {n['table'] or 'on the tables'} so the "
                             "statistics are fresh; for columns that depend on each other, CREATE STATISTICS helps the planner."),
                            ("Function wali condition ka andaaza planner nahi laga sakta (lagbhag 0.5% maan leta hai), isliye upar ka "
                             "plan andhere mein chuna gaya. Seedha column compare karo, ya wahi expression index karo." if why_fn else
                             f"Plan itne galat andaaze se chuna gaya. ANALYZE {n['table'] or ''} chalao taaki statistics taaze hon.")))
    # MySQL 8 tree / EXPLAIN ANALYZE lines are handled by _read_tree
    return {"engine": "postgresql", "analyzed": analyzed, "steps": steps, "findings": _dedupe(finds),
            "total_ms": _float(re.search(r"Execution Time:\s*([\d.]+)", text))}


def _float(m):
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


_TREE = re.compile(r"^(?P<indent>\s*)->\s+(?P<what>.+?)(?:\s+\(cost=[\d.e+]+(?:\.\.[\d.e+]+)?\s+rows=(?P<rows>[\d.e+]+)\))?"
                   r"(?:\s+\(actual time=[\d.]+\.\.[\d.]+ rows=(?P<arows>[\d.]+) loops=(?P<loops>\d+)\))?\s*$", re.M)


def _read_tree(text: str) -> dict | None:
    """MySQL 8 EXPLAIN FORMAT=TREE / EXPLAIN ANALYZE: '-> Table scan on t  (cost=... rows=...)'."""
    lines = [m for m in _TREE.finditer(text) if not re.match(r"^\s*->\s+(Seq Scan|Index Scan|Index Only Scan|Bitmap|Hash Join|Nested Loop\s+\(|"
                                                              r"Hash\s+\(|Sort\s+\(|Limit\s+\()", m.group(0))]
    if not lines or not any(re.search(r"\b(Table scan|Index lookup|Index range scan|Covering index|Single-row index lookup|"
                                      r"Filter:|Nested loop|Index scan on|Full-text)", m.group("what")) for m in lines):
        return None
    M = _meanings("mysql")
    steps, finds = [], []
    for m in reversed(lines):
        what = m.group("what")
        rows = _int(m.group("arows")) if m.group("arows") else (_int(float(m.group("rows"))) if m.group("rows") else None)
        key = next((k for k in M.get("tree", {}) if what.lower().startswith(k.lower())), None)
        mean = M.get("tree", {}).get(key, {}) if key else {}
        steps.append({"table": "", "access": what.split(" on ")[0], "key": "", "rows": rows,
                      "en": f"{what}: {mean.get('en', '')}".rstrip(": ") + (f" ({rows:,} rows)" if rows is not None else ""),
                      "hi": f"{what}: {mean.get('hi', mean.get('en', ''))}".rstrip(": ") + (f" ({rows:,} rows)" if rows is not None else "")})
        tm = re.match(r"Table scan on (\w+)", what)
        if tm and (rows or 0) >= BIG:
            finds.append(_f(3, "plan_full_scan", f"EXPLAIN shows a full table scan on {tm.group(1)}", f"EXPLAIN mein {tm.group(1)} ka poora table scan",
                            what, f"Every row of {tm.group(1)} (about {rows:,}) is read. Index the columns it is filtered or joined by "
                                  "('=' ones first), and compare them bare (no function around the column).",
                            f"{tm.group(1)} ki har row padhi jaati hai. Filter/join columns pe index lagao aur unhe seedha compare karo."))
        fm = re.match(r"Filter:\s*(.+)", what)
        if fm and FUNC_IN_FILTER.search(fm.group(1)):
            finds.append(_f(3, "plan_function", "The filter wraps the column in a function", "Filter column ko function mein lapet-ta hai",
                            what, "A function around the column hides it from every index on it; compare the bare column instead (a date "
                                  "range, the number moved across, a LIKE prefix) or index the expression (MySQL 8.0.13+: INDEX ((expr))).",
                            "Column pe function use index se chhupa deta hai; seedha column compare karo ya expression index karo."))
        if re.search(r"\bSort\b.*(filesort)?", what) and what.lower().startswith("sort"):
            finds.append(_f(2, "plan_filesort", "Sorting without an index (Using filesort)", "Bina index ke sorting (Using filesort)", what,
                            "The rows are sorted after they are found; an index on the WHERE's '=' columns then the ORDER BY columns "
                            "returns them in order.", "Rows milne ke baad sort hoti hain; '=' columns phir ORDER BY columns ka index lagao."))
    return {"engine": "mysql", "analyzed": any(m.group("arows") for m in lines), "steps": steps, "findings": _dedupe(finds)}


# ------------------------------------------------------------------------------------------------ entry
def _f(sev, topic, ten, thi, ev, fen, fhi) -> dict:
    return {"severity": sev, "topic": topic, "title_en": ten, "title_hi": thi, "evidence": ev, "fix_en": fen, "fix_hi": fhi}


def _dedupe(fs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in fs:
        if f["title_en"] in seen:
            continue
        seen.add(f["title_en"])
        out.append(f)
    return out


def looks_like_plan(text: str) -> bool:
    t = text or ""
    return bool(re.search(r"select_type", t, re.I) and re.search(r"possible_keys|\btype\b", t, re.I) or
                re.search(r"\((?:cost=[\d.]+\.\.[\d.]+ rows=\d+|actual time=)", t) or
                re.search(r"^\s*->\s+(Table scan|Index lookup|Index range scan|Filter:|Nested loop)", t, re.M))


def read(text: str) -> dict | None:
    """The plan in the text (MySQL/MariaDB table or \\G, MySQL tree, PostgreSQL), read: {engine, analyzed, steps, findings}."""
    if not looks_like_plan(text):
        return None
    rows = _my_rows(text)
    if rows:
        out = _read_mysql(rows)
    else:
        out = _read_tree(text) or _read_pg(text)
    if not out or not out["steps"]:
        return None
    out["findings"] = _dedupe(out["findings"])
    return out


def render(plan: dict, hi: bool = False) -> str:
    """'How DBaba read the plan': the steps in the order they run, then what to change."""
    eng = "MySQL/MariaDB" if plan["engine"] == "mysql" else "PostgreSQL"
    if hi:
        head = (f"Ye {eng} ka plan hai (format se pata chala). " + ("ANALYZE ke saath: asli row counts hain." if plan["analyzed"] else
                                                                    "Sirf EXPLAIN: row counts andaaza hain, asli nahi (asli ke liye EXPLAIN ANALYZE)."))
    else:
        head = (f"This is a {eng} plan (its format shows it). " + ("It was run (ANALYZE), so the row counts are real." if plan["analyzed"] else
                                                                    "Plain EXPLAIN: the row counts are estimates, not what happened (EXPLAIN ANALYZE runs it)."))
    lines = [head, "", "Steps, in the order they run:" if not hi else "Steps, jis kram mein chalte hain:"]
    for i, st in enumerate(plan["steps"], 1):
        lines.append(f"{i}. {st['hi' if hi else 'en']}")
    if plan.get("total_ms") is not None:
        lines.append(("" if not hi else "") + (f"Total: {plan['total_ms']:g} ms."))
    fs = sorted(plan["findings"], key=lambda f: -f["severity"])
    if fs:
        lines += ["", "Kya badalna hai:" if hi else "What to change:"]
        for f in fs[:6]:
            lines.append(f"- {f['title_hi' if hi else 'title_en']}: {f['fix_hi' if hi else 'fix_en']}")
    else:
        lines += ["", "Is plan mein koi dheema step nahi dikha: har table index se padhi ja rahi hai ya chhoti hai." if hi else
                  "Nothing slow shows in this plan: every table is read through an index or is small."]
    return "\n".join(lines)
