"""Mock LLM task: free-text coding second opinion. Stands in for Claude recognising industry
abbreviations the local classifier has not seen. Anything else comes back unclassified."""

from __future__ import annotations

import json
import re
from typing import Any

from forty8.connectors.mocks.llm import register_task
from forty8.reference.gazetteer import fold

ABBREVIATIONS = {
    "occupancy": {"dc": "warehouse", "whse": "warehouse", "hq": "office", "mfg": "light_manufacturing"},
    "construction": {"jm": "masonry", "urm": "masonry", "rc": "reinforced_concrete", "pemb": "light_metal"},
}


@register_task("free_text_coding")
def code_free_text(instructions: str, content: str) -> dict[str, Any]:
    request = json.loads(content)
    tokens = re.findall(r"[a-z]+", fold(request["text"]))
    for token in tokens:
        label = ABBREVIATIONS.get(request["field"], {}).get(token)
        if label in request["classes"]:
            return {"label": label, "confidence": 0.9, "rationale": f"'{token}' is a common abbreviation"}
    return {"label": None, "confidence": 0.1, "rationale": "not enough information in the text"}
