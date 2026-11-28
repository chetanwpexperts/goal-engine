"""Learning rules and cached examples supplied by the application."""
import time
from app.core.redact import scrub
from app.core.text import fingerprint as _fp, normalize

def _brains() -> tuple[str, ...]:
    from app.mind import packs

    return tuple(packs.all_packs()) + ("general",)


class _Brains(tuple):
    """Every agent pack's brain plus 'general'; a new pack joins learning by itself."""

    def __contains__(self, item):
        return item in _brains()

    def __iter__(self):
        return iter(_brains())


BRAINS = _Brains()
VOTES_TO_LEARN = 3          # different people agreeing
AGREEMENT = 0.75            # share of the votes for that sentence
VOTES_PER_DAY = 30          # per person


def labels(brain: str) -> set[str]:
    if brain == "general":
        from app.engine.ml.intent import training_examples

        return set(training_examples(include_learned=False)[1])
    from app.mind import packs

    fn = packs.hook(brain, "labels")          # the agent's pack names where its meanings come from
    return set(fn()) if fn is not None else set()


def decide(votes: list[str]) -> str | None:
    if len(votes) < VOTES_TO_LEARN:
        return None
    top = max(set(votes), key=votes.count)
    if votes.count(top) < VOTES_TO_LEARN or votes.count(top) / len(votes) < AGREEMENT:
        return None
    return top


_version_cache: dict = {}
_source = lambda brain: []


def set_examples_source(fn):
    global _source
    _source = fn
    _version_cache.clear()


def examples(brain: str) -> list[tuple[str, str]]:
    hit = _version_cache.get(brain)
    if hit and time.monotonic() - hit[0] < 120:
        return hit[1]
    try:
        rows = _source(brain)
    except Exception:  # learning is a bonus; never break understanding
        rows = hit[1] if hit else []
    _version_cache[brain] = (time.monotonic(), rows)
    return rows
