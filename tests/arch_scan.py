"""Reads app/ and reports what the architecture rules forbid (docs/architecture.md). Used by tests/test_layers.py and
tests/test_route_permissions.py, which compare the result with tests/architecture_baseline.json.

Three scans:
  scan_imports()  "module -> top-level package" for every import that goes UP a layer, from the engine into data/security,
                  or between the dbaba and sheetu products.
  scan_db()       "app/file.py:function" for every function outside app/db.py that runs a query on a DB session
                  (db/session/sess/s .query/.execute/.scalar/.scalars/.get) or opens a Session itself.
  scan_routes()   "app/file.py METHOD /path" for every route in app/main.py and app/local/server.py.

The layer of each of today's top-level packages is in LAYER_OF; when code moves to app/core, app/domain, app/data, ...
those folder names are layers themselves (see LAYERS).

    python -m tests.arch_scan      # prints the current baseline as JSON (to refresh it after a step removes debts)
"""

import ast
import json
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
BASELINE = pathlib.Path(__file__).resolve().parent / "architecture_baseline.json"

LAYERS = {"core": 1, "domain": 2, "data": 3, "security": 4, "engine": 5, "products": 6, "services": 7, "api": 8, "main": 9}

# today's top-level packages and modules -> their layer (new folders named like a layer map to themselves)
LAYER_OF = {"config": "core", "db": "core", "models": "domain", "mind": "engine", "ml": "engine", "learning": "engine",
            "engine": "engine", "ai": "engine", "agents": "products", "dbknow": "products", "officeknow": "products",
            "office": "products", "local": "products", "accounts": "services", "admin": "services", "enquiry": "services",
            "expert": "services", "privacy": "services", "usage": "services", "limits": "services", "captcha": "services",
            "console": "services", "dbarea": "services", "main": "main",
            **{k: k for k in LAYERS}}

DB_ATTRS = {"query", "execute", "scalar", "scalars", "get"}
SESSION_NAMES = {"db", "session", "sess", "s"}
SESSION_FACTORIES = {"Session", "SessionLocal", "_SessionLocal"}
ROUTE_FILES = ("main.py", "local/server.py")


def _modname(f: pathlib.Path) -> str:
    return ".".join(f.relative_to(APP).with_suffix("").parts).removesuffix(".__init__")


def product_of(mod: str) -> str | None:
    """'dbaba' | 'sheetu' | 'shared' for product code, else None. Works for today's paths and for app/products/<name>/."""
    p = mod.split(".")
    if p[0] == "products" and len(p) > 1:
        return p[1]
    if p[0] in ("office", "officeknow") or mod.startswith("agents.office_agent"):
        return "sheetu"
    if p[0] in ("dbknow", "local") or (p[0] == "agents" and len(p) > 1 and (p[1].startswith("sql") or p[1] == "database")):
        return "dbaba"
    if p[0] == "agents":
        return "shared"
    return None


def _files():
    for f in sorted(APP.rglob("*.py")):
        if "__pycache__" not in f.parts and _modname(f).split(".")[0] in LAYER_OF:
            yield f


def _targets(f: pathlib.Path, tree: ast.AST) -> list[str]:
    out = []
    pkg = _modname(f).split(".")
    if f.name != "__init__.py":
        pkg = pkg[:-1]                                   # a module's package; a package's __init__ is its own package
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.level > 0:   # relative imports count too: "from .domain import x" in app/db.py
            base = pkg[:len(pkg) - (n.level - 1)] if n.level - 1 <= len(pkg) else []
            mod = ".".join(base + ([n.module] if n.module else []))
            if mod:
                out.append(mod)
                out += [f"{mod}.{a.name}" for a in n.names if (APP / mod.replace(".", "/") / f"{a.name}.py").exists()]
            else:
                out += [a.name for a in n.names]
            continue
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module and (n.module == "app" or n.module.startswith("app.")):
            if n.module == "app":
                out += [a.name for a in n.names]
            else:
                base = n.module[4:]
                out.append(base)
                out += [f"{base}.{a.name}" for a in n.names if (APP / base.replace(".", "/") / f"{a.name}.py").exists()]
        elif isinstance(n, ast.Import):
            out += [a.name[4:] for a in n.names if a.name.startswith("app.")]
    return out


def scan_imports() -> set[str]:
    bad = set()
    for f in _files():
        me = _modname(f)
        top = me.split(".")[0]
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for t in _targets(f, tree):
            tt = t.split(".")[0]
            if tt not in LAYER_OF:
                continue
            pm, pt = product_of(me), product_of(t)
            if pm and pt and {pm, pt} == {"dbaba", "sheetu"}:
                bad.add(f"{me} -> {tt}" if tt != top else f"{me} -> {t}")
                continue
            if tt == top:
                continue
            lm, lt = LAYER_OF[top], LAYER_OF[tt]
            if LAYERS[lt] > LAYERS[lm] or (lm == "engine" and lt in ("data", "security")):
                bad.add(f"{me} -> {tt}")
    return bad


def scan_db() -> set[str]:
    found = set()
    for f in _files():
        rel = f.relative_to(APP.parent).as_posix()
        if rel in ("app/db.py", "app/core/db.py") or rel.startswith("app/data/"):
            continue
        stack: list[str] = []

        class V(ast.NodeVisitor):
            def visit_FunctionDef(self, n):
                stack.append(n.name)
                self.generic_visit(n)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, n):
                fn = n.func
                hit = (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id in SESSION_NAMES
                       and fn.attr in DB_ATTRS) or (isinstance(fn, ast.Name) and fn.id in SESSION_FACTORIES)
                if hit:
                    found.add(f"{rel}:{'.'.join(stack) or '<module>'}")
                self.generic_visit(n)

        V().visit(ast.parse(f.read_text(encoding="utf-8")))
    return found


def scan_routes() -> set[str]:
    out = set()
    for name in ROUTE_FILES:
        f = APP / name
        if not f.exists():
            continue
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in n.decorator_list:
                    if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                            and d.func.attr in ("get", "post", "put", "delete", "patch")
                            and isinstance(d.func.value, ast.Name) and d.args and isinstance(d.args[0], ast.Constant)):
                        out.add(f"app/{name} {d.func.attr.upper()} {d.args[0].value}")
    return out


def load_baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    print(json.dumps({"import_debts": sorted(scan_imports()), "db_session_debts": sorted(scan_db()),
                      "undeclared_routes": sorted(scan_routes())}, indent=2))
