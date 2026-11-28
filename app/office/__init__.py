"""Old path, kept for old imports: the code lives in app.products.sheetu.office.

Names are forwarded to that package, and each old submodule file here (e.g. app/office/<name>.py) forwards to its new
module. This package does not alias itself or change the new package's search path: doing that made an import by the
NEW name find the old forwarding file first and loop back into a half-loaded module.
"""

from importlib import import_module as _import_module

_new = _import_module("app.products.sheetu.office")


def __getattr__(name: str):
    return getattr(_new, name)
