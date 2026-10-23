"""Agent knowledge packs: everything an agent knows about its own field lives in app/knowledge/agents/<name>.json.

The brain has no field words of its own. It reads, from each pack:
  recipes       which agents belong to it
  words         the words that mean "this is about my field" (routing), e.g. {"strong": [...], "about": [...]}
  expert_words  words only practitioners use (a sign the person knows the field)
  chains        starting chains: P(next question pattern | last pattern), before real sessions exist
  first         the pattern a conversation starts from
  next          next-step questions per pattern, with "needs" (states the agent must be in)
  until         {state: pattern}: while that state is missing, that pattern is the only next step
  templates     (optional) question templates the agent's own suggester fills
  patterns      ways people ask each pattern, for recognising and for "the closest thing I can do"
  hooks         {"suggest": "module:function", "state": "module:function"} for the agent's own logic

A new agent = a new pack file (+ its worker code). No change to the brain."""

import importlib
import json
import re
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / "knowledge" / "agents"


@lru_cache(maxsize=1)
def all_packs() -> dict[str, dict]:
    packs = {}
    for p in sorted(DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        packs[data["name"]] = data
    return dict(sorted(packs.items(), key=lambda kv: kv[1].get("priority", 99)))


def get(name: str) -> dict:
    return all_packs().get(name, {})


def brain_for_recipe(recipe: str) -> str | None:
    for name, pack in all_packs().items():
        if recipe in pack.get("recipes", []):
            return name
    return None


def _word_rx(words: list[str]) -> re.Pattern | None:
    if not words:
        return None
    parts = []
    for w in sorted(set(words), key=len, reverse=True):
        e = re.escape(w).replace(r"\ ", r"\s+")
        if re.match(r"^[\w ]+$", w, re.UNICODE) and w.isascii():
            parts.append(rf"\b{e}\b")
        else:
            parts.append(e)                          # '#REF!', Hindi script, 'b-tree': no word edges
    return re.compile("|".join(parts), re.I)


@lru_cache(maxsize=64)
def words(name: str, kind: str = "about") -> re.Pattern | None:
    """The compiled regex for a pack's word list."""
    return _word_rx(get(name).get("words", {}).get(kind, []))


def mentions(name: str, text: str, kind: str = "about") -> bool:
    rx = words(name, kind)
    return bool(rx and rx.search(text or ""))


@lru_cache(maxsize=1)
def expert_rx() -> re.Pattern | None:
    allw = [w for p in all_packs().values() for w in p.get("expert_words", [])]
    return _word_rx(allw)


def chains(name: str) -> dict:
    return get(name).get("chains", {})


def hook(name: str, which: str):
    target = get(name).get("hooks", {}).get(which)
    if not target:
        return None
    mod, fn = target.split(":")
    return getattr(importlib.import_module(mod), fn)


@lru_cache(maxsize=8)
def phrase_index(name: str):
    """TF-IDF over a pack's phrasings (character n-grams, so spelling slips and Hinglish still match)."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    labels, phrases = [], []
    for label, items in get(name).get("patterns", {}).items():
        labels += [label] * len(items)
        phrases += [p.lower() for p in items]
    if not phrases:
        return None
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
    return vec, vec.fit_transform(phrases), labels


def nearest(name: str, text: str, k: int = 3, min_score: float = 0.22) -> list[tuple[str, float]]:
    idx = phrase_index(name)
    if idx is None:
        return []
    vec, mat, labels = idx
    sims = (mat @ vec.transform([(text or "").lower()]).T).toarray().ravel()
    best: dict[str, float] = {}
    for i in sims.argsort()[::-1][:60]:
        if sims[i] < min_score:
            break
        best.setdefault(labels[i], float(sims[i]))
    return sorted(best.items(), key=lambda x: -x[1])[:k]


def label_from_words(name: str, text: str, min_score: float = 0.25) -> str:
    near = nearest(name, text, k=1, min_score=min_score)
    return near[0][0] if near else "other"
