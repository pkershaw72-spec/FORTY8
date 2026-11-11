"""Zone definitions used to apply sublimits to locations (step 9).

PoC simplification: real "Tier 1" wind zones are county lists and flood zones come from flood
maps; here they are derived from country, state and the NATHAN hazard scores on each location."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from forty8.models.exposure import Location
from forty8.reference.gazetteer import fold


def _hazard(loc: Location, name: str) -> float:
    value = loc.hazard.get(name)
    return float(value.value) if value is not None else 0.0


@dataclass(frozen=True)
class ZoneDef:
    key: str
    description: str
    aliases: tuple[str, ...]
    test: Callable[[Location], bool]
    needs_hazard: bool = False


ZONES: tuple[ZoneDef, ...] = (
    ZoneDef("all", "All locations", ("all", "all locations", "worldwide", "any location"), lambda loc: True),
    ZoneDef("california_eq", "California", ("california", "state of california"),
            lambda loc: loc.CountryCode == "US" and (loc.AreaCode or "").upper() == "CA"),
    ZoneDef("japan_eq", "Japan", ("japan",), lambda loc: loc.CountryCode == "JP"),
    ZoneDef("mexico", "Mexico", ("mexico",), lambda loc: loc.CountryCode == "MX"),
    ZoneDef("us_tier1_wind", "US Tier 1 named-windstorm zones (NATHAN tropical cyclone zone 4)",
            ("tier 1 counties", "tier 1", "tier one counties", "tier 1 wind"),
            lambda loc: loc.CountryCode == "US" and _hazard(loc, "nathan.tropical_cyclone_zone") >= 4, True),
    ZoneDef("high_flood", "High hazard flood zones (NATHAN river flood zone 3 or above)",
            ("high hazard flood zones", "high hazard flood"),
            lambda loc: _hazard(loc, "nathan.river_flood_zone") >= 3, True),
)
ZONE_BY_KEY = {z.key: z for z in ZONES}
_BY_ALIAS = {alias: z.key for z in ZONES for alias in (z.key, *z.aliases)}


def resolve_zone(text: str) -> str | None:
    """Exact (case- and accent-insensitive) match of the slip's zone wording to a zone key."""
    return _BY_ALIAS.get(fold(text))
