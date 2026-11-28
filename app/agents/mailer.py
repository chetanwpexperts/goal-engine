"""Compatibility alias for the shared product implementation."""
import sys
from app.products.shared import mailer as _module

sys.modules[__name__] = _module
