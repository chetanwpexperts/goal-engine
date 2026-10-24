import json
from pathlib import Path
from datetime import datetime, date, timedelta
from decimal import Decimal

import pytest

from connector import goal_connector as gc


QUESTIONS = {
    q["id"]: q
    for q in json.loads(Path("app/dbknow/questions.json").read_text())
}


ROWS = [
    {"id": 1, "amount": Decimal("100.00"), "created": datetime(2026, 9, 14, 10, 0)},
    {"id": 2, "amount": Decimal("250.50"), "created": datetime(2026, 10, 14, 10, 0)},
    {"id": 3, "amount": Decimal("500.00"), "created": datetime(2026, 10, 22, 10, 0)},
    {"id": 4, "amount": Decimal("1000.00"), "created": datetime(2026, 10, 24, 10, 0)},
]

TODAY = date(2026, 10, 24)


def render(qid, engine, **kwargs):
    q = QUESTIONS[qid]
    sql = q["templates"][engine]
    return sql.format(**kwargs)


def scalar(db, sql):
    _, rows = db.rows(gc.safe_sql(sql))
    return rows[0][0]


@pytest.mark.parametrize("engine,cfg", [
    ("mysql", {
        "type": "mysql",
        "host": "127.0.0.1",
        "port": "13306",
        "database": "shop_db",
        "user": "shopapp",
        "password": "app pass",
    }),
    ("postgres", {
        "type": "postgres",
        "host": "127.0.0.1",
        "port": "15432",
        "database": "shop_pg",
        "user": "shopapp",
        "password": "app pass",
    }),
])
def test_question_answers_match_fixture_truth(engine, cfg):
    db = gc.Database(cfg).connect()

    # today
    expected = sum(r["amount"] for r in ROWS if r["created"].date() == TODAY)
    sql = render(
        "sales-today", engine,
        table="orders", column="amount", date_col="created"
    )
    assert Decimal(str(scalar(db, sql))) == expected

    expected = sum(1 for r in ROWS if r["created"].date() == TODAY)
    sql = render(
        "orders-today", engine,
        table="orders", column="amount", date_col="created"
    )
    assert int(scalar(db, sql)) == expected

    # current month
    expected = sum(
        r["amount"]
        for r in ROWS
        if r["created"].year == TODAY.year
        and r["created"].month == TODAY.month
    )
    sql = render(
        "sales-month", engine,
        table="orders", column="amount", date_col="created"
    )
    assert Decimal(str(scalar(db, sql))) == expected

    # current year
    expected = sum(
        r["amount"]
        for r in ROWS
        if r["created"].year == TODAY.year
    )
    sql = render(
        "sales-year", engine,
        table="orders", column="amount", date_col="created"
    )
    assert Decimal(str(scalar(db, sql))) == expected

    expected = sum(
        1 for r in ROWS
        if r["created"].year == TODAY.year
    )
    sql = render(
        "orders-year", engine,
        table="orders", column="amount", date_col="created"
    )
    assert int(scalar(db, sql)) == expected

    # amount thresholds
    threshold = Decimal("500")
    expected_ids = [
        r["id"] for r in ROWS
        if r["amount"] > threshold
    ]
    sql = render(
        "orders-over-amount", engine,
        table="orders", column="amount", amount="500"
    )
    _, rows = db.rows(gc.safe_sql(sql))
    assert [int(r[0]) for r in rows] == list(reversed(expected_ids))

    expected_ids = [
        r["id"] for r in ROWS
        if r["amount"] < threshold
    ]
    sql = render(
        "orders-under-amount", engine,
        table="orders", column="amount", amount="500"
    )
    _, rows = db.rows(gc.safe_sql(sql))
    assert [int(r[0]) for r in rows] == expected_ids

    # amount range
    expected_ids = [
        r["id"] for r in ROWS
        if Decimal("200") <= r["amount"] <= Decimal("600")
    ]
    sql = render(
        "amount-range", engine,
        table="orders",
        column="amount",
        min_amount="200",
        max_amount="600"
    )
    _, rows = db.rows(gc.safe_sql(sql))
    assert [int(r[0]) for r in rows] == expected_ids

    # date range
    start = date(2026, 10, 14)
    end = date(2026, 10, 22)
    expected_ids = [
        r["id"] for r in ROWS
        if start <= r["created"].date() <= end
    ]
    sql = render(
        "order-range", engine,
        table="orders",
        date_col="created",
        start="2026-10-14",
        end="2026-10-22"
    )
    _, rows = db.rows(gc.safe_sql(sql))
    assert [int(r[0]) for r in rows] == expected_ids

    # recent / oldest
    sql = render(
        "recent-orders", engine,
        table="orders", date_col="created"
    )
    _, rows = db.rows(gc.safe_sql(sql))
    assert [int(r[0]) for r in rows] == [4, 3, 2, 1]

    sql = render(
        "oldest-orders", engine,
        table="orders", date_col="created"
    )
    _, rows = db.rows(gc.safe_sql(sql))
    assert [int(r[0]) for r in rows] == [1, 2, 3, 4]

    # average daily sales
    by_day = {}
    for r in ROWS:
        by_day.setdefault(r["created"].date(), Decimal("0"))
        by_day[r["created"].date()] += r["amount"]

    expected = sum(by_day.values()) / Decimal(len(by_day))

    sql = render(
        "average-daily-sales", engine,
        table="orders", column="amount", date_col="created"
    )
    assert Decimal(str(scalar(db, sql))) == expected

    # best sales day
    best_day = max(by_day.items(), key=lambda x: x[1])

    sql = render(
        "best-sales-day", engine,
        table="orders", column="amount", date_col="created"
    )
    _, rows = db.rows(gc.safe_sql(sql))
    assert Decimal(str(rows[0][1])) == best_day[1]

    # worst sales day
    worst_day = min(by_day.items(), key=lambda x: x[1])

    sql = render(
        "worst-sales-day", engine,
        table="orders", column="amount", date_col="created"
    )
    _, rows = db.rows(gc.safe_sql(sql))
    assert Decimal(str(rows[0][1])) == worst_day[1]
