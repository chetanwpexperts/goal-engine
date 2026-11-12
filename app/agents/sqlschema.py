"""Reading a table design (night 6 of the curriculum): CREATE TABLE scripts, checked for what makes the database refuse them,
what silently loses or corrupts data, and what makes the design hard to keep right (fundamentals 'table_keys', 'column_types'
and 'one_fact_one_place' in app/dbknow/core/query_fundamentals.json; engine facts in the dialect files' schema_rules).

How DBaba reads a design, on every engine:
- KEYS: every table should say what one row is (a PRIMARY KEY); a link (FOREIGN KEY) must point at a key of a table that
  already exists, with the same type, and is read from the child side, so it needs an index there.
- TYPES: a column's type is a promise about its values. Money must be exact (DECIMAL / NUMERIC, never FLOAT / DOUBLE / REAL);
  dates are DATE / DATETIME / TIMESTAMP, not text (text sorts '10/1' before '9/1' and accepts 30 February); amounts and
  counts are numbers, not text (text sorts '100' before '9'); codes that only look like numbers (phone, PIN code) are text
  (a number drops the leading 0 and a 10-digit phone overflows INT); text that can hold any language and emoji needs
  utf8mb4 on MySQL/MariaDB (their 'utf8' is the old 3-byte utf8mb3).
- ONE FACT IN ONE PLACE: a list in one column (tags, *_ids), numbered copies of a column (phone1, phone2) and a parent's
  details copied next to its id (customer_id + customer_name) all let the same fact disagree with itself.
- THE ENGINE'S OWN LIMITS (from the dialect files, rule B): types and options each engine refuses, reserved words, MyISAM's
  ignored foreign keys, TIMESTAMP's 1970-2038 range and the 65,535-byte row on MySQL/MariaDB, PostgreSQL's foreign keys
  without an index.
Every rule was reproduced on MariaDB 10.11 and PostgreSQL 16 (tests/practice/test_night6.py has the lab table).
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.agents.sqlroutine import _mask

DIALECTS = Path(__file__).resolve().parent.parent / "dbknow" / "dialects"
PROVEN = {k: ["mariadb", "postgresql"] for k in (
    "money_float", "date_as_text", "number_as_text", "code_as_number", "no_primary_key", "fk_no_index", "utf8mb3", "myisam_fk",
    "ddl_dialect", "two_primary_keys", "fk_type", "fk_order", "fk_target", "varchar_length", "reserved_name", "row_too_big",
    "text_key", "timestamp_range", "over_wide", "list_in_column", "repeating_group", "parent_copy")}

NAME = r"(?:`[^`]+`|\"[^\"]+\"|[A-Za-z_][\w$]*)"
CREATE_RE = re.compile(rf"\bcreate\s+(?:(?:global\s+|local\s+)?temp(?:orary)?\s+)?table\s+(?:if\s+not\s+exists\s+)?((?:{NAME}\.)?{NAME})\s*\(",
                       re.I)
TYPE_HEADS = ["double precision", "character varying", "timestamp with time zone", "timestamp without time zone",
              "time with time zone", "time without time zone", "bit varying", "national character varying", "national char"]
CONSTRAINT_RE = re.compile(r"^\s*(constraint\s+\S+\s+)?(primary\s+key\b|unique(?=\s*(?:\(|key\b|index\b|nulls\b|[`\"\w]+\s*\())|"
                           r"foreign\s+key\b|check(?=\s*\()|(?:key|index|fulltext|spatial)(?=\s*(?:\(|[`\"\w]+\s*\(|using\b))|"
                           r"exclude(?=\s*(?:using\b|\())|like\s|period\s+for\b)", re.I)
MONEY = re.compile(r"(^|_)(price|prices|amount|amt|total|subtotal|cost|costs|salary|salaries|fee|fees|balance|wage|wages|"
                   r"payment|charges?|tax|discount|rate|revenue|income|expense|budget|mrp|bill|commission|bonus|refund|fine)(_|$)|"
                   r"(price|amount|salary|balance)$", re.I)
COUNTISH = re.compile(r"(^|_)(qty|quantity|stock|count|age|marks|score|units|seats|pages|points|visits)(_|$)", re.I)
DATEISH = re.compile(r"(^|_)(date|dob|birthday|birthdate|born|doj|joined|joining|hired|deadline|due|expiry|expires|expiry_date|"
                     r"valid_from|valid_to|valid_until|on)$|^(date|dob|birthday|created|updated|modified|deleted)(_|$)|"
                     r"(^|_)(at|timestamp|datetime)$|_date_|^date_", re.I)
DAY_ONLY = re.compile(r"(^|_)(date|dob|birthday|birthdate|born|doj|on|due|deadline|expiry|joining)(_|$)|^date", re.I)
OLD_OR_FAR = re.compile(r"(^|_)(dob|birth\w*|born|birthday|expiry|expires|valid_until|valid_to|maturity|retire\w*|founded|established|"
                        r"anniversary|manufactured|mfg)(_|$)", re.I)
CODEISH = re.compile(r"(^|_)(phone|mobile|cell|contact_no|contact_number|whatsapp|landline|fax|pin|pincode|pin_code|zip|zipcode|"
                     r"zip_code|postal|postal_code|postcode|aadhaar|aadhar|pan|ifsc|account_no|account_number|card_no|"
                     r"card_number|otp|isbn|barcode|ean|upc|gstin|gst_no)(_|$)", re.I)
SHORT_FIELD = {"name": 100, "first_name": 50, "last_name": 50, "middle_name": 50, "full_name": 150, "email": 254, "city": 100,
               "state": 100, "country": 100, "phone": 20, "mobile": 20, "status": 30, "gender": 20, "code": 50, "username": 50,
               "user_name": 50, "title": 200, "pincode": 10, "zip": 10, "sku": 50}
LISTISH = re.compile(r"(^|_)(\w+_ids|ids|tags|skills|categories|emails|phones|hobbies|roles|permissions|items|members|"
                     r"subjects|languages|sizes|colors|colours)$", re.I)
TEXTY = {"char", "varchar", "character varying", "character", "nchar", "nvarchar", "text", "tinytext", "mediumtext", "longtext",
         "string", "national char", "national character varying", "citext"}
FLOATY = {"float", "double", "double precision", "real", "float4", "float8"}
INTY = {"int", "integer", "bigint", "smallint", "mediumint", "tinyint", "int2", "int4", "int8", "numeric", "decimal", "number"}


# ------------------------------------------------------------------------------------------------ engine facts (rule B)
@lru_cache(maxsize=8)
def rules(engine: str) -> dict:
    return json.loads((DIALECTS / f"{engine}.json").read_text(encoding="utf-8")).get("schema_rules", {})


def _fam_rules(fam: str | None) -> dict:
    from app.agents.sqlbasics import CHOSEN

    chosen = CHOSEN.get()
    if fam == "postgresql":
        return rules("postgresql")
    return rules(chosen if chosen in ("mysql", "mariadb") else "mariadb")


def _family(sql: str) -> tuple[str | None, bool]:
    """(family, named): the database the person named or chose (rule A), else the one the script's syntax shows."""
    from app.agents.sqlbasics import CHOSEN, engine_seen
    from app.dbknow import engines as E

    chosen = CHOSEN.get()
    if chosen:
        return E.family(chosen), True
    my = any(re.search(p, sql, re.I) for p in rules("mariadb").get("marks", []))
    pg = any(re.search(p, sql, re.I) for p in rules("postgresql").get("marks", []))
    seen = engine_seen(sql)
    if my and not pg:
        return "mysql", False
    if pg and not my:
        return "postgresql", False
    return seen, False


def _uq(n: str) -> str:
    return (n or "").split(".")[-1].strip('`"').lower()


def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def _short(t: str, n: int = 110) -> str:
    t = _flat(t)
    return t if len(t) <= n else t[:n - 3] + "..."


def is_schema(text: str) -> bool:
    """Does this text make tables? (CREATE TABLE ... ( ... ), possibly after DROP TABLE / SET / USE lines)."""
    m = _mask(text or "")
    return bool(re.match(r"^\s*(?:(?:drop\s+table|set|use|create\s+(?:database|schema))\b[^;]*;\s*)*create\s+(?:(?:global\s+|local\s+)?"
                         rf"temp(?:orary)?\s+)?table\s+(?:if\s+not\s+exists\s+)?(?:{NAME}\.)?{NAME}\s*\(", m, re.I))


# ------------------------------------------------------------------------------------------------ reading a design
def _close(m: str, i: int) -> int:
    """Index of the ')' that closes the '(' at i."""
    d = 0
    for k in range(i, len(m)):
        if m[k] == "(":
            d += 1
        elif m[k] == ")":
            d -= 1
            if d == 0:
                return k
    return len(m)


def _split_items(m: str, a: int, b: int) -> list[tuple[int, int]]:
    """Top-level comma-separated items of m[a:b] as (start, end), spaces trimmed."""
    out, d, last = [], 0, a
    for k in range(a, b):
        c = m[k]
        if c == "(":
            d += 1
        elif c == ")":
            d -= 1
        elif c == "," and d == 0:
            out.append((last, k))
            last = k + 1
    out.append((last, b))
    res = []
    for s, e in out:
        while s < e and m[s].isspace():
            s += 1
        while e > s and m[e - 1].isspace():
            e -= 1
        if e > s:
            res.append((s, e))
    return res


def _names(text: str) -> list[str]:
    return [_uq(x) for x in re.findall(NAME, text or "") if x.lower() not in ("asc", "desc")]


def _key_cols(text: str) -> list[str]:
    """Columns in an index list '(a, b(10) DESC)' -> ['a', 'b']."""
    out = []
    for part in _split_items(text, 0, len(text)):
        piece = text[part[0]:part[1]]
        nm = re.match(NAME, piece.strip())
        if nm:
            out.append(_uq(nm.group(0)))
    return out


def _type(m: str, i: int):
    """The column type starting at i: (base, args, extra words, end). base is lower case with single spaces."""
    rest = m[i:]
    low = re.sub(r"\s+", " ", rest[:60].lower())
    base = None
    for h in TYPE_HEADS:
        if low.startswith(h):
            words = h.split()
            mm = re.match(r"\s*".join(map(re.escape, words)) + r"\b", rest, re.I)
            if mm:
                base, k = h, mm.end()
                break
    if base is None:
        mm = re.match(r"[A-Za-z_][\w]*", rest)
        if not mm:
            return None
        base, k = mm.group(0).lower(), mm.end()
    args = None
    am = re.match(r"\s*\(([^()]*)\)", rest[k:])
    if am:
        args = am.group(1).strip()
        k += am.end()
    zm = re.match(r"\s+with(?:out)?\s+time\s+zone\b", rest[k:], re.I)
    if zm and base in ("timestamp", "time"):
        base = base + (" with time zone" if "without" not in zm.group(0).lower() else " without time zone")
        k += zm.end()
    extra = []
    while True:
        um = re.match(r"\s+(unsigned|signed|zerofill)\b", rest[k:], re.I)
        if not um:
            break
        extra.append(um.group(1).lower())
        k += um.end()
    arr = re.match(r"\s*(\[\s*\d*\s*\])+", rest[k:])
    if arr:
        extra.append("[]")
        k += arr.end()
    return base, args, extra, i + k


def read_design(sql: str) -> dict:
    """Every CREATE TABLE in the script with its columns, keys, links and options (positions are in the original text), plus
    the CREATE INDEX / ALTER TABLE statements that add keys later."""
    m = _mask(sql)
    tables, order = {}, []
    for cm in CREATE_RE.finditer(m):
        open_ = cm.end() - 1
        close = _close(m, open_)
        semi = m.find(";", close)
        end = semi if semi >= 0 else len(m)
        nxt = CREATE_RE.search(m, close)
        if nxt and nxt.start() < end:
            end = nxt.start()
        t = {"raw": cm.group(1), "name": _uq(cm.group(1)), "start": cm.start(), "name_span": (cm.start(1), cm.end(1)), "open": open_, "close": close, "end": end,
             "stmt_end": semi + 1 if semi >= 0 and semi == end else end, "options": m[close + 1:end], "opt_at": close + 1,
             "temporary": bool(re.search(r"\btemp", m[cm.start():cm.end()], re.I)), "cols": [], "pk": [], "pk_items": [],
             "uniques": [], "indexes": [], "fks": [], "items": [], "checks": []}
        for s, e in _split_items(m, open_ + 1, close):
            item = m[s:e]
            t["items"].append((s, e))
            cm2 = CONSTRAINT_RE.match(item)
            if cm2:
                kind = re.sub(r"\s+", " ", cm2.group(2).lower()).strip()
                par = item.find("(")
                cols = _key_cols(item[par + 1:_close(item, par)]) if par >= 0 else []
                if kind == "primary key":
                    t["pk"] = cols
                    t["pk_items"].append((s, e))
                elif kind == "unique":
                    t["uniques"].append(cols)
                elif kind in ("key", "index", "fulltext", "spatial"):
                    t["indexes"].append({"cols": cols, "span": (s, e), "kind": kind})
                elif kind == "foreign key":
                    rm = re.search(rf"\breferences\s+((?:{NAME}\.)?{NAME})\s*(\(([^()]*)\))?", item, re.I)
                    if rm:
                        t["fks"].append({"cols": cols, "parent": _uq(rm.group(1)), "pcols": _key_cols(rm.group(3) or ""),
                                         "span": (s, e), "inline": False})
                elif kind == "check":
                    t["checks"].append((s, e))
                continue
            nm = re.match(NAME, item)
            if not nm:
                continue
            ty = _type(m, s + nm.end() + (len(item[nm.end():]) - len(item[nm.end():].lstrip())))
            if not ty:
                continue
            base, args, extra, tend = ty
            attrs = m[tend:e]
            col = {"raw": nm.group(0), "name": _uq(nm.group(0)), "quoted": nm.group(0)[0] in "`\"", "span": (s, e),
                   "name_span": (s, s + nm.end()), "type_span": (tend - len(m[s + nm.end():tend].lstrip()), tend), "base": base,
                   "args": args, "extra": extra, "attrs": attrs, "attrs_span": (tend, e)}
            col["type_text"] = sql[col["type_span"][0]:col["type_span"][1]]
            al = attrs.lower()
            col["not_null"] = bool(re.search(r"\bnot\s+null\b", al)) or "primary key" in re.sub(r"\s+", " ", al)
            col["auto"] = bool(re.search(r"\bauto_increment\b|\bgenerated\s+(always|by\s+default)\s+as\s+identity\b", al)) or \
                base in ("serial", "bigserial", "smallserial")
            if re.search(r"\bprimary\s+key\b", al):
                t["pk"] = t["pk"] or [col["name"]]
                t["pk_items"].append((s, e))
            if re.search(r"\bunique\b", al):
                t["uniques"].append([col["name"]])
            rm = re.search(rf"\breferences\s+((?:{NAME}\.)?{NAME})\s*(\(([^()]*)\))?", attrs, re.I)
            if rm:
                t["fks"].append({"cols": [col["name"]], "parent": _uq(rm.group(1)), "pcols": _key_cols(rm.group(3) or ""),
                                 "span": (s, e), "inline": True})
            cs = re.search(r"\b(?:character\s+set|charset)\s*=?\s*(\w+)", attrs, re.I)
            col["charset"] = cs.group(1).lower() if cs else None
            col["charset_span"] = (tend + cs.start(1), tend + cs.end(1)) if cs else None
            t["cols"].append(col)
        om = re.search(r"\b(?:default\s+)?(?:character\s+set|charset)\s*=?\s*(\w+)", t["options"], re.I)
        t["charset"] = om.group(1).lower() if om else None
        t["charset_span"] = (t["opt_at"] + om.start(1), t["opt_at"] + om.end(1)) if om else None
        em = re.search(r"\bengine\s*=?\s*(\w+)", t["options"], re.I)
        t["engine"] = em.group(1).lower() if em else None
        t["engine_span"] = (t["opt_at"] + em.start(1), t["opt_at"] + em.end(1)) if em else None
        tables[t["name"]] = t
        order.append(t["name"])
    later = {"pk": {}, "indexes": {}, "fks": {}}            # keys added after the CREATE TABLE, in the same script
    for im in re.finditer(rf"\bcreate\s+(?:unique\s+)?index\s+(?:concurrently\s+)?(?:if\s+not\s+exists\s+)?(?:{NAME}\s+)?on\s+"
                          rf"((?:{NAME}\.)?{NAME})\s*(?:using\s+\w+\s*)?\(([^;]*?)\)\s*(?:;|$)", m, re.I):
        later["indexes"].setdefault(_uq(im.group(1)), []).append(_key_cols(im.group(2)))
    for am in re.finditer(rf"\balter\s+table\s+(?:only\s+)?((?:{NAME}\.)?{NAME})([^;]*)", m, re.I):
        tn, body = _uq(am.group(1)), am.group(2)
        for pm in re.finditer(r"\badd\s+(?:constraint\s+\S+\s+)?primary\s+key\s*\(([^()]*)\)", body, re.I):
            later["pk"][tn] = _key_cols(pm.group(1))
        for km in re.finditer(r"\badd\s+(?:unique\s+)?(?:index|key)\s+(?:\S+\s+)?\(([^()]*)\)|\badd\s+(?:constraint\s+\S+\s+)?unique\s*"
                              r"(?:key|index)?\s*(?:\S+\s*)?\(([^()]*)\)", body, re.I):
            later["indexes"].setdefault(tn, []).append(_key_cols(km.group(1) or km.group(2)))
        for fm in re.finditer(rf"\badd\s+(?:constraint\s+\S+\s+)?foreign\s+key\s*\(([^()]*)\)\s*references\s+((?:{NAME}\.)?{NAME})", body, re.I):
            later["fks"].setdefault(tn, []).append(_key_cols(fm.group(1)))
    return {"tables": tables, "order": order, "later": later, "mask": m}


def _col(t: dict, name: str) -> dict | None:
    return next((c for c in t["cols"] if c["name"] == name), None)


def _singular(word: str) -> str:
    w = word.lower()
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if re.search(r"(ss|sh|ch|x|z)es$", w):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _led(t: dict, design: dict, col: str) -> bool:
    """Is col the first column of some index (primary key, unique, index, one added later)?"""
    lists = [t["pk"]] + t["uniques"] + [ix["cols"] for ix in t["indexes"]] + design["later"]["indexes"].get(t["name"], [])
    lists += [design["later"]["pk"].get(t["name"], [])]
    return any(lst and lst[0] == col for lst in lists)


# ------------------------------------------------------------------------------------------------ the rules
def schema(sql: str, intent: str, F) -> list:
    """[(Finding, [edits])] for a script that makes tables."""
    try:
        return _schema(sql, intent, F)
    except Exception:  # noqa: BLE001 - a design shape this reader doesn't know: say nothing rather than something wrong
        return []


def _label(fam):
    return {"mysql": "MySQL/MariaDB", "postgresql": "PostgreSQL"}.get(fam, "")


def _db(fam):
    from app.agents.sqlbasics import CHOSEN

    chosen = CHOSEN.get()
    return {"mysql": "MySQL", "mariadb": "MariaDB", "postgresql": "PostgreSQL"}.get(chosen) or _label(fam) or "MySQL/MariaDB"


def _schema(sql: str, intent: str, F) -> list:
    out = []
    d = read_design(sql)
    if not d["tables"]:
        return []
    fam, named = _family(sql)
    R_my, R_pg = rules("mariadb"), rules("postgresql")
    tables = d["tables"]
    pos = {n: i for i, n in enumerate(d["order"])}

    def add(kind, topic, ten, thi, ev, fen, fhi, edits=()):
        out.append((F(kind, topic, ten, thi, _short(ev), fen, fhi), list(edits)))

    # ---- written for the other database (rule A): the named database refuses this syntax
    if named and fam in ("mysql", "postgresql"):
        conv = _convert(sql, d, fam)
        if conv:
            what, edits = conv
            other = "PostgreSQL" if fam == "mysql" else "MySQL/MariaDB"
            db = _db(fam)
            add("error", "ddl_dialect", f"{db} refuses this {other} table syntax: {', '.join(what[:5])}",
                f"{db} ye {other} wala table syntax nahi maanta: {', '.join(what[:5])}", ", ".join(what),
                f"These are {other}'s own words, and {db} stops at the first one ("
                + ("syntax error / type does not exist" if fam == "postgresql" else "error 1064 / 4161 Unknown data type")
                + f"). The rewrite uses {db}'s equivalents: "
                + ("AUTO_INCREMENT -> GENERATED BY DEFAULT AS IDENTITY, DATETIME -> TIMESTAMP, TINYINT -> SMALLINT, DOUBLE -> DOUBLE "
                   "PRECISION, LONGTEXT -> TEXT, BLOB -> BYTEA, UNSIGNED dropped, inline KEY -> CREATE INDEX, ENGINE / CHARSET / "
                   "COMMENT options dropped, backticks dropped." if fam == "postgresql" else
                   "GENERATED ... AS IDENTITY / BIGSERIAL -> AUTO_INCREMENT, TIMESTAMPTZ -> DATETIME (store UTC), JSONB -> JSON, "
                   "BYTEA -> LONGBLOB, arrays -> JSON, \"quoted\" names -> `backticks`."),
                f"Ye {other} ke apne shabd hain, aur {db} pehle hi pe ruk jaata hai. Rewrite mein {db} ke barabar wale likhe hain.",
                edits)
    my_side = fam in (None, "mysql")
    pg_side = fam in (None, "postgresql")
    has_my_syntax = any(re.search(p, sql, re.I) for p in R_my.get("marks", []))

    for tn in d["order"]:
        t = tables[tn]
        ev_head = f"CREATE TABLE {t['raw']}"

        # ---- two primary keys
        if len(t["pk_items"]) > 1:
            add("error", "two_primary_keys", f"{t['raw']} has {len(t['pk_items'])} PRIMARY KEYs: a table has only one",
                f"{t['raw']} mein {len(t['pk_items'])} PRIMARY KEY hain: table ki ek hi hoti hai",
                " / ".join(sql[a:b] for a, b in t["pk_items"]),
                "A table has one primary key: the one thing that names each row (MySQL/MariaDB 1068 Multiple primary key defined, "
                "PostgreSQL 42P16 multiple primary keys are not allowed). If the row is named by the two columns together, write "
                "one PRIMARY KEY (a, b); if the other column must only be unique, make it UNIQUE.",
                "Table ki ek hi primary key hoti hai (MySQL/MariaDB 1068, PostgreSQL 42P16). Dono milke row ko naam dete hain to "
                "PRIMARY KEY (a, b) likho; doosra sirf unique hona chahiye to UNIQUE likho.")

        # ---- reserved words as names
        for kind_, raw, nm, span in [("table", t["raw"], t["name"], None)] + \
                [("column", c["raw"], c["name"], c["name_span"]) for c in t["cols"]]:
            if raw[0] in "`\"" or "." in raw:
                continue
            in_my, in_pg = nm in R_my.get("reserved", []), nm in R_pg.get("reserved", [])
            hit = (in_my and my_side) or (in_pg and pg_side)
            if not hit:
                continue
            sure = (fam == "mysql" and in_my) or (fam == "postgresql" and in_pg) or (fam is None and in_my and in_pg)
            where = "MySQL/MariaDB and PostgreSQL" if in_my and in_pg else ("MySQL/MariaDB" if in_my else "PostgreSQL")
            if fam == "mysql":
                new = f"`{raw}`"
            elif fam == "postgresql":
                new = f'"{raw}"' if raw == raw.lower() else None
            else:
                new = _rename(nm, kind_)
            edits = []
            if new:
                edits = [(*(t["name_span"] if kind_ == "table" else span), new)]
            add("error" if sure else "check", "reserved_name", f"{raw} is a reserved word in {where}: the {kind_} can't be named that bare",
                f"{raw} {where} mein reserved word hai: {kind_} ka naam bina quote ke ye nahi ho sakta", f"{ev_head} ... {raw}",
                f"{where} read {raw} as part of the language, so CREATE TABLE stops with a syntax error (lab: MariaDB 1064, "
                f"PostgreSQL 42601). Pick another name ({_rename(nm, kind_)}), or quote it everywhere you use it: `{raw}` on "
                f"MySQL/MariaDB, \"{raw}\" on PostgreSQL. A new name is kinder to every query that comes later.",
                f"{where} {raw} ko SQL ka shabd padhta hai, isliye syntax error. Doosra naam rakho ({_rename(nm, kind_)}), ya har "
                f"jagah quote karo: MySQL pe `{raw}`, PostgreSQL pe \"{raw}\".", edits)

        # ---- no primary key
        if not t["pk"] and not d["later"]["pk"].get(tn) and not t["temporary"]:
            add_pk = _pk_rewrite(sql, t, fam)
            add("check", "no_primary_key", f"{t['raw']} has no PRIMARY KEY: nothing says which row is which",
                f"{t['raw']} mein PRIMARY KEY nahi: kaunsi row kaunsi hai, kuch nahi batata", ev_head + " (...)",
                "Without a primary key the table accepts the same row twice (lab: both engines stored two identical rows), an "
                "UPDATE or DELETE of 'that one row' can't be written safely, other tables can't point at it, and MySQL/MariaDB "
                "make a hidden row id instead (replication and tools work harder). Give each row a name: an id column "
                + ("(PRIMARY KEY on the id that is already there)" if add_pk and not add_pk[0][2].lstrip().startswith("id ") else
                   "(id INT AUTO_INCREMENT PRIMARY KEY on MySQL/MariaDB, id INT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY on "
                   "PostgreSQL), or PRIMARY KEY (a, b) when two columns together name the row (a link table)") + ".",
                "Primary key ke bina table same row do baar le leti hai (lab: dono engines ne do same rows rakhi), 'wo ek row' "
                "ka UPDATE/DELETE safe nahi likh sakte, aur doosri tables isse jud nahi sakti. Har row ko naam do: id column "
                "PRIMARY KEY, ya link table mein PRIMARY KEY (a, b).", add_pk)

        # ---- columns
        for c in t["cols"]:
            base, args, nm = c["base"], c["args"], c["name"]
            ts = c["type_span"]
            ev = f"{t['raw']}.{c['raw']} {c['type_text']}"
            if base in FLOATY or (base == "float"):
                if MONEY.search(nm) and not re.search(r"(^|_)rate$", nm) or nm in ("price", "amount", "salary", "total"):
                    p, s = _precision(args, nm)
                    add("wrong", "money_float", f"{c['raw']} keeps money in {c['type_text'].upper()}: amounts won't add up exactly",
                        f"{c['raw']} paisa {c['type_text'].upper()} mein rakhta hai: total theek nahi aayega", ev,
                        f"FLOAT, DOUBLE and REAL store a close binary guess, not the amount (lab: ten payments of 0.10 summed to "
                        f"1.0000000149 on MariaDB FLOAT and 1.0000001 on PostgreSQL REAL, and 1234567.89 came back as 1234570 / "
                        f"1234567.9; WHERE amount = 0.1 found nothing). Use DECIMAL({p},{s}) (NUMERIC on PostgreSQL, the same type): "
                        f"exact to the paisa.",
                        f"FLOAT/DOUBLE/REAL amount ka andaaza rakhte hain, amount nahi (lab: das baar 0.10 ka SUM 1.0000000149 aaya, "
                        f"1234567.89 wapas 1234570 aaya). DECIMAL({p},{s}) likho: paise tak exact.",
                        [(ts[0], ts[1], f"DECIMAL({p},{s})")])
                continue
            if base in TEXTY and "[]" not in c["extra"]:
                if DATEISH.search(nm) and not re.search(r"(^|_)(format|text|label|note|notes|zone|tz|type|by)$", nm):
                    new = _date_type(nm, fam)
                    add("check", "date_as_text", f"{c['raw']} looks like a date but is stored as text ({c['type_text'].upper()})",
                        f"{c['raw']} date lagta hai par text ({c['type_text'].upper()}) mein rakha hai", ev,
                        "Text compares letter by letter, so dates as text sort and compare wrongly ('10/1/2024' comes before "
                        "'9/1/2024', MAX gives '9/1/2024'), any spelling is accepted ('2024-02-30' was stored; a DATE column refused "
                        "it on both engines), and every date function needs a conversion first. Store it as "
                        + (new or "DATE (a day) or DATETIME / TIMESTAMP (a moment)") + ", and change the text once with "
                        "STR_TO_DATE (MySQL/MariaDB) / to_date (PostgreSQL) when moving old rows.",
                        "Text akshar se akshar compare hota hai: '10/1/2024' '9/1/2024' se pehle aata hai, '2024-02-30' bhi save "
                        "ho jaata hai. " + (new or "DATE / DATETIME") + " type rakho.",
                        [(ts[0], ts[1], new)] if new else [])
                    continue
                if (MONEY.search(nm) or COUNTISH.search(nm)) and not CODEISH.search(nm) and \
                        not re.search(r"(^|_)(type|types|mode|status|name|names|note|notes|unit|currency|label|code|method|text|by|"
                                      r"desc|description|format|terms|plan)$", nm):
                    new = _number_type(nm, args)
                    add("check", "number_as_text", f"{c['raw']} holds a number but is stored as text ({c['type_text'].upper()})",
                        f"{c['raw']} mein number hai par text ({c['type_text'].upper()}) mein rakha hai", ev,
                        "Numbers kept as text sort and compare as text ('100' before '9', MAX gives '9'), accept anything ('abc'), "
                        "and totals either fail (PostgreSQL: function sum(character varying) does not exist) or silently turn bad "
                        f"text into 0 (MariaDB). Store it as {new}.",
                        f"Text mein number '100' ko '9' se pehle rakhta hai, 'abc' bhi le leta hai, aur SUM PostgreSQL pe fail ya "
                        f"MariaDB pe galat. {new} rakho.", [(ts[0], ts[1], new)])
                    continue
                if LISTISH.search(nm) and not c["auto"]:
                    one = _singular(re.sub(r"_ids$", "_id", nm))
                    add("check", "list_in_column", f"{c['raw']} looks like a list kept in one text column",
                        f"{c['raw']} ek text column mein list jaisa lagta hai", ev,
                        f"A list inside one value ('3,7,12') can't be joined, checked by a foreign key or indexed, and finding one "
                        f"item needs LIKE '%7%' (which also matches 17 and 70). Give each item its own row in a link table, e.g. "
                        f"CREATE TABLE {t['name']}_{re.sub(r'_id$', '', one)} ({_singular(t['name'])}_id INT NOT NULL, {one} ... NOT NULL, "
                        f"PRIMARY KEY ({_singular(t['name'])}_id, {one})).",
                        "Ek value mein list ('3,7,12') join, foreign key ya index nahi ho sakti, aur ek item dhoondhne ke liye LIKE "
                        "'%7%' lagta hai (jo 17 aur 70 bhi pakad leta hai). Har item ki apni row ho: alag link table.")
                    continue
                if nm in SHORT_FIELD and args and args.isdigit() and int(args) >= 1000 and base in ("varchar", "character varying", "nvarchar"):
                    new = f"VARCHAR({SHORT_FIELD[nm]})"
                    add("check", "over_wide", f"{c['raw']} {c['type_text'].upper()} is far wider than a {nm} needs",
                        f"{c['raw']} {c['type_text'].upper()} ek {nm} ki zaroorat se bahut chauda hai", ev,
                        f"The length is a check on the data as much as a size: VARCHAR({args}) lets a pasted paragraph into a {nm}, "
                        f"and on MySQL/MariaDB wide VARCHARs count against the 65,535-byte row limit (4 bytes per character in "
                        f"utf8mb4) and the 3,072-byte index key limit. {new} fits real values.",
                        f"Length data ki jaanch bhi hai: VARCHAR({args}) {nm} mein poora paragraph le lega, aur MySQL/MariaDB pe row "
                        f"aur index limit mein ginta hai. {new} kaafi hai.", [(ts[0], ts[1], new)])
                    continue
                if base in ("char", "character", "nchar") and nm in SHORT_FIELD and args and args.isdigit() and int(args) >= 30:
                    new = f"VARCHAR({max(int(args), SHORT_FIELD[nm]) if int(args) < 1000 else SHORT_FIELD[nm]})"
                    add("check", "over_wide", f"{c['raw']} CHAR({args}) pads every {nm} to {args} characters",
                        f"{c['raw']} CHAR({args}) har {nm} ko {args} characters tak bharta hai", ev,
                        f"CHAR is fixed width: a short {nm} is padded with spaces to {args}, which wastes space and, on PostgreSQL, "
                        f"shows up in length() of a cast and in comparisons with VARCHAR values. Use {new} for values whose length varies.",
                        f"CHAR fixed width hai: chhota {nm} spaces se {args} tak bhara jaata hai. Badalti length ke liye {new}.",
                        [(ts[0], ts[1], new)])
                    continue
            if base in INTY and CODEISH.search(nm) and not re.search(r"(^|_)(id|ids|count|type|verified|attempts)$", nm):
                add("check", "code_as_number", f"{c['raw']} is a code made of digits, stored as a number ({c['type_text'].upper()})",
                    f"{c['raw']} digits ka code hai, number ({c['type_text'].upper()}) mein rakha hai", ev,
                    "Phone numbers, PIN / ZIP codes and account numbers are labels, not quantities: nobody adds them. As numbers they "
                    "lose a leading 0 (lab: 012345 came back as 12345 on both engines), a 10-digit mobile overflows INT (MariaDB 1264 "
                    "Out of range, PostgreSQL integer out of range), and '+91' or '-' can't be kept. Store them as VARCHAR(20).",
                    "Phone, PIN code, account number label hain, quantity nahi. Number mein aage ka 0 chala jaata hai (012345 -> "
                    "12345), 10 digit mobile INT mein nahi aata (1264), '+91' nahi rakh sakte. VARCHAR(20) rakho.",
                    [(ts[0], ts[1], "VARCHAR(20)")])
                continue
            if base == "varchar" and args is None and my_side:
                add("error" if fam == "mysql" else "check", "varchar_length", f"{c['raw']} VARCHAR has no length",
                    f"{c['raw']} VARCHAR ki length nahi di", ev,
                    "MySQL/MariaDB need a maximum length for VARCHAR and stop with error 1064 without one; PostgreSQL accepts a bare "
                    "VARCHAR (any length). Say the longest value you expect, e.g. VARCHAR(255), or use TEXT.",
                    "MySQL/MariaDB ko VARCHAR ki length chahiye (bina length 1064); PostgreSQL bina length bhi le leta hai. "
                    "VARCHAR(255) jaisa likho, ya TEXT.", [(ts[0], ts[1], c["type_text"] + "(255)")] if fam == "mysql" else [])
                continue
            if base == "timestamp" and OLD_OR_FAR.search(nm) and my_side and not (fam is None and not has_my_syntax):
                new = "DATE" if DAY_ONLY.search(nm) or re.search(r"birth|born|dob|founded|established|anniversary", nm) else "DATETIME"
                add("check", "timestamp_range", f"{c['raw']} is a TIMESTAMP: MySQL/MariaDB keep only 1970 to 2038 (2106) in it",
                    f"{c['raw']} TIMESTAMP hai: MySQL/MariaDB isme sirf 1970 se 2038 tak rakhte hain", ev,
                    "On MySQL/MariaDB a TIMESTAMP counts seconds from 1970-01-01 UTC and ends on 2038-01-19 (2106 on MariaDB 11.5+): "
                    "lab: MariaDB 10.11 refused both 1965-05-01 and 2040-01-01 with 1292 Incorrect datetime value, and no version "
                    "takes a date before 1970 or a 'never' of 9999-12-31; PostgreSQL's timestamp kept them all. "
                    f"A birth date, an expiry or any date outside that window needs {new}.",
                    f"MySQL/MariaDB TIMESTAMP 1970 se 2038-01-19 tak hi rakhta hai (lab: 1965 aur 2040 dono refuse, 1292). Janam "
                    f"tithi / expiry ke liye {new} rakho.", [(ts[0], ts[1], new)])
                continue
            if base in ("text", "tinytext", "mediumtext", "longtext", "blob") and nm in t["pk"] and my_side:
                add("error" if fam == "mysql" else "check", "text_key", f"{c['raw']} is TEXT and is the primary key",
                    f"{c['raw']} TEXT hai aur primary key hai", ev,
                    "MySQL/MariaDB index only a prefix of a TEXT / BLOB column and refuse it as a whole key (1170 BLOB/TEXT column "
                    "used in key specification without a key length); PostgreSQL accepts it. A key should be short anyway: "
                    "VARCHAR(255) (or an id).",
                    "MySQL/MariaDB TEXT ko poori key nahi banne dete (1170); PostgreSQL de deta hai. Key chhoti ho: VARCHAR(255).",
                    [(ts[0], ts[1], "VARCHAR(255)")] if fam == "mysql" else [])

        # ---- repeating groups: phone1, phone2 ...
        stems = {}
        for c in t["cols"]:
            sm = re.match(r"^(.*?[a-z])_?(\d)$", c["name"])
            if sm and not re.search(r"(line|address|addr|line_)$", sm.group(1)):
                stems.setdefault(sm.group(1), []).append(c)
        for stem, cs in stems.items():
            if len(cs) >= 2:
                add("check", "repeating_group", f"{', '.join(c['raw'] for c in cs)}: the same thing in numbered columns",
                    f"{', '.join(c['raw'] for c in cs)}: ek hi cheez numbered columns mein", f"{t['raw']}: " + ", ".join(c["raw"] for c in cs),
                    f"Numbered columns put a fixed limit on a list ({len(cs)}, and then?), leave most of them NULL, and make every "
                    f"search look in each one (WHERE {stem}1 = ? OR {stem}2 = ? ...). Each {stem} wants its own row: "
                    f"CREATE TABLE {t['name']}_{stem}s ({_singular(t['name'])}_id ... NOT NULL REFERENCES {t['name']}, {stem} ..., "
                    f"PRIMARY KEY ({_singular(t['name'])}_id, {stem})).",
                    f"Numbered columns list pe limit lagate hain ({len(cs)}, phir?), zyada NULL rehte hain, aur har search har column "
                    f"mein dekhti hai. Har {stem} ki apni row ho: alag table.")

        # ---- a parent's details copied next to its id
        for fk in t["fks"] + [{"cols": [c["name"]], "parent": None, "pcols": [], "inline": None} for c in t["cols"]
                              if c["name"].endswith("_id") and not any(c["name"] in f["cols"] for f in t["fks"])]:
            if len(fk["cols"]) != 1:
                continue
            stem = fk["cols"][0][:-3] if fk["cols"][0].endswith("_id") else None
            if not stem:
                continue
            copies = [c for c in t["cols"] if c["name"].startswith(stem + "_") and c["name"] != fk["cols"][0] and
                      re.match(rf"{re.escape(stem)}_(name|email|phone|mobile|city|address|first_name|last_name|full_name|title|"
                               r"category|country|state|dob|gender)$", c["name"])]
            if copies:
                add("check", "parent_copy", f"{t['raw']} keeps {fk['cols'][0]} and also copies {', '.join(c['raw'] for c in copies)}",
                    f"{t['raw']} {fk['cols'][0]} rakhta hai aur saath mein {', '.join(c['raw'] for c in copies)} bhi copy karta hai",
                    f"{t['raw']}: {fk['cols'][0]}, " + ", ".join(c["raw"] for c in copies),
                    f"{', '.join(c['raw'] for c in copies)} belong to the {stem} that {fk['cols'][0]} already points at. Kept here too, "
                    f"they go stale the day the {stem} changes and the two copies disagree. Read them with a JOIN on "
                    f"{fk['cols'][0]} instead, unless this is a deliberate snapshot (what it was at the time of this row), and "
                    f"then name it so (e.g. {stem}_name_at_order).",
                    f"{', '.join(c['raw'] for c in copies)} us {stem} ke hain jise {fk['cols'][0]} pehle se batata hai. Yahan copy "
                    f"rakhne se {stem} badalne pe dono alag ho jaate hain. JOIN se padho, jab tak ye jaan-boojh kar snapshot na ho.")

        # ---- foreign keys
        for fk in t["fks"]:
            parent = tables.get(fk["parent"])
            ev = f"{t['raw']} ({', '.join(fk['cols'])}) REFERENCES {fk['parent']}"
            if parent is None:
                continue                                  # a table made elsewhere: its keys and types aren't in this text
            if pos[fk["parent"]] > pos[tn]:
                edits = _move_before(sql, parent, t)
                add("error", "fk_order", f"{t['raw']} points at {fk['parent']} before {fk['parent']} is created",
                    f"{t['raw']} {fk['parent']} ko point karta hai, {fk['parent']} banne se pehle", ev,
                    f"A foreign key needs its parent table to exist already (lab: MariaDB 1005 errno 150, PostgreSQL 42P01 relation "
                    f"does not exist). Create {fk['parent']} first (parents before children), or add the foreign key afterwards "
                    f"with ALTER TABLE.",
                    f"Foreign key ke liye parent table pehle se honi chahiye (MariaDB 150, PostgreSQL 42P01). {fk['parent']} pehle "
                    f"banao.", edits)
                continue
            pcols = fk["pcols"] or parent["pk"]
            if len(pcols) != len(fk["cols"]):
                continue
            keyed = pcols == parent["pk"] or pcols in parent["uniques"] or pcols == d["later"]["pk"].get(parent["name"])
            if not keyed:
                indexed = _led(parent, d, pcols[0])
                sure = fam == "postgresql" or not indexed
                p_col = _col(parent, pcols[0]) if len(pcols) == 1 else None
                edits = [(p_col["attrs_span"][1], p_col["attrs_span"][1], " UNIQUE")] if p_col else []
                add("error" if sure else "check", "fk_target", f"{fk['parent']}.{', '.join(pcols)} is not a primary key or UNIQUE",
                    f"{fk['parent']}.{', '.join(pcols)} na primary key hai na UNIQUE", ev,
                    "A foreign key must point at something that names exactly one parent row: its primary key or a UNIQUE column. "
                    "PostgreSQL refuses anything else (42830 there is no unique constraint matching given keys); MariaDB refuses a "
                    "column with no index at all (1005 errno 150) and quietly accepts a non-unique indexed one, where one child can "
                    f"then match several parents. Point it at {fk['parent']}'s primary key, or make {', '.join(pcols)} UNIQUE.",
                    "Foreign key ko aisi cheez pe point karna chahiye jo ek hi parent row batati ho: primary key ya UNIQUE. PostgreSQL "
                    "mana karta hai (42830), MariaDB bina index wale ko mana (150). Primary key pe point karo, ya UNIQUE banao.", edits)
                continue
            for cc, pc in zip(fk["cols"], pcols):
                ccol, pcol = _col(t, cc), _col(parent, pc)
                if not ccol or not pcol:
                    continue
                kc, kp = _kind(ccol, fam), _kind(pcol, fam)
                if kc == kp:
                    continue
                cat_c, cat_p = kc[0], kp[0]
                incompatible = cat_c != cat_p
                sure = (fam == "mysql") or incompatible
                new = _child_type(pcol, fam)
                add("error" if sure else "check", "fk_type",
                    f"{t['raw']}.{cc} is {ccol['type_text'].upper()} but {fk['parent']}.{pc} is {pcol['type_text'].upper()}",
                    f"{t['raw']}.{cc} {ccol['type_text'].upper()} hai par {fk['parent']}.{pc} {pcol['type_text'].upper()}", ev,
                    "The two ends of a link must hold the same kind of value. MySQL/MariaDB want the same integer size and the same "
                    "signedness (lab: INT -> BIGINT and INT -> INT UNSIGNED both refused, 1005 errno 150 Foreign key constraint is "
                    "incorrectly formed); PostgreSQL accepts INT -> BIGINT but refuses a number pointing at text (foreign key "
                    f"constraint cannot be implemented). Make {cc} {new}, exactly like {fk['parent']}.{pc}.",
                    "Link ke dono sire same type ke hon. MySQL/MariaDB same integer size aur signed/unsigned chahte hain (150); "
                    f"PostgreSQL INT -> BIGINT le leta hai par number -> text nahi. {cc} ko {new} karo.",
                    [(ccol["type_span"][0], ccol["type_span"][1], new)])

        # ---- an unindexed link (PostgreSQL makes no index for a foreign key; MySQL/MariaDB InnoDB make one)
        if pg_side and not (fam is None and has_my_syntax):
            missing = [fk for fk in t["fks"] if fk["cols"] and not _led(t, d, fk["cols"][0]) and
                       not (fk["parent"] in tables and pos[fk["parent"]] > pos[tn])]
            stmts = [f"CREATE INDEX {('ix_' + t['name'] + '_' + '_'.join(fk['cols']))[:60]} ON {t['raw']} "
                     f"({', '.join(fk['cols'])});" for fk in missing]
            at = t["stmt_end"]
            lead = "" if at > 0 and sql[at - 1:at] == ";" else ";"
            for k, (fk, stmt) in enumerate(zip(missing, stmts)):
                ev = f"{t['raw']} ({', '.join(fk['cols'])}) REFERENCES {fk['parent']}"
                add("check", "fk_no_index", f"{t['raw']}.{', '.join(fk['cols'])} links to {fk['parent']} but has no index on PostgreSQL",
                    f"{t['raw']}.{', '.join(fk['cols'])} {fk['parent']} se juda hai par PostgreSQL pe index nahi", ev,
                    f"PostgreSQL indexes the parent's key but never the child's column (lab: pg_indexes showed only the primary key's "
                    f"index; MariaDB InnoDB made one by itself). Without it, every join from {fk['parent']} to {t['raw']} and every "
                    f"DELETE or key change in {fk['parent']} reads the whole of {t['raw']}. Worth adding: {stmt}",
                    f"PostgreSQL child column pe index khud nahi banata (MariaDB banata hai). Iske bina har join aur parent ka har "
                    f"DELETE poori {t['raw']} padhta hai. Jodo: {stmt}",
                    [(at, at, lead + "".join("\n" + x for x in stmts))] if k == 0 else [])

        # ---- MySQL/MariaDB table options: utf8 (3 bytes) and MyISAM (no foreign keys)
        if my_side:
            spans = [("table", t["charset"], t["charset_span"])] + [("column " + c["raw"], c["charset"], c["charset_span"]) for c in t["cols"]]
            for what, cs, span in spans:
                if cs in ("utf8", "utf8mb3"):
                    edits = [(span[0], span[1], "utf8mb4")]
                    for cm_ in re.finditer(r"\b(utf8(?:mb3)?)_(\w+)", sql[t["start"]:t["end"]], re.I):
                        a = t["start"] + cm_.start(1)
                        edits.append((a, a + len(cm_.group(1)), "utf8mb4"))
                    edits = sorted(set(edits))
                    add("wrong", "utf8mb3", f"{t['raw']} ({what}) uses {cs}: on MySQL/MariaDB that is 3-byte utf8mb3, no emoji",
                        f"{t['raw']} ({what}) {cs} use karta hai: MySQL/MariaDB pe ye 3-byte utf8mb3 hai, emoji nahi", f"CHARSET={cs}",
                        "MySQL/MariaDB's 'utf8' is the old utf8mb3: at most 3 bytes per character, so emoji and some rarer scripts "
                        "(4-byte characters) are refused (lab: MariaDB 1366 Incorrect string value: '\\xF0\\x9F\\x98\\x80' in strict "
                        "mode) or, without strict mode, cut off. utf8mb4 is real UTF-8 and is the default on MySQL 8 and recent "
                        "MariaDB. Use CHARSET=utf8mb4 (and a utf8mb4_* collation).",
                        "MySQL/MariaDB ka 'utf8' purana utf8mb3 hai: 3 byte tak, isliye emoji refuse (1366) ya kat jaate hain. "
                        "utf8mb4 asli UTF-8 hai. CHARSET=utf8mb4 likho.", edits)
            if t["engine"] == "myisam" and t["fks"]:
                more = [(p["engine_span"][0], p["engine_span"][1], "InnoDB") for p in (tables.get(f["parent"]) for f in t["fks"])
                        if p is not None and p.get("engine") == "myisam"]
                add("wrong", "myisam_fk", f"{t['raw']} is MyISAM: its FOREIGN KEY is accepted and then ignored",
                    f"{t['raw']} MyISAM hai: iski FOREIGN KEY maani jaati hai aur phir ignore", f"ENGINE={t['engine']} ... REFERENCES",
                    "MyISAM has no foreign keys and no transactions. MariaDB creates the table without a word (no warning) and then "
                    "lets a child point at a parent that doesn't exist (lab: INSERT with a missing parent succeeded). Use "
                    "ENGINE=InnoDB (the default) for tables that link to each other.",
                    "MyISAM mein foreign key aur transaction nahi hote. MariaDB chup-chaap table bana deta hai aur galat parent wali "
                    "row bhi le leta hai (lab). ENGINE=InnoDB rakho.", [(t["engine_span"][0], t["engine_span"][1], "InnoDB")] + more)
            for f in t["fks"]:
                p = tables.get(f["parent"])
                if p is not None and p.get("engine") == "myisam" and t["engine"] != "myisam":
                    add("wrong", "myisam_fk", f"{f['parent']} is MyISAM: {t['raw']}'s foreign key to it can't be kept",
                        f"{f['parent']} MyISAM hai: {t['raw']} ki foreign key nahi chalegi", f"ENGINE={p['engine']}",
                        "A foreign key needs both tables on InnoDB. Make the parent ENGINE=InnoDB too.",
                        "Foreign key ke liye dono tables InnoDB pe hon.", [(p["engine_span"][0], p["engine_span"][1], "InnoDB")])

            # ---- the 65,535-byte row (VARCHARs count in full: 4 bytes per character in utf8mb4)
            bpc = {"latin1": 1, "ascii": 1, "utf8": 3, "utf8mb3": 3}.get(t["charset"] or "", 4)
            vc = [c for c in t["cols"] if c["base"] in ("varchar", "nvarchar", "character varying") and c["args"] and c["args"].isdigit()]
            too_long = [c for c in vc if int(c["args"]) * {"latin1": 1, "ascii": 1, "utf8": 3, "utf8mb3": 3}.get(c["charset"] or "", bpc)
                        > 65535 - 2]
            total = sum(int(c["args"]) * {"latin1": 1, "ascii": 1, "utf8": 3, "utf8mb3": 3}.get(c["charset"] or "", bpc) + 2 for c in vc)
            if too_long or total > 65535:
                fix_cols = too_long or sorted(vc, key=lambda c: -int(c["args"]))
                edits, left = [], total
                for c in fix_cols:
                    if not too_long and left <= 65000:
                        break
                    left -= int(c["args"]) * bpc + 2
                    edits.append((c["type_span"][0], c["type_span"][1], "TEXT"))
                code = "1074 Column length too big" if too_long else "1118 Row size too large"
                add("error" if fam == "mysql" else "check", "row_too_big",
                    f"{t['raw']}'s VARCHARs need about {total:,} bytes: MySQL/MariaDB allow 65,535 per row",
                    f"{t['raw']} ke VARCHARs ko lagbhag {total:,} bytes chahiye: MySQL/MariaDB ek row mein 65,535 dete hain",
                    ", ".join(f"{c['raw']} {c['type_text']}" for c in vc[:4]),
                    f"On MySQL/MariaDB every VARCHAR counts its full length against one 65,535-byte row ({bpc} bytes per character "
                    f"here), so this CREATE TABLE stops ({code}; lab: MariaDB refused two VARCHAR(10000) in utf8mb4, and any one "
                    f"VARCHAR over 16,383). PostgreSQL has no such limit. Long free text belongs in TEXT, which is stored apart from "
                    f"the row: {', '.join(c['raw'] for c in fix_cols[:len(edits)])} -> TEXT.",
                    f"MySQL/MariaDB pe har VARCHAR poori length ke saath ek 65,535-byte row mein ginta hai ({code}). Lamba text TEXT "
                    f"mein rakho: {', '.join(c['raw'] for c in fix_cols[:len(edits)])} -> TEXT.", edits)
    return out


# ------------------------------------------------------------------------------------------------ rewrites
def _rename(word: str, kind: str) -> str:
    named = {"order": ("orders", "sort_order"), "group": ("user_groups", "group_name"), "user": ("users", "user_name"),
             "desc": ("descs", "description"), "key": ("keys_list", "key_name"), "check": ("checks", "check_value"),
             "table": ("tables_list", "table_name"), "column": ("columns_list", "column_name"), "default": ("defaults", "default_value"),
             "index": ("indexes", "index_no"), "range": ("ranges", "range_value"), "rows": ("row_list", "row_count"),
             "values": ("value_list", "value_text"), "end": ("ends", "end_at"), "offset": ("offsets", "offset_value"),
             "window": ("windows", "window_name"), "limit": ("limits", "limit_value"), "condition": ("conditions", "condition_text")}
    if word in named:
        return named[word][0 if kind == "table" else 1]
    return f"{word}_list" if kind == "table" else f"{word}_value"


def _precision(args, nm) -> tuple[int, int]:
    if args and re.fullmatch(r"\s*\d+\s*,\s*\d+\s*", args):
        p, s = (int(x) for x in args.split(","))
        return max(p, s + 1), s
    return 12, 2


def _date_type(nm: str, fam) -> str | None:
    if DAY_ONLY.search(nm) and not re.search(r"(^|_)(at|time|timestamp|datetime)$", nm):
        return "DATE"
    return {"mysql": "DATETIME", "postgresql": "TIMESTAMP"}.get(fam)


def _number_type(nm: str, args) -> str:
    if MONEY.search(nm):
        return "DECIMAL(12,2)"
    return "INT"


def _kind(col: dict, fam=None) -> tuple:
    """(category, exact form) of a column type, for comparing the two ends of a foreign key."""
    b = col["base"]
    unsigned = "unsigned" in col["extra"]
    if b == "serial":                                    # MySQL/MariaDB SERIAL = BIGINT UNSIGNED; PostgreSQL serial = integer
        return ("int", "int", False) if fam == "postgresql" else ("int", "bigint", True)                   # MySQL/MariaDB SERIAL = BIGINT UNSIGNED; PostgreSQL serial = integer
    if b in ("int", "integer", "int4", "mediumint"):
        return ("int", "int" if b != "mediumint" else "mediumint", unsigned)
    if b in ("bigint", "int8", "bigserial"):
        return ("int", "bigint", unsigned)
    if b in ("smallint", "int2", "smallserial"):
        return ("int", "smallint", unsigned)
    if b == "tinyint":
        return ("int", "tinyint", unsigned)
    if b in TEXTY:
        return ("text", b if b not in ("character varying",) else "varchar", None)
    if b in ("decimal", "numeric"):
        return ("num", (col["args"] or "").replace(" ", ""), unsigned)
    return (b, col["args"], unsigned)


def _child_type(pcol: dict, fam=None) -> str:
    b = pcol["base"]
    if b == "serial":
        return "INTEGER" if fam == "postgresql" else "BIGINT UNSIGNED"
    if b == "bigserial":
        return "BIGINT"
    if b == "smallserial":
        return "SMALLINT"
    if pcol["auto"]:
        txt = re.sub(r"\s*\bauto_increment\b", "", pcol["type_text"], flags=re.I)
        return txt.upper()
    return pcol["type_text"].upper()


def _pk_rewrite(sql: str, t: dict, fam) -> list:
    """Name the rows: PRIMARY KEY on an existing id, on the two link columns of a link table, or a new id column."""
    end_item = t["items"][-1][1] if t["items"] else t["close"]
    own = [c for c in t["cols"] if c["name"].endswith("_id") and len(c["name"]) > 3 and _singular(t["name"]).endswith(c["name"][:-3])]
    idc = _col(t, "id") or _col(t, _singular(t["name"]) + "_id") or (own[0] if own else None)
    if idc:
        return [(end_item, end_item, f",\n  PRIMARY KEY ({idc['raw']})")]
    links = [c for c in t["cols"] if c["name"].endswith("_id") and c not in own]
    if len(links) == 2 and len(t["cols"]) <= 4:
        return [(end_item, end_item, f",\n  PRIMARY KEY ({links[0]['raw']}, {links[1]['raw']})")]
    if fam == "mysql":
        new = "id INT AUTO_INCREMENT PRIMARY KEY,\n  "
    elif fam == "postgresql":
        new = "id INT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,\n  "
    else:
        return []
    first = t["items"][0][0] if t["items"] else t["open"] + 1
    return [(first, first, new)]


def _move_before(sql: str, parent: dict, child: dict) -> list:
    """Move the parent's CREATE TABLE (with its ';') in front of the child's."""
    a, b = parent["start"], parent["stmt_end"]
    text = sql[a:b].strip()
    if not text.endswith(";"):
        text += ";"
    return [(child["start"], child["start"], text + "\n"), (a, b, "")]


# ------------------------------------------------------------------------------------------------ the other database's syntax
def _convert(sql: str, d: dict, fam: str):
    """(what was foreign, edits) to write the script in the named database's own words, or None when nothing is foreign."""
    R = rules("postgresql" if fam == "postgresql" else "mariadb")
    types = R.get("foreign_types", {})
    what, edits = [], []
    m = d["mask"]
    for tn in d["order"]:
        t = d["tables"][tn]
        idx_after = []
        for c in t["cols"]:
            b = c["base"]
            new_type = None
            key = b + ("[]" if "[]" in c["extra"] else "")
            if key in types:
                new_type = types[key]
            elif "[]" in c["extra"] and "array" in types:
                new_type = types["array"]
                what.append("arrays (type[])")
            if fam == "postgresql":
                if "unsigned" in c["extra"] or "zerofill" in c["extra"]:
                    new_type = new_type or c["type_text"]
                    new_type = re.sub(r"\s+(unsigned|zerofill)\b", "", new_type, flags=re.I)
                    what.append("UNSIGNED")
                if b in ("int", "integer", "bigint", "smallint") and c["args"]:
                    new_type = {"int": "INTEGER", "integer": "INTEGER", "bigint": "BIGINT", "smallint": "SMALLINT"}[b]
                    what.append(f"{b.upper()}({c['args']})")
                if b == "float" and c["args"] and "," in c["args"]:
                    new_type = f"NUMERIC({c['args'].replace(' ', '')})"
                    what.append(f"FLOAT({c['args']})")
                if b == "enum" and c["args"]:
                    vals = re.findall(r"'((?:[^']|'')*)'", sql[c["type_span"][0]:c["type_span"][1]])
                    n = max([len(v) for v in vals] + [1])
                    new_type = f"VARCHAR({n}) CHECK ({_bare(c['raw'])} IN ({', '.join(chr(39) + v + chr(39) for v in vals)}))"
            if new_type is not None:
                if key in types:
                    what.append(c["type_text"].split("(")[0].upper().strip())
                edits.append((c["type_span"][0], c["type_span"][1], new_type))
            if fam == "postgresql" and b in ("boolean", "bool"):
                bm = re.search(r"\bdefault\s+([01])\b", m[c["attrs_span"][0]:c["attrs_span"][1]], re.I)
                if bm:
                    a = c["attrs_span"][0] + bm.start(1)
                    edits.append((a, a + 1, "true" if bm.group(1) == "1" else "false"))
                    what.append("BOOLEAN DEFAULT 0/1")
            # column attributes
            for pat, rep, label in R.get("foreign_attrs", []):
                for am in re.finditer(pat, m[c["attrs_span"][0]:c["attrs_span"][1]], re.I):
                    a = c["attrs_span"][0] + am.start()
                    edits.append((a, a + len(am.group(0)), re.sub(pat, rep, am.group(0), flags=re.I)))
                    what.append(label)
        if fam == "postgresql":
            for ix in t["indexes"]:
                if ix["kind"] in ("key", "index"):
                    s, e = ix["span"]
                    comma = m.rfind(",", 0, s)
                    edits.append((comma, e, ""))
                    idx_after.append(f"CREATE INDEX {('ix_' + t['name'] + '_' + '_'.join(ix['cols']))[:60]} ON {_bare(t['raw'])} ({', '.join(ix['cols'])});")
                    what.append("KEY / INDEX inside CREATE TABLE")
            for s, e in [(a, b) for a, b in t["items"] if re.match(r"\s*unique\s+(key|index)\b", m[a:b], re.I)]:
                um = re.match(r"\s*unique\s+(key|index)\s*(?:" + NAME + r"\s*)?", m[s:e], re.I)
                edits.append((s, s + um.end(), "UNIQUE "))
                what.append("UNIQUE KEY")
        opts = t["options"]
        for pat, label in R.get("foreign_options", []):
            for om in re.finditer(pat, opts, re.I):
                a = t["opt_at"] + om.start()
                edits.append((a, a + len(om.group(0)), ""))
                what.append(label)
        if idx_after:
            at = t["stmt_end"]
            lead = "" if at > 0 and sql[at - 1:at] == ";" else ";"
            edits.append((at, at, lead + "\n" + "\n".join(idx_after)))
    # quoting style
    q_from, q_to = ("`", '"') if fam == "postgresql" else ('"', "`")
    for qm in re.finditer(re.escape(q_from) + r"([^`\"]+)" + re.escape(q_from), m):
        name = qm.group(1)
        bare_ok = fam == "postgresql" and re.fullmatch(r"[a-z_][a-z0-9_]*", name) and name not in R.get("reserved", [])
        edits.append((qm.start(), qm.end(), name if bare_ok else f"{q_to}{name}{q_to}"))
        what.append("`backticks`" if q_from == "`" else '"double-quoted" names')
    if not edits:
        return None
    seen, uniq = set(), []
    for w in what:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq, _clean_edits(edits)


def _bare(raw: str) -> str:
    return raw.strip('`"')


def _clean_edits(edits: list) -> list:
    """Drop edits that overlap an earlier one (the first wins)."""
    out = []
    for e in edits:
        if any(e[0] < x[1] and x[0] < e[1] for x in out):
            continue
        out.append(e)
    return out


def apply(sql: str, edits: list) -> str:
    out = sql
    for a, b, new in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        out = out[:a] + new + out[b:]
    out = re.sub(r"\)\s*;\s*;", ");", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\)[ \t]+;", ");", out)
    return out


# ------------------------------------------------------------------------------------------------ how DBaba read it
def reading(sql: str, hi: bool = False) -> dict:
    d = read_design(sql)
    lines = []
    for tn in d["order"]:
        t = d["tables"][tn]
        pk = t["pk"] or d["later"]["pk"].get(tn)
        row = (f"one row per {', '.join(pk)}" if not hi else f"har {', '.join(pk)} ki ek row") if pk else \
            ("no primary key: nothing names a row" if not hi else "primary key nahi: row ka koi naam nahi")
        lines.append(f"- {t['raw']} ({len(t['cols'])} columns): {row}")
        for fk in t["fks"]:
            one = ", ".join(fk["cols"])
            lines.append(("  link: " if not hi else "  link: ") + f"{t['raw']}.{one} -> {fk['parent']}" +
                         (f" (one {_singular(fk['parent'])}, many {t['raw']})" if not hi else f" (ek {_singular(fk['parent'])}, kai {t['raw']})"))
    head = "How DBaba read this design:" if not hi else "DBaba ne design aise padha:"
    grain = "; ".join(f"{tn}: {', '.join(d['tables'][tn]['pk'] or ['?'])}" for tn in d["order"])
    return {"text": head + "\n" + "\n".join(lines), "grain": grain}
