"""Problem-type classifier: TF-IDF (characters + words) + logistic regression.

Character n-grams cope with Hinglish spelling chaos ("chal rha", "chl raha");
word n-grams catch meaning ("not opening", "nahi aate"). Trains in about a second.
"""

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline

from app.ml.normalize import normalize
from app.ml.seed_data import SEED


logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
MODEL_PATH = DATA_DIR / "models" / "intent.joblib"
LEARNED_PATH = DATA_DIR / "learned.jsonl"   # confirmed real examples, added by the learning loop


@dataclass(slots=True)
class Prediction:
    intent: str
    confidence: float
    ranking: list[tuple[str, float]]


def training_examples(include_learned: bool = True) -> tuple[list[str], list[str]]:
    from app.ml.knowledge import all_types

    texts, labels = [], []
    for intent, examples in SEED.items():
        for text in examples:
            texts.append(text)
            labels.append(intent)
    from app.ml.knowledge.extra_examples import EXTRA

    for problem in all_types():
        for text in (*problem.examples, problem.title, *EXTRA.get(problem.key, [])):  # title = tap-button text
            texts.append(text)
            labels.append(problem.key)
    for intent in SEED:
        for text in EXTRA.get(intent, []):
            texts.append(text)
            labels.append(intent)
    from app.ml.slots import INTENT_LABELS

    for intent, label in INTENT_LABELS.items():  # tap-button texts of the core types
        if intent in SEED:
            texts.append(label)
            labels.append(intent)
    known = set(labels)
    if include_learned:
        from app import learning

        for text, label in learning.examples("general"):
            if label in known:
                texts.append(text)
                labels.append(label)
    if include_learned and LEARNED_PATH.exists():
        for line in LEARNED_PATH.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("intent") in known and row.get("text"):
                texts.append(row["text"])
                labels.append(row["intent"])
    return texts, labels


def _fingerprint(texts: list[str], labels: list[str]) -> str:
    digest = hashlib.sha256()
    for text, label in zip(texts, labels):
        digest.update(f"{label}\t{text}\n".encode())
    return digest.hexdigest()


def build_pipeline() -> Pipeline:
    features = FeatureUnion([
        ("chars", TfidfVectorizer(preprocessor=normalize, analyzer="char_wb", ngram_range=(2, 5),
                                  sublinear_tf=True, min_df=1)),
        ("words", TfidfVectorizer(preprocessor=normalize, analyzer="word", ngram_range=(1, 2),
                                  sublinear_tf=True, token_pattern=r"(?u)\b\w+\b")),
    ])
    classifier = LogisticRegression(C=10.0, max_iter=3000, class_weight="balanced")
    return Pipeline([("features", features), ("clf", classifier)])


class IntentModel:
    def __init__(self, path: Path = MODEL_PATH):
        self.path = path
        self._pipeline: Pipeline | None = None
        self._lock = threading.Lock()
        self._fp = ""
        self._checked = 0.0

    def train(self) -> Pipeline:
        texts, labels = training_examples()
        pipeline = build_pipeline()
        pipeline.fit(texts, labels)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")  # each worker writes its own temp file
        joblib.dump({"fingerprint": _fingerprint(texts, labels), "pipeline": pipeline}, tmp)
        tmp.replace(self.path)
        logger.info("Intent model trained on %d examples", len(texts))
        return pipeline

    def load(self) -> Pipeline:
        with self._lock:
            if self._pipeline is not None:
                if time.monotonic() - self._checked < 120:
                    return self._pipeline
                self._checked = time.monotonic()   # users may have taught it something: retrain if so
                texts, labels = training_examples()
                if _fingerprint(texts, labels) == self._fp:
                    return self._pipeline
                self._pipeline = self.train()
                self._fp = _fingerprint(texts, labels)
                return self._pipeline
            self._checked = time.monotonic()
            texts, labels = training_examples()
            wanted = _fingerprint(texts, labels)
            self._fp = wanted
            if self.path.exists():
                try:
                    saved = joblib.load(self.path)
                    if saved.get("fingerprint") == wanted:
                        self._pipeline = saved["pipeline"]
                        return self._pipeline
                except Exception:  # noqa: BLE001 - corrupt or old file: retrain
                    logger.warning("Saved intent model unreadable, retraining")
            self._pipeline = self.train()
            return self._pipeline

    def reload(self) -> None:
        with self._lock:
            self._pipeline = None

    def predict(self, text: str) -> Prediction:
        pipeline = self.load()
        probabilities = pipeline.predict_proba([text])[0]
        ranking = sorted(zip(pipeline.classes_, probabilities), key=lambda kv: kv[1], reverse=True)
        ranking = [(str(intent), float(p)) for intent, p in ranking]
        return Prediction(intent=ranking[0][0], confidence=ranking[0][1], ranking=ranking)
