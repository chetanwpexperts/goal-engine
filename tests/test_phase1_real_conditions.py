import threading
import time

import pymysql
import psycopg

from app import dbknow
from app.dbknow import diagnosis
from connector import goal_connector as gc


MYSQL = dict(
    host="127.0.0.1",
    port=13306,
    user="shopapp",
    password="app pass",
    database="shop_db",
)

PG = dict(
    host="127.0.0.1",
    port=15432,
    user="shopapp",
    password="app pass",
    dbname="shop_pg",
)


def mysql_conn(autocommit=True):
    return pymysql.connect(**MYSQL, autocommit=autocommit)


def pg_conn(autocommit=True):
    return psycopg.connect(**PG, autocommit=autocommit)


def test_mysql_many_idle_connections_and_healthy():
    idle = []

    try:
        for _ in range(60):
            idle.append(mysql_conn())

        cfg = {
            "type": "mysql",
            "host": "127.0.0.1",
            "port": "13306",
            "database": "shop_db",
            "user": "shopapp",
            "password": "app pass",
        }

        db = gc.Database(cfg).connect()

        issue = next(
            x for x in dbknow.load("issues")
            if x["id"] == "too-many-connections"
        )

        checks = [
            x for x in issue["diagnose"]
            if x.get("engine") == "mysql" and x.get("sql")
        ]

        results = [
            db.query(db.resolve_check_sql(check["sql"]), 100)
            for check in checks
        ]

        report = diagnosis.evaluate(issue, checks, results)

        assert any(
            c["id"] == "leak"
            for c in report["likely_causes"]
        )

        assert report["no_problem_found"] is False

    finally:
        for conn in idle:
            conn.close()

    time.sleep(0.2)

    db = gc.Database(cfg).connect()

    results = [
        db.query(db.resolve_check_sql(check["sql"]), 100)
        for check in checks
    ]

    report = diagnosis.evaluate(issue, checks, results)

    assert report["likely_causes"] == []
    assert report["no_problem_found"] is True


def test_postgres_many_idle_connections_and_healthy():
    idle = []

    try:
        for _ in range(60):
            idle.append(pg_conn())

        cfg = {
            "type": "postgres",
            "host": "127.0.0.1",
            "port": "15432",
            "database": "shop_pg",
            "user": "shopapp",
            "password": "app pass",
        }

        db = gc.Database(cfg).connect()

        issue = next(
            x for x in dbknow.load("issues")
            if x["id"] == "too-many-connections"
        )

        checks = [
            x for x in issue["diagnose"]
            if x.get("engine") == "postgres" and x.get("sql")
        ]

        results = [
            db.query(db.resolve_check_sql(check["sql"]), 100)
            for check in checks
        ]

        report = diagnosis.evaluate(issue, checks, results)

        assert any(
            c["id"] == "leak"
            for c in report["likely_causes"]
        )

        assert report["no_problem_found"] is False

    finally:
        for conn in idle:
            conn.close()

    time.sleep(0.2)

    db = gc.Database(cfg).connect()

    results = [
        db.query(db.resolve_check_sql(check["sql"]), 100)
        for check in checks
    ]

    report = diagnosis.evaluate(issue, checks, results)

    assert report["likely_causes"] == []
    assert report["no_problem_found"] is True


def test_mysql_long_running_query():
    def slow():
        conn = mysql_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT SLEEP(4)")
        finally:
            conn.close()

    t = threading.Thread(target=slow, daemon=True)
    t.start()

    time.sleep(1.2)

    observer = mysql_conn()

    try:
        with observer.cursor() as cur:
            cur.execute("""
                SELECT TIME, INFO
                FROM information_schema.PROCESSLIST
                WHERE USER = 'shopapp'
                  AND COMMAND <> 'Sleep'
                  AND TIME >= 1
            """)

            rows = cur.fetchall()

        assert any(
            row[1] and "SLEEP(4)" in str(row[1]).upper()
            for row in rows
        )

    finally:
        observer.close()
        t.join()


def test_postgres_long_running_query():
    def slow():
        conn = pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_sleep(4)")
        finally:
            conn.close()

    t = threading.Thread(target=slow, daemon=True)
    t.start()

    time.sleep(1.2)

    observer = pg_conn()

    try:
        with observer.cursor() as cur:
            cur.execute("""
                SELECT query
                FROM pg_stat_activity
                WHERE usename = 'shopapp'
                  AND state = 'active'
                  AND now() - query_start > interval '1 second'
            """)

            rows = cur.fetchall()

        assert any(
            "pg_sleep(4)" in str(row[0]).lower()
            for row in rows
        )

    finally:
        observer.close()
        t.join()


def test_mysql_real_lock_wait():
    setup = mysql_conn()

    with setup.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS phase1_lock_mysql (
                id INT PRIMARY KEY,
                value INT NOT NULL
            )
        """)

        cur.execute("""
            INSERT INTO phase1_lock_mysql (id, value)
            VALUES (1, 10)
            ON DUPLICATE KEY UPDATE value = 10
        """)

    setup.close()

    c1 = mysql_conn(autocommit=False)
    c2 = mysql_conn(autocommit=False)

    try:
        with c1.cursor() as cur:
            cur.execute("""
                UPDATE phase1_lock_mysql
                SET value = value + 1
                WHERE id = 1
            """)

        def blocked():
            try:
                with c2.cursor() as cur:
                    cur.execute("""
                        UPDATE phase1_lock_mysql
                        SET value = value + 1
                        WHERE id = 1
                    """)
            finally:
                try:
                    c2.rollback()
                except Exception:
                    pass

        t = threading.Thread(target=blocked, daemon=True)
        t.start()

        time.sleep(1)

        observer = mysql_conn()

        try:
            with observer.cursor() as cur:
                cur.execute("""
                    SELECT STATE, INFO
                    FROM information_schema.PROCESSLIST
                    WHERE USER = 'shopapp'
                """)

                rows = cur.fetchall()

            assert any(
                row[1]
                and "UPDATE phase1_lock_mysql" in str(row[1])
                for row in rows
            )

        finally:
            observer.close()

    finally:
        # Release the blocking row lock first.
        try:
            c1.rollback()
        except Exception:
            pass

        # Let the blocked worker finish before closing its connection.
        if "t" in locals():
            t.join(timeout=5)

        try:
            c2.rollback()
        except Exception:
            pass

        c1.close()
        c2.close()


def test_postgres_real_lock_wait():
    setup = pg_conn()

    with setup.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS phase1_lock_pg (
                id INTEGER PRIMARY KEY,
                value INTEGER NOT NULL
            )
        """)

        cur.execute("""
            INSERT INTO phase1_lock_pg (id, value)
            VALUES (1, 10)
            ON CONFLICT (id)
            DO UPDATE SET value = 10
        """)

    setup.close()

    c1 = pg_conn(autocommit=False)
    c2 = pg_conn(autocommit=False)

    try:
        with c1.cursor() as cur:
            cur.execute("""
                UPDATE phase1_lock_pg
                SET value = value + 1
                WHERE id = 1
            """)

        def blocked():
            try:
                with c2.cursor() as cur:
                    cur.execute("""
                        UPDATE phase1_lock_pg
                        SET value = value + 1
                        WHERE id = 1
                    """)
            finally:
                try:
                    c2.rollback()
                except Exception:
                    pass

        t = threading.Thread(target=blocked, daemon=True)
        t.start()

        time.sleep(1)

        observer = pg_conn()

        try:
            with observer.cursor() as cur:
                cur.execute("""
                    SELECT wait_event_type, query
                    FROM pg_stat_activity
                    WHERE usename = 'shopapp'
                      AND wait_event_type = 'Lock'
                """)

                rows = cur.fetchall()

            assert any(
                "update phase1_lock_pg" in str(row[1]).lower()
                for row in rows
            )

        finally:
            observer.close()

    finally:
        # Release the blocking row lock first.
        try:
            c1.rollback()
        except Exception:
            pass

        # Let the blocked worker finish before closing its connection.
        if "t" in locals():
            t.join(timeout=5)

        try:
            c2.rollback()
        except Exception:
            pass

        c1.close()
        c2.close()


def test_mysql_table_without_primary_key():
    conn = mysql_conn()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS phase1_no_pk_mysql (
                    name VARCHAR(100),
                    amount DECIMAL(10,2)
                )
            """)

            cur.execute("""
                SELECT t.TABLE_NAME
                FROM information_schema.TABLES t
                LEFT JOIN information_schema.TABLE_CONSTRAINTS tc
                  ON tc.TABLE_SCHEMA = t.TABLE_SCHEMA
                 AND tc.TABLE_NAME = t.TABLE_NAME
                 AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
                WHERE t.TABLE_SCHEMA = DATABASE()
                  AND t.TABLE_TYPE = 'BASE TABLE'
                  AND tc.CONSTRAINT_NAME IS NULL
            """)

            rows = cur.fetchall()

        assert any(
            row[0] == "phase1_no_pk_mysql"
            for row in rows
        )

    finally:
        conn.close()


def test_postgres_table_without_primary_key():
    conn = pg_conn()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS phase1_no_pk_pg (
                    name TEXT,
                    amount NUMERIC(10,2)
                )
            """)

            cur.execute("""
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n
                  ON n.oid = c.relnamespace
                WHERE c.relkind = 'r'
                  AND n.nspname = 'public'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM pg_constraint con
                      WHERE con.conrelid = c.oid
                        AND con.contype = 'p'
                  )
            """)

            rows = cur.fetchall()

        assert any(
            row[0] == "phase1_no_pk_pg"
            for row in rows
        )

    finally:
        conn.close()


def test_mysql_full_scan_missing_index():
    conn = mysql_conn()

    try:
        with conn.cursor() as cur:
            cur.execute("ANALYZE TABLE scan_test")

            cur.execute("""
                EXPLAIN
                SELECT *
                FROM scan_test
                WHERE customer_name = 'Customer 3499'
            """)

            cols = [d[0] for d in cur.description]
            row = dict(zip(cols, cur.fetchone()))

        assert str(row["type"]).upper() == "ALL"
        assert row["key"] is None

    finally:
        conn.close()


def test_postgres_full_scan_missing_index():
    conn = pg_conn()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS phase1_scan_pg (
                    id SERIAL PRIMARY KEY,
                    customer_name TEXT,
                    status TEXT,
                    amount NUMERIC(10,2)
                )
            """)

            cur.execute("SELECT COUNT(*) FROM phase1_scan_pg")
            count = cur.fetchone()[0]

            if count < 3000:
                cur.execute("""
                    INSERT INTO phase1_scan_pg
                        (customer_name, status, amount)
                    SELECT
                        'Customer ' || g,
                        CASE
                            WHEN g % 2 = 0 THEN 'paid'
                            ELSE 'pending'
                        END,
                        g::numeric
                    FROM generate_series(1, 3500) g
                """)

            cur.execute("ANALYZE phase1_scan_pg")

            cur.execute("""
                EXPLAIN
                SELECT *
                FROM phase1_scan_pg
                WHERE customer_name = 'Customer 3499'
            """)

            plan = "\n".join(row[0] for row in cur.fetchall())

        assert "Seq Scan on phase1_scan_pg" in plan

    finally:
        conn.close()


def test_mysql_broad_privileges():
    conn = mysql_conn()

    try:
        with conn.cursor() as cur:
            cur.execute("SHOW GRANTS FOR CURRENT_USER")
            rows = cur.fetchall()

        text = "\n".join(str(row[0]).upper() for row in rows)

        assert (
            "ALL PRIVILEGES" in text
            or "GRANT OPTION" in text
            or "SUPER" in text
        )

    finally:
        conn.close()


def test_postgres_broad_privileges():
    conn = pg_conn()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    r.rolsuper,
                    r.rolcreaterole,
                    r.rolcreatedb,
                    r.rolreplication,
                    has_database_privilege(
                        current_user,
                        current_database(),
                        'CREATE'
                    ),
                    has_schema_privilege(
                        current_user,
                        'public',
                        'CREATE'
                    )
                FROM pg_roles r
                WHERE r.rolname = current_user
            """)

            row = cur.fetchone()

        assert any(row)

    finally:
        conn.close()
