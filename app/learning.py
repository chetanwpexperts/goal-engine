"""Legacy learning API; implementation lives in the layered packages."""
from app.engine.learning import BRAINS, VOTES_TO_LEARN, AGREEMENT, VOTES_PER_DAY, labels, examples, scrub, _version_cache, _fp
from app.core.redact import _SCRUB
from datetime import timedelta

REQUEST_TTL = timedelta(days=180)
VOTE_TTL = timedelta(days=90)
