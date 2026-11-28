"""Compatibility alias for pure profile rules."""
import sys
from app.engine.mind import profile as _module

sys.modules[__name__] = _module
