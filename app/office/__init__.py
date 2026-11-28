"""Compatibility package for relocated product code."""
import sys
from importlib import import_module

_module = import_module("app.products.sheetu.office")
# Keep the old package path for its explicit submodule aliases.
_module.__path__ = list(__path__) + list(_module.__path__)
sys.modules[__name__] = _module
