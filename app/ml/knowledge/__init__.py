"""The knowledge base: real problem types people bring, grouped by area of life.

Each type has example messages (English, Hinglish, Hindi) the model learns from, and
concrete steps it answers with. Add a type or examples to any file; the model retrains
automatically on the next start.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProblemType:
    key: str
    domain: str
    title: str
    problem: str
    goal: str
    examples: tuple[str, ...]
    steps_en: tuple[str, ...]
    steps_hi: tuple[str, ...]
    next_en: str = ""
    next_hi: str = ""
    caution: str = ""          # e.g. "professional" for health, legal, money advice


def T(key, domain, title, problem, goal, ex, en, hi, next_en="", next_hi="", caution=""):
    return ProblemType(key, domain, title, problem, goal, tuple(ex), tuple(en), tuple(hi), next_en, next_hi, caution)


def all_types() -> list[ProblemType]:
    from app.ml.knowledge import business, career, life, money, tech

    types: list[ProblemType] = []
    for module in (business, tech, career, money, life):
        types.extend(module.TYPES)
    return types
