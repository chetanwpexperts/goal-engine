"""Moved to app/products/shared/meaning.py (generic word reading, used by Sheetu and DBaba). Kept for old imports."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("app.products.shared.meaning")
