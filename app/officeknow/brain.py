"""The Microsoft brain's ears: which Excel/Word/Office/Windows problem is this? No AI.
Trained on the knowledge files plus what users taught it (app/learning.py)."""

import hashlib
import re
import threading

from app import officeknow
from app.ml.normalize import normalize

CODE_RE = re.compile(r"#(?:REF!|N/A|VALUE!|DIV/0!|NAME\?|NUM!|NULL!|SPILL!|CALC!|FIELD!)|0x[0-9A-Fa-f]{8}", re.I)

# What the file tools do besides fixing a known problem
BUILTIN = {
    "compare": ["do sheets compare karo", "compare two excel files", "dono file mein kya fark hai", "data match karo do sheets ka",
                "which rows changed between old and new file", "purani aur nayi sheet ka difference"],
    "clean": ["data saaf karo", "clean my excel data", "extra spaces hatao", "messy data theek karo", "sheet ko clean kar do",
              "blank rows aur galat format hatao"],
    "check_formulas": ["saare formula check karo", "find errors in my sheet", "kaunse formula galat hain", "sheet mein error dhoondo",
                       "check my workbook for mistakes"],
    "explain": ["yeh sheet kya karti hai samjhao", "explain this excel file", "is file ka logic batao", "formula samjhao mujhe",
                "previous person ki sheet samajh nahi aa rahi"],
    "convert": ["pdf ko excel mein badlo", "convert csv to excel", "word ko pdf banao", "file format change karo", "xls ko xlsx karo"],
    "merge": ["saari files ek mein jodo", "merge multiple excel files", "sab sheets ek sheet mein", "combine workbooks"],
}

_lock = threading.Lock()
_model = None
_fingerprint = ""


def _examples():
    texts, labels = [], []
    for key, ex in BUILTIN.items():
        texts += ex
        labels += [f"do:{key}"] * len(ex)
    for issue in officeknow.load("issues"):
        for t in issue["symptoms"] + [issue["title_en"], issue["title_hi"]]:
            texts.append(t)
            labels.append(f"issue:{issue['id']}")
    for f in officeknow.load("formulas"):
        for t in f["examples"] + [f["title_en"], f["title_hi"]]:
            texts.append(t)
            labels.append(f"formula:{f['id']}")
    return texts, labels


def labels() -> set[str]:
    return set(_examples()[1])


def _training():
    from app import learning

    texts, labels_ = _examples()
    known = set(labels_)
    for text, label in learning.examples("office"):
        if label in known:
            texts.append(text)
            labels_.append(label)
    return texts, labels_


def _train():
    global _model, _fingerprint
    texts, labels_ = _training()
    fp = hashlib.sha256(repr((texts, labels_)).encode()).hexdigest()
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
    pipe.fit(texts, labels_)
    _model, _fingerprint = pipe, fp
    return pipe


def recognize(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {"kind": None, "id": None, "confidence": 0.0, "alternatives": []}
    codes = {c.upper() for c in CODE_RE.findall(text)}
    if codes:                                   # "#REF! aa raha hai": the error value says which problem it is
        for issue in officeknow.load("issues"):
            if codes & {c.upper() for c in issue.get("codes", [])}:
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


NAMES = {"compare": ("Do files/sheets compare karna", "Compare two files or sheets"), "clean": ("Data saaf karna", "Clean the data"),
         "check_formulas": ("Saare formula check karna", "Check all formulas"), "explain": ("Sheet samajhna", "Explain the sheet"),
         "convert": ("File format badalna", "Convert the file"), "merge": ("Files jodna", "Merge files")}


def describe(rec: dict, hi: bool) -> str:
    kind, ident = rec.get("kind"), rec.get("id")
    if kind == "issue":
        e = next((i for i in officeknow.load("issues") if i["id"] == ident), None)
    elif kind == "formula":
        e = next((i for i in officeknow.load("formulas") if i["id"] == ident), None)
    else:
        e = None
    if e:
        return e["title_hi" if hi else "title_en"]
    if kind == "do" and ident in NAMES:
        return NAMES[ident][0 if hi else 1]
    return ""
