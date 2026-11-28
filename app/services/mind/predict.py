"""Coordinate pattern persistence and prediction rules."""
from app.privacy import fingerprint
from app.models import utcnow
from app.data.mind import predict as storage
from app.engine.mind import predict as rules
from app.engine.mind.predict import ANY, ALPHA, PERSONAL_ALPHA, DECAY, DROP_BELOW, ONE_OFF_AGE, PERSONAL_TTL, prior


def _who(visitor):
    return fingerprint(f"mind:{visitor}") if visitor else None


def record(db, brain, history, new, visitor=None):
    storage.record(db, brain, history, new, _who(visitor), any_pattern=ANY)


def scores(db, brain, history, visitor=None):
    if not history:
        return rules.combine(brain, history, {}, {}, {})
    p1 = history[-1]
    one = storage.counts(db, brain, ANY, p1)
    two = storage.counts(db, brain, history[-2], p1) if len(history) >= 2 else {}
    who = _who(visitor)
    mine = storage.personal_counts(db, who, brain, p1) if who else {}
    return rules.combine(brain, history, one, two, mine)


def decay(db, now=None):
    return storage.decay(db, now or utcnow(), factor=DECAY, drop_below=DROP_BELOW,
                         one_off_age=ONE_OFF_AGE, personal_ttl=PERSONAL_TTL, any_pattern=ANY)


def delete_visitor(db, visitor):
    return storage.delete_visitor(db, _who(visitor))


def top_paths(db, brain, limit=20):
    return storage.top_paths(db, brain, limit, any_pattern=ANY)
