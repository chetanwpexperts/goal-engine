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


def _undeclared(app) -> list[str]:
    """Every route the app really serves, checked at runtime: it must carry a require(...) dependency."""
    from fastapi.routing import APIRoute

    out = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        calls = [d.call for d in r.dependant.dependencies]
        if not any(getattr(c, "__qualname__", "").startswith("require.") for c in calls):
            out.append(f"{','.join(sorted(r.methods))} {r.path}")
    return sorted(out)


def test_every_site_route_declares_a_permission():
    from app.main import app

    assert _undeclared(app) == []


def test_every_local_route_declares_a_permission(tmp_path):
    from app.products.dbaba.local.server import make_app

    assert _undeclared(make_app(str(tmp_path / "dbaba.json"), 8765, "TOKEN-1", announce=lambda line: None)) == []
