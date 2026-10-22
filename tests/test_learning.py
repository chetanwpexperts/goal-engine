"""Every brain upgrades itself from users: 3 different people agreeing teaches it; nothing personal is kept."""

from sqlalchemy import select

from app import learning
from app.dbknow import brain
from app.models import LearnedPhrase, LearnRequest, LearnVote

PHRASE = "mera data ulta pulta ho gaya hai saare tables mein"


def _client_as(client, n):
    client.cookies.clear()
    client.cookies.set("ge_vid", f"visitor-{n}-0000000000000000")
    return client


def test_scrub_removes_personal_details():
    t = learning.scrub("error 1045 for user 'shop'@'10.0.0.5' password=Secret123 mail me a@b.com, site shop.example.com +91 98765 43210")
    assert "1045" in t
    for gone in ("Secret123", "a@b.com", "10.0.0.5", "98765", "example.com", "shop'"):
        assert gone not in t


def test_three_people_teach_the_database_brain(client):
    label = "issue:question-marks-instead-of-text"
    before = brain.recognize(PHRASE)
    for n in range(2):
        r = _client_as(client, n).post("/api/learn/choice", json={"brain": "database", "text": PHRASE, "label": label})
        assert r.status_code == 200 and r.json()["learned"] is False
    # the same person again doesn't count twice
    assert _client_as(client, 1).post("/api/learn/choice", json={"brain": "database", "text": PHRASE, "label": label}).json()["learned"] is False
    assert _client_as(client, 2).post("/api/learn/choice", json={"brain": "database", "text": PHRASE, "label": label}).json()["learned"] is True
    learning._version_cache.clear()
    after = brain.recognize(PHRASE)
    assert f"{after['kind']}:{after['id']}" == label
    assert after["confidence"] > before["confidence"] or f"{before['kind']}:{before['id']}" != label


def test_unknown_meaning_rejected(client):
    r = client.post("/api/learn/choice", json={"brain": "database", "text": PHRASE, "label": "issue:does-not-exist"})
    assert r.status_code == 400


def test_none_of_these_goes_to_learning_list_scrubbed(client, monkeypatch):
    for n in range(2):
        _client_as(client, n).post("/api/learn/ask", json={"brain": "database", "text": "mongodb atlas se connect nahi ho raha ali@x.com", "guess": "do:health"})
    from app.db import _SessionLocal

    with _SessionLocal() as db:
        row = db.scalar(select(LearnRequest).where(LearnRequest.text.like("mongodb atlas%")))
        assert row.count == 2 and "ali@x.com" not in row.text
        assert not hasattr(row, "visitor_id")
    assert client.get("/api/learning/feed?key=wrong").status_code == 404
    monkeypatch.setenv("LEARN_KEY", "k" * 20)
    feed = client.get("/api/learning/feed?key=" + "k" * 20).json()["requests"]
    assert any(f["text"].startswith("mongodb atlas") and f["asked"] == 2 for f in feed)


def test_rejected_phrase_is_not_learned(client):
    text = "sab kuch gayab ho gaya tables khaali"
    label = "do:health"
    for n in range(3):
        _client_as(client, 10 + n).post("/api/learn/choice", json={"brain": "database", "text": text, "label": label})
    from app.db import _SessionLocal

    with _SessionLocal() as db:
        p = db.scalar(select(LearnedPhrase).where(LearnedPhrase.text == text))
        assert learning.set_status(db, "phrase", p.id, "rejected")
    assert (text, label) not in learning.examples("database")


def test_delete_all_removes_votes(client):
    c = _client_as(client, 99)
    c.post("/api/learn/choice", json={"brain": "database", "text": "kuch bhi alag sa sawaal hai yeh", "label": "do:health"})
    from app.db import _SessionLocal
    from app.privacy import fingerprint

    voter = fingerprint("learn:visitor-99-0000000000000000")
    c.post("/api/privacy/delete-all")
    with _SessionLocal() as db:
        assert not db.scalar(select(LearnVote).where(LearnVote.voter == voter))


def test_general_brain_learns_too(client):
    from app.ml.intent import training_examples

    label = training_examples(include_learned=False)[1][0]
    text = "ek dum naya tareeka bolne ka jo kisi ne nahi socha"
    for n in range(3):
        _client_as(client, 30 + n).post("/api/learn/choice", json={"brain": "general", "text": text, "label": label})
    learning._version_cache.clear()
    texts, labels = training_examples()
    assert (text, label) in zip(texts, labels)
