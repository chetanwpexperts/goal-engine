"""S5's lab: made-up tables on the sandbox MariaDB and PostgreSQL with planted problems, so every report check is proved
on what the engines really say. Used by tests/test_local_report.py and runnable on its own:

    python -m tests.s5lab build      # create s5_* tables, rows and the planted problems
    python -m tests.s5lab drop

Planted (each is a check in app/local/checks.py):
  s5_parcels.ix_hub        redundant: (hub) is the left part of ix_hub_status (hub, status)
  s5_parcels.ix_hub_copy   duplicate of ix_hub (MariaDB keeps both; PostgreSQL too)
  s5_hubs.ix_city          unused: no query ever reads it
  s5_parcels.hub_id        a foreign key with no index starting with it (PostgreSQL only: MariaDB/InnoDB makes one itself)
  slow queries             YEAR()/EXTRACT() around a date column, a LIKE '%...', run several times
  s5_scans                 (PostgreSQL) half its rows updated with autovacuum off: dead rows and stale statistics
"""

import random
import sys

from tests.fuzz.db import MYSQL, PG, Engine

N_PARCELS = 60_000


def _rows(rnd):
    for k in range(1, N_PARCELS + 1):
        yield (k, rnd.randint(1, 40), rnd.choice(["new", "packed", "shipped", "returned"]),
               f"2025-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d} 10:00:00", rnd.randint(1, 9000) / 10,
               f"code{rnd.randint(1, 99999)}")


def build(eng: Engine):
    rnd = random.Random(5)
    drop(eng)
    my = eng.kind == "mysql"
    eng.run("CREATE TABLE s5_hubs (id INT PRIMARY KEY, city VARCHAR(40), name VARCHAR(40))")
    eng.run("CREATE INDEX ix_city ON s5_hubs (city)")
    eng.run("INSERT INTO s5_hubs VALUES (%s, %s, %s)", many=[(k, f"city{k % 9}", f"hub{k}") for k in range(1, 41)])
    eng.run("CREATE TABLE s5_parcels (id INT PRIMARY KEY, hub_id INT, status VARCHAR(20), sent_at "
            + ("DATETIME" if my else "TIMESTAMP") + ", fee DECIMAL(10,2), code VARCHAR(20), "
            "CONSTRAINT fk_s5_hub FOREIGN KEY (hub_id) REFERENCES s5_hubs (id))")
    batch = list(_rows(rnd))
    for i in range(0, len(batch), 5000):
        eng.run("INSERT INTO s5_parcels VALUES (%s, %s, %s, %s, %s, %s)", many=batch[i:i + 5000])
    eng.run("CREATE INDEX ix_hub_status ON s5_parcels (status, sent_at)")
    eng.run("CREATE INDEX ix_hub ON s5_parcels (status)")
    eng.run("CREATE INDEX ix_hub_copy ON s5_parcels (status)")
    eng.run("CREATE TABLE s5_scans (id INT PRIMARY KEY, parcel_id INT, at_hub VARCHAR(20))")
    eng.run("INSERT INTO s5_scans VALUES (%s, %s, %s)", many=[(k, k, "x") for k in range(1, 20001)])
    if not my:
        eng.run("ALTER TABLE s5_scans SET (autovacuum_enabled = false)")
        eng.run("ANALYZE s5_scans")
        eng.run("UPDATE s5_scans SET at_hub = 'y' WHERE id % 2 = 0")
        eng.run("ANALYZE s5_parcels")
        eng.run("ANALYZE s5_hubs")
        import time

        for _ in range(60):                       # PostgreSQL publishes row counts up to a second after the change
            if (eng.run("SELECT n_dead_tup FROM pg_stat_user_tables WHERE relname = 's5_scans'") or [[0]])[0][0] >= 10000:
                break
            time.sleep(0.25)
    else:
        eng.run("ANALYZE TABLE s5_parcels, s5_hubs, s5_scans")
    workload(eng)


def workload(eng: Engine, times: int = 4):
    my = eng.kind == "mysql"
    year = "YEAR(sent_at) = 2025" if my else "EXTRACT(YEAR FROM sent_at) = 2025"
    for _ in range(times):
        eng.run(f"SELECT * FROM s5_parcels WHERE {year} AND fee > 800")
        eng.run("SELECT id, code FROM s5_parcels WHERE code LIKE '%77'")
        eng.run("SELECT h.name, COUNT(*) FROM s5_parcels p JOIN s5_hubs h ON h.id = p.hub_id GROUP BY h.name")


def drop(eng: Engine):
    for t in ("s5_scans", "s5_parcels", "s5_hubs"):
        eng.run(f"DROP TABLE IF EXISTS {t}" + (" CASCADE" if eng.kind == "postgres" else ""))


if __name__ == "__main__":
    for cfg in (MYSQL, PG):
        e = Engine(cfg).open()
        (build if sys.argv[1:] == ["build"] else drop)(e)
        print(cfg["type"], "done")
