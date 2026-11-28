"""Record learning requests and missed answers from product agents."""
from app.core.redact import scrub, scrub_detail
from app.core.text import normalize, fingerprint
from app.engine.learning import BRAINS
from app.data import learning as storage


def ask_to_learn(db, brain, text, guess="", confidence=0.0):
    if brain not in BRAINS:
        raise ValueError("unknown brain")
    clean = scrub(text)
    if len(normalize(clean)) < 3:
        raise ValueError("too short")
    storage.record_request(db, brain, fingerprint(brain, clean), clean, guess, confidence)


def report_miss(db, brain, said, detail, answer=""):
    if brain not in BRAINS:
        raise ValueError("unknown brain")
    d = scrub_detail(detail)
    if len(normalize(d)) < 3:
        raise ValueError("too short")
    storage.record_miss(db, brain, fingerprint(brain, d), scrub(said), d, scrub(answer))
