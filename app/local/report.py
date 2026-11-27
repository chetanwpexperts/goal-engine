"""The database report: a health score and sections, worst first (docs/dbaba-local-contract.md, GET /api/report).

Everything is read-only. A fix is only ever a suggestion with needs_approval: true; nothing here changes the database.
Each check reads the server's own catalog and statistics (lab-run on MariaDB 10.11 and PostgreSQL 16, tests/s5lab.py):
  now       connections against the limit; queries running over a minute; who waits on whom (lock waits); deadlocks
  slow      the queries that take the most time (performance_schema / pg_stat_statements), each read by DBaba's query
            checker against the real table definitions, with the engine's own proof of a full scan
  indexes   redundant and duplicate indexes, unused ones (after 7 days of counting), foreign keys without an index,
            big tables read without an index
  tables    no primary key, wasted space / dead rows, stale statistics, the biggest tables
  settings  memory for data against this machine's memory, connections near the limit, the slow log
  backups   point-in-time restore possible or not; DBaba's own backups come with S6
  security  DBaba's user can change data, remote root, empty passwords, anonymous users, open network, old versions
The judging lives in app/local/checks.py; each report is compared with the one before (what's new, what's gone).
"""

import datetime as dt
import html
import json

from app.local import dbconn

SECTIONS = [("now", "Problems right now"), ("slow", "Slow queries"), ("indexes", "Indexes"), ("tables", "Tables"),
            ("settings", "Settings"), ("backups", "Backups"), ("security", "Security"), ("server", "Server")]
ORDER = {"critical": 0, "warning": 1, "info": 2}


def _item(iid, severity, title, what, why, sql=None):
    return {"id": iid, "severity": severity, "title": title, "what": what, "why": why,
            "fix": {"sql": sql, "needs_approval": True} if sql else None}


def _connections(conn, engine):
    if engine == "postgresql":
        used = int(conn.rows("SELECT count(*) FROM pg_stat_activity")[1][0][0])
        limit = int(conn.rows("SHOW max_connections")[1][0][0])
    else:
        status = dict(conn.rows("SHOW GLOBAL STATUS LIKE 'Threads_connected'")[1])
        used = int(status.get("Threads_connected", 0))
        limit = int(dict(conn.rows("SHOW VARIABLES LIKE 'max_connections'")[1]).get("max_connections", 0))
    return used, limit


def _long_running(conn, engine, seconds=60):
    if engine == "postgresql":
        rows = conn.rows("SELECT pid, usename, EXTRACT(EPOCH FROM now() - query_start)::int, query FROM pg_stat_activity "
                         "WHERE state = 'active' AND pid <> pg_backend_pid() AND now() - query_start > %s * interval '1 second' "
                         "ORDER BY query_start LIMIT 10", (seconds,))[1]
    else:
        rows = conn.rows("SELECT ID, USER, TIME, INFO FROM information_schema.PROCESSLIST WHERE COMMAND <> 'Sleep' "
                         "AND ID <> CONNECTION_ID() AND INFO IS NOT NULL AND TIME > %s ORDER BY TIME DESC LIMIT 10", (seconds,))[1]
    return rows


def scan(db: dict, thresholds: dict | None = None) -> dict:
    """Run the checks; the database can't be reached -> one critical item saying why."""
    engine = db["type"]
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    items: dict[str, list] = {k: [] for k, _ in SECTIONS}
    try:
        conn = dbconn.open_db(db)
    except dbconn.ConnectError as exc:
        items["now"].append(_item("unreachable", "critical", "DBaba can't reach the database", str(exc),
                                  "Nothing else can be checked until DBaba can connect."))
        return _finish(now, engine, items, version="")
    try:
        version = conn.version()
        used, limit = _connections(conn, engine)
        if limit:
            share = used / limit
            sev = "critical" if share >= 0.95 else "warning" if share >= 0.8 else None
            if sev:
                items["now"].append(_item("connections", sev, f"{used} of {limit} connections in use",
                                          f"The server allows {limit} connections at once and {used} are open.",
                                          "When the limit is reached, new visitors and jobs are refused ('Too many connections')."))
        for qid, user, secs, text in _long_running(conn, engine):
            kill = f"SELECT pg_cancel_backend({int(qid)});" if engine == "postgresql" else f"KILL QUERY {int(qid)};"
            items["now"].append(_item(f"long_{qid}", "warning", f"A query has run for {int(secs)} s",
                                      f"User {user} is running: {dbconn.gc.shape(text)[:200]}",
                                      "A long query can hold locks that make other queries wait.", kill))
        schema = conn.schema()["tables"]
        if engine == "postgresql":           # the connector's PostgreSQL schema has no key flags: ask pg_constraint
            nopk = {r[0] for r in conn.rows(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' "
                "AND n.nspname = current_schema() AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = c.oid "
                "AND k.contype = 'p')")[1]}
        else:
            nopk = {t["name"] for t in schema if not any(c.get("key") == "PRI" for c in t["columns"])}
        if True:
            for t in schema:
                if t["name"] in nopk:
                    add = (f"ALTER TABLE {t['name']} ADD COLUMN id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY;"
                           if engine == "postgresql" else f"ALTER TABLE {t['name']} ADD COLUMN id BIGINT AUTO_INCREMENT PRIMARY KEY;")
                    items["tables"].append(_item(f"nopk_{t['name']}", "warning", f"{t['name']} has no primary key",
                                                 f"{t['name']} ({t['rows']:,} rows) has no column that names each row uniquely.",
                                                 "Without one, duplicate rows creep in, replication and some tools slow down, "
                                                 "and one row can't be changed or deleted safely.", add))
        health = conn.health()
        for k, c in enumerate(health.get("checks", [])):
            if c.get("level") == "warn" and "primary key" not in c["text"]:
                if "slow quer" in c["text"]:          # the server's own count: background for the slow-query section
                    items["slow"].append(_item(f"health_{k}", "info", c["text"], c["text"],
                                               "The server's own count of statements over long_query_time; the ones that "
                                               "cost the most are listed here."))
                    continue
                section = "indexes" if "index" in c["text"] else "server"
                items[section].append(_item(f"health_{k}", "warning", c["text"], c["text"],
                                            "A big table read without an index makes every such query read all its rows."
                                            if section == "indexes" else "Worth a look on this server."))
        if conn.can_write():
            items["security"].append(_item("user_writes", "warning", "DBaba's database user can change data",
                                           f"The user '{db.get('user')}' has rights to change or delete rows.",
                                           "DBaba only reads, so a read-only user (SELECT only) means a mistake or a stolen "
                                           "password can't change anything."))
        _deep(conn, engine, db, items, thresholds)
        old = _support(engine, version)
        if old:
            items["security"].append(old)
        items["server"].append(_item("version", "info", f"{'PostgreSQL' if engine == 'postgresql' else 'MariaDB' if 'mariadb' in version.lower() else 'MySQL'} {version.split('-')[0].split(' ')[0]}",
                                     f"Server version {version}; {len(schema)} tables in {db.get('name')}.", "For reference."))
    finally:
        dbconn.close(conn)
    return _finish(now, engine, items, version=version)


def _deep(conn, engine, db, items, thresholds=None):
    """S5: the full checks (app/local/checks.py); a failure here never hides the rest of the report."""
    from app.local import checks

    try:
        g = checks.gather(conn, engine)
        slow_checks = {}
        for row in (g.get("slow") or [])[:60] if isinstance(g.get("slow"), list) else []:
            text = str(row[0] or "")
            if not checks.user_query(text) or len(slow_checks) >= ((thresholds or {}).get("slow_top") or checks.SLOW_TOP):
                continue
            slow_checks[text] = _check_query(conn, engine, text)
        found = checks.judge(g, engine, db, checks.memory(), slow_checks, **(thresholds or {}))
    except Exception as exc:  # noqa: BLE001
        items["server"].append(_item("deep_failed", "info", "Some checks couldn't run",
                                     f"{type(exc).__name__}: {str(exc)[:200]}", "The rest of the report is still right."))
        return
    for sec, its in found.items():
        items.setdefault(sec, []).extend(its)


def _check_query(conn, engine, text):
    """DBaba's own query checker on a slow statement, with the real definitions of the tables it names."""
    from app.agents.sql import analyze_sql
    from app.local import checks

    ddl = checks.structure(conn, engine, checks.TABLES_RX.findall(text))
    import re as _re

    # PostgreSQL hides even the part EXTRACT takes (EXTRACT($1 FROM d)); any part keeps the column wrapped in a function,
    # which is what the checker judges, so it reads it with one named part
    readable = _re.sub(r"(?i)\bextract\s*\(\s*\$\d+\s+from\b", "EXTRACT(YEAR FROM", text)
    if engine != "postgresql":
        readable = checks.tidy(readable)
    try:
        r = analyze_sql(readable, extra=ddl, engine=engine)
    except Exception:  # noqa: BLE001 - a statement the checker can't read is still listed, with its numbers
        r = {}
    idx = [i for i in (r.get("indexes") or []) if isinstance(i, str)]
    return {"findings": [f for f in r.get("findings") or [] if f.get("title_en")], "plan": checks.plan(conn, engine, text),
            "index_sql": idx[0] if idx else None}


def _support(engine, version):
    """A version whose maker no longer fixes it (dialect file 'support_ended', from the makers' published policies)."""
    import re as _re

    from app.dbknow import engines

    eid = "mariadb" if "mariadb" in version.lower() else engine
    try:
        ended = engines.dialect(eid).get("support_ended") or {}
    except OSError:
        return None
    m = _re.match(r"(\d+)\.(\d+)", version)
    if not m:
        return None
    major = m.group(1) if eid == "postgresql" else f"{m.group(1)}.{m.group(2)}"
    for prefix, when in (ended.get("versions") or {}).items():
        if major == prefix:
            return _item("old_version", "warning", f"Version {major} no longer gets security fixes",
                         f"Its maker stopped fixing {major} on {when}.",
                         "New security holes in it will never be closed. Plan an upgrade to a supported version "
                         f"({ended.get('source', '')}).")
    return None


def compare(new: dict, old: dict | None) -> dict:
    """What changed since the last report: items that are new, items that are gone, and the score before."""
    if not old:
        return {"first": True}
    ids = lambda r: {i["id"]: i["title"] for s in r.get("sections", []) for i in s["items"] if i["severity"] != "info"}  # noqa: E731
    a, b = ids(old), ids(new)
    return {"first": False, "score_before": old.get("score"), "at_before": old.get("at"),
            "new": [b[k] for k in b if k not in a][:20], "gone": [a[k] for k in a if k not in b][:20]}


def _finish(at, engine, items, version):
    sections = []
    crit = warn = 0
    for sid, title in SECTIONS:
        its = sorted(items[sid], key=lambda i: ORDER[i["severity"]])
        crit += sum(i["severity"] == "critical" for i in its)
        warn += sum(i["severity"] == "warning" for i in its)
        if its:
            sections.append({"id": sid, "title": title, "items": its})
    sections.sort(key=lambda s: min(ORDER[i["severity"]] for i in s["items"]))
    score = max(0, 100 - 25 * crit - 5 * warn)
    return {"at": at, "score": score, "engine": engine, "version": version, "sections": sections}


def as_html(report: dict) -> str:
    e = html.escape
    rows = []
    for s in report.get("sections", []):
        rows.append(f"<h2>{e(s['title'])}</h2>")
        for i in s["items"]:
            fix = f"<pre>{e(i['fix']['sql'])}</pre><p class=note>Suggestion only: DBaba never runs this without your approval.</p>" if i.get("fix") else ""
            rows.append(f"<div class='item {e(i['severity'])}'><h3>{e(i['severity'].upper())}: {e(i['title'])}</h3>"
                        f"<p>{e(i['what'])}</p><p><b>Why it matters:</b> {e(i['why'])}</p>{fix}</div>")
    return ("<!doctype html><html><head><meta charset=utf-8><title>DBaba report</title><style>"
            "body{font:15px system-ui,sans-serif;max-width:860px;margin:2em auto;padding:0 16px;color:#222}"
            ".item{border-left:4px solid #999;padding:4px 12px;margin:10px 0}.critical{border-color:#c62828}"
            ".warning{border-color:#ef8f00}.info{border-color:#5b4fd6}pre{background:#f4f4f6;padding:8px;overflow:auto}"
            ".note{color:#666;font-size:13px}</style></head><body>"
            f"<h1>DBaba report</h1><p>Score <b>{int(report.get('score', 0))}/100</b> · {e(str(report.get('engine')))} "
            f"· {e(str(report.get('at')))}</p>" + "".join(rows) + "</body></html>")


def save(path, report: dict) -> None:
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd, "w") as fh:
        json.dump(report, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
