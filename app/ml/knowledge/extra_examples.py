"""Compatibility alias for app.engine.ml.knowledge.extra_examples."""
import sys
from app.engine.ml.knowledge import extra_examples as _module

sys.modules[__name__] = _module
