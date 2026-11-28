"""Question pattern prediction rules."""
from datetime import timedelta
from app.engine.mind import packs

ANY = "*"
ALPHA = 3.0          # observations needed before the learned counts outweigh the layer below
PERSONAL_ALPHA = 2.0
DECAY = 0.97         # daily fade
DROP_BELOW = 0.05
ONE_OFF_AGE = timedelta(days=14)        # seen once and never again in two weeks: noise
PERSONAL_TTL = timedelta(days=180)


def prior(brain: str, last: str | None) -> dict[str, float]:
    chains = packs.chains(brain)
    return chains.get(last or "other") or chains.get("other", {})


def _blend(counts: dict[str, float], below: dict[str, float], alpha: float) -> dict[str, float]:
    n = sum(counts.values())
    keys = set(below) | set(counts)
    return {k: (counts.get(k, 0.0) + alpha * below.get(k, 0.0)) / (n + alpha) for k in keys}


def combine(brain, history, one, two, mine):
    p1 = history[-1] if history else None
    out = dict(prior(brain, p1))
    if not p1:
        return out
    out = _blend(one, out, ALPHA)
    if len(history) >= 2:
        out = _blend(two, out, ALPHA)
    if mine:
        out = _blend(mine, out, PERSONAL_ALPHA)
    return out
