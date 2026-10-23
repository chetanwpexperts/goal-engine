"""Which field agent takes a message. The brain doesn't know any field: each pack names its agent's hooks.

  hooks.holds(db, goal, text) -> agent | None        the agent holds this conversation (e.g. a file is open)
  hooks.continue(db, goal, agent, text) -> (reply, options, hi)
  hooks.wants(db, goal, text, relation, held) -> bool   this message is for the agent
  hooks.take(db, goal, text, turn, visitor_id) -> (reply, options, relation)
Packs are asked in priority order; the first that wants it takes it."""

from app.mind import packs


def holder(db, goal, text: str, any_text: bool = False):
    """(pack name, agent) of the agent holding this conversation, or None. any_text: ignore what was typed."""
    for name in packs.all_packs():
        fn = packs.hook(name, "holds")
        if fn is None:
            continue
        agent = fn(db, goal, "" if any_text else text, any_text)
        if agent is not None:
            return name, agent
    return None


def continue_turn(name: str, db, goal, agent, text: str):
    return packs.hook(name, "continue")(db, goal, agent, text)


def taker(db, goal, text: str, relation: str, held: bool) -> str | None:
    for name in packs.all_packs():
        wants = packs.hook(name, "wants")
        if wants is not None and packs.hook(name, "take") is not None and wants(db, goal, text, relation, held):
            return name
    return None


def take(name: str, db, goal, text: str, turn, visitor_id: str):
    return packs.hook(name, "take")(db, goal, text, turn, visitor_id)
