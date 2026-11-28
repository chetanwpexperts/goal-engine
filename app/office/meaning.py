"""Compatibility alias for relocated product code."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("app.products.shared.meaning")
