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
class Database:
    def __init__(self, cfg):
        self.cfg = cfg
        self.kind = cfg["type"]
        self.conn = None

    def connect(self):
        c = self.cfg
        if self.kind == "mysql":
            try:
                import pymysql
            except ImportError:
                sys.exit("Install the MySQL driver first:  pip3 install pymysql")
            self.conn = pymysql.connect(host=c["host"], port=int(c.get("port") or 3306), user=c["user"], password=c["password"],
                                        database=c["database"], charset="utf8mb4", connect_timeout=5, read_timeout=120,
                                        autocommit=False)
        elif self.kind == "postgres":
            try:
                import psycopg
                self.conn = psycopg.connect(host=c["host"], port=int(c.get("port") or 5432), user=c["user"], password=c["password"],
                                            dbname=c["database"], connect_timeout=10)
            except ImportError:
                try:
                    import psycopg2 as psycopg
                except ImportError:
                    import subprocess
                    if subprocess.run([sys.executable, "-m", "pip", "install", "-q", "psycopg[binary]"]).returncode != 0:
                        sys.exit("Install the PostgreSQL driver first:  pip3 install 'psycopg[binary]'")
                    import psycopg
                self.conn = psycopg.connect(host=c["host"], port=int(c.get("port") or 5432), user=c["user"], password=c["password"],
                                            dbname=c["database"], connect_timeout=10)
        elif self.kind == "sqlite":
            import sqlite3
            self.conn = sqlite3.connect(f"file:{c['database']}?mode=ro", uri=True)
        else:
            raise ValueError("Unknown database type")
        return self

    def ensure(self):
        try:
            if self.kind == "mysql":
                self.conn.ping()
            elif self.kind == "postgres" and self.conn.closed:
                self.connect()
        except Exception:  # noqa: BLE001
            self.connect()

    def cursor(self, read_only=True):
        self.ensure()
        cur = self.conn.cursor()
        if read_only and self.kind == "mysql":
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            for limit in ("SET SESSION MAX_EXECUTION_TIME=120000", "SET SESSION max_statement_time=120"):  # MySQL / MariaDB
                try:
                    cur.execute(limit)
                    break
                except Exception:  # noqa: BLE001
                    continue
            cur.execute("START TRANSACTION READ ONLY")
        elif read_only and self.kind == "postgres":
            self.conn.rollback()
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '120s'")
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
        sql = {"mysql": "SELECT VERSION()", "postgres": "SHOW server_version", "sqlite": "SELECT sqlite_version()"}[self.kind]
        return str(self.rows(sql)[1][0][0])

    def can_write(self):
        """Does this database user have write rights? (We still never write.)"""
        try:
            if self.kind == "mysql":
                _, grants = self.rows("SHOW GRANTS")
                text = " ".join(str(g[0]).upper() for g in grants)
                return bool(re.search(r"ALL PRIVILEGES|INSERT|UPDATE|DELETE|DROP", text))
            if self.kind == "postgres":
                _, r = self.rows("SELECT count(*) FROM information_schema.table_privileges WHERE grantee = current_user "
                                 "AND privilege_type IN ('INSERT','UPDATE','DELETE')")
                _, su = self.rows("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                return bool(r[0][0]) or bool(su and su[0][0])
        except Exception:  # noqa: BLE001
            return False
        return False

    # ------------------------------------------------ jobs
    def schema(self):
        if self.kind == "mysql":
            _, rows = self.rows("SELECT table_name, table_rows, data_length + index_length FROM information_schema.tables "
                                "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name")
            _, cols = self.rows("SELECT table_name, column_name, column_type, column_key FROM information_schema.columns "
                                "WHERE table_schema = DATABASE() ORDER BY table_name, ordinal_position")
        elif self.kind == "postgres":
            _, rows = self.rows("SELECT c.relname, c.reltuples::bigint, pg_total_relation_size(c.oid) FROM pg_class c "
                                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' AND n.nspname = 'public' "
                                "ORDER BY c.relname")
            _, cols = self.rows("SELECT table_name, column_name, data_type, '' FROM information_schema.columns "
                                "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position")
        else:
            _, names = self.rows("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            rows, cols = [], []
            for (name,) in names:
                n = self.rows(f'SELECT COUNT(*) FROM "{name}"')[1][0][0]
                rows.append([name, n, None])
                for c in self.rows(f'PRAGMA table_info("{name}")')[1]:
                    cols.append([name, c[1], c[2], "PRI" if c[5] else ""])
        tables = {}
        for name, count, size in rows:
            tables[name] = {"name": name, "rows": int(count or 0), "bytes": int(size or 0), "columns": []}
        for t, c, typ, key in cols:
            if t in tables:
                tables[t]["columns"].append({"name": c, "type": str(typ), "key": str(key or "")})
        return {"tables": list(tables.values())}

    def query(self, sql, limit):
        cols, rows = self.rows(safe_sql(sql), limit=limit)
        return {"columns": cols, "rows": [[clean(v) for v in r] for r in rows], "row_count": len(rows), "limited": len(rows) >= limit}

    def export(self, sql):
        sql = safe_sql(sql)
        cur = self.cursor()
        buf = io.StringIO()
        writer = csv.writer(buf)
        count = 0
        try:
            cur.execute(sql)
            writer.writerow([d[0] for d in cur.description or []])
            while True:
                batch = cur.fetchmany(2000)
                if not batch:
                    break
                for r in batch:
                    writer.writerow([clean(v) for v in r])
                    count += 1
                if count >= MAX_EXPORT_ROWS or buf.tell() > MAX_EXPORT_BYTES:
                    break
        finally:
            cur.close()
            self.done()
        return {"csv": "﻿" + buf.getvalue(), "row_count": count, "limited": count >= MAX_EXPORT_ROWS or buf.tell() > MAX_EXPORT_BYTES}

    def explain(self, sql):
        sql = safe_sql(sql)
        if re.match(r"^\s*explain\b", sql, re.I):
            sql = re.sub(r"^\s*explain\s+", "", sql, flags=re.I)
        prefix = {"mysql": "EXPLAIN ", "postgres": "EXPLAIN ", "sqlite": "EXPLAIN QUERY PLAN "}[self.kind]
        cols, rows = self.rows(prefix + sql)
        return {"columns": cols, "rows": [[clean(v) for v in r] for r in rows], "row_count": len(rows)}

    def health(self):
        out = {"version": self.version(), "checks": []}
        schema = self.schema()["tables"]
        biggest = sorted(schema, key=lambda t: t["bytes"] or t["rows"], reverse=True)[:5]
        out["biggest"] = [{"name": t["name"], "rows": t["rows"], "bytes": t["bytes"]} for t in biggest]
        no_key = [t["name"] for t in schema if not any(c["key"] == "PRI" for c in t["columns"])]
        if no_key and self.kind != "postgres":
            out["checks"].append({"level": "warn", "text": f"{len(no_key)} table(s) have no primary key: {', '.join(no_key[:8])}"})
        try:
            if self.kind == "mysql":
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
            elif self.kind == "postgres":
                _, r = self.rows("SELECT count(*) FROM pg_stat_activity")
                out["status"] = {"connections": r[0][0]}
                _, seq = self.rows("SELECT relname, seq_scan, idx_scan, n_live_tup FROM pg_stat_user_tables "
                                   "WHERE n_live_tup > 10000 AND seq_scan > coalesce(idx_scan, 0) * 10 ORDER BY seq_scan DESC LIMIT 8")
                for name, s, i, n in seq:
                    out["checks"].append({"level": "warn", "text": f"{name} ({n} rows) is mostly read without an index ({s} full scans)"})
        except Exception as exc:  # noqa: BLE001 - not every user can read server status
            out["checks"].append({"level": "info", "text": f"Server status not readable with this user ({type(exc).__name__})"})
        if not out["checks"]:
            out["checks"].append({"level": "ok", "text": "No problems found"})
        return out


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
def call(cfg, path, body=None):
    req = urllib.request.Request(cfg["server"].rstrip("/") + path, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json",
                                          "User-Agent": f"goal-connector/{VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


def handle(db, job):
    kind, payload = job["kind"], job.get("payload") or {}
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
    wait = call(cfg, "/api/connector/hello", info).get("poll_seconds", 4)
    print(f"Connected to Goal Engine. Database: {cfg['database']} ({cfg['type']} {info['db_version']}). Ctrl+C to stop.")
    backoff = wait
    while True:
        try:
            jobs = call(cfg, "/api/connector/poll").get("jobs", [])
            backoff = wait
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                sys.exit("This connector was disconnected from Goal Engine. Create a new one to connect again.")
            jobs, backoff = [], min(backoff * 2, 60)
        except Exception:  # noqa: BLE001 - network blip; try again
            jobs, backoff = [], min(backoff * 2, 60)
        for job in jobs:
            try:
                data = handle(db, job)
                csv_text = data.pop("csv", None)
                result = {"ok": True, "result": data}
                if csv_text is not None:
                    result["csv"] = csv_text
            except Exception as exc:  # noqa: BLE001 - report the error back, keep running
                db.done()
                result = {"ok": False, "error": str(exc)[:500]}
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
