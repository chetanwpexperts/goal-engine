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
- [x] **4. Subqueries.** IN vs EXISTS vs JOIN (same rows or not), correlated subqueries in SELECT, latest/first row per
  group, anti-joins with NULLs.
- [x] **5. Changing data.** UPDATE/DELETE with JOIN per engine, upsert, INSERT ... SELECT column order, preview before
  change, batching, isolation basics; procedures, functions and triggers (DELIMITER, endless loops, missing handlers,
  trigger recursion).
- [x] **6. Schema design.** Reading CREATE TABLE: missing primary key, unindexed foreign key, money as FLOAT, dates or
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
- **Night 4 (11 Nov 2026).** Subqueries in `app/agents/sqlsubq.py` (fundamental `subquery_shape` widened: where a subquery stands
  says how much it may return; new fundamental `correlation`: a subquery reads its own FROM first and is tied to the outer row
  only through an outer name). Rules: EXISTS (SELECT COUNT(*) ...) is always true; x = x inside a correlated subquery (an alias
  twice, or the same table name inside and outside); a subquery that returns the outer row's own column; an id compared with
  another table's id; IN (SELECT MAX(y) ... GROUP BY g) forgetting whose maximum it is (rewritten to `(g, y) IN`); two columns
  where one is compared; a scalar subquery in SELECT tied on a non-key; a correlated SUM that is NULL for nothing; LIMIT with no
  ORDER BY in a subquery; > ALL with NULLs or no rows; NOT IN (..., NULL) and IN (..., NULL); NOT IN dropping rows whose own value
  is NULL; EXISTS with no link to the outer row; a JOIN used only to keep parents that have a child (rewritten to EXISTS).
  Per-engine facts in the dialect files' query_rules (`limit_in_subquery`, `derived_alias`). The checker no longer crashes on a
  total inside a scalar subquery in SELECT, `JOIN LATERAL (...) t ON true` is no longer called a join that mentions nothing, and a
  name inside a WHERE subquery or inside a total in HAVING is no longer taken for a SELECT name. 18 concepts, 41-problem practice
  bank, 15 generator families. Lab lessons worth keeping: `orders.customer_id` inside a subquery that reads orders again binds to
  the inner orders on both engines; MariaDB 10.11 refuses LIMIT inside IN (1235), refuses an unnamed FROM subquery with a plain
  syntax error (1064, MySQL says 1248), has no LATERAL, and reports `id = (SELECT a, b ...)` as 4078 (illegal parameter data
  types) rather than 1241; PostgreSQL 16 accepts an unnamed FROM subquery.
- **Night 5 (11 Nov 2026).** Changing data in `app/agents/sqlchange.py` (fundamental `changes_safety` widened: which rows a
  change touches, how often each changes, what value it gets, which column it fills) and stored routines in
  `app/agents/sqlroutine.py` (new fundamental `routine_flow`: a routine must end, answer, and not feed itself). The checker now
  reads INSERT / REPLACE and CREATE PROCEDURE / FUNCTION / TRIGGER (and DO blocks), and every UPDATE / DELETE comes with a
  read-only preview SELECT of the rows it would change. Rules: each engine's UPDATE / DELETE with a join (rewritten both ways);
  SET alias.col on PostgreSQL; PostgreSQL's FROM reading the target again or a joined table never tied to the target (every row
  changes); one target row with several joined rows (changed once: totalled first); SET col = (SELECT ...) with no WHERE (NULL
  for no match); ON DUPLICATE KEY UPDATE / ON CONFLICT rewritten both ways, a bare column in DO UPDATE, DO UPDATE with no target;
  INSERT ... SELECT filling by position; too few values; no column list; LIMIT on UPDATE/DELETE on PostgreSQL (ctid); deleting
  duplicates through a self-join with no rule for which copy stays; DELETE FROM a JOIN b; INSERT IGNORE; REPLACE. Routines:
  WHILE / REPEAT whose condition the body never changes and LOOP with no way out; a function that can end without RETURN; a
  cursor with no NOT FOUND handler; DECLARE order; DELIMITER for the mysql client; a trigger that changes its own table; NEW
  changed in an AFTER trigger; a PostgreSQL trigger function with no RETURN or RETURN NULL; a MySQL trigger body on PostgreSQL;
  SET NEW.x in plpgsql; no LANGUAGE. 18 concepts, 59-problem practice bank (41 statements, 18 routines), 16 generator
  families. Also fixed: words after a query that start like SQL ('set the amount to 0 ...') were taken as SQL; a meaning-changing
  'check' rewrite was mixed into the fix of a definite mistake; 'repeatable read' was read as 'repeat' (confused). Lab lessons
  worth keeping: an UPDATE changes each row at most once on both engines, so a join to two payments adds one of them;
  PostgreSQL's `SET NEW.note = 'x'` in plpgsql silently sets a custom setting named new.note (with an expression it is a syntax
  error); RETURN NULL from a BEFORE row trigger skips the row with no error; MariaDB compares emails case-blind, so a self-join
  dedupe removes more rows there than on PostgreSQL; MariaDB fires 1442 / 1362 / 1329 / 1337 / 1338 / 1321 exactly as the docs
  say; DELIMITER is only the mysql client's (the CLI cuts an undelimited CREATE PROCEDURE at the first ';', 1064 near '').
- **Night 6 (11 Nov 2026).** Table designs in `app/agents/sqlschema.py` (new fundamentals `table_keys`: every table names its rows
  and every link points at a key of the same type, with an index on the child side; `column_types`: a column's type is a promise
  about its values; `one_fact_one_place`). The checker and the chat now take CREATE TABLE scripts (with the CREATE INDEX / ALTER
  TABLE after them), show "How DBaba read this design" (tables, what names a row, each link) and offer a corrected design. Rules:
  money in FLOAT / DOUBLE / REAL; dates and numbers kept as text; phone / PIN codes kept as numbers; no primary key (PRIMARY KEY on
  the id that is there, both columns of a link table, or a new id in the engine's own words); two primary keys; a foreign key to
  a table made later (reordered), to a column that is not a key, or of another type; PostgreSQL's unindexed foreign keys; utf8
  (utf8mb3) and MyISAM on MySQL/MariaDB; TIMESTAMP for birth dates and expiries (1970-2038); VARCHAR without a length; TEXT as a
  primary key; the 65,535-byte row; over-wide VARCHAR / CHAR; reserved words as names; lists in a column, numbered columns, a
  parent's details copied next to its id; and a design written for the other database, rewritten in the named one's words (rule
  A). Engine facts (marks, reserved words, foreign types and options, limits) in the dialect files' `schema_rules`. 22 concepts,
  49-problem practice bank, 21 generator families. Question shapes now also cover "what data type for X", "kaunsa data type",
  "X use kare ya Y" and general yes/no questions ("is mysql case sensitive"; never one about the person's own work). Lab lessons
  worth keeping: MariaDB 10.11 enforces an inline column REFERENCES (MySQL before 9.0 ignores it, per its docs); MyISAM accepts a
  FOREIGN KEY with no warning and never checks it; INT -> BIGINT, INT -> INT UNSIGNED and INT -> MariaDB SERIAL foreign keys are
  errno 150 on MariaDB but INT -> BIGINT is fine on PostgreSQL; MariaDB accepts a foreign key to a non-unique indexed column
  (PostgreSQL 42830); MariaDB 10.11 TIMESTAMP refused both 1965 and 2040; MariaDB's BOOLEAN stored 5; ADD COLUMN ... NOT NULL
  with no DEFAULT fills '' on MariaDB and is refused by PostgreSQL; MariaDB accepts TEXT in UNIQUE (hidden hash) but not as a
  PRIMARY KEY (1170); `key`, `range`, `rows`, `index`, `values` are reserved on MariaDB only, `user`, `end`, `window`, `do` on
  PostgreSQL only.
