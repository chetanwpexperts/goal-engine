"""Real error messages produced by the real MariaDB / PostgreSQL test databases (scripts/setup_test_dbs.sh)."""

import contextlib


def mariadb():
    import pymysql
    return pymysql.connect(host="127.0.0.1", user="shopapp", password="app pass", database="shop_db", autocommit=True)


def postgres():
    import psycopg
    return psycopg.connect("host=127.0.0.1 user=shopapp password='app pass' dbname=shop_pg", autocommit=True)


def setup_pg(pg):
    with pg.cursor() as c:
        c.execute("DROP TABLE IF EXISTS t_child; DROP TABLE IF EXISTS t_parent;"
                  "CREATE TABLE t_parent(id int primary key, email varchar(20) unique not null);"
                  "CREATE TABLE t_child(id int primary key, parent_id int references t_parent(id));"
                  "INSERT INTO t_parent VALUES (1,'a@b.com'); INSERT INTO t_child VALUES (1,1);")


def my_error(my, sql):
    try:
        with my.cursor() as c:
            c.execute(sql)
    except Exception as e:  # noqa: BLE001
        return f"ERROR {e.args[0]}: {e.args[1]}"
    return None


def pg_error(pg, sql):
    try:
        with pg.cursor() as c:
            c.execute(sql)
    except Exception as e:  # noqa: BLE001
        return f"ERROR:  {e}\nSQLSTATE: {e.sqlstate}"
    return None


MY_CASES = [
    ("INSERT INTO wp_options (option_name) VALUES ('dupcheck'),('dupcheck')", "duplicate_key"),
    ("INSERT INTO order_lines (parent_id) VALUES (999)", "fk_missing_parent"),
    ("SELECT emial FROM wp_users", "unknown_column"),
    ("SELECT * FROM orderz", "unknown_table"),
    ("SELECT id name FROM orders WHERE", "syntax_error"),
    ("INSERT INTO items(name) VALUES (REPEAT('x',60))", "value_too_long"),
    ("SELECT id FROM orders JOIN items ON 1=1", "ambiguous_column"),
    ("SELECT * FROM shop_db.nokey WHERE a = 1 ORDER BY", "syntax_error"),
]
PG_CASES = [
    ("INSERT INTO t_parent VALUES (2,'a@b.com')", "duplicate_key"),
    ("INSERT INTO t_child VALUES (2,99)", "fk_missing_parent"),
    ("DELETE FROM t_parent WHERE id=1", "fk_has_children"),
    ("INSERT INTO t_parent VALUES (3,NULL)", "not_null"),
    ("INSERT INTO t_parent VALUES (4, repeat('x',30))", "value_too_long"),
    ("SELECT emial FROM t_parent", "unknown_column"),
    ("SELECT * FROM orderz", "unknown_table"),
    ("SELECT * FROM t_parent ORDER id", "syntax_error"),
    ("SELECT * FROM t_parent WHERE id = 'abc'", "bad_value_type"),
    ("SELECT id FROM t_parent JOIN t_child ON true", "ambiguous_column"),
    ("SELECT group_concat(email) FROM t_parent", "unknown_function"),
]


def collect():
    """[(message, expected cause, engine)] from the live test databases."""
    out = []
    with contextlib.closing(mariadb()) as my:
        out += [(my_error(my, sql), want, "mysql") for sql, want in MY_CASES]
    with contextlib.closing(postgres()) as pg:
        setup_pg(pg)
        out += [(pg_error(pg, sql), want, "postgresql") for sql, want in PG_CASES]
    return out
