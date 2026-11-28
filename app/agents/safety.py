"""Compatibility alias for the shared product implementation."""
import sys
from app.products.shared import safety as _module

sys.modules[__name__] = _module
