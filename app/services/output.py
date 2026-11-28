"""Filter internal details from public responses."""

from app.config import get_settings


GOAL_INTERNAL = ("intent", "confidence", "interpreted_problem", "desired_goal", "sub_goals", "constraints", "next_action",
                 "original_request", "updates")


def public(data: dict) -> dict:
    """What the browser gets: the replies, cards and goal titles, never how the brain read the message."""
    if get_settings().expose_internals or not isinstance(data, dict):
        return data
    out = {k: v for k, v in data.items() if k not in ("provider", "intent", "confidence")}
    for key in ("goal", "current"):
        if isinstance(out.get(key), dict):
            out[key] = {k: v for k, v in out[key].items() if k not in GOAL_INTERNAL}
    if isinstance(out.get("agents"), list):
        out["agents"] = [{**a, "brain": {k: v for k, v in a["brain"].items() if k not in ("confidence", "ranking", "scores", "score")}}
                         if isinstance(a, dict) and isinstance(a.get("brain"), dict) else a for a in out["agents"]]
    return out
