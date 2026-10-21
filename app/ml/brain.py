"""The ML conversation engine: no external AI.

Flow per message: clean text -> classify problem type -> pull out facts -> update the
persistent goal -> ask at most ONE thing (max 2 per goal, with tap answers) or act.

A short, vague first message gets one request for a ~100-word description (typed or
spoken) instead of a chain of questions.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import hashlib

from app.ml.answers import PLAYBOOKS
from app.ml.howto import BY_KEY, STEP_CHOICES, choices_for, find_howto, render
from app.ml.intent import IntentModel, Prediction
from app.ml.normalize import detect_language, normalize
from app.ml.registry import BUSINESS_DOMAIN, short_title
from app.ml.slots import INTENT_LABELS, RELATED, SPECS, Slot, extract_all, main_issue
from app.models import Goal, dumps, loads


MAX_QUESTIONS_PER_GOAL = 2
SHORT_MESSAGE_WORDS = 15
BRIEF_TARGET_WORDS = 100
NEW_GOAL_CONFIDENCE = 0.22   # measured on unseen messages: almost always right above this
SWITCH_CONFIDENCE = 0.75
REFINE_CONFIDENCE = 0.30
SURE_CONFIDENCE = 0.50       # below this, a new goal also shows "wrong guess? pick one" alternatives
# Answers for these depend on what the business is and where; ask for the short description first.
CONTEXT_INTENTS = {"no_customers", "need_website", "marketing_social", "hiring_staff"}
# Problem types a vague "business is not doing well" goal can turn into (a car problem never can).
REFINES_VAGUE = {"no_customers", "website_problem", "need_website", "customer_followup", "find_work",
                 "marketing_social", "payments_collection", "hiring_staff"} | BUSINESS_DOMAIN

# Safety first: these never go through problem-solving.
CRISIS_RE = re.compile(r"(suicide|kill myself|end my life|want to die|no reason to live|self harm|hurt myself|"
                       r"marna chahta|marna chahti|mar jaana chahta|mar jana chahta|jeena nahi chahta|jeena nahi chahti|"
                       r"zindagi khatam|khudkushi|aatmahatya|atmahatya|khud ko nuksan|khud ko khatam)")
EMERGENCY_RE = re.compile(r"(accident ho gaya|heart attack|aag lag gayi|house on fire|saans nahi aa rahi|can t breathe|"
                          r"cannot breathe|behosh ho gaya|behosh ho gayi|unconscious|bahut khoon)")

# "I didn't understand" / "you gave the same answer again": go one simple step at a time.
CONFUSED_RE = re.compile(r"(samajh nahi|samjh nahi|samaj nahi|samjha nahi|nahi samajh|nahi samjha|samajh mein nahi|"
                         r"didn.?t understand|did not understand|don.?t understand|not clear|confus|"
                         r"phir se (vahi|wahi)|(vahi|wahi|same) (answer|aansar|jawab|baat)|same thing again|repeat)")

SKIP_RE = re.compile(r"(pata nahi|nahi pata|maloom nahi|don.?t know|dont know|not sure|\bskip\b|aap decide|you decide|"
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
    hi: bool = True


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
    digest = hashlib.sha1(reply.encode()).hexdigest()[:12]
    data = _slots(goal)
    if data.get("_last_answer") == digest:
        # Never send the same answer twice: offer to explain one step instead.
        choices = choices_for(goal.intent)
        text = ("Ye steps maine upar bata diye hai. Kis step ko detail mein samjhaun?" if hi
                else "I've shared these steps above. Which one should I explain in detail?")
        if not choices:
            text = ("Ye steps upar hai. Kuch naya batana ho (jaise kaam, shehar, ya kya try kiya) to likho ya 🎤 bolo."
                    if hi else "The steps are above. Tell me anything new (your work, city, what you tried) to refine them.")
        goal.next_action_type = "execute"
        goal.next_action = next_line
        return Turn(text, "", goal, options=choices)
    data["_last_answer"] = digest
    goal.slots = dumps(data)
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
        context_known = sum(1 for k in ("business_type", "city") if _slots(goal).get(k))
        needs_context = pred.intent in CONTEXT_INTENTS and context_known == 0
        if words < SHORT_MESSAGE_WORDS and (vague or needs_context or len(_missing_critical(goal)) >= 2):
            goal.pending = "brief"
            goal.questions_asked = 1
            refresh(goal, hi)
            goal.next_action_type = "ask"
            goal.next_action = "Get a short description of the situation (typed or spoken)"
            understood = SPECS[pred.intent]["title"].lower() if not vague else ""
            return Turn(_brief_request(hi, understood), "new_goal", goal, ask_words=BRIEF_TARGET_WORDS)
        turn = _next_step(goal, hi)
        turn.relation = "new_goal"
        if pred.confidence < SURE_CONFIDENCE and not turn.options:
            alts = [i for i, _ in pred.ranking[1:4] if i in INTENT_LABELS][:2]  # right answer is in the top 3 ~92% of the time
            if alts:
                data = _slots(goal)
                data["_alts"] = alts
                goal.slots = dumps(data)
                turn.options = [INTENT_LABELS[i] for i in alts]
                turn.reply += "\n\n" + ("Galat samjha? Inme se chuno:" if hi else "Did I get it wrong? Pick one:")
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
        if focus is not None and focus.intent not in SPECS:
            focus = None  # goals made by the old AI mode: start fresh instead of guessing their type
        words = len(text.split())
        lang = detect_language(text)
        if words < 4 and focus is not None and focus.detected_language:
            lang = focus.detected_language  # "php me" stays in the conversation's language
        hi = lang in ("Hinglish", "Hindi")
        pred = self.model.predict(text)

        norm_text = normalize(text)
        if CRISIS_RE.search(norm_text):
            reply = ("Mujhe bahut afsos hai ki aap itna mushkil waqt dekh rahe ho. Aapko ye akele nahi jhelna hai. "
                     "Abhi kisi se baat karo: Tele-MANAS 14416 (free, 24x7) pe call karo, ya turant khatra ho to 112. "
                     "Ho sake to kisi bharose wale insaan ko abhi batao ki aap theek nahi ho."
                     if hi else
                     "I'm really sorry you're going through this. You don't have to carry it alone. "
                     "Please talk to someone now: call Tele-MANAS 14416 (free, 24x7, India), or 112 if you're in immediate danger. "
                     "If you can, tell someone you trust right now that you're not okay.")
            return Turn(reply, "safety", None, intent="safety", confidence=1.0)
        if EMERGENCY_RE.search(norm_text):
            reply = ("Ye emergency hai: abhi 112 call karo (ambulance 108). Madad aane tak insaan ke saath raho."
                     if hi else "This is an emergency: call 112 now (ambulance 108). Stay with the person until help arrives.")
            return Turn(reply, "safety", None, intent="safety", confidence=1.0)

        # 0a. Confused or "same answer again": one simple step, with the others as buttons.
        if CONFUSED_RE.search(normalize(text)):
            steps = STEP_CHOICES.get(focus.intent, []) if focus is not None else []
            if steps:
                first = BY_KEY[steps[0]]
                lead = ("Koi baat nahi, ek-ek step karte hai. Sabse pehle ye:" if hi
                        else "No problem, let's go one step at a time. First this:")
                reply = lead + "\n" + render(first, hi).split("\n", 1)[1]
                reply += "\n\n" + ("Ye ho jaaye to agla step chuno:" if hi else "When that's done, pick the next step:")
                focus.updates = (focus.updates or 0) + 1
                return Turn(reply, "howto", focus, options=choices_for(focus.intent, exclude=first.key),
                            intent=pred.intent, confidence=round(pred.confidence, 3))
            reply = ("Koi baat nahi. Apni problem apne shabdon mein dobara batao, likh ke ya 🎤 bol ke."
                     if hi else "No problem. Tell me the problem again in your own words, typed or by tapping 🎤.")
            return Turn(reply, "unknown", focus, ask_words=BRIEF_TARGET_WORDS, intent=pred.intent,
                        confidence=round(pred.confidence, 3))

        # 0. "How do I do that step?" -> explain that step, never repeat the list.
        howto = find_howto(text)
        if howto is not None:
            reply = render(howto, hi)
            options: list[str] = []
            if focus is not None:
                options = choices_for(focus.intent, exclude=howto.key)
                if options:
                    reply += "\n\n" + ("Aur kisi step mein madad chahiye?" if hi else "Want help with another step?")
                focus.updates = (focus.updates or 0) + 1
            return Turn(reply, "howto", focus, options=options, intent=pred.intent,
                        confidence=round(pred.confidence, 3))

        def done(turn: Turn) -> Turn:
            turn.intent, turn.confidence, turn.hi = pred.intent, round(pred.confidence, 3), hi
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
            alts = _slots(focus).get("_alts", [])
            if pred.intent in alts and pred.confidence >= NEW_GOAL_CONFIDENCE:
                # The person corrected our guess: fix this goal instead of starting another.
                _set_intent(focus, pred.intent, 0.9)
                data = _slots(focus)
                data.pop("_alts", None)
                data.pop("_last_answer", None)
                data["_corrected_from_text"] = focus.original_request[:300]
                focus.slots = dumps(data)
                focus.title = SPECS[pred.intent]["title"]
                _merge(focus, extract_all(focus.intent, focus.original_request))
                turn = _next_step(focus, hi)
                turn.relation = "corrected"
                return done(turn)
            same = pred.intent == focus.intent
            refines = ((_spec(focus).get("vague") and pred.intent in REFINES_VAGUE
                        or focus.intent == "general" and pred.intent in SPECS)
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
            found = extract_all(focus.intent, text)
            if words >= 8 and not found and focus.intent != "general":
                # A substantial message the engine can't place: its own goal, not glued onto this one.
                return done(self._general_goal(focus.visitor_id, text, pred, hi, lang, words))
            _merge(focus, found)
            turn = _next_step(focus, hi)
            turn.relation = "update_goal"
            return done(turn)

        # 4. No goal yet.
        if pred.intent in SPECS and pred.confidence >= NEW_GOAL_CONFIDENCE:
            return done(self._new_goal(visitor_id, text, pred, hi, lang))

        # 5. Not learned yet: still a goal, facts and a first plan. Never a loop of questions.
        return done(self._general_goal(visitor_id, text, pred, hi, lang, words))

    def _general_goal(self, visitor_id: str, text: str, pred: Prediction, hi: bool, lang: str, words: int) -> Turn:
        goal = Goal(visitor_id=visitor_id, title="", original_request=text, detected_language=lang,
                    slots=dumps({}), questions_asked=MAX_QUESTIONS_PER_GOAL, updates=0)
        _set_intent(goal, "general", pred.confidence)
        goal.title = short_title(text)
        _merge(goal, extract_all("general", text))
        turn = _next_step(goal, hi)
        lead = ("Is tarah ki problem main abhi seekh raha hoon, par shuruaat aise karo:" if hi
                else "I'm still learning this kind of problem, but here's how to start:")
        turn.reply = lead + "\n" + turn.reply.split("\n", 1)[1]
        turn.options = [INTENT_LABELS[i] for i, c in pred.ranking if i in INTENT_LABELS and c >= 0.12][:3]
        if turn.options:
            turn.reply += "\n\n" + ("Kya aapka matlab inme se kuch hai?" if hi else "Did you mean one of these?")
        if words < 5:
            turn.ask_words = BRIEF_TARGET_WORDS
        turn.relation = "new_goal"
        return turn


def steps_text(goal: Goal, hi: bool) -> str:
    """The do-it-yourself steps, for people who prefer to do it themselves."""
    return _answer(goal, hi)[0]
