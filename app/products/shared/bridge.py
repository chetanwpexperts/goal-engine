"""What one product lends another, without the two importing each other (docs/architecture.md, "Products").

The owner registers a capability when its package loads (app/products/dbaba/__init__.py:
provide("dbaba.concept_match", concepts.match)); the other product calls it by name (call("dbaba.concept_match", ...)).
Code both products use as-is (like the word reading in app/products/shared/meaning.py) lives in shared instead.
A name starts with its owner ("sheetu." / "dbaba."). If it isn't registered yet when it is first called (a script or
test that never started the app or imported that product), the owner's package is loaded once, which registers it.
Without that, a caller would silently get the default and reason with less than it should.
"""

import importlib
import re
from typing import Callable

_impl: dict[str, Callable] = {}
OWNERS = {"sheetu": "app.products.sheetu", "dbaba": "app.products.dbaba"}

DB_TO_SHEET = re.compile(r"\b(query|queries|database|db|mysql|maria ?db|postgre\w*|sql|sqlite)\b.{0,40}\b(excel|xlsx|spreadsheets?)\b|"
                         r"\b(excel|xlsx|spreadsheets?)\b.{0,40}\b(database|mysql|maria ?db|postgre\w*|sqlite)\b", re.I)


def provide(name: str, fn: Callable) -> None:
    _impl[name] = fn


def _find(name: str) -> Callable | None:
    fn = _impl.get(name)
    if fn is None and name.split(".", 1)[0] in OWNERS:
        importlib.import_module(OWNERS[name.split(".", 1)[0]])          # the owner registers its names on import
        fn = _impl.get(name)
    return fn


def call(name: str, *a, default=None, **kw):
    fn = _find(name)
    return fn(*a, **kw) if fn else default
