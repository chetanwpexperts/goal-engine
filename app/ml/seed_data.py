"""Compatibility alias for app.engine.ml.seed_data."""
import sys
from app.engine.ml import seed_data as _module

sys.modules[__name__] = _module
