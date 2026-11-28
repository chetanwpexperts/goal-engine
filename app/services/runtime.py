"""Application lifecycle and scheduled maintenance."""
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app import accounts, console, dbarea, enquiry, privacy
from app.ai.router import AIRouter
from app.engine.ml.brain import Brain
from app.core.config import get_settings
from app.core.db import init_db
from app.products.shared import automation
from app.products.shared.core import AgentError, claim_due, run_agent
from app.domain.models import Goal, utcnow
from app.services.db_reports import _run_db_report

_last_maintenance = 0.0


def _scheduler(stop: threading.Event) -> None:
    """Runs daily agents when they're due. Safe with several workers (each run is claimed once)."""
    log = logging.getLogger("agent-scheduler")
    from app.core.db import get_db as _get_db

    while not stop.wait(60):
        try:
            db = next(_get_db())
            try:
                for agent in claim_due(db, utcnow()):
                    goal = db.get(Goal, agent.goal_id)
                    hi = bool(goal and goal.detected_language in ("Hinglish", "Hindi"))
                    try:
                        if agent.recipe == "automation":
                            automation.run(db, agent, hi, preview=False, scheduled=True)
                        elif agent.recipe == "db_report":
                            _run_db_report(db, agent)
                        else:
                            run_agent(db, agent, hi, scheduled=True)
                    except AgentError as exc:
                        log.warning("Scheduled agent %s: %s", agent.id, exc)
                global _last_maintenance
                if time.monotonic() - _last_maintenance > 600:
                    _last_maintenance = time.monotonic()
                    log.info("Privacy clean-up: %s", privacy.maintain(db))
                    log.info("Account clean-up: %s", accounts.maintain(db))
                    log.info("Database area: %s", dbarea.maintain(db))
                    log.info("SQL console connections ended: %s", console.maintain(db))
                    log.info("Old enquiries deleted: %s", enquiry.maintain(db))
                    from app.limits import prune_bursts

                    prune_bursts(db)
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - the scheduler must keep going
            log.exception("Scheduler tick failed")


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    from app.products import dbaba, sheetu  # register product capabilities
    from app.engine import learning as rules
    from app.data import learning as storage
    from app.services import learning as service
    from app.products.shared import learn
    # Legacy callers receive the same functions without an upward engine import.
    import sys

    legacy = sys.modules.get("app.learning")
    if legacy is None:
        import app.learning as legacy
    for name in ("vote", "set_status", "delete_voter", "overview", "feed", "maintain"):
        setattr(legacy, name, getattr(service, name))
    legacy.ask_to_learn = learn.ask_to_learn
    legacy.report_miss = learn.report_miss
    rules.set_examples_source(storage.load_examples)
    from app.engine.mind import core as mind_core
    from app.services.mind import store as mind_store

    mind_core.set_store(mind_store)
    if not hasattr(application.state, "router"):
        application.state.router = AIRouter.from_settings(get_settings())
    if not hasattr(application.state, "brain"):
        application.state.brain = Brain()
    application.state.brain.model.load()  # train or load the ML model before the first visitor
    try:                                  # and the problem recognisers, so the first visitor never waits for training
        from app.products.dbaba.dbknow import brain as _dbb
        from app.products.sheetu.officeknow import brain as _ofb

        _dbb._train()
        _ofb._train()
    except Exception:  # noqa: BLE001
        logging.getLogger("goal-engine").warning("Recognisers not warmed", exc_info=True)
    from app.products.sheetu import office_agent

    try:
        office_agent.resume_interrupted(utcnow())   # file work cut off by a restart carries on by itself
    except Exception:  # noqa: BLE001
        logging.getLogger("goal-engine").warning("Could not resume file work", exc_info=True)
    stop = threading.Event()
    if os.getenv("AGENT_SCHEDULER", "on") != "off":
        threading.Thread(target=_scheduler, args=(stop,), daemon=True, name="agent-scheduler").start()
    yield
    stop.set()
