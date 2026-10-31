"""Grow DBaba's SQL knowledge with no Claude and no paid API.

    official docs  ->  a local model (Ollama) drafts rules  ->  MySQL, MariaDB and PostgreSQL decide what is true

The model is allowed to be wrong: a rule is kept only if its broken query really fails on a real engine and its
fixed query really runs there. Engines, error codes and messages come from the databases, never from the model.
A rule's 'detect' pattern is kept only if it finds the broken query, not the fixed one, and none of hundreds of
valid generated queries. Verified rules go to app/knowledge/sql_rules/learned.json, which DBaba reads.

Run (on the Mac, from the repo):  docker compose -f tools/knowledge/docker-compose.yml up -d
                                  python tools/knowledge/harvest.py            (all sources; resumes where it stopped)
                                  python tools/knowledge/harvest.py --limit 3  (a quick try)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "app" / "knowledge" / "sql_rules" / "learned.json"
CACHE = HERE / ".cache"
MODEL = os.getenv("HARVEST_MODEL", "qwen2.5-coder:7b")
OLLAMA = os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/generate"
ENGINES = {
    "mysql": {"kind": "mysql", "port": 3307},
    "mariadb": {"kind": "mysql", "port": 3308},
    "postgres": {"kind": "pg", "port": 5433},
}

PROMPT = """You read database documentation and write test cases for a SQL checker.

Documentation ({url}):
---
{text}
---

The test database has exactly these tables:
customers(id, name, email, city, created_at), products(id, name, category, price),
orders(id, customer_id, status, total, created_at), order_items(id, order_id, product_id, qty, unit_price),
employees(id, name, manager_id, salary)

From the documentation above ONLY, write up to 4 mistakes people make in SELECT queries that this documentation
says are errors. For each one give a broken SELECT that uses only the tables above, the corrected SELECT, and a
Python regular expression that finds the mistake in a query's text.

Answer with JSON only:
{{"rules": [{{"title": "short name of the mistake", "bad_sql": "SELECT ...", "fixed_sql": "SELECT ...",
"why": "one sentence: why it is wrong", "fix": "one sentence: what to write instead",
"detect": "regex, case-insensitive, matches the broken query but not the fixed one"}}]}}"""


# ------------------------------------------------------------------ docs
BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130.0 Safari/537.36",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}


def _download(url: str) -> str:
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=BROWSER), timeout=30).read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - some doc sites refuse Python; curl usually gets through
        import subprocess

        args = ["curl", "-sSL", "--compressed", "--max-time", "40"]
        for k, v in BROWSER.items():
            args += ["-H", f"{k}: {v}"]
        out = subprocess.run(args + [url], capture_output=True, text=True, timeout=60)
        if out.returncode != 0 or len(out.stdout) < 2000 or "Access Denied" in out.stdout[:3000]:
            raise RuntimeError("the site refused (save the page into tools/knowledge/pages/ from your browser)")
        return out.stdout


_fetched: dict = {}


def fetch(url: str) -> str:
    if url in _fetched:                                       # a downloaded manual, already read
        return _fetched[url]
    CACHE.mkdir(exist_ok=True)
    f = CACHE / ("page-" + hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")
    if f.exists():
        return f.read_text()
    saved = HERE / "pages" / (url.rstrip("/").split("/")[-1].replace(".html", "") + ".html")
    html = saved.read_text(errors="replace") if saved.exists() else _download(url)   # a page saved by hand wins
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer", "aside"]):
        t.decompose()
    main = soup.find("main") or soup.find(id="docs-body") or soup.find(class_="content") or soup.body or soup
    text = re.sub(r"\n\s*\n+", "\n\n", main.get_text("\n"))
    f.write_text(text)
    return text


def local_docs(path: str, pages: str = "") -> list[tuple[str, str]]:
    """A downloaded manual: a PDF (only the pages asked for, e.g. 72-96,156-185) or a folder of HTML pages
    (the MySQL Reference Manual 'HTML download'). Returns (name, text) per page or file; no website involved."""
    p = Path(path).expanduser()
    out = []
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        r = PdfReader(str(p))
        want = set()
        for part in (pages or f"1-{len(r.pages)}").split(","):
            a, _, b = part.strip().partition("-")
            want.update(range(int(a), int(b or a) + 1))
        for n in sorted(want):
            if 1 <= n <= len(r.pages):
                out.append((f"{p.name} p{n}", r.pages[n - 1].extract_text() or ""))
        return out
    from bs4 import BeautifulSoup

    files = sorted(p.rglob("*.htm*")) if p.is_dir() else [p]
    for f in files:
        soup = BeautifulSoup(f.read_text(errors="replace"), "html.parser")
        for t in soup(["script", "style", "nav", "header", "footer"]):
            t.decompose()
        out.append((f.name, re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n"))))
    return out


def chunks(text: str, size: int = 3500) -> list[str]:
    out, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) > size and cur:
            out.append(cur)
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        out.append(cur)
    return [c for c in out if re.search(r"(?i)\b(group by|order by|having|join|select|union|null|distinct|limit|window)\b", c)]


# ------------------------------------------------------------------ the local model
def draft(url: str, text: str) -> list[dict]:
    body = json.dumps({"model": MODEL, "prompt": PROMPT.format(url=url, text=text[:3800]), "format": "json",
                       "stream": False, "options": {"temperature": 0.2}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    raw = json.loads(urllib.request.urlopen(req, timeout=300).read())["response"]
    try:
        rules = json.loads(raw).get("rules") or []
    except (ValueError, AttributeError):
        return []
    return [r for r in rules if isinstance(r, dict) and r.get("bad_sql") and r.get("fixed_sql")]


def _ollama_up() -> bool:
    try:
        urllib.request.urlopen(OLLAMA.rsplit("/api/", 1)[0] + "/api/tags", timeout=10).read()
        return True
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ the engines decide
SCHEMA = (HERE / "schema.sql").read_text()


def connect(name: str):
    e = ENGINES[name]
    if e["kind"] == "mysql":
        import pymysql

        return pymysql.connect(host="127.0.0.1", port=e["port"], user="root", password="ge", database="ge", autocommit=True,
                               connect_timeout=5, read_timeout=20)
    import psycopg

    return psycopg.connect(host="127.0.0.1", port=e["port"], user="postgres", password="ge", dbname="ge", autocommit=True,
                           connect_timeout=5)


def setup(conns: dict):
    for c in conns.values():
        cur = c.cursor()
        for t in ["order_items", "orders", "products", "customers", "employees"]:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        for stmt in [x.strip() for x in re.sub(r"--[^\n]*", "", SCHEMA).split(";") if x.strip()]:
            cur.execute(stmt)


def version(c) -> str:
    cur = c.cursor()
    cur.execute("SELECT VERSION()")
    return str(cur.fetchone()[0]).split(" on ")[0][:40]


def run(c, sql: str):
    """('ok', None, None) or ('error', code, message). Read-only: anything but one SELECT is refused before it runs."""
    q = sql.strip().rstrip(";")
    if ";" in q or not re.match(r"(?is)^\s*(select|with)\b", q) or re.search(r"(?i)\b(insert|update|delete|drop|alter|create|truncate|grant)\b", q):
        return "refused", None, "not a single SELECT"
    cur = c.cursor()
    try:
        cur.execute(q)
        cur.fetchall()
        return "ok", None, None
    except Exception as exc:  # noqa: BLE001 - the error IS the evidence
        args = getattr(exc, "args", ()) or ()
        code = args[0] if args and isinstance(args[0], int) else getattr(exc, "sqlstate", None)   # MySQL errno / PG SQLSTATE
        msg = str(exc.args[1]) if len(getattr(exc, "args", ())) > 1 else str(exc).splitlines()[0]
        try:
            c.rollback()
        except Exception:  # noqa: BLE001
            pass
        return "error", code, msg[:240]


def verify(rule: dict, conns: dict, valid: list[str]) -> dict | None:
    evidence = {}
    for name, c in conns.items():
        b = run(c, rule["bad_sql"])
        f = run(c, rule["fixed_sql"])
        if b[0] == "refused" or f[0] == "refused":
            return None
        if b[0] == "error" and f[0] == "ok":
            evidence[name] = {"code": b[1], "message": b[2]}
    if not evidence:
        return None                                    # the model said it's an error; no engine agrees
    detect = rule.get("detect") or ""
    try:
        rx = re.compile(detect, re.I | re.S) if detect else None
    except re.error:
        rx = None
    if rx and not (rx.search(rule["bad_sql"]) and not rx.search(rule["fixed_sql"]) and not any(rx.search(v) for v in valid)):
        rx = None                                      # finds the wrong things: keep the rule, drop the pattern
    return {"title": str(rule.get("title", ""))[:120], "why": str(rule.get("why", ""))[:300], "fix": str(rule.get("fix", ""))[:300],
            "bad_sql": rule["bad_sql"].strip(), "fixed_sql": rule["fixed_sql"].strip(), "detect": rx.pattern if rx else None,
            "engines": evidence}


def valid_queries(n: int = 400) -> list[str]:
    """Correct queries on random real schemas (the repo's generator): a pattern that fires on these is wrong."""
    sys.path.insert(0, str(ROOT))
    try:
        import random

        from tests.fuzz import db as F
    except Exception:  # noqa: BLE001 - no generator: the CI test still guards
        return []
    out = []
    for seed in range(1, n // 3 + 1):
        schema, rnd = F.make_schema(seed), random.Random(seed)
        for _ in range(3):
            out.append(F.make_query(schema, rnd)[0])
    return out


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the first N doc pages")
    ap.add_argument("--file", default="", help="a downloaded manual: a .pdf or a folder of .html pages")
    ap.add_argument("--pages", default="", help="with a PDF: which pages, e.g. 72-96,156-185")
    ap.add_argument("--match", default="", help="with a folder: only files whose name contains one of these words (comma list)")
    ap.add_argument("--hours", type=float, default=0, help="stop after this many hours (nightly runs)")
    ap.add_argument("--only", default="", help="only sources whose address contains this (mysql, mariadb, postgresql)")
    args = ap.parse_args()
    known = json.loads(OUT.read_text()) if OUT.exists() else []
    seen = {r["id"] for r in known}
    done_file = CACHE / "done.txt"
    CACHE.mkdir(exist_ok=True)
    done = set(done_file.read_text().split()) if done_file.exists() else set()
    conns = {n: connect(n) for n in ENGINES}
    setup(conns)
    versions = {n: version(c) for n, c in conns.items()}
    valid = valid_queries()
    print(f"engines: {versions}; {len(valid)} valid queries guard the patterns")
    sources = json.loads((HERE / "sources.json").read_text())
    if args.file:                                             # a downloaded manual instead of the website list
        docs = local_docs(args.file, args.pages)
        if args.match:
            words = [w.strip().lower() for w in args.match.split(",") if w.strip()]
            docs = [d for d in docs if any(w in d[0].lower() for w in words)]
        text_by = {}
        for name, text in docs:                               # PDF pages are joined so sections aren't cut in half
            key = name.split(" p")[0] if name.endswith(tuple("0123456789")) and " p" in name else name
            text_by[key] = text_by.get(key, "") + "\n\n" + text
        _fetched.update(text_by)
        sources = list(text_by)
        print(f"{len(docs)} pages/files from {args.file}")
    if args.only:
        sources = [u for u in sources if args.only.lower() in u.lower()]
    if args.limit:
        sources = sources[:args.limit]
    added = drafted = 0
    stop_at = time.time() + args.hours * 3600 if args.hours else None
    for url in sources:
        if stop_at and time.time() > stop_at:
            print("time's up for tonight; the next run carries on from here")
            break
        try:
            text = fetch(url)
        except Exception as exc:  # noqa: BLE001
            print(f"skip {url}: {exc}")
            continue
        for part in chunks(text):
            key = hashlib.sha1((MODEL + url + part).encode()).hexdigest()[:16]
            if key in done:
                continue
            t = time.time()
            rules, fails = None, 0
            while rules is None:
                try:
                    rules = draft(url, part)
                except Exception as exc:  # noqa: BLE001 - a stuck or sleeping model: retry once, then skip this section
                    fails += 1
                    print(f"model didn't answer ({exc}); {'retrying' if fails < 2 else 'skipping this section'}")
                    if fails >= 2:
                        rules = []
                        if not _ollama_up():
                            print("Ollama isn't answering at all: open the Ollama app, then run the same command again")
                            return
            drafted += len(rules)
            for r in rules:
                v = verify(r, conns, valid)
                if v is None:
                    continue
                v["id"] = hashlib.sha1((v["bad_sql"] + v["fixed_sql"]).encode()).hexdigest()[:12]
                if v["id"] in seen:
                    continue
                v.update(source=url, verified_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"), versions=versions, model=MODEL)
                known.append(v)
                seen.add(v["id"])
                added += 1
                where = ", ".join("%s %s" % (k, e["code"]) for k, e in v["engines"].items())
                print("  + %s  [%s]%s" % (v["title"], where, "" if v["detect"] else "  (no pattern)"))
            done.add(key)
            done_file.write_text("\n".join(sorted(done)))
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(known, indent=1, ensure_ascii=False))
            print(f"{url.split('/')[-1] or url}: {len(rules)} drafted, {time.time() - t:.0f}s")
    print(f"\ndone: {drafted} drafted by the model, {added} proved by the engines and kept ({len(known)} in total)")
    print(f"file: {OUT.relative_to(ROOT)}   next: git add it, commit, push")


if __name__ == "__main__":
    main()
