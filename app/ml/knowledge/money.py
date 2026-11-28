"""Compatibility alias for app.engine.ml.knowledge.money."""
import sys
from app.engine.ml.knowledge import money as _module

sys.modules[__name__] = _module
