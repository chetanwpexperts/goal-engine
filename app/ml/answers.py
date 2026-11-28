"""Compatibility alias for app.engine.ml.answers."""
import sys
from app.engine.ml import answers as _module

sys.modules[__name__] = _module
