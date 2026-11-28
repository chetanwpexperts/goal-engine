"""Compatibility alias for app.engine.ml.slots."""
import sys
from app.engine.ml import slots as _module

sys.modules[__name__] = _module
