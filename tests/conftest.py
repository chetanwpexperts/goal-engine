import json
import os
import tempfile

import pytest

_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""
os.environ["VISITOR_DAILY_CAP"] = "5"
os.environ["AI_DAILY_CAP"] = "8"
os.environ["ENGINE_MODE"] = "ai"   # AI-path tests; ML tests switch it on per test
os.environ["AGENT_SCHEDULER"] = "off"
os.environ["EXPOSE_INTERNALS"] = "1"   # tests read how a message was understood; the live site never sends it
os.environ["DB_AREA_LOCK"] = "off"   # older tests connect without an account; test_dbarea switches it on
os.environ["SITE_MODE"] = "open"   # the coming-soon gate has its own test

from fastapi.testclient import TestClient  # noqa: E402

from app.ai.router import AIRouter  # noqa: E402
from app.db import Base, get_engine, init_db  # noqa: E402
from app.main import app  # noqa: E402


class FakeProvider:
    """Returns scripted answers; records the prompts it was given."""

    def __init__(self, name="fake", answers=None):
        self.name = name
        self.answers = list(answers or [])
        self.prompts = []

    def complete_json(self, system, user):
        self.prompts.append(user)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


def interpretation(**overrides):
    base = {
        "relation": "new_goal",
        "goal_ref": None,
        "detected_language": "Hinglish",
        "title": "Get more customers",
        "interpreted_problem": "Website exists but brings few customers.",
        "desired_goal": "More customer enquiries",
        "known_facts": ["Has a website"],
        "missing_info": [{"question": "What does your business sell?", "why": "Decides where customers come from"}],
        "constraints": [],
        "sub_goals": [],
        "confidence": 0.6,
        "next_action": {"type": "ask", "description": "Ask what the business sells"},
        "reply": "Samajh gaya. Aapka business kya bechta hai?",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def fresh_db():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture()
def client(fresh_db):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def use_provider():
    def _set(*providers):
        app.state.router = AIRouter(list(providers))
        return providers[0] if len(providers) == 1 else providers
    return _set


@pytest.fixture()
def ml_mode():
    from app.config import get_settings

    settings = get_settings()
    old_mode, old_cap, old_all = settings.engine_mode, settings.visitor_daily_cap, settings.ai_daily_cap
    settings.engine_mode, settings.visitor_daily_cap, settings.ai_daily_cap = "ml", 100, 1000
    yield
    settings.engine_mode, settings.visitor_daily_cap, settings.ai_daily_cap = old_mode, old_cap, old_all


# SQLite ignores VARCHAR lengths; MySQL refuses. Fail any test that saves a text longer than its column,
# so a too-short column is caught here and not on the live server (the email-code bug, 30 Oct 2026).
from sqlalchemy import String, event  # noqa: E402
from sqlalchemy.orm import Session as _S  # noqa: E402


@event.listens_for(_S, "before_flush")
def _check_lengths(session, _ctx, _instances):
    for obj in list(session.new) + list(session.dirty):
        table = getattr(obj, "__table__", None)
        if table is None:
            continue
        for col in table.columns:
            limit = getattr(col.type, "length", None)
            if isinstance(col.type, String) and limit and type(col.type).__name__ in ("String", "VARCHAR"):
                val = getattr(obj, col.key, None)
                if isinstance(val, str) and len(val) > limit:
                    raise AssertionError(f"{table.name}.{col.name} is VARCHAR({limit}) but got {len(val)} characters")
