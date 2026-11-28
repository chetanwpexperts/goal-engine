"""Compatibility alias for app.engine.ml.howto."""
import sys
from app.engine.ml import howto as _module

sys.modules[__name__] = _module
