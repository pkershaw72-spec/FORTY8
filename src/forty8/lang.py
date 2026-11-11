"""Lightweight, deterministic language detection for headers and slip text."""

from __future__ import annotations

import re

from forty8.reference.gazetteer import fold

_WORDS = {
    "en": "the of and to in for address city street value building contents year built total insured limit "
          "deductible period from any each country state postal occupancy construction currency name site",
    "es": "de del la el los las y en para direccion ciudad calle valor edificio contenidos ano construccion "
          "suma asegurada asegurado limite deducible vigencia pais estado codigo ocupacion giro moneda nombre sitio",
    "de": "der die das und von fur adresse stadt strasse wert gebaude inhalt baujahr gesamt versicherungssumme "
          "land bundesland plz nutzung bauart wahrung standort standortname geschosse flache",
    "fr": "le les des et du pour adresse ville rue valeur batiment contenu annee somme assuree assure limite "
          "franchise pays code postal usage devise nom",
}
VOCAB = {lang: set(words.split()) for lang, words in _WORDS.items()}
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def detect_language(text: str) -> tuple[str, float]:
    """Returns (ISO 639-1 code or 'unknown', confidence 0-1)."""
    if not text or not text.strip():
        return "unknown", 0.0
    if len(_JAPANESE.findall(text)) >= 3:
        return "ja", 0.95
    tokens = re.findall(r"[a-z]+", fold(text))
    hits = {lang: sum(1 for t in tokens if t in vocab) for lang, vocab in VOCAB.items()}
    best = max(hits, key=hits.get)
    total = sum(hits.values())
    if hits[best] < 2:
        return "unknown", 0.0
    return best, round(hits[best] / total, 2)
