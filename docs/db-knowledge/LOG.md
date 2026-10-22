# Database knowledge log

Newest first. One entry per nightly run.

## 2026-10-22 (nightly: hacked database, part 2)
- Feed: not read this run (the fetch needed a permission nobody was there to give), so no feed request ids covered.
- Backlog: "Hacked database part 2" done. 17 new defensive playbooks in `issues.json`:
  how did they get in (entry-point triage), SQL injection signs, leaked config (.env / wp-config / backup / git), brute-force logins,
  weak or reused passwords, DB port open to the internet, remote root / superuser, app user too powerful (least privilege),
  database can read/write server files (FILE, secure_file_priv, server-file roles), no TLS, no audit logging,
  outdated WordPress/plugins, backups never tested, prevention checklist, exposed MongoDB ransom, exposed Elasticsearch ransom, exposed Redis.
- MySQL/PostgreSQL checks are read-only (information_schema, performance_schema, pg_catalog, wp_options) and run on the MariaDB + PostgreSQL test DBs.
  MongoDB/Elasticsearch/Redis playbooks carry read-only commands in `cmd` (not run by the SQL checks; no test DB for them yet).
  `SHOW GRANTS` deliberately not used: on MariaDB it prints the password hash.
- 51 new test phrases (English, Hinglish, Hindi script) all recognised; all 109 test phrases in the brain pass. Added a few more symptoms to
  "is my database hacked" because a new playbook had pulled two of its phrases away.
- No `codes` added: these problems have no error code of their own (1129 "host blocked" is usually a connection problem, left for the Connection item).
- Fixed `tests/test_phpconn.py`: its positional `REPLACE INTO wp_users VALUES (...)` broke after part 1 added WordPress columns to the test table;
  it now names its 4 columns (same assertions, nothing weakened).
- Sources: MySQL security guidelines / privileges / validate_password / host_cache, PostgreSQL ssl-tcp / logging / connection / auth-password / backup,
  OWASP SQL Injection Prevention Cheat Sheet, WordPress hardening + updating, MongoDB security checklist, Elastic minimal security, Redis security.
  Ideas from GitLab 2017 post-mortem, Sucuri, Mailgun, Laracasts, Akamai, Imperva, BleepingComputer, Trend Micro, discuss.elastic.co (in `seen_at`). Defensive only.
- For Chetan: (1) Full suite: 6 failed + 35 errors, all because `tests/data/practice.xlsx` is ignored by `.gitignore` (`data/`) and missing on a fresh clone;
  not from this change. (2) Should `cmd` checks for MongoDB/Redis/Elasticsearch get a runner + test containers later?

## 2026-10-22 (nightly: hacked database, part 1)
- Feed: not read this run (the fetch needed a permission nobody was there to give), so no feed request ids covered.
- Backlog: "Hacked database part 1" done. 16 new defensive playbooks in `issues.json`: is my database hacked (triage), what to do right now (contain),
  unknown WordPress admin, locked out (admin password/email changed), unknown DB accounts/grants, planted triggers, unknown events/procedures/functions,
  spam links/scripts in posts, site redirects (siteurl/home), ransom-note table, data deleted/changed (find what and when), PostgreSQL takeover/crypto-miner,
  open registration with administrator role, malicious code in wp_options/widgets, restore a clean backup, prove it's clean.
- All checks are read-only (information_schema / pg_catalog / wp_* tables); they run on the MariaDB + PostgreSQL test DBs.
  `scripts/setup_test_dbs.sh` now also creates WordPress stub tables (wp_usermeta, wp_options, wp_posts, extra wp_users columns) so WordPress checks are tested.
- 48 test phrases (English, Hinglish, Hindi script) all recognised. Generic "site hack ho gayi" goes to the triage playbook.
- No `codes` added: a hack has no error code of its own, and generic codes (1045, 1146, 28P01) would wrongly route ordinary errors to "hacked".
- Sources: WordPress "FAQ My site was hacked" + hardening guide, MySQL/PostgreSQL manuals; ideas from Sucuri, NinTechNet, Border0, Akamai, Imperva, Wiz, Aqua (cited in `seen_at`). Defensive only.
- For Chetan: (1) WordPress checks assume the default `wp_` table prefix. (2) The full suite has 6 failures + 35 errors that are NOT from this change (same count before it):
  `tests/data/practice.xlsx` and `tests/data` are ignored by the `data/` line in `.gitignore`, so they never reached GitHub; the Excel/follow-orders/ML tests fail on a fresh clone.
  (3) Checking MySQL accounts fully needs SELECT on mysql.user; the checks use information_schema.USER_PRIVILEGES instead, which shows all accounts only to an admin user.

## 2026-10-22 (set up by hand)
- Created the knowledge format (`app/dbknow`), validator, tests that run templates on real MySQL + PostgreSQL, and this backlog.
- Seed entries: 5 errors, 3 question templates, 3 slow-query rules, 2 structure rules.
