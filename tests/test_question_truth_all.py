import calendar
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pymysql
import psycopg
import pytest

from app import dbknow


QUESTION_IDS = [
    "sales-today",
    "sales-week",
    "sales-month",
    "orders-today",
    "orders-week",
    "growth-month",
    "orders-over-amount",
    "orders-under-amount",
    "recent-orders",
    "oldest-orders",
    "sales-by-day",
    "sales-by-week",
    "sales-by-quarter",
    "orders-by-quarter",
    "sales-yesterday",
    "sales-last-seven",
    "sales-last-thirty",
    "orders-last-seven",
    "orders-last-thirty",
    "average-daily-sales",
    "best-sales-day",
    "worst-sales-day",
    "order-range",
    "amount-range",
    "sales-year",
    "orders-year",
]


def previous_month(d):
    if d.month == 1:
        return date(d.year - 1, 12, 15)
    return date(d.year, d.month - 1, 15)


def safe_day(year, month, wanted):
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(wanted, last))


def normal(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def number(value):
    value = normal(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def almost(a, b, eps=0.02):
    assert a is not None, f"SQL result is not numeric: {a!r}"
    assert abs(float(a) - float(b)) <= eps, f"{a} != {b}"


class Params(dict):
    def __missing__(self, key):
        k = key.lower()

        if k in {"table"}:
            return "question_truth_orders"

        if k in {"column", "amount_col", "amount_column"}:
            return "amount"

        if k in {"date_col", "date_column", "created_col"}:
            return "created"

        if "start" in k or k.startswith("from"):
            return self["start_date"]

        if "end" in k or k.startswith("to"):
            return self["end_date"]

        if "min" in k and ("amount" in k or k == "min"):
            return 100

        if "max" in k and ("amount" in k or k == "max"):
            return 500

        if k in {
            "amount",
            "threshold",
            "value",
            "minimum",
            "maximum",
        }:
            return 250

        if "limit" in k or k in {"n", "top"}:
            return 3

        if k == "days":
            return 7

        if k == "months":
            return 1

        if k in {"pk", "id_col"}:
            return "id"

        raise KeyError(
            f"Unsupported question template placeholder: {{{key}}}"
        )


def make_rows(today):
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    prev_month = previous_month(today)

    current_year_day = safe_day(today.year, 1, 15)
    previous_year_day = date(today.year - 1, 6, 15)

    raw = [
        (1, 1000.00, datetime.combine(today, time(10, 0))),
        (2, 25.00, datetime.combine(today, time(11, 0))),
        (3, 300.00, datetime.combine(today - timedelta(days=1), time(12, 0))),
        (4, 500.00, datetime.combine(week_start, time(13, 0))),
        (5, 200.00, datetime.combine(month_start, time(14, 0))),
        (6, 100.00, datetime.combine(prev_month, time(15, 0))),
        (7, 75.00, datetime.combine(current_year_day, time(16, 0))),
        (8, 50.00, datetime.combine(previous_year_day, time(17, 0))),
        (9, 150.00, datetime.combine(today - timedelta(days=8), time(18, 0))),
    ]

    return [
        {"id": i, "amount": amount, "created": created}
        for i, amount, created in raw
    ]


@pytest.fixture(scope="module", params=["mysql", "postgres"])
def truth_db(request):
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

        with conn.cursor() as cur:
            cur.execute("SELECT CURRENT_DATE")
            today = cur.fetchone()[0]

            cur.execute("""
                DROP TABLE IF EXISTS question_truth_orders
            """)

            cur.execute("""
                CREATE TABLE question_truth_orders (
                    id INT PRIMARY KEY,
                    amount DECIMAL(12,2) NOT NULL,
                    created DATETIME NOT NULL
                )
            """)

        rows = make_rows(today)

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO question_truth_orders
                    (id, amount, created)
                VALUES (%s, %s, %s)
                """,
                [
                    (r["id"], r["amount"], r["created"])
                    for r in rows
                ],
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

            cur.execute("""
                DROP TABLE IF EXISTS question_truth_orders
            """)

            cur.execute("""
                CREATE TABLE question_truth_orders (
                    id INTEGER PRIMARY KEY,
                    amount NUMERIC(12,2) NOT NULL,
                    created TIMESTAMP NOT NULL
                )
            """)

        rows = make_rows(today)

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO question_truth_orders
                    (id, amount, created)
                VALUES (%s, %s, %s)
                """,
                [
                    (r["id"], r["amount"], r["created"])
                    for r in rows
                ],
            )

    yield {
        "engine": engine,
        "conn": conn,
        "today": today,
        "rows": rows,
    }

    conn.close()


def execute(ctx, sql):
    conn = ctx["conn"]

    with conn.cursor() as cur:
        cur.execute(sql)

        columns = [
            d.name if hasattr(d, "name") else d[0]
            for d in cur.description
        ]

        rows = cur.fetchall()

    return {
        "columns": columns,
        "rows": rows,
    }


def first_number(result):
    assert result["rows"], result
    return number(result["rows"][0][0])


def named_number(result, names):
    names = [n.lower() for n in names]

    for i, col in enumerate(result["columns"]):
        low = str(col).lower()
        if any(name in low for name in names):
            return number(result["rows"][0][i])

    # Most aggregate queries put the important value last.
    for value in reversed(result["rows"][0]):
        n = number(value)
        if n is not None:
            return n

    return None


def group_last_numbers(result):
    values = []

    for row in result["rows"]:
        n = number(row[-1])
        assert n is not None, (result["columns"], row)
        values.append(round(n, 6))

    return sorted(values)


def daily_totals(rows):
    totals = defaultdict(float)

    for row in rows:
        totals[row["created"].date()] += row["amount"]

    return totals


def week_key(d):
    iso = d.isocalendar()
    return (iso.year, iso.week)


def weekly_totals(rows):
    totals = defaultdict(float)

    for row in rows:
        totals[week_key(row["created"].date())] += row["amount"]

    return totals


def quarter_key(d):
    return (d.year, ((d.month - 1) // 3) + 1)


def quarterly_totals(rows):
    totals = defaultdict(float)

    for row in rows:
        totals[quarter_key(row["created"].date())] += row["amount"]

    return totals


def quarterly_counts(rows):
    counts = defaultdict(int)

    for row in rows:
        counts[quarter_key(row["created"].date())] += 1

    return counts


def filtered_result_truth(result, sql, expected_rows):
    """
    Validate the filter even if the question intentionally returns
    rows, COUNT, or SUM.
    """
    low = sql.lower()
    columns = [str(c).lower() for c in result["columns"]]

    if "id" in columns:
        idx = columns.index("id")

        actual_ids = [
            int(row[idx])
            for row in result["rows"]
        ]

        expected_ids = [
            row["id"]
            for row in expected_rows
        ]

        assert actual_ids == expected_ids
        return

    if "count(" in low:
        almost(
            first_number(result),
            len(expected_rows),
        )
        return

    if "sum(" in low:
        almost(
            first_number(result),
            sum(r["amount"] for r in expected_rows),
        )
        return

    raise AssertionError(
        f"Cannot validate filtered result.\n"
        f"SQL: {sql}\n"
        f"columns={result['columns']}\n"
        f"rows={result['rows']}"
    )


@pytest.mark.parametrize("qid", QUESTION_IDS)
def test_all_corrected_questions_against_python_truth(truth_db, qid):
    ctx = truth_db
    engine = ctx["engine"]
    today = ctx["today"]
    rows = ctx["rows"]

    questions = {
        q["id"]: q
        for q in dbknow.load("questions")
    }

    assert qid in questions

    q = questions[qid]

    template = (
        q["templates"].get("mysql")
        if engine == "mysql"
        else q["templates"].get("postgres")
    )

    assert template, f"{qid}: missing {engine} template"

    params = Params(
        table="question_truth_orders",
        column="amount",
        date_col="created",
        amount=250,
        threshold=250,
        min_amount=100,
        max_amount=500,
        minimum_amount=100,
        maximum_amount=500,
        start_date=(today - timedelta(days=10)).isoformat(),
        end_date=today.isoformat(),
        from_date=(today - timedelta(days=10)).isoformat(),
        to_date=today.isoformat(),
        limit=3,
        n=3,
        top=3,
    )

    placeholders = set(
        re.findall(r"{([A-Za-z_][A-Za-z0-9_]*)}", template)
    )

    # Force unsupported placeholders to fail clearly before SQL execution.
    for key in placeholders:
        _ = params[key]

    sql = template.format_map(params)
    result = execute(ctx, sql)

    def d(row):
        return row["created"].date()

    # ---------- scalar period totals ----------

    if qid == "sales-today":
        expected = sum(r["amount"] for r in rows if d(r) == today)
        almost(first_number(result), expected)

    elif qid == "sales-week":
        expected = sum(
            r["amount"]
            for r in rows
            if week_key(d(r)) == week_key(today)
        )
        almost(first_number(result), expected)

    elif qid == "sales-month":
        expected = sum(
            r["amount"]
            for r in rows
            if d(r).year == today.year
            and d(r).month == today.month
        )
        almost(first_number(result), expected)

    elif qid == "orders-today":
        expected = sum(1 for r in rows if d(r) == today)
        almost(first_number(result), expected)

    elif qid == "sales-yesterday":
        yesterday = today - timedelta(days=1)

        expected = sum(
            r["amount"]
            for r in rows
            if d(r) == yesterday
        )

        almost(first_number(result), expected)

    elif qid == "sales-last-seven":
        cutoff = today - timedelta(days=6)

        expected = sum(
            r["amount"]
            for r in rows
            if cutoff <= d(r) <= today
        )

        almost(first_number(result), expected)

    elif qid == "sales-last-thirty":
        cutoff = today - timedelta(days=29)

        expected = sum(
            r["amount"]
            for r in rows
            if cutoff <= d(r) <= today
        )

        almost(first_number(result), expected)

    elif qid == "orders-last-seven":
        cutoff = today - timedelta(days=6)

        expected = sum(
            1 for r in rows
            if cutoff <= d(r) <= today
        )

        almost(first_number(result), expected)

    elif qid == "orders-last-thirty":
        cutoff = today - timedelta(days=29)

        expected = sum(
            1 for r in rows
            if cutoff <= d(r) <= today
        )

        almost(first_number(result), expected)

    elif qid == "sales-year":
        expected = sum(
            r["amount"]
            for r in rows
            if d(r).year == today.year
        )

        almost(first_number(result), expected)

    elif qid == "orders-year":
        expected = sum(
            1 for r in rows
            if d(r).year == today.year
        )

        almost(first_number(result), expected)

    # ---------- grouped questions ----------

    elif qid == "orders-week":
        week_rows = [
            r for r in rows
            if week_key(d(r)) == week_key(today)
        ]

        counts = defaultdict(int)

        for r in week_rows:
            counts[d(r)] += 1

        expected = sorted(float(v) for v in counts.values())

        assert group_last_numbers(result) == expected

    elif qid == "sales-by-day":
        expected = sorted(
            round(v, 6)
            for v in daily_totals(rows).values()
        )

        assert group_last_numbers(result) == expected

    elif qid == "sales-by-week":
        expected = sorted(
            round(v, 6)
            for v in weekly_totals(rows).values()
        )

        assert group_last_numbers(result) == expected

    elif qid == "sales-by-quarter":
        expected = sorted(
            round(v, 6)
            for v in quarterly_totals(rows).values()
        )

        assert group_last_numbers(result) == expected

    elif qid == "orders-by-quarter":
        expected = sorted(
            float(v)
            for v in quarterly_counts(rows).values()
        )

        assert group_last_numbers(result) == expected

    # ---------- threshold/range filters ----------

    elif qid == "orders-over-amount":
        expected_rows = sorted(
            [
                r for r in rows
                if r["amount"] > 250
            ],
            key=lambda r: r["amount"],
            reverse=True,
        )

        filtered_result_truth(
            result,
            sql,
            expected_rows,
        )

    elif qid == "orders-under-amount":
        expected_rows = sorted(
            [
                r for r in rows
                if r["amount"] < 250
            ],
            key=lambda r: r["amount"],
        )

        filtered_result_truth(
            result,
            sql,
            expected_rows,
        )

    elif qid == "amount-range":
        expected_rows = sorted(
            [
                r for r in rows
                if 100 <= r["amount"] <= 500
            ],
            key=lambda r: r["amount"],
        )

        filtered_result_truth(
            result,
            sql,
            expected_rows,
        )

    elif qid == "order-range":
        start = today - timedelta(days=10)

        expected_rows = sorted(
            [
                r for r in rows
                if start <= d(r) <= today
            ],
            key=lambda r: r["created"],
        )

        filtered_result_truth(
            result,
            sql,
            expected_rows,
        )

    # ---------- ordering ----------

    elif qid == "recent-orders":
        columns = [
            str(c).lower()
            for c in result["columns"]
        ]

        assert "id" in columns, (
            qid,
            sql,
            result,
        )

        idx = columns.index("id")

        actual = [
            int(r[idx])
            for r in result["rows"]
        ]

        expected_all = [
            r["id"]
            for r in sorted(
                rows,
                key=lambda x: (x["created"], x["id"]),
                reverse=True,
            )
        ]

        assert actual == expected_all[:len(actual)]

    elif qid == "oldest-orders":
        columns = [
            str(c).lower()
            for c in result["columns"]
        ]

        assert "id" in columns, (
            qid,
            sql,
            result,
        )

        idx = columns.index("id")

        actual = [
            int(r[idx])
            for r in result["rows"]
        ]

        expected_all = [
            r["id"]
            for r in sorted(
                rows,
                key=lambda x: (x["created"], x["id"]),
            )
        ]

        assert actual == expected_all[:len(actual)]

    # ---------- daily aggregate intelligence ----------

    elif qid == "average-daily-sales":
        totals = list(daily_totals(rows).values())

        expected = sum(totals) / len(totals)

        almost(first_number(result), expected)

    elif qid == "best-sales-day":
        expected = max(
            daily_totals(rows).values()
        )

        actual = named_number(
            result,
            ["total", "sales", "answer"],
        )

        almost(actual, expected)

    elif qid == "worst-sales-day":
        expected = min(
            daily_totals(rows).values()
        )

        actual = named_number(
            result,
            ["total", "sales", "answer"],
        )

        almost(actual, expected)

    # ---------- month-over-month growth ----------

    elif qid == "growth-month":
        prev = previous_month(today)

        current_total = sum(
            r["amount"]
            for r in rows
            if d(r).year == today.year
            and d(r).month == today.month
        )

        previous_total = sum(
            r["amount"]
            for r in rows
            if d(r).year == prev.year
            and d(r).month == prev.month
        )

        expected = (
            ((current_total - previous_total) / previous_total) * 100
            if previous_total
            else None
        )

        actual = named_number(
            result,
            ["growth", "percent", "answer"],
        )

        assert expected is not None
        almost(actual, expected)

    else:
        raise AssertionError(
            f"No Python truth implementation for {qid}"
        )
