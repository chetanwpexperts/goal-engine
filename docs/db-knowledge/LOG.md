# Database knowledge log

Newest first. One entry per nightly run.

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
