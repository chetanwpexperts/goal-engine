"""Agent execution, scheduling controls and owner verification."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import privacy
from app.agents import automation, database as dbagent
from app.agents.core import AgentError, agent_dict, goal_agents, run_agent
from app.engine import _after_run, all_goals
from app.models import Agent, Goal, Message, dumps, loads
from app.domain.schemas.agents import StartIn, OwnerIn
from app.services.output import public


def own_agent(db: Session, agent_id: int, visitor: str) -> Agent:
    agent = db.get(Agent, agent_id)
    if not agent or agent.visitor_id != visitor:
        raise HTTPException(404, "Agent not found")
    return agent


def _goal_hi(goal: Goal | None) -> bool:
    return bool(goal is None or goal.detected_language in ("Hinglish", "Hindi"))


def _agent_payload(db: Session, agent: Agent, visitor: str, reply: str = "") -> dict:
    goal = db.get(Goal, agent.goal_id)
    hi = _goal_hi(goal)
    return public({"reply": reply, "relation": "agent_run", "goal": goal.as_dict() if goal else None,
                   "goals": [g.summary() for g in all_goals(db, visitor)],
                   "agents": [agent_dict(a, db, hi) for a in goal_agents(db, agent.goal_id)]})


def start_agent(agent_id: int, body: StartIn, db: Session, visitor: str):
    agent = own_agent(db, agent_id, visitor)
    goal = db.get(Goal, agent.goal_id)
    hi = _goal_hi(goal)
    inputs = loads(agent.inputs, {}) or {}
    for key, value in body.inputs.items():
        if key in ("url", "query", "explain") and isinstance(value, str) and value.strip():
            inputs[key] = value.strip()[:20000]
    agent.inputs = dumps(inputs)
    db.commit()
    try:
        run = run_agent(db, agent, hi)
    except AgentError as exc:
        if getattr(exc, "options", None):              # one choice first (e.g. which database): asked in the chat, with taps
            db.add(Message(visitor_id=visitor, goal_id=agent.goal_id, role="assistant", text=str(exc), relation="agent_plan", provider="agent"))
            db.commit()
            return public({"reply": str(exc), "options": exc.options, "relation": "agent_plan",
                           "goal": goal.as_dict() if goal else None, "goals": [g.summary() for g in all_goals(db, visitor)],
                           "agents": [agent_dict(a, db, hi) for a in goal_agents(db, agent.goal_id)]})
        return JSONResponse({"error": str(exc)}, status_code=400)
    reply, options = _after_run(agent, run, hi)
    db.add(Message(visitor_id=visitor, goal_id=agent.goal_id, role="assistant", text=reply, relation="agent_run", provider="agent"))
    db.commit()
    return public({"reply": reply, "options": options, "relation": "agent_run",
                   "goal": goal.as_dict() if goal else None,
                   "goals": [g.summary() for g in all_goals(db, visitor)],
                   "agents": [agent_dict(a, db, hi) for a in goal_agents(db, agent.goal_id)]})


def preview_agent(agent_id: int, body: StartIn, db: Session, visitor: str):
    agent = own_agent(db, agent_id, visitor)
    if agent.recipe != "automation":
        raise HTTPException(400, "Preview is for automation agents")
    inputs = loads(agent.inputs, {}) or {}
    if "sheet_url" in body.inputs:
        inputs["sheet_url"] = (body.inputs["sheet_url"] or "").strip()[:2000]
    if "csv_text" in body.inputs:
        privacy.set_rows(inputs, (body.inputs["csv_text"] or "").strip()[:200000])   # stored encrypted
    agent.inputs = dumps(inputs)
    db.commit()
    hi = _goal_hi(db.get(Goal, agent.goal_id))
    run = automation.run(db, agent, hi, preview=True)
    reply = run.summary or ""
    db.add(Message(visitor_id=visitor, goal_id=agent.goal_id, role="assistant", text=reply, relation="agent_run", provider="agent"))
    db.commit()
    return _agent_payload(db, agent, visitor, reply)


def activate_agent(agent_id: int, db: Session, visitor: str):
    agent = own_agent(db, agent_id, visitor)
    if agent.recipe != "automation":
        raise HTTPException(400, "Only automation agents run on a schedule")
    try:
        automation.activate(db, agent)
    except automation.AutomationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    goal = db.get(Goal, agent.goal_id)
    hi = _goal_hi(goal)
    spec = automation.spec_of(agent)
    when = automation.schedule_text(spec["schedule"], hi)
    reply = (f"Agent chalu ho gaya. {when} apne aap chalega. Har run ke baad aapko summary email aayegi." if hi
             else f"Agent is on. It runs {when.lower()} by itself, and emails you a summary after each run.")
    db.add(Message(visitor_id=visitor, goal_id=agent.goal_id, role="assistant", text=reply, relation="agent_run", provider="agent"))
    db.commit()
    return _agent_payload(db, agent, visitor, reply)


def owner_code(body: OwnerIn, db: Session, visitor: str, mailer):
    try:
        automation.request_code(db, visitor, body.email, body.business, mailer(), hi=getattr(body, "lang", "en") != "en")
    except automation.AutomationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    return {"ok": True}


def owner_verify(body: OwnerIn, db: Session, visitor: str):
    try:
        owner = automation.verify_code(db, visitor, body.code)
    except automation.AutomationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True, "email": owner.email}


def my_agents(db: Session, visitor: str) -> dict:
    """My Agents: every agent this visitor has, newest first."""
    rows = list(db.scalars(select(Agent).where(Agent.visitor_id == visitor).order_by(Agent.id.desc()).limit(50)))
    out = []
    for agent in rows:
        goal = db.get(Goal, agent.goal_id)
        item = agent_dict(agent, db, _goal_hi(goal))
        item["goal_id"], item["goal_title"] = agent.goal_id, goal.title if goal else ""
        out.append(item)
    return {"agents": out}


def link_connector(agent_id: int, connector_id: int, db: Session, visitor: str):
    agent = own_agent(db, agent_id, visitor)
    try:
        connector = dbagent.own_connector(db, connector_id, visitor)
    except dbagent.DatabaseError:
        raise HTTPException(404, "Connector not found")
    inputs = loads(agent.inputs, {}) or {}
    inputs["connector_id"] = connector.id
    agent.inputs = dumps(inputs)
    db.commit()
    return _agent_payload(db, agent, visitor)


def pause_agent(agent_id: int, db: Session, visitor: str):
    agent = own_agent(db, agent_id, visitor)
    agent.status, agent.next_run_at = "paused", None
    db.commit()
    goal = db.get(Goal, agent.goal_id)
    return {"agents": [agent_dict(a, db, _goal_hi(goal)) for a in goal_agents(db, agent.goal_id)]}
