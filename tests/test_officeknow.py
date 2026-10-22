"""Microsoft brain: knowledge files are valid, and it understands NEW phrasings in English, Hinglish and Hindi script."""

from app import learning, officeknow
from app.officeknow import brain


def test_knowledge_files_are_valid():
    assert officeknow.validate() == []
    assert officeknow.stats()["entries"] >= 6


def test_understands_new_phrasings_of_every_problem():
    misses = []
    for name, kind in (("issues", "issue"), ("formulas", "formula")):
        for e in officeknow.load(name):
            for t in e["test_phrases"]:
                r = brain.recognize(t)
                if (r["kind"], r["id"]) != (kind, e["id"]):
                    misses.append(f"{e['id']}: '{t}' -> {r['kind']}:{r['id']} ({r['confidence']})")
    assert not misses, "\n".join(misses)


def test_error_values_are_recognised_directly():
    assert brain.recognize("cell mein #REF! likha hai")["id"] == "excel-ref-error"
    assert brain.recognize("getting #N/A everywhere")["id"] == "vlookup-na-no-match"


def test_builtin_jobs():
    assert brain.recognize("purani aur nayi excel file mein kya badla")["id"] == "compare"
    assert brain.recognize("मेरी शीट का डेटा साफ़ कर दो")["id"] == "clean"


def test_users_can_teach_the_office_brain(client):
    text = "hisaab kitaab wali file ka jod galat aa raha"
    label = "issue:numbers-stored-as-text"
    for n in range(3):
        client.cookies.clear()
        client.cookies.set("ge_vid", f"office-{n}-0000000000000000")
        assert client.post("/api/learn/choice", json={"brain": "office", "text": text, "label": label}).status_code == 200
    learning._version_cache.clear()
    r = brain.recognize(text)
    assert f"{r['kind']}:{r['id']}" == label
