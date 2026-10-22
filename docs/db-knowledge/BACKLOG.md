# Database knowledge backlog

Work top to bottom, one item per run. Tick `[x]` when done (and note it in LOG.md).

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

## Later: more database engines
- [ ] SQL Server: error numbers, catalog queries (sys.tables, sys.columns, sys.indexes), DMVs for live view, templates in T-SQL
- [ ] Oracle: ORA- errors, data dictionary (ALL_TABLES, ALL_TAB_COLUMNS), templates
- [ ] SQLite: errors and templates
