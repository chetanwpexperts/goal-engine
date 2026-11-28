"""Goal interpretation and message validation rules."""
import re
from app.ai.schema import Interpretation
from app.domain.models import Goal, dumps, loads

MAX_CONTEXT_GOALS = 6
RECENT_MESSAGES = 6

# Requests that application data can answer, with no AI call (cheapest reliable path).
LIST_GOALS = re.compile(
    r"^\s*(show|list|see|view|dikhao|dikha|batao)?\s*(my|meri|mere|all)?\s*(active\s+)?"
    r"(goals?|tasks?|problems?)\s*(dikhao|dikha|batao|list|show)?\s*[?.!]*\s*$",
    re.I,
)


class InvalidMessage(ValueError):
    pass

def _apply(goal: Goal, data: Interpretation) -> None:
    goal.title = (data.title or goal.title or "Untitled goal")[:200]
    goal.detected_language = data.detected_language or goal.detected_language
    goal.interpreted_problem = data.interpreted_problem or goal.interpreted_problem
    goal.desired_goal = data.desired_goal or goal.desired_goal
    goal.known_facts = dumps(data.known_facts)
    goal.missing_info = dumps([m.model_dump() for m in data.missing_info])
    goal.constraints = dumps(data.constraints)
    goal.sub_goals = dumps(data.sub_goals)
    goal.confidence = data.confidence
    goal.next_action_type = data.next_action.type
    goal.next_action = data.next_action.description

STEPS_LABELS = {"Khud karne ke steps dikhao": True, "Show me the steps instead": False}
WATCH_LABELS = {"Haan, roz check karo": True, "Yes, check it daily": False}


# These get safety guidance and the right real-world help (doctor, bank, 1930), never a sales offer
NO_EXPERT = {"health_symptom", "stress_burnout", "lost_card", "online_fraud"}
NO_EXPERT_DOMAINS = {"health"}

SMALL_TALK_RE = re.compile(r"^\s*(hi|hello|hey|thanks?|thank you|thx|ok|okay|bye|shukriya|dhanyavaad|namaste|good (morning|night|evening))\W*$", re.I)

def agent_mode(agent) -> str:
    return (loads(agent.inputs, {}) or {}).get("mode", "explore")


def validate_message(text, max_chars):
    text = (text or "").strip()
    if not text:
        raise InvalidMessage("Please write something first.")
    if len(text) > max_chars:
        raise InvalidMessage(f"Please keep it under {max_chars} characters.")
    return text
