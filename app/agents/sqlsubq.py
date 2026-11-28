"""Compatibility alias for relocated DBaba code."""
import sys
from importlib import import_module

_module = import_module("app.products.dbaba.sqlsubq")
sys.modules[__name__] = _module
