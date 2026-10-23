"""A fixed slice of the test generator (tests/fuzz/sheets.py) in every test run: random messy files from many fields
and random questions, answers checked against the data. The nightly run uses new seeds every night."""

from tests.fuzz.sheets import run


def test_random_files_and_questions():
    rep = run(range(1, 31))
    assert rep["rate"] >= 0.98, "\n".join(f"{f['field']}/{f['seed']} {f['family']}: {f['q']!r} -> {f['why']}" for f in rep["fails"][:20])
