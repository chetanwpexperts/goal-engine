"""Compatibility alias for the relocated local module."""
import sys
from app.products.dbaba.local import tunnel as _module

sys.modules[__name__] = _module
