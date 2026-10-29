"""Generator for the database reasoning: real-format error messages for every layer and every database, wrapped the
way people really paste them (PHP/Laravel PDO, Django, phpMyAdmin, the command line, logs), with their own words in
English/Hinglish (typos included) around them; plus plain descriptions without any error, where the agent must not
claim a cause but list the right likely ones.
Run: python -m tests.fuzz.db_errors [count] [seed]
"""

import random
import sys

TABLES = ["orders", "users", "customers", "invoices", "students", "products", "payments", "wp_posts", "fee_receipts", "bookings"]
COLS = ["email", "phone", "customer_id", "user_id", "invoice_no", "roll_no", "status", "amount", "name", "slug"]
VALUES = ["a@b.com", "9876543210", "INV-1001", "42", "ravi@example.in", "SKU-77", "2026-10-31", "7"]
HOSTS = ["10.0.0.12", "203.0.113.9", "db.example.com", "localhost"]

MY = {  # MySQL / MariaDB: (code, sqlstate, message)
    "duplicate_key": lambda r: ("1062", "23000", f"Duplicate entry '{r['v']}' for key '{r['t']}.{r['t']}_{r['c']}_unique'"),
    "fk_missing_parent": lambda r: ("1452", "23000", f"Cannot add or update a child row: a foreign key constraint fails (`app`.`{r['t']}`, CONSTRAINT `{r['t']}_{r['c']}_foreign` FOREIGN KEY (`{r['c']}`) REFERENCES `customers` (`id`))"),
    "fk_has_children": lambda r: ("1451", "23000", f"Cannot delete or update a parent row: a foreign key constraint fails (`app`.`{r['t']}`, CONSTRAINT `{r['t']}_{r['c']}_foreign` FOREIGN KEY (`{r['c']}`) REFERENCES `customers` (`id`))"),
    "not_null": lambda r: ("1048", "23000", f"Column '{r['c']}' cannot be null"),
    "value_too_long": lambda r: ("1406", "22001", f"Data too long for column '{r['c']}' at row 1"),
    "out_of_range": lambda r: ("1264", "22003", f"Out of range value for column '{r['c']}' at row 1"),
    "bad_value_type": lambda r: ("1366", "HY000", f"Incorrect integer value: '' for column '{r['c']}' at row 1"),
    "charset_mismatch": lambda r: ("1366", "HY000", f"Incorrect string value: '\\xF0\\x9F\\x98\\x80' for column '{r['c']}' at row 1"),
    "syntax_error": lambda r: ("1064", "42000", f"You have an error in your SQL syntax; check the manual that corresponds to your MySQL server version for the right syntax to use near 'FROM {r['t']} WHERE' at line 1"),
    "reserved_word": lambda r: ("1064", "42000", "You have an error in your SQL syntax; check the manual that corresponds to your MySQL server version for the right syntax to use near 'order, total) VALUES (1, 2)' at line 1"),
    "unknown_table": lambda r: ("1146", "42S02", f"Table 'app.{r['t']}x' doesn't exist"),
    "unknown_column": lambda r: ("1054", "42S22", f"Unknown column '{r['c']}x' in 'field list'"),
    "ambiguous_column": lambda r: ("1052", "23000", "Column 'id' in field list is ambiguous"),
    "group_by_strict": lambda r: ("1055", "42000", f"Expression #2 of SELECT list is not in GROUP BY clause and contains nonaggregated column 'app.{r['t']}.{r['c']}' which is not functionally dependent on columns in GROUP BY clause; this is incompatible with sql_mode=only_full_group_by"),
    "disk_full": lambda r: ("1021", "HY000", "Disk full (/tmp/#sql_1f2_0.MAI); waiting for someone to free some space... (errno: 28 \"No space left on device\")"),
    "packet_too_large": lambda r: ("1153", "08S01", "Got a packet bigger than 'max_allowed_packet' bytes"),
    "deadlock": lambda r: ("1213", "40001", "Deadlock found when trying to get lock; try restarting transaction"),
    "lock_wait": lambda r: ("1205", "HY000", "Lock wait timeout exceeded; try restarting transaction"),
    "access_denied": lambda r: ("1045", "28000", f"Access denied for user 'app'@'{r['h']}' (using password: YES)"),
    "host_not_allowed": lambda r: ("1130", "HY000", f"Host '{r['h']}' is not allowed to connect to this MySQL server"),
    "too_many_connections": lambda r: ("1040", "08004", "Too many connections"),
    "server_unreachable": lambda r: ("2002", "HY000", "Can't connect to local MySQL server through socket '/var/run/mysqld/mysqld.sock' (2)"),
    "connection_lost": lambda r: ("2006", "HY000", "MySQL server has gone away"),
    "unknown_database": lambda r: ("1049", "42000", "Unknown database 'appx'"),
    "permission_denied": lambda r: ("1142", "42000", f"INSERT command denied to user 'app'@'localhost' for table '{r['t']}'"),
    "statement_timeout": lambda r: ("3024", "HY000", "Query execution was interrupted, maximum statement execution time exceeded"),
}
PG = {  # PostgreSQL: (sqlstate, message, detail)
    "duplicate_key": lambda r: ("23505", f"duplicate key value violates unique constraint \"{r['t']}_{r['c']}_key\"", f"Key ({r['c']})=({r['v']}) already exists."),
    "fk_missing_parent": lambda r: ("23503", f"insert or update on table \"{r['t']}\" violates foreign key constraint \"{r['t']}_{r['c']}_fkey\"", f"Key ({r['c']})=({r['v']}) is not present in table \"customers\"."),
    "fk_has_children": lambda r: ("23503", f"update or delete on table \"customers\" violates foreign key constraint \"{r['t']}_{r['c']}_fkey\" on table \"{r['t']}\"", f"Key (id)=({r['v']}) is still referenced from table \"{r['t']}\"."),
    "not_null": lambda r: ("23502", f"null value in column \"{r['c']}\" of relation \"{r['t']}\" violates not-null constraint", ""),
    "value_too_long": lambda r: ("22001", "value too long for type character varying(50)", ""),
    "out_of_range": lambda r: ("22003", "integer out of range", ""),
    "bad_value_type": lambda r: ("22P02", f"invalid input syntax for type integer: \"{r['v']}x\"", ""),
    "check_violation": lambda r: ("23514", f"new row for relation \"{r['t']}\" violates check constraint \"{r['t']}_{r['c']}_check\"", ""),
    "syntax_error": lambda r: ("42601", "syntax error at or near \"FROM\"", ""),
    "reserved_word": lambda r: ("42601", "syntax error at or near \"order\"", ""),
    "unknown_table": lambda r: ("42P01", f"relation \"{r['t']}x\" does not exist", ""),
    "unknown_column": lambda r: ("42703", f"column \"{r['c']}x\" does not exist", ""),
    "ambiguous_column": lambda r: ("42702", "column reference \"id\" is ambiguous", ""),
    "unknown_function": lambda r: ("42883", "function group_concat(text) does not exist", "No function matches the given name and argument types."),
    "group_by_strict": lambda r: ("42803", f"column \"{r['t']}.{r['c']}\" must appear in the GROUP BY clause or be used in an aggregate function", ""),
    "disk_full": lambda r: ("53100", "could not extend file \"base/16384/2619\": No space left on device", ""),
    "out_of_memory": lambda r: ("53200", "out of memory", "Failed on request of size 1048576 in memory context \"ExecutorState\"."),
    "statement_timeout": lambda r: ("57014", "canceling statement due to statement timeout", ""),
    "deadlock": lambda r: ("40P01", "deadlock detected", "Process 1234 waits for ShareLock on transaction 5678; blocked by process 4321."),
    "lock_wait": lambda r: ("55P03", "canceling statement due to lock timeout", ""),
    "serialization_failure": lambda r: ("40001", "could not serialize access due to concurrent update", ""),
    "access_denied": lambda r: ("28P01", "password authentication failed for user \"app\"", ""),
    "host_not_allowed": lambda r: ("28000", f"no pg_hba.conf entry for host \"{r['h']}\", user \"app\", database \"app\", no encryption", ""),
    "too_many_connections": lambda r: ("53300", "sorry, too many clients already", ""),
    "server_unreachable": lambda r: ("08001", f"connection to server at \"{r['h']}\" (10.0.0.5), port 5432 failed: Connection refused\n\tIs the server running on that host and accepting TCP/IP connections?", ""),
    "connection_lost": lambda r: ("08006", "server closed the connection unexpectedly\n\tThis probably means the server terminated abnormally", ""),
    "unknown_database": lambda r: ("3D000", "database \"appx\" does not exist", ""),
    "permission_denied": lambda r: ("42501", f"permission denied for table {r['t']}", ""),
}
MY_WRAP = [
    "ERROR {code} ({state}): {msg}",
    "SQLSTATE[{state}]: Integrity constraint violation: {code} {msg}",
    "SQLSTATE[{state}]: General error: {code} {msg} (SQL: insert into `{t}` (`{c}`) values (?))",
    "#{code} - {msg}",
    "PDOException: SQLSTATE[{state}] [{code}] {msg} in /var/www/html/app/db.php:42",
    "django.db.utils.IntegrityError: ({code}, \"{msg}\")",
    "Error Code: {code}. {msg}",
    "mysqli_sql_exception: {msg}",
]
PG_WRAP = [
    "ERROR:  {msg}\nDETAIL:  {detail}",
    "psycopg2.errors.Error: {msg}\nDETAIL:  {detail}",
    "SQLSTATE[{state}]: {msg}\nDETAIL:  {detail}",
    "org.postgresql.util.PSQLException: ERROR: {msg}\n  Detail: {detail}",
    "error: {msg} (code: '{state}')",
    "Illuminate\\Database\\QueryException: SQLSTATE[{state}]: {msg} (Connection: pgsql, SQL: select * from \"{t}\")",
]
SAY = ["", "", "ye error aa raha hai", "getting this error on my website", "order save karte waqt ye aata hai", "help, what does this mean?",
       "pls fix", "kya karu iska", "our app shows this since morning", "client ki site pe ye aa gaya", "after deploy we get"]
ENGINE_SAY = {"mysql": ["", "", "mysql", "MySQL 8"], "mariadb": ["MariaDB", "mariadb 10.11"], "postgresql": ["", "postgres", "PostgreSQL 16"]}
PLAIN = {  # descriptions without any error text -> the likely causes must include it (never claimed as found)
    "too_many_connections": ["site pe bahut log aate hi database connect nahi hota, connection limit wali problem lagti hai",
                             "too many users and the app cant connect to db"],
    "deadlock": ["kabhi kabhi order save nahi hota aur lock wali problem aati hai", "random save failures, something about locks waiting"],
    "duplicate_key": ["form submit karne pe duplicate wala error aata hai", "it says the email already exists when saving"],
    "full_scan": ["database bahut slow ho gaya hai pages load nahi hote", "queries are very slow since the data grew"],
    "access_denied": ["app database se connect nahi ho rahi, password change kiya tha", "login to mysql fails after password change"],
}


def typo(w, rng):
    if len(w) < 6 or not w.isalpha() or rng.random() > 0.12:
        return w
    i = rng.randrange(1, len(w) - 1)
    return w[:i] + w[i + 1:]


def case(i, seed):
    rng = random.Random(seed * 7919 + i)
    r = {"t": rng.choice(TABLES), "c": rng.choice(COLS), "v": rng.choice(VALUES), "h": rng.choice(HOSTS)}
    if rng.random() < 0.12:
        cause = rng.choice(list(PLAIN))
        return rng.choice(PLAIN[cause]), {"likely": cause}
    engine = rng.choice(["mysql", "mysql", "mariadb", "postgresql", "postgresql"])
    if engine == "postgresql":
        cause = rng.choice(list(PG))
        state, msg, detail = PG[cause](r)
        body = rng.choice(PG_WRAP).format(state=state, msg=msg, detail=detail or "", t=r["t"], c=r["c"])
    else:
        cause = rng.choice(list(MY))
        code, state, msg = MY[cause](r)
        if engine == "mariadb":
            msg = msg.replace("your MySQL server version", "your MariaDB server version")
        body = rng.choice(MY_WRAP).format(code=code, state=state, msg=msg, t=r["t"], c=r["c"])
    said = " ".join(typo(w, rng) for w in rng.choice(SAY).split())
    eng_word = rng.choice(ENGINE_SAY[engine])
    if engine == "mariadb" and "MariaDB" not in body and not eng_word:
        eng_word = "MariaDB"
    text = "\n".join(x for x in (f"{said} {eng_word}".strip(), body) if x) if rng.random() < 0.5 else \
        "\n".join(x for x in (body, f"{eng_word} {said}".strip()) if x)
    family = "postgresql" if engine == "postgresql" else ("mariadb" if engine == "mariadb" else "mysql")
    return text, {"cause": cause, "engine": family, "r": r}


def check(text, want):
    from app.dbknow import reasoning as R

    res = R.diagnose(text)
    found = [x["cause"] for x in sorted(res["found"], key=lambda x: -x["score"])]
    if "likely" in want:
        if found:
            return f"{text!r}: claimed {found} without any error text"
        open_ = [x["cause"] for x in res["open"][:4]]
        return None if want["likely"] in open_ else f"{text!r}: likely {open_}, missing {want['likely']}"
    if not found or found[0] != want["cause"]:
        return f"{text[:160]!r}: found {found}, want {want['cause']}"
    eng = res["engine"]
    ok_eng = eng == want["engine"] or (want["engine"] == "mysql" and eng in ("mysql",)) or (want["engine"] == "mariadb" and eng == "mariadb")
    if not ok_eng:
        return f"{text[:160]!r}: engine {eng}, want {want['engine']}"
    rep = R.report(res, False)
    if not rep or "Based on what you pasted" not in rep:
        return f"{text[:100]!r}: report missing or unlabelled"
    return None


def run(count=2000, seed=1):
    errs = []
    for i in range(count):
        text, want = case(i, seed)
        e = check(text, want)
        if e:
            errs.append(e)
    return count - len(errs), errs


if __name__ == "__main__":
    c = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    s = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    ok, errs = run(c, s)
    print(f"{ok}/{c} right")
    for e in errs[:40]:
        print(" ", e)
    sys.exit(1 if errs else 0)
