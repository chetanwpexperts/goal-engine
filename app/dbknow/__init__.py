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
ENGINES = {"mysql", "mariadb", "postgres", "sqlite", "sqlserver", "oracle", "mongodb", "redis", "db2", "cockroachdb",
           "cassandra", "dynamodb", "elasticsearch", "firebird", "clickhouse", "any"}
FILES = ("errors", "questions", "slow_rules", "structure", "issues")
CATEGORIES = {"connection", "performance", "locking", "errors", "charset_encoding", "timezone", "storage_space", "backup_restore",
              "replication", "security_permissions", "schema_design", "migration_upgrade", "config_tuning", "orm_framework",
              "data_quality", "memory", "crash_recovery", "query_writing"}

REQUIRED = {
    "errors": {"id", "engines", "code", "match", "title_en", "title_hi", "explain_en", "explain_hi", "fix", "source"},
    "questions": {"id", "intent", "examples", "needs", "templates", "source"},
    "slow_rules": {"id", "engines", "detect", "title_en", "title_hi", "fix_en", "fix_hi", "source"},
    "structure": {"id", "kind", "rule", "explain_en", "explain_hi", "source"},
    # A playbook: how people describe it -> what the machine checks itself -> causes -> fixes -> how to prove it's fixed
    "issues": {"id", "title_en", "title_hi", "engines", "category", "symptoms", "diagnose", "causes", "fixes", "verify", "source"},
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
            if name in ("issues", "questions"):
                tps = e.get("test_phrases", [])
                if len(tps) < 2:
                    problems.append(f"{where}: needs at least 2 test_phrases (new ways of saying it, not in symptoms/examples)")
                learned = {x.strip().lower() for x in e.get("symptoms", []) + e.get("examples", [])}
                if any(t.strip().lower() in learned for t in tps):
                    problems.append(f"{where}: test_phrases must be different from the learned sentences")
            if name == "issues":
                if e["category"] not in CATEGORIES:
                    problems.append(f"{where}: category must be one of {sorted(CATEGORIES)}")
                if len(e["symptoms"]) < 3:
                    problems.append(f"{where}: give at least 3 ways people describe it (English and Hinglish)")
                cause_ids = {c.get("id") for c in e["causes"]}
                check_ids = set()
                for d in e["diagnose"]:
                    if d.get("sql") and not re.match(r"^\s*(select|with|show|explain)\b", d["sql"], re.I):
                        problems.append(f"{where}: diagnose queries must be read-only")
                    if d.get("sql") and not d.get("id"):
                        problems.append(f"{where}: every automatic check needs an id")
                    if d.get("id") and d.get("id") in check_ids:
                        problems.append(f"{where}: duplicate check id {d.get('id')}")
                    if d.get("id"):
                        check_ids.add(d["id"])
                for cause in e["causes"]:
                    for signal in cause.get("signals", []):
                        if signal.get("check") not in check_ids:
                            problems.append(f"{where}: signal points to unknown check {signal.get('check')}")
                        if signal.get("manual"):
                            if not str(signal.get("evidence", "")).strip():
                                problems.append(f"{where}: manual signal needs evidence text")
                            continue
                        if signal.get("op") not in ("==", "!=", ">", ">=", "<", "<=", "contains", "in"):
                            problems.append(f"{where}: unknown signal operator {signal.get('op')}")
                for f in e["fixes"]:
                    if f.get("cause") not in cause_ids:
                        problems.append(f"{where}: fix points to unknown cause {f.get('cause')}")
                    if f.get("action") not in ("approved_change", "config", "advice", "code"):
                        problems.append(f"{where}: fix.action must be approved_change/config/advice/code")
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
