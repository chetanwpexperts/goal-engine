"""Compatibility alias for the shared product implementation."""
import sys
from app.products.shared import planner as _module

sys.modules[__name__] = _module
