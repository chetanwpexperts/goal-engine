"""Receive -> understand -> structure. Every message updates goal state, not just the chat."""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import build_user_prompt
from app.ai.router import AIRouter
from app.ai.schema import Interpretation
from app.config import Settings
from app.limits import release_ai_call, reserve_ai_call
from app.agents.core import (YES_RE, AgentError, agent_dict, assign, goal_agents, inputs_from_text,
                             recipe_for, run_agent)
from app.agents import automation as auto
from app.agents import database as dbagent
from app.agents import office_agent as officeagent
from app.agents.core import URL_RE
from app.agents.planner import plan as plan_automation
from app.ml.brain import SKIP_RE, steps_text
from app.ml.registry import domain_of
from app.ml.normalize import normalize
from app.models import Agent, Goal, Message, dumps, loads


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


def active_goals(db: Session, visitor_id: str, limit: int | None = None) -> list[Goal]:
    query = (select(Goal).where(Goal.visitor_id == visitor_id, Goal.status == "active")
             .order_by(Goal.updated_at.desc(), Goal.id.desc()))
    if limit:
        query = query.limit(limit)
    return list(db.scalars(query))


def all_goals(db: Session, visitor_id: str) -> list[Goal]:
    return list(db.scalars(select(Goal).where(Goal.visitor_id == visitor_id)
                           .order_by(Goal.updated_at.desc(), Goal.id.desc())))


def recent_messages(db: Session, visitor_id: str) -> list[dict[str, str]]:
    rows = list(db.scalars(select(Message).where(Message.visitor_id == visitor_id)
                           .order_by(Message.id.desc()).limit(RECENT_MESSAGES)))
    return [{"role": m.role, "text": m.text[:500]} for m in reversed(rows)]


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


def focus_goal(db: Session, visitor_id: str, goal_id: int | None) -> Goal | None:
    """The goal the person is looking at, else their most recent active goal."""
    if goal_id:
        goal = db.get(Goal, goal_id)
        if goal and goal.visitor_id == visitor_id and goal.status == "active":
            return goal
    goals = active_goals(db, visitor_id, 1)
    return goals[0] if goals else None


SHEET_RE = re.compile(r"https?://(?:docs\.google\.com/spreadsheets/[^\s]+|[^\s]+\.csv\b[^\s]*)", re.I)
STEPS_LABELS = {"Khud karne ke steps dikhao": True, "Show me the steps instead": False}
WATCH_LABELS = {"Haan, roz check karo": True, "Yes, check it daily": False}


# These get safety guidance and the right real-world help (doctor, bank, 1930), never a sales offer
NO_EXPERT = {"health_symptom", "stress_burnout", "lost_card", "online_fraud"}
NO_EXPERT_DOMAINS = {"health"}


def _respond(db: Session, visitor_id: str, text: str, goal: Goal | None, reply: str, relation: str,
             options: list[str] | None = None, ask_words: int = 0, intent: str = "", confidence: float = 0.0,
             hi: bool = True) -> dict[str, Any]:
    gid = goal.id if goal else None
    db.add(Message(visitor_id=visitor_id, goal_id=gid, role="user", text=text, relation=relation,
                   intent=intent or None, confidence=confidence or None))
    db.add(Message(visitor_id=visitor_id, goal_id=gid, role="assistant", text=reply, relation=relation, provider="ml"))
    db.commit()
    agents = [agent_dict(a, db, hi) for a in goal_agents(db, goal.id)] if goal else []
    return {
        "reply": reply,
        "relation": relation,
        "goal": goal.as_dict() if goal else None,
        "goals": [g.summary() for g in all_goals(db, visitor_id)],
        "provider": "ml",
        "options": options or [],
        "ask_words": ask_words,
        "agents": agents,
        "offer_expert": bool(goal is not None and not agents and relation in ("new_goal", "corrected", "update_goal")
                             and (goal.intent or "") not in NO_EXPERT and domain_of(goal.intent or "") not in NO_EXPERT_DOMAINS),
    }


def _after_run(agent, run, hi: bool) -> tuple[str, list[str]]:
    reply = run.summary or ""
    options: list[str] = []
    if agent.recipe == "website_check" and run.status == "done":
        reply += "\n\n" + ("Poori report goal card mein hai. Roz check karta rahoon, taaki kuch toote to turant pata chale?"
                            if hi else "The full report is in the goal card. Shall I keep checking it daily, so you know the moment something breaks?")
        options = [next(k for k, v in WATCH_LABELS.items() if v == hi)]
    elif agent.recipe == "sql_review" and run.status == "done":
        reply += "\n\n" + ("Fixes (rewritten query aur index commands) goal card mein hai, copy kar lo." if hi
                            else "The fixes (rewritten query and index commands) are in the goal card, ready to copy.")
    return reply, options


ANOTHER_SITE_RE = re.compile(r"\b(koi|ek|aur|or|another|dusri|doosri|dusra|doosra|new|nayi|naya|one more|ek aur)\b.{0,25}\b(website|site|web ?site|url|domain)\b"
                             r"|\b(website|site)\b.{0,25}\b(aur|or|another|dusri|doosri|bhi)\b", re.I)
SMALL_TALK_RE = re.compile(r"^\s*(hi|hello|hey|thanks?|thank you|thx|ok|okay|bye|shukriya|dhanyavaad|namaste|good (morning|night|evening))\W*$", re.I)


def _handle_ml(db: Session, visitor_id: str, text: str, brain, goal_id: int | None) -> dict[str, Any]:
    focus = focus_goal(db, visitor_id, goal_id)
    hi_focus = (focus.detected_language in ("Hinglish", "Hindi")) if focus is not None else True

    # Tap: "show me the steps instead"
    if text.strip() in STEPS_LABELS and focus is not None and focus.intent:
        hi = STEPS_LABELS[text.strip()]
        return _respond(db, visitor_id, text, focus, steps_text(focus, hi), "howto", hi=hi)

    # Tap: "yes, check it daily" -> a scheduled watch agent with the same website
    if text.strip() in WATCH_LABELS and focus is not None:
        hi = WATCH_LABELS[text.strip()]
        source = next((a for a in goal_agents(db, focus.id) if a.recipe == "website_check"), None)
        if source is not None:
            watch = Agent(visitor_id=visitor_id, goal_id=focus.id, recipe="website_watch", inputs=source.inputs, status="proposed")
            db.add(watch)
            db.flush()
            try:
                run = run_agent(db, watch, hi)
                reply = (("Theek hai, ab main roz is website ko check karunga. Kuch toota to is goal mein alert aayega.\n\n" if hi
                          else "Done. I'll check this website every day and alert you in this goal if something breaks.\n\n")
                         + (run.summary or ""))
            except AgentError as exc:
                reply = str(exc)
            return _respond(db, visitor_id, text, focus, reply, "agent_run", hi=hi)

    # "koi aur website check kar sakte ho?": same agent, new address. Ask for it; don't start a different topic.
    if focus is not None and ANOTHER_SITE_RE.search(text or "") and not URL_RE.search(text or ""):
        site = next((a for a in goal_agents(db, focus.id) if a.recipe in ("website_check", "website_watch")), None)
        if site is not None:
            from app.ml.normalize import detect_language

            hi = detect_language(text) in ("Hinglish", "Hindi") or (focus.detected_language in ("Hinglish", "Hindi") and len(text.split()) < 8)
            reply = ("Haan. Doosri website ka address bhejo (jaise example.com), main turant check karta hoon." if hi
                     else "Yes. Send the other website's address (like example.com) and I'll check it right away.")
            return _respond(db, visitor_id, text, focus, reply, "agent_plan", hi=hi)

    # A file is open in this conversation: what the person types is about that file. Stay on it; don't re-guess the topic.
    if focus is not None:
        _oa = officeagent.goal_agent(db, focus)
        if _oa is not None and officeagent.open_file(db, _oa) is not None and not dbagent.DB_WORDS.search(text or "") \
                and not URL_RE.search(text or "") and not SMALL_TALK_RE.search(text or ""):
            from app.ml.normalize import HINGLISH_MARKERS, detect_language, tokens

            lang = detect_language(text)
            if lang == "English" and focus.detected_language in ("Hinglish", "Hindi") and (
                    len(text.split()) < 4 or any(w in HINGLISH_MARKERS for w in tokens(text))):
                lang = focus.detected_language
            hi = lang in ("Hinglish", "Hindi")
            task_reply = officeagent.chat_task(db, _oa, text, hi)
            options = []
            if isinstance(task_reply, tuple):
                task_reply, options = task_reply
            if not task_reply:
                task_reply = officeagent.cant_yet(officeagent.open_file(db, _oa), hi)
            db.commit()
            return _respond(db, visitor_id, text, focus, task_reply, "agent_plan", options=options, hi=hi)

    # A sheet link for a waiting automation agent: save it and show the preview straight away.
    if focus is not None:
        pending_auto = next((a for a in goal_agents(db, focus.id) if a.recipe == "automation"
                             and a.status in ("proposed", "paused")), None)
        link = SHEET_RE.search(text)
        if pending_auto is not None and link:
            inputs = loads(pending_auto.inputs, {}) or {}
            inputs["sheet_url"] = link.group(0)
            pending_auto.inputs = dumps(inputs)
            db.commit()
            run = auto.run(db, pending_auto, hi_focus, preview=True)
            return _respond(db, visitor_id, text, focus, run.summary or "", "agent_run", hi=hi_focus)

    # The message carries what a waiting agent needs (a URL, a pasted query): use it, don't re-classify.
    if focus is not None:
        recipe = recipe_for(focus)
        waiting = next((a for a in goal_agents(db, focus.id) if recipe and a.recipe == recipe.key
                        and a.status in ("proposed", "done", "failed")), None)
        if (waiting is not None and waiting.status == "proposed" and SKIP_RE.search(normalize(text))
                and not inputs_from_text(recipe, text, {})):
            reply = (("Koi baat nahi. Jab mil jaaye tab bhej dena, main check kar dunga. Tab tak ye kar sakte ho:\n"
                      if hi_focus else "No problem. Send it when you have it and I'll run the check. Meanwhile:\n")
                     + steps_text(focus, hi_focus))
            return _respond(db, visitor_id, text, focus, reply, "howto", hi=hi_focus)
        if waiting is not None and recipe is not None and (inputs_from_text(recipe, text, {}) or (
                waiting.status == "proposed" and YES_RE.search(text))):
            agent, _override, run_now = assign(db, focus, text, hi_focus, asking=False)
            if run_now:
                try:
                    run = run_agent(db, agent, hi_focus)
                    reply, options = _after_run(agent, run, hi_focus)
                except AgentError as exc:
                    reply, options = str(exc), []
                return _respond(db, visitor_id, text, focus, reply, "agent_run", options=options, hi=hi_focus)

    turn = brain.handle(visitor_id, text, focus)
    goal = turn.goal
    if goal is not None and goal.id is None:
        db.add(goal)
    db.flush()
    reply, options, relation = turn.reply, turn.options, turn.relation
    spec = None
    file_open = False
    if goal is not None:
        _oa = officeagent.goal_agent(db, goal)
        file_open = _oa is not None and officeagent.open_file(db, _oa) is not None
    if goal is not None and relation not in ("howto", "safety", "chit_chat") and not file_open:
        spec = plan_automation(text)
    if spec is not None:
        # "Do this automatically" -> build the agent from blocks; the owner only checks the preview and taps Start.
        existing = next((a for a in goal_agents(db, goal.id) if a.recipe == "automation"
                         and (loads(a.inputs, {}) or {}).get("spec", {}).get("template") == spec.get("template")), None)
        if existing is None:
            auto.create_agent(db, goal, spec)
        # The agent now owns this goal: same name, and no leftover questions from the conversation engine.
        goal.title = spec["title"]
        goal.missing_info = "[]"
        goal.pending = None
        goal.next_action_type = "execute"
        goal.next_action = "Add the sheet, check the preview, then Start"
        db.commit()
        reply, options, relation = auto.plan_reply(spec, turn.hi), [], "agent_plan"
    elif goal is not None and relation not in ("safety", "chit_chat") and (
            (file_open and not dbagent.DB_WORDS.search(text or "")) or officeagent.wants_office(goal, text, db)):
        # Excel / file work: the agent works on the file itself and gives back a fixed copy.
        from app.officeknow import brain as officebrain

        rec = officebrain.recognize(text)
        agent = officeagent.goal_agent(db, goal) or officeagent.create_agent(db, goal)
        task_reply = officeagent.chat_task(db, agent, text, turn.hi)
        inputs = loads(agent.inputs, {}) or {}
        if rec["confidence"] >= officeagent.SURE and not task_reply and officeagent.open_file(db, agent) is None:
            inputs["brain"] = rec
            goal.title = officebrain.describe(rec, turn.hi)
        elif not goal.title:
            goal.title = "Excel / file ka kaam" if turn.hi else "Excel and file work"
        agent.inputs = dumps(inputs)
        goal.missing_info = "[]"
        goal.pending = None
        goal.next_action_type = "execute"
        goal.next_action = "Give the file to the agent"
        db.commit()
        opened = officeagent.open_file(db, agent)
        if not task_reply and opened is not None:
            task_reply = officeagent.cant_yet(opened, turn.hi)
        task_options = []
        if isinstance(task_reply, tuple):
            task_reply, task_options = task_reply
        reply, options, relation = (task_reply or officeagent.plan_reply(rec, turn.hi)), task_options, "agent_plan"
    elif goal is not None and relation not in ("howto", "safety", "chit_chat") and dbagent.wants_database(goal, text):
        # Database work: the agent works through a connector on the user's own server.
        from app.dbknow import brain as dbbrain

        mode = dbagent.detect_mode(text)
        rec = dbbrain.recognize(text)
        agent = dbagent.goal_agent(db, goal)
        if agent is None:
            agent = dbagent.create_agent(db, goal, mode)
        inputs = loads(agent.inputs, {}) or {}
        if mode != "explore":
            inputs["mode"] = mode
        if rec["kind"] in ("sql", "error") or (rec["kind"] in ("issue", "do") and rec["confidence"] >= 0.35):
            inputs["brain"] = {**rec, "sql": text if rec["kind"] == "sql" else ""}
        agent.inputs = dumps(inputs)
        goal.title = dbagent.MODE_TEXT[agent_mode(agent)][0 if turn.hi else 1].split(" (")[0]
        goal.missing_info = "[]"
        goal.pending = None
        goal.next_action_type = "execute"
        goal.next_action = "Connect the database, then pick a table"
        db.commit()
        has = bool(dbagent.visitor_connectors(db, visitor_id))
        reply, options, relation = dbagent.plan_reply(mode, turn.hi, has, dbagent.later_parts(text)), [], "agent_plan"
        b = inputs.get("brain") or {}
        seen = dbbrain.describe(b, turn.hi) if (b.get("kind") in ("issue", "error", "sql") or b.get("id") == "slow_query") else ""
        if seen:
            reply = (f"Samjha: {seen}. " if turn.hi else f"Understood: {seen}. ") + (
                ("Database juda hua hai, card mein check shuru karo." if has else "Bas batao database kahan hai; judte hi main khud check karke wajah aur fix dhoondhunga.") if turn.hi
                else ("Your database is connected; start the check on the card." if has else "Just tell me where your database is; once it's connected I'll check it myself and find the cause and the fix."))
    elif goal is not None and relation not in ("howto", "safety", "chit_chat"):
        agent, override, run_now = assign(db, goal, text, turn.hi, asking=bool(goal.pending))
        if run_now:
            try:
                run = run_agent(db, agent, turn.hi)
                reply, options = _after_run(agent, run, turn.hi)
                relation = "agent_run"
            except AgentError as exc:
                reply = str(exc)
        elif override:
            # The system does the work: show the plan instead of a to-do list for the user.
            reply = override
            goal.missing_info = "[]"   # the agent asks for its own input in its panel
            db.commit()
            options = [next(k for k, v in STEPS_LABELS.items() if v == turn.hi)]
    return _respond(db, visitor_id, text, goal, reply, relation, options=options, ask_words=turn.ask_words,
                    intent=turn.intent, confidence=turn.confidence, hi=turn.hi)


def agent_mode(agent) -> str:
    return (loads(agent.inputs, {}) or {}).get("mode", "explore")


def handle_message(db: Session, visitor_id: str, text: str, router: AIRouter, settings: Settings,
                   brain=None, goal_id: int | None = None) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise InvalidMessage("Please write something first.")
    if len(text) > settings.max_message_chars:
        raise InvalidMessage(f"Please keep it under {settings.max_message_chars} characters.")

    if LIST_GOALS.match(text):
        goals = active_goals(db, visitor_id)
        reply = ("Here are your active goals." if goals else "You don't have any goals yet. Tell me what's wrong.")
        db.add(Message(visitor_id=visitor_id, role="user", text=text, relation="list"))
        db.add(Message(visitor_id=visitor_id, role="assistant", text=reply, relation="list", provider="app"))
        db.commit()
        return {"reply": reply, "relation": "list", "goal": None,
                "goals": [g.summary() for g in all_goals(db, visitor_id)], "provider": "app"}

    reserve_ai_call(db, visitor_id, settings)
    if settings.engine_mode == "ml" and brain is not None:
        return _handle_ml(db, visitor_id, text, brain, goal_id)

    context = active_goals(db, visitor_id, MAX_CONTEXT_GOALS)
    prompt = build_user_prompt(text, [g.as_dict() for g in context], recent_messages(db, visitor_id))
    try:
        data, provider = router.interpret(prompt)
    except Exception:
        release_ai_call(db, visitor_id)
        raise

    relation = data.relation
    goal: Goal | None = None
    if relation in ("update_goal", "supporting_task"):
        goal = next((g for g in context if g.id == data.goal_ref), None)
        if goal is None:  # the AI pointed at a goal that isn't this visitor's: start a new one
            relation = "new_goal"
    if relation == "new_goal":
        goal = Goal(visitor_id=visitor_id, title=data.title or "Untitled goal", original_request=text)
        db.add(goal)
    if goal is not None:
        _apply(goal, data)
        goal.updates = (goal.updates or 0) + 1

    db.flush()
    goal_id = goal.id if goal else None
    db.add(Message(visitor_id=visitor_id, goal_id=goal_id, role="user", text=text, relation=relation))
    db.add(Message(visitor_id=visitor_id, goal_id=goal_id, role="assistant", text=data.reply,
                   relation=relation, provider=provider))
    db.commit()

    return {
        "reply": data.reply,
        "relation": relation,
        "goal": goal.as_dict() if goal else None,
        "goals": [g.summary() for g in all_goals(db, visitor_id)],
        "provider": provider,
    }
