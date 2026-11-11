"""Canonical occupancy and construction classes with their OED codes.

NOTE: the code values follow the OED occupancy/construction code lists as best known;
verify against the published OED specification before any production use.
"""

from __future__ import annotations

OCCUPANCY_CODES: dict[str, int] = {
    "unknown": 1000,
    "residential": 1050,
    "commercial": 1100,
    "retail": 1101,
    "warehouse": 1102,        # wholesale trade / storage
    "office": 1104,           # professional, technical and business services
    "hotel": 1119,            # temporary lodging
    "industrial": 1150,
    "heavy_manufacturing": 1151,
    "light_manufacturing": 1152,
    "food_processing": 1153,
    "data_centre": 1156,      # high technology
}

CONSTRUCTION_CODES: dict[str, int] = {
    "unknown": 5000,
    "wood": 5050,
    "masonry": 5100,
    "reinforced_concrete": 5150,
    "steel": 5200,
    "light_metal": 5201,
}
