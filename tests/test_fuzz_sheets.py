"""A fixed slice of the test generator (tests/fuzz/sheets.py) in every test run: random messy files from many fields
and random questions, answers checked against the data. The nightly run uses new seeds every night."""

from tests.fuzz.sheets import run


def test_random_files_and_questions():
    rep = run(range(1, 31))
    assert rep["rate"] >= 0.98, "\n".join(f"{f['field']}/{f['seed']} {f['family']}: {f['q']!r} -> {f['why']}" for f in rep["fails"][:20])


def test_seeds_that_once_failed():
    """'paid by wise expenses' with an 'Rs.' or 'Amt (Rs)' column (the money, added up), rent books with 'Society', 'Flr', 'Paid Via'."""
    rep = run([610294, 700116, 700183, 610254, 610264, 610274, 610295, 610363, 610376])
    assert rep["rate"] == 1.0, "\n".join(f"{f['field']}/{f['seed']} {f['family']}: {f['q']!r} -> {f['why']}" for f in rep["fails"][:20])
