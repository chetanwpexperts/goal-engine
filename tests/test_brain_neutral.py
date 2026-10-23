"""The brain knows people and question patterns, not technologies. Field knowledge lives in the agents' packs
(app/knowledge/agents/*.json), so new agents can be added later without touching the brain."""

import json
import re
import shutil
from pathlib import Path

import pytest

from app.mind import packs
from tests.test_ml import say

ROOT = Path(__file__).resolve().parents[1] / "app"
BRAIN_FILES = sorted((ROOT / "mind").glob("*.py")) + [ROOT / "engine.py", ROOT / "learning.py"]
TECH = re.compile(r"\b(wordpress|php|mysql|mariadb|postgres\w*|excel|xlsx|xls|csv|laravel|shopify|codeigniter|sql|vlookup|xlookup|"
                  r"database|spreadsheets?|sheets?|nginx|apache|htaccess|innodb|google|woocommerce|magento|oracle|mongodb)\b", re.I)


@pytest.mark.parametrize("path", BRAIN_FILES, ids=lambda p: p.name)
def test_no_technology_words_in_the_brain(path):
    hits = sorted({m.group(0).lower() for m in TECH.finditer(path.read_text(encoding="utf-8"))})
    assert not hits, f"{path.name} names technologies {hits}: put them in an agent pack instead"


def test_every_pack_is_complete():
    for name, p in packs.all_packs().items():
        assert p.get("recipes") and p.get("chains") and p.get("patterns"), name
        for which in p.get("hooks", {}):
            assert packs.hook(name, which) is not None, (name, which)


# a brand-new field agent, defined only by a pack + its own code: the brain picks it up
GARDEN_HITS = []


def garden_wants(db, goal, text, relation, held):
    return relation not in ("safety",) and bool(packs.mentions("garden", text))


def garden_take(db, goal, text, turn, visitor_id):
    GARDEN_HITS.append(text)
    return "Garden agent here: I'll check your plants.", [], "agent_plan"


@pytest.fixture()
def garden_pack(tmp_path, monkeypatch):
    d = tmp_path / "agents"
    shutil.copytree(packs.DIR, d)
    (d / "garden.json").write_text(json.dumps({
        "name": "garden", "priority": 0, "recipes": ["garden"], "first": "g_start",
        "words": {"about": ["tomato plant", "lawn", "fertiliser"]},
        "chains": {"g_start": {"g_water": 1.0}}, "patterns": {"g_water": ["water the plants", "paani dena"]},
        "hooks": {"wants": "tests.test_brain_neutral:garden_wants", "take": "tests.test_brain_neutral:garden_take"},
    }), encoding="utf-8")
    monkeypatch.setattr(packs, "DIR", d)
    for fn in (packs.all_packs, packs.words, packs.expert_rx, packs.phrase_index):
        fn.cache_clear()
    yield
    for fn in (packs.all_packs, packs.words, packs.expert_rx, packs.phrase_index):
        fn.cache_clear()


def test_a_new_agent_joins_without_changing_the_brain(client, ml_mode, garden_pack):
    from app import learning

    r = say(client, "my tomato plant leaves are turning yellow, help")
    assert r["reply"].startswith("Garden agent here"), r["reply"]
    assert GARDEN_HITS and "garden" in learning.BRAINS
