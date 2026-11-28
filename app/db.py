"""Compatibility alias; register domain tables for legacy standalone callers."""
import sys
from app.core import db as _db
# The legacy entry point composes core infrastructure and domain registration.
# Core itself never imports domain. Keep this bridge until legacy callers move.
from .domain import models as _models  # noqa: F401

sys.modules[__name__] = _db
