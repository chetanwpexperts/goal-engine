"""Transaction scripts (E1 night 4; fundamental 'transactions' in app/dbknow/core/query_fundamentals.json).

A pasted script with BEGIN / START TRANSACTION / COMMIT / ROLLBACK / SAVEPOINT (or several changes with none of them) is read
the way a careful person reads it: statement by statement, keeping track of whether a transaction is open, what it has read
and written so far, and which savepoints exist. Every database agrees on the idea (a transaction is all-or-nothing, and a
change is kept or undone only at its end), but each one reacts differently to the same slips, so what happens is read from the
database's own dialect file ('txn_rules', rule B), each rule with its lab lines:
- a second BEGIN while one is open: SQLite refuses it; MySQL/MariaDB silently COMMIT the first part (a ROLLBACK later undoes
  only the rest); PostgreSQL ignores it with a warning (one ROLLBACK undoes both parts). What was meant is a SAVEPOINT;
- COMMIT / ROLLBACK with no transaction open: every statement before it was already saved on its own (autocommit), so the
  ROLLBACK undoes nothing (SQLite says 'cannot rollback - no transaction is active', PostgreSQL warns, MySQL says nothing);
- CREATE / ALTER / DROP / TRUNCATE / RENAME / LOCK TABLES inside a transaction on MySQL/MariaDB commit it first (even when
  they then fail); PostgreSQL and SQLite keep them inside the transaction;
- statements that can't run inside a transaction (VACUUM, CREATE INDEX CONCURRENTLY, CREATE DATABASE, a journal_mode change);
- RELEASE / ROLLBACK TO a savepoint that was never made (or, on PostgreSQL, a SAVEPOINT outside a transaction);
- SET TRANSACTION in the wrong place: MySQL/MariaDB want it before START TRANSACTION (1568 inside), PostgreSQL right after
  BEGIN and before any query (outside a block it changes nothing);
- a script that ends with its transaction still open (or with ROLLBACK TO, which keeps it open): its locks stay held (on
  SQLite, the whole database stays locked for other writers);
- SQLite only: a plain (DEFERRED) BEGIN that reads first and writes later; if another connection writes in between, the
  write fails at once with 'database is locked', and a busy timeout does not help (lab); BEGIN IMMEDIATE takes the write
  lock at the start, where waiting works;
- several changes with no transaction at all: if one fails, the ones before it stay (lab, on all three), and on SQLite each
  one is its own disk sync (500 single INSERTs took about 250x longer than the same in one transaction).
What depends on the client (a driver that opens transactions itself, autocommit turned off) is a 'check'. Every statement
inside the script is also checked on its own by the usual checker. Returns {'findings', 'corrected', 'guesses'}.
"""

import difflib
import re

from app.dbknow import engines as E

CONTROL = ("begin", "commit", "rollback", "rollback_to", "savepoint", "release", "set_txn", "autocommit", "lock", "unlock", "setting")
NAME = r"[`\"]?([A-Za-z_][\w$]*)[`\"]?"


def _mask(sql: str) -> str:
    from app.agents.sqlcorrect import _mask as m

    return m(sql)


def classify(stmt: str) -> tuple[str, dict]:
    """What one statement does to the transaction: (kind, details)."""
    s = re.sub(r"\s+", " ", _mask(stmt)).strip().rstrip(";").strip()
    low = s.lower()
    m = re.match(r"^(?:begin|start)\b(?:\s+(deferred|immediate|exclusive))?(?:\s+(transaction|tran|work))?\b(.*)$", low)
    if m and not re.match(r"^begin\s+(?:declare|select|insert|update|delete|if|set)\b", low) and \
            (low.startswith("begin") or re.match(r"^start\s+transaction\b", low)):
        return "begin", {"mode": m.group(1) or ("start" if low.startswith("start") else "deferred"), "word": m.group(2) or "",
                         "rest": m.group(3).strip()}
    m = re.match(rf"^rollback(?:\s+(?:transaction|tran|work))?\s+to\s+(?:savepoint\s+)?{NAME}$", s, re.I)
    if m:
        return "rollback_to", {"name": m.group(1)}
    if re.match(r"^(?:commit|end)(?:\s+(?:transaction|tran|work))?$", low):
        return "commit", {}
    if re.match(r"^rollback(?:\s+(?:transaction|tran|work))?$", low) or low == "abort":
        return "rollback", {}
    m = re.match(rf"^savepoint\s+{NAME}$", s, re.I)
    if m:
        return "savepoint", {"name": m.group(1)}
    m = re.match(rf"^release(?:\s+savepoint)?\s+{NAME}$", s, re.I)
    if m:
        return "release", {"name": m.group(1)}
    m = re.match(r"^set\s+(session\s+|global\s+|local\s+)?transaction\b", low)
    if m:
        return "set_txn", {"scope": (m.group(1) or "").strip()}
    m = re.match(r"^set\s+(?:@@)?autocommit\s*(?:=|to)?\s*(0|1|off|on|false|true)$", low)
    if m:
        return "autocommit", {"on": m.group(1) in ("1", "on", "true")}
    if re.match(r"^set\s+(?:session\s+|global\s+|local\s+)?(?:innodb_lock_wait_timeout|lock_wait_timeout|lock_timeout|lock_timeout)\b|^set\s+lock_timeout\b", low):
        return "setting", {}                          # a lock-wait setting: another database's words on SQLite
    if re.match(r"^lock\s+tables?\b", low):
        return "lock", {}
    if re.match(r"^unlock\s+tables\b", low):
        return "unlock", {}
    m = re.match(r"^pragma\s+(?:\w+\.)?(\w+)\s*(=|\()?", low)
    if m:
        return "pragma", {"name": m.group(1), "sets": bool(m.group(2))}
    if re.match(r"^vacuum\b", low):
        return "vacuum", {}
    if re.match(r"^(create|alter|drop|truncate|rename|reindex|analyze|optimize|grant|revoke)\b", low):
        return "ddl", {}
    if re.match(r"^(insert|update|delete|replace|merge|upsert)\b", low) or \
            (low.startswith("with") and re.search(r"\)\s*(insert|update|delete)\b", low)):
        return "write", {"table": _target(s)}
    if re.match(r"^[\s(]*(select|with|values|table|explain|show|describe|desc)\b", low):
        return "read", {}
    return "other", {}


def _target(s: str) -> str:
    m = re.search(r"\b(?:insert\s+(?:or\s+\w+\s+)?into|replace\s+into|update(?:\s+or\s+\w+)?|delete\s+from|merge\s+into)\s+[`\"]?([\w$.]+)",
                  s, re.I)
    return (m.group(1).lower() if m else "")


def statements(sql: str) -> list[dict]:
    from app.agents.sqlroutine import split_script

    out = []
    for t in split_script(sql):
        m = _mask(t)
        cut = len(m) - len(m.lstrip())                # a comment before the statement is kept, but read past
        k, info = classify(t)
        out.append({"text": t[cut:], "lead": t[:cut], "kind": k, **info})
    return out


def is_script(sql: str) -> bool:
    """A transaction script: any transaction-control statement (BEGIN, COMMIT, ROLLBACK, SAVEPOINT, SET TRANSACTION, LOCK
    TABLES ...), or two or more changes on different tables with none (all-or-nothing is the question then). Routines
    (CREATE PROCEDURE ... BEGIN ... END) are not scripts."""
    from app.agents import sqlroutine

    if not sql or not re.search(r"\b(begin|start|commit|rollback|savepoint|release|end|abort|transaction|autocommit|lock|unlock|"
                                r"insert|update|delete|replace|\w*timeout)\b", sql, re.I):
        return False
    if re.search(r"\bcreate\s+(?:or\s+replace\s+)?(?:definer\s*=\s*\S+\s+)?(procedure|function|trigger|event)\b", _mask(sql), re.I) or \
            re.match(r"^\s*do\s", sql, re.I):
        return False
    st = statements(sql)
    if any(x["kind"] in CONTROL for x in st):
        return not (len(st) == 1 and st[0]["kind"] == "begin" and sqlroutine.is_routine(sql) and "end" in sql.lower())
    writes = [x for x in st if x["kind"] == "write"]
    return len(st) >= 2 and len(writes) >= 2 and len({x["table"] for x in writes}) >= 2 and \
        all(x["kind"] in ("read", "write") for x in st)


def has_control(sql: str) -> bool:
    return any(x["kind"] in CONTROL for x in statements(sql))


# ------------------------------------------------------------------------------------------------ what each database does
def _fams() -> list[str]:
    """The dialect files whose txn_rules answer: the named / chosen database's family (rule A); MySQL/MariaDB and PostgreSQL,
    labelled, when not sure; none for a notes-only database (general reasoning only, said so by the notes line)."""
    from app.agents.sqlbasics import CHOSEN

    eng = CHOSEN.get()
    if eng in ("mysql", "mariadb"):
        return ["mysql"]
    if eng in ("postgresql", "sqlite"):
        return [eng]
    if eng in (None, E.NOT_SURE):
        return ["mysql", "postgresql"]
    return []


def _rule(fam: str, rid: str) -> dict | None:
    r = (E.dialect(fam).get("txn_rules") or {}).get(rid)
    return r if r and r.get("kind") != "none" else None


LABEL = {"mysql": "MySQL/MariaDB", "postgresql": "PostgreSQL", "sqlite": "SQLite"}
ORDER = {"error": 0, "wrong": 1, "check": 2}
GENERAL = {   # true on every database (general reasoning): said this way for a database without its own lab-proven rules
    "left_open": ("The transaction is never ended: it stays open, holding what it locked, until a COMMIT or ROLLBACK (or the "
                  "connection closes, which rolls it back).",
                  "Transaction kabhi khatam nahi hoti: COMMIT ya ROLLBACK tak (ya connection band hone tak, jo rollback karta hai) "
                  "khuli rehti hai aur apne locks pakde rehti hai."),
    "no_txn_writes": ("With no transaction each change is saved on its own as it runs: if a later one fails, the earlier ones "
                      "stay.", "Transaction ke bina har badlav chalte hi apne aap save hota hai: baad wala fail ho to pehle wale reh jaate hain."),
    "end_without_begin": ("No transaction was started, so each statement before it was saved on its own; this COMMIT / ROLLBACK "
                          "has nothing to end (a ROLLBACK undoes nothing) unless the client turned autocommit off.",
                          "Transaction shuru hi nahi hui, isliye pehle ke statements apne aap save ho gaye; ye ROLLBACK kuch "
                          "wapas nahi karta (jab tak client ne autocommit band na kiya ho)."),
}


def _says(rid: str, fams: list[str], hi: bool) -> tuple[str, str] | None:
    """(kind, text) for this rule on these databases; None when it holds on none of them."""
    got = [(f, _rule(f, rid)) for f in fams]
    got = [(f, r) for f, r in got if r]
    if not got:
        if not fams and rid in GENERAL:
            return "check", GENERAL[rid][1 if hi else 0]
        return None
    kind = min((r["kind"] for _f, r in got), key=ORDER.get)
    key = "says_hi" if hi else "says"
    if len(fams) == 1:
        return kind, got[0][1][key]
    return kind, " ".join(f"{LABEL[f]}: {r[key]}" for f, r in got)


# ------------------------------------------------------------------------------------------------ the reading
def check(sql: str, intent: str, F) -> dict:
    st = statements(sql)
    fams = _fams()
    fam = fams[0] if len(fams) == 1 else None
    found: list[tuple] = []           # (Finding, ops)
    words = {"begin": "START TRANSACTION" if fam == "mysql" else "BEGIN"}

    def add(rid, ev, title_en, title_hi, ops, extra_en="", extra_hi=""):
        en, hi = _says(rid, fams, False), _says(rid, fams, True)
        if not en:
            return
        kind = en[0]
        found.append((F(kind, "transaction", title_en, title_hi, re.sub(r"\s+", " ", ev).strip()[:90],
                        (en[1] + (" " + extra_en if extra_en else "")).strip(), (hi[1] + (" " + extra_hi if extra_hi else "")).strip()),
                      ops))

    closes = [i for i, x in enumerate(st) if x["kind"] in ("commit", "rollback")]
    state = {"open": False, "begin": None, "first": None, "writes": [], "reads": [], "savepoints": [], "mode": None,
             "via_savepoint": False, "inner": None}
    loose: list[int] = []             # changes made with no transaction open (since the last end)
    last_end = -1
    upgrade_said = False
    rollback_to_last = False
    auto_off = False

    def close(i):
        nonlocal last_end
        state.update({"open": False, "begin": None, "first": None, "writes": [], "reads": [], "savepoints": [], "mode": None,
                      "via_savepoint": False, "inner": None})
        last_end = i

    def open_(i, mode="deferred"):
        state.update({"open": True, "begin": i, "first": None, "writes": [], "reads": [], "savepoints": [], "mode": mode,
                      "via_savepoint": False, "inner": None})
        loose.clear()

    from app.agents.sqlbasics import _foreign_syntax

    for i, x in enumerate(st):                        # another database's transaction words this one refuses (its 'refuses')
        if x["kind"] in ("read", "write"):
            continue                                  # (checked with the statement itself below)
        for f, e in _foreign_syntax(x["text"], x["text"], F):     # (control words: no strings to hide; '3s' is read)
            new = (x["text"][:e[0]] + e[2] + x["text"][e[1]:]).strip() if e else None
            found.append((f, [("delete", i)] if new == "" else [("replace", i, new)] if new else []))
    lite = fam == "sqlite"
    for i, x in enumerate(st):
        k = x["kind"]
        rollback_to_last = False
        if lite and k == "lock":                      # SQLite: LOCK TABLES is read as BEGIN IMMEDIATE, UNLOCK as COMMIT
            k = "begin" if not state["open"] else "other"
            x = {**x, "mode": "immediate"}
        elif lite and k == "unlock":
            k = "commit"
        if k == "begin":
            if state["open"]:
                later = [j for j in closes if j > i]
                name = f"step{sum(1 for f, _o in found if f.topic == 'transaction' and 'second BEGIN' in f.title_en) + 1}"
                ops = [("replace", i, f"SAVEPOINT {name}")] if len(later) >= 2 else [("delete", i)]
                if len(later) >= 2:
                    j = later[0]
                    ops.append(("replace", j, f"RELEASE SAVEPOINT {name}" if st[j]["kind"] == "commit" else f"ROLLBACK TO SAVEPOINT {name}"))
                    state["inner"] = (j, name)
                add("nested_begin", x["text"], "A second BEGIN while a transaction is already open",
                    "Transaction khuli rehte doosra BEGIN", ops,
                    "For an inner part that can be undone on its own, use SAVEPOINT name ... RELEASE name (or ROLLBACK TO name).",
                    "Andar ke hisse ko alag se wapas lene ke liye SAVEPOINT naam ... RELEASE naam (ya ROLLBACK TO naam).")
                continue
            open_(i, x["mode"])
            continue
        if k == "autocommit":
            auto_off = not x["on"]
            if auto_off and not state["open"]:
                open_(i, "start")
            elif not auto_off and state["open"]:
                close(i)
            continue
        if k in ("commit", "rollback"):
            if state.get("inner") and state["inner"][0] == i:
                state["inner"] = None
                continue
            if not state["open"]:
                if k == "rollback" and loose:
                    first = loose[0]
                    add("end_without_begin", x["text"], "ROLLBACK with no transaction open: the changes before it are already saved",
                        "Bina transaction ke ROLLBACK: pehle ke badlav save ho chuke", [("insert", first, words["begin"])],
                        "To be able to undo them, start a transaction before the first change.",
                        "Unhe wapas lene layak banane ke liye pehle badlav se pehle transaction shuru karo.")
                elif fam and _rule(fam, "end_without_begin") and (fam == "sqlite" or loose):
                    ops = [("insert", loose[0], words["begin"])] if loose else [("delete", i)]
                    add("end_without_begin", x["text"], f"{'COMMIT' if k == 'commit' else 'ROLLBACK'} with no transaction open",
                        f"Bina transaction ke {'COMMIT' if k == 'commit' else 'ROLLBACK'}", ops)
                loose.clear()
                last_end = i
                continue
            close(i)
            if auto_off:                                     # SET autocommit = 0: the next statement starts the next one
                open_(i, "start")
            continue
        if k == "savepoint":
            if not state["open"]:
                if fam == "sqlite":
                    open_(i, "deferred")
                    state["via_savepoint"] = True
                    state["savepoints"].append(x["name"].lower())
                elif fams:
                    rid = "savepoint_outside" if fam == "postgresql" or fam is None else "savepoint_missing"
                    add(rid, x["text"], "SAVEPOINT with no transaction open", "Bina transaction ke SAVEPOINT",
                        [("insert", i, words["begin"])],
                        "Start the transaction first; the savepoint lives inside it." if rid == "savepoint_missing" else "",
                        "Pehle transaction shuru karo; savepoint uske andar rehta hai." if rid == "savepoint_missing" else "")
                    open_(i, "deferred")                     # read on as if it had begun (what was meant)
                    state["savepoints"].append(x["name"].lower())
                continue
            state["savepoints"].append(x["name"].lower())
            continue
        if k in ("release", "rollback_to"):
            name = x["name"].lower()
            if name not in state["savepoints"]:
                near = difflib.get_close_matches(name, state["savepoints"], n=1, cutoff=0.6)
                ops = [("replace", i, re.sub(rf"\b{re.escape(x['name'])}\b(?=[`\"]?\s*;?\s*$)", near[0], x["text"]))] if near else []
                rid = "savepoint_missing"
                add(rid, x["text"], f"No savepoint named {x['name']} exists here", f"{x['name']} naam ka savepoint yahan hai hi nahi", ops,
                    (f"Did you mean {near[0]}?" if near else "Make it first with SAVEPOINT " + x["name"] + " inside the transaction."),
                    (f"Kya matlab {near[0]} tha?" if near else "Pehle transaction ke andar SAVEPOINT " + x["name"] + " banao."))
                continue
            k_at = state["savepoints"].index(name)
            if k == "release":
                state["savepoints"] = state["savepoints"][:k_at]
                if state["via_savepoint"] and not state["savepoints"]:
                    close(i)
            else:
                state["savepoints"] = state["savepoints"][:k_at + 1]
                rollback_to_last = True
            continue
        if k == "set_txn":
            if x["scope"] in ("session", "global"):
                continue
            if fam == "mysql" or "mysql" in fams:
                if state["open"]:
                    add("set_txn_place", x["text"], "SET TRANSACTION inside an open transaction", "Khuli transaction ke andar SET TRANSACTION",
                        [("delete", i), ("insert", state["begin"], x["text"])])
            if fam == "postgresql" or (fam is None and "postgresql" in fams):
                if not state["open"]:
                    nxt = next((j for j in range(i + 1, len(st)) if st[j]["kind"] == "begin"), None)
                    ops = [("delete", i), ("insert_after", nxt, x["text"])] if nxt is not None else []
                    add("set_txn_outside", x["text"], "SET TRANSACTION outside a transaction block", "Transaction block ke bahar SET TRANSACTION",
                        ops)
                elif state["reads"] or state["writes"]:
                    add("set_txn_late", x["text"], "SET TRANSACTION after the transaction already ran a query",
                        "Transaction mein query chalne ke baad SET TRANSACTION", [("delete", i), ("insert_after", state["begin"], x["text"])])
            continue
        if k == "lock" and state["open"] and fam == "mysql":
            add("ddl_commits", x["text"], "LOCK TABLES inside the transaction commits it first",
                "Transaction ke andar LOCK TABLES pehle use COMMIT kar deta hai", [("delete", i), ("insert", state["begin"], x["text"])])
            close(i)
            continue
        only_outside = k == "vacuum" or (k == "pragma" and x["name"] == "journal_mode" and x["sets"]) or \
            (k == "ddl" and re.match(r"^\s*(?:create\s+(?:unique\s+)?index\s+concurrently|(?:create|drop)\s+database)\b", x["text"], re.I))
        if only_outside and state["open"] and _says("outside_only", fams, False) and \
                (k != "ddl" or "postgresql" in fams) and (k != "pragma" or "sqlite" in fams):
            nxt = next((j for j in range(i + 1, len(st)) if st[j]["kind"] in ("commit", "rollback")), None)
            if k == "pragma":
                ops = [("delete", i), ("insert", state["begin"], x["text"])]      # set the mode first, then the transaction
            elif nxt is not None:
                ops = [("delete", i), ("insert_after", nxt, x["text"])]            # after the transaction ends
            else:
                ops = [("delete", i), ("append", None, x["text"])]
            verb = " ".join(x["text"].split()[:1 if k == "vacuum" else 3 if k == "ddl" else 2]).upper().rstrip(";")
            add("outside_only", x["text"], f"{verb} can't run inside a transaction", f"{verb} transaction ke andar nahi chalta", ops)
            continue
        if k in ("vacuum", "pragma"):
            continue
        if k == "ddl":
            if state["open"] and "mysql" in fams and _rule("mysql", "ddl_commits"):
                before = bool(state["writes"])
                if fam == "mysql" or fam is None:
                    title = ("CREATE / ALTER / DROP inside the transaction commits the changes before it" if before else
                             "CREATE / ALTER / DROP inside the transaction ends it")
                    title_hi = ("Transaction ke andar CREATE / ALTER / DROP pehle ke badlav COMMIT kar deta hai" if before else
                                "Transaction ke andar CREATE / ALTER / DROP use khatam kar deta hai")
                    n0 = len(found)
                    add("ddl_commits", x["text"], title, title_hi, [("delete", i), ("insert", state["begin"], x["text"])],
                        "Run the table change before START TRANSACTION, then the data changes inside it.",
                        "Table ka badlav START TRANSACTION se pehle chalao, phir data ke badlav uske andar.")
                    if len(found) > n0 and not before:
                        f = found[-1][0]
                        if f.kind == "wrong":
                            f.kind = "check"
                if fam == "mysql":
                    close(i)
                    continue
            if state["open"]:
                state["writes"].append(i)
                state["first"] = state["first"] or "write"
            continue
        if k == "read":
            if state["open"]:
                state["reads"].append(i)
                state["first"] = state["first"] or "read"
            continue
        if k == "write":
            if state["open"]:
                if state["first"] == "read" and state["mode"] == "deferred" and fam == "sqlite" and not upgrade_said:
                    upgrade_said = True
                    b = state["begin"]
                    new = re.sub(r"^\s*begin(?:\s+deferred)?\b", "BEGIN IMMEDIATE", st[b]["text"], flags=re.I) \
                        if st[b]["kind"] == "begin" else "BEGIN IMMEDIATE"
                    op = ("replace", b, new) if st[b]["kind"] == "begin" else ("insert", b, "BEGIN IMMEDIATE")
                    add("deferred_upgrade", st[b]["text"] + " ... " + x["text"], "BEGIN reads first and writes later: another writer in between makes the write fail",
                        "BEGIN pehle padhta hai, baad mein likhta hai: beech mein koi aur likhe to write fail", [op])
                state["writes"].append(i)
                state["first"] = state["first"] or "write"
            else:
                loose.append(i)
            continue
    if state["open"] and (state["writes"] or rollback_to_last) and not auto_off:
        what = "ROLLBACK TO keeps the transaction open" if rollback_to_last else "The transaction is never ended (no COMMIT or ROLLBACK)"
        what_hi = "ROLLBACK TO transaction ko khula rakhta hai" if rollback_to_last else "Transaction kabhi khatam nahi hoti (na COMMIT, na ROLLBACK)"
        add("left_open", st[-1]["text"] if rollback_to_last else st[state["begin"]]["text"], what, what_hi, [("append", None, "COMMIT")],
            ("ROLLBACK TO undoes back to the savepoint but does not end the transaction: end it with COMMIT (keeps what came before "
             "the savepoint) or ROLLBACK (undoes everything)." if rollback_to_last else
             "End it with COMMIT to keep the changes (or ROLLBACK to undo them)."),
            ("ROLLBACK TO savepoint tak wapas leta hai par transaction khatam nahi karta: COMMIT (savepoint se pehle ka rakho) ya "
             "ROLLBACK (sab wapas) se khatam karo." if rollback_to_last else "COMMIT se badlav rakho (ya ROLLBACK se wapas lo)."))
    if not any(x["kind"] in CONTROL for x in st) and len(loose) >= 2:
        tables = {st[j]["table"] for j in loose}
        if len(tables) >= 2:
            add("no_txn_writes", f"{len(loose)} changes, no transaction", f"{len(loose)} changes with no transaction: a failure halfway leaves half of them saved",
                f"{len(loose)} badlav bina transaction: beech mein fail hua to aadhe save reh jayenge",
                [("insert", loose[0], words["begin"]), ("insert_after", loose[-1], "COMMIT")])
    fs = sorted(found, key=lambda fo: ORDER[fo[0].kind])
    inner_f, inner_ops = _inner(st, intent)
    fs_all = [f for f, _o in fs] + inner_f
    sure = any(f.kind in ("error", "wrong") for f in fs_all)
    ops = []
    for f, o in fs:
        if sure and f.kind == "check":
            continue
        ops += o
    for f, o in inner_ops:
        if f.kind in ("error", "wrong"):
            ops += o                                  # a statement's own 'check' rewrite (batching ...) is offered, not applied
    fixed = _apply(st, ops) if ops else None
    fs_all.sort(key=lambda f: ORDER[f.kind])
    return {"findings": [f.as_dict() for f in fs_all], "corrected": fixed if fixed and fixed != _join(x.get("lead", "") + x["text"] for x in st) else None, "guesses": []}


def _inner(st: list[dict], intent: str) -> tuple[list, list]:
    """Each SELECT / INSERT / UPDATE / DELETE inside the script, checked on its own by the usual checker."""
    from app.agents import sqlcorrect

    found, ops = [], []
    for i, x in enumerate(st):
        if x["kind"] not in ("read", "write"):
            continue
        tok = sqlcorrect.BLOCK_KEYS.set(None)
        try:
            r = sqlcorrect.check(x["text"], intent=intent)
        except Exception:  # noqa: BLE001 - one statement the checker can't read doesn't stop the script reading
            r = {"findings": []}
        finally:
            sqlcorrect.BLOCK_KEYS.reset(tok)
        fs = [sqlcorrect.Finding(**{k: f[k] for k in ("kind", "topic", "title_en", "title_hi", "evidence", "fix_en", "fix_hi")})
              for f in r.get("findings") or []]
        found += fs
        if r.get("corrected") and fs:
            worst = min(fs, key=lambda f: ORDER[f.kind])
            ops.append((worst, [("replace", i, r["corrected"].strip().rstrip(";"))]))
    return found, ops


def _join(texts) -> str:
    return "\n".join(t.strip().rstrip(";") + ";" for t in texts)


def _apply(st: list[dict], ops: list) -> str:
    """Statement-level edits: replace / delete / insert (before) / insert_after / append; the first edit of a statement wins."""
    rep, dele, before, after, tail, done = {}, set(), {}, {}, [], set()
    for op in ops:
        kind, i, *rest = op
        if kind == "replace" and i not in rep and i not in dele:
            rep[i] = rest[0]
        elif kind == "delete" and i not in rep:
            dele.add(i)
        elif kind == "insert" and (i, rest[0]) not in done:
            before.setdefault(i, []).append(rest[0])
            done.add((i, rest[0]))
        elif kind == "insert_after" and (i, rest[0]) not in done:
            after.setdefault(i, []).append(rest[0])
            done.add((i, rest[0]))
        elif kind == "append" and rest[0] not in tail:
            tail.append(rest[0])
    out = []
    for i, x in enumerate(st):
        out += before.get(i, [])
        if i not in dele:
            out.append(x.get("lead", "") + rep.get(i, x["text"]))
        out += after.get(i, [])
    out += tail
    return _join(out)
