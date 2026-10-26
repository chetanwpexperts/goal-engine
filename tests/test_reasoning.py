"""The Excel agent reasons out WHY from the person's own file: exactly the causes that are there (alone or combined),
proven with cells and amounts, a fix that makes them go away, and an honest "checked, not there" otherwise."""

import tempfile
from pathlib import Path

import pytest

from app.mind import reason
from tests.fuzz import excel_causes as gen
from tests.test_follow_orders import _upload
from tests.test_ml import say


@pytest.mark.parametrize("seed", [3, 11])
def test_finds_exactly_the_causes_in_unseen_files(seed):
    ok, errs = gen.run(40, seed)
    assert not errs, "\n".join(errs[:10])


def test_never_states_a_cause_without_proof():
    k = {"causes": {"a": {"prior": 0.9, "explains": {"x": 1.0}}, "b": {"prior": 0.1, "explains": {"x": 1.0}}}}
    r = reason.think(k, {"x": 1.0}, {"a": None, "b": {"seen": False}})
    assert not r["found"] and r["next_check"] == "a" and [x["cause"] for x in r["ruled_out"]] == ["b"]
    r = reason.think(k, {"x": 1.0}, {"a": {"seen": False}, "b": {"seen": True}})
    assert [x["cause"] for x in r["found"]] == ["b"]
    assert reason.learned(0.5, worked=10) > 0.5 > reason.learned(0.5, failed=10)


def test_agent_explains_and_fixes_in_chat(client, ml_mode):
    folder = tempfile.mkdtemp()
    path = Path(folder) / "sales.xlsx"
    import random

    truth = gen.build(str(path), {"text_in_totals", "short_range"}, random.Random(5))
    r = say(client, "my total is wrong in excel")
    assert "Numbers saved as text inside your totals" in r["reply"], r["reply"]
    gid = r["goal"]["id"]
    agent = next(a for a in r["agents"] if a.get("office"))
    _upload(client, agent["id"], path.read_bytes(), "sales.xlsx")
    r = say(client, "done", gid)
    reply = r["reply"]
    assert "Found the causes" in reply and "saved as text" in reply and "range stops" in reply, reply
    short = truth["right_total"] - truth["shown_total"]
    assert "Proof" in reply and "Prevent" in reply
    r = say(client, "yes", gid)
    assert "Fixing" in r["reply"], r["reply"]
    assert short > 0


def test_fundamentals_are_complete_and_official():
    from app.officeknow import diagnose
    from app.officeknow.diagnose import NO

    k = diagnose.knowledge()
    for cid, c in k["causes"].items():
        assert c["source"].startswith(("https://support.microsoft.com/", "https://learn.microsoft.com/")), cid
        for key in ("title", "why", "fix", "confirm", "prevent"):
            assert c.get(f"{key}_en") and c.get(f"{key}_hi"), (cid, key)
        assert set(c["explains"]) <= set(k["complaints"]), cid
        assert cid in NO, cid
