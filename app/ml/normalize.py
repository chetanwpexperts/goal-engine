"""Compatibility alias for app.engine.ml.normalize."""
import sys
from app.engine.ml import normalize as _module

sys.modules[__name__] = _module
