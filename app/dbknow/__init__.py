"""Goal Engine's own database knowledge: the 'brain' that lets the database agent solve problems without AI.

Plain JSON files, grown a little every night from official documentation (see docs/db-knowledge/README.md):

  errors.json      database error -> what it means in plain words -> automatic fix or advice
  questions.json   everyday questions ("pichle 3 mahine ke orders") -> tested query templates per database
  slow_rules.json  slow-query patterns -> why it's slow -> the fix
  structure.json   rules for reading a database without documentation (guessing links, spotting bad types)

Every entry names its official source. tests/test_dbknow.py checks every file on every change.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINES = {"mysql", "mariadb", "postgres", "sqlite", "sqlserver", "oracle"}
FILES = ("errors", "questions", "slow_rules", "structure")

REQUIRED = {
    "errors": {"id", "engines", "code", "match", "title_en", "title_hi", "explain_en", "explain_hi", "fix", "source"},
    "questions": {"id", "intent", "examples", "needs", "templates", "source"},
    "slow_rules": {"id", "engines", "detect", "title_en", "title_hi", "fix_en", "fix_hi", "source"},
    "structure": {"id", "kind", "rule", "explain_en", "explain_hi", "source"},
}
FIX_TYPES = {"advice", "closest_column", "closest_table", "qualify_column", "retry", "add_group_by", "cast", "limit"}


@lru_cache(maxsize=None)
def load(name: str) -> list[dict]:
    path = HERE / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def validate() -> list[str]:
    """All problems found in the knowledge files (empty list = all good)."""
    problems = []
    for name in FILES:
        seen = set()
        for i, e in enumerate(load(name)):
            where = f"{name}[{i}] {e.get('id', '?')}"
            missing = REQUIRED[name] - set(e)
            if missing:
                problems.append(f"{where}: missing {sorted(missing)}")
                continue
            if e["id"] in seen:
                problems.append(f"{where}: duplicate id")
            seen.add(e["id"])
            if not str(e["source"]).startswith("https://"):
                problems.append(f"{where}: source must be an https link to official docs")
            for link in e.get("seen_at", []):
                if not str(link).startswith("https://"):
                    problems.append(f"{where}: seen_at links must be https")
            if "license" in e and str(e["license"]).upper().startswith(("GPL", "AGPL", "LGPL")):
                problems.append(f"{where}: nothing may be copied from GPL-family projects")
            for eng in e.get("engines", []):
                if eng not in ENGINES:
                    problems.append(f"{where}: unknown engine {eng}")
            for key in ("match", "detect"):
                if key in e:
                    try:
                        re.compile(e[key], re.I | re.S)
                    except re.error as exc:
                        problems.append(f"{where}: bad regex in {key}: {exc}")
            if name == "errors" and e["fix"].get("type") not in FIX_TYPES:
                problems.append(f"{where}: fix.type must be one of {sorted(FIX_TYPES)}")
            if name == "questions":
                for eng, tpl in e["templates"].items():
                    if eng not in ENGINES:
                        problems.append(f"{where}: unknown engine {eng} in templates")
                    if not re.match(r"^\s*(select|with)\b", tpl, re.I):
                        problems.append(f"{where}: template for {eng} must be a read-only SELECT")
    return problems


def find_error(engine: str, message: str) -> dict | None:
    for e in load("errors"):
        if engine in e["engines"] and re.search(e["match"], message or "", re.I | re.S):
            return e
    return None


def slow_findings(engine: str, sql: str) -> list[dict]:
    return [r for r in load("slow_rules") if engine in r["engines"] and re.search(r["detect"], sql or "", re.I | re.S)]


def stats() -> dict:
    """For the home screen: how much the database agent knows, and when it last learned."""
    total = sum(len(load(n)) for n in FILES)
    updated = ""
    log = HERE.parents[1] / "docs" / "db-knowledge" / "LOG.md"
    if log.exists():
        m = re.search(r"^## (\d{4}-\d{2}-\d{2})", log.read_text(encoding="utf-8"), re.M)
        updated = m.group(1) if m else ""
    return {"entries": total, "updated": updated}
