# Database knowledge backlog

Work top to bottom, one item per run. Tick `[x]` when done (and note it in LOG.md).
Stack Overflow / DBA Stack Exchange show which problems are most common for each item; official docs are the truth.

## Issue playbooks, MySQL/MariaDB + PostgreSQL first  (issues.json, 10-20 playbooks per item)
- [x] Hacked database (defensive incident response, part 1 of 2): signs of a break-in (unknown admin users, new MySQL/PostgreSQL accounts or grants, unexpected triggers/events/stored procedures, spam links or scripts injected in posts/options, wp_users/wp_usermeta admin added, changed siteurl/home, ransom note table, data deleted), read-only checks for each, what to do right now (contain: rotate DB + app passwords, remove unknown users/grants, block remote access, take a copy for evidence), clean up, restore from backup, how to prove it's clean
- [x] Hacked database part 2: how they got in and how to close it (SQL injection signs in logs, leaked .env / wp-config, weak or reused passwords, DB port open to the internet, outdated plugins), prevention checklist (least-privilege users, no remote root, TLS, backups tested, audit logging), ransomware / "pay bitcoin" tables on exposed MongoDB/Elasticsearch/MySQL
- [ ] Connection: refused, timeout, too many connections (done: 1), gone away / server closed the connection, SSL required, auth plugin, pg_hba, max_user_connections
- [ ] Performance: site suddenly slow, one page slow, CPU 100%, high disk IO, slow after data grew, slow COUNT(*), slow LIKE search, slow ORDER BY + LIMIT, slow JOIN, temp tables on disk
- [ ] Locking: deadlocks, lock wait timeout, long-running transaction blocking, metadata lock on ALTER, idle in transaction
- [ ] Charset/encoding: ???? (done: 1), mixed collations "Illegal mix of collations", emoji, CSV import garbled, sorting wrong for Hindi
- [ ] Timezone: dates off by 5:30, NOW() vs UTC, DST, TIMESTAMP vs DATETIME
- [ ] Storage: disk full, table bigger than data (fragmentation), binlog/WAL filling disk, huge log tables, temp space
- [ ] Backup/restore: mysqldump/pg_dump errors, restore fails, partial restore of one table, point-in-time recovery, backup too slow
- [ ] Security/permissions: access denied variants, grant read-only user, remove root remote access, password policy, SQL injection signs
- [ ] Schema design: no primary key, no foreign keys, EAV/meta tables, wrong types, NULL handling, soft deletes, big VARCHAR/TEXT
- [ ] Migration/upgrade: MySQL 5.7 -> 8 breaking changes, MariaDB vs MySQL differences, PostgreSQL major upgrade, Laravel migration failures
- [ ] Config tuning: innodb_buffer_pool_size, shared_buffers/work_mem, max_connections vs RAM, query cache (removed in 8.0)
- [ ] ORM/framework: Laravel N+1, Eloquent slow queries, WordPress slow admin (wp_options autoload), WooCommerce order queries, CodeIgniter
- [ ] Replication: replica lag, replication stopped, duplicate key on replica
- [ ] Data quality: duplicates, orphans, bad dates, trailing spaces, inconsistent case
- [ ] Crash/recovery: table marked as crashed, InnoDB recovery, corrupted index, PostgreSQL won't start
- [ ] Memory: out of memory kills, too much RAM per connection, sort/join buffers
- [ ] Query writing: how to do X in SQL (pivot, running total, top N per group, gaps and islands, upsert, delete duplicates keeping one)

## Big failures (learned from public post-mortems)
- [ ] Replication lag and broken replicas, failover gone wrong / split brain, backup that never restored (GitLab 2017), accidental DELETE/DROP in production and how to recover, runaway migration locking a big table, connection pool exhaustion cascading into an outage

## Errors -> plain words -> fix  (errors.json)
- [ ] MySQL/MariaDB: the 40 most common server errors (syntax 1064, access 1045/1142/1227, duplicate key 1062, foreign key 1451/1452, data too long 1406, incorrect value 1366/1292, deadlock 1213, lock wait 1205, too many connections 1040, gone away 2006/2013, ONLY_FULL_GROUP_BY 1055, divide by zero, out of range 1264, ...)
- [ ] PostgreSQL: the 40 most common SQLSTATE codes (42601 syntax, 42P01 undefined table, 23505 unique, 23503 FK, 22P02 invalid text, 22001 too long, 40P01 deadlock, 53300 too many connections, 57014 statement timeout, 42501 permission, 22012 division by zero, 22003 out of range, ...)
- [ ] Client/connection errors for both (refused, timeout, SSL required, authentication plugin caching_sha2_password, pg_hba.conf)

## Everyday questions -> query templates  (questions.json)
- [ ] Counts and totals: total rows, count where, sum/avg/min/max of a column, count by a category column, top N by a value
- [ ] Time: today, yesterday, this week/month/year, last N days/months, by day/week/month/year, compare this month vs last month, growth %
- [ ] Customers: new customers per month, repeat customers, inactive since N days, top customers by spend, first/last order date
- [ ] Data quality: duplicates, empty/NULL values per column, invalid emails/phones (regex), orphan rows (child without parent), future dates
- [ ] Money: revenue by day/month, pending vs paid, average order value, refunds
- [ ] Search: find rows containing text in any text column of a table (bounded, LIMIT)

## Slow queries  (slow_rules.json)
- [ ] MySQL optimization guide: OR across columns, NOT IN with NULLs, implicit type conversion (string vs number), ORDER BY RAND(), SELECT * on wide tables, missing composite index order, DISTINCT + ORDER BY, subquery in SELECT per row, COUNT(*) on huge tables
- [ ] PostgreSQL: sequential scans on big tables, missing index on FK columns, LIKE without pg_trgm, functions on columns, CTE materialization (older versions), N+1 patterns

## Reading an undocumented database  (structure.json)
- [ ] Link guessing: framework conventions (Laravel, WordPress wp_* tables and meta tables, CodeIgniter, Django, Rails), plural/singular, prefixes
- [ ] Type smells: money as FLOAT, ids as VARCHAR, booleans as VARCHAR, dates as text, phone as INT, oversized VARCHAR, utf8 vs utf8mb4
- [ ] Table purpose guessing: users/auth, orders, payments, products, logs, sessions, settings, jobs/queues, migrations, cache
- [ ] Index rules: duplicate/redundant indexes, unused indexes (perf schema / pg_stat_user_indexes), missing index on join/filter columns

## Real-world problems (Stack Overflow as the guide, official docs as the truth)
- [ ] Top 50 MySQL/MariaDB questions on Stack Overflow + DBA Stack Exchange (by votes): check each is covered by errors/questions/slow_rules; add what's missing (with `seen_at` links)
- [ ] Same for PostgreSQL
- [ ] Laravel / WordPress / WooCommerce database problems people hit most (N+1, wp_options autoload bloat, postmeta joins, missing indexes on meta tables)

## Recognise popular software from its tables (GitHub as facts)  (structure.json, kind "app_signature")
- [ ] WordPress core + WooCommerce (+ HPOS order tables) + common plugins (Yoast, Elementor): table sets, what each table holds, key links
- [ ] Laravel defaults (users, password_reset_tokens, jobs, failed_jobs, sessions, cache, migrations, personal_access_tokens), Spatie permission tables
- [ ] Magento 2, OpenCart, PrestaShop, Django auth/admin, CodeIgniter sessions

## Ideas from well-known database tools (write our own rules; MIT/Apache only if copying)
- [ ] MySQLTuner checks -> health rules; pt-query-digest / pgBadger report sections -> daily report ideas; SQL linter anti-patterns -> slow_rules

## Every other database (errors + playbooks + structure queries + question templates)
- [ ] SQL Server (T-SQL): common errors, playbooks (blocking, tempdb, log full, parameter sniffing), sys.* catalog queries
- [ ] Oracle: ORA- errors, playbooks (tablespace full, ORA-01555, sessions), data dictionary
- [ ] SQLite: database is locked, file corruption, WAL mode, size growth
- [ ] MongoDB: slow queries without index, connection pool, duplicate key, schema validation, aggregation memory limit
- [ ] Redis: OOM / maxmemory, eviction, keys growing, persistence (RDB/AOF), latency
- [ ] Others as Stack Overflow shows demand: CockroachDB, ClickHouse, Elasticsearch, DynamoDB, Cassandra, DB2

## Later: more database engines
- [ ] SQL Server: error numbers, catalog queries (sys.tables, sys.columns, sys.indexes), DMVs for live view, templates in T-SQL
- [ ] Oracle: ORA- errors, data dictionary (ALL_TABLES, ALL_TAB_COLUMNS), templates
- [ ] SQLite: errors and templates
