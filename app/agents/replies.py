"""Format completed agent replies and follow-up choices."""
from app.models import loads
from app.engine.goal import WATCH_LABELS

def _sql_res(run) -> dict:
    r = run.result
    return (loads(r, {}) if isinstance(r, str) else r) or {}

def _after_run(agent, run, hi: bool) -> tuple[str, list[str]]:
    reply = run.summary or ""
    options: list[str] = []
    if agent.recipe == "website_check" and run.status == "done":
        reply += "\n\n" + ("Poori report goal card mein hai. Roz check karta rahoon, taaki kuch toote to turant pata chale?"
                            if hi else "The full report is in the goal card. Shall I keep checking it daily, so you know the moment something breaks?")
        options = [next(k for k, v in WATCH_LABELS.items() if v == hi)]
    elif agent.recipe == "sql_review" and run.status == "done" and _sql_res(run).get("correct", {}).get("findings"):
        from app.agents.core import correctness_options

        options = correctness_options(_sql_res(run), hi)          # taps made from this query's own mistakes
    elif agent.recipe == "sql_review" and run.status == "done" and any(_sql_res(run).get(k) for k in ("rewritten", "indexes", "check_query")):
        reply += "\n\n" + ("Fixes (rewritten query aur index commands) goal card mein hai, copy kar lo." if hi
                            else "The fixes (rewritten query and index commands) are in the goal card, ready to copy.")
    options += [t for t in (_sql_res(run).get("taps") or []) if t not in options]    # e.g. one tap to correct a guess
    return reply, options
