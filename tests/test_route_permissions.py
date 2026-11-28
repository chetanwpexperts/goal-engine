"""Prevent new undeclared routes and require removal of resolved route debts."""

from tests.arch_scan import load_baseline, scan_routes


def test_no_new_undeclared_routes():
    new = scan_routes() - set(load_baseline()["undeclared_routes"])
    assert not new, (
        "every new route must declare a permission with require(...) from app/security, "
        "see docs/architecture.md:\n" + "\n".join(sorted(new))
    )


def test_undeclared_routes_only_shrink():
    fixed = set(load_baseline()["undeclared_routes"]) - scan_routes()
    assert not fixed, "fixed: remove these from tests/architecture_baseline.json:\n" + "\n".join(sorted(fixed))
