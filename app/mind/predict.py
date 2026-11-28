"""Compatibility alias for pure predict rules."""
import sys
from app.engine.mind import predict as _module

sys.modules[__name__] = _module
