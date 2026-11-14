"""The practice schema (a small shop with staff): customers, orders, order_items, products, categories, payments, employees,
departments, suppliers, returns. Made-up names and values, with the traps real data has: NULLs (middle names, phones,
commission, discounts), duplicate emails, ties, names with '_' and '%' in them.

Built as TEMPORARY tables on each connection, so it never touches the test databases' own tables (a temporary table hides
a permanent one of the same name for that connection only), on MariaDB/MySQL and PostgreSQL alike.
"""

from tests.fuzz.db import MYSQL, PG, Engine

TABLES = {
    "departments": (["id INT", "name VARCHAR(40)", "location VARCHAR(40)"], [
        (1, "Sales", "Pune"), (2, "Support", "Delhi"), (3, "Finance", "Pune"), (4, "Research", None)]),
    "employees": (["id INT", "first_name VARCHAR(40)", "last_name VARCHAR(40)", "department_id INT", "manager_id INT",
                   "salary DECIMAL(10,2)", "commission DECIMAL(10,2)", "hired_on DATE", "email VARCHAR(80)"], [
        (1, "Asha", "Rao", 1, None, 90000, None, "2019-04-01", "asha@corp.test"),
        (2, "Vikram", "Mehta", 1, 1, 60000, 5000, "2020-06-15", "vikram@corp.test"),
        (3, "Neha", "Kapoor", 1, 1, 60000, 2500, "2021-01-10", "neha@corp.test"),
        (4, "Rohan", "Das", 2, 1, 45000, None, "2021-03-03", "rohan@corp.test"),
        (5, "Isha", "Singh", 2, 4, 42000, 0, "2022-07-19", "isha@corp.test"),
        (6, "Kabir", "Jain", 3, 1, 75000, None, "2018-11-30", "kabir@corp.test"),
        (7, "Meera", "Nair", None, 1, 50000, 1000, "2023-02-01", "meera@corp.test")]),
    "categories": (["id INT", "name VARCHAR(40)", "parent_id INT"], [
        (1, "Electronics", None), (2, "Phones", 1), (3, "Laptops", 1), (4, "Books", None), (5, "Toys", None)]),
    "suppliers": (["id INT", "name VARCHAR(40)", "country VARCHAR(40)", "email VARCHAR(80)"], [
        (1, "Alpha Traders", "India", "sales@alpha.test"), (2, "Beta Goods", "UAE", None), (3, "Gamma Supply", "India", "hi@gamma.test")]),
    "products": (["id INT", "name VARCHAR(60)", "category_id INT", "supplier_id INT", "price DECIMAL(10,2)", "stock INT", "sku VARCHAR(20)"], [
        (1, "Phone X", 2, 1, 25000, 12, "PH_100"), (2, "Phone Lite", 2, 1, 12000, 0, "PH1000"), (3, "Laptop Pro", 3, 2, 80000, 5, "LP_200"),
        (4, "SQL Basics", 4, 3, 500, 40, "BK_001"), (5, "100% Cotton Bear", 5, None, 800, 15, "TY_010"),
        (6, "Laptop Air", 3, 2, 65000, 3, "LP2000"), (7, "Old Pager", None, None, 300, 0, "XX_999")]),
    "customers": (["id INT", "first_name VARCHAR(40)", "middle_name VARCHAR(40)", "last_name VARCHAR(40)", "email VARCHAR(80)",
                   "city VARCHAR(40)", "phone VARCHAR(20)", "created_at DATE"], [
        (1, "Aarav", None, "Shah", "aarav@mail.test", "Pune", "98100", "2024-01-05"),
        (2, "Diya", "K", "Patel", "diya@mail.test", "Delhi", None, "2024-02-11"),
        (3, "Kabir", None, "Khan", "kabir@mail.test", "Mumbai", "98300", "2024-02-20"),
        (4, "Riya", None, "Sen", "riya@mail.test", None, "98400", "2024-03-01"),
        (5, "Aarav", None, "Shah", "AARAV@mail.test", "Pune", None, "2024-03-15"),
        (6, "Tara", "M", "Iyer", "tara@mail.test", "Chennai", "98600", "2024-04-02"),
        (7, "Dev", None, "Joshi", "diya@mail.test", "Delhi", None, "2024-04-20"),
        (8, "Sara", None, "Ali", "sara_a@mail.test", "Mumbai", "98800", "2024-05-09")]),
    "orders": (["id INT", "customer_id INT", "order_date DATE", "status VARCHAR(20)", "amount DECIMAL(10,2)",
                "shipping DECIMAL(10,2)", "discount DECIMAL(10,2)"], [
        (1, 1, "2024-06-01", "paid", 25500, 100, None), (2, 1, "2024-06-15", "shipped", 500, None, 50),
        (3, 2, "2024-06-20", "paid", 80000, 0, 1000), (4, 3, "2024-07-02", "cancelled", 12000, 100, None),
        (5, 3, "2024-07-10", "paid", 800, None, None), (6, 6, "2024-07-11", "pending", 65000, 200, 500),
        (7, 2, "2024-07-30", "paid", 500, 50, None), (8, 8, "2024-08-03", "shipped", 300, None, None)]),
    "order_items": (["id INT", "order_id INT", "product_id INT", "quantity INT", "unit_price DECIMAL(10,2)", "discount DECIMAL(10,2)"], [
        (1, 1, 1, 1, 25000, None), (2, 1, 4, 1, 500, None), (3, 2, 4, 1, 500, 50), (4, 3, 3, 1, 80000, 1000),
        (5, 4, 2, 1, 12000, None), (6, 5, 5, 1, 800, None), (7, 6, 6, 1, 65000, 500), (8, 7, 4, 1, 500, None),
        (9, 8, 7, 1, 300, None), (10, 1, 4, 2, 500, 100)]),
    "payments": (["id INT", "order_id INT", "paid_at DATE", "amount DECIMAL(10,2)", "method VARCHAR(20)"], [
        (1, 1, "2024-06-01", 25500, "card"), (2, 3, "2024-06-21", 40000, "upi"), (3, 3, "2024-06-28", 39000, "upi"),
        (4, 5, "2024-07-10", 800, "cash"), (5, 7, "2024-07-30", 500, "card"), (6, 2, "2024-06-16", 450, None)]),
    "returns": (["id INT", "order_item_id INT", "returned_on DATE", "reason VARCHAR(40)", "quantity INT"], [
        (1, 3, "2024-06-25", "damaged", 1), (2, 6, "2024-07-20", None, 1), (3, 10, "2024-06-10", "wrong size", 1)]),
}
ORDER = list(TABLES)


def build(eng) -> None:
    """(Re)create the practice tables as temporary tables on this connection."""
    for name in reversed(ORDER):
        # only ever the temporary copy: a bare DROP on PostgreSQL would fall through to the real table of that name
        eng.run(f"DROP TEMPORARY TABLE IF EXISTS {name}" if eng.kind == "mysql" else f"DROP TABLE IF EXISTS pg_temp.{name}")
    for name in ORDER:
        cols, rows = TABLES[name]
        eng.run(f"CREATE TEMPORARY TABLE {name} ({', '.join(cols)}, PRIMARY KEY (id))")
        ph = ", ".join(["%s"] * len(cols))
        eng.run(f"INSERT INTO {name} VALUES ({ph})", many=rows)


def sqlite_db(path: str):
    """E1: the same practice tables in a fresh SQLite database file (Python's sqlite3), for the SQLite concepts and practice
    bank. A file, not :memory:, so journal modes (WAL) behave as they do for an app."""
    import sqlite3

    conn = sqlite3.connect(path, isolation_level=None)
    for name in ORDER:
        cols, rows_ = TABLES[name]
        conn.execute(f"CREATE TABLE {name} ({', '.join(cols)}, PRIMARY KEY (id))")
        conn.executemany(f"INSERT INTO {name} VALUES ({', '.join(['?'] * len(cols))})", rows_)
    return conn


def engines() -> dict:
    """{'mysql': Engine, 'postgres': Engine} with the practice tables built; engines that aren't running are left out."""
    out = {}
    for cfg in (MYSQL, PG):
        try:
            e = Engine(cfg).open()
            build(e)
            out[cfg["type"]] = e
        except Exception:  # noqa: BLE001 - that server isn't running here
            pass
    return out


def norm(v) -> str:
    """One way to write a value, whichever engine returned it (10.00 / Decimal('10.00') / 10.0)."""
    from decimal import Decimal

    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (Decimal, float)):
        d = Decimal(str(v)).quantize(Decimal("0.01"))
        s = format(d.normalize(), "f")
        return s
    return str(v)


def rows(eng, sql: str, ordered: bool = False):
    """The rows a query returns, as normalised strings (sorted unless the order is part of the answer)."""
    out = [tuple(norm(x) for x in r) for r in eng.run(sql)]
    return out if ordered else sorted(out)
