"""Compatibility alias for conversation reasoning."""
import sys
from app.engine.mind import core as _module

sys.modules[__name__] = _module
