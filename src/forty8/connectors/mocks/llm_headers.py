"""Mock LLM task: header mapping. Stands in for Claude reading headers in languages or
abbreviations the synonym table does not hold. Deterministic: a small multilingual lexicon
plus fuzzy matching against the target fields' synonyms."""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import Any

from forty8.connectors.mocks.llm import register_task
from forty8.schema.target import normalise_header

LEXICON = {  # German and French schedule headers
    "standort nr.": "LocNumber", "standort nr": "LocNumber", "standortname": "LocName", "adresse": "StreetAddress",
    "stadt": "City", "bundesland": "AreaCode", "plz": "PostalCode", "land": "CountryCode",
    "nutzung": "occupancy_text", "bauart": "construction_text", "baujahr": "YearBuilt",
    "geschosse": "NumberOfStoreys", "flache": "FloorArea", "gebaudewert": "BuildingTIV",
    "inhaltswert": "ContentsTIV", "bu-wert": "BITIV", "gesamtversicherungssumme": "stated_tiv",
    "wahrung": "LocCurrency", "ville": "City", "pays": "CountryCode", "code postal": "PostalCode",
    "valeur batiment": "BuildingTIV", "valeur contenu": "ContentsTIV", "annee de construction": "YearBuilt",
}


@register_task("header_mapping")
def map_headers(instructions: str, content: str) -> dict[str, Any]:
    request = json.loads(content)
    mappings = []
    for item in request["headers"]:
        key = normalise_header(item["header"])
        if key in LEXICON:
            mappings.append({"header": item["header"], "field": LEXICON[key], "confidence": 0.9})
            continue
        best_field, best = None, 0.0
        for target in request["fields"]:
            for synonym in target["synonyms"]:
                score = SequenceMatcher(None, key, normalise_header(synonym)).ratio()
                if score > best:
                    best_field, best = target["field"], score
        mappings.append({"header": item["header"], "field": best_field if best >= 0.6 else None,
                         "confidence": round(best, 2)})
    return {"mappings": mappings}
