"""Deterministic mock geocoders and web search.

Behaviour is driven only by the address text, so tests can predict it:
- Moody's: street number + known city -> rooftop; else postcode -> postcode level; else city level.
- Bing:    also resolves named estates ("Industrial Park", "Parque Industrial"...) to street level.
- Web search: resolves highway-marker addresses ("km 14 Carretera 57") to coordinates.
- An unknown city is unmatched everywhere.
"""

from __future__ import annotations

import hashlib
import re

from forty8.models.exposure import AddressQuery, GeocodeResolution, GeocodeResult, SearchHit
from forty8.reference.gazetteer import CITIES, City, find_city, fold

_NOT_A_STREET_NUMBER = r"(?:km|carretera|highway|hwy|route|ruta|bldg|building|unit|nave|warehouse|bodega|lote|plot|suite)\.?\s*\d+"
_ESTATE = re.compile(r"industrial park|parque industrial|business park|zona industrial|industrial estate|gewerbepark")
_KM_MARKER = re.compile(r"\bkm\.?\s*\d+")


def has_street_number(street: str) -> bool:
    cleaned = re.sub(_NOT_A_STREET_NUMBER, " ", fold(street))
    return re.search(r"\b\d{1,5}[a-z]?\b", cleaned) is not None


def _jitter(text: str, scale: float) -> tuple[float, float]:
    digest = hashlib.sha256(fold(text).encode()).digest()
    dx = (int.from_bytes(digest[:4], "big") / 2**32 - 0.5) * scale
    dy = (int.from_bytes(digest[4:8], "big") / 2**32 - 0.5) * scale
    return dx, dy


def _result(city: City, address: AddressQuery, resolution: GeocodeResolution, provider: str, confidence: float) -> GeocodeResult:
    precise = resolution in (GeocodeResolution.ROOFTOP, GeocodeResolution.STREET)
    dlat, dlon = _jitter(address.one_line(), 0.08) if precise else (0.0, 0.0)
    return GeocodeResult(
        resolution=resolution, provider=provider, confidence=confidence,
        latitude=round(city.lat + dlat, 6), longitude=round(city.lon + dlon, 6),
        matched_address=address.one_line() if precise else f"{city.name}, {city.country}",
    )


class _MockGeocoder:
    name = "mock"
    resolves_estates = False

    def __init__(self, options: dict | None = None):
        self.calls: list[AddressQuery] = []

    def geocode(self, address: AddressQuery) -> GeocodeResult:
        self.calls.append(address)
        city = find_city(address.city, address.country)
        if city is None:
            return GeocodeResult(resolution=GeocodeResolution.UNMATCHED, provider=self.name, confidence=0.0)
        if has_street_number(address.street):
            return _result(city, address, GeocodeResolution.ROOFTOP, self.name, 0.95)
        if self.resolves_estates and _ESTATE.search(fold(address.street)):
            return _result(city, address, GeocodeResolution.STREET, self.name, 0.80)
        if address.postal_code.strip():
            return _result(city, address, GeocodeResolution.POSTCODE, self.name, 0.60)
        return _result(city, address, GeocodeResolution.CITY, self.name, 0.40)


class MockMoodysGeocoder(_MockGeocoder):
    name = "moodys_geocoder"


class MockBingGeocoder(_MockGeocoder):
    name = "bing_geocoder"
    resolves_estates = True


class MockWebSearch:
    def __init__(self, options: dict | None = None):
        self.calls: list[str] = []

    def search(self, query: str) -> list[SearchHit]:
        self.calls.append(query)
        folded = fold(query)
        if not _KM_MARKER.search(folded):
            return []
        city = next((c for c in CITIES if any(fold(n) in folded for n in (c.name, *c.aliases))), None)
        if city is None:
            return []
        dlat, dlon = _jitter(query, 0.2)
        lat, lon = round(city.lat + dlat, 6), round(city.lon + dlon, 6)
        return [SearchHit(
            title=f"Industrial site near {city.name} (synthetic listing)",
            snippet=f"Facility on the highway outside {city.name}. Coordinates: {lat}, {lon}.",
            url="https://example.invalid/synthetic-listing",
        )]
