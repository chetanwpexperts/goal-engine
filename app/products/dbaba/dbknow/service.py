"""The database brain at work: from a problem to checks, causes, proven fixes. Knowledge stays here;
connectors only run the read-only checks we send."""

from app import dbknow
from app.agents import database as dbagent
from app.agents.sql import analyze_sql

ENGINE_OF = {"mysql": {"mysql", "mariadb"}, "mariadb": {"mysql", "mariadb"}, "postgres": {"postgres"}, "sqlite": {"sqlite"}}


def playbook(issue_id: str) -> dict | None:
    return next((i for i in dbknow.load("issues") if i["id"] == issue_id), None)


def error_entry(error_id: str) -> dict | None:
    return next((e for e in dbknow.load("errors") if e["id"] == error_id), None)


def checks_for(issue: dict, db_type: str) -> list[dict]:
    engines = ENGINE_OF.get(db_type or "mysql", {db_type})
    return [d for d in issue["diagnose"] if d.get("engine") in engines and d.get("sql")]


def start_checks(db, connector, issue_id: str):
    issue = playbook(issue_id)
    if issue is None:
        raise dbagent.DatabaseError("Unknown problem")
    checks = checks_for(issue, connector.db_type)
    if not checks:
        raise dbagent.DatabaseError("No automatic checks for this database type yet; the fixes below still apply")
    job = dbagent.queue(db, connector, "checks", {"queries": [c["sql"] for c in checks]}, summary=f"Checks · {issue['title_en']}")
    return job, issue, checks


def index_for_engine(stmt: str, db_type: str) -> str:
    """The SQL checker writes MySQL style; PostgreSQL/SQLite need CREATE INDEX."""
    from app.agents.direct import gc

    ix = gc.parse_index(stmt)
    if ix is None:
        return stmt
    if db_type in ("postgres", "sqlite"):
        return f"CREATE INDEX {ix['name']} ON {ix['table']} ({', '.join(ix['columns'])})"
    return f"ALTER TABLE {ix['table']} ADD INDEX {ix['name']} ({', '.join(ix['columns'])})"


def start_optimize(db, connector, sql: str):
    from app.agents.direct import gc

    analysis = analyze_sql(sql)
    if not analysis.get("ok"):
        raise dbagent.DatabaseError(analysis.get("error") or "Couldn't read that query")
    original = dbagent.read_only_sql(sql)
    rewrites = []
    rewritten = (analysis.get("rewritten") or "").strip().rstrip(";")
    if rewritten and rewritten.lower() != original.lower():
        try:
            rewrites.append(dbagent.read_only_sql(rewritten))
        except dbagent.DatabaseError:
            pass
    indexes = [index_for_engine(i.rstrip(";"), connector.db_type) for i in analysis.get("indexes", [])]
    tables = sorted({gc.parse_index(i)["table"] for i in indexes if gc.parse_index(i)})
    job = dbagent.queue(db, connector, "optimize", {"sql": original, "rewrites": rewrites, "index_tables": tables},
                        summary=f"Query test · {dbagent.normalize_sql(original)}")
    extra = [{"title_en": r["title_en"], "title_hi": r["title_hi"], "fix_en": r["fix_en"], "fix_hi": r["fix_hi"]}
             for r in dbknow.slow_findings(connector.db_type or "mysql", original)]
    return job, {"findings": analysis["findings"], "extra": extra, "rewritten": rewrites[0] if rewrites else None, "indexes": indexes}
