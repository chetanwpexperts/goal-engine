"""Night 10B practice bank: the project's own schema decides. The practice shop (tests/practice/schema.py) is described the
way a project would describe it: once as a schema dump (plain SQL, columns NULL unless they say NOT NULL) and once as Laravel
migrations (NOT NULL unless ->nullable(), with later migrations that rename, add and index). Both must give the same facts.

Each problem is a requirement, the query someone wrote, and what the schema makes of it:
- 'dropped'  : a risk the schema rules out (a NOT NULL column in NOT IN, a DATE in BETWEEN, a join on the parent's key under
               DISTINCT, ...). DBaba must not report it, must list it as ruled out with the reason, and the query as written
               must return the requirement's rows (the truth) on MariaDB and PostgreSQL.
- 'verified' : a risk the schema proves possible (a nullable column ...). DBaba reports it as verified, and its suggested
               query returns the truth on both databases.
- 'error'    : a name the table doesn't have, or a type the engine refuses. Fixed to the right name / form, run on both.
- 'absent'   : something the schema shows is fine (an index that exists): nothing is said.
LAB is what each engine really does with tonight's facts (MariaDB 10.11 / PostgreSQL 16), re-run here.
"""

import pytest

from app.agents import sqlproject, sqlscan, sqlverify
from tests.practice import schema as S

DUMP = """-- practice shop, mysqldump --no-data style
CREATE TABLE `departments` (`id` int NOT NULL, `name` varchar(40) NOT NULL, `location` varchar(40) DEFAULT NULL, PRIMARY KEY (`id`));
CREATE TABLE `employees` (
  `id` int NOT NULL, `first_name` varchar(40) NOT NULL, `last_name` varchar(40) NOT NULL, `department_id` int DEFAULT NULL,
  `manager_id` int DEFAULT NULL, `salary` decimal(10,2) NOT NULL, `commission` decimal(10,2) DEFAULT NULL, `hired_on` date NOT NULL,
  `email` varchar(80) NOT NULL, PRIMARY KEY (`id`), UNIQUE KEY `uq_employees_email` (`email`),
  CONSTRAINT `fk_emp_dept` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`)
) ENGINE=InnoDB /*!40101 DEFAULT CHARSET=utf8mb4 */;
CREATE TABLE `categories` (`id` int NOT NULL, `name` varchar(40) NOT NULL, `parent_id` int DEFAULT NULL, PRIMARY KEY (`id`));
CREATE TABLE `suppliers` (`id` int NOT NULL, `name` varchar(40) NOT NULL, `country` varchar(40) NOT NULL, `email` varchar(80) DEFAULT NULL,
  PRIMARY KEY (`id`));
CREATE TABLE `products` (`id` int NOT NULL, `name` varchar(60) NOT NULL, `category_id` int DEFAULT NULL, `supplier_id` int DEFAULT NULL,
  `price` decimal(10,2) NOT NULL, `stock` int NOT NULL, `sku` varchar(20) NOT NULL, PRIMARY KEY (`id`), UNIQUE KEY `uq_sku` (`sku`));
CREATE TABLE `customers` (`id` int NOT NULL, `first_name` varchar(40) NOT NULL, `middle_name` varchar(40) DEFAULT NULL,
  `last_name` varchar(40) NOT NULL, `email` varchar(80) NOT NULL, `city` varchar(40) DEFAULT NULL, `phone` varchar(20) DEFAULT NULL,
  `created_at` date NOT NULL, PRIMARY KEY (`id`), KEY `ix_customers_city` (`city`));
CREATE TABLE `orders` (`id` int NOT NULL, `customer_id` int NOT NULL, `order_date` date NOT NULL, `status` varchar(20) NOT NULL,
  `amount` decimal(10,2) NOT NULL, `shipping` decimal(10,2) DEFAULT NULL, `discount` decimal(10,2) DEFAULT NULL, PRIMARY KEY (`id`),
  KEY `ix_orders_status_date` (`status`, `order_date`), CONSTRAINT `fk_orders_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`));
CREATE TABLE `order_items` (`id` int NOT NULL, `order_id` int NOT NULL, `product_id` int NOT NULL, `quantity` int NOT NULL,
  `unit_price` decimal(10,2) NOT NULL, `discount` decimal(10,2) DEFAULT NULL, PRIMARY KEY (`id`));
CREATE TABLE `payments` (`id` int NOT NULL, `order_id` int NOT NULL, `paid_at` date NOT NULL, `amount` decimal(10,2) NOT NULL,
  `method` varchar(20) DEFAULT NULL, PRIMARY KEY (`id`));
CREATE INDEX ix_payments_order ON payments (order_id);
CREATE TABLE `returns` (`id` int NOT NULL, `order_item_id` int NOT NULL, `returned_on` date NOT NULL, `reason` varchar(40) DEFAULT NULL,
  `quantity` int NOT NULL, PRIMARY KEY (`id`));
"""

# the same shop as Laravel migrations (NOT NULL unless ->nullable()), written over several files and changed later
LARAVEL = {
    "2024_01_01_000000_create_shop_tables.php": """<?php
use Illuminate\\Database\\Migrations\\Migration;
use Illuminate\\Database\\Schema\\Blueprint;
use Illuminate\\Support\\Facades\\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::create('departments', function (Blueprint $table) {
            $table->integer('id')->primary();
            $table->string('name', 40);
            $table->string('location', 40)->nullable();
        });
        Schema::create('employees', function (Blueprint $table) {
            $table->integer('id')->primary();
            $table->string('first_name', 40);
            $table->string('last_name', 40);
            $table->integer('department_id')->nullable();
            $table->integer('manager_id')->nullable();
            $table->decimal('salary', 10, 2);
            $table->decimal('commission', 10, 2)->nullable();
            $table->date('hired_on');
            $table->string('mail', 80)->unique();
            $table->foreign('department_id')->references('id')->on('departments');
        });
        Schema::create('categories', function (Blueprint $table) {
            $table->integer('id')->primary();
            $table->string('name', 40);
            $table->integer('parent_id')->nullable();
        });
        Schema::create('suppliers', function (Blueprint $table) {
            $table->integer('id')->primary();
            $table->string('name', 40);
            $table->string('country', 40);
            $table->string('email', 80)->nullable();
        });
        Schema::create('products', function (Blueprint $table) {
            $table->integer('id')->primary();
            $table->string('name', 60);
            $table->integer('category_id')->nullable();
            $table->integer('supplier_id')->nullable();
            $table->decimal('price', 10, 2);
            $table->integer('stock');
            $table->string('sku', 20)->unique();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('products');
    }
};
""",
    "2024_01_02_000000_create_sales_tables.php": """<?php
return new class extends Migration {
    public function up(): void
    {
        Schema::create('customers', function (Blueprint $table) {
            $table->integer('id')->primary();
            $table->string('first_name', 40);
            $table->string('middle_name', 40)->nullable();
            $table->string('last_name', 40);
            $table->string('email', 80);
            $table->string('city', 40)->nullable()->index();
            $table->string('phone', 20)->nullable();
            $table->date('created_at');
        });
        Schema::create('orders', function (Blueprint $table) {
            $table->integer('id')->primary();
            $table->integer('customer_id');
            $table->date('order_date');
            $table->string('status', 20);
            $table->decimal('amount', 10, 2);
            $table->decimal('shipping', 10, 2)->nullable();
            $table->decimal('discount', 10, 2)->nullable();
            $table->index(['status', 'order_date']);
            $table->foreign('customer_id')->references('id')->on('customers');
        });
        Schema::create('order_items', function (Blueprint $table) {
            $table->integer('id')->primary();
            $table->integer('order_id');
            $table->integer('product_id');
            $table->integer('quantity');
            $table->decimal('unit_price', 10, 2);
            $table->decimal('discount', 10, 2)->nullable();
        });
        Schema::create('payments', function (Blueprint $table) {
            $table->integer('id')->primary();
            $table->integer('order_id');
            $table->date('paid_at');
            $table->decimal('amount', 10, 2);
            $table->string('method', 20)->nullable();
        });
        Schema::create('returns', function (Blueprint $table) {
            $table->integer('id')->primary();
            $table->integer('order_item_id');
            $table->date('returned_on');
            $table->string('reason', 40)->nullable();
            $table->integer('quantity');
        });
    }
};
""",
    "2024_02_10_000000_tidy_employees_and_payments.php": """<?php
return new class extends Migration {
    public function up(): void
    {
        Schema::table('employees', function (Blueprint $table) {
            $table->renameColumn('mail', 'email');
        });
        Schema::table('payments', function (Blueprint $table) {
            $table->index('order_id');
            $table->string('note', 40)->nullable();
        });
        Schema::table('payments', function (Blueprint $table) {
            $table->dropColumn('note');
        });
    }

    public function down(): void
    {
        Schema::table('payments', function (Blueprint $table) {
            $table->dropIndex(['order_id']);
        });
    }
};
""",
}

# (id, requirement, query, what the schema makes of it, topic, the query whose rows are right (None: no rows to compare), engine)
P = [
    # ---- risks the schema rules out: not said, listed as ruled out, and the query as written is right
    ("no_orders_not_in", "customers who never ordered", "SELECT id FROM customers WHERE id NOT IN (SELECT customer_id FROM orders) ORDER BY id",
     "dropped", "not_in", "SELECT id FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id) ORDER BY id", None),
    ("unsold_products_not_in", "products never sold", "SELECT id FROM products WHERE id NOT IN (SELECT product_id FROM order_items) ORDER BY id",
     "dropped", "not_in", "SELECT id FROM products p WHERE NOT EXISTS (SELECT 1 FROM order_items i WHERE i.product_id = p.id) ORDER BY id", None),
    ("unpaid_orders_not_in", "orders with no payment", "SELECT id FROM orders WHERE id NOT IN (SELECT order_id FROM payments) ORDER BY id",
     "dropped", "not_in", "SELECT id FROM orders o WHERE NOT EXISTS (SELECT 1 FROM payments p WHERE p.order_id = o.id) ORDER BY id", "postgresql"),
    ("items_not_returned", "order items never returned",
     "SELECT id FROM order_items WHERE id NOT IN (SELECT order_item_id FROM returns) ORDER BY id",
     "dropped", "not_in", "SELECT id FROM order_items i WHERE NOT EXISTS (SELECT 1 FROM returns r WHERE r.order_item_id = i.id) ORDER BY id", "mariadb"),
    ("june_orders_between", "orders placed 1 to 20 June 2024, the 20th included",
     "SELECT id FROM orders WHERE order_date BETWEEN '2024-06-01' AND '2024-06-20' ORDER BY id",
     "dropped", "date_range", "SELECT id FROM orders WHERE order_date >= '2024-06-01' AND order_date < '2024-06-21' ORDER BY id", None),
    ("june_payments_between", "payments made in June 2024", "SELECT id FROM payments WHERE paid_at BETWEEN '2024-06-01' AND '2024-06-30' ORDER BY id",
     "dropped", "date_range", "SELECT id FROM payments WHERE paid_at >= '2024-06-01' AND paid_at < '2024-07-01' ORDER BY id", None),
    ("q1_customers_between", "customers who joined in the first quarter of 2024",
     "SELECT id FROM customers WHERE created_at BETWEEN '2024-01-01' AND '2024-03-31' ORDER BY id",
     "dropped", "date_range", "SELECT id FROM customers WHERE created_at >= '2024-01-01' AND created_at < '2024-04-01' ORDER BY id", "postgresql"),
    ("hired_2021_between", "employees hired in 2021", "SELECT id FROM employees WHERE hired_on BETWEEN '2021-01-01' AND '2021-12-31' ORDER BY id",
     "dropped", "date_range", "SELECT id FROM employees WHERE hired_on >= '2021-01-01' AND hired_on < '2022-01-01' ORDER BY id", None),
    ("june_returns_between", "returns made in June 2024", "SELECT id FROM returns WHERE returned_on BETWEEN '2024-06-01' AND '2024-06-30' ORDER BY id",
     "dropped", "date_range", "SELECT id FROM returns WHERE returned_on >= '2024-06-01' AND returned_on < '2024-07-01' ORDER BY id", "mariadb"),
    ("pune_orders_distinct", "orders of customers from Pune",
     "SELECT DISTINCT o.id, o.amount FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune' ORDER BY o.id",
     "dropped", "distinct_join", "SELECT o.id, o.amount FROM orders o WHERE o.customer_id IN (SELECT id FROM customers WHERE city = 'Pune') ORDER BY o.id", None),
    ("phone_products_distinct", "products in the Phones category",
     "SELECT DISTINCT p.id, p.name FROM products p JOIN categories k ON k.id = p.category_id WHERE k.name = 'Phones' ORDER BY p.id",
     "dropped", "distinct_join", "SELECT id, name FROM products WHERE category_id = 2 ORDER BY id", None),
    ("staff_in_pune_distinct", "employees whose department is in Pune",
     "SELECT DISTINCT e.id, e.first_name FROM employees e JOIN departments d ON d.id = e.department_id WHERE d.location = 'Pune' ORDER BY e.id",
     "dropped", "distinct_join", "SELECT id, first_name FROM employees WHERE department_id IN (1, 3) ORDER BY id", "postgresql"),
    ("no_orders_anti_join", "customers who never ordered",
     "SELECT c.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.status IS NULL ORDER BY c.id",
     "dropped", "anti_join", "SELECT id FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id) ORDER BY id", None),
    ("unsold_anti_join", "products never sold",
     "SELECT p.id FROM products p LEFT JOIN order_items i ON i.product_id = p.id WHERE i.quantity IS NULL ORDER BY p.id",
     "dropped", "anti_join", "SELECT id FROM products p WHERE NOT EXISTS (SELECT 1 FROM order_items i WHERE i.product_id = p.id) ORDER BY id", None),
    ("supplier_staff_scalar", "each supplier with the employee who has the same email (if any)",
     "SELECT s.id, (SELECT e.first_name FROM employees e WHERE e.email = s.email) AS staff FROM suppliers s ORDER BY s.id",
     "dropped", "scalar_rows", "SELECT s.id, NULL AS staff FROM suppliers s ORDER BY s.id", None),
    # ---- risks the schema proves possible: reported as verified, and the suggestion is what was meant
    ("unused_categories_not_in", "categories no product is filed under",
     "SELECT id FROM categories WHERE id NOT IN (SELECT category_id FROM products) ORDER BY id",
     "verified", "not_in", "SELECT id FROM categories k WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.category_id = k.id) ORDER BY id", None),
    ("empty_departments_not_in", "departments with no employees",
     "SELECT id FROM departments WHERE id NOT IN (SELECT department_id FROM employees) ORDER BY id",
     "verified", "not_in", "SELECT id FROM departments d WHERE NOT EXISTS (SELECT 1 FROM employees e WHERE e.department_id = d.id) ORDER BY id", None),
    ("idle_suppliers_not_in", "suppliers none of whose products exist",
     "SELECT id FROM suppliers WHERE id NOT IN (SELECT supplier_id FROM products) ORDER BY id",
     "verified", "not_in", "SELECT id FROM suppliers s WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.supplier_id = s.id) ORDER BY id", "mariadb"),
    ("order_total_shipping", "each order's amount plus shipping (no shipping = 0)",
     "SELECT o.id, o.amount + o.shipping AS total FROM orders o ORDER BY o.id",
     "verified", "null_math", "SELECT id, amount + COALESCE(shipping, 0) AS total FROM orders ORDER BY id", None),
    ("line_net", "each order line's price after its discount (no discount = 0)",
     "SELECT i.id, i.unit_price * i.quantity - i.discount AS net FROM order_items i ORDER BY i.id",
     "verified", "null_math", "SELECT id, unit_price * quantity - COALESCE(discount, 0) AS net FROM order_items ORDER BY id", None),
    ("pay_with_commission", "each employee's salary plus commission (none = 0)",
     "SELECT e.id, e.salary + e.commission AS pay FROM employees e ORDER BY e.id",
     "verified", "null_math", "SELECT id, salary + COALESCE(commission, 0) AS pay FROM employees ORDER BY id", "postgresql"),
    ("full_names_concat", "every customer's full name, middle name when there is one",
     "SELECT c.id, CONCAT(c.first_name, ' ', c.middle_name) AS name FROM customers c ORDER BY c.id",
     "verified", "concat", "SELECT id, CONCAT(first_name, ' ', COALESCE(middle_name, '')) AS name FROM customers ORDER BY id", "mariadb"),
    ("buyers_distinct", "customers who placed an order",
     "SELECT DISTINCT c.id, c.first_name FROM customers c JOIN orders o ON o.customer_id = c.id ORDER BY c.id",
     "verified", "distinct_join", "SELECT id, first_name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id) ORDER BY id", None),
    ("unpaid_anti_join", "orders with no payment",
     "SELECT o.id FROM orders o LEFT JOIN payments p ON p.order_id = o.id WHERE p.method IS NULL ORDER BY o.id",
     "verified", "anti_join", "SELECT id FROM orders o WHERE NOT EXISTS (SELECT 1 FROM payments p WHERE p.order_id = o.id) ORDER BY id", None),
    ("top3_price", "the three most expensive products, the same ones every time",
     "SELECT id, name FROM products ORDER BY price DESC LIMIT 3",
     "verified", "stable_order", "SELECT id, name FROM products ORDER BY price DESC, id LIMIT 3", None),
    ("orders_2024_year", "orders placed in 2024", "SELECT id FROM orders WHERE YEAR(order_date) = 2024 ORDER BY id",
     "verified", "sargable", "SELECT id FROM orders WHERE order_date >= '2024-01-01' AND order_date < '2025-01-01' ORDER BY id", "mariadb"),
    ("customers_2024_year", "customers who joined in 2024", "SELECT id FROM customers WHERE YEAR(created_at) = 2024 ORDER BY id",
     "verified", "sargable", "SELECT id FROM customers WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01' ORDER BY id", "mariadb"),
    # ---- names the table doesn't have (fixed to the close real one) and types the engine refuses
    ("typo_email", "customers' emails", "SELECT id, emial FROM customers ORDER BY id", "error", "schema_column",
     "SELECT id, email FROM customers ORDER BY id", None),
    ("typo_amount", "orders over 10,000", "SELECT o.id FROM orders o WHERE o.ammount > 10000 ORDER BY o.id", "error", "schema_column",
     "SELECT id FROM orders WHERE amount > 10000 ORDER BY id", None),
    ("typo_last_name", "staff names", "SELECT first_name, lastname FROM employees ORDER BY id", "error", "schema_column",
     "SELECT first_name, last_name FROM employees ORDER BY id", None),
    ("typo_price", "product prices", "SELECT p.name, p.prise FROM products p ORDER BY p.id", "error", "schema_column",
     "SELECT name, price FROM products ORDER BY id", "postgresql"),
    ("typo_method", "UPI payments", "SELECT id FROM payments WHERE methd = 'upi' ORDER BY id", "error", "schema_column",
     "SELECT id FROM payments WHERE method = 'upi' ORDER BY id", None),
    ("typo_group", "orders per customer", "SELECT cutomer_id, COUNT(*) FROM orders GROUP BY cutomer_id ORDER BY cutomer_id", "error",
     "schema_column", "SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id ORDER BY customer_id", None),
    ("typo_city", "customers' cities", "SELECT c.id, c.ctiy FROM customers c ORDER BY c.id", "error", "schema_column",
     "SELECT id, city FROM customers ORDER BY id", "mariadb"),
    ("phone_as_number_pg", "the customer with phone 98100", "SELECT id FROM customers WHERE phone = 98100 ORDER BY id", "error",
     "implicit_cast", "SELECT id FROM customers WHERE phone = '98100' ORDER BY id", "postgresql"),
    ("join_number_text_pg", "orders joined to customers by mistake on the phone", "SELECT o.id FROM orders o JOIN customers c ON o.customer_id = c.phone",
     "error", "join_types", None, "postgresql"),
    # ---- the schema tells what exists and what is missing
    ("no_index_amount", "big orders", "SELECT id FROM orders WHERE amount > 10000 ORDER BY id", "verified", "index_missing", None, "postgresql"),
    ("city_index_exists", "customers in Pune", "SELECT id FROM customers WHERE city = 'Pune' ORDER BY id", "absent", "index_missing", None, None),
    ("fk_index_payments", "payments of order 3", "SELECT id FROM payments WHERE order_id = 3 ORDER BY id", "absent", "index_missing", None, None),
    ("date_alone_prefix", "orders from July 2024 on", "SELECT id FROM orders WHERE order_date >= '2024-07-01' ORDER BY id", "verified",
     "index_prefix", None, None),
]

# what each engine really does with tonight's facts (re-run below): MariaDB, then PostgreSQL
LAB = [
    ("INT = VARCHAR in a join ('1', '01', '1abc' stored)",
     ["CREATE TEMPORARY TABLE lab_a (id INT PRIMARY KEY)", "CREATE TEMPORARY TABLE lab_b (id INT PRIMARY KEY, ref VARCHAR(10))",
      "INSERT INTO lab_a VALUES (1)", "INSERT INTO lab_b VALUES (1, '1'), (2, '01'), (3, '1abc')",
      "SELECT b.id FROM lab_a a JOIN lab_b b ON a.id = b.ref ORDER BY b.id"], "1,2,3", "error 42883"),
    ("ADD COLUMN ... NOT NULL without DEFAULT on a table with rows",
     ["CREATE TEMPORARY TABLE lab_a (id INT PRIMARY KEY)", "INSERT INTO lab_a VALUES (1)", "ALTER TABLE lab_a ADD COLUMN qty INT NOT NULL",
      "SELECT qty FROM lab_a"], "0", "error 23502"),
    ("an unknown column", ["SELECT nope FROM customers"], "error 1054", "error 42703"),
    ("BETWEEN up to a day on a DATE column keeps that whole day",
     ["SELECT id FROM orders WHERE order_date BETWEEN '2024-06-01' AND '2024-06-20' ORDER BY id"], "1,2,3", "1,2,3"),
    ("NOT IN over a column with a NULL", ["SELECT id FROM categories WHERE id NOT IN (SELECT category_id FROM products)"], "no rows", "no rows"),
    ("NOT IN over a column with no NULL", ["SELECT id FROM customers WHERE id NOT IN (SELECT customer_id FROM orders) ORDER BY id"],
     "4,5,7", "4,5,7"),
    ("a text column compared with a number", ["SELECT id FROM customers WHERE phone = 98100"], "1", "error 42883"),
]


def _engines():
    e = S.engines()
    if len(e) < 2:
        pytest.skip("needs MariaDB and PostgreSQL")
    return e


@pytest.fixture(scope="module")
def engines():
    return _engines()


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("shop")
    (root / "database" / "migrations").mkdir(parents=True)
    for name, text in LARAVEL.items():
        (root / "database" / "migrations" / name).write_text(text)
    return sqlproject.read_project(str(root))


def _rows(eng, sql):
    S.build(eng)
    return S.rows(eng, sql, ordered=True)


FACTS = lambda sch: {(t["name"], c["name"], c["not_null"]) for t in sch["tables"].values() for c in t["cols"].values()}  # noqa: E731


def test_the_bank_has_enough_problems():
    assert len(P) >= 30 and len({p[0] for p in P}) == len(P)


def test_dump_and_migrations_say_the_same(project):
    dump = sqlproject.from_text(DUMP)
    assert set(dump["tables"]) == set(project["tables"]) == set(S.TABLES)
    assert FACTS(dump) == FACTS(project)
    for sch in (dump, project):
        t = sch["tables"]
        assert t["employees"]["cols"]["email"]["not_null"] and sqlproject.is_unique(t["employees"], ["email"])
        assert not sqlproject.is_unique(t["customers"], ["email"])                     # duplicates exist in the shop
        assert [ix["cols"] for ix in t["orders"]["indexes"] if ix["kind"] != "primary"] == [["status", "order_date"]]
        assert sqlproject.indexes_leading(t["payments"], "order_id")
        assert "note" not in t["payments"]["cols"] and sqlproject.family(t["orders"]["cols"]["order_date"]["type"]) == "date"
    # the data the practice tables hold agrees with every NOT NULL the project states
    for tname, (cols, rows) in S.TABLES.items():
        for k, col in enumerate(cols):
            c = dump["tables"][tname]["cols"][col.split()[0]]
            if c["not_null"]:
                assert all(r[k] is not None for r in rows), (tname, c["name"])


@pytest.mark.parametrize("pid,words,sql,what,topic,truth,engine", P, ids=[p[0] for p in P])
def test_dbaba_with_the_schema(engines, project, pid, words, sql, what, topic, truth, engine):
    for sch in (sqlproject.from_text(DUMP), project):
        res = sqlverify.check(sql, sch, intent=words, engine=engine)
        mine = [f for f in res["findings"] if f["topic"] == topic]
        others = [f["title_en"] for f in res["findings"] if f["kind"] in ("error", "wrong") and f["topic"] != topic]
        assert not others, (pid, others)
        if what == "dropped":
            assert not mine and topic in {d["topic"] for d in res["dropped"]}, (pid, [f["topic"] for f in res["findings"]], res["dropped"])
            assert all(d["why_en"] and d["why_hi"] for d in res["dropped"])
            run = sql
        elif what == "absent":
            assert not mine, (pid, [f["title_en"] for f in mine])
            continue
        else:
            assert mine and mine[0].get("schema") == "verified", (pid, [(f["topic"], f.get("schema")) for f in res["findings"]])
            if what == "error":
                assert mine[0]["kind"] == "error", (pid, mine[0]["kind"])
            if truth is None:
                continue
            run = res["corrected"]
            assert run, (pid, "no corrected query")
        if truth is None:
            continue
        names = {"mariadb": ["mysql"], "postgresql": ["postgres"]}.get(engine, ["mysql", "postgres"])
        for name in names:
            assert _rows(engines[name], run) == _rows(engines[name], truth), (pid, name, run)


def test_without_the_schema_nothing_changes():
    """The same queries with no schema: what depends on it stays a 'check' (or isn't said), and nothing is dropped."""
    for pid, _w, sql, what, topic, _t, engine in P:
        if what == "dropped" and topic in ("not_in", "date_range", "distinct_join"):
            res = sqlverify.check(sql, None, engine=engine)
            assert topic in {f["topic"] for f in res["findings"]} and not res.get("dropped"), pid


def test_the_lab_table(engines):
    for what, stmts, want_my, want_pg in LAB:
        for name, want in (("mysql", want_my), ("postgres", want_pg)):
            eng = engines[name]
            S.build(eng)
            got = None
            try:
                rows = None
                for stmt in stmts:
                    out = eng.run(stmt)
                    if isinstance(out, (list, tuple)):
                        rows = out
                got = ",".join(S.norm(r[0]) for r in rows) if rows else "no rows"
            except Exception as exc:  # noqa: BLE001
                eng.rollback()
                got = f"error {(exc.args[0] if exc.args else '') if name == 'mysql' else getattr(exc, 'sqlstate', '')}"
            finally:
                for t in ("lab_a", "lab_b"):
                    try:
                        eng.run(f"DROP {'TEMPORARY ' if name == 'mysql' else ''}TABLE IF EXISTS {t}")
                    except Exception:  # noqa: BLE001
                        eng.rollback()
            assert got == want, (what, name, got, want)


def test_reading_sql_migrations(tmp_path):
    """Plain SQL migrations by version (V10 after V9), only their 'up' part, undo files skipped; ALTERs rename, add, drop
    and index; a dump is read before the migrations."""
    m = tmp_path / "db" / "migration"
    m.mkdir(parents=True)
    (tmp_path / "schema.sql").write_text("CREATE TABLE t_base (id INT PRIMARY KEY, code VARCHAR(10));\n")
    (m / "V1__init.sql").write_text("CREATE TABLE shop_order (id SERIAL PRIMARY KEY, cust INT, placed TIMESTAMP NOT NULL);\n")
    (m / "V9__rename.sql").write_text("ALTER TABLE shop_order RENAME COLUMN cust TO customer_id;\n")
    (m / "V10__more.sql").write_text("ALTER TABLE shop_order ALTER COLUMN customer_id SET NOT NULL, ADD COLUMN note TEXT;\n"
                                     "CREATE INDEX ix_order_cust ON shop_order USING btree (customer_id);\n"
                                     "ALTER TABLE t_base ADD CONSTRAINT uq_code UNIQUE (code);\n")
    (m / "U10__more.sql").write_text("ALTER TABLE shop_order DROP COLUMN note;\n")
    (m / "V11__dbmate.sql").write_text("-- migrate:up\nALTER TABLE shop_order DROP COLUMN note;\n-- migrate:down\n"
                                       "ALTER TABLE shop_order ADD COLUMN note TEXT;\n")
    sch = sqlproject.read_project(str(tmp_path))
    o = sch["tables"]["shop_order"]
    assert o["order"] == ["id", "customer_id", "placed"] and o["cols"]["customer_id"]["not_null"]
    assert sqlproject.family(o["cols"]["placed"]["type"]) == "time" and o["cols"]["id"]["auto"]
    assert sqlproject.indexes_leading(o, "customer_id")[0]["name"] == "ix_order_cust"
    assert sqlproject.is_unique(sch["tables"]["t_base"], ["code"])
    assert sch["files"][0] == "schema.sql" and [f.split("/")[-1] for f in sch["files"][1:]] == \
        ["V1__init.sql", "V9__rename.sql", "V10__more.sql", "V11__dbmate.sql"]


def test_laravel_details(tmp_path):
    """id(), foreignId()->constrained(), timestamps() / softDeletes() nullable, morphs(), ->change(), a schema dump that already
    holds some migrations, and code DBaba can't read for sure (marked partial: nothing is decided from that table)."""
    (tmp_path / "database" / "schema").mkdir(parents=True)
    (tmp_path / "database" / "migrations").mkdir(parents=True)
    (tmp_path / "database" / "schema" / "mysql-schema.sql").write_text(
        "CREATE TABLE `users` (`id` bigint unsigned NOT NULL AUTO_INCREMENT, `name` varchar(255) NOT NULL, PRIMARY KEY (`id`));\n"
        "INSERT INTO `migrations` VALUES (1,'2024_01_01_000000_create_users_table',1);\n")
    (tmp_path / "database" / "migrations" / "2024_01_01_000000_create_users_table.php").write_text(
        "<?php\nreturn new class extends Migration { public function up(): void { Schema::create('users', function (Blueprint $t) {"
        " $t->id(); $t->string('full_name'); }); } };\n")
    (tmp_path / "database" / "migrations" / "2024_03_01_000000_create_posts.php").write_text("""<?php
return new class extends Migration {
    public function up(): void
    {
        Schema::create('posts', function (Blueprint $table) {
            $table->id();
            $table->foreignId('user_id')->constrained()->cascadeOnDelete();
            $table->foreignIdFor(Category::class)->nullable();
            $table->string('title');
            $table->morphs('taggable');
            $table->timestamps();
            $table->softDeletes();
        });
        Schema::create('weird', function (Blueprint $table) {
            $table->id();
            if ($big) { $table->text('body'); }
        });
    }
};
""")
    (tmp_path / "database" / "migrations" / "2024_04_01_000000_change_posts.php").write_text(
        "<?php\nreturn new class extends Migration { public function up(): void { Schema::table('posts', function (Blueprint $table) {"
        " $table->string('title', 120)->nullable()->change(); }); } };\n")
    sch = sqlproject.read_project(str(tmp_path))
    u, p = sch["tables"]["users"], sch["tables"]["posts"]
    assert list(u["cols"]) == ["id", "name"]                                     # the dump's users; its migration isn't replayed
    assert p["cols"]["user_id"]["not_null"] and p["fks"][0]["parent"] == "users" and p["cols"]["category_id"]["not_null"] is False
    assert not p["cols"]["created_at"]["not_null"] and not p["cols"]["deleted_at"]["not_null"]
    assert p["cols"]["title"]["type"] == "varchar(120)" and not p["cols"]["title"]["not_null"]
    assert sqlproject.indexes_leading(p, "taggable_type")
    assert sch["tables"]["weird"]["partial"] and any("weird" in x["why"] for x in sch["unread"])
    res = sqlverify.check("SELECT id FROM weird WHERE id NOT IN (SELECT body FROM weird)", sch)
    assert not res["dropped"] and not any(f["topic"] == "schema_column" for f in res["findings"])


def test_project_scan_uses_the_schema(tmp_path, capsys):
    """dbaba check: queries in the code are checked against the schema the project's files create; 'dbaba schema' prints it."""
    (tmp_path / "composer.json").write_text('{"require": {"ext-pdo_mysql": "*"}}')
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "schema.sql").write_text(DUMP)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "report.php").write_text(
        "<?php\n$a = $pdo->query(\"SELECT id FROM customers WHERE id NOT IN (SELECT customer_id FROM orders)\")->fetchAll();\n"
        "$b = $pdo->query(\"SELECT id, emial FROM customers WHERE id = 3\")->fetchAll();\n"
        "$c = $pdo->query(\"SELECT o.id, o.amount + o.shipping AS total FROM orders o\")->fetchAll();\n")
    before = {p: p.read_text() for p in tmp_path.rglob("*") if p.is_file()}
    rep = sqlscan.scan(str(tmp_path))
    assert rep["schema"]["tables"] == 10
    by_line = {f["line"]: f for f in rep["with_findings"]}
    assert set(by_line) == {3, 4}
    assert by_line[3]["findings"][0]["topic"] == "schema_column" and "email" in by_line[3]["corrected"]
    assert by_line[4]["findings"][0]["schema"] == "verified"
    assert rep["ruled_out"][0]["line"] == 2 and rep["ruled_out"][0]["dropped"][0]["topic"] == "not_in"
    assert sqlscan.main(["check", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "Schema: 10 tables from 1 file" in out and "Ruled out by the project's schema" in out and "verified with the schema" in out
    assert sqlscan.main(["schema", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "customers  (db/schema.sql:" in out and "UNIQUE INDEX uq_sku (sku)" in out
    assert before == {p: p.read_text() for p in tmp_path.rglob("*") if p.is_file()}


def test_no_false_alarms_with_a_real_schema():
    """Random valid queries over random schemas (tests/fuzz/db.py), checked with their own CREATE TABLE script: nothing new is
    called an error or wrong."""
    import random

    from tests.fuzz import db as D

    for seed in range(40):
        g = D.make_schema(seed)
        kind = "mysql" if seed % 2 else "postgres"
        sch = sqlproject.from_text(";\n".join(x for t in g["tables"].values() for x in D._ddl(t, kind)) + ";")
        assert len(sch["tables"]) == len(g["tables"])
        rnd = random.Random(seed)
        for _ in range(4):
            q, _ = D.make_query(g, rnd)
            res = sqlverify.check(q, sch, engine="mariadb" if kind == "mysql" else "postgresql")
            assert not [f for f in res["findings"] if f["topic"] in ("schema_column", "join_types") or
                        f["kind"] in ("error", "wrong") and f.get("schema")], (seed, q)
