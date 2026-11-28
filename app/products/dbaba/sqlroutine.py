"""Stored procedures, functions and triggers (night 5 of the curriculum): reading them and the mistakes that stop them, loop
them forever, or make them silently do nothing (fundamentals 'routine_flow' and 'engine_syntax' in
app/dbknow/core/query_fundamentals.json).

What a routine must get right, on every engine:
- IT MUST END: a WHILE / REPEAT whose condition reads only variables the body never changes, or a bare LOOP with no
  LEAVE / EXIT / RETURN, either never runs or never stops (the lab: MariaDB stopped only by max_statement_time, PostgreSQL
  only by statement_timeout).
- IT MUST ANSWER: a function must reach a RETURN on every path (MySQL/MariaDB 1321 'ended without RETURN', PostgreSQL
  'control reached end of function without RETURN'); a PostgreSQL trigger function must RETURN NEW (or OLD) for the row to be
  kept: RETURN NULL in a BEFORE row trigger silently skips the change.
- A TRIGGER MUST NOT CHANGE ITS OWN TABLE with a statement: MySQL/MariaDB refuse it when it fires (1442), PostgreSQL fires the
  trigger again and again until 'stack depth limit exceeded'. Change the row itself instead: SET NEW.col = ... in a BEFORE
  trigger (MySQL/MariaDB), NEW.col := ...; RETURN NEW in a BEFORE trigger function (PostgreSQL). Changing NEW in an AFTER
  trigger is refused by MySQL/MariaDB (1362) and silently ignored by PostgreSQL.
- MySQL/MariaDB only: a cursor read in a loop needs DECLARE CONTINUE HANDLER FOR NOT FOUND (else 1329 'No data' at the end);
  DECLAREs come first, in the order variables, cursors, handlers (1064 / 1337 / 1338); the mysql client splits a script at
  every ';', so a body with several statements needs DELIMITER around it.
- PostgreSQL only: a trigger runs a function (CREATE TRIGGER ... EXECUTE FUNCTION f()), never a statement; plpgsql assigns
  with := (SET NEW.x = <expression> is a syntax error, and SET NEW.x = 'text' silently sets a custom setting named new.x,
  leaving the row as it was); a function body needs LANGUAGE.
Every rule was reproduced on MariaDB 10.11 and PostgreSQL 16 (tests/practice/test_night5.py has the lab table).
"""

import re

PROVEN = {k: ["mariadb", "postgresql"] for k in (
    "endless_loop", "function_return", "trigger_same_table", "after_new", "trigger_return", "cursor_handler", "declare_order",
    "delimiter", "trigger_body_pg", "no_language")}

NAME = r"[`\"]?[A-Za-z_][\w$]*[`\"]?"
HEAD_RE = re.compile(r"^\s*(?:delimiter\s+\S+\s+)?create\s+(?:or\s+replace\s+)?(?:definer\s*=\s*\S+\s+)?(procedure|function|trigger)\s+"
                     r"(?:if\s+not\s+exists\s+)?([`\"\w.]+)", re.I)
KW = {"and", "or", "not", "null", "is", "true", "false", "in", "between", "like", "exists", "select", "from", "where", "do", "loop",
      "then", "else", "end", "if", "while", "repeat", "until", "case", "when", "found", "row_count", "new", "old", "count"}


def _uq(n: str) -> str:
    return (n or "").strip('`"').lower()


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def _short(t: str, n: int = 110) -> str:
    t = _flat(t)
    return t if len(t) <= n else t[:n - 3] + "..."


def _mask(text: str) -> str:
    """Same length: string contents and comments blanked (keeps $$ ... $$ bodies, which are code)."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        c = text[i]
        if c == "'":
            j = i + 1
            while j < n and not (text[j] == "'" and (j + 1 >= n or text[j + 1] != "'")):
                j += 2 if text[j] == "'" else 1
            for k in range(i + 1, min(j, n)):
                out[k] = "x"
            i = j + 1
        elif text.startswith("--", i) or c == "#":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def split_script(text: str, blocks: bool = False) -> list[str]:
    """A script as the database clients run it: DELIMITER lines change the separator (mysql client), $$ ... $$ (and $tag$)
    keep a PostgreSQL body whole, strings and comments are skipped. Returns the statements without their separators.
    blocks=True: also keep a BEGIN ... END body whole when no DELIMITER was set (to read what the person meant, not what the
    mysql client would send)."""
    out, cur, delim = [], [], ";"
    m = _mask(text)
    i, n = 0, len(text)
    dollar = None
    depth = 0
    while i < n:
        if blocks and dollar is None and delim == ";" and (i == 0 or not (m[i - 1].isalnum() or m[i - 1] == "_")):
            om = re.match(r"(begin|case)\b(?!\s+(?:transaction|work)\b)", m[i:i + 20], re.I)
            cm = re.match(r"end\b(\s+(if|while|loop|repeat|case)\b)?", m[i:i + 20], re.I)
            if om and not re.match(r"begin\s*;", m[i:i + 20], re.I):
                depth += 1
            elif cm and (cm.group(2) is None or cm.group(2).lower() == "case"):
                depth = max(0, depth - 1)
        if blocks and depth > 0 and m[i] == ";" and dollar is None:
            cur.append(text[i])
            i += 1
            continue
        if dollar is None and (i == 0 or text[i - 1] == "\n"):
            dm = re.match(r"[ \t]*delimiter[ \t]+(\S+)[ \t]*(?:\n|$)", text[i:], re.I)
            if dm:
                if "".join(cur).strip():
                    out.append("".join(cur).strip())
                cur = []
                delim = dm.group(1)
                i += dm.end()
                continue
        if dollar is None:
            tm = re.match(r"\$([A-Za-z_]\w*)?\$", m[i:i + 40])
            if tm:
                dollar = tm.group(0)
                cur.append(text[i:i + len(dollar)])
                i += len(dollar)
                continue
            if m.startswith(delim, i):
                if "".join(cur).strip():
                    out.append("".join(cur).strip())
                cur = []
                i += len(delim)
                continue
        elif text.startswith(dollar, i):
            cur.append(dollar)
            i += len(dollar)
            dollar = None
            continue
        cur.append(text[i])
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def with_delimiter(text: str) -> str:
    """A one-routine script made safe for the mysql client: DELIMITER // ... // DELIMITER ;"""
    return "DELIMITER //\n" + text.strip().rstrip(";").rstrip() + " //\nDELIMITER ;"


def is_routine(text: str) -> bool:
    return bool(HEAD_RE.match(_mask(text or ""))) or bool(re.match(r"\s*do\s+\$", text or "", re.I))


# ------------------------------------------------------------------------------------------------ reading a routine
def _blocks(sql: str) -> list:
    """Each CREATE PROCEDURE / FUNCTION / TRIGGER (or DO block) in the text: kind, name, header, body, positions."""
    stmts = split_script(sql, blocks=True)
    out = []
    for st in stmts:
        m = _mask(st)
        hm = HEAD_RE.match(m)
        if not hm and not re.match(r"\s*do\s+\$", m, re.I):
            out.append({"kind": "statement", "text": st, "mask": m})
            continue
        kind = hm.group(1).lower() if hm else "do"
        name = _uq(hm.group(2).split(".")[-1]) if hm else ""
        d = re.search(r"\$([A-Za-z_]\w*)?\$", m)
        if d:
            tag = d.group(0)
            e = m.find(tag, d.end())
            body = (d.end(), e if e > 0 else len(m))
            style = "pg"
        else:
            bm = re.search(r"\bbegin\b", m, re.I)
            if kind == "trigger":
                fm = re.search(r"\bfor\s+each\s+row\b(?:\s+(?:follows|precedes)\s+\w+)?", m, re.I)
                body = (fm.end(), len(m)) if fm else ((bm.start(), len(m)) if bm else (len(m), len(m)))
            else:
                rm = re.search(r"\)\s*(?:returns\s+[\w() ,]+?)?\s*(?:(?:deterministic|not\s+deterministic|contains\s+sql|reads\s+sql\s+data|"
                               r"modifies\s+sql\s+data|no\s+sql|comment\s+'x*'|language\s+sql|sql\s+security\s+\w+)\s*)*", m, re.I)
                body = (bm.start() if bm else (rm.end() if rm else len(m)), len(m))
            style = "pg" if re.search(r"\bexecute\s+(?:function|procedure)\b", m, re.I) else "my"
        info = {"kind": kind, "name": name, "text": st, "mask": m, "body": body, "style": style}
        if kind == "trigger":
            tm = re.search(rf"\b(before|after|instead\s+of)\s+(insert|update|delete)(?:\s+or\s+(?:insert|update|delete))*(?:\s+of\s+[\w, ]+?)?\s+on\s+({NAME}(?:\.{NAME})?)",
                           m, re.I)
            if tm:
                info["timing"], info["event"], info["table"] = tm.group(1).lower(), tm.group(2).lower(), _uq(tm.group(3).split(".")[-1])
                info["timing_span"] = (tm.start(1), tm.end(1))
            em = re.search(r"\bexecute\s+(?:function|procedure)\s+([\w.\"]+)\s*\(", m, re.I)
            info["calls"] = _uq(em.group(1).split(".")[-1]) if em else None
        if kind == "function":
            info["returns_trigger"] = re.search(r"\breturns\s+trigger\b", m, re.I) is not None
        out.append(info)
    return out


def _loops(m: str, a: int, b: int) -> list:
    """Loops in m[a:b]: [{'kind': while/repeat/loop/for, 'cond': (s, e), 'body': (s, e), 'start': i, 'label': str}]."""
    toks = []
    rx = re.compile(r"\bend\s+(while|repeat|loop)\b|\b(while)\b|\b(repeat)\b|\b(for)\b|\b(loop)\b|\b(until)\b|\b(do)\b", re.I)
    for t in rx.finditer(m, a, b):
        toks.append(t)
    stack, out = [], []
    k = 0
    while k < len(toks):
        t = toks[k]
        if t.group(1):                                     # END WHILE / END REPEAT / END LOOP
            want = t.group(1).lower()
            while stack:
                top = stack.pop()
                if top["kind"] == want or (want == "loop" and top["kind"] in ("while", "for", "loop")):
                    top["body"] = (top["body_start"], t.start())
                    if top["kind"] == "repeat" and top.get("until"):
                        top["body"] = (top["body_start"], top["until"][0])
                        top["cond"] = (top["until"][1], t.start())
                    out.append(top)
                    break
        elif t.group(2) or t.group(4):                     # WHILE cond DO|LOOP / FOR ... LOOP
            kind = "while" if t.group(2) else "for"
            nxt = next((x for x in toks[k + 1:] if (x.group(7) or x.group(5))), None)
            if nxt is None:
                break
            lab = re.search(r"(\w+)\s*:\s*$", m[max(a, t.start() - 40):t.start()])
            stack.append({"kind": kind, "start": t.start(), "cond": (t.end(), nxt.start()), "body_start": nxt.end(),
                          "label": lab.group(1).lower() if lab else ""})
            k = toks.index(nxt)
        elif t.group(3):
            lab = re.search(r"(\w+)\s*:\s*$", m[max(a, t.start() - 40):t.start()])
            stack.append({"kind": "repeat", "start": t.start(), "cond": None, "body_start": t.end(),
                          "label": lab.group(1).lower() if lab else ""})
        elif t.group(5):
            lab = re.search(r"(\w+)\s*:\s*$", m[max(a, t.start() - 40):t.start()])
            stack.append({"kind": "loop", "start": t.start(), "cond": None, "body_start": t.end(),
                          "label": lab.group(1).lower() if lab else ""})
        elif t.group(6) and stack and stack[-1]["kind"] == "repeat":
            stack[-1]["until"] = (t.start(), t.end())
        k += 1
    return out


def _assigned(text: str) -> set:
    """Variables a piece of routine code changes."""
    out = set()
    for m in re.finditer(r"\bset\s+([^;]+)", text, re.I):
        for part in m.group(1).split(","):
            pm = re.match(r"\s*(@?[A-Za-z_][\w.]*)\s*(?::=|=)", part)
            if pm:
                out.add(pm.group(1).lower())
    out |= {x.lower() for x in re.findall(r"(@?[A-Za-z_]\w*)\s*:=", text)}
    for m in re.finditer(r"\binto\s+(@?[A-Za-z_][\w]*(?:\s*,\s*@?[A-Za-z_]\w*)*)", text, re.I):
        if not re.search(r"\binsert\s+(?:ignore\s+)?$", text[max(0, m.start() - 20):m.start()], re.I):
            out |= {x.strip().lower() for x in m.group(1).split(",")}
    return out


def _cond_vars(text: str) -> set:
    return {w.lower() for w in re.findall(r"(?<![\w.$])(@?[A-Za-z_]\w*)(?!\s*\()", text) if w.lower() not in KW}


# ------------------------------------------------------------------------------------------------ the rules
def routines(sql: str, intent: str, F) -> list:
    """[(Finding, [edits])] for a script with CREATE PROCEDURE / FUNCTION / TRIGGER or a DO block."""
    try:
        return _routines(sql, intent, F)
    except Exception:  # noqa: BLE001 - a routine shape this reader doesn't know: say nothing rather than something wrong
        return []


def _engine(sql: str):
    from app.agents import sqlbasics

    return sqlbasics.engine_seen(sql)


def _routines(sql: str, intent: str, F) -> list:
    out = []
    eng = _engine(sql)
    blocks = _blocks(sql)
    triggers = [b for b in blocks if b["kind"] == "trigger"]
    has_delim = re.search(r"(?mi)^\s*delimiter\s+\S+", sql) is not None

    def at(b, i):                                          # position of block-relative i in the original text
        return sql.find(b["text"]) + i

    for b in blocks:
        if b["kind"] == "statement":
            continue
        m, st = b["mask"], b["text"]
        ba, bb = b["body"]
        body_m = m[ba:bb]
        ev = _short(st)

        # ---- loops that can't end
        for lp in _loops(m, ba, bb):
            if lp["kind"] == "for":
                continue
            inner = m[lp["body"][0]:lp["body"][1]]
            exits = re.search(r"\b(leave|exit|return)\b", inner, re.I)
            if lp["kind"] == "loop":
                if exits:
                    continue
                out.append((F("wrong", "endless_loop", "This LOOP has no way out: it never ends",
                              "Is LOOP se bahar nikalne ka raasta nahi: ye kabhi khatam nahi hoga", _short(st[lp["start"]:lp["body"][1] + 8]),
                              "A plain LOOP repeats until a LEAVE (MySQL/MariaDB) or EXIT (PostgreSQL) or RETURN runs, and this one has "
                              "none, so the call hangs until a timeout kills it. Add the exit: IF done THEN LEAVE label; END IF "
                              "(MySQL/MariaDB) or EXIT WHEN done; (PostgreSQL).",
                              "Plain LOOP tab tak chalta hai jab tak LEAVE / EXIT / RETURN na chale, aur isme ek bhi nahi, isliye call "
                              "timeout tak latki rehti hai. Bahar nikalna jodo: IF done THEN LEAVE label; END IF (MySQL) ya EXIT WHEN done; "
                              "(PostgreSQL)."), []))
                continue
            if not lp.get("cond"):
                continue
            cond = m[lp["cond"][0]:lp["cond"][1]]
            if re.search(r"\bselect\b|\(", cond, re.I) or exits:
                continue                                   # reads a table or a function: it may change from outside
            vars_ = _cond_vars(cond)
            if not vars_ or vars_ & _assigned(inner):
                continue
            v = sorted(vars_)[0]
            word = "WHILE" if lp["kind"] == "while" else "REPEAT ... UNTIL"
            out.append((F("wrong", "endless_loop", f"{word} {_flat(cond)}: nothing in the loop changes {v}",
                          f"{word} {_flat(cond)}: loop ke andar {v} kabhi nahi badalta", _short(st[lp["start"]:lp["body"][1] + 10]),
                          f"The condition reads only {', '.join(sorted(vars_))}, and the loop body never sets {'it' if len(vars_) == 1 else 'them'}, "
                          f"so the condition gives the same answer every time: the loop either never runs or never stops (the lab: "
                          f"stopped only by max_statement_time on MariaDB, statement_timeout on PostgreSQL). Change {v} inside the loop, "
                          f"e.g. SET {v} = {v} + 1 (MySQL/MariaDB) or {v} := {v} + 1 (PostgreSQL).",
                          f"Condition sirf {', '.join(sorted(vars_))} padhti hai aur loop ke andar wo kabhi set nahi hota, isliye har baar "
                          f"same jawab: loop ya to chalega hi nahi ya kabhi rukega nahi. Loop ke andar {v} badlo, jaise SET {v} = {v} + 1."),
                        []))

        # ---- a function that can end without RETURN
        if b["kind"] == "function" and not b.get("returns_trigger"):
            core = re.sub(r"\bend\s*(?:\w+)?\s*[;$]*\s*$", "", body_m.rstrip(), flags=re.I).rstrip().rstrip(";")
            if re.search(r"\bbegin\b", body_m, re.I) and re.search(r"\breturn\b", body_m, re.I):
                last = re.split(r";|\bbegin\b", core, flags=re.I)[-1]
                tail_ok = re.match(r"\s*return\b", last, re.I) or re.search(r"\bend\s+if\s*$", core, re.I) and \
                    _if_has_else_return(core)
                if not tail_ok and not re.search(r"\bend\s+case\s*$", core, re.I):
                    em = list(re.finditer(r"\bend\b", m[:bb] if b["style"] == "pg" else m, re.I))
                    edits = []
                    if em:
                        pos = at(b, em[-1].start())
                        edits = [(pos, pos, "RETURN NULL;\n")]
                    out.append((F("wrong", "function_return", f"Function {b['name']} can end without a RETURN",
                                  f"Function {b['name']} bina RETURN ke khatam ho sakta hai", ev,
                                  "When no branch with a RETURN runs (an IF with no ELSE), the function reaches its END with nothing to "
                                  "return: MySQL/MariaDB stop with error 1321 (FUNCTION ... ended without RETURN), PostgreSQL with "
                                  "'control reached end of function without RETURN'. End the body with a RETURN for the remaining case "
                                  "(an ELSE branch, or RETURN NULL / a default before END).",
                                  "Jab RETURN wali koi branch nahi chalti (IF bina ELSE), function END tak pahunch jaata hai: MySQL "
                                  "error 1321, PostgreSQL 'control reached end of function without RETURN'. Baaki case ke liye END se "
                                  "pehle RETURN do (ELSE branch, ya RETURN NULL / default)."), edits))

        # ---- PostgreSQL trigger function: RETURN NEW
        if b["kind"] == "function" and b.get("returns_trigger"):
            sets_new = re.search(r"\bnew\.\w+\s*:?=", body_m, re.I) is not None
            used_by = [t for t in triggers if t.get("calls") == b["name"]]
            before_row = any(t.get("timing") == "before" for t in used_by)
            if not re.search(r"\breturn\b", body_m, re.I):
                em = list(re.finditer(r"\bend\b", body_m, re.I))
                edits = [(at(b, ba + em[-1].start()), at(b, ba + em[-1].start()), "RETURN NEW;\n")] if em else []
                out.append((F("error", "trigger_return", f"Trigger function {b['name']} has no RETURN",
                              f"Trigger function {b['name']} mein RETURN nahi", ev,
                              "A PostgreSQL trigger function must return a row: when it fires, it stops with 'control reached end of "
                              "trigger procedure without RETURN' and the change is cancelled. End it with RETURN NEW (the row as it "
                              "should be saved; RETURN OLD for DELETE).",
                              "PostgreSQL trigger function ko row lautani padti hai: warna 'control reached end of trigger procedure "
                              "without RETURN' aur change cancel. Aakhir mein RETURN NEW likho (DELETE ke liye RETURN OLD)."), edits))
            else:
                rn = re.search(r"\breturn\s+null\b", body_m, re.I)
                if rn and (sets_new or before_row) and not re.search(r"\breturn\s+(new|old)\b", body_m, re.I):
                    p = at(b, ba + rn.start())
                    kind = "wrong" if sets_new else "check"
                    out.append((F(kind, "trigger_return", "RETURN NULL in a BEFORE row trigger skips the change",
                                  "BEFORE row trigger mein RETURN NULL change ko chhod deta hai", ev,
                                  "In a BEFORE ... FOR EACH ROW trigger, the returned row is what PostgreSQL saves, and NULL means 'skip "
                                  "this row': the INSERT/UPDATE silently does nothing (the lab: 0 rows changed, no error)"
                                  + (", and the NEW.x := ... set before it is thrown away" if sets_new else "")
                                  + ". Return the row: RETURN NEW. (RETURN NULL is only right when skipping is the goal, or in AFTER triggers, "
                                  "where the value is ignored.)",
                                  "BEFORE ... FOR EACH ROW trigger mein jo row lautai jaati hai wahi save hoti hai, aur NULL ka matlab 'is "
                                  "row ko chhodo': INSERT/UPDATE chupchaap kuch nahi karta. RETURN NEW likho."),
                                [(p, p + len(rn.group(0)), "RETURN NEW")] if sets_new else []))
            for t in used_by:
                if sets_new and t.get("timing") == "after":
                    a1, a2 = t["timing_span"]
                    p = at(t, a1)
                    out.append((F("wrong", "after_new", "Changing NEW in an AFTER trigger does nothing",
                                  "AFTER trigger mein NEW badalna kuch nahi karta", _short(t["text"]),
                                  "An AFTER trigger runs once the row is already saved, so PostgreSQL ignores the NEW it changes (the "
                                  "lab: the column stayed empty, no error); MySQL/MariaDB refuse it outright (1362). To change the row "
                                  "being saved, make it a BEFORE trigger.",
                                  "AFTER trigger tab chalta hai jab row save ho chuki, isliye PostgreSQL NEW ke badlav ko ignore karta hai; "
                                  "MySQL/MariaDB mana karte hain (1362). Row badalni hai to BEFORE trigger banao."),
                                [(p, p + (a2 - a1), "BEFORE")]))

        # ---- a trigger that changes its own table with a statement
        if b["kind"] == "trigger" and b.get("table"):
            tab = b["table"]
            bodies = [(b, body_m)] if b["style"] == "my" else \
                [(f, f["mask"][f["body"][0]:f["body"][1]]) for f in blocks if f["kind"] == "function" and f["name"] == b.get("calls")]
            for owner, txt in bodies:
                hit = re.search(rf"\b(update\s+{re.escape(tab)}\b|insert\s+(?:ignore\s+)?into\s+{re.escape(tab)}\b|delete\s+from\s+{re.escape(tab)}\b)",
                                txt, re.I)
                if not hit:
                    continue
                edits = []
                if b["style"] == "my":
                    um = re.fullmatch(rf"\s*(?:begin\s+)?update\s+{re.escape(tab)}\s+set\s+([\s\S]+?)\s+where\s+(\w+)\s*=\s*new\.(\w+)\s*;?\s*(?:end\s*;?)?\s*",
                                      body_m, re.I)
                    if um and um.group(2).lower() == um.group(3).lower() and b.get("event") in ("insert", "update"):
                        sets = ", ".join(f"NEW.{x.strip()}" for x in re.split(r",(?![^(]*\))", st[ba + um.start(1):ba + um.end(1)]))
                        head = st[:b["timing_span"][0]] + "BEFORE" + st[b["timing_span"][1]:ba]
                        edits = [(at(b, 0), at(b, len(st)), f"{head.rstrip()} SET {sets}")]
                out.append((F("error" if b["style"] == "my" else "wrong", "trigger_same_table",
                              f"The trigger on {tab} changes {tab} itself", f"{tab} ka trigger khud {tab} ko badalta hai",
                              _short(txt[hit.start():hit.start() + 90]),
                              f"A statement on {tab} from inside {tab}'s own trigger: MySQL/MariaDB refuse it when the trigger fires "
                              f"(error 1442, Can't update table '{tab}' in stored function/trigger because it is already used by statement "
                              f"which invoked this trigger), so the original statement fails too; PostgreSQL fires the trigger again for "
                              f"its own change, again and again, until 'stack depth limit exceeded'. To change the row being saved, set it "
                              f"in a BEFORE trigger: SET NEW.col = ... (MySQL/MariaDB) or NEW.col := ...; RETURN NEW (PostgreSQL).",
                              f"{tab} ke apne trigger se {tab} pe statement: MySQL/MariaDB fire hote hi mana karte hain (1442) aur asli "
                              f"statement bhi fail; PostgreSQL trigger baar baar chalata hai jab tak 'stack depth limit exceeded'. Row badalni "
                              f"hai to BEFORE trigger mein: SET NEW.col = ... (MySQL) ya NEW.col := ...; RETURN NEW (PostgreSQL)."), edits))
                break

        # ---- MySQL/MariaDB: changing NEW in an AFTER trigger
        if b["kind"] == "trigger" and b["style"] == "my" and b.get("timing") == "after" and \
                re.search(r"\bset\s+new\.\w+\s*=", body_m, re.I):
            a1, a2 = b["timing_span"]
            out.append((F("error", "after_new", "SET NEW.col in an AFTER trigger is refused",
                          "AFTER trigger mein SET NEW.col mana hai", ev,
                          "MySQL/MariaDB stop at CREATE TRIGGER with error 1362 (Updating of NEW row is not allowed in after trigger): "
                          "once the row is saved it can't be changed this way. Make it BEFORE, which runs while the row can still change.",
                          "MySQL/MariaDB CREATE TRIGGER pe hi error 1362 dete hain. BEFORE trigger banao, jab row abhi badal sakti hai."),
                        [(at(b, a1), at(b, a2), "BEFORE")]))

        # ---- MySQL/MariaDB: cursors and DECLARE order
        if b["style"] == "my" and b["kind"] in ("procedure", "function", "trigger"):
            if re.search(r"\bdeclare\s+\w+\s+cursor\s+for\b", body_m, re.I) and re.search(r"\bfetch\b", body_m, re.I) and \
                    not re.search(r"\bhandler\s+for\s+(?:not\s+found|sqlstate\s+(?:value\s+)?'02000')", st, re.I):
                out.append((F("error", "cursor_handler", "The cursor loop has no NOT FOUND handler",
                              "Cursor loop mein NOT FOUND handler nahi", ev,
                              "After the last row, FETCH has nothing left to read and MySQL/MariaDB stop the procedure with error 1329 "
                              "(No data - zero rows fetched, selected, or processed). Declare a flag and a handler after the cursor, and "
                              "leave the loop when it is set: DECLARE done INT DEFAULT 0; ... DECLARE CONTINUE HANDLER FOR NOT FOUND SET "
                              "done = 1; ... FETCH cur INTO v; IF done THEN LEAVE read_loop; END IF;",
                              "Aakhri row ke baad FETCH ko kuch nahi milta aur MySQL/MariaDB error 1329 dete hain. Flag aur handler "
                              "declare karo: DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1; FETCH ke baad IF done THEN LEAVE ...; END IF;"),
                            []))
            bm = re.search(r"\bbegin\b", body_m, re.I)
            if bm:
                stmts = [x for x in re.split(r";", body_m[bm.end():])]
                seen_other, seen_cursor, seen_handler, bad = False, False, False, None
                for x in stmts:
                    xs = x.strip()
                    if not xs:
                        continue
                    if re.match(r"(?:\w+\s*:\s*)?(begin|if|while|repeat|loop|case|end)\b", xs, re.I) and not xs.lower().startswith("declare"):
                        seen_other = True
                        break
                    if re.match(r"declare\b", xs, re.I):
                        is_c = re.match(r"declare\s+\w+\s+cursor\b", xs, re.I)
                        is_h = re.match(r"declare\s+(continue|exit|undo)\s+handler\b", xs, re.I)
                        if seen_other:
                            bad = ("1064", "a DECLARE after another statement", xs)
                        elif not is_c and not is_h and (seen_cursor or seen_handler):
                            bad = ("1337", "a variable declared after a cursor or handler", xs)
                        elif is_c and seen_handler:
                            bad = ("1338", "a cursor declared after a handler", xs)
                        seen_cursor |= bool(is_c)
                        seen_handler |= bool(is_h)
                        if bad:
                            break
                    else:
                        seen_other = True
                if bad:
                    out.append((F("error", "declare_order", f"DECLARE order: {bad[1]}", f"DECLARE ka order: {bad[1]}",
                                  _short(bad[2]),
                                  f"MySQL/MariaDB want every DECLARE at the start of its BEGIN ... END block, in this order: variables "
                                  f"and conditions, then cursors, then handlers. Here it is {bad[1]} (error {bad[0]}). Move the "
                                  f"DECLAREs to the top in that order.",
                                  f"MySQL/MariaDB ko har DECLARE BEGIN ke turant baad chahiye, is order mein: variables, phir cursors, "
                                  f"phir handlers. Yahan {bad[1]} hai (error {bad[0]}). DECLAREs upar le jao."), []))

        # ---- MySQL client: a body with several statements needs DELIMITER
        if b["style"] == "my" and b["kind"] in ("procedure", "function", "trigger") and not has_delim and eng != "postgresql":
            inner_semis = body_m.rstrip().rstrip(";").count(";")
            if inner_semis:
                edits = []                                 # the wrapping is done last, around the other fixes (sqlcorrect)
                out.append((F("check", "delimiter", "The body has ';' inside: the mysql client needs DELIMITER around it",
                              "Body ke andar ';' hai: mysql client ko DELIMITER chahiye", ev,
                              "The mysql command-line client (and many tools that run scripts) send each statement at the first ';', "
                              "so they send the CREATE cut in the middle of its body and MySQL/MariaDB stop with error 1064 near '' "
                              "(the lab, with the mysql client). Change the separator around it: DELIMITER // ... END // DELIMITER ;. "
                              "Drivers and GUIs that send the whole text as one statement don't need it.",
                              "mysql client pehle ';' pe statement bhej deta hai, isliye CREATE beech mein kat jaata hai aur error 1064 "
                              "near ''. Separator badlo: DELIMITER // ... END // DELIMITER ;"), edits))

        # ---- PostgreSQL: a trigger runs a function; plpgsql assigns with :=; LANGUAGE is required
        if b["kind"] == "trigger" and b["style"] == "my" and eng == "postgresql":
            sm = re.fullmatch(r"\s*(?:begin\s+)?((?:set\s+new\.\w+\s*=[^;]+;?\s*)+)(?:end\s*;?)?\s*", body_m, re.I)
            edits = []
            if sm and b.get("table"):
                assigns = re.findall(r"set\s+(new\.\w+)\s*=\s*([^;]+)", st[ba:bb], re.I)
                fn = f"{b['name']}_fn"
                body = " ".join(f"{a} := {_flat(e).rstrip(',')};" for a, e in assigns)
                head = st[:ba]
                new = (f"CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN {body} RETURN NEW; END $$;\n"
                       f"{_flat(head)} EXECUTE FUNCTION {fn}()")
                edits = [(at(b, 0), at(b, len(st)), new)]
            out.append((F("error", "trigger_body_pg", "A PostgreSQL trigger can't hold a statement: it runs a function",
                          "PostgreSQL trigger mein statement nahi, function chalta hai", ev,
                          "PostgreSQL refuses a statement after FOR EACH ROW ('syntax error'): the work goes in a function that RETURNS "
                          "trigger (plpgsql, NEW.col := ...; RETURN NEW;), and the trigger calls it with EXECUTE FUNCTION f().",
                          "PostgreSQL FOR EACH ROW ke baad statement nahi maanta: kaam ek function mein jaata hai (RETURNS trigger, "
                          "NEW.col := ...; RETURN NEW;), aur trigger use EXECUTE FUNCTION f() se chalata hai."), edits))
        if b["style"] == "pg" and b["kind"] in ("function", "procedure", "do"):
            for sm in re.finditer(r"\bset\s+(new\.\w+|\w+)\s*=", body_m, re.I):
                if re.match(r"\s*(local\s+|session\s+)?\w+\s+(to|=)", body_m[sm.start() + 3:], re.I) and not sm.group(1).lower().startswith("new."):
                    continue                               # SET search_path = ... is a real command
                p = at(b, ba + sm.start())
                literal = re.match(r"\s*(?:'x*'|-?\d+(?:\.\d+)?|true|false|on|off)\s*(?:;|$)", body_m[sm.end():], re.I) is not None
                out.append((F("wrong" if literal else "error", "trigger_body_pg", f"SET {sm.group(1)} = ... is MySQL syntax; plpgsql assigns with :=",
                              f"SET {sm.group(1)} = ... MySQL ka syntax hai; plpgsql mein :=", _short(body_m[sm.start():sm.start() + 60]),
                              (f"In plpgsql, SET is PostgreSQL's command for settings, and a dotted name like {sm.group(1)} is a custom setting: "
                               f"with a plain value it runs without an error and sets that setting, while the row stays unchanged (the lab: "
                               f"the column stayed empty). " if literal else
                               "PostgreSQL stops at CREATE FUNCTION with 'syntax error' here (SET is its command for settings, which takes "
                               "only a plain value). ") + f"Assign to the row with {sm.group(1)} := ...;",
                              (f"plpgsql mein SET settings ka command hai, aur {sm.group(1)} jaisa naam ek custom setting ban jaata hai: bina "
                               f"error chalta hai par row nahi badalti. " if literal else "PostgreSQL yahan 'syntax error' deta hai. ")
                              + f"Row ko {sm.group(1)} := ...; se badlo."),
                            [(p, p + len(sm.group(0)), f"{st[ba + sm.start(1):ba + sm.end(1)]} :=")]))
            if b["kind"] in ("function", "procedure") and not re.search(r"\blanguage\s+\w+", m, re.I) and re.search(r"\bbegin\b", body_m, re.I):
                endq = m.find("$", bb)
                endq = m.find("$", endq + 1) + 1 if endq >= 0 else len(m)
                p = at(b, endq)
                out.append((F("error", "no_language", f"{b['kind'].title()} {b['name']} has no LANGUAGE",
                              f"{b['kind'].title()} {b['name']} mein LANGUAGE nahi", ev,
                              "PostgreSQL refuses it ('no language specified'): a body with BEGIN ... END is plpgsql and must say so. "
                              "Add LANGUAGE plpgsql (before AS or after the closing $$).",
                              "PostgreSQL mana karta hai ('no language specified'). LANGUAGE plpgsql jodo."), [(p, p, " LANGUAGE plpgsql")]))
    return out


def _if_has_else_return(core: str) -> bool:
    """The body ends with IF ... ELSE ... RETURN ...; END IF: every path returns."""
    last_if = list(re.finditer(r"\bif\b(?!\s*\()", core, re.I))
    if not last_if:
        return False
    tail = core[last_if[0].start():]
    return re.search(r"\belse\b[\s\S]*\breturn\b", tail, re.I) is not None
