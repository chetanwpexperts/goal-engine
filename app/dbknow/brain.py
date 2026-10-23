"""The database brain's ears: what is this person's database problem? No AI.

It learns from the knowledge files themselves (playbook symptoms, question examples, error texts),
so every entry added by the nightly learning makes it understand more, with no code change.
"""

import hashlib
import re
import threading

from app import dbknow
from app.ml.normalize import normalize

SQL_RE = re.compile(r"\b(select\b[\s\S]+?\bfrom\b|update\s+[`\"\w.]+\s+set\b|delete\s+from\b|insert\s+into\b)", re.I)

# Built-in things the brain can do besides playbooks
BUILTIN = {
    "slow_query": ["query slow hai", "query bahut time le rahi hai", "optimize my query", "query fast karni hai",
                   "slow sql", "is query ko optimize karo", "query speed badhao", "query hang ho jaati hai",
                   "database bahut slow chal raha hai", "my database is very slow", "db slow ho gaya hai",
                   "database dhima ho gaya", "data load hone mein bahut time lagta hai", "report nikalne mein minute lag jaate hain"],
    "health": ["database ki health check karo", "database theek hai kya", "database check karo", "db health",
               "database mein kya problem hai", "check my database"],
    "understand": ["database samjhao", "is database ka structure batao", "kaunsi table kya karti hai", "explain my database",
                   "previous developer ne kt nahi diya", "database ka map dikhao", "tables ke beech relation batao"],
    "export": ["database se excel chahiye", "data download karna hai", "table export karo", "csv chahiye"],
}

_lock = threading.Lock()
_model = None
_fingerprint = ""


def _examples():
    texts, labels = [], []
    for key, ex in BUILTIN.items():
        texts += ex
        labels += [f"do:{key}"] * len(ex)
    for issue in dbknow.load("issues"):
        texts += issue["symptoms"]
        labels += [f"issue:{issue['id']}"] * len(issue["symptoms"])
    for q in dbknow.load("questions"):
        texts += q["examples"]
        labels += [f"question:{q['id']}"] * len(q["examples"])
    for e in dbknow.load("errors"):
        for t in (e["title_en"], e["title_hi"]):
            texts.append(t)
            labels.append(f"error:{e['id']}")
    return texts, labels


def _training():
    """Knowledge files + what real users taught it (app/learning.py)."""
    from app import learning

    texts, labels = _examples()
    known = set(labels)
    for text, label in learning.examples("database"):
        if label in known:
            texts.append(text)
            labels.append(label)
    return texts, labels


def _train():
    global _model, _fingerprint
    texts, labels = _training()
    fp = hashlib.sha256(repr((texts, labels)).encode()).hexdigest()
    if _model is not None and fp == _fingerprint:
        return _model
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline

    pipe = Pipeline([
        ("features", FeatureUnion([
            ("chars", TfidfVectorizer(preprocessor=normalize, analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True)),
            ("words", TfidfVectorizer(preprocessor=normalize, ngram_range=(1, 2), sublinear_tf=True)),
        ])),
        ("clf", LogisticRegression(C=10, max_iter=2000, class_weight="balanced")),
    ])
    pipe.fit(texts, labels)
    _model, _fingerprint = pipe, fp
    return pipe


def recognize(text: str, engine: str = "") -> dict:
    """{'kind': 'sql'|'error'|'issue'|'question'|'do'|None, 'id': ..., 'confidence': 0..1, 'alternatives': [...]}"""
    text = (text or "").strip()
    if not text:
        return {"kind": None, "id": None, "confidence": 0.0, "alternatives": []}
    if SQL_RE.search(text):
        return {"kind": "sql", "id": "slow_query", "confidence": 1.0, "alternatives": []}
    for e in dbknow.load("errors"):              # an error message pasted as-is: exact match beats guessing
        if (not engine or engine in e["engines"]) and re.search(e["match"], text, re.I | re.S):
            return {"kind": "error", "id": e["id"], "confidence": 1.0, "alternatives": []}
    codes = set(re.findall(r"\b(\d{4,5}|[0-9A-Z]{5})\b", text))
    if codes:                                     # "error 1040 aa raha hai": the code says which problem it is
        for issue in dbknow.load("issues"):
            if codes & set(issue.get("codes", [])):
                return {"kind": "issue", "id": issue["id"], "confidence": 1.0, "alternatives": []}
    with _lock:
        model = _train()
    probs = model.predict_proba([text])[0]
    ranked = sorted(zip(model.classes_, probs), key=lambda x: -x[1])
    label, conf = ranked[0]
    kind, _, ident = label.partition(":")
    return {"kind": kind, "id": ident, "confidence": round(float(conf), 3),
            "alternatives": [{"kind": l.partition(":")[0], "id": l.partition(":")[2], "confidence": round(float(p), 3)}
                             for l, p in ranked[1:4]]}


def describe(rec: dict, hi: bool) -> str:
    """One line: what the brain understood."""
    kind, ident = rec.get("kind"), rec.get("id")
    if kind == "issue":
        issue = next((i for i in dbknow.load("issues") if i["id"] == ident), None)
        if issue:
            return issue["title_hi" if hi else "title_en"]
    if kind == "error":
        e = next((x for x in dbknow.load("errors") if x["id"] == ident), None)
        if e:
            return e["title_hi" if hi else "title_en"]
    if kind == "sql":
        return "Query ko tez karna" if hi else "Make the query faster"
    names = {"slow_query": ("Slow query theek karna", "Fix a slow query"), "health": ("Database health check", "Database health check"),
             "understand": ("Database samajhna", "Understand the database"), "export": ("Data export", "Export data")}
    if kind == "do" and ident in names:
        return names[ident][0 if hi else 1]
    if kind == "question":
        return "Database se sawaal" if hi else "A question about your data"
    return ""


def labels() -> set[str]:
    """Everything this brain can mean (for the learning loop)."""
    return set(_examples()[1])
