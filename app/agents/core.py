"""Agent assignment: the system picks and assembles the agent for a goal, the user only approves.

A recipe is a chain of building blocks (connect -> check -> act -> when -> verify) plus the
inputs it needs and how risky it is. The user never configures anything: inputs come from the
conversation, the plan is shown in plain words, and one tap starts it.
"""

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.sql import analyze_sql
from app.agents.website import check_website
from app.models import Agent, AgentRun, Goal, Message, dumps, loads, utcnow


@dataclass(frozen=True)
class Block:
    kind: str        # connect / check / act / when / verify
    en: str
    hi: str


@dataclass(frozen=True)
class Input:
    key: str
    kind: str        # url / sql / text
    label_en: str
    label_hi: str
    required: bool = True
    multiline: bool = False


@dataclass(frozen=True)
class Recipe:
    key: str
    name_en: str
    name_hi: str
    access: str                      # none / connector
    blocks: tuple[Block, ...]
    inputs: tuple[Input, ...]
    run: Callable[[dict], dict] | None
    summarize: Callable[[dict, bool], str] | None
    interval_minutes: int | None = None
    available: bool = True


# ---------------------------------------------------------------- summaries in plain words
def _website_summary(result: dict, hi: bool) -> str:
    if not result.get("ok"):
        return (f"Check nahi ho paya: {result.get('error')}" if hi else f"Couldn't check: {result.get('error')}")
    findings = result["findings"]
    if not findings:
        return ("Website check ki: koi badi problem nahi mili. Score 100/100." if hi
                else "Checked the website: no problems found. Score 100/100.")
    worst = findings[0]["title_hi" if hi else "title_en"]
    count = len(findings)
    return (f"Website check ki: {count} problem mili. Sabse badi: {worst}. Score {result['score']}/100." if hi
            else f"Checked the website: found {count} problem{'s' if count > 1 else ''}. Biggest: {worst}. Score {result['score']}/100.")


def _sql_summary(result: dict, hi: bool) -> str:
    if not result.get("ok"):
        return (f"Query check nahi ho payi: {result.get('error')}" if hi else f"Couldn't check the query: {result.get('error')}")
    n, idx = len(result["findings"]), len(result.get("indexes", []))
    extra = (" Rewritten query bhi taiyar hai." if hi else " A rewritten query is ready too.") if result.get("rewritten") else ""
    if idx and not result.get("indexes_verified"):
        extra += (" Aapke maujooda index mujhe nahi dikhte, isliye pehle card wali check query chalao (ya SHOW CREATE TABLE paste karo)."
                  if hi else " I can't see your current indexes from the query alone, so run the check query in the card first "
                             "(or paste SHOW CREATE TABLE) and I'll list only the missing ones.")
    what = ("index lag sakte hain" if hi else "possible indexes") if idx and not result.get("indexes_verified") else (
        "index suggest kiye" if hi else f"index{'es' if idx != 1 else ''} suggested")
    return (f"Query check ki: {n} problem mili, {idx} {what}.{extra}" if hi
            else f"Checked the query: {n} problem{'s' if n != 1 else ''} found, {idx} {what}.{extra}")


def _run_website(inputs: dict) -> dict:
    return check_website(inputs.get("url", ""))


def _run_sql(inputs: dict) -> dict:
    return analyze_sql(inputs.get("query", ""), inputs.get("explain", ""))


RECIPES: dict[str, Recipe] = {
    "website_check": Recipe(
        "website_check", "Website Checker", "Website Checker", "none",
        (Block("connect", "Open your public website, like a visitor (no login or password needed)",
               "Aapki public website kholunga, jaise visitor kholta hai (koi login ya password nahi)"),
         Block("check", "Check security, speed, phone layout, errors, Google basics and contact buttons",
               "Security, speed, phone layout, errors, Google basics aur contact buttons check karunga"),
         Block("act", "Report every problem in plain words, worst first, with the fix",
               "Har problem saaf shabdon mein bataunga, sabse badi pehle, aur uska fix")),
        (Input("url", "url", "Website address", "Website ka address"),),
        _run_website, _website_summary),
    "website_watch": Recipe(
        "website_watch", "Website Watch (daily)", "Website Watch (roz)", "none",
        (Block("when", "Every day", "Roz"),
         Block("check", "Check the website is up, secure and fast", "Check karunga website khul rahi hai, secure aur fast hai"),
         Block("act", "Add an alert to this goal if something breaks", "Kuch tootega to is goal mein alert daalunga"),
         Block("verify", "Keep a log of every check", "Har check ka log rakhunga")),
        (Input("url", "url", "Website address", "Website ka address"),),
        _run_website, _website_summary, interval_minutes=24 * 60),
    "sql_review": Recipe(
        "sql_review", "SQL Query Checker", "SQL Query Checker", "none",
        (Block("connect", "Read only the query you paste (no database access, no data)",
               "Sirf aapki paste ki hui query padhunga (database access nahi, data nahi)"),
         Block("check", "Check it against common slow-query problems and real bugs",
               "Slow query ki aam problems aur asli bugs check karunga"),
         Block("act", "Give a rewritten query and the exact index commands",
               "Rewritten query aur exact index commands dunga")),
        (Input("query", "sql", "Paste the SQL query", "SQL query yahan paste karo", multiline=True),
         Input("explain", "text", "EXPLAIN output or CREATE TABLE (optional)", "EXPLAIN output ya CREATE TABLE (optional)",
               required=False, multiline=True)),
        _run_sql, _sql_summary),
    "db_automation": Recipe(
        "db_automation", "Database Automation", "Database Automation", "connector",
        (Block("connect", "Connect through a small read-only connector on your server (your password never leaves it)",
               "Aapke server pe chhota read-only connector (aapka password wahin rehta hai)"),
         Block("when", "On the schedule you choose (e.g. every day 9 AM)", "Aapke chune samay pe (jaise roz subah 9 baje)"),
         Block("act", "Run the approved job: backup, report or alert", "Approved kaam: backup, report ya alert"),
         Block("verify", "Log every run and alert you if it fails", "Har run ka log aur fail hone pe alert")),
        (), None, None, available=False),
}

RECIPE_FOR_INTENT = {
    "website_problem": "website_check",
    "wordpress_issue": "website_check",
    "no_customers": "website_check",
    "server_down": "website_watch",
    "slow_db_query": "sql_review",
}

INPUT_SLOTS = {"website_check": ("website_url",), "website_watch": ("website_url",)}
URL_RE = re.compile(r"\b((?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|in|co\.in|net|org|co|io|biz|info|store|shop|online|co\.uk|com\.au|ae|dev|app|site|tech)(?:/[^\s]*)?)", re.I)
SQL_RE = re.compile(r"\b(select\b[\s\S]+?\bfrom\b|update\s+[`\w.]+\s+set\b|delete\s+from\b)", re.I)
YES_RE = re.compile(r"^\s*(haan|ha|haa|han|yes|yep|ok|okay|theek hai|thik hai|kar do|karo|start|go|chalu karo|shuru karo|please do)\b", re.I)

RUNS_PER_VISITOR_PER_DAY = 30
_running = threading.Lock()


def pasted_query(text: str) -> bool:
    """The whole message is one SQL query (maybe straight from code), or a short line and then one."""
    return query_in(text) is not None


def query_in(text: str) -> str | None:
    """The SQL query pasted in this message (maybe straight from code), after at most a short lead-in line like
    'here is my query:'; None when the message is a sentence that only mentions SQL."""
    from app.agents.sql import _strip_comments, unwrap

    lines = (text or "").strip().splitlines()
    for k in range(min(len(lines), 4)):
        lead, rest = " ".join(lines[:k]), "\n".join(lines[k:])
        if len(lead.split()) > 15:
            break
        q = _strip_comments(unwrap(rest)).strip()
        if re.match(r"^\(?\s*(select|with|update|delete)\b", q, re.I) and SQL_RE.search(q):
            return rest.strip()
    return None


def recipe_for(goal: Goal | None) -> Recipe | None:
    if goal is None:
        return None
    key = RECIPE_FOR_INTENT.get(goal.intent or "")
    if key == "website_check" and goal.intent == "no_customers":
        if (loads(goal.slots, {}) or {}).get("has_website") != "yes" and not (loads(goal.slots, {}) or {}).get("website_url"):
            return None  # no website to check
    return RECIPES.get(key) if key else None


def agent_dict(agent: Agent, db: Session, hi: bool) -> dict:
    if agent.recipe == "automation":
        from app.agents.automation import automation_dict

        return automation_dict(db, agent, hi)
    if agent.recipe == "database":
        from app.agents.database import database_dict

        return database_dict(db, agent, hi)
    if agent.recipe == "office":
        from app.agents.office_agent import office_dict

        return office_dict(db, agent, hi)
    if agent.recipe == "db_report":
        inputs = loads(agent.inputs, {}) or {}
        from app.agents.spec import schedule_text

        return {"id": agent.id, "lang": "hi" if hi else "en", "recipe": "db_report", "db_report": True,
                "name": inputs.get("title", "Database report"), "status": agent.status,
                "schedule": schedule_text(inputs.get("schedule") or {"every": "day", "at": "09:00"}, hi),
                "next_run_at": agent.next_run_at.isoformat() if agent.next_run_at else None, "sql": inputs.get("sql", ""),
                "blocks": [], "runs": [], "access": "connector", "available": True}
    recipe = RECIPES[agent.recipe]
    runs = list(db.scalars(select(AgentRun).where(AgentRun.agent_id == agent.id).order_by(AgentRun.id.desc()).limit(5)))
    inputs = loads(agent.inputs, {}) or {}
    return {
        "id": agent.id,
        "lang": "hi" if hi else "en",
        "recipe": recipe.key,
        "name": recipe.name_hi if hi else recipe.name_en,
        "status": agent.status,
        "access": recipe.access,
        "available": recipe.available,
        "blocks": [{"kind": b.kind, "text": b.hi if hi else b.en} for b in recipe.blocks],
        "inputs": [{"key": i.key, "kind": i.kind, "label": i.label_hi if hi else i.label_en, "required": i.required,
                    "multiline": i.multiline, "value": inputs.get(i.key, "")} for i in recipe.inputs],
        "ready": recipe.available and all(inputs.get(i.key) for i in recipe.inputs if i.required),
        "schedule": (f"every {recipe.interval_minutes // 60}h" if recipe.interval_minutes else "once"),
        "next_run_at": agent.next_run_at.isoformat() if agent.next_run_at else None,
        "runs": [{"id": r.id, "status": r.status, "summary": r.summary, "result": loads(r.result, None),
                  "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None}
                 for r in runs],
    }


def goal_agents(db: Session, goal_id: int) -> list[Agent]:
    return list(db.scalars(select(Agent).where(Agent.goal_id == goal_id).order_by(Agent.id.desc())))


def plan_text(recipe: Recipe, hi: bool, missing: list[Input]) -> str:
    """One short line: the plan itself is drawn in the agent panel."""
    head = (f"Main ye karunga: {recipe.name_hi}." if hi else f"Here's what I'll do: {recipe.name_en}.")
    if not recipe.available:
        return head + (" Ye agent abhi ban raha hai; aapki zaroorat note kar li hai. Tab tak steps dekh sakte ho."
                       if hi else " This agent is being built; your need is noted. Meanwhile you can see the steps.")
    if missing:
        need = missing[0]
        return head + (f" Bas ek cheez chahiye: {need.label_hi}." if hi else f" I just need one thing: {need.label_en}.")
    return head + (" Shuru karun? Start dabao ya \"haan\" likho." if hi else " Shall I start? Tap Start or type \"yes\".")


def inputs_from_text(recipe: Recipe, text: str, slots: dict) -> dict:
    found: dict[str, str] = {}
    for inp in recipe.inputs:
        if inp.kind == "url":
            m = URL_RE.search(text) or (URL_RE.search(slots.get("website_url", "")) if slots.get("website_url") else None)
            if m:
                found[inp.key] = m.group(1)
        elif inp.kind == "sql":
            m = SQL_RE.search(text)
            if m:                                  # pasted from code: keep the code around it, the checker unwraps it
                found[inp.key] = (query_in(text) or text[m.start():]).strip()[:20000]
    return found


def assign(db: Session, goal: Goal | None, text: str, hi: bool, asking: bool) -> tuple[Agent | None, str | None, bool]:
    """Create or update the goal's agent from this message.

    Returns (agent, reply_override, run_now). The reply override replaces generic advice with the plan.
    """
    recipe = recipe_for(goal)
    if goal is not None and (recipe is None or not any(i.kind == "sql" for i in recipe.inputs)) and pasted_query(text):
        goal.intent, recipe = "slow_db_query", RECIPES["sql_review"]   # the message IS a query: whatever the chat was about, check it
    if recipe is None or goal is None:
        return None, None, False
    if any(a.recipe == "automation" for a in goal_agents(db, goal.id)):
        return None, None, False  # this goal already has its automation agent
    slots = loads(goal.slots, {}) or {}
    agent = next((a for a in goal_agents(db, goal.id) if a.recipe == recipe.key), None)
    found = inputs_from_text(recipe, text, slots)
    if agent is None:
        if asking and goal.pending in INPUT_SLOTS.get(recipe.key, ()):
            goal.pending = None        # the agent asks for its own input (e.g. the website address)
            asking = False
        direct = goal.intent == "website_problem" and recipe.key in INPUT_SLOTS
        if asking and not found and not direct:
            return None, None, False   # the conversation is still asking its one question; propose after
        if direct:
            goal.pending = None        # "my website has issues": the agent only needs the address; ask for that, not an essay
        agent = Agent(visitor_id=goal.visitor_id, goal_id=goal.id, recipe=recipe.key, inputs=dumps(found),
                      status="proposed" if recipe.available else "planned")
        db.add(agent)
        db.flush()
        missing = [i for i in recipe.inputs if i.required and not found.get(i.key)]
        if recipe.available and not missing and any(i.kind == "sql" and found.get(i.key) for i in recipe.inputs):
            return agent, None, True   # a pasted query is the whole input (only its text is read): check it now, don't ask "start?"
        return agent, plan_text(recipe, hi, missing), False

    inputs = loads(agent.inputs, {}) or {}
    if found:
        inputs.update(found)
        agent.inputs = dumps(inputs)
    ready = recipe.available and all(inputs.get(i.key) for i in recipe.inputs if i.required)
    if agent.status in ("proposed", "done", "failed") and ready and (YES_RE.search(text) or found):
        return agent, None, True
    return agent, None, False   # the plan was shown once; never repeat it


def runs_today(db: Session, visitor_id: str) -> int:
    start = datetime(*utcnow().timetuple()[:3])
    return len(list(db.scalars(select(AgentRun.id).join(Agent, Agent.id == AgentRun.agent_id)
                               .where(Agent.visitor_id == visitor_id, AgentRun.started_at >= start))))


class AgentError(RuntimeError):
    pass


def run_agent(db: Session, agent: Agent, hi: bool, scheduled: bool = False) -> AgentRun:
    """Run now. Scheduled runs also post their summary into the goal's conversation."""
    recipe = RECIPES[agent.recipe]
    if not recipe.available or recipe.run is None:
        raise AgentError("This agent isn't available yet")
    inputs = loads(agent.inputs, {}) or {}
    missing = [i.label_en for i in recipe.inputs if i.required and not inputs.get(i.key)]
    if missing:
        raise AgentError(f"Missing: {', '.join(missing)}")
    if not scheduled and runs_today(db, agent.visitor_id) >= RUNS_PER_VISITOR_PER_DAY:
        raise AgentError("Today's limit for agent runs is reached. Please try again tomorrow.")

    run = AgentRun(agent_id=agent.id, status="running")
    db.add(run)
    agent.status = "running"
    db.commit()
    try:
        result = recipe.run(inputs)
        run.status = "done" if result.get("ok", True) else "failed"
    except Exception as exc:  # noqa: BLE001 - an agent failure must never break the app
        result = {"ok": False, "error": f"Agent failed: {type(exc).__name__}"}
        run.status = "failed"
    run.result = dumps(result)
    run.summary = recipe.summarize(result, hi) if recipe.summarize else ""
    run.finished_at = utcnow()
    agent.last_run_at = run.finished_at
    if recipe.interval_minutes:
        agent.status = "active"
        agent.interval_minutes = recipe.interval_minutes
        agent.next_run_at = run.finished_at + timedelta(minutes=recipe.interval_minutes)
    else:
        agent.status = run.status
    goal = db.get(Goal, agent.goal_id)
    if goal is not None:
        goal.next_action_type = "execute" if run.status == "done" else "investigate"
        goal.next_action = run.summary
        if scheduled:
            db.add(Message(visitor_id=agent.visitor_id, goal_id=goal.id, role="assistant", text=run.summary,
                           relation="agent_run", provider="agent"))
    db.commit()
    return run


def claim_due(db: Session, now: datetime) -> list[Agent]:
    """Claim scheduled agents that are due. Safe with several workers: each due agent is claimed once."""
    claimed = []
    for agent in db.scalars(select(Agent).where(Agent.status == "active", Agent.next_run_at <= now).limit(20)):
        result = db.execute(update(Agent).where(Agent.id == agent.id, Agent.next_run_at == agent.next_run_at)
                            .values(next_run_at=now + timedelta(minutes=agent.interval_minutes or 1440)))
        db.commit()
        if result.rowcount == 1:
            db.refresh(agent)
            claimed.append(agent)
    return claimed
