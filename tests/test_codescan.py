"""A fixed slice of night 10A's project generator (tests/fuzz/codescan.py) in every test run: realistic project folders with
wrong queries planted in code strings must be found at their file and line, ordinary strings must stay quiet, ORM calls and
queries built in pieces must be reported as unread, and every fix must return the intended rows on MariaDB and PostgreSQL.
The nightly run uses new seeds."""

from tests.fuzz import codescan
from tests.practice import schema as S


def test_generated_projects():
    engines = S.engines()
    engines = engines if len(engines) == 2 else {}
    made, fails, seen, _e = codescan.run(6, 4242, engines=engines)
    assert not fails, "\n\n".join(fails[:10])
    assert seen["bug"] and seen["clean"] and seen["unread"], seen
