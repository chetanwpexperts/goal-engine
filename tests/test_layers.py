"""Guard the layer boundaries and keep existing architecture debts shrinking."""

from tests.arch_scan import load_baseline, scan_db, scan_imports


def test_no_new_upward_imports():
    new = scan_imports() - set(load_baseline()["import_debts"])
    assert not new, "New forbidden imports; see docs/architecture.md:\n" + "\n".join(sorted(new))


def test_import_debts_only_shrink():
    fixed = set(load_baseline()["import_debts"]) - scan_imports()
    assert not fixed, "fixed: remove these from tests/architecture_baseline.json:\n" + "\n".join(sorted(fixed))


def test_no_new_db_session_debts():
    new = scan_db() - set(load_baseline()["db_session_debts"])
    assert not new, "New DB-session violations; see docs/architecture.md:\n" + "\n".join(sorted(new))


def test_db_session_debts_only_shrink():
    fixed = set(load_baseline()["db_session_debts"]) - scan_db()
    assert not fixed, "fixed: remove these from tests/architecture_baseline.json:\n" + "\n".join(sorted(fixed))


def test_baseline_is_sorted_and_unique():
    for name, entries in load_baseline().items():
        if isinstance(entries, list):
            assert entries == sorted(set(entries)), f"{name} must be sorted and unique"
