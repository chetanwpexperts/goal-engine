"""Compatibility alias for the relocated local module."""
import sys
from app.products.dbaba.local import config as _module

sys.modules[__name__] = _module
