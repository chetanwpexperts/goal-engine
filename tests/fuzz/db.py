"""The database agent's test generator: random schemas from many fields, created on the real test databases
(MySQL/MariaDB shop_db and PostgreSQL shop_pg), random queries people really write (wrapped in code, with comments, odd
spacing), random follow-up questions in English / Hinglish / Hindi, and new ways of describing every problem playbook.
The truth for every check is worked out here, from the generated schema, the data and the engines themselves, never by
the agent.

    python -m tests.fuzz.db --start 1 --seeds 150 --json out.json   # a big run: pass rate per family and every failure
    python -m tests.fuzz.db --seed 7 --show                          # one seed, every item printed

Families:
  queries    analyze_sql on random SELECT/UPDATE/DELETE: unwrap, rewrite gives the same rows, indexes are real and run,
             verified/unverified wording, findings match what was injected
  followups  a chat through the API: the slow-database message, the query, then follow-ups (optimise, what changed,
             why slow, which indexes, explain)
  phrasings  new descriptions of every playbook in app/dbknow/issues.json: the right playbook, or "not sure"
  diagnosis  randomly injected evidence for known causes plus healthy controls

Tables are named fz<seed>_* and dropped afterwards. An engine whose server is down is skipped.
"""

import argparse
import datetime as dt
import json
import os
import random
import re
import sys
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal

MYSQL = {"type": "mysql", "host": "127.0.0.1", "port": 3306, "user": "shopapp", "password": "app pass", "database": "shop_db"}
PG = {"type": "postgres", "host": "127.0.0.1", "port": 5432, "user": "shopapp", "password": "app pass", "database": "shop_pg"}

F_NAMES = ["Aditi", "Pooja", "Neha", "Simran", "Kavya", "Riya", "Anjali", "Sneha", "Isha", "Meera", "Tanvi", "Priya"]
M_NAMES = ["Arjun", "Rohan", "Rahul", "Vikram", "Aman", "Karan", "Dev", "Ishaan", "Manav", "Yash", "Aarav", "Kabir"]
LAST = ["Sharma", "Verma", "Gill", "Khan", "Singh", "Mehta", "Rana", "Joshi", "Bansal", "Kaur", "Malik", "Gupta"]
CITIES = ["Mohali", "Kharar", "Zirakpur", "Panchkula", "Ludhiana", "Amritsar", "Jalandhar"]
WORDS = ["late", "ok", "follow up", "pending docs", "paid in part", "urgent", "regular", "new", "returned", "called"]

# ---------------------------------------------------------------- fields: tables and their columns
# column: (name, kind, params)   kinds: int, money, cat, name, text, date, datetime, fk (params = parent table)
DOMAINS = {
    "school": [
        ("classes", [("class_name", "cat", ["Nursery", "LKG", "First", "Second", "Third"]), ("section", "cat", ["A", "B", "C"]),
                     ("room_no", "int", (1, 40))]),
        ("students", [("student_name", "name", None), ("class_id", "fk", "classes"), ("gender", "cat", ["M", "F"]),
                      ("dob", "date", (2010, 2018)), ("admission_date", "datetime", (2023, 2026)), ("city", "cat", CITIES),
                      ("status", "cat", ["active", "left", "passed"])]),
        ("fee_payments", [("student_id", "fk", "students"), ("amount", "money", (500, 5000)), ("paid_on", "datetime", (2025, 2026)),
                          ("mode", "cat", ["cash", "upi", "card", "cheque"]), ("remarks", "text", WORDS)])],
    "shop": [
        ("customers", [("customer_name", "name", None), ("email", "email", None), ("city", "cat", CITIES),
                       ("created_at", "datetime", (2024, 2026))]),
        ("products", [("product_name", "cat", ["Soap", "Rice 5kg", "Oil 1L", "Tea", "Sugar", "Atta"]),
                      ("category", "cat", ["grocery", "personal", "kitchen"]), ("price", "money", (20, 900)), ("stock", "int", (0, 300))]),
        ("orders", [("customer_id", "fk", "customers"), ("order_date", "datetime", (2025, 2026)),
                    ("status", "cat", ["new", "packed", "shipped", "cancelled"]), ("total", "money", (50, 9000))]),
        ("order_items", [("order_id", "fk", "orders"), ("product_id", "fk", "products"), ("qty", "int", (1, 12)),
                         ("price", "money", (20, 900))])],
    "hr": [
        ("departments", [("dept_name", "cat", ["Sales", "Accounts", "HR", "IT", "Ops"]), ("location", "cat", CITIES)]),
        ("employees", [("emp_name", "name", None), ("department_id", "fk", "departments"),
                       ("designation", "cat", ["Executive", "Manager", "Intern", "Lead"]), ("joining_date", "date", (2015, 2026)),
                       ("salary", "money", (15000, 120000)), ("email", "email", None), ("status", "cat", ["Active", "Left"])]),
        ("attendance", [("employee_id", "fk", "employees"), ("att_date", "date", (2026, 2026)), ("check_in", "datetime", (2026, 2026)),
                        ("status", "cat", ["present", "absent", "half day", "leave"])])],
    "clinic": [
        ("doctors", [("doctor_name", "name", None), ("speciality", "cat", ["General", "Child", "Skin", "Bones"]), ("fee", "money", (300, 1500))]),
        ("patients", [("patient_name", "name", None), ("gender", "cat", ["Male", "Female"]), ("phone", "phone", None),
                      ("city", "cat", CITIES), ("registered_at", "datetime", (2024, 2026))]),
        ("visits", [("patient_id", "fk", "patients"), ("doctor_id", "fk", "doctors"), ("visit_date", "datetime", (2025, 2026)),
                    ("bill_amount", "money", (300, 15000)), ("payment_status", "cat", ["Paid", "Pending"])])],
    "hotel": [
        ("rooms", [("room_type", "cat", ["Deluxe", "Suite", "Standard"]), ("floor", "int", (0, 6)), ("rate", "money", (1500, 9000))]),
        ("guests", [("guest_name", "name", None), ("email", "email", None), ("city", "cat", CITIES)]),
        ("bookings", [("guest_id", "fk", "guests"), ("room_id", "fk", "rooms"), ("check_in", "date", (2026, 2026)), ("nights", "int", (1, 7)),
                      ("amount", "money", (2000, 60000)), ("source", "cat", ["Website", "Walk-in", "Agent", "OTA"]),
                      ("status", "cat", ["booked", "checked out", "cancelled"])])],
    "inventory": [
        ("warehouses", [("wh_name", "cat", ["Main", "North", "South", "Depot"]), ("city", "cat", CITIES)]),
        ("items", [("item_name", "cat", ["Bolt", "Nut", "Washer", "Pipe", "Valve", "Tap"]), ("sku", "sku", None),
                   ("category", "cat", ["hardware", "plumbing"]), ("unit_price", "money", (2, 800))]),
        ("stock_moves", [("item_id", "fk", "items"), ("warehouse_id", "fk", "warehouses"), ("moved_at", "datetime", (2025, 2026)),
                         ("qty", "int", (1, 500)), ("move_type", "cat", ["in", "out", "transfer"])])],
    "fees": [
        ("courses", [("course_name", "cat", ["Tally", "Excel", "Spoken English", "Coding", "Design"]), ("duration_months", "int", (1, 12)),
                     ("fee", "money", (2000, 40000))]),
        ("batches", [("course_id", "fk", "courses"), ("batch_name", "cat", ["Morning", "Evening", "Weekend"]), ("start_date", "date", (2025, 2026))]),
        ("enrollments", [("batch_id", "fk", "batches"), ("learner_name", "name", None), ("enrolled_on", "datetime", (2025, 2026)),
                         ("fee_paid", "money", (0, 40000)), ("status", "cat", ["active", "dropped", "completed"])])],
    "attendance": [
        ("staff", [("staff_name", "name", None), ("shift", "cat", ["day", "night", "general"]), ("phone", "phone", None)]),
        ("attendance_log", [("staff_id", "fk", "staff"), ("log_date", "date", (2026, 2026)), ("in_time", "datetime", (2026, 2026)),
                            ("status", "cat", ["P", "A", "L"]), ("late_minutes", "int", (0, 90))])],
    "loans": [
        ("borrowers", [("borrower_name", "name", None), ("city", "cat", CITIES), ("phone", "phone", None),
                       ("kyc_status", "cat", ["done", "pending", "rejected"])]),
        ("loans", [("borrower_id", "fk", "borrowers"), ("principal", "money", (10000, 500000)), ("rate", "money", (8, 24)),
                   ("disbursed_on", "date", (2023, 2026)), ("status", "cat", ["open", "closed", "overdue"])]),
        ("repayments", [("loan_id", "fk", "loans"), ("paid_on", "datetime", (2024, 2026)), ("amount", "money", (500, 50000)),
                        ("mode", "cat", ["cash", "upi", "nach"])])],
}
SINGULAR = {"classes": "class", "students": "student", "fee_payments": "payment", "customers": "customer", "products": "product",
            "orders": "order", "order_items": "item", "departments": "department", "employees": "employee", "attendance": "attendance",
            "doctors": "doctor", "patients": "patient", "visits": "visit", "rooms": "room", "guests": "guest", "bookings": "booking",
            "warehouses": "warehouse", "items": "item", "stock_moves": "move", "courses": "course", "batches": "batch",
            "enrollments": "enrollment", "staff": "staff", "attendance_log": "log", "borrowers": "borrower", "loans": "loan",
            "repayments": "repayment"}
RESERVED = {"or", "do", "as", "in", "is", "on", "by", "to", "if", "at", "of"}
TEXT_KINDS = ("cat", "name", "text", "email", "phone", "sku")


def _camel(name: str, upper: bool) -> str:
    parts = name.split("_")
    out = "".join(p.capitalize() for p in parts)
    return out if upper else out[0].lower() + out[1:]


# ---------------------------------------------------------------- making a schema and its data
def make_schema(seed: int) -> dict:
    rnd = random.Random(seed)
    domain = rnd.choice(sorted(DOMAINS))
    style = rnd.choice(["snake"] * 6 + ["camel", "Camel"])
    name_col = (lambda n: n) if style == "snake" else (lambda n: _camel(n, style == "Camel"))
    tables = {}
    for base, cols in DOMAINS[domain]:
        tname = f"fz{seed}_{base}"
        pk = rnd.choice(["id", "id", f"{SINGULAR[base]}_id"])
        tcols = [{"name": name_col(pk), "kind": "pk", "params": None}]
        for c, kind, p in cols:
            if kind == "fk":
                tcols.append({"name": None, "kind": "fk", "params": f"fz{seed}_{p}", "base": c})
            elif rnd.random() < 0.9 or kind in ("name",):
                tcols.append({"name": name_col(c), "kind": kind, "params": p})
        tables[tname] = {"name": tname, "base": base, "pk": name_col(pk), "cols": tcols, "fks": [], "indexes": []}
    for t in tables.values():                     # foreign keys point at the parent's real primary key
        for c in t["cols"]:
            if c["kind"] == "fk":
                parent = tables[c["params"]]
                c["name"] = name_col(c["base"]) if parent["pk"].lower() == "id" else parent["pk"]
                c["ref"] = parent["name"]
                if rnd.random() < 0.6:
                    t["fks"].append((c["name"], parent["name"], parent["pk"]))
    for t in tables.values():                     # some indexes are already there
        plain = [c["name"] for c in t["cols"] if c["kind"] != "pk"]
        for c in plain:
            if rnd.random() < 0.25:
                t["indexes"].append([c])
        if len(plain) >= 2 and rnd.random() < 0.3:
            t["indexes"].append(rnd.sample(plain, 2))
        seen, uniq = set(), []
        for ix in t["indexes"]:
            if tuple(ix) not in seen:
                seen.add(tuple(ix))
                uniq.append(ix)
        t["indexes"] = uniq
    # data
    order = list(tables)                          # parents come first in DOMAINS
    for tname in order:
        t = tables[tname]
        n = rnd.randint(25, 140)
        rows = []
        for k in range(n):
            r = []
            for c in t["cols"]:
                r.append(_value(c, k, rnd, tables))
            rows.append(r)
        t["rows"] = rows
    return {"seed": seed, "domain": domain, "style": style, "tables": tables, "order": order}


def _value(c, k, rnd, tables):
    kind, p = c["kind"], c["params"]
    if kind == "pk":
        return k + 1
    if rnd.random() < 0.08:
        return None
    if kind == "fk":
        parent = tables[p]
        return rnd.randint(1, len(parent["rows"]))
    if kind == "int":
        return rnd.randint(*p)
    if kind == "money":
        return Decimal(rnd.randint(p[0] * 100, p[1] * 100)) / 100
    if kind == "cat":
        return rnd.choice(p)
    if kind == "text":
        return rnd.choice(p)
    if kind == "name":
        return f"{rnd.choice(F_NAMES + M_NAMES)} {rnd.choice(LAST)}"
    if kind == "email":
        return f"user{k}@{rnd.choice(['example.com', 'example.in', 'mail.test'])}"
    if kind == "phone":
        return f"98{rnd.randint(10000000, 99999999)}"
    if kind == "sku":
        return f"SKU-{rnd.randint(1000, 9999)}"
    if kind == "date":
        return dt.date(rnd.randint(*p), rnd.randint(1, 12), rnd.randint(1, 28))
    if kind == "datetime":
        return dt.datetime(rnd.randint(*p), rnd.randint(1, 12), rnd.randint(1, 28), rnd.randint(0, 23), rnd.randint(0, 59), rnd.randint(0, 59))
    raise ValueError(kind)


SQLTYPE = {"mysql": {"pk": "INT NOT NULL", "fk": "INT", "int": "INT", "money": "DECIMAL(12,2)", "date": "DATE", "datetime": "DATETIME"},
           "postgres": {"pk": "INTEGER NOT NULL", "fk": "INTEGER", "int": "INTEGER", "money": "NUMERIC(12,2)", "date": "DATE",
                        "datetime": "TIMESTAMP"}}


def _ddl(t: dict, engine: str) -> list[str]:
    types = SQLTYPE[engine]
    cols = ",\n  ".join(f"{c['name']} {types.get(c['kind'], 'VARCHAR(80)')}" for c in t["cols"])
    out = [f"CREATE TABLE {t['name']} (\n  {cols},\n  PRIMARY KEY ({t['pk']})"
           + "".join(f",\n  FOREIGN KEY ({c}) REFERENCES {p} ({pk})" for c, p, pk in t["fks"])
           + ("\n) ENGINE=InnoDB" if engine == "mysql" else "\n)")]
    for k, ix in enumerate(t["indexes"]):
        name = f"ix{k}_{t['name']}"[:60]
        out.append(f"CREATE INDEX {name} ON {t['name']} ({', '.join(ix)})")
    return out


# ---------------------------------------------------------------- the engines
class Engine:
    def __init__(self, cfg):
        self.cfg, self.kind, self.conn = cfg, cfg["type"], None

    def open(self):
        c = self.cfg
        if self.kind == "mysql":
            import pymysql

            self.conn = pymysql.connect(host=c["host"], port=c["port"], user=c["user"], password=c["password"], database=c["database"],
                                        charset="utf8mb4", connect_timeout=3, autocommit=True)
            self.run("SET SESSION lock_wait_timeout = 20")
        else:
            import psycopg

            self.conn = psycopg.connect(host=c["host"], port=c["port"], user=c["user"], password=c["password"], dbname=c["database"],
                                        connect_timeout=3, autocommit=True)
            self.run("SET lock_timeout = '20s'")
        return self

    def run(self, sql, params=None, many=None):
        cur = self.conn.cursor()
        try:
            if many is not None:
                cur.executemany(sql, many)
                return None
            cur.execute(sql, params) if params else cur.execute(sql)
            return cur.fetchall() if cur.description else cur.rowcount
        finally:
            cur.close()

    def commit(self):
        self.run("COMMIT")

    def begin(self):
        self.run("START TRANSACTION" if self.kind == "mysql" else "BEGIN")

    def rollback(self):
        try:
            self.run("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass

    def drop(self, names):
        self.rollback()
        for n in reversed(names):
            try:
                self.run(f"DROP TABLE IF EXISTS {n}" + (" CASCADE" if self.kind == "postgres" else ""))
                self.commit()
            except Exception:  # noqa: BLE001
                self.rollback()

    def create(self, schema):
        names = schema["order"]
        if self.kind == "mysql":
            self.run("SET FOREIGN_KEY_CHECKS=0")
        self.drop(names)
        self.begin()
        for n in names:
            t = schema["tables"][n]
            for stmt in _ddl(t, self.kind):
                self.run(stmt)
            ph = ", ".join(["%s"] * len(t["cols"]))
            self.run(f"INSERT INTO {n} VALUES ({ph})", many=[tuple(r) for r in t["rows"]])
        self.commit()
        if self.kind == "mysql":
            self.run("SET FOREIGN_KEY_CHECKS=1")

    def structure(self, names) -> str:
        """What a person pastes: SHOW CREATE TABLE output (MySQL) or pg_dump --schema-only text (PostgreSQL)."""
        if self.kind == "mysql":
            return "\n\n".join(self.run(f"SHOW CREATE TABLE {n}")[0][1] for n in names)
        out = []
        for n in names:
            cols = self.run("SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema = current_schema() "
                            "AND table_name = %s ORDER BY ordinal_position", (n,))
            body = ",\n".join(f"    {c} {ty}{' NOT NULL' if nl == 'NO' else ''}" for c, ty, nl in cols)
            out.append(f"CREATE TABLE public.{n} (\n{body}\n);")
            for cname, cdef in self.run("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass "
                                        "ORDER BY contype DESC", (n,)):
                out.append(f"ALTER TABLE ONLY public.{n}\n    ADD CONSTRAINT {cname} {cdef};")
            for (idef,) in self.run("SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() AND tablename = %s AND indexname NOT IN "
                                    "(SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass)", (n, n)):
                out.append(idef + ";")
        return "\n\n".join(out)

    def leading(self, table) -> tuple[set, set]:
        """(primary key columns, first columns of every index), lower case, from the engine's own catalog."""
        if self.kind == "mysql":
            rows = self.run("SELECT INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
                            "AND TABLE_NAME = %s AND SEQ_IN_INDEX = 1", (table,))
            pk = {c.lower() for i, c in rows if i == "PRIMARY"}
            return pk, {c.lower() for _, c in rows}
        rows = self.run("SELECT x.indisprimary, a.attname FROM pg_index x JOIN pg_attribute a ON a.attrelid = x.indrelid "
                        "AND a.attnum = x.indkey[0] WHERE x.indrelid = %s::regclass", (table,))
        return {c.lower() for p, c in rows if p}, {c.lower() for _, c in rows}

    def columns(self, table) -> list[str]:
        if self.kind == "mysql":
            return [r[0] for r in self.run("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                                           "AND TABLE_NAME = %s", (table,))]
        return [r[0] for r in self.run("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
                                       "AND table_name = %s", (table,))]


def engines_up() -> dict:
    out = {}
    for cfg in (MYSQL, PG):
        try:
            out[cfg["type"]] = Engine(cfg).open()
        except Exception:  # noqa: BLE001 - server down: that engine is skipped
            pass
    return out


# ---------------------------------------------------------------- random queries, with what was put in them
class Q:
    """One generated query: its text, and the truth about it."""

    def __init__(self):
        self.funcs: list[str] = []       # functions put on filtered columns
        self.leading_like = False
        self.mixed = False               # AND and OR mixed without brackets
        self.star = False
        self.mysql_only = False
        self.total = False               # ORDER BY gives one exact order
        self.kind = "select"
        self.table = ""


def _lit(v):
    if isinstance(v, (int, Decimal)):
        return str(v)
    if isinstance(v, dt.datetime):
        return f"'{v:%Y-%m-%d %H:%M:%S}'"
    if isinstance(v, dt.date):
        return f"'{v:%Y-%m-%d}'"
    return "'" + str(v).replace("'", "''") + "'"


class Writer:
    def __init__(self, rnd, schema, backticks):
        self.rnd, self.schema, self.bt = rnd, schema, backticks
        self.kw_case = rnd.choice(["upper", "upper", "lower", "title"])

    def kw(self, s):
        return {"upper": s.upper(), "lower": s.lower(), "title": s.title()}[self.kw_case]

    def ident(self, s):
        return f"`{s}`" if self.bt and self.rnd.random() < 0.6 else s

    def col(self, alias, c):
        return f"{self.ident(alias)}.{self.ident(c)}" if alias else self.ident(c)


def _sample(t, cname, rnd):
    i = [c["name"] for c in t["cols"]].index(cname)
    vals = [r[i] for r in t["rows"] if r[i] is not None]
    return rnd.choice(vals) if vals else None


def _cond(w: Writer, q: Q, t, alias, rnd, allow_func=True):
    """One condition on table t (alias may be ''): text."""
    cols = [c for c in t["cols"] if c["kind"] != "pk"] or t["cols"]
    c = rnd.choice(cols)
    name, kind = c["name"], c["kind"]
    ref = w.col(alias, name)
    v = _sample(t, name, rnd)
    if v is None or rnd.random() < 0.05:
        return f"{ref} {w.kw('IS NULL') if rnd.random() < 0.5 else w.kw('IS NOT NULL')}"
    if kind in ("int", "money", "fk", "pk"):
        op = rnd.choice(["=", "in", ">", ">=", "<", "between"])
        if op == "in":
            vs = sorted({_sample(t, name, rnd) for _ in range(3)} - {None}, key=str)
            return f"{ref} {w.kw('IN')} ({', '.join(_lit(x) for x in vs)})"
        if op == "between":
            v2 = _sample(t, name, rnd)
            lo, hi = sorted([v, v2 if v2 is not None else v])
            return f"{ref} {w.kw('BETWEEN')} {_lit(lo)} {w.kw('AND')} {_lit(hi)}"
        return f"{ref} {op} {_lit(v)}"
    if kind in ("date", "datetime"):
        d = v.date() if isinstance(v, dt.datetime) else v
        f = rnd.random()
        if allow_func and f < 0.35:
            q.funcs.append("DATE")
            op = rnd.choice(["=", "=", ">=", "<", ">", "<=", "between"])
            fn = w.kw("DATE") + "(" + ref + ")"
            if op == "between":
                d2 = d + dt.timedelta(days=rnd.randint(0, 40))
                return f"{fn} {w.kw('BETWEEN')} {_lit(d)} {w.kw('AND')} {_lit(d2)}"
            return f"{fn} {op} {_lit(d)}"
        if allow_func and f < 0.5:
            q.funcs.append("YEAR")
            q.mysql_only = True
            return f"{w.kw('YEAR')}({ref}) = {d.year}"
        op = rnd.choice([">=", "<", "between", "="])
        if op == "between":
            d2 = d + dt.timedelta(days=rnd.randint(1, 60))
            return f"{ref} {w.kw('BETWEEN')} {_lit(d)} {w.kw('AND')} {_lit(d2)}"
        return f"{ref} {op} {_lit(v if kind == 'datetime' and op == '=' else d)}"
    # text
    f = rnd.random()
    sv = str(v)
    if allow_func and f < 0.2:
        fn = rnd.choice(["LOWER", "UPPER"])
        q.funcs.append(fn)
        return f"{w.kw(fn)}({ref}) = {_lit(sv.lower() if fn == 'LOWER' else sv.upper())}"
    if f < 0.35:
        q.leading_like = True
        return f"{ref} {w.kw('LIKE')} {_lit('%' + sv[-3:])}"
    if f < 0.5:
        return f"{ref} {w.kw('LIKE')} {_lit(sv[:2] + '%')}"
    if f < 0.6:
        vs = sorted({str(_sample(t, name, rnd)) for _ in range(3)} - {"None"})
        return f"{ref} {w.kw('IN')} ({', '.join(_lit(x) for x in vs)})"
    if f < 0.67:
        return f"{ref} <> {_lit(sv)}"
    return f"{ref} = {_lit(sv)}"


def _where(w: Writer, q: Q, parts: list[str], rnd) -> str:
    """Join conditions with AND; sometimes OR, with or without brackets."""
    A, O = w.kw("AND"), w.kw("OR")
    if len(parts) >= 3 and rnd.random() < 0.35:
        if rnd.random() < 0.5:
            q.mixed = True
            return f"{parts[0]} {A} {parts[1]} {O} {parts[2]}" + "".join(f" {A} {p}" for p in parts[3:])
        return f"{parts[0]} {A} ({parts[1]} {O} {parts[2]})" + "".join(f" {A} {p}" for p in parts[3:])
    if len(parts) == 2 and rnd.random() < 0.15:
        return f"{parts[0]} {O} {parts[1]}"
    return f" {A} ".join(parts)


def _children(schema, tname):
    return [(t, c) for t in schema["tables"].values() for c in t["cols"] if c["kind"] == "fk" and c["ref"] == tname]


def make_query(schema: dict, rnd: random.Random) -> tuple[str, Q]:
    q = Q()
    backticks = rnd.random() < 0.2
    w = Writer(rnd, schema, backticks)
    q.mysql_only = backticks
    tables = schema["tables"]
    kind = rnd.choice(["select"] * 7 + ["update", "delete"])
    q.kind = kind
    if kind == "delete":          # only leaf tables lose rows (nothing points at them)
        leaves = [n for n in schema["order"] if not _children(schema, n)]
        tname = rnd.choice(leaves)
    else:
        tname = rnd.choice(schema["order"])
    t = tables[tname]
    q.table = tname
    nl = rnd.choice([" ", " ", "\n", "\n  ", "  "])
    if kind != "select":
        parts = [_cond(w, q, t, "", rnd) for _ in range(rnd.randint(1, 3))]
        if rnd.random() < 0.25:
            parts.append(_subquery(w, q, schema, t, "", rnd))
        where = _where(w, q, parts, rnd)
        if kind == "update":
            sets = [c for c in t["cols"] if c["kind"] in ("int", "money", "cat", "text")]
            if not sets:
                kind = q.kind = "delete" if not _children(schema, tname) else "select"
                if kind == "select":
                    q.funcs, q.leading_like, q.mixed = [], False, False   # those conditions aren't used
            else:
                c = rnd.choice(sets)
                v = _sample(t, c["name"], rnd)
                val = _lit(v) if v is not None else w.kw("NULL")
                sql = f"{w.kw('UPDATE')} {w.ident(tname)} {w.kw('SET')} {w.ident(c['name'])} = {val}{nl}{w.kw('WHERE')} {where}"
                return _decorate(sql, w, rnd), q
        if q.kind == "delete":
            sql = f"{w.kw('DELETE FROM')} {w.ident(tname)}{nl}{w.kw('WHERE')} {where}"
            return _decorate(sql, w, rnd), q
    # SELECT
    alias = rnd.choice([a for a in ("", SINGULAR[t["base"]][0], SINGULAR[t["base"]][:2], "t") if a not in RESERVED])
    joins, jt = [], []
    parent_fk = [c for c in t["cols"] if c["kind"] == "fk"]
    kids = _children(schema, tname)
    child_join = None
    if parent_fk and rnd.random() < 0.45:
        c = rnd.choice(parent_fk)
        p = tables[c["ref"]]
        pa = "p" if alias != "p" else "pp"
        alias = alias or "t"
        on = f"{w.col(pa, p['pk'])} = {w.col(alias, c['name'])}"
        if rnd.random() < 0.25:
            on += f" {w.kw('AND')} " + _cond(w, Q(), p, pa, rnd, allow_func=False)
        joins.append(f"{w.kw(rnd.choice(['JOIN', 'INNER JOIN', 'LEFT JOIN']))} {w.ident(p['name'])} {pa} {w.kw('ON')} {on}")
        jt.append((p, pa))
    elif kids and rnd.random() < 0.25:
        ct, cc = rnd.choice(kids)
        alias = alias or "t"
        ca = "c" if alias != "c" else "cc"
        joins.append(f"{w.kw('LEFT JOIN')} {w.ident(ct['name'])} {ca} {w.kw('ON')} {w.col(ca, cc['name'])} = {w.col(alias, t['pk'])}")
        jt.append((ct, ca))
        child_join = (ct, ca)
    parts = [_cond(w, q, t, alias, rnd) for _ in range(rnd.randint(1, 3))]
    for p, pa in jt:
        if rnd.random() < 0.5:
            parts.append(_cond(w, q, p, pa, rnd))
    if rnd.random() < 0.2:
        parts.append(_subquery(w, q, schema, t, alias, rnd))
    rnd.shuffle(parts)
    where = _where(w, q, parts, rnd)
    if rnd.random() < 0.07:                      # no WHERE at all: nothing of it is in the query
        where, q.funcs, q.leading_like, q.mixed = "", [], False, False
    group = rnd.random() < 0.2
    cats = [c for c in t["cols"] if c["kind"] in ("cat", "fk", "int")]
    nums = [c for c in t["cols"] if c["kind"] in ("money", "int")]
    order, limit = "", ""
    if group and cats:
        g = rnd.choice(cats)
        gref = w.col(alias, g["name"])
        sel = f"{gref}, {w.kw('COUNT')}(*) {w.kw('AS')} n"
        if nums:
            sel += f", {w.kw('SUM')}({w.col(alias, rnd.choice(nums)['name'])}) {w.kw('AS')} total"
        tail = f"{nl}{w.kw('GROUP BY')} {gref}"
        if rnd.random() < 0.6:
            tail += f"{nl}{w.kw('ORDER BY')} {gref}"
            q.total = True
        elif rnd.random() < 0.5:
            tail += f"{nl}{w.kw('ORDER BY')} n {w.kw('DESC')}"
    else:
        r = rnd.random()
        if r < 0.3:
            sel = "*"
            q.star = True
        elif r < 0.4 and alias:
            sel = f"{w.ident(alias)}.*"
            q.star = True
            if jt:
                p, pa = jt[0]
                sel += ", " + w.col(pa, rnd.choice(p["cols"])["name"])
        else:
            pick = rnd.sample(t["cols"], min(len(t["cols"]), rnd.randint(1, 4)))
            sel = ", ".join(w.col(alias, c["name"]) for c in pick)
            if rnd.random() < 0.2:
                tx = [c for c in t["cols"] if c["kind"] in ("name", "cat")]
                if tx:
                    sel += f", {w.kw('UPPER')}({w.col(alias, tx[0]['name'])}) {w.kw('AS')} shown"
            if jt and rnd.random() < 0.6:
                p, pa = jt[0]
                sel += ", " + w.col(pa, rnd.choice(p["cols"])["name"])
        tail = ""
        if rnd.random() < 0.6:
            oc = rnd.choice([c for c in t["cols"] if c["kind"] != "pk"] or t["cols"])
            keys = [w.col(alias, oc["name"]) + rnd.choice(["", " " + w.kw("DESC"), " " + w.kw("ASC")])]
            if rnd.random() < 0.7:
                keys.append(w.col(alias, t["pk"]))
                q.total = child_join is None
                if child_join is not None and rnd.random() < 0.7:
                    keys.append(w.col(child_join[1], child_join[0]["pk"]))
                    q.total = True
            tail = f"{nl}{w.kw('ORDER BY')} " + ", ".join(keys)
        if q.total and rnd.random() < 0.5:
            n, off = rnd.randint(1, 30), rnd.choice([0, 0, 5, 20])
            if off and rnd.random() < 0.3:
                limit = f"{nl}{w.kw('LIMIT')} {off}, {n}"
                q.mysql_only = True
            else:
                limit = f"{nl}{w.kw('LIMIT')} {n}" + (f" {w.kw('OFFSET')} {off}" if off else "")
    sql = (f"{w.kw('SELECT')} {sel}{nl}{w.kw('FROM')} {w.ident(tname)}" + (f" {alias}" if alias else "")
           + "".join(nl + j for j in joins) + (f"{nl}{w.kw('WHERE')} {where}" if where else "") + tail + limit)
    return _decorate(sql, w, rnd), q


def _subquery(w, q, schema, t, alias, rnd):
    tables = schema["tables"]
    fks = [c for c in t["cols"] if c["kind"] == "fk"]
    kids = _children(schema, t["name"])
    outer = alias or t["name"]
    if fks and (not kids or rnd.random() < 0.5):
        c = rnd.choice(fks)
        p = tables[c["ref"]]
        return (f"{w.col(alias, c['name'])} {w.kw('IN')} ({w.kw('SELECT')} {w.ident(p['pk'])} {w.kw('FROM')} {w.ident(p['name'])} "
                f"{w.kw('WHERE')} {_cond(w, q, p, '', rnd)})")
    if kids:
        ct, cc = rnd.choice(kids)
        ia = "sq"
        return (f"{w.kw('EXISTS')} ({w.kw('SELECT')} 1 {w.kw('FROM')} {w.ident(ct['name'])} {ia} {w.kw('WHERE')} "
                f"{w.col(ia, cc['name'])} = {w.col(outer, t['pk'])} {w.kw('AND')} {_cond(w, q, ct, ia, rnd)})")
    return _cond(w, q, t, alias, rnd)


def _decorate(sql: str, w: Writer, rnd) -> str:
    r = rnd.random()
    if r < 0.12:
        sql = "/* " + rnd.choice(["report", "list for the page", "old query, slow", "don't cache this", "owner's list"]) + " */ " + sql
    elif r < 0.22:
        sql = "-- " + rnd.choice(["fetch the list", "used on dashboard"]) + "\n" + sql
    elif r < 0.3:
        sql = re.sub(r"\s(" + w.kw("FROM") + r")\s", lambda m: f" /* main */ {m.group(1)} ", sql, count=1)
    if rnd.random() < 0.1:
        wk = " " + w.kw("WHERE") + " "
        if wk in sql:
            sql = sql.replace(wk, "\n-- " + rnd.choice(["filters", "only what's needed"]) + "\n" + w.kw("WHERE") + " ", 1)
    if rnd.random() < 0.1:
        sql = re.sub(r"\b(DATE|YEAR|LOWER|UPPER|date|year|lower|upper|Date|Year|Lower|Upper)\(", r"\1 (", sql)
    if rnd.random() < 0.15:
        sql = sql.replace(" = ", "=", 2)
    if rnd.random() < 0.1:
        sql = sql.replace(" ", "   ", 3)
    return sql


WRAPS = ["none", "none", "semi", "php", "heredoc", "js", "py", "py3", "pyexec", "java"]


def wrap(sql: str, rnd) -> tuple[str, str]:
    style = rnd.choice(WRAPS)
    one = re.sub(r"\s*\n\s*", " ", sql) if "--" not in sql else sql
    if style == "semi":
        return style, sql + ";"
    if style == "php":
        return style, f'$sql = "{sql}";'
    if style == "heredoc":
        return style, f"$sql = <<<SQL\n{sql}\nSQL;"
    if style == "js":
        return style, f"const query = `\n    {sql}\n`;"
    if style == "py":
        return (style, f'query = "{one}"') if "--" not in sql else (style, f'query = """{sql}"""')
    if style == "py3":
        return style, f'query = """\n{sql}\n"""'
    if style == "pyexec":
        return style, f'cursor.execute("""{sql}""")'
    if style == "java" and "--" not in sql:
        return style, f'String sql = "{one}";'
    return "none", sql


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().rstrip(";").strip())


# ---------------------------------------------------------------- family 1: queries
COVER = Counter()          # how much each check really exercised (so a 100% can't hide an empty check)
def _norm_val(v):
    if isinstance(v, Decimal):
        return str(v.normalize()) if v == v.to_integral() else str(v)
    if isinstance(v, float):
        return repr(round(v, 6))
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return repr(v)


def _rows(eng: Engine, sql: str, q: Q, tables: list[str]):
    """What a statement gives: SELECT -> its rows; UPDATE/DELETE -> the rows it touches and the table after (rolled back)."""
    eng.rollback()
    try:
        if q.kind == "select":
            rows = [tuple(_norm_val(v) for v in r) for r in eng.run(sql)]
            return rows if q.total else sorted(rows)
        eng.begin()
        n = eng.run(sql)
        after = [tuple(_norm_val(v) for v in r) for r in eng.run(f"SELECT * FROM {q.table} ORDER BY 1")]
        return [("rowcount", n)] + after
    finally:
        eng.rollback()


def check_query(item: dict, engines: dict, schema: dict) -> list[str]:
    """Every problem with the agent's answer for one generated query (empty = pass)."""
    from app.agents.sql import analyze_sql, unwrap

    bare, wrapped, q = item["sql"], item["wrapped"], item["q"]
    fails = []
    # a. never crashes; unwrap gives the bare query
    try:
        if _ws(unwrap(wrapped)) != _ws(bare):
            fails.append(f"a: unwrap left {unwrap(wrapped)[:160]!r}")
        res = analyze_sql(wrapped)
    except Exception as e:  # noqa: BLE001
        return [f"a: crashed {type(e).__name__}: {e}"]
    if not res.get("ok"):
        return [f"a: not analysed: {res.get('error')}"]
    texts = " | ".join(f"{f['title_en']} {f['fix_en']}" for f in res["findings"]).lower()
    titles = [f["title_en"] for f in res["findings"]]
    # d. without the structure: a check query whenever indexes are suggested, and never 'missing for sure'
    if res["indexes"] and not res.get("check_query"):
        fails.append("d: indexes suggested without the structure but no check_query")
    if res.get("indexes_verified"):
        fails.append("d: indexes_verified without any structure")
    for bad in ("checked against your table structure", "missing indexes on", "has no index", "isn't indexed", "is not indexed"):
        if bad in texts:
            fails.append(f"d: claims for sure without the structure: {bad!r}")
    # e. findings match what was put in
    for fn in sorted(set(q.funcs)):
        if not any(t.startswith(f"{fn}()") or f"{fn}()" in t for t in titles):
            fails.append(f"e: {fn}() on a filtered column not reported (findings: {titles})")
    if not q.funcs and any("() on a column" in t for t in titles):
        fails.append(f"e: function finding without a function in WHERE: {titles}")
    has_like = any(t.startswith("LIKE '%") for t in titles)
    if q.leading_like != has_like:
        fails.append(f"e: leading wildcard {'missed' if q.leading_like else 'reported but not there'} ({titles})")
    has_mixed = any(t.startswith("AND and OR mixed") for t in titles)
    if q.mixed != has_mixed:
        fails.append(f"e: AND/OR without brackets {'missed' if q.mixed else 'reported but brackets are there'} ({titles})")
    has_star = any(t.startswith("SELECT *") for t in titles)
    if q.star != has_star:
        fails.append(f"e: SELECT * {'missed' if q.star else 'reported but not there'}")
    if res.get("rewritten") is None and re.search(r"rewritten query|optimi[sz]ed|i changed", texts):
        fails.append(f"e: talks about a rewrite but nothing was rewritten: {texts[:200]!r}")
    if res.get("rewritten") is not None and _ws(res["rewritten"]) == _ws(bare):
        fails.append("e: 'rewritten' is the same as the original")
    # b. the rewrite gives exactly the same rows; c. indexes (with the structure) are real and run
    for kind, eng in engines.items():
        if q.mysql_only and kind != "mysql":
            continue
        try:
            before = _rows(eng, bare, q, schema["order"])
        except Exception as e:  # noqa: BLE001
            fails.append(f"gen: the generated query doesn't run on {kind}: {str(e)[:160]}")
            continue
        COVER[f"ran on {kind}"] += 1
        if res.get("rewritten"):
            COVER[f"rewrites compared on {kind}"] += 1
            try:
                after = _rows(eng, unwrap(res["rewritten"]), q, schema["order"])
                if after != before:
                    fails.append(f"b: rewrite gives different rows on {kind} ({len(before)} vs {len(after)}): {res['rewritten'][:200]!r}")
            except Exception as e:  # noqa: BLE001
                fails.append(f"b: rewrite fails on {kind}: {str(e)[:160]} :: {res['rewritten'][:200]!r}")
        fails += _check_indexes(wrapped, eng, kind, schema, item)
    return fails


def _check_indexes(wrapped, eng: Engine, kind: str, schema: dict, item: dict) -> list[str]:
    from app.agents.sql import analyze_sql
    from connector import goal_connector as gc

    fails = []
    used = [n for n in schema["order"] if re.search(rf"\b{n}\b", item["sql"])]
    rnd = random.Random(hash((schema["seed"], item["n"])) & 0xFFFF)
    names = used if rnd.random() < 0.6 else schema["order"]
    eng.rollback()
    extra = eng.structure(names)
    try:
        res = analyze_sql(wrapped, extra)
    except Exception as e:  # noqa: BLE001
        return [f"a: crashed with the {kind} structure: {type(e).__name__}: {e}"]
    if not res.get("indexes_verified"):
        fails.append(f"d: {kind} structure given but indexes_verified is false")
    if res.get("check_query"):
        fails.append(f"d: {kind} structure given but still a check_query")
    if not res.get("indexes"):
        return fails
    driver = (gc.MySQL if kind == "mysql" else gc.Postgres)(dict(eng.cfg)).connect()
    with driver.conn.cursor() as cur:
        cur.execute("SET SESSION lock_wait_timeout = 20" if kind == "mysql" else "SET lock_timeout = '20s'")
    try:
        for stmt in res["indexes"]:
            try:
                ix = gc.parse_index(stmt)
            except ValueError as e:
                fails.append(f"c: {kind}: the connector refuses {stmt!r}: {e}")
                continue
            if ix is None:
                fails.append(f"c: {kind}: not a plain index statement: {stmt!r}")
                continue
            table = ix["table"]
            if table not in schema["tables"]:
                fails.append(f"c: {kind}: no such table {table!r} in {stmt!r}")
                continue
            real = eng.columns(table)
            bad = [c for c in ix["columns"] if c not in real and (kind != "mysql" or c.lower() not in {r.lower() for r in real})]
            if bad:
                fails.append(f"c: {kind}: {bad} are not columns of {table} (has {real}): {stmt!r}")
                continue
            pk, lead = eng.leading(table)
            first = ix["columns"][0].lower()
            if first in pk:
                fails.append(f"c: {kind}: suggests an index starting with the primary key {first!r}: {stmt!r}")
                continue
            if first in lead:
                fails.append(f"c: {kind}: an index starting with {first!r} already exists: {stmt!r}")
                continue
            try:
                plan = driver.plan_change(stmt)
                if plan.get("blocked"):
                    fails.append(f"c: {kind}: connector blocks {stmt!r}: {plan['blocked']}")
                    continue
                record, _ = driver.apply_change(stmt, None)
                driver.undo_change(record)
                COVER[f"indexes created and undone on {kind}"] += 1
            except Exception as e:  # noqa: BLE001
                driver.done()
                fails.append(f"c: {kind}: {stmt!r} doesn't run: {str(e)[:160]}")
    finally:
        try:
            driver.conn.close()
        except Exception:  # noqa: BLE001
            pass
    return fails


# ---------------------------------------------------------------- family 2: follow-ups through the API
SLOW = {"en": ["My database is very slow, please check it and fix it", "our site database has become really slow",
               "the database is slow, pages take ages"],
        "hi": ["mera database bahut slow chal raha hai", "database slow ho gaya hai, check karo", "db bahut dhima hai bhai"],
        "dev": ["मेरा डेटाबेस बहुत धीरे चल रहा है", "डेटाबेस बहुत स्लो हो गया है"]}
FOLLOW = {
    "optimize": {"en": ["can you optimize this query", "make it faster", "optimise it please", "how do I speed this up",
                        "rewrite it to be faster", "can you improve this query"],
                 "hi": ["isko fast kaise karu", "query ko optimize karo", "ise tez karo", "isko behtar banao", "query fast kar do"],
                 "dev": ["इस क्वेरी को तेज़ करो", "इसे ऑप्टिमाइज़ करो", "क्वेरी को बेहतर बनाओ"]},
    "diff": {"en": ["what did you change?", "what is the difference between my query and yours", "what changed",
                    "compare it with my original"],
             "hi": ["kya badla isme", "meri query aur tumhari query mein kya farak hai", "kya change kiya"],
             "dev": ["क्या बदला?", "मेरी क्वेरी और आपकी क्वेरी में क्या फ़र्क है", "आपने क्या बदलाव किया"]},
    "why": {"en": ["why is it slow", "why is this query slow", "what makes it slow"],
            "hi": ["ye slow kyun hai", "query slow kyu chal rahi hai", "iski wajah kya hai"],
            "dev": ["ये धीमी क्यों है", "क्वेरी धीमी क्यों चल रही है"]},
    "index": {"en": ["which indexes should I add", "what indexes do I need", "which index"],
              "hi": ["kaunse index lagau", "index kaunsa lagana hai"],
              "dev": ["कौन से इंडेक्स लगाऊं", "कौनसा इंडेक्स चाहिए"]},
    "explain": {"en": ["explain this query", "can you explain it", "explain please"],
                "hi": ["samjhao isko", "ye query samjhao"],
                "dev": ["इसे समझाओ", "ये क्वेरी समझाइए"]},
}
KIND_MARKS = {
    "optimize": ["Your query, optimised", "already written well", "haven't changed the query's text", "behtar roop", "pehle se theek likhi",
                 "Query ka text maine nahi badla"],
    "diff": ["What I changed", "didn't change your query's text", "Ye badla", "query ka text nahi badla"],
    "why": ["Why this query can be slow", "slow kyun ho sakti"],
}
FALLBACK = ["i've covered this above", "tell me what's different now", "your work, city", "steps are above", "tell me a bit more", "thoda aur batao", "what would you like", "i didn't understand",
            "samajh nahi aaya"]
WRAPPER_LEFT = ["$sql", "<<<", '";', "`;", '"""', "execute(", "const query", "String sql", "query = "]
QUERY_WORDS = re.compile(r"query|quer|index|slow|explain|optimi|change|badla|kyun|क्वेरी|इंडेक्स", re.I)


def _make_client():
    """The app with a fresh test database (like tests/conftest.py), in ML mode."""
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.db import Base, get_engine, init_db
    from app.main import app

    engine = get_engine()
    Base.metadata.drop_all(engine)
    init_db()
    s = get_settings()
    s.engine_mode, s.visitor_daily_cap, s.ai_daily_cap = "ml", 100000, 100000
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    c = TestClient(app)
    c.__enter__()                                  # runs the app's startup, like 'with TestClient(app)'
    return c


def followups(client, seed: int, sql_items: list[dict], rnd: random.Random) -> list[dict]:
    """One conversation: the slow-database message, a query pasted (maybe from code), then 4 random follow-ups."""
    from app.agents.sql import analyze_sql, unwrap

    selects = [i for i in sql_items if i["q"].kind == "select"] or sql_items
    item = rnd.choice(selects)
    client.post("/api/new-chat")
    client.cookies.clear()
    lang = rnd.choice(["en", "hi", "dev"])
    out = []

    def post(text):
        r = client.post("/api/message", json={"text": text})
        return r.json() if r.status_code == 200 else {"reply": f"HTTP {r.status_code}", "relation": "error", "next": []}

    post(rnd.choice(SLOW[lang]))
    lead = rnd.choice(["", "", "", "here is my query:\n", "ye meri query hai\n", "this one is slow:\n\n"])
    r = post(lead + item["wrapped"])
    if r.get("relation") != "agent_run":
        return [{"seed": seed, "family": "followups", "q": item["wrapped"][:120], "why": f"query not checked: {r.get('relation')} {r.get('reply', '')[:160]!r}"}]
    res = analyze_sql(unwrap(item["wrapped"]))
    prev, prev_kind = r.get("reply", ""), None
    for _ in range(4):
        kind = rnd.choice(sorted(FOLLOW))
        lang = rnd.choice(["en", "en", "hi", "dev"])
        text = rnd.choice(FOLLOW[kind][lang])
        r = post(text)
        reply = r.get("reply", "")
        why = []
        if r.get("relation") != "agent_reply":
            why.append(f"not answered about the query (relation {r.get('relation')})")
        if _ws(reply) == _ws(prev):
            why.append("same reply as the one before")
        low = reply.lower()
        for f in FALLBACK:
            if f in low:
                why.append(f"generic fallback text {f!r}")
        for wl in WRAPPER_LEFT:
            if wl in reply:
                why.append(f"code wrapper left in the reply: {wl!r}")
        want = "why" if kind == "explain" else kind
        repeat = prev_kind == want             # asked the same thing again: a different answer is all that's owed
        if want in KIND_MARKS and not repeat and not any(m in reply for m in KIND_MARKS[want]):
            why.append(f"not a {want} answer")
        if want == "index" and not repeat:
            idx = res.get("indexes") or []
            if idx and not all(x in reply for x in idx):
                why.append(f"doesn't list the indexes {idx}")
            if not idx and not re.search(r"no (new )?index|koi (naya )?index nahi|index ki zaroorat nahi", reply, re.I):
                why.append("no index to add, but the reply doesn't say so")
            if not idx and re.search(r"speed-up comes from the indexes|index aur in baaton se", reply):
                why.append("says the speed-up comes from indexes, but there are none to add")
        if kind == "optimize" and not res.get("indexes") and re.search(r"speed-up comes from the indexes|index aur in baaton se", reply):
            why.append("says the speed-up comes from indexes, but there are none to add")
        if kind == "optimize" and (res.get("rewritten") is None) and re.search(r"Your query, optimised|behtar roop", reply):
            why.append("says 'optimised' but the query text is the same")
        nxt = r.get("next") or []
        off = [n for n in nxt if not QUERY_WORDS.search(n)]
        if off:
            why.append(f"next suggestions not about the query: {off}")
        COVER[f"follow-up {kind} ({lang})"] += 1
        out.append({"seed": seed, "family": "followups", "q": text, "kind": kind, "ok": not why,
                    "why": "; ".join(why) + (f" :: {reply[:220]!r}" if why else "")})
        prev, prev_kind = reply, want
    return out


# ---------------------------------------------------------------- family 3: new ways of describing each playbook
# fillers carry no meaning of their own ("what should I do now?" is itself a playbook: hacked-what-to-do-now)
FILL = {"en": (["hi, ", "please help: ", "urgent - ", "hello team, ", "", "", "need help, "],
               [" any idea?", " please help", "", "", " thanks", " help!"]),
        "hi": (["bhai ", "yaar ", "sir ", "", "", "please "],
               [" please help karo", " jaldi batao", "", " help chahiye"]),
        "dev": (["कृपया मदद करें, ", "भाई ", "", ""], [" मदद कीजिए", "", ""])}
CONTEXT = {"en": ["since yesterday", "on our live site", "for our billing app", "since this morning", "on the office server"],
           "hi": ["kal se", "live site pe", "subah se", "office ke server pe", "billing app mein"],
           "dev": ["कल से", "सुबह से", "लाइव साइट पर"]}
DEV = re.compile(r"[ऀ-ॿ]")
HINGLISH = re.compile(r"\b(hai|ho|ka|ki|ke|kya|nahi|mein|pe|karo|kar|raha|rahi|gaya|gayi|aa|bata\w*|kaise|kyun|abhi|sab)\b", re.I)


def _lang(s):
    return "dev" if DEV.search(s) else "hi" if HINGLISH.search(s) else "en"


def _typo(word, rnd):
    if len(word) < 5 or not word.isalpha() or DEV.search(word):
        return word
    i = rnd.randrange(1, len(word) - 2)
    op = rnd.choice(["swap", "drop", "double"])
    if op == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if op == "drop":
        return word[:i] + word[i + 1:]
    return word[:i] + word[i] + word[i:]


SYN = {"database": ["db", "DB", "database server", "databse"], "website": ["site", "web site", "portal"], "site": ["website", "portal"],
       "error": ["issue", "problem", "eror"], "mysql": ["MySQL", "my sql"], "postgres": ["PostgreSQL", "postgresql", "pg"],
       "password": ["pwd", "passwd"], "connect": ["conect", "connect hona"], "nahi": ["nhi", "nai"], "hai": ["h", "he"],
       "karo": ["kro"], "kya": ["kia"], "bahut": ["bohot", "bhut"], "raha": ["rha"], "rahi": ["rhi"]}


_TOKEN = re.compile(r"[\w\u0900-\u097F.]{3,}")
_DISTINCT: dict = {}


def _distinct(issue: dict) -> set:
    """Words only this playbook's symptoms use: what makes a description about IT and not another problem."""
    if not _DISTINCT:
        from app import dbknow

        words = {i["id"]: {w.lower() for s in i["symptoms"] for w in _TOKEN.findall(s)} for i in dbknow.load("issues")}
        df = Counter(w for ws in words.values() for w in ws)
        _DISTINCT.update({k: {w for w in ws if df[w] == 1} for k, ws in words.items()})
    return _DISTINCT.get(issue["id"], set())


def phrase(issue: dict, rnd: random.Random) -> str:
    syms = issue["symptoms"]
    s1 = rnd.choice(syms)
    lang = _lang(s1)
    same = [s for s in syms if _lang(s) == lang and s != s1]
    text = s1
    if same and rnd.random() < 0.6:                       # half of one way of saying it, half of another
        a, b = s1.split(), rnd.choice(same).split()
        mixed = " ".join(a[:max(2, (len(a) + 1) // 2)]) + rnd.choice([", ", " - ", ". ", " and " if lang == "en" else " aur " if lang == "hi" else " और "]) \
            + " ".join(b[len(b) // 2:])
        if {w.lower() for w in _TOKEN.findall(mixed)} & _distinct(issue):
            text = mixed                                  # still says something only this problem has (else it's another problem)
    ws = text.split()
    if len(ws) > 5 and rnd.random() < 0.3:                # a word left out
        ws.pop(rnd.randrange(1, len(ws) - 1))
    ws = [rnd.choice(SYN[w.lower()]) if w.lower() in SYN and rnd.random() < 0.5 else w for w in ws]
    text = " ".join(ws)
    if lang != "dev" and rnd.random() < 0.4:
        ws = text.split()
        for _ in range(rnd.randint(1, 2)):
            k = rnd.randrange(len(ws))
            ws[k] = _typo(ws[k], rnd)
        text = " ".join(ws)
    if issue.get("codes") and rnd.random() < 0.3:
        code = rnd.choice(issue["codes"])
        text += rnd.choice([f", error {code}", f" ({code})", f" - code {code} aa raha hai" if lang == "hi" else f" - error code {code}"])
    if rnd.random() < 0.4:
        ctx = rnd.choice(CONTEXT[lang])
        text = f"{ctx} {text}" if rnd.random() < 0.5 else f"{text} {ctx}"
    pre, post = FILL[lang]
    text = rnd.choice(pre) + text + rnd.choice(post)
    if lang != "dev" and rnd.random() < 0.3:
        text = text.lower()
    return text.strip()


def phrasings(seed: int, rnd: random.Random) -> list[dict]:
    from app import dbknow
    from app.agents.database import _sure
    from app.dbknow import brain

    out = []
    for issue in dbknow.load("issues"):
        text = phrase(issue, rnd)
        rec = brain.recognize(text)
        right = (rec["kind"], rec["id"]) == ("issue", issue["id"])
        sure = _sure(rec) if rec["kind"] != "sql" else True
        ok = right or not sure
        out.append({"seed": seed, "family": "phrasings", "q": text, "ok": ok, "recognised": right,
                    "why": "" if ok else f"confidently wrong: {rec['kind']}:{rec['id']} ({rec['confidence']}) instead of {issue['id']}"})
    return out


def diagnoses(seed: int, rnd: random.Random) -> list[dict]:
    """Random evidence packets: injected causes must be named; clean packets stay clean."""
    from app.dbknow.diagnosis import evaluate
    from app.dbknow.service import playbook

    issue = playbook("too-many-connections")
    checks = [d for d in issue["diagnose"] if d["engine"] == "mysql"]
    limit = rnd.randint(100, 400)
    cases = []
    for injected in ("leak", "traffic", "healthy"):
        connected = rnd.randint(2, limit // 3)
        sleeping = rnd.randint(0, 10)
        if injected == "leak":
            sleeping, connected = rnd.randint(51, 180), rnd.randint(55, limit - 1)
        elif injected == "traffic":
            connected = rnd.randint(max(1, int(limit * .85) + 1), limit)
        raw = [
            {"columns": ["Variable_name", "Value"], "rows": [["Threads_connected", connected]]},
            {"columns": ["Variable_name", "Value"], "rows": [["max_connections", limit]]},
            {"columns": ["USER", "n", "sleeping"], "rows": [["generated_app", connected, sleeping]]},
        ]
        got = evaluate(issue, checks, raw)
        named = {c["id"] for c in got["likely_causes"]}
        ok = (injected in named) if injected != "healthy" else (not named and got["no_problem_found"])
        COVER[f"diagnosis {injected}"] += 1
        cases.append({"seed": seed, "family": "diagnosis", "q": injected, "ok": ok,
                      "why": "" if ok else f"injected {injected}, got {sorted(named)}"})
    return cases


# ---------------------------------------------------------------- the run
def run(seeds, show=False, families=("queries", "followups", "phrasings", "diagnosis"), client=None, queries_per_seed=8) -> dict:
    engines = engines_up() if "queries" in families or "followups" in families else {}
    COVER.clear()
    stats, fails = defaultdict(lambda: [0, 0]), []
    recognised = [0, 0]
    own_client = None
    if "followups" in families and client is None:
        own_client = client = _make_client()
    try:
        for seed in seeds:
            schema = make_schema(seed)
            for eng in engines.values():
                eng.create(schema)
            try:
                rnd = random.Random(seed * 7919)
                items = []
                for n in range(queries_per_seed):
                    sql, q = make_query(schema, rnd)
                    style, wrapped = wrap(sql, rnd)
                    items.append({"n": n, "sql": sql, "wrapped": wrapped, "wrap": style, "q": q})
                if "queries" in families:
                    for it in items:
                        problems = check_query(it, engines, schema)
                        st = stats["queries"]
                        st[0] += not problems
                        st[1] += 1
                        if show:
                            print(("PASS " if not problems else "FAIL ") + f"[{schema['domain']}/{seed}] {it['wrapped'][:150]!r}")
                            for p in problems:
                                print("    " + p)
                        for p in problems:
                            fails.append({"seed": seed, "family": "queries", "domain": schema["domain"], "wrap": it["wrap"],
                                          "check": p.split(":")[0], "q": it["wrapped"], "why": p})
                if "followups" in families:
                    for r in followups(client, seed, items, random.Random(seed * 104729)):
                        st = stats["followups"]
                        ok = r.get("ok", False)
                        st[0] += ok
                        st[1] += 1
                        if show:
                            print(("PASS " if ok else "FAIL ") + f"[follow/{seed}] {r['q']!r} {r['why'][:200]}")
                        if not ok:
                            fails.append(r)
                if "phrasings" in families:
                    for r in phrasings(seed, random.Random(seed * 15485863)):
                        st = stats["phrasings"]
                        st[0] += r["ok"]
                        st[1] += 1
                        recognised[0] += r["recognised"]
                        recognised[1] += 1
                        if show and (not r["ok"] or not r["recognised"]):
                            print(("NOT SURE " if r["ok"] else "FAIL ") + f"[phrase/{seed}] {r['q']!r} {r['why']}")
                        if not r["ok"]:
                            fails.append(r)
                if "diagnosis" in families:
                    for r in diagnoses(seed, random.Random(seed * 32452843)):
                        st = stats["diagnosis"]
                        st[0] += r["ok"]
                        st[1] += 1
                        if show:
                            print(("PASS " if r["ok"] else "FAIL ") + f"[diagnosis/{seed}] {r['q']} {r['why']}")
                        if not r["ok"]:
                            fails.append(r)
            finally:
                for eng in engines.values():
                    eng.drop(schema["order"])
    finally:
        if own_client is not None:
            own_client.__exit__(None, None, None)
        for eng in engines.values():
            try:
                eng.conn.close()
            except Exception:  # noqa: BLE001
                pass
    total = sum(v[1] for v in stats.values())
    passed = sum(v[0] for v in stats.values())
    by_check = Counter(f.get("check") or f["family"] for f in fails)
    return {"passed": passed, "total": total, "rate": round(passed / total, 4) if total else 0, "engines": sorted(engines),
            "by_family": {k: f"{v[0]}/{v[1]} ({v[0] / v[1]:.1%})" for k, v in sorted(stats.items())},
            "phrasings_recognised": f"{recognised[0]}/{recognised[1]}", "coverage": dict(sorted(COVER.items())),
            "by_check": dict(by_check), "fails": fails}


def _test_env():
    """Running on its own (not under pytest): the same settings the test suite uses, on a throwaway database."""
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    for k, v in {"DATABASE_URL": f"sqlite:///{db}", "GEMINI_API_KEY": "", "GROQ_API_KEY": "", "ENGINE_MODE": "ml",
                 "AGENT_SCHEDULER": "off", "EXPOSE_INTERNALS": "1", "SITE_MODE": "open", "VISITOR_DAILY_CAP": "100000",
                 "AI_DAILY_CAP": "100000"}.items():
        os.environ.setdefault(k, v)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--family", action="append", choices=["queries", "followups", "phrasings", "diagnosis"])
    ap.add_argument("--json", help="write the report here")
    a = ap.parse_args(argv)
    _test_env()
    seeds = [a.seed] if a.seed is not None else list(range(a.start, a.start + a.seeds))
    rep = run(seeds, show=a.show, families=tuple(a.family or ("queries", "followups", "phrasings", "diagnosis")))
    print(f"passed {rep['passed']}/{rep['total']} ({rep['rate']:.1%})   engines: {', '.join(rep['engines']) or 'none (engine checks skipped)'}")
    print(json.dumps(rep["by_family"], indent=1))
    print("phrasings recognised (rest: 'not sure'):", rep["phrasings_recognised"])
    print("checked:", json.dumps(rep["coverage"], ensure_ascii=False))
    print("failures by check:", json.dumps(rep["by_check"]))
    for f in rep["fails"][:400]:
        print(f"- [{f['family']}/{f['seed']}] {f['q'][:160]!r} -> {f['why'][:300]}")
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1, default=str, ensure_ascii=False)
    return 0 if rep["rate"] >= 0.99 else 1


if __name__ == "__main__":
    sys.exit(main())
