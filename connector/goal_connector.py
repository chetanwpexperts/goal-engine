#!/usr/bin/env python3
"""Goal Engine database connector. Runs on YOUR server, next to your database.

  python3 goal_connector.py setup --server https://solve.chetanbuilds.com --token gec_...
  python3 goal_connector.py run

* Your database password is saved only in goal_connector.json on this server (chmod 600).
  It is never sent to Goal Engine.
* The connector only calls out (HTTPS). It opens no port, so nothing can connect to it.
* Read-only: every query is checked here as well, runs in a read-only transaction, and
  writes are refused unless you yourself set "allow_write": true in the config file.
* Stop it any time with Ctrl+C, or disconnect it from the Goal Engine screen.
"""

import argparse
import csv
import datetime as dt
import decimal
import getpass
import io
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request

VERSION = "1.0"
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "goal_connector.json")
MAX_EXPORT_ROWS = 100_000
MAX_EXPORT_BYTES = 20 * 1024 * 1024

READ_START = re.compile(r"^\s*(select|with|show|describe|desc|explain)\b", re.I)
WRITE_WORDS = re.compile(r"\b(insert|update|delete|replace|merge|drop|alter|create|truncate|rename|grant|revoke|lock|unlock|"
                         r"call|exec|execute|handler|load_file|sleep|benchmark|pg_sleep|copy|vacuum|set|into\s+outfile|into\s+dumpfile)\b", re.I)
STRINGS = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`")
COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*|#[^\n]*", re.S)


def safe_sql(sql, allow_write=False):
    sql = (sql or "").strip().rstrip(";").strip()
    bare = COMMENTS.sub(" ", STRINGS.sub("''", sql))
    if not sql or ";" in bare:
        raise ValueError("One statement at a time, please")
    if allow_write:
        return sql
    if not READ_START.match(bare) or WRITE_WORDS.search(bare):
        raise ValueError("This connector is read-only; that query would change data")
    return sql


# ---------------------------------------------------------------- database drivers
# ---------------------------------------------------------------- database drivers
# Each database type is one small class. To support a new database (SQL Server, Oracle, ...),
# add a Driver subclass and register it in DRIVERS; the jobs, safety checks and Goal Engine
# itself don't change.
class Driver:
    name = ""
    default_port = 0
    explain_prefix = "EXPLAIN "
    version_sql = "SELECT 1"
    has_primary_keys_in_schema = True

    def __init__(self, cfg):
        self.cfg = cfg
        self.kind = cfg["type"]
        self.conn = None

    # --- per database
    def open(self):
        raise NotImplementedError

    def start_read_only(self, cur):
        """Put the session/transaction in read-only mode with a time limit."""

    def alive(self):
        return True

    def write_rights(self):
        return False

    def tables(self):
        """[(name, rows, bytes)] and [(table, column, type, key)]"""
        raise NotImplementedError

    def server_checks(self, out):
        """Extra health checks this database can do."""

    # --- shared by every database
    def connect(self):
        self.conn = self.open()
        return self

    def ensure(self):
        try:
            if not self.alive():
                self.connect()
        except Exception:  # noqa: BLE001
            self.connect()

    def cursor(self, read_only=True):
        self.ensure()
        cur = self.conn.cursor()
        if read_only:
            self.start_read_only(cur)
        return cur

    def done(self):
        try:
            self.conn.rollback()
        except Exception:  # noqa: BLE001
            pass

    def rows(self, sql, params=(), limit=None):
        cur = self.cursor()
        try:
            cur.execute(sql, params) if params else cur.execute(sql)
            cols = [d[0] for d in cur.description or []]
            data = cur.fetchmany(limit) if limit else cur.fetchall()
            return cols, [list(r) for r in data]
        finally:
            cur.close()
            self.done()

    def version(self):
        return str(self.rows(self.version_sql)[1][0][0])

    def can_write(self):
        """Does this database user have write rights? (We still never write.)"""
        try:
            return bool(self.write_rights())
        except Exception:  # noqa: BLE001
            return False

    def schema(self):
        rows, cols = self.tables()
        tables = {}
        for name, count, size in rows:
            tables[name] = {"name": name, "rows": int(count or 0), "bytes": int(size or 0), "columns": []}
        for t, c, typ, key in cols:
            if t in tables:
                tables[t]["columns"].append({"name": c, "type": str(typ), "key": str(key or "")})
        return {"tables": list(tables.values())}

    def query(self, sql, limit):
        cols, rows = self.rows(safe_sql(sql), limit=limit)
        hidden = secret_columns(cols)
        return {"columns": cols, "rows": [mask_row([clean(v) for v in r], hidden) for r in rows], "row_count": len(rows),
                "limited": len(rows) >= limit, "hidden_columns": [cols[i] for i in hidden]}

    def export(self, sql):
        """Whole CSV as text (small exports and tests)."""
        buf = io.StringIO()
        info = self._export_into(sql, buf, lambda: buf.tell())
        return {"csv": "\ufeff" + buf.getvalue(), **info}

    def export_file(self, sql, path):
        """Stream the CSV straight into a gzip file, a few thousand rows at a time: memory stays small."""
        import gzip

        raw = open(path, "wb")
        try:
            with gzip.open(raw, "wt", encoding="utf-8", newline="") as fh:
                fh.write("\ufeff")
                info = self._export_into(sql, fh, lambda: raw.tell() * 4)   # ~4x compression: stop near the size limit
        finally:
            raw.close()
        return info

    def _export_into(self, sql, out, size):
        sql = safe_sql(sql)
        cur = self.cursor()
        writer = csv.writer(out)
        count = 0
        try:
            cur.execute(sql)
            cols = [d[0] for d in cur.description or []]
            hidden = secret_columns(cols)
            writer.writerow(cols)
            while True:
                batch = cur.fetchmany(2000)
                if not batch:
                    break
                for r in batch:
                    writer.writerow(mask_row([clean(v) for v in r], hidden))
                    count += 1
                if count >= MAX_EXPORT_ROWS or size() > MAX_EXPORT_BYTES:
                    break
        finally:
            cur.close()
            self.done()
        return {"row_count": count, "limited": count >= MAX_EXPORT_ROWS or size() > MAX_EXPORT_BYTES}

    def explain(self, sql):
        sql = safe_sql(sql)
        sql = re.sub(r"^\s*explain\s+", "", sql, flags=re.I)
        cols, rows = self.rows(self.explain_prefix + sql)
        return {"columns": cols, "rows": [[clean(v) for v in r] for r in rows], "row_count": len(rows)}

    def health(self):
        out = {"version": self.version(), "checks": []}
        schema = self.schema()["tables"]
        biggest = sorted(schema, key=lambda t: t["bytes"] or t["rows"], reverse=True)[:5]
        out["biggest"] = [{"name": t["name"], "rows": t["rows"], "bytes": t["bytes"]} for t in biggest]
        no_key = [t["name"] for t in schema if not any(c["key"] == "PRI" for c in t["columns"])]
        if no_key and self.has_primary_keys_in_schema:
            out["checks"].append({"level": "warn", "text": f"{len(no_key)} table(s) have no primary key: {', '.join(no_key[:8])}"})
        try:
            self.server_checks(out)
        except Exception as exc:  # noqa: BLE001 - not every user can read server status
            out["checks"].append({"level": "info", "text": f"Server status not readable with this user ({type(exc).__name__})"})
        if not out["checks"]:
            out["checks"].append({"level": "ok", "text": "No problems found"})
        return out


class MySQL(Driver):
    name, default_port, version_sql = "MySQL / MariaDB", 3306, "SELECT VERSION()"

    def open(self):
        try:
            import pymysql
        except ImportError:
            sys.exit("Install the MySQL driver first:  pip3 install pymysql")
        c = self.cfg
        return pymysql.connect(host=c["host"], port=int(c.get("port") or 3306), user=c["user"], password=c["password"],
                               database=c["database"], charset="utf8mb4", connect_timeout=5, read_timeout=120, autocommit=False)

    def alive(self):
        self.conn.ping()
        return True

    def start_read_only(self, cur):
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        for limit in ("SET SESSION MAX_EXECUTION_TIME=120000", "SET SESSION max_statement_time=120"):  # MySQL / MariaDB
            try:
                cur.execute(limit)
                break
            except Exception:  # noqa: BLE001
                continue
        cur.execute("START TRANSACTION READ ONLY")

    def write_rights(self):
        _, grants = self.rows("SHOW GRANTS")
        return re.search(r"ALL PRIVILEGES|INSERT|UPDATE|DELETE|DROP", " ".join(str(g[0]).upper() for g in grants))

    def tables(self):
        _, rows = self.rows("SELECT table_name, table_rows, data_length + index_length FROM information_schema.tables "
                            "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name")
        _, cols = self.rows("SELECT table_name, column_name, column_type, column_key FROM information_schema.columns "
                            "WHERE table_schema = DATABASE() ORDER BY table_name, ordinal_position")
        return rows, cols

    def server_checks(self, out):
        _, r = self.rows("SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Slow_queries','Uptime','Max_used_connections')")
        status = {k: v for k, v in r}
        _, mc = self.rows("SHOW VARIABLES LIKE 'max_connections'")
        out["status"] = {**status, "max_connections": mc[0][1] if mc else ""}
        if int(status.get("Slow_queries", 0) or 0) > 0:
            out["checks"].append({"level": "warn", "text": f"{status['Slow_queries']} slow queries since the server started"})
        _, big_unindexed = self.rows(
            "SELECT t.table_name, t.table_rows FROM information_schema.tables t WHERE t.table_schema = DATABASE() "
            "AND t.table_rows > 10000 AND NOT EXISTS (SELECT 1 FROM information_schema.statistics s "
            "WHERE s.table_schema = t.table_schema AND s.table_name = t.table_name AND s.index_name <> 'PRIMARY')")
        for name, n in big_unindexed[:8]:
            out["checks"].append({"level": "warn", "text": f"{name} has {n} rows but no index besides the primary key"})


class Postgres(Driver):
    name, default_port, version_sql = "PostgreSQL", 5432, "SHOW server_version"
    has_primary_keys_in_schema = False

    def open(self):
        try:
            import psycopg
        except ImportError:
            try:
                import psycopg2 as psycopg
            except ImportError:
                import subprocess
                if subprocess.run([sys.executable, "-m", "pip", "install", "-q", "psycopg[binary]"]).returncode != 0:
                    sys.exit("Install the PostgreSQL driver first:  pip3 install 'psycopg[binary]'")
                import psycopg
        c = self.cfg
        return psycopg.connect(host=c["host"], port=int(c.get("port") or 5432), user=c["user"], password=c["password"],
                               dbname=c["database"], connect_timeout=10)

    def alive(self):
        return not self.conn.closed

    def start_read_only(self, cur):
        self.conn.rollback()
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = '120s'")

    def write_rights(self):
        _, r = self.rows("SELECT count(*) FROM information_schema.table_privileges WHERE grantee = current_user "
                         "AND privilege_type IN ('INSERT','UPDATE','DELETE')")
        _, su = self.rows("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        return bool(r[0][0]) or bool(su and su[0][0])

    def tables(self):
        _, rows = self.rows("SELECT c.relname, c.reltuples::bigint, pg_total_relation_size(c.oid) FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' AND n.nspname = 'public' "
                            "ORDER BY c.relname")
        _, cols = self.rows("SELECT table_name, column_name, data_type, '' FROM information_schema.columns "
                            "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position")
        return rows, cols

    def server_checks(self, out):
        _, r = self.rows("SELECT count(*) FROM pg_stat_activity")
        out["status"] = {"connections": r[0][0]}
        _, seq = self.rows("SELECT relname, seq_scan, idx_scan, n_live_tup FROM pg_stat_user_tables "
                           "WHERE n_live_tup > 10000 AND seq_scan > coalesce(idx_scan, 0) * 10 ORDER BY seq_scan DESC LIMIT 8")
        for name, s, i, n in seq:
            out["checks"].append({"level": "warn", "text": f"{name} ({n} rows) is mostly read without an index ({s} full scans)"})


class SQLite(Driver):
    name, version_sql, explain_prefix = "SQLite", "SELECT sqlite_version()", "EXPLAIN QUERY PLAN "

    def open(self):
        import sqlite3
        return sqlite3.connect(f"file:{self.cfg['database']}?mode=ro", uri=True)   # the file itself is opened read-only

    def tables(self):
        _, names = self.rows("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        rows, cols = [], []
        for (name,) in names:
            rows.append([name, self.rows(f'SELECT COUNT(*) FROM "{name}"')[1][0][0], None])
            for c in self.rows(f'PRAGMA table_info("{name}")')[1]:
                cols.append([name, c[1], c[2], "PRI" if c[5] else ""])
        return rows, cols


DRIVERS = {"mysql": MySQL, "postgres": Postgres, "sqlite": SQLite}


def Database(cfg):  # noqa: N802 - kept as the public name
    driver = DRIVERS.get(cfg.get("type"))
    if driver is None:
        raise ValueError(f"Unknown database type: {cfg.get('type')}")
    return driver(cfg)


# Columns that hold secrets are never sent, even in a preview (WordPress user_pass, API tokens, ...)
SECRET_COLUMN = re.compile(r"(^|_)(pass|password|passwd|pwd|secret|secrets|token|tokens|salt|hash|otp|cvv|pin)($|_)"
                           r"|api_?key|private_?key|card_?number|session", re.I)
MASK = "••••"


def secret_columns(cols):
    # apiToken -> api_token, so camelCase names are caught too
    return [i for i, c in enumerate(cols) if SECRET_COLUMN.search(re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(c or "")).lower())]


def mask_row(row, hidden):
    return [MASK if i in hidden and v is not None else v for i, v in enumerate(row)]


def clean(v):
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time, dt.timedelta)):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    return str(v)


# ---------------------------------------------------------------- talking to Goal Engine
def call(cfg, path, body=None, timeout=60):
    req = urllib.request.Request(cfg["server"].rstrip("/") + path, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json",
                                          "User-Agent": f"goal-connector/{VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def upload(cfg, job_id, path):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        req = urllib.request.Request(cfg["server"].rstrip("/") + f"/api/connector/upload/{job_id}", data=fh, method="POST",
                                     headers={"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/gzip",
                                              "Content-Length": str(size), "User-Agent": f"goal-connector/{VERSION}"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()


def handle(db, job, export_path=None):
    kind, payload = job["kind"], job.get("payload") or {}
    if kind == "export" and export_path:
        return db.export_file(payload.get("sql", ""), export_path)
    if kind == "schema":
        return db.schema()
    if kind == "query":
        return db.query(payload.get("sql", ""), int(payload.get("limit") or 50))
    if kind == "export":
        return db.export(payload.get("sql", ""))
    if kind == "explain":
        return db.explain(payload.get("sql", ""))
    if kind == "health":
        return db.health()
    raise ValueError(f"Unknown job: {kind}")


def run(cfg):
    db = Database(cfg).connect()
    info = {"version": VERSION, "db_type": cfg["type"], "db_version": db.version(), "db_name": cfg["database"],
            "host": socket.gethostname(), "can_write": db.can_write(), "allow_write": False}
    hello = call(cfg, "/api/connector/hello", info)
    wait = hello.get("poll_seconds", 4)
    long_poll = int(hello.get("long_poll") or 0)   # the server holds the request until a job arrives
    print(f"Connected to Goal Engine. Database: {cfg['database']} ({cfg['type']} {info['db_version']}). Ctrl+C to stop.")
    backoff = wait
    while True:
        try:
            path = f"/api/connector/poll?wait={long_poll}" if long_poll else "/api/connector/poll"
            jobs = call(cfg, path, timeout=long_poll + 30).get("jobs", [])
            backoff = 0.2 if long_poll else wait
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                sys.exit("This connector was disconnected from Goal Engine. Create a new one to connect again.")
            jobs, backoff = [], min(backoff * 2, 60)
        except Exception:  # noqa: BLE001 - network blip; try again
            jobs, backoff = [], min(backoff * 2, 60)
        for job in jobs:
            tmp = os.path.join(os.path.dirname(CONFIG), f"export_{job['id']}.csv.gz")
            try:
                if job["kind"] == "export":
                    data = handle(db, job, export_path=tmp)
                    upload(cfg, job["id"], tmp)          # streamed from disk, compressed
                    result = {"ok": True, "result": data, "uploaded": True}
                else:
                    result = {"ok": True, "result": handle(db, job)}
            except Exception as exc:  # noqa: BLE001 - report the error back, keep running
                db.done()
                result = {"ok": False, "error": str(exc)[:500]}
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            try:
                call(cfg, f"/api/connector/result/{job['id']}", result)
            except Exception as exc:  # noqa: BLE001
                print(f"Couldn't send result of job {job['id']}: {exc}")
        time.sleep(backoff if not jobs else 0.5)


# ---------------------------------------------------------------- finding the database settings by itself
SEARCH_ROOTS = ["{cwd}", "{home}", "/var/www", "/srv", "/opt", "/cdata", "/usr/share/nginx", "/home/*/public_html", "/home/*/www", "/home/*/htdocs"]
SKIP_DIRS = {"node_modules", "vendor", ".git", "storage", "cache", "wp-content", "public", "assets", "tmp", ".venv", "venv", "__pycache__"}


def _unquote(v):
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
    return v


def parse_env(text):
    """Laravel / CodeIgniter 4 / most frameworks: KEY=value lines."""
    env = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z_][\w.]*)\s*=\s*(.*)$", line)
        if m and not line.lstrip().startswith("#"):
            env[m.group(1)] = _unquote(m.group(2).split(" #")[0])
    if env.get("DB_DATABASE"):
        kind = {"mysql": "mysql", "mariadb": "mysql", "pgsql": "postgres", "postgres": "postgres"}.get(env.get("DB_CONNECTION", "mysql"))
        if kind:
            return {"type": kind, "host": env.get("DB_HOST", "127.0.0.1"), "port": env.get("DB_PORT", ""), "database": env["DB_DATABASE"],
                    "user": env.get("DB_USERNAME", ""), "password": env.get("DB_PASSWORD", ""), "app": "Laravel"}
    if env.get("database.default.database"):
        kind = {"MySQLi": "mysql", "Postgre": "postgres"}.get(env.get("database.default.DBDriver", "MySQLi"), "mysql")
        return {"type": kind, "host": env.get("database.default.hostname", "localhost"), "port": env.get("database.default.port", ""),
                "database": env["database.default.database"], "user": env.get("database.default.username", ""),
                "password": env.get("database.default.password", ""), "app": "CodeIgniter 4"}
    if env.get("DATABASE_URL", "").startswith(("mysql", "postgres")):
        m = re.match(r"^(mysql\w*|postgres\w*)://([^:@/]*)(?::([^@/]*))?@([^:/]+)(?::(\d+))?/([^?]+)", env["DATABASE_URL"])
        if m:
            from urllib.parse import unquote
            return {"type": "mysql" if m.group(1).startswith("mysql") else "postgres", "host": m.group(4), "port": m.group(5) or "",
                    "database": m.group(6), "user": unquote(m.group(2)), "password": unquote(m.group(3) or ""), "app": "App"}
    return None


def parse_wp(text):
    vals = dict(re.findall(r"define\(\s*['\"](DB_NAME|DB_USER|DB_PASSWORD|DB_HOST)['\"]\s*,\s*['\"](.*?)['\"]\s*\)", text))
    if not vals.get("DB_NAME"):
        return None
    host, _, port = vals.get("DB_HOST", "localhost").partition(":")
    return {"type": "mysql", "host": host or "localhost", "port": port if port.isdigit() else "", "database": vals["DB_NAME"],
            "user": vals.get("DB_USER", ""), "password": vals.get("DB_PASSWORD", ""), "app": "WordPress"}


def parse_ci3(text):
    vals = dict(re.findall(r"\$db\[['\"]default['\"]\]\[['\"](hostname|username|password|database|dbdriver|port)['\"]\]\s*=\s*['\"]?(.*?)['\"]?\s*;", text))
    if not vals.get("database"):
        return None
    return {"type": "postgres" if "postgre" in vals.get("dbdriver", "") else "mysql", "host": vals.get("hostname", "localhost"),
            "port": vals.get("port", ""), "database": vals["database"], "user": vals.get("username", ""),
            "password": vals.get("password", ""), "app": "CodeIgniter 3"}


PARSERS = {".env": parse_env, "wp-config.php": parse_wp, "database.php": parse_ci3}


def find_projects(roots=None, max_depth=4):
    import glob

    home = os.path.expanduser("~")
    seen, found = set(), []
    for pattern in roots or SEARCH_ROOTS:
        for root in glob.glob(pattern.format(cwd=os.getcwd(), home=home)):
            if not os.path.isdir(root):
                continue
            base_depth = root.rstrip("/").count("/")
            for dirpath, dirnames, filenames in os.walk(root):
                depth = dirpath.count("/") - base_depth
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")] if depth < max_depth else []
                for name, parser in PARSERS.items():
                    if name not in filenames:
                        continue
                    path = os.path.join(dirpath, name)
                    if name == "database.php" and not dirpath.endswith(os.path.join("application", "config")):
                        continue
                    real = os.path.realpath(path)
                    if real in seen:
                        continue
                    seen.add(real)
                    try:
                        with open(path, errors="ignore") as fh:
                            cfg = parser(fh.read(200_000))
                    except OSError:
                        continue
                    if cfg and cfg["database"] and cfg["user"]:
                        project = dirpath[: -len("/application/config")] if name == "database.php" else dirpath
                        found.append({**cfg, "path": project})
    # one entry per database (staging and live copies of the same site stay separate)
    unique = {}
    for f in found:
        unique.setdefault((f["type"], f["host"], f["database"], f["user"]), f)
    return list(unique.values())


def ask(prompt, default=""):
    try:
        v = input(prompt).strip()
    except EOFError:
        v = ""
    return v or default


def manual_details():
    kind = ask("Database type [mysql/postgres] (mysql): ", "mysql").lower()
    kind = {"mariadb": "mysql", "postgresql": "postgres", "pg": "postgres"}.get(kind, kind)
    if kind not in ("mysql", "postgres", "sqlite"):
        sys.exit("Only mysql (or MariaDB) and postgres for now.")
    if kind == "sqlite":
        return {"type": "sqlite", "host": "", "port": "", "user": "", "password": "", "database": ask("Path to the .sqlite file: ")}
    return {"type": kind, "host": ask("Host (127.0.0.1): ", "127.0.0.1"),
            "port": ask(f"Port ({3306 if kind == 'mysql' else 5432}): ", str(3306 if kind == "mysql" else 5432)),
            "database": ask("Database name: "), "user": ask("Database user: "), "password": getpass.getpass("Password (hidden): ")}


def make_reader(cfg):
    """Create a read-only database user with a random password (needs sudo). Returns new user/password or None."""
    import secrets
    import subprocess

    pw = secrets.token_urlsafe(18)
    db = cfg["database"].replace("`", "").replace('"', "")
    if cfg["type"] == "mysql":
        hosts = ["localhost", "127.0.0.1"] if cfg["host"] in ("localhost", "127.0.0.1", "") else ["%"]
        sql = ""
        for h in hosts:
            sql += (f"CREATE USER IF NOT EXISTS 'goal_reader'@'{h}' IDENTIFIED BY '{pw}';\n"
                    f"ALTER USER 'goal_reader'@'{h}' IDENTIFIED BY '{pw}';\n"
                    f"GRANT SELECT, SHOW VIEW ON `{db}`.* TO 'goal_reader'@'{h}';\n"
                    f"GRANT PROCESS ON *.* TO 'goal_reader'@'{h}';\n")
        cmd = ["sudo", "mysql"]
    else:
        sql = (f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='goal_reader') THEN CREATE ROLE goal_reader LOGIN; END IF; END $$;\n"
               f"ALTER ROLE goal_reader LOGIN PASSWORD '{pw}';\n"
               f'GRANT CONNECT ON DATABASE "{db}" TO goal_reader;\n'
               f"\\c \"{db}\"\n"
               "GRANT USAGE ON SCHEMA public TO goal_reader;\n"
               "GRANT SELECT ON ALL TABLES IN SCHEMA public TO goal_reader;\n"
               "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO goal_reader;\n"
               "GRANT pg_read_all_stats TO goal_reader;\n")
        cmd = ["sudo", "-u", "postgres", "psql", "-q", "-v", "ON_ERROR_STOP=1"]
    try:
        # the SQL (with the password) goes in through stdin, so it never shows in the process list
        r = subprocess.run(cmd, input=sql, text=True, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return {**cfg, "user": "goal_reader", "password": pw}


def try_connect(cfg):
    try:
        return Database(cfg).connect(), None
    except SystemExit as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - show a short reason, not a traceback
        msg = str(exc)
        if "1045" in msg or "1698" in msg or "Access denied" in msg or "password authentication failed" in msg:
            return None, "The database said no to this user/password."
        if "1049" in msg or "does not exist" in msg:
            return None, "No database with that name."
        return None, f"Couldn't connect: {msg[:200]}"


def setup(args):
    print("Goal Engine connector. Your database password stays on this server; it is never sent anywhere.\n")
    cfg = None
    if not args.manual:
        print("Looking for your projects...")
        projects = find_projects()
        if projects:
            print(f"Found {len(projects)}. Checking which ones connect...")
            working = []
            for p in projects:
                probe = dict(p)
                probe["port"] = probe.get("port") or ("3306" if p["type"] == "mysql" else "5432")
                db, _ = try_connect(probe)
                if db is not None:
                    working.append(p)
                    try:
                        db.conn.close()
                    except Exception:  # noqa: BLE001
                        pass
            if working:
                projects = working
            else:
                print("None of them connected from here, so type the details instead.")
                projects = []
        if projects:
            for i, p in enumerate(projects, 1):
                print(f"  {i}) {p['path']}  ·  {p['app']}  ·  {p['type']}  ·  {p['database']}")
            print("  m) Type the details myself")
            pick = ask(f"Which database should Goal Engine read? [1]: ", "1").lower()
            if pick != "m":
                if not pick.isdigit() or not 1 <= int(pick) <= len(projects):
                    sys.exit("Please run it again and pick a number from the list.")
                cfg = dict(projects[int(pick) - 1])
                cfg.pop("app", None)
                cfg.pop("path", None)
        else:
            print("No project settings found. Type the details instead.")
    if cfg is None:
        cfg = manual_details()
    if cfg["type"] != "sqlite" and not cfg.get("port"):
        cfg["port"] = "3306" if cfg["type"] == "mysql" else "5432"
    db, err = try_connect(cfg)
    if db is None:
        sys.exit(f"\n{err}\nRun the same command again and choose 'm' to type the details.")
    print(f"Connected ({cfg['type']} {db.version()}, database {cfg['database']}).")

    if cfg["type"] != "sqlite" and db.can_write():
        print("\nThat user can change data. Goal Engine never writes, but a separate read-only user is safer.")
        if ask("Create a read-only user for Goal Engine now? (asks for your sudo password) [Y/n]: ", "y").lower().startswith("y"):
            reader = make_reader(cfg)
            rdb, _ = try_connect(reader) if reader else (None, None)
            if rdb is not None:
                cfg, db = reader, rdb
                print("Done. Goal Engine will use the read-only user 'goal_reader' (random password, saved only here).")
            else:
                print("Couldn't create it (needs sudo access to the database). Continuing with the app's user; the connector itself stays read-only.")

    out = {"server": args.server, "token": args.token, **{k: cfg[k] for k in ("type", "host", "port", "database", "user", "password")}}
    with open(os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(out, fh, indent=2)
    os.chmod(CONFIG, 0o600)
    print(f"\nSaved the settings in {CONFIG} (only you can read it).")


PIDFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "goal_connector.pid")


def already_running():
    try:
        with open(PIDFILE) as fh:
            pid = int(fh.read().strip() or 0)
        if pid and pid != os.getpid():
            os.kill(pid, 0)
            return pid
    except (OSError, ValueError):
        pass
    return 0


def main():
    ap = argparse.ArgumentParser(description="Goal Engine database connector")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--server", required=True)
    s.add_argument("--token", required=True)
    s.add_argument("--manual", action="store_true", help="type the database details instead of finding them")
    sub.add_parser("run")
    sub.add_parser("stop")
    args = ap.parse_args()
    if args.cmd == "stop":
        pid = already_running()
        if pid:
            os.kill(pid, 15)
            print("Stopped.")
        else:
            print("It isn't running.")
        return
    if args.cmd == "setup":
        setup(args)
        return
    if not os.path.exists(CONFIG):
        sys.exit("Run setup first.")
    with open(CONFIG) as fh:
        cfg = json.load(fh)
    cfg["allow_write"] = False  # Level 1: never writes, whatever the file says
    if already_running():
        sys.exit("The connector is already running.")
    with open(PIDFILE, "w") as fh:
        fh.write(str(os.getpid()))
    try:
        run(cfg)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        try:
            os.remove(PIDFILE)
        except OSError:
            pass


if __name__ == "__main__":
    main()
