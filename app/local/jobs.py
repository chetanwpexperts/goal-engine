"""The jobs DBaba runs by itself, and the alerts they raise (GET /api/jobs, /api/state "alerts").

S4 ships two jobs; S6 adds backups with a test restore, and S7 growth and maintenance.
  health  every 5 minutes: can DBaba reach the database, and how close are connections to the limit
  scan    every day at its time (default 02:40): the full report (app/local/report.py)
An alert is raised once per problem and stays until it is acknowledged; when the problem goes away a short "back to
normal" info alert says so. Alerts live in data/local/alerts.json (newest 200), never anywhere else.
"""

import datetime as dt
import json
import os
import secrets
import tempfile
import threading
import time

from app.local import dbconn, report

JOBS = {"health": {"name": "Health check", "every": "5min", "at": None, "enabled": True},
        "scan": {"name": "Full database scan", "every": "day", "at": "02:40", "enabled": True}}
MAX_ALERTS = 200


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


class Jobs:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.alerts_path = cfg.data_dir / "alerts.json"
        self.runs_path = cfg.data_dir / "runs.json"
        self.report_path = cfg.data_dir / "report.json"
        self.scanning = False
        self.connected: bool | None = None
        self._stop = threading.Event()

    # ---- files
    def _load(self, path, empty):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return empty

    def _save(self, path, data):
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    # ---- jobs
    def settings(self) -> dict:
        saved = self.cfg.data.get("jobs") or {}
        out = {}
        for jid, base in JOBS.items():
            s = saved.get(jid) or {}
            out[jid] = {**base, "enabled": bool(s.get("enabled", base["enabled"])), "at": s.get("at", base["at"])}
        return out

    def listing(self) -> list[dict]:
        runs = self._load(self.runs_path, {})
        return [{"id": jid, "name": j["name"], "enabled": j["enabled"], "every": j["every"], "at": j["at"],
                 "last_run": runs.get(jid)} for jid, j in self.settings().items()]

    def change(self, jid: str, enabled: bool, at) -> dict:
        if jid not in JOBS:
            raise KeyError(jid)
        if JOBS[jid]["every"] == "5min":
            at = None                                   # a 5-minute job has no time of day
        self.cfg.update(lambda d: d.setdefault("jobs", {}).__setitem__(jid, {"enabled": bool(enabled), "at": at}))
        return next(j for j in self.listing() if j["id"] == jid)

    def _record(self, jid, ok, text):
        with self.lock:
            runs = self._load(self.runs_path, {})
            runs[jid] = {"at": _iso(utcnow()), "ok": bool(ok), "text": text[:300]}
            self._save(self.runs_path, runs)

    # ---- alerts
    def alerts(self) -> list[dict]:
        return self._load(self.alerts_path, [])

    def raise_alert(self, key, level, title, text):
        with self.lock:
            al = self.alerts()
            if any(a.get("key") == key and not a["acknowledged"] for a in al):
                return None                             # already raised and not yet seen
            a = {"id": "a" + secrets.token_hex(6), "key": key, "level": level, "title": title, "text": text,
                 "at": _iso(utcnow()), "acknowledged": False}
            self._save(self.alerts_path, ([a] + al)[:MAX_ALERTS])
            return a

    def clear(self, key, title, text):
        """The problem behind `key` went away: close its open alert with a short info alert."""
        with self.lock:
            al = self.alerts()
            open_ = [a for a in al if a.get("key") == key and not a["acknowledged"] and a["level"] != "info"]
            if not open_:
                return
            for a in open_:
                a["acknowledged"] = True
            self._save(self.alerts_path, al)
        self.raise_alert(key + ":ok", "info", title, text)

    def ack(self, aid: str) -> bool:
        with self.lock:
            al = self.alerts()
            hit = next((a for a in al if a["id"] == aid), None)
            if hit is None:
                return False
            hit["acknowledged"] = True
            self._save(self.alerts_path, al)
            return True

    def public_alerts(self) -> list[dict]:
        return [{k: a[k] for k in ("id", "level", "title", "text", "at", "acknowledged")} for a in self.alerts()]

    # ---- the work
    def health(self):
        db = self.cfg.database
        if not db:
            return
        try:
            conn = dbconn.open_db(db)
        except dbconn.ConnectError as exc:
            self.connected = False
            self.raise_alert("unreachable", "critical", "DBaba can't reach the database", str(exc))
            self._record("health", False, str(exc))
            return
        try:
            used, limit = report._connections(conn, db["type"])
        except Exception as exc:  # noqa: BLE001 - this user may not read the status
            used, limit = 0, 0
            self._record("health", True, f"Connected; connection count not readable ({type(exc).__name__})")
        finally:
            dbconn.close(conn)
        self.connected = True
        self.clear("unreachable", "The database is reachable again", "DBaba connected again.")
        if limit and used / limit >= 0.8:
            self.raise_alert("connections", "critical" if used / limit >= 0.95 else "warning",
                             f"{used} of {limit} connections in use", "New connections are refused at the limit.")
        elif limit:
            self.clear("connections", "Connections are back to normal", f"{used} of {limit} in use.")
        if limit:
            self._record("health", True, f"Connected; {used} of {limit} connections in use")

    def scan(self) -> bool:
        db = self.cfg.database
        if not db or self.scanning:
            return False
        self.scanning = True
        try:
            old = self.last_report()
            rep = report.scan(db)
            rep["changes"] = report.compare(rep, old)
            if old:
                report.save(self.report_path.with_name("report.prev.json"), old)
            report.save(self.report_path, rep)
            for s in rep["sections"]:                    # a new critical problem is an alert too, not only a line in the report
                for i in s["items"]:
                    if i["severity"] == "critical" and i["id"] != "unreachable":
                        self.raise_alert("report:" + i["id"], "critical", i["title"], i["what"])
            crit = sum(i["severity"] == "critical" for s in rep["sections"] for i in s["items"])
            self._record("scan", crit == 0, f"Score {rep['score']}/100")
            return True
        except Exception as exc:  # noqa: BLE001
            self._record("scan", False, f"The scan stopped: {type(exc).__name__}")
            return False
        finally:
            self.scanning = False

    def scan_in_background(self) -> bool:
        if self.scanning or not self.cfg.database:
            return False
        threading.Thread(target=self.scan, daemon=True, name="dbaba-scan").start()
        return True

    def last_report(self) -> dict | None:
        return self._load(self.report_path, None)

    # ---- the clock
    def due(self, jid, job, now: dt.datetime, runs) -> bool:
        if not job["enabled"]:
            return False
        last = runs.get(jid, {}).get("at")
        last_t = dt.datetime.fromisoformat(last) if last else None
        if job["every"] == "5min":
            return last_t is None or now - last_t >= dt.timedelta(minutes=5)
        hh, mm = (int(x) for x in (job["at"] or "02:40").split(":"))
        today = now.astimezone().replace(hour=hh, minute=mm, second=0, microsecond=0)
        return now.astimezone() >= today and (last_t is None or last_t < today)

    def tick(self, now=None):
        now = now or utcnow()
        if not self.cfg.setup_done:
            return
        runs = self._load(self.runs_path, {})
        for jid, job in self.settings().items():
            if self.due(jid, job, now, runs):
                (self.health if jid == "health" else self.scan)()

    def start(self, every: float = 30.0):
        def loop():
            if self.cfg.setup_done:                      # right after a restart: say "connected" now, not in 5 minutes
                try:
                    self.health()
                except Exception:  # noqa: BLE001
                    pass
            while not self._stop.wait(every):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 - the next tick tries again
                    pass
        threading.Thread(target=loop, daemon=True, name="dbaba-jobs").start()

    def stop(self):
        self._stop.set()
        time.sleep(0)
