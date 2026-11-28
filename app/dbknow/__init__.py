"""Compatibility alias for relocated DBaba code."""
import sys
from importlib import import_module

_module = import_module("app.products.dbaba.dbknow")
_module.__path__ = list(__path__) + list(_module.__path__)
sys.modules[__name__] = _module
