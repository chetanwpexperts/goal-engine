"""Compatibility alias for app.engine.ml.knowledge.life."""
import sys
from app.engine.ml.knowledge import life as _module

sys.modules[__name__] = _module
