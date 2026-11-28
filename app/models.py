"""Compatibility alias for the relocated persistent models."""
import sys
from app.domain import models as _models

sys.modules[__name__] = _models
