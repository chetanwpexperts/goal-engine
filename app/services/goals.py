"""Coordinate goal understanding, agents and persistence."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.ai.prompts import build_user_prompt
from app.ai.router import AIRouter
from app.core.config import Settings
from app.limits import release_ai_call, reserve_ai_call
from app.products.shared.core import (YES_RE, AgentError, agent_dict, assign, goal_agents, inputs_from_text,
                             recipe_for, run_agent)
from app.products.shared import automation as auto
from app.products.shared.core import URL_RE
from app.products.shared.planner import plan as plan_automation
from app.engine.ml.brain import SKIP_RE, steps_text
from app.engine.ml.registry import domain_of
from app.core.text import normalize
from app.engine.mind import route
from app.domain.models import Agent, Goal, Message, dumps, loads

logger = logging.getLogger(__name__)


from fastapi import HTTPException
from app.data import goals as storage
from app.data.goals import active_goals, all_goals, focus_goal, recent_messages
from app.engine.goal import (InvalidMessage, MAX_CONTEXT_GOALS, LIST_GOALS, STEPS_LABELS, WATCH_LABELS,
                             NO_EXPERT, NO_EXPERT_DOMAINS, SMALL_TALK_RE, _apply, agent_mode, validate_message)
from app.products.shared.replies import _after_run


def own_goal(db: Session, goal_id: int, visitor: str) -> Goal:
    goal = storage.get_goal(db, goal_id)
    if not goal or goal.visitor_id != visitor:
        raise HTTPException(404, "Goal not found")
    return goal


def _respond(db: Session, visitor_id: str, text: str, goal: Goal | None, reply: str, relation: str,
             options: list[str] | None = None, ask_words: int = 0, intent: str = "", confidence: float = 0.0,
             hi: bool = False) -> dict[str, Any]:
    gid = goal.id if goal else None
    storage.add(db, Message(visitor_id=visitor_id, goal_id=gid, role="user", text=text, relation=relation,
                   intent=intent or None, confidence=confidence or None))
    storage.add(db, Message(visitor_id=visitor_id, goal_id=gid, role="assistant", text=reply, relation=relation, provider="ml"))
    storage.commit(db)
    nxt: list[str] = []
    try:                                   # the mind: learn this step, work out the likely next questions
        from app.engine import mind

        thought = mind.after(db, visitor_id, goal, text, reply, goal_agents(db, goal.id) if goal else [], hi)
        # when the agent just asked the person something, the answer comes first: no suggestions next to it
        from app.products.shared.core import quiet_next

        own = any(o in ("Optimize it", "Ise fast karo") for o in options or []) or quiet_next(db, goal, text)
        # (the query checker waiting for its query: the query is the next step, nothing else)
        nxt = [] if own else [x for x in thought["next"] if x not in (options or [])]   # taps made from this reply replace the general ones
        if thought["reply"] != reply:
            reply = thought["reply"]
            storage.replace_last_reply(db, visitor_id, reply)
    except Exception:  # noqa: BLE001 - the mind never breaks a reply
        logger.exception("mind")
    finally:
        from app.engine.mind import core as _mc

        _mc._current.set(None)
    agents = [agent_dict(a, db, hi) for a in goal_agents(db, goal.id)] if goal else []
    return {
        "reply": reply,
        "next": nxt,
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


def _handle_ml(db: Session, visitor_id: str, text: str, brain, goal_id: int | None) -> dict[str, Any]:
    focus = focus_goal(db, visitor_id, goal_id)
    try:                                   # the mind reads the person before any agent answers
        from app.engine import mind

        mind.before(db, visitor_id, focus.id if focus is not None else None, text)
    except Exception:  # noqa: BLE001
        logger.exception("mind")
    hi_focus = (focus.detected_language in ("Hinglish", "Hindi")) if focus is not None else False

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
            storage.add(db, watch)
            storage.flush(db)
            try:
                run = run_agent(db, watch, hi)
                reply = (("Theek hai, ab main roz is website ko check karunga. Kuch toota to is goal mein alert aayega.\n\n" if hi
                          else "Done. I'll check this website every day and alert you in this goal if something breaks.\n\n")
                         + (run.summary or ""))
            except AgentError as exc:
                reply = str(exc)
            return _respond(db, visitor_id, text, focus, reply, "agent_run", hi=hi)

    # An agent holds this conversation (e.g. a file is open): what the person types is for it. Don't re-guess the topic.
    if focus is not None and not URL_RE.search(text or "") and not SMALL_TALK_RE.search(text or ""):
        held = route.holder(db, focus, text)
        if held is not None:
            name, agent = held
            out = route.continue_turn(name, db, focus, agent, text)
            reply, options, hi = out[:3]
            storage.commit(db)                    # an agent may say what this turn was (e.g. it ran the check the person was asked about)
            return _respond(db, visitor_id, text, focus, reply, out[3] if len(out) > 3 else "agent_reply", options=options, hi=hi)

    # A data link for a waiting automation agent: save it and show the preview straight away.
    if focus is not None:
        pending_auto = next((a for a in goal_agents(db, focus.id) if a.recipe == "automation"
                             and a.status in ("proposed", "paused")), None)
        link = auto.SOURCE_LINK_RE.search(text)
        if pending_auto is not None and link:
            inputs = loads(pending_auto.inputs, {}) or {}
            inputs["sheet_url"] = link.group(0)
            pending_auto.inputs = dumps(inputs)
            storage.commit(db)
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
                rel = "agent_run"
                try:
                    run = run_agent(db, agent, hi_focus)
                    reply, options = _after_run(agent, run, hi_focus)
                except AgentError as exc:              # an agent asking one choice first carries its taps
                    reply, options = str(exc), list(getattr(exc, "options", []))
                    rel = "agent_plan" if options else rel
                return _respond(db, visitor_id, text, focus, reply, rel, options=options, hi=hi_focus)

    turn = brain.handle(visitor_id, text, focus)
    goal = turn.goal
    if goal is not None and goal.id is None:
        storage.add(db, goal)
    storage.flush(db)
    reply, options, relation = turn.reply, turn.options, turn.relation
    spec = None
    file_open = goal is not None and route.holder(db, goal, text, any_text=True) is not None
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
        goal.next_action = auto.NEXT_ACTION
        storage.commit(db)
        reply, options, relation = auto.plan_reply(spec, turn.hi), [], "agent_plan"
    elif goal is not None and (taker := route.taker(db, goal, text, relation, file_open)) is not None:
        # a field agent takes it (its pack names the hooks): it works on the person's own file / system
        reply, options, relation = route.take(taker, db, goal, text, turn, visitor_id)
    elif goal is not None and relation not in ("howto", "safety", "chit_chat"):
        agent, override, run_now = assign(db, goal, text, turn.hi, asking=bool(goal.pending))
        if run_now:
            try:
                run = run_agent(db, agent, turn.hi)
                reply, options = _after_run(agent, run, turn.hi)
                relation = "agent_run"
            except AgentError as exc:
                reply, options = str(exc), list(getattr(exc, "options", []))
                relation = "agent_plan" if options else relation
        elif override:
            # The system does the work: show the plan instead of a to-do list for the user.
            reply = override
            goal.missing_info = "[]"   # the agent asks for its own input in its panel
            storage.commit(db)
            options = [next(k for k, v in STEPS_LABELS.items() if v == turn.hi)]
    return _respond(db, visitor_id, text, goal, reply, relation, options=options, ask_words=turn.ask_words,
                    intent=turn.intent, confidence=turn.confidence, hi=turn.hi)


def handle_message(db: Session, visitor_id: str, text: str, router: AIRouter, settings: Settings,
                   brain=None, goal_id: int | None = None, client_ip: str = "") -> dict[str, Any]:
    text = validate_message(text, settings.max_message_chars)

    if LIST_GOALS.match(text):
        goals = active_goals(db, visitor_id)
        reply = ("Here are your active goals." if goals else "You don't have any goals yet. Tell me what's wrong.")
        storage.add(db, Message(visitor_id=visitor_id, role="user", text=text, relation="list"))
        storage.add(db, Message(visitor_id=visitor_id, role="assistant", text=reply, relation="list", provider="app"))
        storage.commit(db)
        return {"reply": reply, "relation": "list", "goal": None,
                "goals": [g.summary() for g in all_goals(db, visitor_id)], "provider": "app"}

    reserve_ai_call(db, visitor_id, settings, client_ip)
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
        storage.add(db, goal)
    if goal is not None:
        _apply(goal, data)
        goal.updates = (goal.updates or 0) + 1

    storage.flush(db)
    goal_id = goal.id if goal else None
    storage.add(db, Message(visitor_id=visitor_id, goal_id=goal_id, role="user", text=text, relation=relation))
    storage.add(db, Message(visitor_id=visitor_id, goal_id=goal_id, role="assistant", text=data.reply,
                   relation=relation, provider=provider))
    storage.commit(db)

    return {
        "reply": data.reply,
        "relation": relation,
        "goal": goal.as_dict() if goal else None,
        "goals": [g.summary() for g in all_goals(db, visitor_id)],
        "provider": provider,
    }
