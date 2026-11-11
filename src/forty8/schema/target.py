"""Loads the configurable target schema (OED by default)."""

from __future__ import annotations

import re
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from forty8.reference.gazetteer import fold

SCHEMA_DIR = Path(__file__).parent


class TargetField(BaseModel):
    canonical: str
    name: str
    required: bool = False
    synonyms: list[str] = Field(default_factory=list)


class TargetSchema(BaseModel):
    format: str
    fields: list[TargetField]

    @property
    def required(self) -> list[str]:
        return [f.canonical for f in self.fields if f.required]

    @property
    def canonical_names(self) -> list[str]:
        return [f.canonical for f in self.fields]

    def synonym_index(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for f in self.fields:
            for term in (f.canonical, f.name, *f.synonyms):
                index.setdefault(normalise_header(term), f.canonical)
        return index

    def resolve(self, name: str) -> str | None:
        """Accept a canonical name or a target-format name, case-insensitively."""
        wanted = str(name).strip().lower()
        for f in self.fields:
            if wanted in (f.canonical.lower(), f.name.lower()):
                return f.canonical
        return None


def normalise_header(header: str) -> str:
    """Lower-case, strip accents, drop bracketed text such as units and notes."""
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", fold(header))
    return " ".join(text.split()).strip(" :")


def header_unit(header: str) -> str | None:
    folded = fold(header)
    if re.search(r"sq\.?\s*ft|sqft|ft2|square feet|pies", folded):
        return "sqft"
    if re.search(r"\bm2\b|sq\.?\s*m|sqm|square met", folded):
        return "m2"
    return None


@lru_cache
def load_schema(target: str) -> TargetSchema:
    path = SCHEMA_DIR / f"{target}.toml"
    if not path.exists():
        available = sorted(p.stem for p in SCHEMA_DIR.glob("*.toml"))
        raise KeyError(f"no target schema '{target}' (available: {available})")
    with open(path, "rb") as fh:
        return TargetSchema.model_validate(tomllib.load(fh))
