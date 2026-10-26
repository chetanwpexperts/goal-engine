"""Reasoning from evidence, for every agent. It knows causes, signs and facts, never a field: each agent gives it
its own knowledge map (causes -> which complaints they explain -> which fact shows them) and the facts it checked.

think():  which causes are proven by the facts, which were checked and ruled out, which could not be checked yet,
          and the one check that would tell the most. A cause is only ever "found" when its fact was seen: the
          reasoning never states a guess as the answer.
learned(): the prior of a cause, moved by real outcomes (fixed it / didn't).
"""

SEEN_LR = 30.0          # how much a seen fact raises a cause
ABSENT_LR = 0.03        # how much a checked-and-absent fact lowers it
NO_COMPLAINT = 0.3      # nothing specific was described: every cause is equally worth a look
FOUND_MIN = 0.35        # weaker links than this are "also in the file", never "the cause"


def learned(prior: float, worked: int = 0, failed: int = 0) -> float:
    """Outcomes move the prior (a Beta update, the prior counted as 10 earlier cases)."""
    a, b = prior * 10 + worked, (1 - prior) * 10 + failed
    return a / (a + b)


def _relevance(cause: dict, complaints: dict[str, float]) -> float:
    if not complaints:
        return NO_COMPLAINT
    ex = cause.get("explains", {})
    return max((ex.get(c, 0.0) * w for c, w in complaints.items()), default=0.0)


def think(knowledge: dict, complaints: dict[str, float], facts: dict[str, dict | None],
          outcomes: dict[str, tuple[int, int]] | None = None) -> dict:
    """complaints: what the person described -> how sure we are they said it (0..1).
    facts: fact id -> {"seen": bool, ...details} once checked, or None when it could not be checked."""
    outcomes = outcomes or {}
    found, also, open_, ruled_out = [], [], [], []
    for cid, cause in knowledge.get("causes", {}).items():
        rel = _relevance(cause, complaints)
        prior = learned(cause.get("prior", 0.1), *outcomes.get(cid, (0, 0)))
        fact = facts.get(cause.get("shown_by", cid))
        odds = prior / (1 - prior) * (1 + 9 * rel)
        if fact is None:
            state = "unknown"
        elif fact.get("seen"):
            state, odds = "seen", odds * SEEN_LR * (1 + min(fact.get("weight", 1.0), 5.0) / 5)
        else:
            state, odds = "absent", odds * ABSENT_LR
        item = {"cause": cid, "relevance": round(rel, 3), "score": odds / (1 + odds), "fact": fact,
                "state": state}
        if state == "seen" and rel >= FOUND_MIN:
            found.append(item)
        elif state == "seen":
            also.append(item)
        elif state == "unknown" and rel > 0.05:
            open_.append(item)
        elif state == "absent" and rel > 0.05:
            ruled_out.append(item)
    for lst in (found, also, open_, ruled_out):
        lst.sort(key=lambda x: (-x["score"], x["cause"]))
    # the next check: the unchecked cause that would explain the most, if it turned out to be there
    nxt = open_[0]["cause"] if open_ else None
    return {"found": found, "also": also, "open": open_, "ruled_out": ruled_out, "next_check": nxt,
            "sure": bool(found)}
