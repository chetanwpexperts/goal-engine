"""Locks, concurrency and large data (night 9 of the curriculum), shared by every database (fundamental 'locking' in
app/dbknow/core/query_fundamentals.json; engine facts in the dialect files' query_rules, rule B).

A change locks the rows it changes until its transaction ends, and a reader that wants the same rows for writing waits. So:
- one huge UPDATE / DELETE holds its locks (and its undo / WAL) for the whole run: other writers wait or time out (MariaDB 1205,
  PostgreSQL 55P03) and replicas fall behind. Done in batches of a few thousand rows, each its own short transaction, the
  final table is the same. Words that say the table is big ('millions of rows', 'lakhs', 'huge table', 'times out') make DBaba
  offer the batched form in the database's own words: LIMIT on MySQL/MariaDB, a ctid subquery on PostgreSQL. An UPDATE is
  batched only when it sets plain values, with a guard so the rows already changed stop matching (else the loop never ends)
  -> 'check', offered.
- MySQL/MariaDB (InnoDB) lock every row their search READS, not only the rows that match: an UPDATE / DELETE / SELECT ... FOR
  UPDATE whose WHERE has no usable index locks the whole table for the transaction (lab: a second session updating another
  row waited and got 1205). PostgreSQL locks only the rows it changes. Said when the table's structure is pasted (lock_fit).
- workers that take 'the next job' with SELECT ... FOR UPDATE all queue behind the first one (lab: the second waited 5 s and
  got 1205 / 55P03); FOR UPDATE SKIP LOCKED lets each take a different free row (lab: the second got job 2 at once) -> 'check'
  when the words say queue / worker / job, offered.
- the same statement sent once per id (an N+1 loop, seen as a log of near-identical queries) costs a round trip each; one
  statement with IN (...) (or one multi-row INSERT) does the same work -> 'check', the combined statement given.
- a deadlock is two transactions each holding a row the other wants (lab: MariaDB 1213 / SQLSTATE 40001 and PostgreSQL 40P01;
  one of them is rolled back). read_deadlock() reads MariaDB/MySQL's LATEST DETECTED DEADLOCK and PostgreSQL's DETAIL and says
  which rows were taken in which order, and the fix: the same order everywhere, short transactions, retry the victim.
Returns [(Finding, [edits])], edit = (start, end, new text) on the original query.
"""

import re

PROVEN = {k: ["mariadb", "postgresql"] for k in ("batch_change", "lock_scan", "skip_locked", "n_plus_one", "deadlock_order")}
BATCH = 5000

BIG_SAID = re.compile(r"\b(millions?|lakhs?|crores?|billions?|\d+\s*(?:m|k|lakh|million|crore)\s+rows|huge|very\s+(?:big|large)|"
                      r"big\s+table|large\s+table|bahut\s+(?:badi|bada|saari|sari|zyada)|badi\s+table|times?\s+out|timeout|"
                      r"timed\s+out|lock(?:s|ed|ing)?\b|hangs?|stuck|replica(?:tion)?\s+lag|binlog|undo|takes?\s+(?:hours|forever|too\s+long))",
                      re.I)
QUEUE_SAID = re.compile(r"\b(queue|jobs?|workers?|tasks?|consumers?|pick(?:s|ing)?\s+(?:the\s+)?next|next\s+(?:job|task|order|item|row)|"
                        r"several\s+(?:workers|processes|servers)|parallel|concurrent\w*|at\s+the\s+same\s+time|ek\s+saath|dono\s+worker)", re.I)
LIT = r"(?:'(?:[^'\\]|\\.|'')*'|-?\d+(?:\.\d+)?|null|true|false)"
NAME = r"[`\"]?[A-Za-z_][\w$]*[`\"]?(?:\.[`\"]?[A-Za-z_][\w$]*[`\"]?)?"


def _C():
    from app.agents import sqlcorrect as C

    return C


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def _short(t: str, n: int = 90) -> str:
    t = _flat(t)
    return t if len(t) <= n else t[:n - 3] + "..."


def _engine(sql: str):
    from app.agents import sqlbasics

    return sqlbasics.engine_seen(sql)


def _says(key: str, eng) -> str:
    from app.agents import sqlbasics

    if eng in ("mysql", "postgresql"):
        return sqlbasics.rules(eng).get(key, {}).get("says", "")
    return f"{sqlbasics.rules('mysql').get(key, {}).get('says', '')} {sqlbasics.rules('postgresql').get(key, {}).get('says', '')}".strip()


# ------------------------------------------------------------------------------------------------ one big change -> batches
_DELETE = re.compile(r"^\s*delete\s+from\s+(" + NAME + r")(?:\s+(?:as\s+)?([A-Za-z_]\w*))?\s+where\s+(.+?)\s*;?\s*$", re.I | re.S)
_UPDATE = re.compile(r"^\s*update\s+(" + NAME + r")(?:\s+(?:as\s+)?([A-Za-z_]\w*))?\s+set\s+(.+?)\s+where\s+(.+?)\s*;?\s*$", re.I | re.S)


def batched(sql: str, eng) -> dict | None:
    """{'sql': the batched statement, 'kind': 'delete'|'update'} for a single-table UPDATE / DELETE, in the engine's words."""
    C = _C()
    s = C._mask(sql)
    if re.search(r"\b(join|using|order\s+by|limit|returning)\b|^\s*update\b[^;]*?\bfrom\b", C._no_subqueries(s), re.I):
        return None                                          # a join, or already limited: not a plain one-table change
    m = _DELETE.match(s)
    kind = "delete"
    if not m:
        m = _UPDATE.match(s)
        kind = "update"
    if not m or m.group(2) and m.group(2).lower() in ("where", "set"):
        return None
    table = sql[m.start(1):m.end(1)]
    alias = m.group(2)
    if kind == "delete":
        cond = sql[m.start(3):m.end(3)].strip().rstrip(";").strip()
        guard = ""
        head = f"DELETE FROM {table}{' ' + alias if alias else ''}"
    else:
        sets = sql[m.start(3):m.end(3)]
        cond = sql[m.start(4):m.end(4)].strip().rstrip(";").strip()
        guards = []
        for _o, part in C._split_top(sets):
            am = re.fullmatch(r"\s*(" + NAME + r")\s*=\s*(" + LIT + r")\s*", part, re.I | re.S)
            if not am:
                return None                                  # SET x = x + 1: each run changes it again, batches can't tell what's done
            col, val = am.group(1), am.group(2)
            guards.append(f"{col} IS NOT NULL" if val.lower() == "null" else f"({col} <> {val} OR {col} IS NULL)")
        guard = guards[0] if len(guards) == 1 else "(" + " OR ".join(guards) + ")"   # a row is done when every column has its value
        head = f"UPDATE {table}{' ' + alias if alias else ''} SET {_flat(sets)}"
    where = f"({cond})" + (f" AND {guard}" if guard else "")
    if eng == "postgresql":
        inner = f"SELECT ctid FROM {table}{' ' + alias if alias else ''} WHERE {where} LIMIT {BATCH}"
        if kind == "delete":
            new = f"DELETE FROM {table} WHERE ctid IN ({inner})"
        else:
            new = f"UPDATE {table} SET {_flat(sets)} WHERE ctid IN ({inner})"
    else:
        new = f"{head} WHERE {where} LIMIT {BATCH}"
    return {"sql": new, "kind": kind}


def _batch_change(sql: str, s: str, intent: str, F, eng) -> list:
    if not BIG_SAID.search(intent or "") or not re.match(r"\s*(delete|update)\b", s, re.I):
        return []
    my, pg = batched(sql, "mysql"), batched(sql, "postgresql")
    if not my:
        if re.match(r"\s*update\b", s, re.I) and re.search(r"\bset\b", s, re.I):
            return [(F("check", "batch_change", "One huge UPDATE holds its locks until it ends: batch it by key range",
                       "Ek badi UPDATE khatam hone tak locks rakhti hai: key range se tukdon mein karo", _short(sql, 80),
                       "A single UPDATE over many rows keeps every changed row locked until it commits, so other writers wait or time "
                       "out (MariaDB 1205, PostgreSQL 55P03), and its undo / WAL grows. This SET changes the value again each time it "
                       "runs (x = x + ...), so a 'repeat until 0 rows' loop would never end: batch it by key range instead "
                       "(... AND id >= :from AND id < :to, moving the range each time, each batch committed on its own).",
                       "Ek UPDATE bahut rows pe saari changed rows ko commit tak lock rakhti hai. Ye SET har baar value phir badalta hai, "
                       "isliye key range se tukde karo (id >= :from AND id < :to), har tukda alag commit."), [])]
        return []
    pick = pg if eng == "postgresql" else my
    both = "" if eng in ("mysql", "postgresql") else (f" On PostgreSQL (no LIMIT in UPDATE/DELETE): {pg['sql']}" if pg else "")
    what = "DELETE" if my["kind"] == "delete" else "UPDATE"
    guard_en = (" The added condition skips rows that already have the new value, so each run takes rows not yet done and the "
                "loop ends." if what == "UPDATE" else "")
    return [(F("check", "batch_change", f"One huge {what} holds its locks until it ends: run it in batches of {BATCH:,}",
               f"Ek badi {what} khatam hone tak locks rakhti hai: {BATCH:,} rows ke tukdon mein chalao", _short(sql, 80),
               f"A single {what} over a big table keeps every row it changes locked until it commits, so other writers wait or time "
               f"out (lab: MariaDB 1205, PostgreSQL 55P03), and its undo log / WAL and the replicas' lag grow with it. Run the batched "
               f"form again and again, each run its own short transaction (autocommit), until it changes 0 rows: the final table is "
               f"exactly what the single statement makes (proven on MariaDB and PostgreSQL), and other sessions get the rows in "
               f"between.{guard_en}{both} An index on the WHERE's column keeps each batch from re-reading the table.",
               f"Badi table pe ek {what} commit tak har badli row lock rakhti hai, isliye doosre writers rukte ya timeout hote hain. "
               f"Tukdon wala roop baar baar chalao, har baar chhota transaction, jab tak 0 rows na badle: aakhri table bilkul wahi "
               f"banti hai, aur beech mein doosre sessions ko rows milti rehti hain."), [(0, len(sql.rstrip().rstrip(";").rstrip()), pick["sql"])])]


# ------------------------------------------------------------------------------------------------ queue workers -> SKIP LOCKED
def _skip_locked(sql: str, s: str, intent: str, F, eng) -> list:
    m = re.search(r"\bfor\s+(?:update|share|no\s+key\s+update)\b(?!\s+(?:of\s+\S+\s+)?(?:skip\s+locked|nowait))", s, re.I)
    if not m or not QUEUE_SAID.search(intent or "") or not re.search(r"\blimit\s+\d+", s, re.I):
        return []
    end = m.end()
    of = re.match(r"\s+of\s+[\w\s,]+?(?=\s*(?:;|$))", s[end:], re.I)
    if of:
        end += of.end()
    new = sql[:end] + " SKIP LOCKED" + sql[end:]
    return [(F("check", "skip_locked", "Workers taking the next job with FOR UPDATE wait for each other",
               "FOR UPDATE se agla job lene wale workers ek doosre ka intezaar karte hain", _short(sql[m.start():m.end()], 40),
               "FOR UPDATE locks the row the first worker took, and every other worker asking for 'the next one' reads that same row "
               "first and waits for it (lab: the second worker waited until the lock timeout, MariaDB 1205 / PostgreSQL 55P03). With "
               "FOR UPDATE SKIP LOCKED each worker skips rows another one holds and takes the next free row at once (lab: job 2 "
               "straight away). With no other worker running it returns exactly the same rows. " + _says("skip_locked", eng),
               "FOR UPDATE pehle worker ki row lock karta hai, aur baaki worker wahi row padh ke rukte hain. FOR UPDATE SKIP LOCKED se "
               "har worker locked row chhod ke agli free row turant leta hai; akele chalne pe rows wahi aati hain."),
             [(0, len(sql.rstrip().rstrip(";").rstrip()), new.rstrip().rstrip(";").rstrip())])]


# ------------------------------------------------------------------------------------------------ entry (query alone)
def locks(sql: str, s: str, intent: str, F, is_select: bool) -> list:
    eng = _engine(sql)
    out = []
    if not is_select:
        out += _batch_change(sql, s, intent, F, eng)
    out += _skip_locked(sql, s, intent, F, eng)
    return out


# ------------------------------------------------------------------------------------------------ N+1: the same statement per id
def _statements(text: str) -> list[str]:
    C = _C()
    s = C._mask(text)
    out, last = [], 0
    for i, ch in enumerate(s):
        if ch == ";":
            out.append(text[last:i].strip())
            last = i + 1
    out.append(text[last:].strip())
    return [x for x in out if x]


def repeated(text: str) -> dict | None:
    """Three or more statements that differ only in one value (a log of an N+1 loop): {'sql': one statement doing the same work,
    'count': n, 'kind': 'select'|'update'|'delete'|'insert', 'column': the key column or None}."""
    C = _C()
    stmts = _statements(text)
    if len(stmts) < 3:
        return None
    lit = re.compile(LIT, re.I)
    shapes = []
    for st in stmts:
        s = C._mask(st)
        if not re.match(r"\s*(select|update|delete|insert)\b", s, re.I):
            return None
        vals = [(m.start(), m.end()) for m in lit.finditer(s) if not re.match(r"[\w.]", s[m.start() - 1:m.start()] or " ")]
        skel = s
        for a, b in reversed(vals):
            skel = skel[:a] + "?" + skel[b:]
        shapes.append((re.sub(r"\s+", " ", skel).strip().lower(), [st[a:b] for a, b in vals], st))
    if len({sh for sh, _v, _s in shapes}) != 1:
        return None
    vals = [v for _sh, v, _s in shapes]
    diff = [k for k in range(len(vals[0])) if len({tuple([v[k]]) for v in vals}) > 1]
    first = shapes[0][2]
    s0 = C._mask(first)
    kind = s0.lstrip().split()[0].lower()
    if kind == "insert":
        vm = re.search(r"\bvalues\s*(\()", s0, re.I)
        if not vm or re.search(r"\b(select|on\s+duplicate|on\s+conflict|returning)\b", s0, re.I):
            return None
        rows = []
        for _sh, _v, st in shapes:
            sm = C._mask(st)
            a = re.search(r"\bvalues\s*(\()", sm, re.I).start(1)
            b = C._close(sm, a)
            if sm[b + 1:].strip():
                return None
            rows.append(st[a:b + 1])
        return {"sql": first[:vm.start(1)].rstrip() + "\n" + ",\n".join(rows), "count": len(stmts), "kind": "insert", "column": None}
    if len(diff) != 1:
        return None
    k = diff[0]
    lits = list(dict.fromkeys(v[k] for v in vals))
    lm = list(m for m in lit.finditer(s0) if not re.match(r"[\w.]", s0[m.start() - 1:m.start()] or " "))[k]
    before = s0[:lm.start()]
    cm = re.search(r"(" + NAME + r")\s*=\s*$", before)
    if not cm or not re.search(r"\bwhere\b", before, re.I) or re.search(r"\b(limit|group\s+by|having|union|order\s+by)\b", s0[lm.end():], re.I):
        return None
    if re.search(r"\b(or|not)\b", s0[re.search(r"\bwhere\b", before, re.I).end():], re.I):
        return None                                             # the value inside an OR: IN would change what the others mean
    col = first[cm.start(1):cm.end(1)]
    new = first[:cm.start(1)] + f"{col} IN ({', '.join(lits)})" + first[lm.end():]
    if kind == "select":
        sel = C._clauses(s0, C._depths(s0))
        a, b = sel["select"]
        items = [x.strip() for _o, x in C._split_top(first[a:b])]
        bare = col.split(".")[-1].strip('`"').lower()
        if not any(x == "*" or x.endswith(".*") or re.split(r"[.\s]", x.strip('`"'))[-1].strip('`"').lower() == bare for x in items):
            new = new[:a] + f" {col}," + new[a:]                 # the app needs to know which row belongs to which id
    return {"sql": new.strip().rstrip(";"), "count": len(stmts), "kind": kind, "column": col}


def n_plus_one(text: str) -> dict | None:
    r = repeated(text)
    if not r:
        return None
    n = r["count"]
    if r["kind"] == "insert":
        ten, thi = f"{n} single-row INSERTs: one INSERT with {n} rows does the same", f"{n} alag INSERT: ek INSERT mein {n} rows wahi karta hai"
        how_en = "one INSERT with several VALUES rows (in batches of a few hundred to a few thousand rows)"
        how_hi = "ek INSERT mein kai VALUES rows"
    else:
        ten = f"The same {r['kind'].upper()} sent {n} times, once per value (the N+1 pattern)"
        thi = f"Ek hi {r['kind'].upper()} {n} baar, har value ke liye alag (N+1 pattern)"
        how_en = f"one statement with {r['column']} IN (...)"
        how_hi = f"{r['column']} IN (...) wala ek statement"
    return {"severity": 3, "kind": "check", "topic": "n_plus_one", "title_en": ten, "title_hi": thi, "evidence": f"{n} statements",
            "fix_en": (f"Each statement is a round trip to the database (network, parsing, its own transaction in autocommit), so {n} of "
                       f"them cost far more than the work itself; in a loop over a list (one query for the list, then one per row: "
                       f"'N+1') it grows with the data. {how_en[0].upper() + how_en[1:]} does the same work in one trip and returns "
                       f"(or changes) exactly the same rows (proven on MariaDB and PostgreSQL)" +
                       (f"; {r['column']} is added to the SELECT so the app can tell which row belongs to which value." if
                        r["kind"] == "select" else ".") + " In app code: collect the ids first, then send one query (or a JOIN)."),
            "fix_hi": (f"Har statement database tak ek chakkar hai, isliye {n} chakkar asli kaam se zyada mehenge. {how_hi} ek hi "
                       f"chakkar mein wahi rows deta/badalta hai. Code mein: pehle saari ids ikattha karo, phir ek query (ya JOIN)."),
            "sql": r["sql"]}


# ------------------------------------------------------------------------------------------------ with the table structure
def lock_fit(query: str, extra: str, eng=None) -> list[dict]:
    """With the structure pasted: an UPDATE / DELETE / SELECT ... FOR UPDATE whose WHERE has no usable index. MySQL/MariaDB lock
    every row the search reads (the whole table); PostgreSQL only reads it all."""
    from app.agents import sqlspeed
    from app.agents.sql import _strip_comments

    st = sqlspeed.structure(extra)
    q = _strip_comments(query or "")
    C = _C()
    s = C._mask(q)
    if not st or not re.search(r"^\s*(update|delete)\b|\bfor\s+(update|share)\b", s, re.I):
        return []
    tm = re.match(r"\s*(?:update|delete\s+from)\s+[`\"]?(?:\w+\.)?([\w$]+)", s, re.I) or \
        re.search(r"\bfrom\s+[`\"]?(?:\w+\.)?([\w$]+)", s, re.I)
    if not tm or tm.group(1).lower() not in st:
        return []
    t = tm.group(1).lower()
    info = st[t]
    wm = re.search(r"\bwhere\b(.+?)(?:\border\s+by\b|\blimit\b|\bfor\s+(?:update|share)\b|$)", s, re.I | re.S)
    if not wm or re.search(r"\(\s*select\b|\bor\b", wm.group(1), re.I):
        return []
    cols = [c.split(".")[-1].strip('`"').lower() for c in re.findall(r"(" + NAME + r")\s*(?:=|<=>|<=|>=|<|>|\bin\b|\bbetween\b|\blike\s+'[^%])",
                                                                        wm.group(1), re.I)]
    cols = [c for c in cols if c in info["cols"]]
    if not cols or any(ix and ix[0] in cols for ix in info["indexes"]):
        return []
    what = "UPDATE" if re.match(r"\s*update", s, re.I) else "DELETE" if re.match(r"\s*delete", s, re.I) else "SELECT ... FOR UPDATE"
    pg = eng == "postgresql"
    return [{"severity": 2 if pg else 3, "topic": "lock_scan",
             "title_en": (f"{what} reads all of {t}: no index on {', '.join(dict.fromkeys(cols))}" if pg else
                          f"{what} locks every row of {t} it reads: no index on {', '.join(dict.fromkeys(cols))}"),
             "title_hi": f"{what} {t} ki saari rows padhta/lock karta hai: {', '.join(dict.fromkeys(cols))} pe index nahi",
             "evidence": f"WHERE {_short(q[wm.start(1):wm.end(1)], 60)}",
             "fix_en": (_says("lock_scan", eng) + f" Add an index on {cols[0]} (CREATE INDEX ... ON {t} ({cols[0]})): then only the "
                        f"matching rows are read" + ("" if pg else " and locked, and other sessions working on other rows don't wait") + "."),
             "fix_hi": (f"MySQL/MariaDB (InnoDB) search mein padhi har row lock karte hain: bina index ke poori table lock, doosre sessions "
                        f"doosri rows pe bhi rukte hain (lab: 1205). PostgreSQL sirf badalne wali rows lock karta hai. {cols[0]} pe index "
                        f"lagao: sirf matching rows padhi aur lock hongi.")}]


# ------------------------------------------------------------------------------------------------ reading a deadlock report
def read_deadlock(text: str) -> dict | None:
    """MariaDB/MySQL 'LATEST DETECTED DEADLOCK' (SHOW ENGINE INNODB STATUS) or PostgreSQL's deadlock DETAIL: who held what, who
    wanted what, and the fix. {'engine', 'en', 'hi', 'statements', 'tables'}."""
    t = text or ""
    if "LATEST DETECTED DEADLOCK" in t or re.search(r"\*\*\* \(1\) TRANSACTION", t):
        sec = t[t.find("LATEST DETECTED DEADLOCK"):] if "LATEST DETECTED DEADLOCK" in t else t
        parts = re.split(r"\*\*\* \(\d\) TRANSACTION:", sec)[1:3]
        stmts, tables, gap, victim = [], [], False, None
        for p in parts:
            sm = re.search(r"^\s*((?:UPDATE|DELETE|INSERT|SELECT|REPLACE)\b[^\n]*)", p, re.M | re.I)
            stmts.append(_flat(sm.group(1)) if sm else "?")
            for tb in re.findall(r"of table `([^`]+)`\.`([^`]+)`", p):
                tables.append(tb[1])
            if re.search(r"lock_mode X(?! locks rec but not gap)|locks gap before rec|insert intention", p):
                gap = True
        vm = re.search(r"WE ROLL BACK TRANSACTION \((\d)\)", sec)
        victim = vm.group(1) if vm else None
        eng = "mysql"
    elif re.search(r"deadlock detected|40P01", t, re.I) and re.search(r"Process \d+ waits for \w+Lock on transaction \d+; blocked by process \d+", t):
        stmts = [_flat(x) for x in re.findall(r"Process \d+: ([^\n]+)", t)][:2] or ["?", "?"]
        tables = re.findall(r'relation "([^"]+)"', t)
        gap, victim, eng = False, None, "postgresql"
    else:
        return None
    tables = list(dict.fromkeys(tables)) or ["(the table)"]
    one = stmts[0] if stmts else "?"
    two = stmts[1] if len(stmts) > 1 else "?"
    en = [f"Deadlock on {', '.join(tables)}: transaction 1 held a row lock that transaction 2 needed, while waiting for a row "
          f"transaction 2 held. Neither could go on, so the database rolled one of them back"
          + (f" (transaction {victim})" if victim else "") + " (MariaDB/MySQL error 1213, PostgreSQL 40P01).",
          f"Waiting when it happened: (1) {_short(one, 120)}; (2) {_short(two, 120)}.",
          "Fix: make every transaction that changes these rows take them in the same order (for example ORDER BY id in "
          "SELECT ... FOR UPDATE, or update the lower id first); keep transactions short (no user waits or slow calls inside); "
          "give the WHERE an index so fewer rows are locked; and let the app retry a transaction that fails with 1213 / 40P01 "
          "(it was rolled back completely, so retrying is safe)."]
    if gap:
        en.append("The lock modes show gap / next-key locks: under REPEATABLE READ InnoDB also locks the gaps between rows it "
                  "searched (lab: an INSERT into a range another transaction updated waited and got 1205). READ COMMITTED takes far "
                  "fewer gap locks, if the application doesn't need repeatable reads.")
    hi = [f"{', '.join(tables)} pe deadlock: transaction 1 ke paas wo row thi jo transaction 2 ko chahiye thi, aur wo khud "
          "transaction 2 wali row ka intezaar kar raha tha. Dono ruk gaye, isliye database ne ek ko rollback kiya (1213 / 40P01).",
          f"Us waqt: (1) {_short(one, 120)}; (2) {_short(two, 120)}.",
          "Fix: in rows ko badalne wala har transaction unhe ek hi kram mein le (jaise ORDER BY id ke saath FOR UPDATE, ya pehle "
          "chhoti id); transaction chhote rakho; WHERE pe index; aur 1213 / 40P01 aane pe app transaction dobara chalaye (wo poora "
          "rollback hua tha, isliye dobara chalana safe hai)."]
    return {"engine": eng, "en": "\n".join(en), "hi": "\n".join(hi), "statements": stmts, "tables": tables, "gap": gap}
