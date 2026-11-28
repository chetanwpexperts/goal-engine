"""The engine layer: understanding (ml), learning rules and the goal engine.

The goal engine's names stay importable as `from app.engine import handle_message, ...`, but app/engine/goal.py is
loaded only when one of them is first used. Loading it eagerly here would make every `import app.engine.ml...`
pull in the whole goal engine (and through it the agents), which imports app.engine.ml again: a circular import.
"""

_GOAL_NAMES = ("InvalidMessage", "handle_message", "all_goals", "active_goals", "focus_goal", "recent_messages",
               "_after_run", "agent_mode")

__all__ = list(_GOAL_NAMES)


def __getattr__(name: str):
    if name in _GOAL_NAMES:
        from app.engine import goal

        return getattr(goal, name)
    raise AttributeError(f"module 'app.engine' has no attribute {name!r}")
