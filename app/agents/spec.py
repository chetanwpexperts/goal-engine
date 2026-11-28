"""Compatibility alias for the shared product implementation."""
import sys
from app.products.shared import spec as _module

sys.modules[__name__] = _module
