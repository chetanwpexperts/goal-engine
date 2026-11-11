# DBaba curriculum

DBaba is Goal Engine's database agent. It solves MySQL, MariaDB and PostgreSQL problems and checks or explains SQL
queries without AI, in English, Hinglish or Hindi, by reasoning from fundamentals rather than matching stored answers.
This file is the plan it grows by: one night per nightly run, ticked off in the same pull request that does the work.

## How every night is learned

- The topic lists come from guide documents. They point at what to learn; they are not the only source. Each night learns
  the reasoning behind a topic from the official manuals (dev.mysql.com, mariadb.com/kb, postgresql.org/docs), then from
  people who teach and answer it (Stack Overflow, DBA Stack Exchange, r/SQL, Use The Index Luke, database blogs).
- Everything is written in our own words. No copied text or code.
- The lab decides: every mistake is reproduced on MariaDB and PostgreSQL with small data, and only what the lab confirms
  becomes a rule. Every rule records the engines it was proven on.
- A rule is general, never one example: a check in the SQL checker (or the reasoning core for server errors), a safe
  rewrite that does the intended thing on every engine, and the fundamental it belongs to
  (`app/dbknow/core/query_fundamentals.json`). Sure mistakes are `error`/`wrong`; anything that depends on the data or on
  what the person meant is `check`, and its rewrite is only offered ("If that's what you meant").
- Real users first: the learning feed's database items (questions DBaba couldn't answer, answers people said were wrong)
  come before the night's topic, and each becomes a permanent test.
- Each night adds a practice bank (30+ problems on the practice schema, verified on both databases), new generator
  families (400+ random statements, 0 problems), and 15+ concept questions in three languages.

## Standing product rules

**A. The user's database decides the answer.** Named ("this is a MySQL query", "postgres error", "SQL Server") -> answer
only for that database and remember it on the goal until another one is named. Not named but shown by the text (backticks
or `LIMIT a,b` -> MySQL/MariaDB; `::` casts, `ILIKE`, `RETURNING`, `$1` -> PostgreSQL; `TOP n`, `[brackets]`, `GETDATE` ->
SQL Server; `NVL`, `ROWNUM`, `DUAL` -> Oracle; or the engine's own error format) -> answer for it, say which one was
detected, with a one-tap way to correct it. Neither -> ask first (MySQL / MariaDB / PostgreSQL / SQL Server / Oracle / Not
sure); only "Not sure" gets an answer per engine, labelled. SQL Server, Oracle and SQLite are general SQL reasoning plus
notes until they have lab-tested knowledge of their own. Basic concept questions that are the same everywhere are answered
directly, with the engine differences added.

**B. Shared core, per-engine files.** What is true for every database lives in the shared core (`app/dbknow/core`, the
query fundamentals, general rules, concepts). What belongs to one engine lives only in
`app/dbknow/dialects/<engine>.json`. Every rule says which engines proved it, so each engine can later become its own
sub-agent.

**C. A running score in every PR.** Catch rate (planted mistakes found / planted, across the generators), false-alarm rate
(error/wrong findings on valid queries) and basic-question answer rate (concept questions answered correctly / asked),
tonight vs last night.

**D. Basic and concept questions get answers.** "What is a JOIN", "LEFT JOIN vs INNER JOIN", "WHERE vs HAVING", "primary key
kya hota hai", "DELETE vs TRUNCATE", "how to find duplicate emails", "how to delete duplicate rows in mysql": a short correct
answer in the language asked, with what it is, when to use it, one example query run on MariaDB and PostgreSQL with its
result, the common mistake, and the engine differences. How-to questions get a tested template for the user's database;
anything that changes data comes with a preview query first. Knowledge lives in `app/dbknow/core/concepts.json`. Question
shapes are recognised generally (what is / kya hota hai / X vs Y / difference between / how to / kaise / when to use / why),
never as fixed sentences. These questions never go to the query checker, the error diagnoser, a connect step or the generic
"no specific answer" reply. An unknown concept is said honestly, with the nearest known ones, and recorded in the learning
feed.

## The 15 nights

- [x] **1. Foundation and basics.** Merge the query-fundamentals and learn-from-misses work (fundamentals behind every
  finding, reading a query stage by stage, keeping answers users say are wrong). Build rule D: question shapes, the concepts
  pack, routing before the checker. Seed concepts (SELECT, WHERE, ORDER BY, LIMIT, DISTINCT, NULL, LIKE, GROUP BY, HAVING,
  WHERE vs HAVING, every join type, keys, UNIQUE, index, normalization, DELETE/TRUNCATE/DROP, transaction, view, duplicates).
  Basics: NULL in arithmetic and CONCAT, LIKE wildcards and escaping, ties before LIMIT, DISTINCT vs GROUP BY, AND/OR order.
- [x] **2. Rule A, then joins.** Named / detected / ask, in three languages, including a switch mid-conversation. Then every
  join type, anti-join, self-join, missing or wrong join condition (row explosion), joining on the wrong column, duplicates
  hidden by DISTINCT.
- [x] **3. Aggregation.** COUNT(*) vs COUNT(col) vs COUNT(DISTINCT), AVG/SUM with NULLs, SUM(CASE ...), totals at the wrong
  grain, fan-out through two child tables, percent of total.
- [ ] **4. Subqueries.** IN vs EXISTS vs JOIN (same rows or not), correlated subqueries in SELECT, latest/first row per
  group, anti-joins with NULLs.
- [ ] **5. Changing data.** UPDATE/DELETE with JOIN per engine, upsert, INSERT ... SELECT column order, preview before
  change, batching, isolation basics; procedures, functions and triggers (DELIMITER, endless loops, missing handlers,
  trigger recursion).
- [ ] **6. Schema design.** Reading CREATE TABLE: missing primary key, unindexed foreign key, money as FLOAT, dates or
  numbers stored as text, utf8 vs utf8mb4, over-wide types; advising a better structure.
- [ ] **7. CASE, CTEs, windows.** CASE order and missing ELSE, recursive CTEs that must stop, window frames and ties in
  running totals, LAG/LEAD, NTILE, gaps and islands, versions without window functions.
- [ ] **8. Indexes and EXPLAIN.** Reading EXPLAIN on both engines, composite indexes and the leftmost prefix, sargable
  rewrites, OR -> UNION ALL, keyset pagination, covering indexes; every speed rewrite proven to return the same rows.
- [ ] **9. Concurrency and large data.** Deadlocks, lock waits, long transactions, batching huge changes, N+1; a deadlock
  and a lock wait reproduced in the lab.
- [ ] **10. Mixed hard problems and portability.** Long CTE-heavy queries end to end; LIMIT/OFFSET/FETCH/TOP,
  IFNULL/COALESCE/NVL/ISNULL, dates, upsert, case, identities; Oracle / SQL Server / SQLite recognised and answered as notes.
  Final exam: 50 generated problems and 100 basic questions, all passing.

Query-only reasoning (nights 11-15): with only the pasted query, never invent schema facts; prove what the text proves,
label the rest, upgrade to verified when schema, EXPLAIN or data arrive.

- [ ] **11. Confidence classes and the inferred schema.** PROVEN / LIKELY RISK / NEEDS SCHEMA / NEEDS EXECUTION on every
  finding; an inferred schema (verified=false); "what can't be proven from the query alone"; index advice worded as "worth
  checking".
- [ ] **12. Grain at every stage.** Blocks and their dependency tree; input/output grain per block; re-check totals after a
  grain-changing join; tree and grain shown for 30+ line queries.
- [ ] **13. Remaining structural checks.** Cartesian joins, ON that doesn't connect its tables, broken or repeated correlated
  subqueries, DISTINCT hiding duplicates, UNION vs UNION ALL, late filters, big OFFSET, functions on filtered columns,
  leading wildcards, SELECT * across joins; speed findings are risks, not guarantees.
- [ ] **14. Safe rewrites with proof.** Pre-aggregate repeated correlated subqueries, IN <-> EXISTS, compute once, UNION ->
  UNION ALL only when safe, OFFSET -> keyset; each states what must stay the same and is proven on both databases.
- [ ] **15. Big-query exam.** Generated 100-300 line queries with planted mistakes: dependency tree, grain per block, the
  planted findings with the right confidence class, a proven rewrite; the same query with CREATE TABLE / EXPLAIN upgrades or
  drops inferred findings.

## After night 15

Real users' feed first, then the weakest area the generators or feed show, then in order: vacuum and bloat (PostgreSQL),
charset and collation, permissions and grants, backups that won't restore, timeouts and pool exhaustion, the planner and
statistics. Every night still adds 15+ concepts from its area. A new guide document gets its own nights added here.

## Night log

- **Night 1 (2 Nov 2026).** Concepts pack with 40 concepts (215 phrasings in English, Hinglish, Hindi), every example run on
  MariaDB and PostgreSQL. Basic SELECT rules in `app/agents/sqlbasics.py` with per-engine facts in the dialect files. 37-problem
  practice bank, 10 new generator families. Lab lessons worth keeping: NULLs sort first in ASC on MariaDB and first in DESC on
  PostgreSQL; text sorts case-blind on MariaDB and case-sensitive on PostgreSQL; `ORDER BY 1 IS NULL` is a constant, not
  column 1; `||` is OR on MariaDB; integer division truncates on PostgreSQL.
- **Night 2 (5 Nov 2026).** Rule A in `app/dbknow/engines.py`: the database named in the words is remembered on the goal and
  switched when another is named; one shown by the query's syntax or the error's format is detected, said, and correctable with
  one tap; otherwise DBaba asks first (MySQL / MariaDB / PostgreSQL / SQL Server / Oracle / Not sure), for queries and for errors.
  How each database is named, its syntax marks, its error format and what it refuses live in its dialect file (`named`,
  `query_marks`, `weak_marks`, `error_marks`, `refuses`); SQL Server, Oracle and SQLite are `notes_only`. Joins in
  `app/agents/sqljoins.py` (fundamental `join_condition`): cartesian comma joins, an ON that names one side, two primary keys
  compared, a key the wrong way round or pointing at another parent, names instead of keys, NATURAL JOIN / USING (id), anti-joins
  testing a nullable column, self-join pairs, DISTINCT hiding a join's copies (rewritten to EXISTS). 18 concepts, 42-problem
  practice bank, 11 generator families. Lab lessons worth keeping: `JOIN t` with no ON is a cross join on MariaDB but a syntax
  error on PostgreSQL; NATURAL JOIN of two tables with `id` pairs row N with row N; a `-uses` plural is not always `-us`
  (warehouses, not warehous); a backticked name on PostgreSQL is safest bare (unquoted names fold to lower case).
- **Night 3 (11 Nov 2026).** Aggregation in `app/agents/sqlaggs.py` (new fundamental `aggregation`: a total sees only the rows
  and the non-NULL values that reach it, once per group): COUNT of CASE ... ELSE 0 / IF(c, 1, 0) / a comparison counts every
  row; a total inside a total (MAX(COUNT(*))); a plain column inside a window of a grouped query; SUM over a LEFT JOIN is NULL,
  not 0; AVG skips NULLs; SUM(DISTINCT x); an average of averages (rewritten to total / count); a share whose total ignores the
  outer WHERE; 'how many customers' while the query counts order rows (request words). Per-engine facts in the dialect files'
  query_rules (`sum_boolean`, `having_alias`, `count_distinct_multi`, `window_over_ungrouped`). 21 concepts, 40-problem practice
  bank, 12 generator families. The statement generator now really compares a SELECT's rows on MariaDB (pymysql returns a
  tuple, which was being read as "not a SELECT"). Lab lessons worth keeping: MariaDB runs `SUM(amount) OVER ()` in a grouped
  query and sums one arbitrary row per group (silently wrong; PostgreSQL 42803); `COUNT(DISTINCT a, b)` skips NULL pairs on
  MariaDB but `COUNT(DISTINCT (a, b))` counts them on PostgreSQL; a HAVING name that is also a real column means the column on
  PostgreSQL; MariaDB 10.11 has only `WITH ROLLUP` (no `ROLLUP (...)`, no `GROUPING()`, no `FILTER`).
