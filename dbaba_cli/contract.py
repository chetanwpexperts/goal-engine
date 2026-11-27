"""Local contract commands; no remote URLs, redirects or proxies."""
import os
from pathlib import Path
import re
import stat
from urllib.parse import quote

def api_key():
    key = os.environ.get("DBABA_API_KEY")
    if key is None:
        path = Path.home() / ".config" / "dbaba" / "key"
        try:
            if path.is_symlink() or not path.is_file():
                return None
            if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != 0o600:
                raise ValueError("Unsafe API key file permissions.")
            key = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
    if key and (len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key)):
        raise ValueError("Invalid API key.")
    return key

def segment(value):
    if not value or value in (".", "..") or "/" in value or "\\" in value:
        raise ValueError("Invalid identifier.")
    return quote(value, safe="")

def bad_alert(a):
    return not a["acknowledged"] and a["level"] in ("warning", "critical")

def commands_for(client, args):
    if args.command == "status":
        health = client.call("/api/health")
        if health["ok"] is not True:
            print("DBaba is not healthy.")
            return 1
        state = client.call("/api/state")
        db = state["database"] or {}
        alerts = state["alerts"]
        print("DBaba is running. Version:", state["version"])
        print("Setup:", "complete" if state["setup_done"] else "not complete")
        print("Database:", db.get("engine", "not configured"), db.get("name", ""), "- connected" if db.get("connected") else "- not connected")
        print("Unread alerts:", sum(not a["acknowledged"] for a in alerts))
        scan = state["last_scan"]
        print("Last scan:", f'{scan["at"]} (score {scan["score"]}/100)' if scan else "not yet run")
        return int(not state["setup_done"] or not db.get("connected") or any(map(bad_alert, alerts)))
    if args.command == "alerts":
        if args.ack:
            ok = client.call("/api/alerts/" + segment(args.ack) + "/ack", "POST")["ok"]
            print("Alert acknowledged." if ok else "Alert not acknowledged.")
            return int(not ok)
        alerts = client.call("/api/state")["alerts"]
        for a in alerts:
            print(f'{a["id"]}: {a["level"]} - {a["title"]}: {a["text"]}' + (" (acknowledged)" if a["acknowledged"] else ""))
        if not alerts:
            print("No alerts.")
        return int(any(map(bad_alert, alerts)))
    if args.command == "report":
        if args.scan:
            started = client.call("/api/report/scan", "POST")["started"]
            print("Scan started. Run dbaba report again to see the result." if started else "Scan not started.")
            if not args.html or not started:
                return int(not started)
        if args.html:
            data = client.call("/api/report.html", raw=True)
            fd = os.open(args.html, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            print("HTML report saved.")
        report = client.call("/api/report")
        if report.get("at") is None or report.get("score") is None:
            print("No scan yet")
        else:
            print(f'Score: {report["score"]}/100 ({report["engine"]}, {report["at"]})')
        if report.get("pending"):
            print("Scan in progress.")
        items = [(section["title"], item) for section in report["sections"] for item in section["items"]]
        for section, item in sorted(items, key=lambda row: {"critical":0,"warning":1,"info":2}.get(row[1]["severity"], 3)):
            print(f'{item["severity"]}: {section}: {item["title"]}\n{item["what"]}\nWhy: {item["why"]}')
            if item["fix"]:
                print("Suggested SQL (not executed):", item["fix"]["sql"])
                if item["fix"]["needs_approval"]:
                    print("Requires approval.")
        return int(any(i["severity"] in ("warning", "critical") for _, i in items))
    if args.command == "jobs":
        jobs = client.call("/api/jobs")["jobs"]
        if args.id:
            job = next((j for j in jobs if j["id"] == args.id), None)
            if job is None:
                print("Job not found.")
                return 1
            if args.on or args.off or args.at is not None:
                if args.at is not None and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", args.at):
                    raise ValueError("Invalid time.")
                body = {"enabled": True if args.on else False if args.off else job["enabled"], "at": args.at if args.at is not None else job["at"]}
                job = client.call("/api/jobs/" + segment(args.id), "POST", body)
            jobs = [job]
        elif args.on or args.off or args.at is not None:
            raise ValueError("Specify a job ID.")
        for job in jobs:
            last = job["last_run"]
            print(f'{job["id"]}: {job["name"]} - {"on" if job["enabled"] else "off"}, {job["every"]}, {job["at"] or "no fixed time"}')
            print("Last run:", f'{last["at"]}: {"ok" if last["ok"] else "failed"} - {last["text"]}' if last else "not yet run")
        return int(any(j["last_run"] and not j["last_run"]["ok"] for j in jobs))
