"""A project's own schema (night 10B of the curriculum): the tables, columns, types, NULLs, keys and indexes a project
creates, read (never run, never changed) from the files that create them, so the findings that depend on them can be proven
or dropped (app/agents/sqlverify.py; fundamental 'schema_facts' in app/dbknow/core/query_fundamentals.json).

What is read, in the order the database would see it:
- schema dumps and plain .sql scripts (mysqldump --no-data, pg_dump --schema-only, Laravel's database/schema/*.sql,
  hand-written schema.sql): CREATE TABLE, CREATE [UNIQUE] INDEX, ALTER TABLE (add / drop / change / rename columns,
  keys, indexes, NOT NULL), RENAME TABLE, DROP TABLE, DROP INDEX;
- then migrations, by their version (the number or timestamp at the front of the file name): plain SQL migrations (Flyway
  V1__x.sql, golang-migrate 0001_x.up.sql, dbmate / goose / sql-migrate files with only their 'up' part) and Laravel
  migrations (Schema::create / Schema::table / Schema::drop[IfExists] / Schema::rename and the Blueprint column methods,
  only the up() part). Undo files (U1__x.sql, *.down.sql) and 'down' parts are skipped: they are not the schema.
A Laravel dump lists the migrations it already contains; those are not applied twice.

How a migration framework writes a column matters (lab and docs): plain SQL columns may be NULL unless they say NOT NULL;
a Laravel column is NOT NULL unless ->nullable() (timestamps(), softDeletes() and rememberToken() are nullable).
Anything that can't be read for sure (a column method we don't know, PHP logic around the schema calls, a table built with
CREATE TABLE ... AS SELECT / LIKE) is listed as 'couldn't read completely' and that table is marked partial, so nothing
is guessed from it: findings about a partial table are never upgraded or dropped.
"""

import os
import re

from app.agents.sqlroutine import _mask, split_script
from app.agents.sqlschema import NAME, read_design

SKIP_DIRS = {".git", ".svn", ".hg", "vendor", "node_modules", "bower_components", "build", "dist", "out", "target", "coverage",
             "__pycache__", ".venv", "venv", "env", "storage", "cache", ".cache", ".idea", ".vscode", ".next", ".nuxt"}
MAX_BYTES = 5_000_000
SCHEMA_WORDS = re.compile(r"\b(?:create\s+(?:(?:global\s+|local\s+)?temp(?:orary)?\s+)?table|alter\s+table|create\s+(?:unique\s+)?index|"
                          r"drop\s+table|rename\s+table)\b", re.I)
MIGRATION_DIR = re.compile(r"(^|[\\/])(migrations?|migrate|db[\\/]changelog|flyway|sql[\\/]versions?|schema[\\/]migrations?)([\\/]|$)",
                           re.I)
UNDO_FILE = re.compile(r"(^U\d+(?:[._]\d+)*__|\.down\.sql$|(^|[_.-])down\.sql$|_rollback\.sql$)", re.I)
VERSIONED = re.compile(r"^(?:V|R__)?(\d+(?:[._]\d+)*)", re.I)
DOWN_MARK = re.compile(r"^\s*--\s*(?:migrate:down|\+goose\s+down|\+migrate\s+down)\b.*$", re.I | re.M)
UP_MARK = re.compile(r"^\s*--\s*(?:migrate:up|\+goose\s+up|\+migrate\s+up)\b.*$", re.I | re.M)

TEXT_FAMILY = re.compile(r"^(char|varchar|character|nchar|nvarchar|text|tinytext|mediumtext|longtext|citext|enum|set|uuid|string|"
                         r"national|json|jsonb|xml|inet|cidr|macaddr)", re.I)
NUMBER_FAMILY = re.compile(r"^(int|integer|bigint|smallint|mediumint|tinyint|serial|bigserial|smallserial|int2|int4|int8|"
                           r"decimal|numeric|number|float|double|real|float4|float8|bit|money|dec|fixed)", re.I)
TIME_FAMILY = re.compile(r"^(datetime|timestamp|timestamptz)", re.I)


def uq(n: str) -> str:
    """A name as the schema keys it: no schema prefix, no quotes, lower case."""
    return (n or "").strip().split(".")[-1].strip('`"[]').lower()


def family(type_text: str) -> str:
    """number / text / date / time (has a time of day) / other: what kind of value a column type holds."""
    t = (type_text or "").strip().lower()
    if t == "tinyint(1)" or t.startswith(("bool", "boolean")):
        return "number"
    if t.startswith("date") and not t.startswith("datetime"):
        return "date"
    if TIME_FAMILY.match(t):
        return "time"
    if t.startswith("time"):
        return "other"
    if NUMBER_FAMILY.match(t):
        return "number"
    if TEXT_FAMILY.match(t):
        return "text"
    return "other"


def empty() -> dict:
    return {"tables": {}, "files": [], "unread": [], "notes": [], "applied": 0}


def _table(sch: dict, name: str, raw: str, at: str) -> dict:
    t = {"name": uq(name), "raw": raw.strip().split(".")[-1].strip('`"'), "cols": {}, "order": [], "pk": [], "uniques": [],
         "indexes": [], "fks": [], "at": at, "partial": False}
    sch["tables"][t["name"]] = t
    return t


def _col(t: dict, name: str, type_text: str, not_null: bool, at: str, auto: bool = False, default: str | None = None) -> dict:
    c = {"name": uq(name), "raw": name.strip('`"'), "type": re.sub(r"\s+", " ", type_text.strip()).lower(), "not_null": not_null,
         "auto": auto, "default": default, "at": at}
    if c["name"] not in t["cols"]:
        t["order"].append(c["name"])
    t["cols"][c["name"]] = c
    return c


def _index(t: dict, cols: list, unique: bool = False, name: str | None = None, kind: str = "index") -> None:
    cols = [uq(c) if re.fullmatch(rf"{NAME}", c.strip()) else c.strip().lower() for c in cols if c and c.strip()]
    if not cols:
        return
    nm = uq(name) if name else None
    t["indexes"] = [ix for ix in t["indexes"] if not (nm and ix["name"] == nm)]
    t["indexes"].append({"name": nm, "cols": cols, "unique": unique, "kind": kind})
    if unique and cols not in t["uniques"]:
        t["uniques"].append(cols)


def _drop_index(t: dict, name: str) -> bool:
    nm = uq(name)
    before = len(t["indexes"])
    gone = [ix for ix in t["indexes"] if ix["name"] == nm]
    t["indexes"] = [ix for ix in t["indexes"] if ix["name"] != nm]
    for ix in gone:
        if ix["unique"] and ix["cols"] in t["uniques"] and not any(i["unique"] and i["cols"] == ix["cols"] for i in t["indexes"]):
            t["uniques"].remove(ix["cols"])
    t["fks"] = [fk for fk in t["fks"] if fk.get("name") != nm]
    return len(t["indexes"]) < before


def _drop_column(t: dict, name: str) -> None:
    c = uq(name)
    t["cols"].pop(c, None)
    t["order"] = [x for x in t["order"] if x != c]
    t["indexes"] = [ix for ix in ({**ix, "cols": [x for x in ix["cols"] if x != c]} for ix in t["indexes"]) if ix["cols"]]
    t["uniques"] = [u for u in t["uniques"] if c not in u]
    t["pk"] = [] if c in t["pk"] else t["pk"]
    t["fks"] = [fk for fk in t["fks"] if c not in fk["cols"]]


def _rename_column(t: dict, old: str, new: str) -> None:
    o, n = uq(old), uq(new)
    if o not in t["cols"]:
        return
    c = t["cols"].pop(o)
    c["name"], c["raw"] = n, new.strip('`"')
    t["cols"][n] = c
    t["order"] = [n if x == o else x for x in t["order"]]
    sw = lambda cols: [n if x == o else x for x in cols]  # noqa: E731
    for ix in t["indexes"]:
        ix["cols"] = sw(ix["cols"])
    t["uniques"] = [sw(u) for u in t["uniques"]]
    t["pk"] = sw(t["pk"])
    for fk in t["fks"]:
        fk["cols"] = sw(fk["cols"])


def _pk(t: dict, cols: list) -> None:
    t["pk"] = [uq(c) for c in cols]
    for c in t["pk"]:
        if c in t["cols"]:
            t["cols"][c]["not_null"] = True
    if t["pk"] and t["pk"] not in t["uniques"]:
        t["uniques"].append(t["pk"])


def _rename_table(sch: dict, old: str, new: str) -> None:
    o, n = uq(old), uq(new)
    if o in sch["tables"]:
        t = sch["tables"].pop(o)
        t["name"], t["raw"] = n, new.strip().split(".")[-1].strip('`"')
        sch["tables"][n] = t
        for other in sch["tables"].values():
            for fk in other["fks"]:
                if fk["parent"] == o:
                    fk["parent"] = n


# ------------------------------------------------------------------------------------------------ SQL scripts
def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _cols_list(text: str) -> list[str]:
    """'(a, `b`(10) DESC, lower(c))' -> ['a', 'b', 'lower(c)']: a bare name per column, an expression kept as written."""
    out, d, cur = [], 0, ""
    for ch in text:
        if ch == "(":
            d += 1
        elif ch == ")":
            d -= 1
        if ch == "," and d == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    res = []
    for p in out:
        p = re.sub(r"\s+(asc|desc|nulls\s+(first|last)|\w+_ops)\b.*$", "", p.strip(), flags=re.I).strip()
        bare = re.fullmatch(rf"({NAME})\s*(\(\s*\d+\s*\))?", p)
        if bare:
            res.append(uq(bare.group(1)))
        elif p:
            res.append(re.sub(r"\s+", "", p.lower()))
    return res


def _default(attrs: str) -> str | None:
    m = re.search(r"\bdefault\s+('(?:[^']|'')*'|[^\s,]+)", attrs or "", re.I)
    return m.group(1) if m and m.group(1).upper() != "NULL" else None


def _create(sch: dict, stmt: str, at: str) -> None:
    m = _mask(stmt)
    head = re.match(rf"\s*create\s+(?:(?:global\s+|local\s+)?temp(?:orary)?\s+)?table\s+(if\s+not\s+exists\s+)?((?:{NAME}\.)?{NAME})", m, re.I)
    if not head:
        return
    name = uq(stmt[head.start(2):head.end(2)])
    if head.group(1) and name in sch["tables"]:
        return
    if re.match(r"\s*(like\b|as\b|\(\s*like\b)", m[head.end():], re.I) or "(" not in m[head.end():head.end() + 3]:
        t = _table(sch, name, stmt[head.start(2):head.end(2)], at)
        t["partial"] = True
        sch["unread"].append({"at": at, "why": f"CREATE TABLE {name} copies another table or a query's result: its columns aren't written here"})
        return
    d = read_design(stmt)
    if not d["order"]:
        return
    dt = d["tables"][d["order"][0]]
    if name in sch["tables"]:
        sch["notes"].append(f"{name} is created again at {at} (the later definition is used)")
    t = _table(sch, name, dt["raw"], at)
    for c in dt["cols"]:
        _col(t, c["raw"], c["type_text"] + (" unsigned" if "unsigned" in c.get("extra", []) else ""), c["not_null"], at, c["auto"],
             _default(c["attrs"]))
    for u in dt["uniques"]:
        _index(t, u, unique=True, kind="unique")
    for s, e in dt["items"]:
        item = d["mask"][s:e]
        km = re.match(rf"\s*(?:constraint\s+({NAME})\s+)?(unique\s+(?:key|index)?|unique|key|index|fulltext(?:\s+(?:key|index))?|"
                      rf"spatial(?:\s+(?:key|index))?)\s*({NAME})?\s*(?:using\s+\w+\s*)?\(", item, re.I)
        if km and not re.match(r"\s*(?:constraint\s+\S+\s+)?primary\b", item, re.I):
            kind = km.group(2).lower().split()[0]
            par = item.find("(", km.start(2))
            close = _close(item, par)
            nm = km.group(3) or km.group(1)
            nm = stmt[s:e][item.find(nm):item.find(nm) + len(nm)] if nm and item.find(nm) >= 0 else nm
            cols = _cols_list(stmt[s + par + 1:s + close])
            if kind == "unique":
                t["indexes"] = [ix for ix in t["indexes"] if not (ix["unique"] and ix["cols"] == [uq(x) for x in cols] and not ix["name"])]
            _index(t, cols, unique=kind == "unique", name=nm, kind=kind)
    if dt["pk"]:
        _pk(t, dt["pk"])
    missing = sorted({c for ix in t["indexes"] for c in ix["cols"] if re.fullmatch(r"\w+", c) and c not in t["cols"]} |
                     {c for c in t["pk"] if c not in t["cols"]})
    if missing:                                    # a key on columns the text doesn't show: only part of the table was given
        _partial(sch, t, at, f"keys name {', '.join(missing)}, which the CREATE TABLE doesn't list (only part of the table is shown)")
    for fk in dt["fks"]:
        item = d["mask"][fk["span"][0]:fk["span"][1]]
        nm = re.match(rf"\s*constraint\s+({NAME})", item, re.I)
        t["fks"].append({"cols": fk["cols"], "parent": uq(fk["parent"]), "pcols": fk["pcols"] or [], "name": uq(nm.group(1)) if nm else None})


def _close(m: str, i: int) -> int:
    d = 0
    for k in range(i, len(m)):
        if m[k] == "(":
            d += 1
        elif m[k] == ")":
            d -= 1
            if d == 0:
                return k
    return len(m)


def _split_top(text: str) -> list[str]:
    m = _mask(text)
    out, d, last = [], 0, 0
    for k, ch in enumerate(m):
        if ch in "([{":
            d += 1
        elif ch in ")]}":
            d -= 1
        elif ch == "," and d == 0:
            out.append(text[last:k])
            last = k + 1
    out.append(text[last:])
    return [x.strip() for x in out if x.strip()]


def _column_def(t: dict, text: str, at: str, not_null_default: bool = False) -> dict | None:
    """'name TYPE attrs' (an ADD COLUMN / MODIFY body) read with the CREATE TABLE reader."""
    d = read_design(f"CREATE TABLE x__ ({text})")
    dt = d["tables"].get("x__")
    if not dt or not dt["cols"]:
        return None
    c = dt["cols"][0]
    col = _col(t, c["raw"], c["type_text"] + (" unsigned" if "unsigned" in c.get("extra", []) else ""), c["not_null"] or not_null_default,
               at, c["auto"], _default(c["attrs"]))
    if dt["pk"]:
        _pk(t, dt["pk"])
    for u in dt["uniques"]:
        _index(t, u, unique=True, kind="unique")
    for fk in dt["fks"]:
        t["fks"].append({"cols": fk["cols"], "parent": uq(fk["parent"]), "pcols": fk["pcols"] or [], "name": None})
    return col


def _alter(sch: dict, stmt: str, at: str) -> None:
    m = _mask(stmt)
    head = re.match(rf"\s*alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?((?:{NAME}\.)?{NAME})\s*", m, re.I)
    if not head:
        return
    name = uq(stmt[head.start(1):head.end(1)])
    t = sch["tables"].get(name)
    if t is None:
        sch["unread"].append({"at": at, "why": f"ALTER TABLE {name}: the table isn't created in the files read"})
        return
    for act in _split_top(stmt[head.end():]):
        a = _mask(act)
        low = re.sub(r"\s+", " ", a.lower()).strip()
        r = None
        if (r := re.match(rf"add\s+(?:constraint\s+({NAME})\s+)?primary\s+key\s*(?:using\s+\w+\s*)?\(", a, re.I)):
            p = a.find("(", r.end() - 1)
            _pk(t, _cols_list(act[p + 1:_close(a, p)]))
        elif (r := re.match(rf"add\s+(?:constraint\s+({NAME})\s+)?foreign\s+key\s*(?:{NAME}\s*)?\(", a, re.I)):
            p = r.end() - 1
            cols = _cols_list(act[p + 1:_close(a, p)])
            rm = re.search(rf"references\s+((?:{NAME}\.)?{NAME})\s*(?:\(([^()]*)\))?", act, re.I)
            if rm:
                t["fks"].append({"cols": cols, "parent": uq(rm.group(1)), "pcols": _cols_list(rm.group(2) or ""),
                                 "name": uq(r.group(1)) if r.group(1) else None})
        elif (r := re.match(rf"add\s+(?:constraint\s+({NAME})\s+)?(unique|index|key|fulltext|spatial)(?:\s+(?:key|index))?\s*"
                            rf"(?:if\s+not\s+exists\s+)?({NAME})?\s*(?:using\s+\w+\s*)?\(", a, re.I)):
            p = r.end() - 1
            kind = r.group(2).lower()
            nm = r.group(3) or r.group(1)
            _index(t, _cols_list(act[p + 1:_close(a, p)]), unique=kind == "unique", name=nm, kind=kind)
        elif re.match(r"add\s+(?:constraint\s+\S+\s+)?check\b", low):
            pass
        elif (r := re.match(r"add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?", a, re.I)) and not re.match(r"add\s+(?:constraint|partition)\b", low):
            body = act[r.end():].strip()
            body = re.sub(r"\s+(first|after\s+\S+)\s*$", "", body, flags=re.I)
            if body.startswith("("):
                for part in _split_top(body[1:_close(_mask(body), 0)]):
                    _column_def(t, part, at)
            elif not _column_def(t, body, at):
                _partial(sch, t, at, f"couldn't read '{_short(act)}'")
        elif (r := re.match(rf"drop\s+(?:column\s+)?(?:if\s+exists\s+)?({NAME})\s*(?:cascade|restrict)?\s*$", act.strip(), re.I)) \
                and not re.match(r"drop\s+(index|key|primary|foreign|constraint|partition|check|default)\b", low):
            _drop_column(t, r.group(1))
        elif re.match(r"drop\s+primary\s+key", low):
            t["uniques"] = [u for u in t["uniques"] if u != t["pk"]]
            t["pk"] = []
        elif (r := re.match(rf"drop\s+(?:index|key|foreign\s+key|constraint)\s+(?:if\s+exists\s+)?({NAME})", act.strip(), re.I)):
            nm = uq(r.group(1))
            if nm.endswith("_pkey") and not any(ix["name"] == nm for ix in t["indexes"]):
                t["uniques"] = [u for u in t["uniques"] if u != t["pk"]]
                t["pk"] = []
            else:
                _drop_index(t, nm)
        elif (r := re.match(r"(?:modify|alter\s+modify)\s+(?:column\s+)?(.*)$", act.strip(), re.I | re.S)):
            body = re.sub(r"\s+(first|after\s+\S+)\s*$", "", r.group(1), flags=re.I)
            nm = re.match(NAME, body)
            old = t["cols"].get(uq(nm.group(0))) if nm else None
            c = _column_def(t, body, at)
            if not c:
                _partial(sch, t, at, f"couldn't read '{_short(act)}'")
            elif old and old["name"] in t["pk"]:
                c["not_null"] = True
        elif (r := re.match(rf"change\s+(?:column\s+)?({NAME})\s+(.*)$", act.strip(), re.I | re.S)):
            body = re.sub(r"\s+(first|after\s+\S+)\s*$", "", r.group(2), flags=re.I)
            nm = re.match(NAME, body)
            if nm and uq(nm.group(0)) != uq(r.group(1)):
                _rename_column(t, r.group(1), nm.group(0))
            if not _column_def(t, body, at):
                _partial(sch, t, at, f"couldn't read '{_short(act)}'")
        elif (r := re.match(rf"alter\s+(?:column\s+)?({NAME})\s+(.*)$", act.strip(), re.I | re.S)):
            c = t["cols"].get(uq(r.group(1)))
            rest = re.sub(r"\s+", " ", r.group(2).strip().lower())
            if c is None:
                continue
            if rest.startswith("set not null"):
                c["not_null"] = True
            elif rest.startswith("drop not null"):
                c["not_null"] = False
            elif rest.startswith(("set default", "drop default")):
                c["default"] = None if rest.startswith("drop") else rest[12:].strip()
            elif (tm := re.match(r"(?:set\s+data\s+)?type\s+(.*?)(?:\s+using\b.*)?$", r.group(2).strip(), re.I | re.S)):
                c["type"] = re.sub(r"\s+", " ", tm.group(1)).lower().strip()
            elif rest.startswith(("add generated", "set statistics", "set storage", "restart", "set (", "reset (", "set generated",
                                  "drop identity", "drop expression", "set compression")):
                pass
            else:
                _partial(sch, t, at, f"couldn't read '{_short(act)}'")
        elif (r := re.match(rf"rename\s+column\s+({NAME})\s+to\s+({NAME})", act.strip(), re.I)):
            _rename_column(t, r.group(1), r.group(2))
        elif (r := re.match(rf"rename\s+(?:index|key)\s+({NAME})\s+to\s+({NAME})", act.strip(), re.I)):
            for ix in t["indexes"]:
                if ix["name"] == uq(r.group(1)):
                    ix["name"] = uq(r.group(2))
        elif (r := re.match(rf"rename\s+(?:to\s+|as\s+)?((?:{NAME}\.)?{NAME})\s*$", act.strip(), re.I)):
            _rename_table(sch, name, r.group(1))
            name = uq(r.group(1))
        elif (r := re.match(rf"rename\s+({NAME})\s+to\s+({NAME})", act.strip(), re.I)):
            _rename_column(t, r.group(1), r.group(2))
        elif re.match(r"(engine|default\s+charset|default\s+character|character\s+set|charset|collate|auto_increment|comment|"
                      r"row_format|owner\s+to|set\s+\(|set\s+schema|set\s+tablespace|enable|disable|validate|alter\s+constraint|"
                      r"add\s+partition|drop\s+partition|algorithm|lock|convert\s+to|force|order\s+by|attach|detach|replica|"
                      r"cluster|inherit|no\s+inherit|of\b|not\s+of|set\s+(logged|unlogged|without)|force\s+row|no\s+force|"
                      r"drop\s+check|drop\s+default)", low):
            pass
        else:
            _partial(sch, t, at, f"couldn't read '{_short(act)}'")


def _partial(sch: dict, t: dict, at: str, why: str) -> None:
    t["partial"] = True
    sch["unread"].append({"at": at, "why": f"{t['name']}: {why}"})


def _short(t: str, n: int = 60) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[:n - 3] + "..."


def apply_sql(sch: dict, text: str, path: str = "") -> int:
    """Apply every schema statement of a SQL script, in order. Returns how many were applied."""
    n, cur = 0, 0
    for stmt in split_script(text):
        pos = text.find(stmt, cur)
        if pos < 0:
            pos = cur
        cur = pos + len(stmt)
        lead = re.match(r"(?:\s|--[^\n]*(?:\n|$)|#[^\n]*(?:\n|$)|/\*(?!!).*?\*/|/\*!\d*\s*.*?\*/\s*;?)*", stmt, re.S).end()
        body = stmt[lead:]
        body = re.sub(r"/\*!\d+\s*(.*?)\*/", r"\1", body, flags=re.S)        # mysqldump's /*!40101 ... */ are read as SQL
        at = f"{path}:{_line(text, pos + lead)}" if path else f"line {_line(text, pos + lead)}"
        low = _mask(body).lower().lstrip()
        if re.match(r"create\s+(?:(?:global\s+|local\s+)?temp(?:orary)?\s+)?table\b", low):
            _create(sch, body, at)
        elif re.match(r"alter\s+table\b", low):
            _alter(sch, body, at)
        elif (m := re.match(rf"create\s+(unique\s+)?index\s+(?:concurrently\s+)?(?:if\s+not\s+exists\s+)?({NAME}\s+)?on\s+(?:only\s+)?"
                            rf"((?:{NAME}\.)?{NAME})\s*(?:using\s+\w+\s*)?\(", body.lstrip(), re.I)):
            t = sch["tables"].get(uq(m.group(3)))
            b0 = body.lstrip()
            p = m.end() - 1
            if t is None:
                sch["unread"].append({"at": at, "why": f"CREATE INDEX on {uq(m.group(3))}: the table isn't created in the files read"})
            else:
                part = re.search(r"\bwhere\b", _mask(b0)[_close(_mask(b0), p):], re.I)
                _index(t, _cols_list(b0[p + 1:_close(_mask(b0), p)]), unique=bool(m.group(1)), name=(m.group(2) or "").strip() or None,
                       kind="partial" if part else ("unique" if m.group(1) else "index"))
        elif (m := re.match(r"drop\s+table\s+(?:if\s+exists\s+)?(.*?)\s*(?:cascade|restrict)?\s*$", body.strip(), re.I | re.S)):
            for nm in _split_top(m.group(1)):
                sch["tables"].pop(uq(nm), None)
        elif (m := re.match(rf"drop\s+index\s+(?:concurrently\s+)?(?:if\s+exists\s+)?((?:{NAME}\.)?{NAME})(?:\s+on\s+((?:{NAME}\.)?{NAME}))?",
                            body.strip(), re.I)):
            targets = [sch["tables"][uq(m.group(2))]] if m.group(2) and uq(m.group(2)) in sch["tables"] else list(sch["tables"].values())
            for t in targets:
                if _drop_index(t, m.group(1)):
                    break
        elif (m := re.match(r"rename\s+table\s+(.*)$", body.strip(), re.I | re.S)):
            for pair in _split_top(m.group(1)):
                pm = re.match(rf"((?:{NAME}\.)?{NAME})\s+to\s+((?:{NAME}\.)?{NAME})", pair, re.I)
                if pm:
                    _rename_table(sch, pm.group(1), pm.group(2))
        else:
            continue
        n += 1
    return n


def _up_part(text: str) -> str:
    """Only the 'up' part of a dbmate / goose / sql-migrate file."""
    d = DOWN_MARK.search(text)
    if not d:
        return text
    u = UP_MARK.search(text)
    if u and u.start() > d.start():                     # down first, then up (line numbers kept)
        return "\n" * text[:u.end()].count("\n") + text[u.end():]
    return text[:d.start()]


# ------------------------------------------------------------------------------------------------ Laravel migrations
LARAVEL = re.compile(r"\bSchema::(?:connection\([^)]*\)\s*->\s*)?(create|table|drop|dropIfExists|rename)\s*\(", re.I)
INT_TYPES = {"integer": "int", "biginteger": "bigint", "smallinteger": "smallint", "tinyinteger": "tinyint",
             "mediuminteger": "mediumint", "unsignedinteger": "int unsigned", "unsignedbiginteger": "bigint unsigned",
             "unsignedsmallinteger": "smallint unsigned", "unsignedtinyinteger": "tinyint unsigned",
             "unsignedmediuminteger": "mediumint unsigned"}
INCREMENTS = {"id": "bigint unsigned", "bigincrements": "bigint unsigned", "increments": "int unsigned",
              "smallincrements": "smallint unsigned", "tinyincrements": "tinyint unsigned", "mediumincrements": "mediumint unsigned"}
PLAIN_TYPES = {"text": "text", "mediumtext": "mediumtext", "longtext": "longtext", "tinytext": "tinytext", "date": "date",
               "datetime": "datetime", "datetimetz": "datetime", "timestamp": "timestamp", "timestamptz": "timestamp",
               "time": "time", "timetz": "time", "year": "year", "json": "json", "jsonb": "jsonb", "boolean": "boolean",
               "binary": "blob", "uuid": "uuid", "ulid": "char(26)", "ipaddress": "varchar(45)", "macaddress": "varchar(17)",
               "float": "float", "double": "double", "geometry": "geometry", "point": "point", "softdeletes": "timestamp",
               "softdeletestz": "timestamp", "enum": "enum", "set": "set", "foreignuuid": "char(36)", "foreignulid": "char(26)",
               "vector": "vector"}
TABLE_OPS = {"primary", "unique", "index", "fulltext", "spatialindex", "foreign", "dropcolumn", "dropcolumns", "renamecolumn",
             "dropindex", "dropunique", "dropforeign", "dropprimary", "dropfulltext", "dropspatialindex", "droptimestamps",
             "droptimestampstz", "dropsoftdeletes", "dropsoftdeletestz", "dropremembertoken", "dropmorphs", "rename",
             "renameindex", "engine", "charset", "collation", "comment", "temporary", "dropconstrainedforeignid",
             "dropforeignidfor", "dropconstrainedforeignidfor", "droptimestamp"}
IGNORED_MODS = {"after", "first", "comment", "charset", "collation", "storedas", "virtualas", "usecurrent", "usecurrentonupdate",
                "cascadeondelete", "cascadeonupdate", "restrictondelete", "restrictonupdate", "nullondelete", "noactionondelete",
                "ondelete", "onupdate", "invisible", "from", "startingvalue", "generatedas", "always", "persisted", "isgeometry",
                "projection", "deferrable", "initiallyimmediate", "notinitiallyimmediate", "lang", "algorithm", "language",
                "cascadeondeleteandupdate", "fulltext", "spatialindex"}


def _php_args(text: str) -> list:
    """The arguments of a PHP call, as Python values where they are plain literals (strings, numbers, true/false/null,
    arrays of those, Model::class), else '?code'. Named arguments (length: 100) come back as ('name', value)."""
    out = []
    for raw in _split_top(text):
        name = None
        nm = re.match(r"([A-Za-z_]\w*)\s*:(?!:)\s*(.*)$", raw, re.S)
        if nm:
            name, raw = nm.group(1), nm.group(2).strip()
        val = _php_value(raw)
        out.append((name, val) if name else val)
    return out


def _php_value(raw: str):
    raw = raw.strip()
    if re.fullmatch(r"'(?:[^'\\]|\\.)*'", raw) or re.fullmatch(r'"(?:[^"\\$]|\\.)*"', raw):
        return raw[1:-1].replace("\\'", "'")
    if re.fullmatch(r"-?\d+(\.\d+)?", raw):
        return float(raw) if "." in raw else int(raw)
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    if raw.lower() == "null":
        return None
    if raw.startswith("[") and raw.endswith("]"):
        return [_php_value(x) for x in _split_top(raw[1:-1])]
    cm = re.fullmatch(r"\\?(?:[A-Za-z_]\w*\\)*([A-Za-z_]\w*)::class", raw)
    if cm:
        return ("class", cm.group(1))
    return "?code"


def _calls(chain: str) -> list | None:
    """'->string('email')->nullable()->unique()' -> [('string', [...]), ('nullable', []), ('unique', [])]."""
    out, i, m = [], 0, _mask(chain)
    while i < len(chain):
        mm = re.match(r"\s*->\s*([A-Za-z_]\w*)\s*\(", m[i:])
        if not mm:
            return out if not chain[i:].strip() else None
        p = i + mm.end() - 1
        q = _close(m, p)
        out.append((mm.group(1), _php_args(chain[p + 1:q])))
        i = q + 1
    return out


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _plural(word: str) -> str:
    """Laravel's English plural for the usual table nouns (user -> users, category -> categories, address -> addresses)."""
    w = word.lower()
    irregular = {"person": "people", "child": "children", "man": "men", "woman": "women", "status": "statuses"}
    if w in irregular:
        return irregular[w]
    if re.search(r"[^aeiou]y$", w):
        return w[:-1] + "ies"
    if re.search(r"(s|x|z|ch|sh)$", w):
        return w + "es"
    return w + "s"


def _str(args: list, k: int, default=None):
    pos = [a for a in args if not isinstance(a, tuple) or a[0] == "class"]
    v = pos[k] if len(pos) > k else default
    return v


def _named(args: list, name: str, k: int, default=None):
    for a in args:
        if isinstance(a, tuple) and a[0] == name:
            return a[1]
    return _str(args, k, default)


def _laravel_index_name(table: str, cols: list, kind: str) -> str:
    return re.sub(r"[-.]", "_", f"{table}_{'_'.join(cols)}_{kind}").lower()


def _blueprint(sch: dict, t: dict, body: str, base_line: int, path: str, creating: bool) -> None:
    m = _mask(body)
    var = re.search(r"function\s*\(\s*(?:Blueprint\s+)?\$(\w+)", body)
    v = var.group(1) if var else "table"
    a = m.find("{")
    if a < 0:
        _partial(sch, t, f"{path}:{base_line}", "couldn't find the Blueprint body")
        return
    b = _close(m.replace("{", "(").replace("}", ")"), a)
    inner, im = body[a + 1:b], m[a + 1:b]
    pos = 0
    for stmt in re.split(r";", im):
        st_raw = inner[pos:pos + len(stmt)]
        st_at = a + 1 + pos
        pos += len(stmt) + 1
        s = stmt.strip()
        if not s or re.fullmatch(r"(//[^\n]*\s*|/\*.*?\*/\s*)*", s, re.S):
            continue
        lead = len(st_raw) - len(st_raw.lstrip())
        at = f"{path}:{base_line + body.count(chr(10), 0, st_at + lead)}"
        code = re.sub(r"^(?:\s|//[^\n]*\n|/\*.*?\*/)*", "", st_raw, flags=re.S)
        cm = re.match(rf"\${v}\s*(->.*)$", code, re.S)
        calls = _calls(cm.group(1)) if cm else None
        if not calls:
            _partial(sch, t, at, f"couldn't read '{_short(code)}'")
            continue
        _laravel_stmt(sch, t, calls, at, creating)


def _laravel_stmt(sch: dict, t: dict, calls: list, at: str, creating: bool) -> None:
    head, args = calls[0][0].lower(), calls[0][1]
    mods = [(n.lower(), a) for n, a in calls[1:]]
    tn = t["name"]

    def cols_of(x):
        x = x if x is not None else []
        return [x] if isinstance(x, str) else [c for c in x if isinstance(c, str)] if isinstance(x, list) else None

    if head in TABLE_OPS:
        if head in ("primary", "unique", "index", "fulltext", "spatialindex"):
            cols = cols_of(_str(args, 0))
            if not cols:
                return _partial(sch, t, at, f"couldn't read the columns of {head}()")
            nm = _named(args, "name", 1) if head != "primary" else None
            if head == "primary":
                _pk(t, cols)
                _index(t, cols, unique=True, name=_laravel_index_name(tn, cols, "primary"), kind="primary")
            else:
                kind = {"spatialindex": "spatialindex"}.get(head, head)
                _index(t, cols, unique=head == "unique", name=nm if isinstance(nm, str) else _laravel_index_name(tn, cols, kind), kind=head)
        elif head == "foreign":
            cols = cols_of(_str(args, 0))
            ref = next((cols_of(a) for n, a in mods if n == "references"), None)
            on = next((_str(a, 0) for n, a in mods if n == "on"), None)
            if not cols or not isinstance(on, str):
                return _partial(sch, t, at, "couldn't read the foreign key")
            nm = _named(args, "name", 1)
            t["fks"].append({"cols": [uq(c) for c in cols], "parent": uq(on), "pcols": [uq(c) for c in (ref or ["id"])],
                             "name": nm if isinstance(nm, str) else _laravel_index_name(tn, cols, "foreign")})
        elif head in ("dropcolumn", "dropcolumns"):
            cols = [c for a in args for c in (cols_of(a) or [])]
            for c in cols:
                _drop_column(t, c)
        elif head == "renamecolumn":
            o, n = _str(args, 0), _str(args, 1)
            if isinstance(o, str) and isinstance(n, str):
                _rename_column(t, o, n)
        elif head in ("dropindex", "dropunique", "dropforeign", "dropprimary", "dropfulltext", "dropspatialindex"):
            x = _str(args, 0)
            if head == "dropprimary":
                t["uniques"] = [u for u in t["uniques"] if u != t["pk"]]
                t["indexes"] = [ix for ix in t["indexes"] if ix["kind"] != "primary"]
                t["pk"] = []
                return
            kind = {"dropindex": "index", "dropunique": "unique", "dropforeign": "foreign", "dropfulltext": "fulltext",
                    "dropspatialindex": "spatialindex"}[head]
            nm = x if isinstance(x, str) else _laravel_index_name(tn, cols_of(x) or [], kind) if isinstance(x, list) else None
            if nm:
                _drop_index(t, nm)
        elif head in ("dropconstrainedforeignid",):
            c = _str(args, 0)
            if isinstance(c, str):
                _drop_index(t, _laravel_index_name(tn, [c], "foreign"))
                _drop_column(t, c)
        elif head in ("droptimestamps", "droptimestampstz"):
            _drop_column(t, "created_at")
            _drop_column(t, "updated_at")
        elif head in ("dropsoftdeletes", "dropsoftdeletestz"):
            _drop_column(t, _str(args, 0, "deleted_at") or "deleted_at")
        elif head == "dropremembertoken":
            _drop_column(t, "remember_token")
        elif head == "dropmorphs":
            n = _str(args, 0)
            if isinstance(n, str):
                _drop_column(t, f"{n}_type")
                _drop_column(t, f"{n}_id")
        elif head == "rename":
            n = _str(args, 0)
            if isinstance(n, str):
                _rename_table(sch, tn, n)
        elif head == "renameindex":
            o, n = _str(args, 0), _str(args, 1)
            for ix in t["indexes"]:
                if isinstance(o, str) and ix["name"] == uq(o):
                    ix["name"] = uq(n)
        return
    # ---- a column (or a group of columns)
    nullable = None
    for n, a in mods:
        if n == "nullable":
            nullable = (_str(a, 0, True) is not False)
    unknown = [n for n, _a in mods if n not in IGNORED_MODS | {"nullable", "default", "unsigned", "unique", "index", "primary",
                                                                "autoincrement", "change", "constrained", "references", "on",
                                                                "nullable", "length", "total", "places", "precision", "fulltext"}]
    if head in ("timestamps", "timestampstz", "nullabletimestamps", "datetimes"):
        ty = "datetime" if head == "datetimes" else "timestamp"
        _col(t, "created_at", ty, False, at)
        _col(t, "updated_at", ty, False, at)
        return
    if head == "remembertoken":
        _col(t, "remember_token", "varchar(100)", False, at)
        return
    if head in ("morphs", "nullablemorphs", "uuidmorphs", "nullableuuidmorphs", "ulidmorphs", "nullableulidmorphs", "numericmorphs"):
        n = _str(args, 0)
        if not isinstance(n, str):
            return _partial(sch, t, at, f"couldn't read {head}()")
        null = head.startswith("nullable")
        _col(t, f"{n}_type", "varchar(255)", not null, at)
        _col(t, f"{n}_id", "char(36)" if "uuid" in head else "char(26)" if "ulid" in head else "bigint unsigned", not null, at)
        _index(t, [f"{n}_type", f"{n}_id"], name=_str(args, 1) if isinstance(_str(args, 1), str) else
               _laravel_index_name(tn, [f"{n}_type", f"{n}_id"], "index"))
        return
    name = _str(args, 0)
    if head == "id" and name is None:
        name = "id"
    if head == "foreignidfor":
        cls = _str(args, 0)
        name = _str(args, 1) if isinstance(_str(args, 1), str) else (f"{_snake(cls[1])}_id" if isinstance(cls, tuple) else None)
    if head in ("softdeletes", "softdeletestz") and name is None:
        name = "deleted_at"
    if not isinstance(name, str):
        return _partial(sch, t, at, f"couldn't read the column name of {calls[0][0]}()")
    auto = False
    if head in INCREMENTS:
        ty, auto = INCREMENTS[head], True
    elif head in INT_TYPES:
        ty = INT_TYPES[head]
        auto = _named(args, "autoIncrement", 1) is True
    elif head in ("foreignid", "foreignidfor"):
        ty = "bigint unsigned"
    elif head in ("string", "char"):
        ln = _named(args, "length", 1, 255)
        ty = f"{'varchar' if head == 'string' else 'char'}({ln if isinstance(ln, int) else 255})"
    elif head in ("decimal", "unsigneddecimal"):
        tot, pl = _named(args, "total", 1, 8), _named(args, "places", 2, 2)
        ty = f"decimal({tot if isinstance(tot, int) else 8},{pl if isinstance(pl, int) else 2})"
    elif head in PLAIN_TYPES:
        ty = PLAIN_TYPES[head]
    else:
        return _partial(sch, t, at, f"unknown column method {calls[0][0]}()")
    if unknown:
        sch["notes"].append(f"{at}: {', '.join(unknown)}() not read (no effect on keys, types or NULLs assumed)")
    for n, a in mods:
        if n == "unsigned" and "unsigned" not in ty and re.match(r"(big|small|tiny|medium)?int", ty):
            ty += " unsigned"
        if n == "autoincrement":
            auto = True
    not_null = True if head not in ("softdeletes", "softdeletestz") else False
    if nullable is not None:
        not_null = not nullable
    default = next((str(_str(a, 0)) for n, a in mods if n == "default"), None)
    changing = any(n == "change" for n, _a in mods)
    if changing and uq(name) not in t["cols"]:
        return _partial(sch, t, at, f"->change() of {name}, which isn't in the table read")
    _col(t, name, ty, not_null, at, auto, default)
    if auto or head in INCREMENTS or any(n == "primary" for n, _a in mods):
        _pk(t, [name])
        _index(t, [name], unique=True, name=_laravel_index_name(tn, [name], "primary"), kind="primary")
    for n, a in mods:
        if n == "unique" and _str(a, 0) is not False:
            nm = _str(a, 0)
            _index(t, [name], unique=True, name=nm if isinstance(nm, str) else _laravel_index_name(tn, [name], "unique"), kind="unique")
        elif n == "index" and _str(a, 0) is not False:
            nm = _str(a, 0)
            _index(t, [name], name=nm if isinstance(nm, str) else _laravel_index_name(tn, [name], "index"))
        elif n == "fulltext":
            _index(t, [name], name=_laravel_index_name(tn, [name], "fulltext"), kind="fulltext")
        elif n == "constrained":
            parent = _named(a, "table", 0)
            pcol = _named(a, "column", 1, "id")
            if not isinstance(parent, str):
                base = re.sub(r"_id$", "", name)
                parent = _plural(base) if base != name else None
                if head == "foreignidfor" and isinstance(_str(args, 0), tuple):
                    parent = _plural(_snake(_str(args, 0)[1]))
            if not parent:
                return _partial(sch, t, at, f"couldn't tell which table {name} points at")
            nm = _named(a, "indexName", 2)
            t["fks"].append({"cols": [uq(name)], "parent": uq(parent), "pcols": [uq(pcol if isinstance(pcol, str) else "id")],
                             "name": nm if isinstance(nm, str) else _laravel_index_name(tn, [name], "foreign")})
        elif n == "references":
            ref = _str(a, 0)
            on = next((_str(a2, 0) for n2, a2 in mods if n2 == "on"), None)
            if isinstance(on, str):
                t["fks"].append({"cols": [uq(name)], "parent": uq(on), "pcols": [uq(ref if isinstance(ref, str) else "id")],
                                 "name": _laravel_index_name(tn, [name], "foreign")})


def _up_body(text: str) -> tuple[str, int]:
    """The body of a migration's up() method, and its offset (the whole file when there is none)."""
    m = _mask(text)
    u = re.search(r"\bfunction\s+up\s*\([^)]*\)\s*(?::\s*\??\w+\s*)?\{", m, re.I)
    if not u:
        return text, 0
    a = u.end() - 1
    b = _close(m.replace("{", "(").replace("}", ")"), a)
    return text[a:b + 1], a


def apply_laravel(sch: dict, text: str, path: str = "") -> int:
    body, off = _up_body(text)
    m = _mask(body)
    n = 0
    for sm in LARAVEL.finditer(m):
        op = sm.group(1).lower()
        p = sm.end() - 1
        q = _close(m, p)
        args_text = body[p + 1:q]
        line = text.count("\n", 0, off + sm.start()) + 1
        at = f"{path}:{line}"
        first = _split_top(args_text)
        name = _php_value(first[0]) if first else None
        if not isinstance(name, str):
            sch["unread"].append({"at": at, "why": f"Schema::{sm.group(1)} with a table name worked out in code"})
            continue
        if op == "create":
            t = _table(sch, name, name, at)
            _blueprint(sch, t, args_text[len(first[0]) + 1:] if first else "", line, path, True)
        elif op == "table":
            t = sch["tables"].get(uq(name))
            if t is None:
                sch["unread"].append({"at": at, "why": f"Schema::table('{name}') changes a table that isn't created in the files read"})
                continue
            _blueprint(sch, t, args_text[len(first[0]) + 1:], line, path, False)
        elif op in ("drop", "dropifexists"):
            sch["tables"].pop(uq(name), None)
        elif op == "rename":
            to = _php_value(first[1]) if len(first) > 1 else None
            if isinstance(to, str):
                _rename_table(sch, name, to)
        n += 1
    for dm in re.finditer(r"\bDB::(?:statement|unprepared)\s*\(", m):
        p = dm.end() - 1
        q = _close(m, p)
        sql = _php_value(_split_top(body[p + 1:q])[0]) if _split_top(body[p + 1:q]) else None
        line = text.count("\n", 0, off + dm.start()) + 1
        if isinstance(sql, str) and SCHEMA_WORDS.search(sql):
            n += apply_sql(sch, sql, f"{path}:{line}") and 1
        elif not isinstance(sql, str):
            sch["unread"].append({"at": f"{path}:{line}", "why": "DB::statement with SQL built in code"})
    if not n and re.search(r"\bSchema::|->(?:create|table)\s*\(\s*['\"]", m):
        sch["unread"].append({"at": f"{path}:1", "why": "schema calls that aren't read (a builder other than Schema::)"})
    return n


# ------------------------------------------------------------------------------------------------ a whole project
def _version_key(path: str):
    base = os.path.basename(path)
    m = VERSIONED.match(base)
    if m:
        return (0, [int(x) for x in re.split(r"[._]", m.group(1))], base.lower())
    return (1, [], base.lower())


def _files(root: str):
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        for f in sorted(files):
            if f.lower().endswith((".sql", ".php")):
                yield os.path.join(d, f)


def _read(p: str) -> str | None:
    try:
        if os.path.getsize(p) > MAX_BYTES:
            return None
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def read_project(root: str) -> dict:
    """The schema a project's files create (read only). See the module text for what is read and in which order."""
    sch = empty()
    if not os.path.isdir(root):
        return sch
    dumps, migrations = [], []
    for p in _files(root):
        rel = os.path.relpath(p, root)
        base = os.path.basename(p)
        if p.lower().endswith(".php"):
            if not MIGRATION_DIR.search(os.path.dirname(rel)):
                continue
            text = _read(p)
            if text and LARAVEL.search(text) or text and re.search(r"\bDB::(?:statement|unprepared)\s*\(", text or "") and \
                    re.search(r"extends\s+Migration\b", text or ""):
                migrations.append((rel, text, "laravel"))
            continue
        if UNDO_FILE.search(base) or re.search(r"(^|[\\/])(rollbacks?|down|undo)([\\/]|$)", os.path.dirname(rel), re.I):
            continue
        text = _read(p)
        if not text or not SCHEMA_WORDS.search(_mask(text)):
            continue
        if MIGRATION_DIR.search(os.path.dirname(rel)) or VERSIONED.match(base) and re.match(r"(?:V\d|\d{3,})", base):
            migrations.append((rel, _up_part(text), "sql"))
        else:
            dumps.append((rel, text, "sql"))
    in_dump = set()
    for rel, text, _k in sorted(dumps, key=lambda x: x[0]):
        n = apply_sql(sch, text, rel)
        if n:
            sch["files"].append(rel)
            sch["applied"] += n
        for im in re.finditer(r"insert\s+into\s+[`\"]?migrations[`\"]?[^;]*?values\s*(.*?);", text, re.I | re.S):
            in_dump |= set(re.findall(r"'(\d{4}_\d{2}_\d{2}_\d{6}_[\w]+)'", im.group(1)))
    for rel, text, kind in sorted(migrations, key=lambda x: _version_key(x[0])):
        if kind == "laravel" and os.path.splitext(os.path.basename(rel))[0] in in_dump:
            continue
        n = apply_laravel(sch, text, rel) if kind == "laravel" else apply_sql(sch, text, rel)
        if n:
            sch["files"].append(rel)
            sch["applied"] += n
    return sch


def from_text(text: str) -> dict:
    """The schema of CREATE TABLE / ALTER / CREATE INDEX statements pasted in the chat (or given with a query)."""
    sch = empty()
    sch["origin"] = "paste"
    sch["applied"] = apply_sql(sch, text or "", "")
    return sch


# ------------------------------------------------------------------------------------------------ questions asked of it
def table(sch: dict, name: str) -> dict | None:
    return (sch or {}).get("tables", {}).get(uq(name)) if name else None


def column(sch: dict, tname: str, cname: str) -> dict | None:
    t = table(sch, tname)
    return t["cols"].get(uq(cname)) if t else None


def is_unique(t: dict, cols: list) -> bool:
    """Do these columns pick at most one row? (some PRIMARY KEY / UNIQUE is made only of them; NULLs in a UNIQUE column
    don't repeat a value, so a UNIQUE key still gives at most one row per real value)."""
    have = {uq(c) for c in cols}
    return any(u and set(u) <= have for u in t["uniques"] + ([t["pk"]] if t["pk"] else []))


def indexes_leading(t: dict, col: str, family_: str | None = None) -> list[dict]:
    """The indexes that start with this column, plus (on MySQL/MariaDB, where InnoDB makes one for every foreign key) the
    one a foreign key on it gets."""
    c = uq(col)
    out = [ix for ix in t["indexes"] if ix["cols"] and ix["cols"][0] == c and ix["kind"] not in ("fulltext", "spatial", "spatialindex", "partial")]
    if t["pk"] and t["pk"][0] == c and not any(ix["kind"] == "primary" for ix in out):
        out.append({"name": "PRIMARY KEY", "cols": t["pk"], "unique": True, "kind": "primary"})
    if not out and family_ == "mysql":
        for fk in t["fks"]:
            if fk["cols"] and fk["cols"][0] == c:
                out.append({"name": fk.get("name") or f"{c} (made for the foreign key)", "cols": fk["cols"], "unique": False,
                            "kind": "fk_auto"})
    return out


def summary(sch: dict) -> str:
    n = len(sch["tables"])
    if not n:
        return ""
    partial = [t["name"] for t in sch["tables"].values() if t["partial"]]
    s = f"{n} table{'s' if n != 1 else ''} from {len(sch['files'])} file{'s' if len(sch['files']) != 1 else ''}"
    if partial:
        s += f" ({', '.join(partial[:5])} only partly read)"
    return s


def ddl(sch: dict) -> str:
    """The schema as plain CREATE TABLE / CREATE INDEX text (for the index reader that takes pasted structure)."""
    out = []
    for t in sch["tables"].values():
        cols = [f"{c['name']} {c['type'] or 'text'}{' NOT NULL' if c['not_null'] else ''}" for c in (t["cols"][k] for k in t["order"])]
        if t["pk"]:
            cols.append(f"PRIMARY KEY ({', '.join(t['pk'])})")
        out.append(f"CREATE TABLE {t['name']} (\n  " + ",\n  ".join(cols) + "\n);")
        for ix in t["indexes"]:
            if ix["kind"] in ("primary", "fulltext", "spatial", "spatialindex", "partial") or not all(re.fullmatch(r"\w+", c) for c in ix["cols"]):
                continue
            out.append(f"CREATE {'UNIQUE ' if ix['unique'] else ''}INDEX {ix['name'] or 'ix_' + '_'.join(ix['cols'])} ON {t['name']} "
                       f"({', '.join(ix['cols'])});")
    return "\n".join(out)
