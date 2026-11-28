"""S5: the full database report's checks. Two halves, kept apart so each can be proved:

* gather(conn, engine): read-only reads of the engine's own catalog and statistics (lab-run on MariaDB 10.11 with
  performance_schema on and off, PostgreSQL 16 with pg_stat_statements on and off; tests/s5lab.py plants each problem).
  Anything this user may not read comes back as "not readable", never as "fine".
* judge(g, engine, db, ram): pure reasoning over what was gathered -> report items. Every threshold says why it is there.

What the lab showed (and the judging follows):
* MariaDB's statement statistics are dominated by data loading (INSERT ... VALUES, CREATE INDEX): only SELECT / UPDATE /
  DELETE / WITH count as "slow queries". MariaDB says per statement how many runs used no index (SUM_NO_INDEX_USED):
  the engine's own proof of a full scan. PostgreSQL 16 can plan a statement that still has $1 placeholders
  (EXPLAIN (GENERIC_PLAN)), which proves a Seq Scan where the text parses; EXTRACT($1 FROM ...) does not, and then only the
  reading of the query is reported.
* Both engines replace literal values with ? / $n, so LIKE ? may be LIKE '%x': an index on that column is never
  recommended for it (an index can't help a pattern that starts with %).
* Index usage counts start when the server (MariaDB) or the statistics (PostgreSQL) started: an unused index is only a
  warning after 7 days of counting; before that it's an info item saying how long the count covers.
* A freshly loaded InnoDB table already shows a few MB of DATA_FREE (space set aside to grow): only 100 MB and 25 % of
  the table count as wasted space.
* The read-only user usually can't read mysql.user: accounts are then "not checked", with the grant that would allow it.
"""

import datetime as dt
import re

LONG_DAYS = 7
SLOW_TOP = 5
SLOW_MS, SLOW_TOTAL_MS = 50.0, 60_000.0        # slow = 50 ms a run on average, or a minute in all since counting began


def _item(iid, severity, title, what, why, sql=None):
    return {"id": iid, "severity": severity, "title": title[:160], "what": what, "why": why,
            "fix": {"sql": sql, "needs_approval": True} if sql else None}


def _try(fn, default=None):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - not every user may read every view
        return {"_error": f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"} if default is None else default


def _err(x):
    return isinstance(x, dict) and "_error" in x


WORK = re.compile(r"^\s*(?:/\*.*?\*/\s*)?(select|update|delete|with)\b", re.I | re.S)
# the database's own catalogs and DBaba's own checks are not the person's slow queries (lab: the connector's pg_class
# query showed up, and the checker then suggested an index on pg_class)
SYSTEM = re.compile(r"(?<![\w$])(pg_catalog|pg_class|pg_namespace|pg_attribute|pg_index|pg_constraint|pg_roles|pg_settings|"
                    r"pg_database|pg_stat\w*|pg_extension|pg_indexes|pg_locks|information_schema|performance_schema|"
                    r"`?mysql`?\s*\.|`?sys`?\s*\.|version\s*\(|`version`|database\s*\(|connection_id|current_schema|"
                    r"pg_backend_pid|@@)", re.I)


def tidy(text: str) -> str:
    """MariaDB / MySQL write statement statistics with spaces inside names and calls (`h` . `id`, COUNT ( * ));
    written back the usual way, so the query checker reads it as the person wrote it (lab: the spaced form gave a false
    'the ON doesn't mention h'). Values are already ? there, so no text literal is touched."""
    t = re.sub(r"\s*\.\s*(?=[`\w*])", ".", str(text))
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"\s+\)", ")", t)
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r"\b(\w+)\s+\((?!\s*select\b)", lambda m: m.group(0) if m.group(1).upper() in ("IN", "ON", "AS", "VALUES", "FROM", "JOIN", "AND", "OR", "NOT", "EXISTS", "WHERE") else m.group(1) + "(", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def user_query(text) -> bool:
    t = str(text or "")
    return bool(WORK.match(t)) and not SYSTEM.search(t)


# ================================================================ gather
def gather(conn, engine: str) -> dict:
    return _gather_pg(conn) if engine == "postgresql" else _gather_my(conn)


def _gather_my(conn) -> dict:
    r = conn.rows
    g = {"engine_family": "mysql"}
    g["vars"] = _try(lambda: dict(r("SELECT 'innodb_buffer_pool_size', @@innodb_buffer_pool_size UNION ALL SELECT 'max_connections', "
                                    "@@max_connections UNION ALL SELECT 'performance_schema', @@performance_schema UNION ALL "
                                    "SELECT 'slow_query_log', @@slow_query_log UNION ALL SELECT 'log_bin', @@log_bin UNION ALL "
                                    "SELECT 'bind_address', @@bind_address UNION ALL SELECT 'version', @@version")[1]))
    g["status"] = _try(lambda: {k: v for k, v in r("SHOW GLOBAL STATUS WHERE Variable_name IN ('Uptime','Max_used_connections',"
                                                    "'Threads_connected','Innodb_deadlocks')")[1]})
    g["indexes"] = _try(lambda: [list(x) for x in r(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX")[1]])
    g["tables"] = _try(lambda: [list(x) for x in r(
        "SELECT TABLE_NAME, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH, DATA_FREE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'")[1]])
    g["fks"] = []                                   # InnoDB indexes every foreign key column itself
    if str((g["vars"] or {}).get("performance_schema", 0)) in ("1", "ON"):
        g["slow"] = _try(lambda: [list(x) for x in r(
            "SELECT DIGEST_TEXT, COUNT_STAR, SUM_TIMER_WAIT / 1e9, AVG_TIMER_WAIT / 1e9, SUM_ROWS_EXAMINED, SUM_ROWS_SENT, "
            "SUM_NO_INDEX_USED FROM performance_schema.events_statements_summary_by_digest WHERE SCHEMA_NAME = DATABASE() "
            "AND DIGEST_TEXT IS NOT NULL ORDER BY SUM_TIMER_WAIT DESC LIMIT 60")[1]])
        g["index_use"] = _try(lambda: [list(x) for x in r(
            "SELECT OBJECT_NAME, INDEX_NAME, COUNT_STAR FROM performance_schema.table_io_waits_summary_by_index_usage "
            "WHERE OBJECT_SCHEMA = DATABASE() AND INDEX_NAME IS NOT NULL")[1]])
    else:
        g["slow"] = g["index_use"] = None
    g["lock_waits"] = _try(lambda: [list(x) for x in r(
        "SELECT w.requesting_trx_id, rt.trx_mysql_thread_id, TIMESTAMPDIFF(SECOND, rt.trx_wait_started, NOW()), rt.trx_query, "
        "w.blocking_trx_id, bt.trx_mysql_thread_id, bt.trx_query FROM information_schema.INNODB_LOCK_WAITS w "
        "JOIN information_schema.INNODB_TRX rt ON rt.trx_id = w.requesting_trx_id "
        "JOIN information_schema.INNODB_TRX bt ON bt.trx_id = w.blocking_trx_id")[1]])
    g["accounts"] = _try(lambda: [list(x) for x in r("SELECT User, Host, authentication_string = '' AND plugin IN "
                                                     "('mysql_native_password', '') FROM mysql.user")[1]])
    return g


def _gather_pg(conn) -> dict:
    r = conn.rows
    g = {"engine_family": "postgresql"}
    g["vars"] = _try(lambda: {k: (v, u) for k, v, u in r(
        "SELECT name, setting, unit FROM pg_settings WHERE name IN ('shared_buffers', 'max_connections', 'autovacuum', "
        "'listen_addresses', 'ssl', 'password_encryption', 'archive_mode', 'wal_level', 'work_mem', 'server_version')")[1]})
    g["started"] = _try(lambda: r("SELECT now() - COALESCE(stats_reset, pg_postmaster_start_time()), deadlocks FROM pg_stat_database "
                                  "WHERE datname = current_database()")[1][0])
    g["indexes"] = _try(lambda: [list(x) for x in r(
        "SELECT c.relname, i.relname, k.n, a.attname, NOT x.indisunique FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
        "JOIN pg_class c ON c.oid = x.indrelid JOIN pg_namespace ns ON ns.oid = c.relnamespace "
        "JOIN LATERAL unnest(x.indkey) WITH ORDINALITY k(att, n) ON true "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.att WHERE ns.nspname = current_schema() "
        "ORDER BY 1, 2, 3")[1]])
    g["tables"] = _try(lambda: [list(x) for x in r(
        "SELECT relname, n_live_tup, pg_table_size(relid), pg_indexes_size(relid), n_dead_tup, n_mod_since_analyze, "
        "COALESCE(last_autovacuum, last_vacuum), COALESCE(last_autoanalyze, last_analyze) FROM pg_stat_user_tables "
        "WHERE schemaname = current_schema()")[1]])
    g["fks"] = _try(lambda: [list(x) for x in r(
        "SELECT c.conrelid::regclass::text, c.conname, array_agg(a.attname ORDER BY k.n) FROM pg_constraint c "
        "JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(att, n) ON true "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.att "
        "WHERE c.contype = 'f' AND c.connamespace = current_schema()::regnamespace GROUP BY 1, 2")[1]])
    g["index_use"] = _try(lambda: [list(x) for x in r(
        "SELECT s.relname, s.indexrelname, s.idx_scan FROM pg_stat_user_indexes s JOIN pg_index x ON x.indexrelid = s.indexrelid "
        "WHERE s.schemaname = current_schema() AND NOT x.indisunique")[1]])
    has_pss = _try(lambda: r("SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'")[1], [])
    if has_pss:
        g["slow"] = _try(lambda: [list(x) for x in r(
            "SELECT s.query, s.calls, s.total_exec_time, s.mean_exec_time, NULL, s.rows, NULL FROM pg_stat_statements s "
            "JOIN pg_database d ON d.oid = s.dbid WHERE d.datname = current_database() ORDER BY s.total_exec_time DESC LIMIT 60")[1]])
    else:
        g["slow"] = None
    g["lock_waits"] = _try(lambda: [list(x) for x in r(
        "SELECT a.pid, a.pid, EXTRACT(EPOCH FROM now() - a.query_start)::int, a.query, b.pid, b.pid, b.query "
        "FROM pg_stat_activity a JOIN LATERAL unnest(pg_blocking_pids(a.pid)) bp(pid) ON true "
        "JOIN pg_stat_activity b ON b.pid = bp.pid")[1]])
    g["accounts"] = _try(lambda: [list(x) for x in r("SELECT rolname, rolsuper FROM pg_roles WHERE rolcanlogin")[1]])
    g["ddl"] = {}
    return g


def plan(conn, engine: str, query: str) -> str | None:
    """PostgreSQL 16+: the plan of a statement that still has $n placeholders. None when it can't be planned."""
    if engine != "postgresql" or not re.match(r"^\s*(select|with)\b", query, re.I):
        return None
    try:
        return "\n".join(str(x[0]) for x in conn.rows("EXPLAIN (GENERIC_PLAN) " + query)[1])
    except Exception:  # noqa: BLE001 - older PostgreSQL, or a statement the planner can't take with placeholders
        return None


def structure(conn, engine: str, tables) -> str:
    """CREATE TABLE text for the tables a query names, so the checker knows their columns and indexes."""
    out = []
    for t in sorted(set(tables))[:6]:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]{0,63}", t):
            continue
        try:
            if engine == "postgresql":
                cols = conn.rows("SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema = "
                                 "current_schema() AND table_name = %s ORDER BY ordinal_position", (t,))[1]
                if not cols:
                    continue
                body = ",\n".join(f"  {c} {ty}{' NOT NULL' if nl == 'NO' else ''}" for c, ty, nl in cols)
                out.append(f"CREATE TABLE {t} (\n{body}\n);")
                for (idef,) in conn.rows("SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() AND tablename = %s", (t,))[1]:
                    out.append(idef + ";")
            else:
                out.append(conn.rows(f"SHOW CREATE TABLE `{t}`")[1][0][1] + ";")
        except Exception:  # noqa: BLE001
            continue
    return "\n\n".join(out)


TABLES_RX = re.compile(r"\b(?:from|join|update|into)\s+[`\"]?([A-Za-z_][A-Za-z0-9_$]*)[`\"]?", re.I)


# ================================================================ judge
def _mb(n) -> str:
    n = float(n or 0)
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def _index_map(g) -> dict:
    """{table: {index: {"cols": [...], "unique": bool}}}"""
    out: dict = {}
    if _err(g.get("indexes")) or not g.get("indexes"):
        return out
    for t, ix, _, col, non_unique in g["indexes"]:
        e = out.setdefault(t, {}).setdefault(ix, {"cols": [], "unique": not bool(non_unique)})
        e["cols"].append(col)
    return out


def judge(g: dict, engine: str, db: dict, ram: int | None = None, slow_checks=None, slow_ms=None, slow_total_ms=None,
          slow_top=None) -> dict:
    """-> {section_id: [items]}. slow_checks: {query: {"findings": [...], "plan": str|None, "index_sql": str|None}}"""
    items = {k: [] for k in ("now", "slow", "indexes", "tables", "settings", "backups", "security")}
    my = engine != "postgresql"
    v = g.get("vars") if not _err(g.get("vars")) else {}
    val = (lambda k: (v.get(k) or (None,))[0]) if not my else (lambda k: v.get(k))

    # ---- problems right now
    waits = g.get("lock_waits")
    if _err(waits):
        items["now"].append(_item("locks_unreadable", "info", "Lock waits not checked",
                                  f"This user can't read who waits on whom ({waits['_error']}).",
                                  "MySQL / MariaDB: GRANT PROCESS ON *.* TO the DBaba user. PostgreSQL: GRANT pg_monitor."))
    for k, (_, wpid, secs, wq, _, bpid, bq) in enumerate(waits or []):
        from app.products.dbaba.local.dbconn import gc

        kill = f"SELECT pg_cancel_backend({int(bpid)});" if not my else f"KILL QUERY {int(bpid)};"
        items["now"].append(_item(f"lockwait_{k}", "critical" if (secs or 0) >= 60 else "warning",
                                  f"Connection {wpid} has waited {secs or 0} s for a lock held by {bpid}",
                                  f"Waiting: {gc.shape(wq)[:160]}. Holding the lock: {gc.shape(bq)[:160] or '(an open transaction, idle)'}.",
                                  "Every query that needs those rows queues behind the holder. An open transaction left idle in "
                                  "an app or a console is the usual cause: finish (COMMIT) or end it.", kill))
    dl = (g.get("status") or {}).get("Innodb_deadlocks") if my else (g.get("started") or [None, None])[1]
    if not _err(dl) and dl not in (None, "") and int(dl) > 0:
        items["now"].append(_item("deadlocks", "warning", f"{int(dl)} deadlocks since the server started",
                                  "Two transactions each held a row the other needed; the database cancelled one of them.",
                                  "The cancelled transaction's work is lost unless the app retries it. Touch rows in the same order "
                                  "everywhere and keep transactions short; ask DBaba to read the deadlock report."))

    # ---- slow queries
    slow = g.get("slow")
    if slow is None:
        how = ("Switch on performance_schema (performance_schema = ON in the server's config, then a restart)." if my else
               "Add pg_stat_statements: shared_preload_libraries = 'pg_stat_statements' in postgresql.conf, restart, then "
               "CREATE EXTENSION pg_stat_statements; in this database.")
        items["slow"].append(_item("slow_blind", "info", "DBaba can't see which queries are slow",
                                   ("performance_schema is off on this server." if my else "pg_stat_statements isn't installed in this database."),
                                   "The database only keeps per-query timings when this is switched on. " + how))
    elif _err(slow):
        items["slow"].append(_item("slow_unreadable", "info", "Slow queries not readable", slow["_error"],
                                   "MySQL / MariaDB: GRANT SELECT ON performance_schema.* to the DBaba user. PostgreSQL: GRANT pg_read_all_stats."))
    else:
        lo, lo_total = SLOW_MS if slow_ms is None else slow_ms, SLOW_TOTAL_MS if slow_total_ms is None else slow_total_ms
        work = [s for s in slow if user_query(s[0]) and (float(s[3] or 0) >= lo or float(s[2] or 0) >= lo_total)][:slow_top or SLOW_TOP]
        if not work:
            items["slow"].append(_item("slow_none", "info", "No slow queries",
                                       f"No query of this database takes {lo:g} ms a run on average or {lo_total / 1000:g} s in all.",
                                       "Counted since the statistics started; look again after a busy day."))
        for k, (text, calls, total_ms, avg_ms, examined, sent, no_index) in enumerate(work):
            ck = (slow_checks or {}).get(text) or {}
            finds = ck.get("findings") or []
            sure = [f for f in finds if (f.get("severity") or 0) >= 3 and f.get("topic") != "index_missing"]
            # a full scan alone is not a fault (a count over every row must read every row): a warning needs a reason the
            # query checker can name; the scan is the engine's evidence, said next to it
            sev = "warning" if sure else "info"
            bits = [f"Ran {int(calls):,} times, {float(avg_ms):,.1f} ms on average, {float(total_ms) / 1000:,.1f} s in all."]
            if my and examined is not None and calls:
                bits.append(f"Reads {int(examined) // max(int(calls), 1):,} rows per run to send {int(sent or 0) // max(int(calls), 1):,}.")
            if my and no_index:
                bits.append(f"MariaDB / MySQL says {int(no_index):,} of the runs used no index at all.")
            if ck.get("plan") and "Seq Scan" in ck["plan"]:
                bits.append("PostgreSQL's plan for it reads the whole table (Seq Scan).")
            why = (" ".join(f"{f['title_en']}: {f.get('fix_en') or ''}".strip() for f in sure[:2])
                   or "It takes the most time of all queries here; nothing in its text is a known cause, so the next step "
                      "is its plan with real values (ask DBaba with the query and its EXPLAIN).")
            like_any = re.search(r"\blike\s+(\?|\$\d+)", str(text), re.I)
            sql = None                                   # values are hidden (? / $n): no change is proven for this query
            if like_any:
                bits.append("Its LIKE pattern is hidden (?): if it starts with %, no index can help; search for the start of "
                            "the text instead, or use full-text search.")
            elif ck.get("index_sql"):
                bits.append(f"An index worth testing (check with EXPLAIN on real values first): {ck['index_sql']}")
            one_line = re.sub(r"\s+", " ", str(text))[:110]
            items["slow"].append(_item(f"slow_{k}", sev, f"Slow query: {one_line}", " ".join(bits),
                                       why if why.endswith((".", ")")) else why + ".", sql))

    # ---- indexes
    imap = _index_map(g)
    _dups = []
    for t, ixs in imap.items():
        names = sorted(ixs)
        for a in names:
            ca = ixs[a]["cols"]
            for b in names:
                if a == b or ixs[a]["unique"]:
                    continue
                cb = ixs[b]["cols"]
                if ca == cb and a > b:
                    continue                              # report an exact pair once
                if len(ca) <= len(cb) and cb[:len(ca)] == ca and (ca != cb or a > b):
                    if (not my) or a != "PRIMARY":
                        same = ca == cb
                        drop = f"DROP INDEX {a};" if not my else f"ALTER TABLE {t} DROP INDEX {a};"
                        items["indexes"].append(_item(f"dup_{t}_{a}", "warning",
                                                      f"{t}.{a} {'is the same as' if same else 'is covered by'} {b}",
                                                      f"{a} ({', '.join(ca)}) {'has exactly the columns of' if same else 'is the first part of'} "
                                                      f"{b} ({', '.join(cb)}).",
                                                      "Every insert and update writes both, and both take memory; any query "
                                                      f"that can use {a} can use {b}.", drop))
                        _dups.append((t, a))
                        break
    use = g.get("index_use")
    up_days = None
    if my and not _err(g.get("status")) and g.get("status"):
        up_days = int(g["status"].get("Uptime", 0)) / 86400
    elif not my and not _err(g.get("started")) and g.get("started") and g["started"][0] is not None:
        up_days = g["started"][0].total_seconds() / 86400
    flagged = set(_dups)
    if use and not _err(use) and up_days is not None:
        unused = [(t, ix) for t, ix, n in use if int(n or 0) == 0 and ix != "PRIMARY"
                  and not (imap.get(t, {}).get(ix, {}).get("unique")) and (t, ix) not in flagged]
        if unused and up_days < LONG_DAYS:              # too young to judge: one line, not one item per index
            names = ", ".join(f"{t}.{ix}" for t, ix in unused[:8]) + (f" and {len(unused) - 8} more" if len(unused) > 8 else "")
            items["indexes"].append(_item("unused_young", "info", f"{len(unused)} indexes not used yet",
                                          f"No query has read {names}, but the counts cover only {up_days * 24:.0f} hours.",
                                          f"An index can be idle for days and still matter (a monthly report). DBaba judges "
                                          f"unused indexes after {LONG_DAYS} days of counting."))
            unused = []
        for t, ix in unused[:12]:
            cols = ", ".join(imap.get(t, {}).get(ix, {}).get("cols", []))
            if up_days >= LONG_DAYS:
                drop = f"DROP INDEX {ix};" if not my else f"ALTER TABLE {t} DROP INDEX {ix};"
                items["indexes"].append(_item(f"unused_{t}_{ix}", "warning", f"{t}.{ix} hasn't been used in {up_days:.0f} days",
                                              f"No query has read index {ix} ({cols}) since the counts started.",
                                              "An index nobody reads still slows every insert and update and takes space. "
                                              "Check monthly reports or rare jobs first: they may need it.", drop))
            else:
                items["indexes"].append(_item(f"unused_{t}_{ix}", "info", f"{t}.{ix} not used yet",
                                              f"No query has read {ix} ({cols}), but the counts cover only {up_days * 24:.0f} hours.",
                                              f"Look again after {LONG_DAYS} days of normal work before deciding anything."))
    for t, name, cols in (g.get("fks") or []) if not _err(g.get("fks")) else []:
        if not any(ix["cols"][:len(cols)] == list(cols) for ix in imap.get(t, {}).values()):
            items["indexes"].append(_item(f"fkidx_{t}_{name}", "warning", f"Foreign key {t}.{name} has no index",
                                          f"{t} ({', '.join(cols)}) points at another table, and no index starts with those columns.",
                                          "PostgreSQL doesn't index a foreign key by itself: every delete or key change in the parent "
                                          f"table reads all of {t}, and joins on it read every row.",
                                          f"CREATE INDEX CONCURRENTLY ix_{t}_{'_'.join(cols)}"[:63] + f" ON {t} ({', '.join(cols)});"))

    # ---- tables
    tabs = g.get("tables") if not _err(g.get("tables")) else []
    for row in tabs or []:
        if my:
            t, nrows, data, idx, free = row
            free, size = int(free or 0), int(data or 0) + int(idx or 0)
            if free >= 100 * 1024 ** 2 and size and free / size >= 0.25:
                items["tables"].append(_item(f"free_{t}", "warning", f"{t} has {_mb(free)} of empty space",
                                             f"{t} takes {_mb(size)}; {_mb(free)} of its file is left over from deleted rows.",
                                             "Wasted disk and slower full reads. OPTIMIZE TABLE rebuilds it (it takes time and a lock "
                                             "on older versions: run it at a quiet hour).", f"OPTIMIZE TABLE {t};"))
        else:
            t, live, size, isize, dead, mods, vac, ana = row
            live, dead, mods = int(live or 0), int(dead or 0), int(mods or 0)
            if dead >= 10_000 and dead >= 0.2 * max(live, 1):
                items["tables"].append(_item(f"dead_{t}", "warning", f"{t} has {dead:,} dead rows ({dead * 100 // max(live, 1)} % of live)",
                                             f"Updated or deleted rows of {t} are still on disk{', and it has never been vacuumed' if vac is None else ''}.",
                                             "PostgreSQL keeps old row versions until VACUUM clears them: the table grows and reads "
                                             "slow down. Check that autovacuum runs for it.", f"VACUUM (ANALYZE) {t};"))
            if mods >= 10_000 and mods >= 0.2 * max(live, 1) and not (dead >= 10_000 and dead >= 0.2 * max(live, 1)):
                items["tables"].append(_item(f"stale_{t}", "warning", f"{t}'s statistics are out of date",
                                             f"{mods:,} rows changed since {t} was last analysed.",
                                             "The planner chooses plans from these numbers: stale ones pick slow plans.", f"ANALYZE {t};"))
    big = sorted(tabs or [], key=lambda x: -(int(x[2] or 0) + int(x[3] or 0)))[:5]
    if big:
        items["tables"].append(_item("biggest", "info", "Biggest tables",
                                     "; ".join(f"{x[0]} {_mb(int(x[2] or 0) + int(x[3] or 0))} ({int(x[1] or 0):,} rows)" for x in big),
                                     "For reference: growth is tracked from one report to the next."))

    # ---- settings
    if my:
        bp = int(val("innodb_buffer_pool_size") or 0)
        if ram and bp and _local(db) and bp < ram * 0.1 and ram >= 2 * 1024 ** 3:
            items["settings"].append(_item("buffer_pool", "warning", f"InnoDB buffer pool is {_mb(bp)} on a {_mb(ram)} machine",
                                           "The buffer pool is where InnoDB keeps data in memory; it is under a tenth of this machine's memory.",
                                           "Reads go to disk that could come from memory. On a machine that runs mostly the database, "
                                           "50-70 % of memory is usual; leave room for the app if it runs here too.",
                                           f"SET GLOBAL innodb_buffer_pool_size = {int(ram * 0.5) // (128 * 1024 ** 2) * 128 * 1024 ** 2};  "
                                           f"-- {_mb(int(ram * 0.5) // (128 * 1024 ** 2) * 128 * 1024 ** 2)}; also set it in the config file"))
        mx = int(val("max_connections") or 0)
        used = int((g.get("status") or {}).get("Max_used_connections", 0) or 0) if not _err(g.get("status")) else 0
        if mx and used >= 0.85 * mx:
            items["settings"].append(_item("max_conn", "warning", f"Connections peaked at {used} of {mx}",
                                           "Since the server started, connections came within 15 % of the limit.",
                                           "At the limit new visitors get 'Too many connections'. Find who holds idle connections "
                                           "(a pool that is too big) before raising the limit."))
        if str(val("slow_query_log")) in ("0", "OFF"):
            items["settings"].append(_item("slow_log_off", "info", "The slow query log is off",
                                           "Queries slower than long_query_time are not written anywhere.",
                                           "Switch it on to keep a record of slow queries between DBaba's checks.",
                                           "SET GLOBAL slow_query_log = ON;  SET GLOBAL long_query_time = 1;"))
        if str(val("bind_address")) in ("0.0.0.0", "*", "::"):  # reading the database's setting to warn about it, not binding  # nosec B104
            items["security"].append(_item("bind_all", "warning", "The database listens on every network address",
                                           f"bind_address = {val('bind_address')}.",
                                           "Anyone who can reach this machine can try passwords. If only apps on this machine use "
                                           "it, bind to 127.0.0.1; otherwise allow only your app servers in the firewall."))
    else:
        sb = int(val("shared_buffers") or 0) * 8192
        if ram and sb and _local(db) and sb < ram * 0.1 and ram >= 2 * 1024 ** 3:
            items["settings"].append(_item("shared_buffers", "warning", f"shared_buffers is {_mb(sb)} on a {_mb(ram)} machine",
                                           "shared_buffers is PostgreSQL's own memory for data; it is under a tenth of this machine's memory.",
                                           "About 25 % of memory is the usual start (the rest helps through the OS cache). "
                                           "Changing it needs a restart.",
                                           f"ALTER SYSTEM SET shared_buffers = '{int(ram * 0.25) // 1024 ** 2}MB';  -- then restart"))
        if str(val("autovacuum")) == "off":
            items["settings"].append(_item("autovacuum_off", "critical", "autovacuum is off",
                                           "Nothing clears dead rows or refreshes statistics by itself.",
                                           "Tables keep growing, plans go stale, and in the end PostgreSQL stops writing to "
                                           "protect itself (transaction ID wraparound).", "ALTER SYSTEM SET autovacuum = on; SELECT pg_reload_conf();"))
        if str(val("listen_addresses")) in ("*", "0.0.0.0"):  # reading the database's setting to warn about it, not binding  # nosec B104
            items["security"].append(_item("listen_all", "warning", "PostgreSQL listens on every network address",
                                           f"listen_addresses = '{val('listen_addresses')}'.",
                                           "Anyone who can reach this machine can try passwords. Limit it, and keep pg_hba.conf strict."))
        if str(val("ssl")) == "off" and not _local(db) and not db.get("ssh"):      # an SSH tunnel already encrypts it
            items["security"].append(_item("ssl_off", "warning", "SSL is off",
                                           "Passwords and data cross the network unencrypted.", "Turn on ssl for connections from other machines."))
        if str(val("password_encryption")) == "md5":
            items["security"].append(_item("md5", "warning", "Passwords are stored with md5",
                                           "password_encryption = md5.", "Use scram-sha-256: md5 is the old, weaker method.",
                                           "ALTER SYSTEM SET password_encryption = 'scram-sha-256';  -- then set each password again"))

    if not _local(db):
        items["settings"].append(_remote_note(db))

    # ---- backups
    if my:
        if str(val("log_bin")) in ("0", "OFF"):
            items["backups"].append(_item("no_binlog", "info", "No binary log: a restore can only go back to the last backup",
                                          "log_bin is off.", "With the binary log on, a backup plus the log restores to any minute "
                                                             "(point-in-time). It needs a restart and some disk."))
    else:
        if str(val("archive_mode")) == "off":
            items["backups"].append(_item("no_archive", "info", "No WAL archive: a restore can only go back to the last backup",
                                          "archive_mode is off.", "With WAL archiving, a base backup plus the archive restores to any "
                                                                 "minute (point-in-time). It needs a restart and a place to keep the files."))
    items["backups"].append(_item("backup_unknown", "info", "DBaba can't see your backups yet",
                                  "Backups live outside the database (files, a hosting panel, snapshots).",
                                  "A backup counts only once a restore of it has worked. DBaba's daily backup with a test restore "
                                  "comes next (S6)."))

    # ---- security
    acc = g.get("accounts")
    if _err(acc):
        items["security"].append(_item("accounts_unreadable", "info", "Accounts not checked",
                                       f"This user can't read the account list ({acc['_error']}).",
                                       "To let DBaba check for remote root, empty passwords and anonymous users: "
                                       + ("GRANT SELECT ON mysql.user TO the DBaba user." if my else "it needs pg_read_all_settings.")))
    elif my:
        for user, host, empty in acc or []:
            if user == "root" and host not in ("localhost", "127.0.0.1", "::1"):
                items["security"].append(_item(f"remote_root_{host}", "critical", f"root may log in from {host}",
                                               f"The account 'root'@'{host}' exists.",
                                               "The all-powerful account is open to other machines: the first thing attackers try.",
                                               f"DROP USER 'root'@'{host}';"))
            if user == "":
                items["security"].append(_item(f"anon_{host}", "critical", f"An anonymous account exists (''@'{host}')",
                                               "Anyone can log in with no user name.", "Remove it.", f"DROP USER ''@'{host}';"))
            elif empty:
                items["security"].append(_item(f"nopass_{user}_{host}", "critical", f"'{user}'@'{host}' has no password",
                                               "This account logs in with an empty password.", "Set a strong password or remove it.",
                                               f"ALTER USER '{user}'@'{host}' IDENTIFIED BY '<a strong password>';"))
    else:
        supers = [u for u, s in acc or [] if s]
        if len(supers) > 1:
            items["security"].append(_item("supers", "warning", f"{len(supers)} superuser accounts can log in",
                                           ", ".join(supers) + ".", "Keep one; give apps only the rights they need."))
    return items


def _local(db: dict) -> bool:
    """The database runs on the machine DBaba runs on, so this machine's memory is the database's memory. Through an SSH
    tunnel the address is 127.0.0.1 too, but the database is on the SSH server: not local."""
    return not db.get("ssh") and (db.get("host") or "127.0.0.1") in ("127.0.0.1", "localhost", "::1")


def _remote_note(db: dict) -> dict:
    via = "through SSH" if db.get("ssh") else "over the network"
    return _item("memory_unseen", "info", "Memory settings not compared with the server's memory",
                 f"DBaba reaches this database {via} from another computer, so it can't see how much memory the server has.",
                 "The memory advice (buffer pool / shared_buffers against the machine's memory) needs DBaba on the database "
                 "server itself: install it there to have it checked.")


def memory() -> int | None:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None
