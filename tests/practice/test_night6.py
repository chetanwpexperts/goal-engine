"""Night 6 practice bank: table design (CREATE TABLE): keys (no primary key, two primary keys, a foreign key to a table made
later, to a column that is not a key, of another type, without an index on PostgreSQL, on MyISAM), column types (money in
FLOAT / DOUBLE / REAL, dates and numbers kept as text, phone and PIN codes kept as numbers, utf8 vs utf8mb4, TIMESTAMP's
1970-2038 range, widths, VARCHAR without a length, TEXT as a key, the 65,535-byte row), one fact in one place (lists in a
column, numbered columns, a parent's details copied next to its id), reserved words, and a design written for the other
database (rule A).

Each problem: what the person wants (in words), the CREATE TABLE they wrote (wrong), the topic DBaba must find, the database
it is for (None = not said), a probe (statements run after the design: inserts and reads that show what the design does) and a
correct design (per engine when the syntax differs; None = finding it is enough). The correct design is run first and its probe
results are the verified answer. Then DBaba gets the wrong design: it must find the mistake under the right topic, name its
fundamental, and its corrected (or 'if that's what you meant') design must give exactly the same probe results. The wrong
design must give different ones (the mistake really changes something on that engine).

Tables are real tables named n6_* (a design is DDL: MySQL/MariaDB commit it at once), dropped before and after each run.
LAB below is what each engine does with the wrong design (reproduced on MariaDB 10.11 and PostgreSQL 16).
"""

import pytest

from app.agents.sql import analyze_sql
from app.agents.sqlroutine import split_script
from tests.practice import schema as S

TEN = "INSERT INTO n6_t (id, amount) VALUES " + ", ".join(f"({i}, 0.1)" for i in range(1, 11))
MONEY_PROBE = [TEN, "INSERT INTO n6_t (id, amount) VALUES (11, 1234567.89)", "SELECT SUM(amount) = 1 FROM n6_t WHERE id <= 10",
               "SELECT COUNT(*) FROM n6_t WHERE amount = 0.1", "SELECT amount FROM n6_t WHERE id = 11"]
PARENT = "CREATE TABLE n6_customers (id INT PRIMARY KEY, name VARCHAR(50));\n"
PG_INDEX = "SELECT COUNT(*) FROM pg_indexes WHERE tablename = '{t}' AND indexdef LIKE '%({c})%'"

# (id, words, wrong, topic, database said, probe, correct ({'mysql':, 'postgres':} or one text for both, or None))
P = [
    # ---- money in a binary floating type
    ("mf_float", "a table for payments with their amount", "CREATE TABLE n6_t (id INT PRIMARY KEY, amount FLOAT)", "money_float", None,
     MONEY_PROBE, "CREATE TABLE n6_t (id INT PRIMARY KEY, amount DECIMAL(12,2))"),
    ("mf_double", "payments table, amount column", "CREATE TABLE n6_t (id INT PRIMARY KEY, amount DOUBLE)", "money_float", "mariadb",
     MONEY_PROBE, "CREATE TABLE n6_t (id INT PRIMARY KEY, amount DECIMAL(12,2))"),
    ("mf_real", "store the amount of each payment (postgres)", "CREATE TABLE n6_t (id INT PRIMARY KEY, amount REAL)", "money_float",
     "postgresql", MONEY_PROBE, "CREATE TABLE n6_t (id INT PRIMARY KEY, amount DECIMAL(12,2))"),
    ("mf_double_pg", "payment amounts, postgresql", "CREATE TABLE n6_t (id INT PRIMARY KEY, amount DOUBLE PRECISION)", "money_float",
     "postgresql", MONEY_PROBE, "CREATE TABLE n6_t (id INT PRIMARY KEY, amount DECIMAL(12,2))"),
    ("mf_float_args", "amount with 2 decimals (mysql)", "CREATE TABLE n6_t (id INT PRIMARY KEY, amount FLOAT(10,2))", "money_float", "mysql",
     MONEY_PROBE, "CREATE TABLE n6_t (id INT PRIMARY KEY, amount DECIMAL(10,2))"),
    # ---- dates kept as text
    ("dt_order", "orders with the date they were placed", "CREATE TABLE n6_t (id INT PRIMARY KEY, order_date VARCHAR(20))", "date_as_text",
     None, ["INSERT INTO n6_t VALUES (1, '2024-02-30')", "SELECT COUNT(*) FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, order_date DATE)"),
    ("dt_dob", "students and their date of birth", "CREATE TABLE n6_t (id INT PRIMARY KEY, dob CHAR(10))", "date_as_text", None,
     ["INSERT INTO n6_t VALUES (1, '2010-13-01')", "SELECT COUNT(*) FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, dob DATE)"),
    ("dt_created", "when each user signed up, on mariadb", "CREATE TABLE n6_t (id INT PRIMARY KEY, created_at VARCHAR(30))", "date_as_text",
     "mariadb", ["INSERT INTO n6_t VALUES (1, '2024-02-30 10:00:00')", "SELECT COUNT(*) FROM n6_t"],
     "CREATE TABLE n6_t (id INT PRIMARY KEY, created_at DATETIME)"),
    ("dt_paid_pg", "the day each bill was paid (postgres)", "CREATE TABLE n6_t (id INT PRIMARY KEY, paid_on TEXT)", "date_as_text",
     "postgresql", ["INSERT INTO n6_t VALUES (1, '31/04/2024')", "SELECT COUNT(*) FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, paid_on DATE)"),
    # ---- numbers kept as text
    ("nt_price", "products and their price", "CREATE TABLE n6_t (id INT PRIMARY KEY, price VARCHAR(20))", "number_as_text", None,
     ["INSERT INTO n6_t VALUES (1, '100'), (2, '9')", "SELECT MAX(price) FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, price DECIMAL(12,2))"),
    ("nt_stock", "how many units are in stock", "CREATE TABLE n6_t (id INT PRIMARY KEY, stock VARCHAR(10))", "number_as_text", None,
     ["INSERT INTO n6_t VALUES (1, '100'), (2, '9')", "SELECT id FROM n6_t ORDER BY stock DESC LIMIT 1"], "CREATE TABLE n6_t (id INT PRIMARY KEY, stock INT)"),
    ("nt_qty_pg", "quantity of each line, postgresql", "CREATE TABLE n6_t (id INT PRIMARY KEY, quantity TEXT)", "number_as_text", "postgresql",
     ["INSERT INTO n6_t VALUES (1, '3'), (2, '4')", "SELECT SUM(quantity) FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, quantity INT)"),
    ("nt_salary", "staff salaries (mariadb)", "CREATE TABLE n6_t (id INT PRIMARY KEY, salary VARCHAR(15))", "number_as_text", "mariadb",
     ["INSERT INTO n6_t VALUES (1, '90000'), (2, '120000')", "SELECT MAX(salary) FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, salary DECIMAL(12,2))"),
    # ---- codes made of digits kept as numbers
    ("cn_phone", "customers with their mobile number", "CREATE TABLE n6_t (id INT PRIMARY KEY, phone INT)", "code_as_number", None,
     ["INSERT INTO n6_t VALUES (1, '9810012345')", "SELECT phone FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, phone VARCHAR(20))"),
    ("cn_pincode", "addresses with a PIN code", "CREATE TABLE n6_t (id INT PRIMARY KEY, pincode INT)", "code_as_number", None,
     ["INSERT INTO n6_t VALUES (1, '012345')", "SELECT pincode FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, pincode VARCHAR(20))"),
    ("cn_mobile_pg", "keep each patient's mobile (postgres)", "CREATE TABLE n6_t (id INT PRIMARY KEY, mobile BIGINT)", "code_as_number",
     "postgresql", ["INSERT INTO n6_t VALUES (1, '09810012345')", "SELECT mobile FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, mobile VARCHAR(20))"),
    # ---- no primary key
    ("pk_id", "a customers table", "CREATE TABLE n6_t (id INT, name VARCHAR(50))", "no_primary_key", None,
     ["INSERT INTO n6_t VALUES (1, 'Asha')", "INSERT INTO n6_t VALUES (1, 'Asha')", "SELECT COUNT(*) FROM n6_t"],
     "CREATE TABLE n6_t (id INT, name VARCHAR(50), PRIMARY KEY (id))"),
    ("pk_link", "which student takes which course", "CREATE TABLE n6_t (student_id INT NOT NULL, course_id INT NOT NULL)", "no_primary_key",
     None, ["INSERT INTO n6_t VALUES (1, 2)", "INSERT INTO n6_t VALUES (1, 2)", "SELECT COUNT(*) FROM n6_t"],
     "CREATE TABLE n6_t (student_id INT NOT NULL, course_id INT NOT NULL, PRIMARY KEY (student_id, course_id))"),
    ("pk_new_my", "notes people write (mariadb)", "CREATE TABLE n6_t (note TEXT, author VARCHAR(50))", "no_primary_key", "mariadb",
     ["INSERT INTO n6_t (note, author) VALUES ('hello', 'Asha')", "SELECT id, note FROM n6_t"],
     "CREATE TABLE n6_t (id INT AUTO_INCREMENT PRIMARY KEY, note TEXT, author VARCHAR(50))"),
    ("pk_new_pg", "notes people write (postgresql)", "CREATE TABLE n6_t (note TEXT, author VARCHAR(50))", "no_primary_key", "postgresql",
     ["INSERT INTO n6_t (note, author) VALUES ('hello', 'Asha')", "SELECT id, note FROM n6_t"],
     "CREATE TABLE n6_t (id INT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, note TEXT, author VARCHAR(50))"),
    # ---- PostgreSQL makes no index for a foreign key
    ("fx_orders", "orders that belong to customers, postgres",
     PARENT + "CREATE TABLE n6_orders (id INT PRIMARY KEY, customer_id INT, FOREIGN KEY (customer_id) REFERENCES n6_customers (id));",
     "fk_no_index", "postgresql", [PG_INDEX.format(t="n6_orders", c="customer_id")],
     PARENT + "CREATE TABLE n6_orders (id INT PRIMARY KEY, customer_id INT, FOREIGN KEY (customer_id) REFERENCES n6_customers (id));\n"
     "CREATE INDEX ix_n6_orders_customer_id ON n6_orders (customer_id);"),
    ("fx_items", "order lines: which product in which order (postgresql)",
     "CREATE TABLE n6_products (id INT PRIMARY KEY);\nCREATE TABLE n6_orders (id INT PRIMARY KEY);\n"
     "CREATE TABLE n6_items (order_id INT REFERENCES n6_orders (id), product_id INT REFERENCES n6_products (id), qty INT, "
     "PRIMARY KEY (order_id, product_id));", "fk_no_index", "postgresql",
     [PG_INDEX.format(t="n6_items", c="product_id"), PG_INDEX.format(t="n6_items", c="order_id")],
     "CREATE TABLE n6_products (id INT PRIMARY KEY);\nCREATE TABLE n6_orders (id INT PRIMARY KEY);\n"
     "CREATE TABLE n6_items (order_id INT REFERENCES n6_orders (id), product_id INT REFERENCES n6_products (id), qty INT, "
     "PRIMARY KEY (order_id, product_id));\nCREATE INDEX ix_n6_items_product_id ON n6_items (product_id);"),
    # ---- utf8 on MySQL/MariaDB is utf8mb3
    ("u8_table", "comments that can have emoji, mariadb", "CREATE TABLE n6_t (id INT PRIMARY KEY, note VARCHAR(50)) DEFAULT CHARSET=utf8",
     "utf8mb3", "mariadb", ["INSERT INTO n6_t VALUES (1, 'nice 😀')", "SELECT note FROM n6_t"],
     "CREATE TABLE n6_t (id INT PRIMARY KEY, note VARCHAR(50)) DEFAULT CHARSET=utf8mb4"),
    ("u8_column", "a review text column (mysql)",
     "CREATE TABLE n6_t (id INT PRIMARY KEY, review VARCHAR(200) CHARACTER SET utf8 COLLATE utf8_general_ci)", "utf8mb3", "mysql",
     ["INSERT INTO n6_t VALUES (1, 'loved it 👍')", "SELECT review FROM n6_t"],
     "CREATE TABLE n6_t (id INT PRIMARY KEY, review VARCHAR(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci)"),
    ("u8_mb3", "messages table on mariadb", "CREATE TABLE n6_t (id INT PRIMARY KEY, body TEXT) ENGINE=InnoDB CHARSET=utf8mb3",
     "utf8mb3", "mariadb", ["INSERT INTO n6_t VALUES (1, 'ok 🙏')", "SELECT body FROM n6_t"],
     "CREATE TABLE n6_t (id INT PRIMARY KEY, body TEXT) ENGINE=InnoDB CHARSET=utf8mb4"),
    # ---- MyISAM keeps no foreign keys
    ("my_fk", "orders must belong to a real customer (mariadb)",
     "CREATE TABLE n6_customers (id INT PRIMARY KEY) ENGINE=MyISAM;\n"
     "CREATE TABLE n6_orders (id INT PRIMARY KEY, customer_id INT, FOREIGN KEY (customer_id) REFERENCES n6_customers (id)) ENGINE=MyISAM;",
     "myisam_fk", "mariadb", ["INSERT INTO n6_orders VALUES (1, 99)", "SELECT COUNT(*) FROM n6_orders"],
     "CREATE TABLE n6_customers (id INT PRIMARY KEY) ENGINE=InnoDB;\n"
     "CREATE TABLE n6_orders (id INT PRIMARY KEY, customer_id INT, FOREIGN KEY (customer_id) REFERENCES n6_customers (id)) ENGINE=InnoDB;"),
    # ---- written for the other database (rule A)
    ("dl_pg", "products table, this is postgresql",
     "CREATE TABLE n6_t (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, name VARCHAR(80) NOT NULL, price DECIMAL(10,2), added DATETIME, "
     "active TINYINT(1) DEFAULT 1, KEY ix_name (name)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;", "ddl_dialect", "postgresql",
     ["INSERT INTO n6_t (name, price, added) VALUES ('Tea', 20.50, '2024-05-01 10:00:00')", "SELECT id, name, price, added, active FROM n6_t",
      PG_INDEX.format(t="n6_t", c="name")],
     "CREATE TABLE n6_t (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, name VARCHAR(80) NOT NULL, price DECIMAL(10,2), "
     "added TIMESTAMP, active SMALLINT DEFAULT 1);\nCREATE INDEX ix_n6_t_name ON n6_t (name);"),
    ("dl_pg_backticks", "posts table for postgres",
     "CREATE TABLE `n6_t` (`id` BIGINT(20) NOT NULL AUTO_INCREMENT, `title` VARCHAR(200) COMMENT 'headline', `body` LONGTEXT, "
     "`score` DOUBLE, `changed` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, PRIMARY KEY (`id`)) "
     "ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;", "ddl_dialect", "postgresql",
     ["INSERT INTO n6_t (title, body, score) VALUES ('Hi', 'text', 1.5)", "SELECT id, title, body, score FROM n6_t"],
     "CREATE TABLE n6_t (id BIGINT NOT NULL GENERATED BY DEFAULT AS IDENTITY, title VARCHAR(200), body TEXT, score DOUBLE PRECISION, "
     "changed TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id));"),
    ("dl_pg_enum", "orders with a status, postgres",
     "CREATE TABLE n6_t (id INT PRIMARY KEY, status ENUM('new','shipped') NOT NULL, paid BOOLEAN DEFAULT 0)", "ddl_dialect", "postgresql",
     ["INSERT INTO n6_t (id, status) VALUES (1, 'new')", "INSERT INTO n6_t (id, status) VALUES (2, 'lost')", "SELECT id, status, paid FROM n6_t"],
     "CREATE TABLE n6_t (id INT PRIMARY KEY, status VARCHAR(7) CHECK (status IN ('new', 'shipped')) NOT NULL, paid BOOLEAN DEFAULT false)"),
    ("dl_my", "events table, mariadb",
     "CREATE TABLE n6_t (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, title TEXT NOT NULL, starts_at TIMESTAMPTZ, details JSONB, "
     "raw BYTEA)", "ddl_dialect", "mariadb",
     ["INSERT INTO n6_t (title, starts_at, details) VALUES ('Launch', '2024-05-01 10:00:00', '{\"a\": 1}')", "SELECT id, title, starts_at FROM n6_t"],
     "CREATE TABLE n6_t (id BIGINT AUTO_INCREMENT PRIMARY KEY, title TEXT NOT NULL, starts_at DATETIME, details JSON, raw LONGBLOB)"),
    ("dl_my_serial", "products with tags (mysql)",
     "CREATE TABLE n6_t (id BIGSERIAL PRIMARY KEY, price NUMERIC(10,2), tags TEXT[], note TEXT DEFAULT ''::text)", "ddl_dialect", "mysql",
     ["INSERT INTO n6_t (price, tags) VALUES (9.5, '[\"x\"]')", "SELECT id, price, note FROM n6_t"],
     "CREATE TABLE n6_t (id BIGINT AUTO_INCREMENT PRIMARY KEY, price NUMERIC(10,2), tags JSON, note TEXT DEFAULT '')"),
    # ---- two primary keys
    ("tp_two", "an id and a unique product code", "CREATE TABLE n6_t (id INT PRIMARY KEY, code VARCHAR(10) PRIMARY KEY)", "two_primary_keys",
     None, ["SELECT 1"], None),
    # ---- the two ends of a link must have the same type
    ("ft_bigint", "orders point at customers whose id is BIGINT (mariadb)",
     "CREATE TABLE n6_customers (id BIGINT PRIMARY KEY);\n"
     "CREATE TABLE n6_orders (id INT PRIMARY KEY, customer_id INT, FOREIGN KEY (customer_id) REFERENCES n6_customers (id));", "fk_type", "mariadb",
     ["INSERT INTO n6_customers VALUES (5000000000)", "INSERT INTO n6_orders VALUES (1, 5000000000)", "SELECT customer_id FROM n6_orders"],
     "CREATE TABLE n6_customers (id BIGINT PRIMARY KEY);\n"
     "CREATE TABLE n6_orders (id INT PRIMARY KEY, customer_id BIGINT, FOREIGN KEY (customer_id) REFERENCES n6_customers (id));"),
    ("ft_unsigned", "orders point at customers (mysql)",
     "CREATE TABLE n6_customers (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY);\n"
     "CREATE TABLE n6_orders (id INT PRIMARY KEY, customer_id INT, FOREIGN KEY (customer_id) REFERENCES n6_customers (id));", "fk_type", "mysql",
     ["INSERT INTO n6_customers VALUES (7)", "INSERT INTO n6_orders VALUES (1, 7)", "SELECT customer_id FROM n6_orders"],
     "CREATE TABLE n6_customers (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY);\n"
     "CREATE TABLE n6_orders (id INT PRIMARY KEY, customer_id INT UNSIGNED, FOREIGN KEY (customer_id) REFERENCES n6_customers (id));"),
    ("ft_text", "items point at a category code",
     "CREATE TABLE n6_categories (code VARCHAR(10) PRIMARY KEY);\n"
     "CREATE TABLE n6_items (id INT PRIMARY KEY, category_code INT, FOREIGN KEY (category_code) REFERENCES n6_categories (code));", "fk_type", None,
     ["INSERT INTO n6_categories VALUES ('TOYS')", "INSERT INTO n6_items VALUES (1, 'TOYS')", "SELECT category_code FROM n6_items"],
     "CREATE TABLE n6_categories (code VARCHAR(10) PRIMARY KEY);\n"
     "CREATE TABLE n6_items (id INT PRIMARY KEY, category_code VARCHAR(10), FOREIGN KEY (category_code) REFERENCES n6_categories (code));"),
    # ---- a link to a table that comes later
    ("fo_order", "customers and their orders",
     "CREATE TABLE n6_orders (id INT PRIMARY KEY, customer_id INT, FOREIGN KEY (customer_id) REFERENCES n6_customers (id));\n" + PARENT, "fk_order", None,
     ["INSERT INTO n6_customers VALUES (1, 'Asha')", "INSERT INTO n6_orders VALUES (1, 1)", "INSERT INTO n6_orders VALUES (2, 9)",
      "SELECT COUNT(*) FROM n6_orders"],
     PARENT + "CREATE TABLE n6_orders (id INT PRIMARY KEY, customer_id INT, FOREIGN KEY (customer_id) REFERENCES n6_customers (id));"),
    # ---- a link to a column that is not a key
    ("fg_email", "orders keep the customer's email as the link (postgres)",
     "CREATE TABLE n6_customers (id INT PRIMARY KEY, email VARCHAR(80));\n"
     "CREATE TABLE n6_orders (id INT PRIMARY KEY, email VARCHAR(80) REFERENCES n6_customers (email));", "fk_target", "postgresql",
     ["INSERT INTO n6_customers VALUES (1, 'a@x.test')", "INSERT INTO n6_customers VALUES (2, 'a@x.test')", "INSERT INTO n6_orders VALUES (1, 'a@x.test')",
      "SELECT COUNT(*) FROM n6_customers"],
     "CREATE TABLE n6_customers (id INT PRIMARY KEY, email VARCHAR(80) UNIQUE);\n"
     "CREATE TABLE n6_orders (id INT PRIMARY KEY, email VARCHAR(80) REFERENCES n6_customers (email));"),
    ("fg_plain_my", "link orders to customers by email, mariadb",
     "CREATE TABLE n6_customers (id INT PRIMARY KEY, email VARCHAR(80));\n"
     "CREATE TABLE n6_orders (id INT PRIMARY KEY, email VARCHAR(80), FOREIGN KEY (email) REFERENCES n6_customers (email));", "fk_target", "mariadb",
     ["INSERT INTO n6_customers VALUES (1, 'a@x.test')", "INSERT INTO n6_orders VALUES (1, 'a@x.test')", "SELECT COUNT(*) FROM n6_orders"],
     "CREATE TABLE n6_customers (id INT PRIMARY KEY, email VARCHAR(80) UNIQUE);\n"
     "CREATE TABLE n6_orders (id INT PRIMARY KEY, email VARCHAR(80), FOREIGN KEY (email) REFERENCES n6_customers (email));"),
    # ---- engine limits on MySQL/MariaDB
    ("vl_len", "a name column (mariadb)", "CREATE TABLE n6_t (id INT PRIMARY KEY, name VARCHAR)", "varchar_length", "mariadb",
     ["INSERT INTO n6_t VALUES (1, 'Asha')", "SELECT name FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, name VARCHAR(255))"),
    ("rb_row", "two long text fields (mariadb)", "CREATE TABLE n6_t (id INT PRIMARY KEY, summary VARCHAR(10000), details VARCHAR(10000))",
     "row_too_big", "mariadb", ["INSERT INTO n6_t VALUES (1, 'a', 'b')", "SELECT summary, details FROM n6_t"],
     "CREATE TABLE n6_t (id INT PRIMARY KEY, summary TEXT, details VARCHAR(10000))"),
    ("rb_col", "one very long note (mysql)", "CREATE TABLE n6_t (id INT PRIMARY KEY, note VARCHAR(20000))", "row_too_big", "mysql",
     ["INSERT INTO n6_t VALUES (1, 'a')", "SELECT note FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, note TEXT)"),
    ("tk_text", "coupons named by their code (mariadb)", "CREATE TABLE n6_t (code TEXT PRIMARY KEY, pct INT)", "text_key", "mariadb",
     ["INSERT INTO n6_t VALUES ('DIWALI', 10)", "SELECT code FROM n6_t"], "CREATE TABLE n6_t (code VARCHAR(255) PRIMARY KEY, pct INT)"),
    ("ts_dob", "members and their birth date (mariadb)", "CREATE TABLE n6_t (id INT PRIMARY KEY, dob TIMESTAMP NULL)", "timestamp_range",
     "mariadb", ["INSERT INTO n6_t VALUES (1, '1965-05-01')", "SELECT COUNT(*) FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, dob DATE NULL)"),
    ("ts_expiry", "when each licence expires ('never' is 9999-12-31), mysql", "CREATE TABLE n6_t (id INT PRIMARY KEY, expiry_date TIMESTAMP NULL)", "timestamp_range",
     "mysql", ["INSERT INTO n6_t VALUES (1, '9999-12-31')", "SELECT COUNT(*) FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, expiry_date DATE NULL)"),
    # ---- widths
    ("ow_email", "users and their email", "CREATE TABLE n6_t (id INT PRIMARY KEY, email VARCHAR(5000))", "over_wide", None,
     ["INSERT INTO n6_t VALUES (1, " + "'" + "a" * 300 + "')", "SELECT COUNT(*) FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, email VARCHAR(254))"),
    # ---- reserved words
    ("rn_cols", "items with a description and a sort order",
     "CREATE TABLE n6_t (id INT PRIMARY KEY, desc VARCHAR(100), order INT)", "reserved_name", None,
     ["INSERT INTO n6_t VALUES (1, 'red', 2)", "SELECT * FROM n6_t"], "CREATE TABLE n6_t (id INT PRIMARY KEY, description VARCHAR(100), sort_order INT)"),
    ("rn_group_my", "a table for groups (mariadb)", "CREATE TABLE group (id INT PRIMARY KEY, name VARCHAR(50))", "reserved_name", "mariadb",
     ["INSERT INTO `group` VALUES (1, 'admins')", "SELECT name FROM `group`"], "CREATE TABLE `group` (id INT PRIMARY KEY, name VARCHAR(50))"),
    ("rn_user_pg", "a user table (postgresql)", "CREATE TABLE user (id INT PRIMARY KEY, name VARCHAR(50))", "reserved_name", "postgresql",
     ["INSERT INTO \"user\" VALUES (1, 'Asha')", "SELECT name FROM \"user\""], "CREATE TABLE \"user\" (id INT PRIMARY KEY, name VARCHAR(50))"),
    # ---- one fact in one place (advice: the design changes, so finding it is enough)
    ("lc_tags", "posts and their tag ids", "CREATE TABLE n6_t (id INT PRIMARY KEY, title VARCHAR(100), tag_ids VARCHAR(200))", "list_in_column",
     None, ["SELECT 1"], None),
    ("rg_phones", "a contact with up to three phones", "CREATE TABLE n6_t (id INT PRIMARY KEY, phone1 VARCHAR(20), phone2 VARCHAR(20), "
     "phone3 VARCHAR(20))", "repeating_group", None, ["SELECT 1"], None),
    ("pc_orders", "orders of customers", PARENT + "CREATE TABLE n6_orders (id INT PRIMARY KEY, customer_id INT REFERENCES n6_customers (id), "
     "customer_name VARCHAR(50), customer_email VARCHAR(80));", "parent_copy", None, ["SELECT 1"], None),
]

# what each engine does with the wrong design: 'refused' (the CREATE stops) or 'runs' (then the probe shows the damage)
LAB = {pid: {} for pid, *_ in P}
REFUSED = {   # (engine family: what it said)
    "dl_pg": {"postgres": "syntax error at or near UNSIGNED"}, "dl_pg_backticks": {"postgres": 'syntax error at or near "`"'},
    "dl_pg_enum": {"postgres": 'type "enum" does not exist'}, "dl_my": {"mysql": "1064 near GENERATED ALWAYS AS IDENTITY"},
    "dl_my_serial": {"mysql": "4161 Unknown data type BIGSERIAL"},
    "tp_two": {"mysql": "1068 Multiple primary key defined", "postgres": "42P16 multiple primary keys are not allowed"},
    "ft_bigint": {"mysql": "1005 errno 150"}, "ft_unsigned": {"mysql": "1005 errno 150"},
    "ft_text": {"mysql": "1005 errno 150", "postgres": "foreign key constraint cannot be implemented"},
    "fo_order": {"mysql": "1005 errno 150", "postgres": "42P01 relation does not exist"},
    "fg_email": {"postgres": "42830 there is no unique constraint matching given keys"}, "fg_plain_my": {"mysql": "1005 errno 150"},
    "vl_len": {"mysql": "1064"}, "rb_row": {"mysql": "1118 Row size too large"}, "rb_col": {"mysql": "1074 Column length too big"},
    "tk_text": {"mysql": "1170 BLOB/TEXT column used in key specification without a key length"},
    "rn_cols": {"mysql": "1064", "postgres": "42601"}, "rn_group_my": {"mysql": "1064"}, "rn_user_pg": {"postgres": "42601"},
}
for pid, said in REFUSED.items():
    LAB[pid] = said
# the others run and silently keep what the correct design refuses or changes (the probe shows it), e.g. mf_float: ten 0.10
# don't sum to 1 and 1234567.89 comes back 1234570 (MariaDB FLOAT) / 1234567.9 (PostgreSQL REAL); u8_table: MariaDB 1366 on
# the emoji; my_fk: the orphan order is kept; ts_dob: MariaDB 1292 on 1965; cn_phone: 1264 / integer out of range.

TABLES = ["n6_t", "n6_items", "n6_orders", "n6_customers", "n6_products", "n6_categories", "n6_rates", "n6_child", "n6_parent", "n6_log"]


def _fam(said):
    return {"postgresql": ["postgres"], "mariadb": ["mysql"], "mysql": ["mysql"]}.get(said, ["mysql", "postgres"])


def _drop(eng):
    eng.rollback()
    if eng.kind == "mysql":
        eng.run("SET FOREIGN_KEY_CHECKS = 0")
    for t in TABLES + (["`group`"] if eng.kind == "mysql" else ['"user"']):
        try:
            eng.run(f"DROP TABLE IF EXISTS {t}" + (" CASCADE" if eng.kind == "postgres" else ""))
        except Exception:  # noqa: BLE001
            eng.rollback()
    if eng.kind == "mysql":
        eng.run("SET FOREIGN_KEY_CHECKS = 1")


def _text(design, eng):
    return design.get(eng.kind) if isinstance(design, dict) else design


def outcome(eng, design, probe):
    """'refused' when the design doesn't run; else what each probe statement gives ('ok', 'error' or its rows)."""
    _drop(eng)
    try:
        try:
            for st in split_script(design):
                eng.run(st)
        except Exception:  # noqa: BLE001
            eng.rollback()
            return "refused"
        out = []
        for p in probe:
            try:
                r = eng.run(p)
                out.append(sorted(tuple(S.norm(x) for x in row) for row in r) if isinstance(r, (list, tuple)) else "ok")
            except Exception:  # noqa: BLE001
                eng.rollback()
                out.append("error")
        return out
    finally:
        _drop(eng)


@pytest.fixture(scope="module")
def engines():
    from tests.fuzz.db import MYSQL, PG, Engine

    out = {}
    for cfg in (MYSQL, PG):
        try:
            out[cfg["type"]] = Engine(cfg).open()
        except Exception:  # noqa: BLE001
            pass
    if len(out) < 2:
        pytest.skip("needs MariaDB and PostgreSQL running")
    return out


def test_the_bank_is_big_enough():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)


@pytest.mark.parametrize("pid,words,wrong,topic,said,probe,fix", P, ids=[p[0] for p in P])
def test_the_lab_still_holds(engines, pid, words, wrong, topic, said, probe, fix):
    """The wrong design is refused where the lab says, and elsewhere behaves differently from the correct one."""
    for fam in _fam(said):
        eng = engines[fam]
        got = outcome(eng, wrong, probe)
        if fam in LAB[pid]:
            assert got == "refused", (pid, fam, got)
            continue
        if fix is None:
            continue
        want = outcome(eng, _text(fix, eng), probe)
        assert want != "refused", (pid, fam, "the correct design must run")
        assert got != want, (pid, fam, "the mistake changes nothing here?", got)


@pytest.mark.parametrize("pid,words,wrong,topic,said,probe,fix", P, ids=[p[0] for p in P])
def test_dbaba_finds_and_fixes_the_design(engines, pid, words, wrong, topic, said, probe, fix):
    res = analyze_sql(wrong, engine=said)
    assert res.get("ok"), (pid, res.get("error"))
    found = [f for f in res["correct"]["findings"] if f["topic"] == topic]
    assert found, (pid, [(f["topic"], f["title_en"]) for f in res["correct"]["findings"]])
    assert all(f.get("basis") for f in found), (pid, "every finding names its fundamental")
    assert res.get("reading", {}).get("en", "").startswith("How DBaba read this design"), pid
    if fix is None:
        return
    offered = res.get("rewritten") or res.get("suggested")
    assert offered, (pid, "no corrected design", res["correct"].get("corrected"))
    for fam in _fam(said):
        eng = engines[fam]
        got, want = outcome(eng, offered, probe), outcome(eng, _text(fix, eng), probe)
        assert got != "refused" and got == want, (pid, fam, offered, got, want)


GOOD = [
    ("CREATE TABLE n6_customers (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL, email VARCHAR(254) UNIQUE, "
     "phone VARCHAR(20), created_at DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4", "mariadb"),
    ("CREATE TABLE n6_customers (id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name TEXT NOT NULL, email VARCHAR(254) UNIQUE, "
     "created_at TIMESTAMPTZ DEFAULT now());\nCREATE TABLE n6_orders (id BIGSERIAL PRIMARY KEY, customer_id INT NOT NULL "
     "REFERENCES n6_customers (id), amount NUMERIC(12,2) NOT NULL CHECK (amount >= 0), order_date DATE NOT NULL);\n"
     "CREATE INDEX ix_orders_customer ON n6_orders (customer_id);", "postgresql"),
    ("CREATE TABLE n6_products (id INT PRIMARY KEY, name VARCHAR(80), price DECIMAL(10,2), stock INT DEFAULT 0);\n"
     "CREATE TABLE n6_items (order_id INT, product_id INT, qty INT NOT NULL, price DECIMAL(10,2), PRIMARY KEY (order_id, product_id), "
     "FOREIGN KEY (product_id) REFERENCES n6_products (id));\nCREATE INDEX ix_items_product ON n6_items (product_id);", None),
    ("CREATE TABLE n6_rates (id INT PRIMARY KEY, interest_rate DOUBLE PRECISION, latitude DOUBLE PRECISION, score REAL)", None),
    ("CREATE TEMPORARY TABLE n6_tmp (a INT, b TEXT)", None),
    ("CREATE TABLE IF NOT EXISTS `n6_t` (`id` int(11) NOT NULL AUTO_INCREMENT, `order` int, `desc` text, PRIMARY KEY (`id`)) ENGINE=InnoDB", "mysql"),
    ("CREATE TABLE n6_t (id INT PRIMARY KEY, address_line1 VARCHAR(100), address_line2 VARCHAR(100), phone_type VARCHAR(10), "
     "date_format VARCHAR(20), status VARCHAR(20))", None),
    ("CREATE TABLE n6_parent (id INT PRIMARY KEY);\nCREATE TABLE n6_child (id INT PRIMARY KEY, parent_id INT);\n"
     "ALTER TABLE n6_child ADD CONSTRAINT fk_p FOREIGN KEY (parent_id) REFERENCES n6_parent (id);", None),
    ("CREATE TABLE n6_log (id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, msg TEXT, created_at TIMESTAMP NULL DEFAULT NULL)", "mariadb"),
]


@pytest.mark.parametrize("design,said", GOOD)
def test_good_designs_raise_no_alarm(design, said):
    res = analyze_sql(design, engine=said)
    assert res.get("ok"), design
    bad = [(f["topic"], f["title_en"]) for f in res["correct"]["findings"] if f["kind"] in ("error", "wrong")]
    assert not bad, (design, bad)


def test_good_designs_really_run(engines):
    for design, said in GOOD[:3] + GOOD[3:4]:
        for fam in _fam(said):
            assert outcome(engines[fam], design, ["SELECT 1"]) != "refused", (design, fam)
