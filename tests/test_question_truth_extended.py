from datetime import date, datetime, time
from decimal import Decimal

import pymysql
import psycopg
import pytest

from app import dbknow


QUESTION_IDS = [
    "average-order",
    "duplicate-order-amounts",
    "highest-order",
    "lowest-order",
    "median-like-order",
    "missing-amounts",
    "order-count",
    "orders-previous-month",
    "sales-previous-month",
    "sales-total",
    "zero-amount-orders",
]


def shift_month(d, delta):
    value = d.year * 12 + d.month - 1 + delta
    year, month0 = divmod(value, 12)
    return date(year, month0 + 1, 15)


def number(value):
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (int, float)):
        return float(value)

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def almost(actual, expected, eps=0.02):
    actual = number(actual)

    assert actual is not None, (
        actual,
        expected,
    )

    assert abs(actual - expected) <= eps, (
        actual,
        expected,
    )


@pytest.fixture(
    scope="module",
    params=["mysql", "postgres"],
)
def edge_db(request):
    engine = request.param

    if engine == "mysql":
        conn = pymysql.connect(
            host="127.0.0.1",
            port=13306,
            user="shopapp",
            password="app pass",
            database="shop_db",
            autocommit=True,
        )
    else:
        conn = psycopg.connect(
            host="127.0.0.1",
            port=15432,
            user="shopapp",
            password="app pass",
            dbname="shop_pg",
            autocommit=True,
        )

    with conn.cursor() as cur:
        cur.execute("SELECT CURRENT_DATE")
        today = cur.fetchone()[0]

    previous = shift_month(today, -1)
    older = shift_month(today, -2)

    rows = [
        (
            1,
            0.00,
            datetime.combine(
                today,
                time(10, 0),
            ),
        ),
        (
            2,
            None,
            datetime.combine(
                today,
                time(11, 0),
            ),
        ),
        (
            3,
            300.00,
            datetime.combine(
                today,
                time(12, 0),
            ),
        ),
        (
            4,
            300.00,
            datetime.combine(
                today,
                time(13, 0),
            ),
        ),
        (
            5,
            100.00,
            datetime.combine(
                previous,
                time(14, 0),
            ),
        ),
        (
            6,
            500.00,
            datetime.combine(
                previous,
                time(15, 0),
            ),
        ),
        (
            7,
            50.00,
            datetime.combine(
                older,
                time(16, 0),
            ),
        ),
    ]

    with conn.cursor() as cur:
        cur.execute(
            "DROP TABLE IF EXISTS "
            "question_truth_edge_orders"
        )

        if engine == "mysql":
            cur.execute("""
                CREATE TABLE question_truth_edge_orders (
                    id INT PRIMARY KEY,
                    amount DECIMAL(12,2) NULL,
                    created DATETIME NOT NULL
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE question_truth_edge_orders (
                    id INTEGER PRIMARY KEY,
                    amount NUMERIC(12,2) NULL,
                    created TIMESTAMP NOT NULL
                )
            """)

        cur.executemany(
            """
            INSERT INTO question_truth_edge_orders
                (id, amount, created)
            VALUES (%s, %s, %s)
            """,
            rows,
        )

    yield {
        "engine": engine,
        "conn": conn,
        "today": today,
        "previous": previous,
        "rows": rows,
    }

    with conn.cursor() as cur:
        cur.execute(
            "DROP TABLE IF EXISTS "
            "question_truth_edge_orders"
        )

    conn.close()


def execute(ctx, sql):
    with ctx["conn"].cursor() as cur:
        cur.execute(sql)

        columns = [
            d.name
            if hasattr(d, "name")
            else d[0]
            for d in cur.description
        ]

        rows = cur.fetchall()

    return columns, rows


@pytest.mark.parametrize(
    "qid",
    QUESTION_IDS,
)
def test_extended_question_truth(
    edge_db,
    qid,
):
    ctx = edge_db

    questions = {
        q["id"]: q
        for q in dbknow.load("questions")
    }

    assert qid in questions

    q = questions[qid]

    template = q["templates"][
        "mysql"
        if ctx["engine"] == "mysql"
        else "postgres"
    ]

    sql = template.format(
        table="question_truth_edge_orders",
        column="amount",
        date_col="created",
    )

    columns, result = execute(
        ctx,
        sql,
    )

    rows = ctx["rows"]

    non_null = [
        float(row[1])
        for row in rows
        if row[1] is not None
    ]

    if qid == "average-order":
        almost(
            result[0][0],
            sum(non_null) / len(non_null),
        )

    elif qid == "highest-order":
        almost(
            result[0][0],
            max(non_null),
        )

    elif qid == "lowest-order":
        almost(
            result[0][0],
            min(non_null),
        )

    elif qid == "order-count":
        almost(
            result[0][0],
            len(rows),
        )

    elif qid == "sales-total":
        almost(
            result[0][0],
            sum(non_null),
        )

    elif qid == "median-like-order":
        ordered = sorted(non_null)
        n = len(ordered)

        if n % 2:
            expected = ordered[n // 2]
        else:
            expected = (
                ordered[n // 2 - 1]
                + ordered[n // 2]
            ) / 2

        almost(
            result[0][0],
            expected,
        )

    elif qid == "zero-amount-orders":
        amount_idx = [
            c.lower()
            for c in columns
        ].index("amount")

        actual = [
            number(row[amount_idx])
            for row in result
        ]

        assert actual == [0.0]

    elif qid == "missing-amounts":
        amount_idx = [
            c.lower()
            for c in columns
        ].index("amount")

        actual = [
            row[amount_idx]
            for row in result
        ]

        assert actual == [None]

    elif qid == "duplicate-order-amounts":
        lower = [
            c.lower()
            for c in columns
        ]

        amount_idx = lower.index("amount")
        count_idx = lower.index(
            "occurrences"
        )

        actual = {
            (
                number(row[amount_idx]),
                int(row[count_idx]),
            )
            for row in result
        }

        assert actual == {
            (300.0, 2),
        }

    elif qid == "sales-previous-month":
        expected = sum(
            float(row[1])
            for row in rows
            if (
                row[1] is not None
                and row[2].year
                == ctx["previous"].year
                and row[2].month
                == ctx["previous"].month
            )
        )

        almost(
            result[0][0],
            expected,
        )

    elif qid == "orders-previous-month":
        expected = sum(
            1
            for row in rows
            if (
                row[2].year
                == ctx["previous"].year
                and row[2].month
                == ctx["previous"].month
            )
        )

        almost(
            result[0][0],
            expected,
        )

    else:
        raise AssertionError(
            f"No truth rule for {qid}"
        )
