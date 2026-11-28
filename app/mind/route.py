"""Compatibility alias for relocated reasoning module."""
import sys
from app.engine.mind import route as _module

sys.modules[__name__] = _module
