"""Joins the knowledge base into the engine: every problem type gets a spec (what to track)
and a playbook (what to answer). Also the general fallback for problems not learned yet."""

import re
from typing import Callable

from app.ml.answers import PLAYBOOKS
from app.ml.knowledge import ProblemType, all_types
from app.ml.normalize import normalize
from app.ml.slots import BUDGET, CITY, INTENT_LABELS, SINCE, SITE, SPECS, Slot, skill


TECH_TERMS = ["php", "laravel", "codeigniter", "mysql", "mariadb", "postgres", "postgresql", "mongodb", "redis",
              "wordpress", "woocommerce", "react", "angular", "vue", "node", "python", "django", "flask", "fastapi",
              "java", "spring", "android", "flutter", "ios", "swift", "aws", "azure", "gcp", "docker", "kubernetes",
              "nginx", "apache", "linux", "windows", "excel", "sql", "api", "javascript", "typescript", "shopify"]


def tech_terms(text: str) -> str | None:
    found = [t for t in TECH_TERMS if re.search(rf"\b{re.escape(t)}\b", text)]
    return ", ".join(found[:4]) if found else None


GENERIC_SLOTS = [Slot("tech", "Technology", tech_terms), Slot("skill", "Skills", skill), CITY, SINCE, BUDGET, SITE]

CAUTION_EN = "General guidance only. For your exact situation, check with a qualified professional."
CAUTION_HI = "Ye general salah hai. Apni sahi situation ke liye kisi qualified professional se zaroor poochein."

KNOWLEDGE: dict[str, ProblemType] = {t.key: t for t in all_types()}


def _playbook(t: ProblemType) -> Callable[[dict, bool], tuple[list[str], str]]:
    def run(_slots: dict, hi: bool) -> tuple[list[str], str]:
        steps = list(t.steps_hi if hi else t.steps_en)
        nxt = (t.next_hi if hi else t.next_en) or ("Agla step: pehla point aaj shuru karo." if hi else "Next step: start with the first point today.")
        if t.caution:
            nxt += "\n\n" + (CAUTION_HI if hi else CAUTION_EN)
        return steps, nxt
    return run


def _general(_slots: dict, hi: bool) -> tuple[list[str], str]:
    if hi:
        return (["Problem ek line mein likho: kya ho raha hai aur kya hona chahiye.",
                 "Kab se ho raha hai aur us samay kya badla tha, ye note karo.",
                 "Ab tak kya try kiya aur uska kya result aaya, likho.",
                 "Sabse chhota aur safe upaay pehle karo, phir dekho fark pada ya nahi."],
                "Thodi aur detail likhoge to main behtar samajh paunga.")
    return (["Write the problem in one line: what's happening vs what should happen.",
             "Note when it started and what changed around then.",
             "List what you've tried so far and what happened.",
             "Try the smallest, safest fix first, then check if it helped."],
            "A little more detail will help me understand it better.")


def install() -> None:
    """Register knowledge types and the general fallback alongside the core types."""
    for t in KNOWLEDGE.values():
        if t.key in SPECS:
            continue
        SPECS[t.key] = {"title": t.title, "problem": t.problem, "goal": t.goal, "slots": GENERIC_SLOTS,
                        "knowledge": True, "domain": t.domain}
        PLAYBOOKS[t.key] = _playbook(t)
        INTENT_LABELS.setdefault(t.key, t.title)
    SPECS.setdefault("general", {"title": "Solve this problem", "problem": "A problem the engine hasn't learned yet.",
                                 "goal": "A clear plan for this problem", "slots": GENERIC_SLOTS, "general": True})
    PLAYBOOKS.setdefault("general", _general)


def short_title(text: str) -> str:
    words = [w for w in normalize(text).split() if len(w) > 2][:7]
    return ("Help with: " + " ".join(words))[:120] if words else "Help with a problem"


BUSINESS_DOMAIN = {k for k, t in KNOWLEDGE.items() if t.domain == "business"}

install()


def domain_of(intent: str) -> str:
    t = KNOWLEDGE.get(intent or "")
    return t.domain if t else ""
