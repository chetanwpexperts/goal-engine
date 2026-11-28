"""Compatibility alias for app.engine.ml.knowledge.business."""
import sys
from app.engine.ml.knowledge import business as _module

sys.modules[__name__] = _module
