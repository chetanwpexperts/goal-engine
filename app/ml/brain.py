"""The ML conversation engine: no external AI.

Flow per message: clean text -> classify problem type -> pull out facts -> update the
persistent goal -> ask at most ONE thing (max 2 per goal, with tap answers) or act.

A short, vague first message gets one request for a ~100-word description (typed or
spoken) instead of a chain of questions.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.ml.answers import PLAYBOOKS
from app.ml.intent import IntentModel, Prediction
from app.ml.normalize import detect_language, normalize
from app.ml.slots import INTENT_LABELS, RELATED, SPECS, Slot, extract_all, main_issue
from app.models import Goal, dumps, loads


MAX_QUESTIONS_PER_GOAL = 2
SHORT_MESSAGE_WORDS = 15
BRIEF_TARGET_WORDS = 100
NEW_GOAL_CONFIDENCE = 0.40
SWITCH_CONFIDENCE = 0.75
REFINE_CONFIDENCE = 0.30
# Problem types a vague "business is not doing well" goal can turn into (a car problem never can).
REFINES_VAGUE = {"no_customers", "website_problem", "need_website", "customer_followup", "find_work",
                 "marketing_social", "payments_collection", "hiring_staff"}

SKIP_RE = re.compile(r"(pata nahi|nahi pata|maloom nahi|don ?t know|dont know|not sure|\bskip\b|aap decide|you decide|"
                     r"kuch bhi|koi bhi|anything|nahi bata sakta)")


@dataclass
class Turn:
    reply: str
    relation: str                       # new_goal / update_goal / supporting_task / chit_chat / unknown
    goal: Goal | None = None
    options: list[str] = field(default_factory=list)
    ask_words: int = 0                  # >0: UI shows a word counter with this target
    intent: str = ""
    confidence: float = 0.0


# ---------------------------------------------------------------- goal state helpers
def _slots(goal: Goal) -> dict[str, Any]:
    return loads(goal.slots, {}) or {}


def _spec(goal: Goal) -> dict:
    return SPECS.get(goal.intent or "", SPECS["business_slow"])


def _missing_critical(goal: Goal) -> list[Slot]:
    data = _slots(goal)
    skipped = set(data.get("_skipped", []))
    return [s for s in _spec(goal)["slots"] if s.critical and not data.get(s.key) and s.key not in skipped]


def _set_intent(goal: Goal, intent: str, confidence: float) -> None:
    spec = SPECS[intent]
    goal.intent = intent
    goal.title = spec["title"]
    data = _slots(goal)
    data["_conf"] = round(confidence, 3)
    goal.slots = dumps(data)


def _merge(goal: Goal, found: dict[str, str]) -> None:
    data = _slots(goal)
    for key, value in found.items():
        if value:
            data[key] = value
            if key in data.get("_skipped", []):
                data["_skipped"].remove(key)
    goal.slots = dumps(data)


def _skip(goal: Goal, key: str) -> None:
    data = _slots(goal)
    data.setdefault("_skipped", [])
    if key not in data["_skipped"]:
        data["_skipped"].append(key)
    goal.slots = dumps(data)


def refresh(goal: Goal, hi: bool) -> None:
    """Rebuild the visible goal state from intent + slots."""
    spec = _spec(goal)
    data = _slots(goal)
    skipped = set(data.get("_skipped", []))
    facts, constraints = [], []
    for slot in spec["slots"]:
        value = data.get(slot.key)
        if slot.key == "budget":
            if value:
                constraints.append(f"Budget: {value}")
            continue
        if value:
            facts.append(f"{slot.label}: {value}")
        elif slot.key in skipped and slot.default:
            facts.append(f"Assumed {slot.label.lower()}: {slot.default}")
    if data.get("main_issue_text"):
        facts.append(f"Main issue: {data['main_issue_text']}")
    missing = _missing_critical(goal)
    critical = [s for s in spec["slots"] if s.critical]
    fill = 1.0 if not critical else (len(critical) - len(missing)) / len(critical)
    goal.interpreted_problem = spec["problem"]
    goal.desired_goal = spec["goal"]
    goal.known_facts = dumps(facts)
    goal.constraints = dumps(constraints)
    goal.sub_goals = dumps(data.get("_supporting", []))
    goal.missing_info = dumps([{"question": s.question_hi if hi else s.question_en, "why": ""} for s in missing])
    goal.confidence = round(0.5 * float(data.get("_conf", 0.5)) + 0.5 * fill, 2)


# ---------------------------------------------------------------- replies
def _brief_request(hi: bool, understood: str = "") -> str:
    if hi:
        lead = f"Samajh gaya: {understood}. " if understood else ""
        return (f"{lead}Sahi madad ke liye apni situation thodi detail mein likh do (around {BRIEF_TARGET_WORDS} words):\n"
                "• kya kaam / business hai\n• kahan hai\n• kya problem aa rahi hai, aur kab se\n"
                "Likhna mushkil ho to 🎤 dabake bol do.")
    lead = f"Got it: {understood}. " if understood else ""
    return (f"{lead}To help properly, describe your situation in a bit more detail (around {BRIEF_TARGET_WORDS} words):\n"
            "• what the work or business is\n• where it is\n• what's going wrong, and since when\n"
            "If typing is hard, tap 🎤 and say it.")


def _question(slot: Slot, hi: bool) -> str:
    return (f"Theek hai. {slot.question_hi}" if hi else f"Okay. {slot.question_en}")


def _answer(goal: Goal, hi: bool) -> tuple[str, str]:
    data = {k: v for k, v in _slots(goal).items() if not k.startswith("_")}
    points, next_line = PLAYBOOKS[goal.intent](data, hi)
    head = "Ye kijiye:" if hi else "Here's what to do:"
    body = "\n".join(f"{i}. {p}" for i, p in enumerate(points, 1))
    return f"{head}\n{body}\n\n{next_line}", next_line


def _next_step(goal: Goal, hi: bool) -> Turn:
    """Ask the one most important missing thing (if budget allows), otherwise act."""
    missing = _missing_critical(goal)
    asked = goal.questions_asked or 0
    if missing and asked < MAX_QUESTIONS_PER_GOAL:
        slot = missing[0]
        goal.pending = slot.key
        goal.questions_asked = asked + 1
        refresh(goal, hi)
        goal.next_action_type = "ask"
        goal.next_action = slot.question_en
        return Turn(_question(slot, hi), "", goal, options=list(slot.options))
    for slot in missing:  # budget used up: assume and move on
        _skip(goal, slot.key)
    goal.pending = None
    refresh(goal, hi)
    reply, next_line = _answer(goal, hi)
    goal.next_action_type = "investigate" if goal.intent == "website_problem" and _slots(goal).get("website_url") else "execute"
    goal.next_action = next_line
    return Turn(reply, "", goal)


# ---------------------------------------------------------------- the turn
class Brain:
    def __init__(self, model: IntentModel | None = None):
        self.model = model or IntentModel()

    def _new_goal(self, visitor_id: str, text: str, pred: Prediction, hi: bool, lang: str) -> Turn:
        goal = Goal(visitor_id=visitor_id, title=SPECS[pred.intent]["title"], original_request=text,
                    detected_language=lang, slots=dumps({}), questions_asked=0, updates=0)
        _set_intent(goal, pred.intent, pred.confidence)
        _merge(goal, extract_all(pred.intent, text))
        words = len(text.split())
        vague = SPECS[pred.intent].get("vague", False)
        if words < SHORT_MESSAGE_WORDS and (vague or len(_missing_critical(goal)) >= 2):
            goal.pending = "brief"
            goal.questions_asked = 1
            refresh(goal, hi)
            goal.next_action_type = "ask"
            goal.next_action = "Get a short description of the situation (typed or spoken)"
            understood = SPECS[pred.intent]["title"].lower() if not vague else ""
            return Turn(_brief_request(hi, understood), "new_goal", goal, ask_words=BRIEF_TARGET_WORDS)
        turn = _next_step(goal, hi)
        turn.relation = "new_goal"
        return turn

    def _refine_vague(self, goal: Goal, text: str) -> None:
        """A vague goal ("business nahi chal raha") becomes specific from the details given."""
        pred = self.model.predict(text)
        if (pred.intent in SPECS and pred.intent in REFINES_VAGUE and pred.intent != goal.intent
                and pred.confidence >= REFINE_CONFIDENCE):
            _set_intent(goal, pred.intent, pred.confidence)
            return
        issue = main_issue(normalize(text))
        if issue in SPECS:
            _set_intent(goal, issue, 0.7)

    def _answer_pending(self, goal: Goal, text: str, hi: bool) -> Turn:
        pending, norm = goal.pending, normalize(text)
        if pending == "brief":
            combined = f"{goal.original_request} {text}"
            if _spec(goal).get("vague"):
                self._refine_vague(goal, text)
            data = _slots(goal)
            data["_brief"] = text[:2000]
            goal.slots = dumps(data)
            _merge(goal, extract_all(goal.intent, combined))
        else:
            slot = next((s for s in _spec(goal)["slots"] if s.key == pending), None)
            found = extract_all(goal.intent, text)
            if slot and not found.get(slot.key):
                if SKIP_RE.search(norm):
                    _skip(goal, slot.key)
                elif found:
                    pass  # they answered something else useful; keep that, don't force it into this slot
                elif slot.free_text and len(text.split()) <= 8:
                    found[slot.key] = text.strip()[:60]
                else:
                    _skip(goal, slot.key)
            _merge(goal, found)
            if pending == "main_issue":
                target = _slots(goal).get("main_issue")
                data = _slots(goal)
                data["main_issue_text"] = text.strip()[:60]
                goal.slots = dumps(data)
                if target in SPECS:
                    _set_intent(goal, target, 0.8)
                    _merge(goal, extract_all(target, f"{goal.original_request} {data.get('_brief', '')}"))
        goal.pending = None
        turn = _next_step(goal, hi)
        turn.relation = "update_goal"
        return turn

    def handle(self, visitor_id: str, text: str, focus: Goal | None) -> Turn:
        words = len(text.split())
        lang = detect_language(text)
        if words < 4 and focus is not None and focus.detected_language:
            lang = focus.detected_language  # "php me" stays in the conversation's language
        hi = lang in ("Hinglish", "Hindi")
        pred = self.model.predict(text)

        def done(turn: Turn) -> Turn:
            turn.intent, turn.confidence = pred.intent, round(pred.confidence, 3)
            if turn.goal is not None:
                turn.goal.updates = (turn.goal.updates or 0) + 1
                if words >= 4 or not turn.goal.detected_language:
                    turn.goal.detected_language = lang
            return turn

        # 1. An answer to the question we asked (unless it's clearly a brand-new problem).
        if focus is not None and focus.pending:
            pending_slot_found = focus.pending != "brief" and extract_all(focus.intent, text).get(focus.pending)
            brand_new = (pred.intent not in (focus.intent, "chit_chat") and pred.confidence >= SWITCH_CONFIDENCE
                         and words >= 3 and not pending_slot_found and not _spec(focus).get("vague")
                         and pred.intent not in RELATED.get(focus.intent, set()) and focus.pending != "brief")
            if not brand_new:
                return done(self._answer_pending(focus, text, hi))

        # 2. Greetings and thanks.
        if pred.intent == "chit_chat" and pred.confidence >= 0.5 and words <= 5:
            if focus is not None:
                reply = (f"Namaste! Hum \"{focus.title}\" pe kaam kar rahe hai. Kuch aur batana ho to likho ya 🎤 bolo."
                         if hi else f"Hi! We're working on \"{focus.title}\". Add anything else by typing or tapping 🎤.")
            else:
                reply = ("Namaste! Apni problem batao, likh ke ya 🎤 bol ke. Main samajh ke agla step bataunga."
                         if hi else "Hi! Tell me the problem, typed or by tapping 🎤, and I'll work out the next step.")
            return done(Turn(reply, "chit_chat"))

        # 3. More about the current goal, a supporting task, or a separate problem.
        if focus is not None:
            same = pred.intent == focus.intent
            refines = (_spec(focus).get("vague") and pred.intent in REFINES_VAGUE
                       and pred.confidence >= NEW_GOAL_CONFIDENCE)
            if same or refines:
                if refines and not same:
                    _set_intent(focus, pred.intent, pred.confidence)
                _merge(focus, extract_all(focus.intent, text))
                turn = _next_step(focus, hi)
                turn.relation = "update_goal"
                return done(turn)
            if pred.intent in RELATED.get(focus.intent, set()) and pred.confidence >= NEW_GOAL_CONFIDENCE:
                data = _slots(focus)
                label = SPECS[pred.intent]["title"]
                data.setdefault("_supporting", [])
                if label not in data["_supporting"]:
                    data["_supporting"].append(label)
                focus.slots = dumps(data)
                _merge(focus, extract_all(focus.intent, text))
                refresh(focus, hi)
                points, next_line = PLAYBOOKS[pred.intent]({k: v for k, v in data.items() if not k.startswith("_")}, hi)
                head = (f"Ye \"{focus.title}\" ka hissa hai, isliye maine ise supporting task bana diya. Abhi ke liye:"
                        if hi else f"This is part of \"{focus.title}\", so I added it as a supporting task. For now:")
                reply = head + "\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(points[:3], 1))
                return done(Turn(reply, "supporting_task", focus))
            if pred.intent in SPECS and pred.confidence >= NEW_GOAL_CONFIDENCE:
                return done(self._new_goal(focus.visitor_id, text, pred, hi, lang))
            _merge(focus, extract_all(focus.intent, text))
            turn = _next_step(focus, hi)
            turn.relation = "update_goal"
            return done(turn)

        # 4. No goal yet.
        if pred.intent in SPECS and pred.confidence >= NEW_GOAL_CONFIDENCE:
            return done(self._new_goal(visitor_id, text, pred, hi, lang))

        # 5. Not understood: one request for detail, plus tap choices. Never a chain of questions.
        choices = [INTENT_LABELS[i] for i, _ in pred.ranking if i in INTENT_LABELS][:3]
        reply = ("Main abhi poori tarah samjha nahi. Apni problem thodi detail mein likho (around "
                 f"{BRIEF_TARGET_WORDS} words) ya 🎤 dabake bolo. Ya inme se chuno:"
                 if hi else "I didn't fully get that. Describe it in a bit more detail (around "
                 f"{BRIEF_TARGET_WORDS} words) or tap 🎤 and say it. Or pick one:")
        return done(Turn(reply, "unknown", options=choices, ask_words=BRIEF_TARGET_WORDS))
