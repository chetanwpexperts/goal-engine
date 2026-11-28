"""Compatibility alias for app.engine.ml.registry."""
import sys
from app.engine.ml import registry as _module

sys.modules[__name__] = _module
