"""Compatibility alias for relocated reasoning module."""
import sys
from app.engine.mind import packs as _module

sys.modules[__name__] = _module
