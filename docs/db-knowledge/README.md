# Database knowledge: nightly build rules

Goal Engine solves database problems **without AI**. Its "brain" is the knowledge in `app/dbknow/*.json`.
A scheduled session adds to it every night. These rules are for that session (and any human doing the same work).

## Sources
- **Truth comes from official documentation** (dev.mysql.com, mariadb.com/kb, postgresql.org/docs, learn.microsoft.com, docs.oracle.com, sqlite.org). Every entry's `source` is an official page.
- **Stack Overflow / DBA Stack Exchange are the teacher**: the best map of the real problems developers hit, for every database. Use them to discover the variety of issues, how people describe them, what the causes turned out to be, and which fixes worked; then write OUR OWN entry, checked against the official docs. Never copy answer text or code. Read questions to pick the most common real problems, then write our own entry from the official docs.
  Put the question links in the optional `seen_at` list. Never copy answer text or code (CC BY-SA would then apply to Goal Engine). Don't scrape in bulk; read pages normally.
- **GitHub: facts and ideas, not code.**
  - Table structures of popular software (WordPress core, WooCommerce, Laravel defaults, Magento, Django auth, OpenCart, PrestaShop) are facts: record them in `structure.json` as recognisers ("this table set = WooCommerce").
  - Well-known database tools (MySQLTuner, pt-query-digest, pgBadger, SQL linters) show which problems are worth detecting: write our own rules. Copy text/code only from MIT/Apache/BSD projects with the license noted in the entry, and **never from GPL/AGPL projects**.

- **Other good sites** (Percona, PlanetScale, Cybertec, Crunchy Data, Use The Index Luke, vendor blogs): ideas only, written in our own words, cited in `seen_at`.
- **Real incidents and post-mortems** (AWS / Azure / Google Cloud post-event summaries, GitLab's 2017 database incident write-up, GitHub, Uber, Shopify, Cloudflare engineering blogs): the best teacher for big failures (replication lag, split brain, failover, backups that didn't restore, cascading outages). Ideas only, our own words, cited in `seen_at`.
- **Database hubs** (DbVisualizer "The Table", Sematext, Percona, Planet MySQL / Planet PostgreSQL): ideas only, cited in `seen_at`.
- **Practice platforms** (LeetCode Database, HackerRank SQL): their problems are copyrighted. Use them only to learn WHICH kinds of query questions people struggle with (top N per group, running totals, gaps and islands, ...), then write our own question templates. Never copy a problem or its solution.
- **Forums** (Reddit r/Database, r/SQL, r/PostgreSQL, r/mysql, r/Wordpress): how real people describe problems and which fixes worked. Ideas and wording only, never copied posts.
- **Security** (OWASP, CISA advisories, MySQL/PostgreSQL security docs, WordPress.org hardening guide, vendor incident-response guides): for the "my database was hacked" playbooks. **Defensive only**: how to spot a break-in, stop it, clean up, recover and prevent it. Never write attack steps, injection payloads or ways to break into a database.
- **Your own experience as Claude** with the database questions people ask every day counts too: write it as our own entry, but every claim must match an official doc (`source`).

## Goal
One place where anyone can get ANY database problem solved without AI, for every database
(MySQL, MariaDB, PostgreSQL, SQL Server, Oracle, SQLite, MongoDB, Redis and more).
The most useful entry is an **issue playbook** (`issues.json`): a short title (English + Hinglish), how people describe it (English + Hinglish, at least 3 ways),
the read-only checks the agent runs by itself to find the cause, the possible causes, the fix for each cause,
and how to prove it's fixed.

## Never
- Never look at, connect to, or collect anything from any user's database or Goal Engine's production data.
- Never push to `main`, never deploy, never touch the server. Work only on the `db-knowledge` branch.
- Never add AI/LLM calls or paid APIs.
- Never weaken a safety rule (read-only checks, approval + Undo, privacy wipes). If a task seems to need that, stop and write it in LOG.md for Chetan.

## Each run
1. `git checkout db-knowledge && git pull` (create it from `main` if missing; merge `main` in if it moved ahead).
2. `bash scripts/setup_test_dbs.sh` (local MariaDB + PostgreSQL test databases), `.venv` with `pip install -r requirements.txt pytest httpx`.
3. Take the **first unchecked item** in `BACKLOG.md`. Do one item well rather than many badly (about 10–40 entries per run).
4. Read the official docs for it. Add entries to the right JSON file. **Every entry needs an `https://` `source` link** to the page it came from.
   - Plain-language text in both English (`*_en`) and simple Hinglish (`*_hi`), short, for a small-business owner.
   - Query templates must be read-only (`SELECT`/`WITH`), use `{placeholders}`, and be written for each engine they support.
5. **Prove it works before keeping it:**
   - Every playbook and question gets `test_phrases`: at least 3 NEW ways a person would say it, one each in English, Hinglish (Roman) and Hindi (Devanagari, e.g. "मेरा डेटाबेस धीमा है"), NOT copies of the symptoms/examples. Write them the way a normal non-technical user (or voice typing) would say it, not like a developer.
     `tests/test_dbbrain.py` checks the brain recognises them. If it doesn't, add richer `symptoms`/`examples` (more variety, real wording from Stack Overflow titles) until it does. Never weaken the test phrases to make it pass.
   - Add the error codes a problem shows (`codes`: ["1040", "53300"]) so "error 1040 aa raha hai" is recognised directly.
   - Every check and template is executed against the real test databases (tests/test_dbknow.py).
6. Run the **whole** suite: `.venv/bin/python -m pytest -q`. Everything must pass. If something fails that you can't fix, revert your change.
7. Tick the item in `BACKLOG.md`, add a dated entry to `LOG.md` (what was added, how many entries, anything Chetan should decide), commit, push `db-knowledge`.

## File formats
See `app/dbknow/__init__.py` (`REQUIRED`, `FIX_TYPES`) and the existing entries. `dbknow.validate()` must return `[]`.

## The test generator (proves the database agent on data it has never seen)
`tests/fuzz/db.py` is to the database agent what `tests/fuzz/sheets.py` is to the Excel agent. Each seed makes a random
schema from a field (school, shop orders, HR, clinic, hotel, inventory, fees, attendance, loans), with primary keys
(`id` or `<table>_id`), foreign keys, some existing indexes, snake/camel-case names and random data (NULLs, dates, text,
numbers). It creates the tables (`fz<seed>_*`) on the local test databases (MySQL/MariaDB `shop_db`, PostgreSQL `shop_pg`,
from `scripts/setup_test_dbs.sh`), runs the checks and drops them. An engine whose server is down is skipped.
All data is generated: never real users' databases, real names or secrets.

Three families, with the truth worked out by the generator (from the schema, the data and the engines), never by the agent:
- **queries**: random SELECT / UPDATE / DELETE (filters, functions on columns, LIKE, AND/OR, joins, GROUP BY, ORDER BY,
  LIMIT/OFFSET, IN/EXISTS subqueries, comments, odd spacing, wrapped in PHP / heredoc / JS / Python / Java code).
  `analyze_sql` must not crash, `unwrap` must give the bare query, a rewrite must return exactly the same rows on every
  engine where the original runs, every suggested index must name real columns, never a primary key or an already indexed
  leading column, and must really run through the connector (create, then Undo), wording must match what the agent can
  know (structure pasted or not), and findings must match what was put in the query.
- **followups**: a chat through the API (slow database, the query, then optimise / what changed / why slow / which
  indexes / explain, in English, Hinglish and Hindi): the right kind of answer, never the same reply twice, no generic
  fallback, no code wrapper left, next suggestions about the query.
- **phrasings**: new ways of describing every playbook in `issues.json`: the right playbook, or "not sure".
  Confidently wrong is a failure.

```
python -m tests.fuzz.db --start 1 --seeds 150 --json out.json     # pass rate per family, coverage, every failure
python -m tests.fuzz.db --seed 7 --show                           # one seed, every item printed
```

`tests/test_fuzz_db.py` runs a small fixed set of seeds in the normal suite (under a minute) plus every failure the
generator ever found, as a permanent case.

### In the nightly run
1. After setup, run the generator on **new seeds every night**:
   `.venv/bin/python -m tests.fuzz.db --start $(( $(date +%Y%m%d) % 100000 * 10 )) --seeds 150 --json fuzz-db.json`
2. **Fix its failures before taking a backlog item.** Group them, fix the biggest group first: knowledge in
   `app/dbknow/*.json` first (richer symptoms/examples), code in `app/agents/sql.py`, `app/agents/database.py` or
   `app/dbknow/` only when data can't fix it. Re-run the same seeds until the pass rate stops improving.
3. Add each fixed failure to `tests/test_fuzz_db.py` as a permanent case.
4. Never make a check in the generator more lenient to get a pass, and never weaken `test_phrases`.
   If a failure can't be fixed safely, write it in `LOG.md` for Chetan (seed, family, what fails).
5. Note the pass rate per family in `LOG.md`.
