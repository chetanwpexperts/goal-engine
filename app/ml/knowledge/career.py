"""Compatibility alias for app.engine.ml.knowledge.career."""
import sys
from app.engine.ml.knowledge import career as _module

sys.modules[__name__] = _module
