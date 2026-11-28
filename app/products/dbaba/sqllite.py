"""How SQLite reads a table design (E1 night 2): CREATE TABLE scripts checked the way SQLite really takes them.

SQLite keeps a type with every VALUE, not with the column: a column's declared type only gives it an affinity (a preference
for converting what comes in), read from the type's letters (INT -> INTEGER; CHAR / CLOB / TEXT -> TEXT; BLOB or none ->
BLOB; REAL / FLOA / DOUB -> REAL; anything else -> NUMERIC). From that one fact follow most of the surprises people meet when
they bring a MySQL / PostgreSQL design:
- IDS: only a single-column PRIMARY KEY declared exactly INTEGER is the rowid and numbers itself. INT, BIGINT, INTEGER(11),
  SERIAL, 'INT AUTO_INCREMENT' are ordinary columns: an INSERT without the id stores NULL, and NULLs never clash.
- KEYS: any other primary key accepts NULL in an ordinary table (NOT NULL, STRICT or WITHOUT ROWID stop it).
- CHECKS THAT AREN'T: VARCHAR(n), CHAR(n) and UNSIGNED are accepted and never enforced; there is no date type (DATE /
  DATETIME are NUMERIC and take anything) and no exact decimal (DECIMAL keeps 1.10 as the binary 1.1).
- LINKS: foreign keys are checked only on connections that run PRAGMA foreign_keys = ON; nothing indexes the child column; a
  link to a column that isn't a key breaks every write to the child once checking is on.
- STRICT tables take only INT, INTEGER, REAL, TEXT, BLOB and ANY.
- Other databases' words it refuses (AUTO_INCREMENT after PRIMARY KEY, IDENTITY, ENGINE=, KEY inside CREATE TABLE, ENUM,
  DEFAULT now(), ON UPDATE ...) are rewritten in its words.
Every fact is in app/dbknow/dialects/sqlite.json (schema_rules, rule B) with its lab lines, and every rule and rewrite was run
on SQLite 3.53 (tests/practice/test_e1_sqlite2.py); the reasoning here is the same for any script.
"""

import re

from app.products.dbaba import sqlschema as S
from app.products.dbaba.dbknow import engines as E

NOT_TYPE = r"(?:constraint|primary|not|null|unique|check|default|collate|references|generated|as|on)"
DATEY = {"date", "datetime", "timestamp", "timestamptz", "time", "timestamp with time zone", "timestamp without time zone", "datetime2",
         "smalldatetime", "datetimeoffset", "timetz"}
MONEYISH = {"real", "float", "double", "double precision", "decimal", "numeric", "money", "number", "float4", "float8", "smallmoney"}


def rules() -> dict:
    return E.dialect("sqlite").get("schema_rules", {})


def declared(sql: str, m: str, col: dict) -> tuple[str, int, int]:
    """(SQLite's declared type, start, end): the type's names and its (n, m), up to the first constraint word. 'INT
    AUTO_INCREMENT PRIMARY KEY' declares 'INT AUTO_INCREMENT' (lab: pragma_table_info said so)."""
    a, e = col["type_span"][0], col["span"][1]
    mm = re.match(rf"[A-Za-z_]\w*(?:\s+(?!{NOT_TYPE}\b)[A-Za-z_]\w*)*(?:\s*\([^()]*\))?", m[a:e], re.I)
    if not mm:
        return "", a, a
    return re.sub(r"\s+", " ", sql[a:a + mm.end()]).strip(), a, a + mm.end()


def affinity(decl: str) -> str:
    u = decl.upper()
    if not u:
        return rules().get("affinity", {}).get("empty", "BLOB")
    for letters, aff in rules().get("affinity", {}).get("rules", []):
        if letters in u:
            return aff
    return rules().get("affinity", {}).get("otherwise", "NUMERIC")


def _opts(t: dict) -> tuple[bool, bool]:
    o = t["options"].lower()
    return bool(re.search(r"\bstrict\b", o)), bool(re.search(r"\bwithout\s+rowid\b", o))


def _item_start(m: str, t: dict, span: tuple[int, int]) -> int:
    """Where to cut an item out together with the comma before it."""
    k = m.rfind(",", t["open"], span[0])
    return k if k >= 0 else span[0]


def _clean_attrs(text: str, auto: bool) -> str:
    """A column's words after its type, without what SQLite refuses there."""
    out = re.sub(r"\s+on\s+update\s+current_timestamp(?:\s*\(\s*\d*\s*\))?", "", text, flags=re.I)
    out = re.sub(r"\bdefault\s+(?:now|getdate|sysdate|localtimestamp|current_timestamp)\s*\(\s*\d*\s*\)", "DEFAULT CURRENT_TIMESTAMP", out,
                 flags=re.I)
    if auto:
        out = re.sub(r"\s*\bauto_increment\b", "", out, flags=re.I)
        out = re.sub(r"\s*\bgenerated\s+(?:always|by\s+default)\s+as\s+identity(?:\s*\([^()]*\))?", "", out, flags=re.I)
        out = re.sub(r"\s*\bprimary\s+key\b(?:\s+(?:asc|desc))?(?:\s+autoincrement)?", "", out, flags=re.I)
        out = re.sub(r"\s*\bunsigned\b", "", out, flags=re.I)
    return out


def design(sql: str, d: dict, F) -> list:
    out = []
    m = d["mask"]
    R = rules()
    tables, pos = d["tables"], {n: i for i, n in enumerate(d["order"])}

    def add(kind, topic, ten, thi, ev, fen, fhi, edits=()):
        out.append((F(kind, topic, ten, thi, S._short(ev), fen, fhi), list(edits)))

    foreign, foreign_edits, wrong_ids, auto_done = [], [], [], set()
    for tn in d["order"]:
        t = tables[tn]
        strict, no_rowid = _opts(t)
        ev_head = f"CREATE TABLE {t['raw']}"
        decl = {c["name"]: declared(sql, m, c) for c in t["cols"]}
        single_pk = t["pk"][0] if len(t["pk"]) == 1 else None
        table_pk_item = next(((a, b) for a, b in t["pk_items"] if re.match(r"\s*(constraint\s+\S+\s+)?primary\s+key\b", m[a:b], re.I)), None)

        # ---- other databases' words: auto-numbered ids (AUTO_INCREMENT, IDENTITY, SERIAL) become INTEGER PRIMARY KEY AUTOINCREMENT
        for c in t["cols"]:
            dt, da, de = decl[c["name"]]
            attrs = sql[de:c["span"][1]]
            refused_auto = bool(re.search(r"\bauto_increment\b|\bgenerated\s+(?:always|by\s+default)\s+as\s+identity\b", m[de:c["span"][1]], re.I))
            loose_auto = bool(re.search(r"\bauto_increment\b", dt, re.I)) or c["base"] in ("serial", "bigserial", "smallserial")
            if refused_auto or loose_auto:
                is_pk = single_pk == c["name"]
                rest = _clean_attrs(attrs, True)
                new = c["raw"] + (" INTEGER PRIMARY KEY AUTOINCREMENT" if is_pk else " INTEGER") + rest
                edits = [(c["span"][0], c["span"][1], new)]
                if is_pk and table_pk_item and not re.search(r"\bprimary\s+key\b", m[c["span"][0]:c["span"][1]], re.I):
                    edits.append((_item_start(m, t, table_pk_item), table_pk_item[1], ""))
                auto_done.add((tn, c["name"]))
                if refused_auto:
                    word = "AUTO_INCREMENT" if re.search(r"auto_increment", attrs, re.I) else "GENERATED ... AS IDENTITY"
                    foreign.append(word)
                    foreign_edits += edits
                else:
                    wrong_ids.append((t, c, dt, edits))
                continue
            cleaned = _clean_attrs(attrs, False)
            if cleaned != attrs:
                foreign_edits.append((de, c["span"][1], cleaned))
                if re.search(r"on\s+update", attrs, re.I):
                    foreign.append("ON UPDATE CURRENT_TIMESTAMP")
                if re.search(r"default\s+\w+\s*\(", attrs, re.I):
                    foreign.append("DEFAULT now()")
            if c["base"] == "enum" and c["args"]:
                vals = re.findall(r"'((?:[^']|'')*)'", sql[c["type_span"][0]:c["type_span"][1]])
                foreign_edits.append((c["type_span"][0], c["type_span"][1],
                                      f"TEXT CHECK ({S._bare(c['raw'])} IN ({', '.join(chr(39) + v + chr(39) for v in vals)}))"))
                foreign.append("ENUM(...)")

        # ---- KEY / INDEX inside CREATE TABLE, UNIQUE KEY name (...), table options (ENGINE=, CHARSET= ...)
        idx_after = []
        for ix in t["indexes"]:
            s_, e_ = ix["span"]
            foreign_edits.append((_item_start(m, t, (s_, e_)), e_, ""))
            if ix["kind"] in ("key", "index") and ix["cols"]:
                idx_after.append(f"CREATE INDEX {('ix_' + t['name'] + '_' + '_'.join(ix['cols']))[:60]} ON {S._bare(t['raw'])} "
                                 f"({', '.join(ix['cols'])});")
            foreign.append(f"{ix['kind'].upper()} inside CREATE TABLE")
        for s_, e_ in [(a, b) for a, b in t["items"] if re.match(r"\s*(?:constraint\s+\S+\s+)?unique\s+(key|index)\b", m[a:b], re.I)]:
            um = re.match(r"\s*(?:constraint\s+\S+\s+)?unique\s+(key|index)\s*(?:" + S.NAME + r"\s*)?(?=\()", m[s_:e_], re.I)
            if um:
                foreign_edits.append((s_, s_ + um.end(), "UNIQUE "))
                foreign.append("UNIQUE KEY")
        for om in re.finditer(r"\s*,?\s*\b(?:default\s+)?(?:engine|character\s+set|charset|collate|auto_increment|row_format|comment|"
                              r"tablespace)\b\s*=?\s*(?:'x*'|[\w$]+)", t["options"], re.I):
            if re.match(r"\s*,?\s*(?:strict|without)\b", om.group(0), re.I):
                continue
            a = t["opt_at"] + om.start()
            foreign_edits.append((a, a + len(om.group(0)), ""))
            foreign.append(re.sub(r"\s*=.*", "=", om.group(0).strip(" ,")).upper().replace("DEFAULT ", ""))
        if idx_after:
            at = t["stmt_end"]
            lead = "" if at > 0 and sql[at - 1:at] == ";" else ";"
            foreign_edits.append((at, at, lead + "\n" + "\n".join(idx_after)))

        # ---- STRICT: only INT, INTEGER, REAL, TEXT, BLOB, ANY
        if strict:
            allowed = {x.upper() for x in R.get("strict", {}).get("types", [])}
            bad = []
            for c in t["cols"]:
                dt, da, de = decl[c["name"]]
                if dt.upper() in allowed or c in [w[1] for w in wrong_ids]:
                    continue
                aff = affinity(dt)
                low = c["base"]
                new = {"INTEGER": "INTEGER", "TEXT": "TEXT", "BLOB": "BLOB", "REAL": "REAL"}.get(aff) or \
                    ("TEXT" if low in DATEY or low in ("json", "jsonb", "uuid", "string") else "INTEGER" if low in ("boolean", "bool")
                     else "REAL" if low in MONEYISH else "ANY")
                if aff == "INTEGER" and dt.upper() != "INT":
                    new = "INTEGER"
                bad.append((c, dt, new, (da, de)))
            if bad:
                add("error", "sqlite_strict_type", f"{t['raw']} is STRICT, and STRICT takes only INT, INTEGER, REAL, TEXT, BLOB, ANY",
                    f"{t['raw']} STRICT hai, aur STRICT sirf INT, INTEGER, REAL, TEXT, BLOB, ANY leta hai",
                    ", ".join(f"{c['raw']} {dt}" for c, dt, _n, _s in bad),
                    f"A STRICT table refuses every other type name (lab: unknown datatype for {t['name']}.{bad[0][0]['name']}: "
                    f"\"{bad[0][1].upper()}\", on 3.53 and 3.45). Written in its words: "
                    + ", ".join(f"{dt} -> {n}" for _c, dt, n, _s in bad) + ". A length (VARCHAR(n)) becomes a CHECK (length(col) <= n) "
                    "if it matters; dates are TEXT in 'YYYY-MM-DD' shape; DECIMAL becomes REAL (or an INTEGER of paise for exact money).",
                    "STRICT table baaki type naam mana karti hai (lab: unknown datatype). Iske shabdon mein: "
                    + ", ".join(f"{dt} -> {n}" for _c, dt, n, _s in bad) + ".",
                    [(sp[0], sp[1], n) for _c, _dt, n, sp in bad])

        # ---- an id that doesn't number itself (lab: NULL ids), and other keys that take NULL
        if single_pk and not no_rowid:
            c = S._col(t, single_pk)
            if c and c not in [w[1] for w in wrong_ids] and (tn, c["name"]) not in auto_done:
                dt, da, de = decl[c["name"]]
                desc = re.search(r"\bprimary\s+key\s+desc\b", m[de:c["span"][1]], re.I)
                intish = affinity(dt) == "INTEGER"
                if (intish and dt.upper() != "INTEGER") or (dt.upper() == "INTEGER" and desc):
                    edits = [(da, de, "INTEGER")] if dt.upper() != "INTEGER" else []
                    if desc:
                        a = de + desc.start()
                        k = m.lower().index("desc", a)
                        edits.append((k - 1, k + 4, ""))
                    wrong_ids.append((t, c, dt + (" PRIMARY KEY DESC" if desc else ""), edits))
        for tt, c, dt, edits in [w for w in wrong_ids if w[0] is t]:
            surrogate = c["name"] == "id" or c["name"] == S._singular(tt["name"]) + "_id" or bool(re.search(r"auto_increment|serial", dt, re.I))
            refused = bool(re.search(r"\bautoincrement\b", m[c["span"][0]:c["span"][1]], re.I))   # lab: 'AUTOINCREMENT is only allowed on an INTEGER PRIMARY KEY'
            add("error" if refused else "wrong" if surrogate else "check", "sqlite_rowid_pk",
                f"{c['raw']} {dt.upper()} is not SQLite's self-numbering id: new rows get NULL ids",
                f"{c['raw']} {dt.upper()} SQLite ka apne-aap ginti wala id nahi: nayi rows ka id NULL",
                f"{ev_head} ({c['raw']} {dt} ... PRIMARY KEY)",
                f"On SQLite only a column declared exactly INTEGER PRIMARY KEY becomes the rowid that numbers itself. {dt.upper()} is an "
                f"ordinary column, so an INSERT that leaves the id out stores NULL, and a NULL key doesn't clash with another NULL "
                f"(lab: two INSERTs gave two rows with id NULL" + (", in a STRICT table 'NOT NULL constraint failed'" if strict else "")
                + ("; with AUTOINCREMENT the CREATE TABLE itself stops: 'AUTOINCREMENT is only allowed on an INTEGER PRIMARY KEY'" if refused else "")
                + "). Declare it INTEGER PRIMARY KEY" + (" AUTOINCREMENT (it never hands out a deleted row's id again, like "
                                                         "AUTO_INCREMENT / IDENTITY; plain INTEGER PRIMARY KEY is enough when reuse is fine)"
                                                         if re.search(r"auto_increment|serial|identity", dt, re.I) else "")
                + ". If the app always sends its own ids, this only matters for the NULLs.",
                f"SQLite pe sirf theek INTEGER PRIMARY KEY likha column rowid banke apne-aap ginta hai. {dt.upper()} aam column hai: id "
                f"chhod ke INSERT karo to NULL jaata hai (lab: do rows, dono id NULL). INTEGER PRIMARY KEY likho.", edits)
        pk_cols = [] if (strict or no_rowid) else [n for n in t["pk"] if not (n == single_pk and S._col(t, n) and
                                                                               (declared(sql, m, S._col(t, n))[0].upper() == "INTEGER" or
                                                                                any(w[1]["name"] == n for w in wrong_ids if w[0] is t)))]
        open_cols = [S._col(t, n) for n in pk_cols if S._col(t, n) and not re.search(r"\bnot\s+null\b", m[S._col(t, n)["span"][0]:S._col(t, n)["span"][1]], re.I)]
        if open_cols:
            edits = [(decl[c["name"]][2], decl[c["name"]][2], " NOT NULL") for c in open_cols]
            add("check", "sqlite_pk_null", f"{t['raw']}'s PRIMARY KEY ({', '.join(t['pk'])}) accepts NULL on SQLite",
                f"{t['raw']} ki PRIMARY KEY ({', '.join(t['pk'])}) SQLite pe NULL le leti hai", f"{ev_head} ... PRIMARY KEY ({', '.join(t['pk'])})",
                "In an ordinary SQLite table a primary key that isn't the INTEGER rowid accepts NULL (an old bug kept for compatibility), "
                "and NULLs never clash, so rows without a key pile up (lab: two rows with a NULL key, for a TEXT key and for a two-column "
                "key). Add NOT NULL to the key's columns" + (" (or make the table STRICT / WITHOUT ROWID)" if True else "") + ".",
                "SQLite ki aam table mein INTEGER rowid ke alawa primary key NULL le leti hai, aur NULL aapas mein nahi takraate (lab: do "
                "NULL key wali rows). Key ke columns pe NOT NULL lagao.", edits)

        # ---- names SQLite reads as its own words
        res = set(R.get("reserved", []))
        for kind_, raw, nm, span in [("table", t["raw"], t["name"], t["name_span"])] + [("column", c["raw"], c["name"], c["name_span"]) for c in t["cols"]]:
            if raw[0] in "`\"[" or "." in raw or nm not in res:
                continue
            add("error", "reserved_name", f"{raw} is a reserved word in SQLite: the {kind_} can't be named that bare",
                f"{raw} SQLite mein reserved word hai: {kind_} ka naam bina quote ke ye nahi ho sakta", f"{ev_head} ... {raw}",
                f"SQLite reads {raw} as part of the language (lab: near \"{nm}\": syntax error). Pick another name ({S._rename(nm, kind_)}), "
                f"or put it in double quotes everywhere it is used: \"{raw}\". A new name is kinder to every query that comes later.",
                f"SQLite {raw} ko SQL ka shabd padhta hai (syntax error). Doosra naam rakho ({S._rename(nm, kind_)}) ya har jagah \"{raw}\".",
                [(span[0], span[1], f'"{raw}"')])

        # ---- no primary key
        if not t["pk"] and not d["later"]["pk"].get(tn) and not t["temporary"]:
            first = t["items"][0][0] if t["items"] else t["open"] + 1
            idc = S._col(t, "id")
            edits = [(decl["id"][1], decl["id"][2] if decl["id"][0] else decl["id"][1], "INTEGER PRIMARY KEY")] if idc and decl["id"][0] else \
                ([] if idc else [(first, first, "id INTEGER PRIMARY KEY,\n  ")])
            add("check", "no_primary_key", f"{t['raw']} has no PRIMARY KEY: nothing says which row is which",
                f"{t['raw']} mein PRIMARY KEY nahi: kaunsi row kaunsi hai, kuch nahi batata", ev_head + " (...)",
                "Without a primary key the table accepts the same row twice, an UPDATE or DELETE of 'that one row' can't be written "
                "safely and other tables can't point at it. SQLite gives every row a hidden rowid, but it isn't part of your design, and "
                "VACUUM may renumber it when no column is its INTEGER PRIMARY KEY. Name the rows: id INTEGER PRIMARY KEY (it numbers "
                "itself), or PRIMARY KEY (a, b) NOT NULL columns when two columns together name the row.",
                "Primary key ke bina same row do baar aa sakti hai aur doosri tables isse jud nahi sakti. SQLite ka chhupa rowid design ka "
                "hissa nahi. id INTEGER PRIMARY KEY likho.", edits)

        # ---- columns: what SQLite stores, whatever the type says
        unenf, dates, money = [], [], []
        for c in t["cols"]:
            dt, da, de = decl[c["name"]]
            nm = c["name"]
            ev = f"{t['raw']}.{c['raw']} {dt}"
            aff = affinity(dt)
            has_check = bool(re.search(r"\bcheck\s*\(", m[de:c["span"][1]], re.I))
            if re.search(r"\bstring\b", dt, re.I):
                add("wrong", "sqlite_affinity", f"{c['raw']} STRING is not a text type on SQLite: '012345' is stored as 12345",
                    f"{c['raw']} STRING SQLite pe text type nahi: '012345' 12345 ban jaata hai", ev,
                    "SQLite reads a type name for the letters INT, CHAR, CLOB, TEXT, BLOB, REAL, FLOA, DOUB; STRING has none, so the "
                    "column gets NUMERIC affinity and any text that looks like a number is turned into one (lab: '012345' came back as "
                    "the integer 12345, the leading 0 gone). TEXT keeps it as written.",
                    "STRING mein SQLite ke pehchane akshar nahi, isliye column NUMERIC banta hai aur number jaisa text number ban jaata hai "
                    "(lab: '012345' -> 12345). TEXT likho.", [(da, de, "TEXT")])
                continue
            if c in [w[1] for w in wrong_ids if w[0] is t]:
                continue
            if S.CODEISH.search(nm) and aff in ("INTEGER", "NUMERIC", "REAL") and not re.search(r"(^|_)(id|ids|count|type|verified|attempts)$", nm):
                add("check", "code_as_number", f"{c['raw']} is a code made of digits, stored as a number ({dt.upper()})",
                    f"{c['raw']} digits ka code hai, number ({dt.upper()}) mein rakha hai", ev,
                    f"Phone numbers, PIN codes and account numbers are labels, not quantities. A {dt.upper()} column has {aff} "
                    "affinity on SQLite and turns '09876543210' into 9876543210 (lab: the leading 0 was gone). Store them as TEXT.",
                    "Phone, PIN code label hain. SQLite pe number column '09876543210' ko 9876543210 bana deta hai (lab). TEXT rakho.",
                    [(da, de, "TEXT")])
                continue
            if aff == "TEXT" and (S.MONEY.search(nm) or S.COUNTISH.search(nm)) and not S.CODEISH.search(nm) and \
                    not re.search(r"(^|_)(type|types|mode|status|name|names|note|notes|unit|currency|label|code|method|text|by|desc|"
                                  r"description|format|terms|plan)$", nm):
                new = "INTEGER" if S.COUNTISH.search(nm) and not S.MONEY.search(nm) else "INTEGER"
                add("check", "number_as_text", f"{c['raw']} holds a number but is stored as text ({dt.upper()})",
                    f"{c['raw']} mein number hai par text ({dt.upper()}) mein rakha hai", ev,
                    "A TEXT column keeps '10' as text, so it sorts and compares as text (lab: max of '10', '9', '100' was '9') and SUM "
                    "silently skips what isn't a number (lab: '10' + '5' + 'abc' summed to 15.0, no error). Store it as a number: "
                    + ("INTEGER." if not S.MONEY.search(nm) else "INTEGER of paise (or REAL, rounded when shown)."),
                    "TEXT column '10' ko text rakhta hai: max '9' aata hai aur SUM bina error 'abc' chhod deta hai (lab). Number rakho.",
                    [(da, de, new)] if not S.MONEY.search(nm) else [])
                continue
            if S._list_col(t, c, add, ev):
                continue
            if S.MONEY.search(nm) and not re.search(r"(^|_)rate$", nm) and (c["base"] in MONEYISH or aff == "REAL"):
                money.append((c, dt))
                continue
            if c["base"] in DATEY and aff == "NUMERIC" and not has_check:
                dflt = m[de:c["span"][1]].lower()
                fn = "date" if c["base"] == "date" and "current_timestamp" not in dflt else "time" if c["base"] in ("time", "timetz") else "datetime"
                dates.append((c, dt, fn, (da, de)))
                continue
            if not strict and not has_check:
                lm = re.search(r"\b(?:n?var)?char(?:acter)?(?:\s+varying)?2?\s*\(\s*(\d+)\s*\)", dt, re.I)
                if lm:
                    unenf.append((c, f"length({S._bare(c['raw'])}) <= {lm.group(1)}", f"{dt.upper()}"))
                elif re.search(r"\bunsigned\b", dt, re.I):
                    unenf.append((c, f"{S._bare(c['raw'])} >= 0", f"{dt.upper()}"))
        if unenf:
            add("check", "sqlite_unenforced", f"{t['raw']}: SQLite doesn't enforce {', '.join(sorted({u[2].split('(')[0] for u in unenf}))}",
                f"{t['raw']}: SQLite {', '.join(sorted({u[2].split('(')[0] for u in unenf}))} lagu nahi karta",
                f"{t['raw']}: " + ", ".join(f"{c['raw']} {w}" for c, _x, w in unenf),
                "SQLite accepts these type words and checks nothing with them (lab: 'abcdef' went into VARCHAR(3) and kept 6 letters; -5 "
                "went into INT UNSIGNED). If the limit matters, say it as a rule SQLite does check: "
                + "; ".join(f"{c['raw']}: CHECK ({x})" for c, x, _w in unenf) + " (lab: then 'CHECK constraint failed').",
                "SQLite ye shabd le leta hai par kuch check nahi karta (lab: VARCHAR(3) mein 'abcdef', UNSIGNED mein -5). Limit zaroori "
                "hai to CHECK likho: " + "; ".join(f"CHECK ({x})" for _c, x, _w in unenf) + ".",
                [(c["span"][1], c["span"][1], f" CHECK ({x})") for c, x, _w in unenf])
        if dates:
            add("check", "sqlite_date_type", f"{t['raw']}: SQLite has no date type, so {', '.join(c['raw'] for c, *_ in dates)} take any shape",
                f"{t['raw']}: SQLite mein date type nahi, isliye {', '.join(c['raw'] for c, *_ in dates)} koi bhi shape le lete hain",
                ", ".join(f"{c['raw']} {dt}" for c, dt, _f, _s in dates),
                "DATE, DATETIME and TIMESTAMP are only names on SQLite: the column gets NUMERIC affinity and takes anything (lab: '2024', "
                "20240105 and '2024-1-5' all went in), and dates of two shapes then sort and compare wrongly ('2024-1-20' is outside "
                "'2024-01-10' to '2024-02-01'). Keep dates as TEXT in one shape and let SQLite check it: "
                + "; ".join(f"{c['raw']} TEXT CHECK ({S._bare(c['raw'])} IS {f}({S._bare(c['raw'])}))" for c, _dt, f, _s in dates)
                + " (lab: '2024-1-5' and '2024-02-30' refused, NULL and CURRENT_TIMESTAMP accepted; a 'T' between date and time is refused "
                "by the datetime one, so send 'YYYY-MM-DD HH:MM:SS').",
                "SQLite pe DATE / DATETIME sirf naam hain: column NUMERIC banta hai aur kuch bhi le leta hai (lab). Dates TEXT mein ek shape "
                "mein rakho aur CHECK lagao: " + "; ".join(f"CHECK ({S._bare(c['raw'])} IS {f}({S._bare(c['raw'])}))" for c, _dt, f, _s in dates) + ".",
                [e for c, _dt, f, sp in dates for e in ((sp[0], sp[1], "TEXT"),
                                                        (c["span"][1], c["span"][1], f" CHECK ({S._bare(c['raw'])} IS {f}({S._bare(c['raw'])}))"))])
        if money:
            add("check", "sqlite_money", f"{t['raw']}: SQLite has no exact decimal, so {', '.join(c['raw'] for c, _ in money)} hold binary fractions",
                f"{t['raw']}: SQLite mein exact decimal nahi, isliye {', '.join(c['raw'] for c, _ in money)} binary fraction hain",
                ", ".join(f"{c['raw']} {dt}" for c, dt in money),
                "On SQLite DECIMAL and NUMERIC are only names: 1.10 is stored as the binary REAL 1.1, the same as REAL, FLOAT and DOUBLE "
                "(lab: DECIMAL(10,2) given 0.1 + 0.2 held 0.30000000000000004, and = 0.3 found nothing). Totals of many amounts drift by "
                "tiny fractions. For exact money keep whole paise / cents in an INTEGER column (amount_paise INTEGER: 1999 for 19.99) "
                "and divide by 100 only to show it; or keep REAL and ROUND(x, 2) every total and never compare amounts with =.",
                "SQLite pe DECIMAL sirf naam hai: 1.10 binary REAL ban jaata hai (lab: 0.1 + 0.2 = 0.30000000000000004). Exact paise ke "
                "liye INTEGER mein paise rakho (1999 = 19.99) aur dikhate waqt 100 se bhaag do; ya REAL mein har total ROUND(x, 2) karo.")

        # ---- links
        for fk in t["fks"]:
            parent = tables.get(fk["parent"])
            ev = f"{t['raw']} ({', '.join(fk['cols'])}) REFERENCES {fk['parent']}"
            if parent is None:
                continue
            pcols = fk["pcols"] or parent["pk"]
            if len(pcols) != len(fk["cols"]):
                continue
            keyed = pcols == parent["pk"] or pcols in parent["uniques"] or pcols == d["later"]["pk"].get(parent["name"]) or \
                any(pcols == ix for ix in d["later"]["indexes"].get(parent["name"], []) if re.search(
                    rf"create\s+unique\s+index[^;]*\bon\s+{re.escape(parent['name'])}\b", m, re.I))
            if not keyed:
                p_col = S._col(parent, pcols[0]) if len(pcols) == 1 else None
                add("error", "fk_target", f"{fk['parent']}.{', '.join(pcols)} is not a primary key or UNIQUE",
                    f"{fk['parent']}.{', '.join(pcols)} na primary key hai na UNIQUE", ev,
                    "A foreign key must point at something that names exactly one parent row. SQLite creates the table anyway, and then, "
                    f"once foreign keys are on, every INSERT into {t['raw']} fails (lab: foreign key mismatch - \"{t['name']}\" referencing "
                    f"\"{fk['parent']}\"). Point it at {fk['parent']}'s primary key, or make {', '.join(pcols)} UNIQUE.",
                    "Foreign key ko aisi cheez pe point karna chahiye jo ek hi parent row batati ho. SQLite table bana deta hai, phir foreign "
                    "keys chalu hote hi har INSERT fail (lab: foreign key mismatch). Primary key pe point karo, ya UNIQUE banao.",
                    [(p_col["span"][1], p_col["span"][1], " UNIQUE")] if p_col else [])
                continue
            for cc, pc in zip(fk["cols"], pcols):
                ccol, pcol = S._col(t, cc), S._col(parent, pc)
                if not ccol or not pcol:
                    continue
                ac, ap = affinity(decl[cc][0]), affinity(declared(sql, m, pcol)[0])
                if pcol in [w[1] for w in wrong_ids if w[0] is parent] or any(w[1] is pcol for w in wrong_ids):
                    ap = "INTEGER"
                num = {"INTEGER", "REAL", "NUMERIC"}
                if (ac in num) == (ap in num) or "BLOB" in (ac, ap):
                    continue
                new = "INTEGER" if ap in num else "TEXT"
                add("check", "fk_type", f"{t['raw']}.{cc} is {decl[cc][0].upper()} but {fk['parent']}.{pc} is {declared(sql, m, pcol)[0].upper()}",
                    f"{t['raw']}.{cc} {decl[cc][0].upper()} hai par {fk['parent']}.{pc} {declared(sql, m, pcol)[0].upper()}", ev,
                    "The two ends of a link should hold the same kind of value. SQLite doesn't compare them (lab: a TEXT child of an INTEGER "
                    "parent took '1' and stored it as text), and joins still match because the column converts the other side; but values "
                    "of two types are two different values to DISTINCT, UNION and GROUP BY (lab: 1 and '1' were two rows), and to anything "
                    f"compared through an expression. Make {cc} {new}, like {fk['parent']}.{pc}.",
                    f"Link ke dono sire ek type ke hon. SQLite compare nahi karta (lab: TEXT child mein '1' text rakha), aur DISTINCT / UNION "
                    f"mein 1 aur '1' do alag values hain. {cc} ko {new} karo.", [(decl[cc][1], decl[cc][2], new)])
        missing = [fk for fk in t["fks"] if fk["cols"] and not S._led(t, d, fk["cols"][0])]
        stmts = [f"CREATE INDEX {('ix_' + t['name'] + '_' + '_'.join(fk['cols']))[:60]} ON {S._bare(t['raw'])} ({', '.join(fk['cols'])});"
                 for fk in missing]
        at = t["stmt_end"]
        lead = "" if at > 0 and sql[at - 1:at] == ";" else ";"
        for k, (fk, stmt) in enumerate(zip(missing, stmts)):
            add("check", "fk_no_index", f"{t['raw']}.{', '.join(fk['cols'])} links to {fk['parent']} but has no index on SQLite",
                f"{t['raw']}.{', '.join(fk['cols'])} {fk['parent']} se juda hai par SQLite pe index nahi",
                f"{t['raw']} ({', '.join(fk['cols'])}) REFERENCES {fk['parent']}",
                f"SQLite makes no index for a foreign key column (lab: none after CREATE TABLE; EXPLAIN QUERY PLAN said SCAN {t['name']} for "
                f"WHERE {fk['cols'][0]} = ?, and SEARCH ... USING INDEX once it was added). Without it every lookup of a "
                f"{S._singular(fk['parent'])}'s {t['raw']} and, with foreign keys on, every DELETE in {fk['parent']} reads the whole of "
                f"{t['raw']}. Worth adding: {stmt}",
                f"SQLite foreign key column pe index nahi banata (lab: SCAN). Iske bina har lookup aur parent ka har DELETE poori {t['raw']} "
                f"padhta hai. Jodo: {stmt}",
                [(at, at, lead + "".join("\n" + x for x in stmts))] if k == 0 else [])

        S._one_fact(t, add)

    # ---- two primary keys (refused on SQLite too)
    for tn in d["order"]:
        t = tables[tn]
        if len(t["pk_items"]) > 1:
            add("error", "two_primary_keys", f"{t['raw']} has {len(t['pk_items'])} PRIMARY KEYs: a table has only one",
                f"{t['raw']} mein {len(t['pk_items'])} PRIMARY KEY hain: table ki ek hi hoti hai",
                " / ".join(sql[a:b] for a, b in t["pk_items"]),
                f"A table has one primary key (lab: table \"{t['name']}\" has more than one primary key). If the row is named by the two "
                "columns together, write one PRIMARY KEY (a, b); if the other column must only be unique, make it UNIQUE.",
                "Table ki ek hi primary key hoti hai. Dono milke row ko naam dete hain to PRIMARY KEY (a, b); doosra sirf unique ho to UNIQUE.")

    # ---- foreign keys are off unless each connection turns them on
    if any(tables[tn]["fks"] for tn in d["order"]) and not re.search(r"\bpragma\s+foreign_keys\s*=\s*(?:on|1|true|yes)\b", m, re.I):
        n = sum(len(tables[tn]["fks"]) for tn in d["order"])
        add("check", "sqlite_fk_off", f"SQLite won't check {'this' if n == 1 else 'these'} {n} foreign key{'s' if n > 1 else ''} unless the "
                                      "connection turns them on",
            f"Connection chalu na kare to SQLite {'ye' if n == 1 else 'ye'} {n} foreign key check nahi karega", "REFERENCES ...",
            "SQLite stores FOREIGN KEY / REFERENCES but checks them only on a connection that has run PRAGMA foreign_keys = ON, and it is off "
            "by default (lab: a child pointing at a missing parent was inserted; with the PRAGMA, FOREIGN KEY constraint failed). It lasts "
            "for that connection only, so the app must run it every time it opens the database (outside a transaction).",
            "SQLite foreign key rakhta hai par check sirf us connection pe karta hai jisne PRAGMA foreign_keys = ON chalaya; default band "
            "hai (lab: galat parent wali row chali gayi). App har connection kholte hi ye chalaye.",
            [(0, 0, "PRAGMA foreign_keys = ON;\n")])

    # ---- other databases' table words SQLite refuses (one finding, every rewrite)
    if foreign:
        seen, what = set(), []
        for w in foreign:
            if w not in seen:
                seen.add(w)
                what.append(w)
        add("error", "ddl_dialect", f"SQLite refuses this table syntax from another database: {', '.join(what[:5])}",
            f"SQLite doosre database ka ye table syntax nahi maanta: {', '.join(what[:5])}", ", ".join(what),
            "These are MySQL/MariaDB or PostgreSQL words, and SQLite stops at them (lab: near \"AUTO_INCREMENT\" / \"IDENTITY\" / \"=\" / "
            "\"KEY\" / \"(\": syntax error). In SQLite's words: an auto-numbered id is INTEGER PRIMARY KEY AUTOINCREMENT; table options "
            "(ENGINE=, CHARSET=, COLLATE=) are dropped; an index is its own CREATE INDEX; ENUM is TEXT with a CHECK; DEFAULT now() is "
            "DEFAULT CURRENT_TIMESTAMP (UTC text); ON UPDATE CURRENT_TIMESTAMP has no equivalent in a column (a trigger can keep it "
            "current: CREATE TRIGGER ... AFTER UPDATE ON t BEGIN UPDATE t SET updated_at = CURRENT_TIMESTAMP WHERE rowid = NEW.rowid; END).",
            "Ye MySQL/PostgreSQL ke shabd hain, SQLite inpe ruk jaata hai. SQLite mein: id INTEGER PRIMARY KEY AUTOINCREMENT, ENGINE= / "
            "CHARSET= hatao, index alag CREATE INDEX, ENUM ki jagah TEXT CHECK, DEFAULT now() ki jagah DEFAULT CURRENT_TIMESTAMP.",
            S._clean_edits(foreign_edits))
    return out
