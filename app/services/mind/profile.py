"""Coordinate working style persistence and rules."""
from app.models import utcnow
from app.data.mind import profile as storage
from app.engine.mind import profile as rules
from app.engine.mind.profile import STYLE_KEYS, STYLE_TTL, SESSION_TTL, HISTORY, update, add_pattern
from app.data.mind.profile import delete_visitor, style_of


def load(db, goal_id, visitor_id):
    session = storage.load_session(db, goal_id)
    return rules.restore(session, None if session is not None else storage.load_style(db, visitor_id))


def save(db, goal_id, visitor_id, p):
    storage.save(db, goal_id, visitor_id, p, rules.merge_style)


def maintain(db, now=None):
    return storage.maintain(db, now or utcnow(), session_ttl=SESSION_TTL, style_ttl=STYLE_TTL)
