"""Step 5 model: a small local text classifier (character n-gram TF-IDF + logistic regression)
trained on synthetic labelled phrases. Deterministic; trains in well under a second."""

from __future__ import annotations

from functools import lru_cache

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from forty8.reference.coding_rules import TRAINING
from forty8.reference.gazetteer import fold

MODEL_NAME = "tfidf_logreg_v1"


class TextClassifier:
    def __init__(self, training: dict[str, list[str]]):
        texts = [fold(p) for phrases in training.values() for p in phrases]
        labels = [label for label, phrases in training.items() for _ in phrases]
        self._pipeline = make_pipeline(
            TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True),
            LogisticRegression(C=40.0, max_iter=2000, random_state=48),
        )
        self._pipeline.fit(texts, labels)

    def predict(self, text: str) -> list[tuple[str, float]]:
        """Classes with probabilities, most likely first."""
        probs = self._pipeline.predict_proba([fold(text)])[0]
        ranked = sorted(zip(self._pipeline.classes_, probs), key=lambda pair: -pair[1])
        return [(str(label), float(prob)) for label, prob in ranked]


@lru_cache
def get_classifier(field: str) -> TextClassifier:
    return TextClassifier(TRAINING[field])
