# Database reasoning: the 5 layers (from Chetan, 27 Oct 2026)

Source material for the database reasoning core. Learn the ideas; write our own fundamentals; engine-neutral core + one dialect file per database (MySQL, MariaDB, PostgreSQL now; MongoDB etc. later).
For each cause: the fix (reacting) AND the prevention at scale (upsert, lock order, pooling, buffer sizes, zero-downtime migrations, monitoring, backups/PITR).


Category 1: Syntax & Compilation Errors (The Parser Layer)

These errors happen at the very beginning. The database engine's SQL parser looks at your query, realizes it doesn't understand the command, and refuses to even attempt running it.
• What it means: The code violates the grammatical rules of the SQL language dialect.
• Common Examples:
	• MySQL/MariaDB 1064 (42000) / PostgreSQL 42601: Syntax error. Usually a missing comma, unclosed parenthesis, or an unquoted string.
	• Column/Table Not Found (MySQL 1146 / PostgreSQL 42P01): A typo in the table or column name, or querying a table that hasn't been created yet.
	• Ambiguous Column Name: Joining two tables that share a column name (like id) and selecting it without specifying the table name (e.g., writing SELECT id instead of SELECT users.id).
• Study Focus: Learn strict ANSI SQL standards versus engine-specific reserved words.

Category 2: Data Integrity & Constraint Violations (The Schema Layer)

The syntax is perfect, but the query is trying to write data that violates the logical rules and guardrails defined in your database schema.
• What it means: The query breaks business rules enforced by the database (like uniqueness or relationships).
• Common Examples:
	• Duplicate Key / Unique Violation (PostgreSQL 23505): Trying to insert a record with a value that already exists in a column marked as UNIQUE (like registering an existing email address).
	• Foreign Key Constraint Violation (MySQL 1216 / PostgreSQL 23503): Trying to add an order for a customer_id that does not exist in the parent customers table, or trying to delete a customer who still has active orders.
	• Not Null Violation: Attempting to leave a column completely empty (NULL) when the schema explicitly requires a value.
• Study Focus: Master ON DELETE CASCADE, ON CONFLICT / UPSERT logic, and schema design.

Category 3: Resource & Infrastructure Errors (The Hardware Layer)

The query is fine, the schema rules are respected, but the physical server or virtual instance runs out of physical capacity to complete the job.
• What it means: The database is choking on hardware limitations (RAM, CPU, Storage, or network packets).
• Common Examples:
	• Out of Memory / OOM Killer: The operating system forces the database process to shut down because it completely exhausted the server's RAM during a massive, unindexed table scan.
	• Disk Full Errors (MySQL 1021): The server has literally run out of storage space to write data or temporary sort files.
	• Packet Too Large (MySQL 2006): The application sent a single query or binary object (like a massive image file) that exceeds the server's maximum allowed buffer memory size.
• Study Focus: Performance tuning, setting up configuration limits (e.g., shared_buffers, innodb_buffer_pool_size), and index optimization.

Category 4: Operational & Concurrency Errors (The Transaction Layer)

These errors happen in multi-user environments when multiple application threads try to modify or access the exact same data rows at the exact same fraction of a second.
• What it means: The database engine blocks a query to preserve data consistency and prevent corruption.
• Common Examples:
	• Deadlock Detected (MySQL 1213 / PostgreSQL 40P01): User A locks Row 1 and wants Row 2. User B locks Row 2 and wants Row 1. They block each other forever, forcing the engine to step in and kill one of the transactions.
	• Lock Wait Timeout Exceeded: A query waits too long in line for another long-running background process to finish updating a specific table or row.
	• Serialization Failure: Two concurrent transactions try to modify the same data under strict isolation settings, creating an impossible logical conflict.
• Study Focus: ACID properties, transaction isolation levels (Read Committed vs. Serializable), and row-level locking strategies.

Category 5: Connectivity & Security Errors (The Gateway Layer)

These errors happen outside the database engine itself, blocking your application from establishing a working pipeline to the system.
• What it means: The client application cannot talk to the database due to wrong passwords, incorrect network setups, or system capacity caps.
• Common Examples:
	• Access Denied (MySQL 1045): Wrong username or password, or trying to connect remotely when the user profile is locked to local access only.
	• Too Many Connections (MySQL 1040): The database engine has hit its absolute maximum ceiling of simultaneous connected clients and rejects any new visitors.
	• Connection Refused / Timeout: The database server is completely offline, running on a different port, or blocked by a cloud firewall configuration.
• Study Focus: Database connection pooling, firewall rules, user privilege management (GRANT/REVOKE), and network topologies.

## Prevention view (from the same material)
- Syntax: lint SQL before it reaches production.
- Integrity: migration workflows, normalization, zero-downtime schema changes, ON CONFLICT / upsert, ON DELETE rules.
- Resources: buffer sizes, index strategy, connection poolers (PgBouncer, ProxySQL), scaling.
- Concurrency: isolation levels, predictable lock order, short transactions, retry on deadlock/serialization failure.
- Connectivity: pooling, firewall rules, least-privilege GRANT/REVOKE, high availability.
- Cross-cutting: observability (slow query log, EXPLAIN ANALYZE, I/O), backup & point-in-time recovery, automation.
