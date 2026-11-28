"""Compatibility alias for shared text normalization."""
import sys
from app.core import text as _module

sys.modules[__name__] = _module
