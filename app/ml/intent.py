"""Compatibility alias for app.engine.ml.intent."""
import sys
from app.engine.ml import intent as _module

sys.modules[__name__] = _module
