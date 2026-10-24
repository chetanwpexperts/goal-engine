import pymysql
import psycopg

from app import dbknow


def mysql_conn():
    return pymysql.connect(
        host="127.0.0.1",
        port=13306,
        user="shopapp",
        password="app pass",
        database="shop_db",
        autocommit=True,
    )


def pg_conn():
    return psycopg.connect(
        host="127.0.0.1",
        port=15432,
        user="shopapp",
        password="app pass",
        dbname="shop_pg",
        autocommit=True,
    )


def capture(fn):
    try:
        fn()
    except Exception as exc:
        return str(exc)
    raise AssertionError("Expected database error")


def test_real_mysql_errors_match():
    conn = mysql_conn()

    cases = [
        (
            "mysql-unknown-table",
            lambda: conn.cursor().execute(
                "SELECT * FROM definitely_missing_table"
            ),
        ),
        (
            "mysql-syntax",
            lambda: conn.cursor().execute(
                "SELEC id FROM orders"
            ),
        ),
        (
            "mysql-data-too-long",
            lambda: conn.cursor().execute(
                "INSERT INTO ge_users (ID, user_login) VALUES (999999, %s)",
                ("x" * 200,),
            ),
        ),
    ]

    for expected, fn in cases:
        message = capture(fn)
        found = dbknow.find_error("mysql", message)

        print(expected, "=>", found["id"] if found else "NO MATCH")

        assert found is not None
        assert found["id"] == expected


def test_real_postgres_errors_match():
    conn = pg_conn()

    cases = [
        (
            "pg-relation-missing",
            lambda: conn.cursor().execute(
                "SELECT * FROM definitely_missing_table"
            ),
        ),
        (
            "pg-syntax",
            lambda: conn.cursor().execute(
                "SELEC id FROM orders"
            ),
        ),
        (
            "pg-invalid-input",
            lambda: conn.cursor().execute(
                "SELECT CAST('abc' AS INTEGER)"
            ),
        ),
        (
            "pg-division-zero",
            lambda: conn.cursor().execute(
                "SELECT 1 / 0"
            ),
        ),
    ]

    for expected, fn in cases:
        message = capture(fn)
        found = dbknow.find_error("postgres", message)

        print(expected, "=>", found["id"] if found else "NO MATCH")

        assert found is not None
        assert found["id"] == expected
