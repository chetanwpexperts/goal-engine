"""Compatibility alias for app.engine.ml.knowledge.tech."""
import sys
from app.engine.ml.knowledge import tech as _module

sys.modules[__name__] = _module
