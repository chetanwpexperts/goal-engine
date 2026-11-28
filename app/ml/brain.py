"""Compatibility alias for app.engine.ml.brain."""
import sys
from app.engine.ml import brain as _module

sys.modules[__name__] = _module
