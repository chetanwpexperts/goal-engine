"""Provider for conversation reasoning, registered at application startup."""
from app.data.mind.core import previous_texts, previous_reply, commit, record_step
from app.services.mind import predict, profile
from app.services.mind.profile import load, save
from app.services.mind.predict import scores


def record(db, brain, chain, label, visitor=None):
    record_step(db, predict.record, brain, chain, label, visitor)
