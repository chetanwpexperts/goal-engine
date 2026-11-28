"""Lazy compatibility exports for the former goal engine entry points.

Pure rules live in this package; legacy session-taking entry points resolve to
the service only when requested. Internal callers use their owning layer directly.
"""
from importlib import import_module

_GOAL_NAMES = ("InvalidMessage", "handle_message", "all_goals", "active_goals", "focus_goal", "recent_messages",
               "_after_run", "agent_mode")
__all__ = list(_GOAL_NAMES)


def __getattr__(name: str):
    if name in ("InvalidMessage", "agent_mode"):
        return getattr(import_module("app.engine.goal"), name)
    if name == "_after_run":
        return getattr(import_module("app.agents.replies"), name)
    if name in _GOAL_NAMES:
        return getattr(import_module("app.services.goals"), name)
    raise AttributeError(f"module 'app.engine' has no attribute {name!r}")
