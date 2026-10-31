"""Rules the local model drafted and the engines proved (tools/knowledge/harvest.py): each one must find its own broken
query, not its fixed one, and never fire on hundreds of valid generated queries. A bad rule fails here, before deploy."""

import json
import random
import re
from pathlib import Path

from app.agents import sqlcorrect
from tests.fuzz import db as dbfuzz

RULES = json.loads((Path(__file__).parents[1] / "app" / "knowledge" / "sql_rules" / "learned.json").read_text())


def _valid():
    out = []
    for seed in range(1, 300):
        schema, rnd = dbfuzz.make_schema(seed), random.Random(seed)
        out += [dbfuzz.make_query(schema, rnd)[0] for _ in range(3)]
    return out


def test_every_learned_rule_is_proved_and_its_pattern_is_safe():
    valid = _valid()
    for r in RULES:
        assert r["engines"] and r["bad_sql"] and r["fixed_sql"], r.get("id")
        assert all(k in ("mysql", "mariadb", "postgres") for k in r["engines"]), r["id"]
        if r.get("detect"):
            rx = re.compile(r["detect"], re.I | re.S)
            assert rx.search(r["bad_sql"]) and not rx.search(r["fixed_sql"]), r["id"]
            hits = [v for v in valid if rx.search(v)]
            assert not hits, (r["id"], r["title"], hits[:2])


def test_a_learned_rule_shows_up_in_the_check(monkeypatch):
    rule = {"title": "Aggregate in WHERE", "why": "WHERE runs before grouping.", "fix": "Put it in HAVING.",
            "detect": r"where[^;]*\b(sum|count|avg|min|max)\s*\(", "fixed_sql": "SELECT 1",
            "engines": {"mysql": {"code": 1111, "message": "Invalid use of group function"}, "mariadb": {"code": 1111, "message": "Invalid use of group function"},
                        "postgres": {"code": "42803", "message": "aggregate functions are not allowed in WHERE"}}}
    monkeypatch.setattr(sqlcorrect, "_LEARNED", [{**rule, "_rx": re.compile(rule["detect"], re.I | re.S)}])
    r = sqlcorrect.check("SELECT o.customer_id FROM orders o WHERE SUM(o.total) > 100 GROUP BY o.customer_id")
    f = next(x for x in r["findings"] if x["topic"] == "learned")
    assert f["kind"] == "error" and "Invalid use of group function" in f["title_en"] and "PostgreSQL error 42803" in f["fix_en"]
