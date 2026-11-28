"""E1 (SQLite), night 2 of 4: types and tables. How SQLite really takes a CREATE TABLE (type affinity, ids, keys, STRICT,
foreign keys), what its ALTER TABLE can't do, and values compared across types in queries.

The lab is SQLite itself (Python's sqlite3: 3.53 when this was written; the 3.45 command line for the older behaviour). Every
design problem is a requirement, a table script a person wrote, and the correct SQLite script; each one comes with a PROBE (a
few statements and a query) that shows the difference in the lab:
- LAB is what the person's script really does on SQLite ('error' when SQLite refuses the script or the probe, else the
  probe's rows): NULL ids, a 6-letter value in VARCHAR(3), '012345' stored as 12345 ...
- DBaba (told the database is SQLite, rule A) must find the mistake under its topic, and its corrected script must behave
  exactly like the correct one on the probe (same rows, or refused the same way).
Query problems run on the practice tables (tests/practice/schema.py) in SQLite: the corrected query returns the correct
query's rows. REFUSED_TONIGHT are ALTER TABLE forms SQLite refuses: each is refused in the lab, found by DBaba, and its rewrite
(when there is one) runs and does what was meant.
"""

import os
import sqlite3
import tempfile

import pytest

from app.agents import sqlcorrect, sqlport
from app.agents.sql import analyze_sql
from app.dbknow import engines as E
from app.dbknow import reasoning as R
from tests.practice import schema as S

V353 = sqlite3.sqlite_version_info >= (3, 53, 0)
ERR = "error"
U = "INSERT INTO users (name) VALUES ('a'); INSERT INTO users (name) VALUES ('b')"
# (id, words, the person's script, topic, the correct SQLite script, probe statements, probe query, LAB: what the person's
#  script gives on the probe)
D = [
    ("int_pk", "users whose id numbers itself", "CREATE TABLE users (id INT PRIMARY KEY, name TEXT)", "sqlite_rowid_pk",
     "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)", U, "SELECT id, name FROM users ORDER BY rowid", [("NULL", "a"), ("NULL", "b")]),
    ("bigint_pk", "users with a big id that numbers itself", "CREATE TABLE users (id BIGINT PRIMARY KEY, name TEXT)", "sqlite_rowid_pk",
     "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)", U, "SELECT id, name FROM users ORDER BY rowid", [("NULL", "a"), ("NULL", "b")]),
    ("int11_pk", "users table copied from mysql, id int(11)", "CREATE TABLE users (id INTEGER(11) PRIMARY KEY, name TEXT)", "sqlite_rowid_pk",
     "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)", U, "SELECT id, name FROM users ORDER BY rowid", [("NULL", "a"), ("NULL", "b")]),
    ("serial_pk", "users with a serial id (from postgres)", "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT)", "sqlite_rowid_pk",
     "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)", U, "SELECT id, name FROM users ORDER BY rowid", [("NULL", "a"), ("NULL", "b")]),
    ("int_auto_pk", "users with an auto increment id", "CREATE TABLE users (id INT AUTO_INCREMENT PRIMARY KEY, name TEXT)", "sqlite_rowid_pk",
     "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)", U, "SELECT id, name FROM users ORDER BY rowid", [("NULL", "a"), ("NULL", "b")]),
    ("pk_desc", "users, newest id first", "CREATE TABLE users (id INTEGER PRIMARY KEY DESC, name TEXT)", "sqlite_rowid_pk",
     "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)", U, "SELECT id, name FROM users ORDER BY rowid", [("NULL", "a"), ("NULL", "b")]),
    ("table_pk_int", "users keyed by id", "CREATE TABLE users (id INT, name TEXT, PRIMARY KEY (id))", "sqlite_rowid_pk",
     "CREATE TABLE users (id INTEGER, name TEXT, PRIMARY KEY (id))", U, "SELECT id, name FROM users ORDER BY rowid", [("NULL", "a"), ("NULL", "b")]),
    ("auto_after_pk", "users, id auto increment (mysql words)", "CREATE TABLE users (id INTEGER PRIMARY KEY AUTO_INCREMENT, name TEXT)", "ddl_dialect",
     "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)", U, "SELECT id, name FROM users ORDER BY id", ERR),
    ("identity", "users with an identity id (postgres words)", "CREATE TABLE users (id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name TEXT)",
     "ddl_dialect", "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)", U, "SELECT id, name FROM users ORDER BY id", ERR),
    ("autoinc_on_int", "users with autoincrement on an int id", "CREATE TABLE users (id INT PRIMARY KEY AUTOINCREMENT, name TEXT)", "sqlite_rowid_pk",
     "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)", U, "SELECT id, name FROM users ORDER BY id", ERR),
    ("text_pk_null", "products keyed by their sku", "CREATE TABLE products (sku TEXT PRIMARY KEY, name TEXT)", "sqlite_pk_null",
     "CREATE TABLE products (sku TEXT NOT NULL PRIMARY KEY, name TEXT)",
     "INSERT INTO products (name) VALUES ('a'); INSERT INTO products (name) VALUES ('b')", "SELECT count(*) FROM products WHERE sku IS NULL", [("2",)]),
    ("link_pk_null", "tags of each post, one row per pair", "CREATE TABLE post_tags (post_id INTEGER, tag TEXT, PRIMARY KEY (post_id, tag))",
     "sqlite_pk_null", "CREATE TABLE post_tags (post_id INTEGER NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (post_id, tag))",
     "INSERT INTO post_tags VALUES (1, NULL); INSERT INTO post_tags VALUES (1, NULL)", "SELECT count(*) FROM post_tags", [("2",)]),
    ("varchar_len", "discount codes, at most 5 letters", "CREATE TABLE codes (id INTEGER PRIMARY KEY, code VARCHAR(5))", "sqlite_unenforced",
     "CREATE TABLE codes (id INTEGER PRIMARY KEY, code VARCHAR(5) CHECK (length(code) <= 5))",
     "INSERT INTO codes (code) VALUES ('abcdefgh')", "SELECT code, length(code) FROM codes", [("abcdefgh", "8")]),
    ("unsigned", "stock quantity, never negative", "CREATE TABLE stock (id INTEGER PRIMARY KEY, qty INT UNSIGNED)", "sqlite_unenforced",
     "CREATE TABLE stock (id INTEGER PRIMARY KEY, qty INT UNSIGNED CHECK (qty >= 0))", "INSERT INTO stock (qty) VALUES (-5)", "SELECT qty FROM stock",
     [("-5",)]),
    ("date_type", "events and the day each is held", "CREATE TABLE events (id INTEGER PRIMARY KEY, held_on DATE)", "sqlite_date_type",
     "CREATE TABLE events (id INTEGER PRIMARY KEY, held_on TEXT CHECK (held_on IS date(held_on)))", "INSERT INTO events (held_on) VALUES ('2024-1-5')",
     "SELECT held_on FROM events", [("2024-1-5",)]),
    ("datetime_type", "logins with their time", "CREATE TABLE logins (id INTEGER PRIMARY KEY, at_time DATETIME DEFAULT CURRENT_TIMESTAMP)",
     "sqlite_date_type", "CREATE TABLE logins (id INTEGER PRIMARY KEY, at_time TEXT DEFAULT CURRENT_TIMESTAMP CHECK (at_time IS datetime(at_time)))",
     "INSERT INTO logins (at_time) VALUES (20240105)", "SELECT at_time, typeof(at_time) FROM logins", [("20240105", "integer")]),
    ("date_feb30", "bookings and their day", "CREATE TABLE bookings (id INTEGER PRIMARY KEY, booked_on DATE)", "sqlite_date_type",
     "CREATE TABLE bookings (id INTEGER PRIMARY KEY, booked_on TEXT CHECK (booked_on IS date(booked_on)))",
     "INSERT INTO bookings (booked_on) VALUES ('2024-02-30')", "SELECT booked_on FROM bookings", [("2024-02-30",)]),
    ("string_type", "pin codes", "CREATE TABLE places (id INTEGER PRIMARY KEY, pin STRING)", "sqlite_affinity",
     "CREATE TABLE places (id INTEGER PRIMARY KEY, pin TEXT)", "INSERT INTO places (pin) VALUES ('012345')", "SELECT pin, typeof(pin) FROM places",
     [("12345", "integer")]),
    ("phone_int", "customers with their mobile number", "CREATE TABLE contacts (id INTEGER PRIMARY KEY, phone INTEGER)", "code_as_number",
     "CREATE TABLE contacts (id INTEGER PRIMARY KEY, phone TEXT)", "INSERT INTO contacts (phone) VALUES ('09876543210')", "SELECT phone FROM contacts",
     [("9876543210",)]),
    ("qty_text", "items and how many are in stock", "CREATE TABLE items (id INTEGER PRIMARY KEY, qty TEXT)", "number_as_text",
     "CREATE TABLE items (id INTEGER PRIMARY KEY, qty INTEGER)", "INSERT INTO items (qty) VALUES ('10'), ('9'), ('100')", "SELECT max(qty) FROM items",
     [("9",)]),
    ("money_decimal", "products and their price", "CREATE TABLE products (id INTEGER PRIMARY KEY, price DECIMAL(10,2))", "sqlite_money",
     None, "INSERT INTO products (price) VALUES (0.1 + 0.2)", "SELECT price = 0.3 FROM products", [("0",)]),
    ("money_real", "salaries", "CREATE TABLE staff (id INTEGER PRIMARY KEY, salary REAL)", "sqlite_money",
     None, "INSERT INTO staff (salary) VALUES (0.1 + 0.2)", "SELECT salary = 0.3 FROM staff", [("0",)]),
    ("fk_off", "orders that must belong to a customer",
     "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT); CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers (id))",
     "sqlite_fk_off",
     "PRAGMA foreign_keys = ON; CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT); CREATE TABLE orders (id INTEGER PRIMARY KEY, "
     "customer_id INTEGER REFERENCES customers (id)); CREATE INDEX ix_orders_customer_id ON orders (customer_id)",
     "INSERT INTO orders (customer_id) VALUES (99)", "SELECT count(*) FROM orders", [("1",)]),
    ("fk_target", "invoices pointing at a customer code",
     "PRAGMA foreign_keys = ON; CREATE TABLE customers (id INTEGER PRIMARY KEY, code TEXT); "
     "CREATE TABLE invoices (id INTEGER PRIMARY KEY, customer_code TEXT REFERENCES customers (code))", "fk_target",
     "PRAGMA foreign_keys = ON; CREATE TABLE customers (id INTEGER PRIMARY KEY, code TEXT UNIQUE); "
     "CREATE TABLE invoices (id INTEGER PRIMARY KEY, customer_code TEXT REFERENCES customers (code))",
     "INSERT INTO customers (code) VALUES ('C1'); INSERT INTO invoices (customer_code) VALUES ('C1')", "SELECT count(*) FROM invoices", ERR),
    ("fk_type", "order lines pointing at their order",
     "CREATE TABLE orders (id INTEGER PRIMARY KEY); CREATE TABLE lines (id INTEGER PRIMARY KEY, order_id TEXT REFERENCES orders (id))", "fk_type",
     "CREATE TABLE orders (id INTEGER PRIMARY KEY); CREATE TABLE lines (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders (id))",
     "INSERT INTO orders VALUES (1); INSERT INTO lines (order_id) VALUES ('1')", "SELECT order_id, typeof(order_id) FROM lines", [("1", "text")]),
    ("fk_no_index", "payments of each order, looked up by order",
     "PRAGMA foreign_keys = ON; CREATE TABLE orders (id INTEGER PRIMARY KEY); CREATE TABLE payments (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders (id))",
     "fk_no_index",
     "PRAGMA foreign_keys = ON; CREATE TABLE orders (id INTEGER PRIMARY KEY); CREATE TABLE payments (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders (id)); "
     "CREATE INDEX ix_payments_order_id ON payments (order_id)",
     "SELECT 1", "SELECT count(*) FROM sqlite_master WHERE type = 'index' AND tbl_name = 'payments'", [("0",)]),
    ("strict_varchar", "a strict table of codes", "CREATE TABLE codes (id INTEGER PRIMARY KEY, code VARCHAR(20)) STRICT", "sqlite_strict_type",
     "CREATE TABLE codes (id INTEGER PRIMARY KEY, code TEXT) STRICT", "INSERT INTO codes (code) VALUES ('A1')", "SELECT code FROM codes", ERR),
    ("strict_date", "a strict table of holidays", "CREATE TABLE holidays (id INTEGER PRIMARY KEY, day DATE, paid BOOLEAN) STRICT", "sqlite_strict_type",
     "CREATE TABLE holidays (id INTEGER PRIMARY KEY, day TEXT, paid INTEGER) STRICT", "INSERT INTO holidays (day, paid) VALUES ('2024-08-15', 1)",
     "SELECT day, paid FROM holidays", ERR),
    ("mysql_table", "the users table from our mysql app",
     "CREATE TABLE `users` (`id` int(11) NOT NULL AUTO_INCREMENT, `email` varchar(255) NOT NULL, `status` enum('active','blocked') DEFAULT 'active', "
     "PRIMARY KEY (`id`), UNIQUE KEY `uq_email` (`email`), KEY `ix_status` (`status`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4", "ddl_dialect",
     "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, status TEXT CHECK (status IN ('active', 'blocked')) DEFAULT 'active', "
     "UNIQUE (email)); CREATE INDEX ix_users_status ON users (status)",
     "INSERT INTO users (email) VALUES ('a@x.test'); INSERT INTO users (email) VALUES ('b@x.test')", "SELECT id, email, status FROM users ORDER BY id", ERR),
    ("enum", "orders with a status from a fixed list", "CREATE TABLE orders (id INTEGER PRIMARY KEY, status ENUM('new','paid'))", "ddl_dialect",
     "CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT CHECK (status IN ('new', 'paid')))", "INSERT INTO orders (status) VALUES ('new')",
     "SELECT status FROM orders", ERR),
    ("default_now", "notes with the time they were written", "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT, created_at TEXT DEFAULT now())",
     "ddl_dialect", "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)", "INSERT INTO notes (body) VALUES ('x')",
     "SELECT body, length(created_at) FROM notes", ERR),
    ("on_update", "pages with the time they last changed",
     "CREATE TABLE pages (id INTEGER PRIMARY KEY, title TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP)", "ddl_dialect",
     "CREATE TABLE pages (id INTEGER PRIMARY KEY, title TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)", "INSERT INTO pages (title) VALUES ('x')",
     "SELECT title, length(updated_at) FROM pages", ERR),
    ("inline_key", "visits looked up by page", "CREATE TABLE visits (id INTEGER PRIMARY KEY, page_id INTEGER, KEY ix_page (page_id))", "ddl_dialect",
     "CREATE TABLE visits (id INTEGER PRIMARY KEY, page_id INTEGER); CREATE INDEX ix_visits_page_id ON visits (page_id)",
     "INSERT INTO visits (page_id) VALUES (3)", "SELECT count(*) FROM sqlite_master WHERE type = 'index' AND tbl_name = 'visits'", ERR),
    ("engine_opts", "a settings table (mysql options)", "CREATE TABLE settings (id INTEGER PRIMARY KEY, k TEXT, v TEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
     "ddl_dialect", "CREATE TABLE settings (id INTEGER PRIMARY KEY, k TEXT, v TEXT)", "INSERT INTO settings (k, v) VALUES ('a', 'b')", "SELECT k, v FROM settings", ERR),
    ("reserved", "menu items in their display order", "CREATE TABLE menu (id INTEGER PRIMARY KEY, label TEXT, order INTEGER)", "reserved_name",
     "CREATE TABLE menu (id INTEGER PRIMARY KEY, label TEXT, \"order\" INTEGER)", "INSERT INTO menu (label, \"order\") VALUES ('a', 1)",
     "SELECT label, \"order\" FROM menu", ERR),
    ("two_pk", "enrolments of students in courses", "CREATE TABLE enrolments (student_id INTEGER PRIMARY KEY, course_id INTEGER PRIMARY KEY)",
     "two_primary_keys", None,
     "INSERT INTO enrolments VALUES (1, 2)", "SELECT student_id, course_id FROM enrolments", ERR),
    ("no_pk", "a log of messages", "CREATE TABLE logs (msg TEXT, created_at TEXT)", "no_primary_key",
     "CREATE TABLE logs (id INTEGER PRIMARY KEY, msg TEXT, created_at TEXT)", "INSERT INTO logs (msg) VALUES ('a'); INSERT INTO logs (msg) VALUES ('a')",
     "SELECT count(*) FROM pragma_table_info('logs') WHERE pk > 0", [("0",)]),
]
# query problems on the practice tables: (id, words, wrong query, topic, correct SQLite query, keep row order?)
Q = [
    ("strftime_year", "orders of 2024", "SELECT id FROM orders WHERE strftime('%Y', order_date) = 2024 ORDER BY id", "type_compare",
     "SELECT id FROM orders WHERE strftime('%Y', order_date) = '2024' ORDER BY id", True),
    ("strftime_month", "orders placed in July", "SELECT id FROM orders WHERE strftime('%m', order_date) = 7 ORDER BY id", "type_compare",
     "SELECT id FROM orders WHERE strftime('%m', order_date) = '07' ORDER BY id", True),
    ("strftime_month_text", "orders placed in June", "SELECT id FROM orders WHERE strftime('%m', order_date) = '6' ORDER BY id", "type_compare",
     "SELECT id FROM orders WHERE strftime('%m', order_date) = '06' ORDER BY id", True),
    ("strftime_ge", "orders from 2025 on", "SELECT id FROM orders WHERE strftime('%Y', order_date) >= 2025 ORDER BY id", "type_compare",
     "SELECT id FROM orders WHERE order_date >= '2025-01-01' ORDER BY id", True),
    ("strftime_in", "orders of June or July", "SELECT id FROM orders WHERE strftime('%m', order_date) IN (6, 7) ORDER BY id", "type_compare",
     "SELECT id FROM orders WHERE strftime('%m', order_date) IN ('06', '07') ORDER BY id", True),
    ("strftime_between", "orders from June to July", "SELECT COUNT(*) FROM orders WHERE strftime('%m', order_date) BETWEEN 6 AND 7", "type_compare",
     "SELECT COUNT(*) FROM orders WHERE order_date >= '2024-06-01' AND order_date < '2024-08-01'", False),
    ("substr_year", "customers who joined in 2024", "SELECT id FROM customers WHERE substr(created_at, 1, 4) = 2024 ORDER BY id", "type_compare",
     "SELECT id FROM customers WHERE created_at LIKE '2024%' ORDER BY id", True),
    ("length_quoted", "customers with a four-letter first name", "SELECT id FROM customers WHERE length(first_name) = '4' ORDER BY id", "type_compare",
     "SELECT id FROM customers WHERE length(first_name) = 4 ORDER BY id", True),
    ("upper_number", "customers whose phone, trimmed, is 98100", "SELECT id FROM customers WHERE trim(phone) = 98100 ORDER BY id", "type_compare",
     "SELECT id FROM customers WHERE phone = '98100' ORDER BY id", True),
    ("date_unpadded", "orders from 1 July", "SELECT id FROM orders WHERE order_date >= '2024-7-1' ORDER BY id", "date_shape",
     "SELECT id FROM orders WHERE order_date >= '2024-07-01' ORDER BY id", True),
    ("date_between_unpadded", "orders in June", "SELECT id FROM orders WHERE order_date BETWEEN '2024-6-1' AND '2024-6-30' ORDER BY id", "date_shape",
     "SELECT id FROM orders WHERE order_date BETWEEN '2024-06-01' AND '2024-06-30' ORDER BY id", True),
    ("date_dmy", "orders before 15 July", "SELECT id FROM orders WHERE order_date < '15/07/2024' ORDER BY id", "date_shape",
     "SELECT id FROM orders WHERE order_date < '2024-07-15' ORDER BY id", True),
    ("date_slash_ymd", "orders of 15 June", "SELECT id FROM orders WHERE order_date = '2024/06/15' ORDER BY id", "date_shape",
     "SELECT id FROM orders WHERE order_date = '2024-06-15' ORDER BY id", True),
    ("date_fn_unpadded", "orders from 1 July (with date())", "SELECT id FROM orders WHERE order_date >= date('2024-7-1') ORDER BY id", "date_shape",
     "SELECT id FROM orders WHERE order_date >= '2024-07-01' ORDER BY id", True),
    ("payment_unpadded", "payments made in the first week of June", "SELECT id FROM payments WHERE paid_at < '2024-6-8' ORDER BY id", "date_shape",
     "SELECT id FROM payments WHERE paid_at < '2024-06-08' ORDER BY id", True),
]
# ALTER TABLE forms SQLite refuses (the dialect file's 'refuses' entries from night 2): (setup, statement, what the rewrite must
# leave behind: a query and its rows; None when there is no rewrite)
SETUP = "CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, email TEXT, city TEXT); INSERT INTO people (name, email) VALUES ('a', 'a@x.test'), ('b', NULL)"
REFUSED_TONIGHT = {
    "alter_add_constraint_unique": ("ALTER TABLE people ADD CONSTRAINT uq_people_email UNIQUE (email)",
                                    ("SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'uq_people_email'", [("uq_people_email",)])),
    "alter_add_unique": ("ALTER TABLE people ADD UNIQUE (email)", ("SELECT count(*) FROM pragma_index_list('people') WHERE \"unique\" = 1", [("1",)])),
    "alter_add_index": ("ALTER TABLE people ADD INDEX ix_people_city (city)",
                        ("SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'ix_people_city'", [("ix_people_city",)])),
    "alter_add_key": ("ALTER TABLE people ADD CONSTRAINT fk_city FOREIGN KEY (city) REFERENCES cities (name)", None),
    "alter_add_if_not_exists": ("ALTER TABLE people ADD COLUMN IF NOT EXISTS phone TEXT",
                                ("SELECT count(*) FROM pragma_table_info('people') WHERE name = 'phone'", [("1",)])),
    "alter_add_many": ("ALTER TABLE people ADD COLUMN phone TEXT, ADD COLUMN pin TEXT", None),
    "alter_add_unique_column": ("ALTER TABLE people ADD COLUMN code TEXT UNIQUE",
                                ("SELECT count(*) FROM pragma_index_list('people') WHERE \"unique\" = 1", [("1",)])),
    "alter_add_pk_column": ("ALTER TABLE people ADD COLUMN uid INTEGER PRIMARY KEY", None),
    "alter_drop_constraint": ("ALTER TABLE people DROP CONSTRAINT uq_people_email", None),
    "alter_add_now_default": ("ALTER TABLE people ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP", None),
    "alter_add_not_null": ("ALTER TABLE people ADD COLUMN status TEXT NOT NULL", None),
    "alter_set_not_null": ("ALTER TABLE people ALTER COLUMN email SET NOT NULL", None),
}


def _fresh():
    return sqlite3.connect(":memory:", isolation_level=None)


def _probe(script, probe, query):
    """What a table script does on SQLite: 'error' when the script or the probe is refused, else the probe query's rows."""
    c = _fresh()
    try:
        c.executescript(script)
        c.executescript(probe)
        return [tuple(S.norm(v) for v in r) for r in c.execute(query).fetchall()]
    except sqlite3.Error:
        return ERR
    finally:
        c.close()


@pytest.fixture()
def lite():
    d = tempfile.mkdtemp()
    conn = S.sqlite_db(os.path.join(d, "bank.db"))
    yield conn
    conn.close()
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    os.rmdir(d)


def _rows(conn, sql, ordered):
    out = [tuple(S.norm(v) for v in r) for r in conn.execute(sql).fetchall()]
    return out if ordered else sorted(out)


def test_the_bank_has_enough_problems():
    ids = [p[0] for p in D] + [q[0] for q in Q]
    assert len(ids) >= 30 and len(set(ids)) == len(ids)


@pytest.mark.parametrize("pid,words,wrong,topic,fix,probe,query,lab", D, ids=[p[0] for p in D])
def test_the_lab_shows_each_design_mistake(pid, words, wrong, topic, fix, probe, query, lab):
    """What the person's script really does on SQLite (LAB), and that the correct script differs on the same probe."""
    assert _probe(wrong, probe, query) == lab, pid
    if fix:
        assert _probe(fix, probe, query) != lab, (pid, "the correct script behaves differently")


@pytest.mark.parametrize("pid,words,wrong,topic,fix,probe,query,lab", D, ids=[p[0] for p in D])
def test_dbaba_finds_and_fixes_each_design(pid, words, wrong, topic, fix, probe, query, lab):
    res = analyze_sql(f"{wrong};\n\n{words}", engine="sqlite")
    found = [f for f in res["correct"]["findings"] if f["topic"] == topic]
    assert found, (pid, [(f["topic"], f["title_en"]) for f in res["correct"]["findings"]])
    assert all(f.get("basis") for f in found), (pid, "every finding names its fundamental")
    if fix is None:                                       # a decision for the person (exact money): said, not rewritten
        return
    offered = res["correct"].get("corrected")
    assert offered, (pid, "no corrected script")
    assert _probe(offered, probe, query) == _probe(fix, probe, query), (pid, offered)
    again = sqlcorrect.check(offered, engine="sqlite")["findings"]
    assert not [f for f in again if f["kind"] in ("error", "wrong")], (pid, offered, again)


@pytest.mark.parametrize("pid,words,wrong,topic,fix,ordered", Q, ids=[q[0] for q in Q])
def test_dbaba_finds_and_fixes_each_query(lite, pid, words, wrong, topic, fix, ordered):
    assert _rows(lite, wrong, ordered) != _rows(lite, fix, ordered), (pid, "the lab: the person's query gives other rows")
    res = analyze_sql(f"{wrong};\n\n{words}", engine="sqlite")
    found = [f for f in res["correct"]["findings"] if f["topic"] == topic]
    assert found and all(f["kind"] == "wrong" for f in found), (pid, [(f["topic"], f["kind"], f["title_en"]) for f in res["correct"]["findings"]])
    extra = [f["title_en"] for f in res["correct"]["findings"] if f["kind"] in ("error", "wrong") and f["topic"] not in sqlport.PORT_TOPICS]
    assert not extra, (pid, extra)
    offered = res["correct"].get("corrected")
    assert offered, (pid, "no corrected query")
    assert _rows(lite, offered, ordered) == _rows(lite, fix, ordered), (pid, offered)


def test_the_same_queries_mean_something_else_on_the_servers():
    """MySQL/MariaDB turn the text into a number (YEAR-like text = 2024 matches) and read '2024-7-1' as a date: no finding there."""
    for q in ["SELECT id FROM orders WHERE DATE_FORMAT(order_date, '%Y') = 2024", "SELECT id FROM orders WHERE order_date >= '2024-7-1'"]:
        bad = [f for f in sqlcorrect.check(q, engine="mysql")["findings"] if f["topic"] in ("type_compare", "date_shape")]
        assert not bad, (q, bad)


def test_values_compared_with_a_column_are_left_alone():
    """A column converts what it is compared with (code = 2 on a TEXT column finds '2'): only expressions are flagged."""
    for q in ["SELECT id FROM orders WHERE customer_id = '3'", "SELECT id FROM customers WHERE phone = 98100",
              "SELECT id FROM orders WHERE CAST(strftime('%Y', order_date) AS INTEGER) = 2024", "SELECT id FROM orders WHERE order_date >= '2024-07-01'",
              "SELECT id FROM orders WHERE strftime('%Y', order_date) = '2024'", "SELECT id FROM orders WHERE length(status) = 4",
              "SELECT strftime('%d/%m/%Y', order_date) AS shown FROM orders WHERE id = 1"]:
        bad = [f for f in sqlcorrect.check(q, engine="sqlite")["findings"] if f["topic"] in ("type_compare", "date_shape")]
        assert not bad, (q, bad)


def test_a_bound_value_is_a_check_not_a_rewrite():
    res = sqlcorrect.check("SELECT id FROM orders WHERE strftime('%Y', order_date) = ?", engine="sqlite")
    hit = [f for f in res["findings"] if f["topic"] == "type_compare"]
    assert hit and hit[0]["kind"] == "check" and not res.get("corrected"), res
    c = _fresh()
    c.executescript("CREATE TABLE o (d TEXT); INSERT INTO o VALUES ('2024-03-09')")
    assert c.execute("SELECT count(*) FROM o WHERE strftime('%Y', d) = ?", (2024,)).fetchone() == (0,)     # the lab: a bound int
    assert c.execute("SELECT count(*) FROM o WHERE strftime('%Y', d) = ?", ("2024",)).fetchone() == (1,)


def test_an_ambiguous_date_is_said_not_guessed(lite):
    res = sqlcorrect.check("SELECT id FROM orders WHERE order_date < '05/07/2024'", engine="sqlite")
    hit = [f for f in res["findings"] if f["topic"] == "date_shape"]
    assert hit and "2024-07-05" in hit[0]["fix_en"] and "2024-05-07" in hit[0]["fix_en"] and not res.get("corrected"), res


@pytest.mark.parametrize("rid", sorted(REFUSED_TONIGHT))
def test_alter_forms_sqlite_refuses(rid):
    stmt, after = REFUSED_TONIGHT[rid]
    r = next(x for x in E.dialect("sqlite")["refuses"] if x["id"] == rid)
    assert r["proven"] == ["sqlite"]
    c = _fresh()
    c.executescript(SETUP)
    with pytest.raises(sqlite3.Error):                     # the lab (rows present; an older SQLite refuses SET NOT NULL outright)
        c.execute(stmt)
    res = sqlcorrect.check(stmt, engine="sqlite")
    hit = [f for f in res["findings"] if f["topic"] == "dialect"]
    assert hit and hit[0]["kind"] == r.get("kind", "error"), (rid, res["findings"])
    if after:
        assert res["corrected"], rid
        c.executescript(res["corrected"])
        assert [tuple(S.norm(v) for v in row) for row in c.execute(after[0]).fetchall()] == after[1], (rid, res["corrected"])
    else:
        assert not res["corrected"], (rid, res["corrected"])


def test_the_alter_forms_sqlite_does_take_are_left_alone():
    c = _fresh()
    c.executescript(SETUP)
    for stmt in ["ALTER TABLE people ADD COLUMN phone TEXT", "ALTER TABLE people ADD COLUMN status TEXT NOT NULL DEFAULT 'new'",
                 "ALTER TABLE people RENAME COLUMN city TO town", "ALTER TABLE people DROP COLUMN phone", "ALTER TABLE people RENAME TO persons"]:
        assert not [f for f in sqlcorrect.check(stmt, engine="sqlite")["findings"] if f["kind"] in ("error", "wrong")], stmt
        c.execute(stmt)


@pytest.mark.skipif(not V353, reason="ALTER COLUMN ... SET / DROP NOT NULL came in SQLite 3.53")
def test_set_not_null_on_3_53():
    c = _fresh()
    c.executescript("CREATE TABLE t (a TEXT); INSERT INTO t VALUES ('x'); ALTER TABLE t ALTER COLUMN a SET NOT NULL")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO t VALUES (NULL)")
    c.execute("ALTER TABLE t ALTER COLUMN a DROP NOT NULL")
    c.execute("INSERT INTO t VALUES (NULL)")


def test_good_sqlite_designs_get_no_mistakes():
    for sql in [
        "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE, created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, qty INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0), price_paise INTEGER NOT NULL)",
        "CREATE TABLE t (code TEXT NOT NULL PRIMARY KEY, label TEXT) WITHOUT ROWID",
        "CREATE TABLE t (id INTEGER PRIMARY KEY, n INT, day TEXT CHECK (day IS date(day))) STRICT",
        "PRAGMA foreign_keys = ON; CREATE TABLE p (id INTEGER PRIMARY KEY); CREATE TABLE c (id INTEGER PRIMARY KEY, p_id INTEGER NOT NULL REFERENCES p (id)); "
        "CREATE INDEX ix_c_p_id ON c (p_id)",
    ]:
        res = sqlcorrect.check(sql, engine="sqlite")
        assert not [f for f in res["findings"] if f["kind"] in ("error", "wrong")], (sql, res["findings"])
        _fresh().executescript(sql)


def test_mysql_and_postgresql_designs_are_read_as_before():
    """The SQLite reading is only for SQLite (rule A): the same scripts on MySQL / PostgreSQL get their own engines' answers."""
    my = sqlcorrect.check("CREATE TABLE users (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(50))", engine="mysql")
    assert not [f for f in my["findings"] if f["topic"].startswith("sqlite_") or f["kind"] in ("error", "wrong")], my["findings"]
    pg = sqlcorrect.check("CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT)", engine="postgresql")
    assert not [f for f in pg["findings"] if f["topic"].startswith("sqlite_")], pg["findings"]
    money = sqlcorrect.check("CREATE TABLE p (id INT PRIMARY KEY, price FLOAT)", engine="mysql")
    assert any(f["topic"] == "money_float" for f in money["findings"]), money["findings"]


def test_rule_a_a_sqlite_script_is_read_as_sqlite_without_being_named():
    from app.agents.sqlbasics import CHOSEN

    assert CHOSEN.get() is None
    res = sqlcorrect.check("CREATE TABLE notes (id INT PRIMARY KEY, body TEXT) STRICT")
    assert any(f["topic"] == "sqlite_rowid_pk" for f in res["findings"]), res["findings"]
    res = sqlcorrect.check("CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, made DATE)")
    assert any(f["topic"] == "sqlite_date_type" for f in res["findings"]), res["findings"]


def test_tonights_topics_have_their_fundamentals_and_proof():
    import json

    from app.agents import sqlschema

    funds = json.load(open("app/products/dbaba/dbknow/core/query_fundamentals.json"))["fundamentals"]
    topics = {t for f in funds.values() for t in f["topics"]}
    tonight = {"sqlite_rowid_pk", "sqlite_pk_null", "sqlite_unenforced", "sqlite_date_type", "sqlite_money", "sqlite_affinity", "sqlite_fk_off",
               "sqlite_strict_type", "type_compare", "date_shape"}
    assert tonight <= topics
    assert all("sqlite" in sqlschema.PROVEN[t] for t in tonight - {"type_compare", "date_shape"})
    rules = E.dialect("sqlite")
    assert all(rules["schema_rules"][k]["proven"] == ["sqlite"] for k in ("rowid_alias", "pk_null", "unenforced", "no_date_type", "money", "strict",
                                                                          "foreign_keys", "affinity"))
    assert all(rules["query_rules"][k]["proven"] == ["sqlite"] for k in ("type_compare", "date_shape"))


# SQLite's own messages for table designs, as the lab produced them (3.53 and the 3.45 command line)
ERRORS = [
    ("CREATE TABLE u (n VARCHAR(50)) STRICT", "strict_type"),
    ("CREATE TABLE u (i) STRICT", "strict_type"),
    ("CREATE TABLE u (id INT PRIMARY KEY AUTOINCREMENT)", "autoinc_not_integer_pk"),
    ("CREATE TABLE u (a INTEGER PRIMARY KEY, b TEXT PRIMARY KEY)", "two_primary_keys"),
    ("PRAGMA foreign_keys = ON; CREATE TABLE p (id INTEGER PRIMARY KEY, code TEXT); CREATE TABLE c (id INTEGER PRIMARY KEY, pc TEXT REFERENCES p (code)); "
     "INSERT INTO c (pc) VALUES ('a')", "fk_target_not_key"),
    ("CREATE TABLE u (id INTEGER PRIMARY KEY, n TEXT UNIQUE); ALTER TABLE u DROP COLUMN n", "drop_column_blocked"),
    ("CREATE TABLE u (id INTEGER PRIMARY KEY, n TEXT); CREATE INDEX ix_n ON u (n); ALTER TABLE u DROP COLUMN n", "drop_column_blocked"),
    ("CREATE TABLE u (id INTEGER PRIMARY KEY, n TEXT); CREATE VIEW v AS SELECT n FROM u; ALTER TABLE u DROP COLUMN n", "drop_column_blocked"),
    ("CREATE TABLE u (id INTEGER PRIMARY KEY, n TEXT); ALTER TABLE u DROP COLUMN id", "drop_column_blocked"),
    ("CREATE TABLE u (n TEXT); INSERT INTO u VALUES ('a'); ALTER TABLE u ADD COLUMN c TEXT DEFAULT CURRENT_TIMESTAMP", "alter_not_supported"),
]


@pytest.mark.parametrize("script,cause", ERRORS)
def test_sqlites_design_errors_are_diagnosed_from_the_real_message(script, cause):
    c = _fresh()
    with pytest.raises(sqlite3.Error) as e:
        c.executescript(script)
    msg = str(e.value)
    for text in (f"sqlite3.{type(e.value).__name__}: {msg}", f"Error: in prepare, {msg}", f"SQLSTATE[HY000]: General error: 1 {msg}"):
        res = R.diagnose(text)
        assert res["found"] and res["found"][0]["cause"] == cause, (text, [f["cause"] for f in res["found"]])
        assert "sqlite" in res["found"][0]["fact"]["engines"], text


def test_the_new_general_causes_on_the_servers_too():
    for text, cause in [("ERROR 1068 (42000): Multiple primary key defined", "two_primary_keys"),
                        ("ERROR:  multiple primary keys for table \"zz1\" are not allowed", "two_primary_keys"),
                        ("ERROR:  there is no unique constraint matching given keys for referenced table \"zzp\"", "fk_target_not_key")]:
        res = R.diagnose(text)
        assert res["found"] and res["found"][0]["cause"] == cause, (text, res["found"])


def test_the_notes_line_now_names_table_designs():
    assert "table designs" in E.dialect("sqlite")["proven_parts"]["en"]
