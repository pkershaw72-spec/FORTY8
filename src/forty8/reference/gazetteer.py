"""Small gazetteer of real cities (public centroid coordinates) used by the synthetic
data generator and the mock geocoders/hazard connectors. No client data."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class City:
    name: str
    country: str      # ISO 3166-1 alpha-2
    area: str         # state / province / prefecture
    postcode: str
    lat: float
    lon: float
    region: str       # US | MX | EU | JP
    currency: str
    zones: tuple[str, ...] = field(default=())
    aliases: tuple[str, ...] = field(default=())


CITIES: tuple[City, ...] = (
    City("Miami", "US", "FL", "33131", 25.7617, -80.1918, "US", "USD", ("us_tier1_wind", "us_flood")),
    City("Houston", "US", "TX", "77002", 29.7604, -95.3698, "US", "USD", ("us_tier1_wind", "us_flood")),
    City("New Orleans", "US", "LA", "70112", 29.9511, -90.0715, "US", "USD", ("us_tier1_wind", "us_flood")),
    City("Charleston", "US", "SC", "29401", 32.7765, -79.9311, "US", "USD", ("us_tier1_wind",)),
    City("Los Angeles", "US", "CA", "90012", 34.0522, -118.2437, "US", "USD", ("california_eq",)),
    City("San Francisco", "US", "CA", "94103", 37.7749, -122.4194, "US", "USD", ("california_eq",)),
    City("Seattle", "US", "WA", "98101", 47.6062, -122.3321, "US", "USD", ("pnw_eq",)),
    City("Chicago", "US", "IL", "60601", 41.8781, -87.6298, "US", "USD"),
    City("Dallas", "US", "TX", "75201", 32.7767, -96.7970, "US", "USD"),
    City("New York", "US", "NY", "10001", 40.7128, -74.0060, "US", "USD", ("us_flood",)),
    City("Mexico City", "MX", "CDMX", "06600", 19.4326, -99.1332, "MX", "MXN", ("mexico_eq",),
         ("Ciudad de México", "CDMX")),
    City("Monterrey", "MX", "NL", "64000", 25.6866, -100.3161, "MX", "MXN"),
    City("Guadalajara", "MX", "JAL", "44100", 20.6597, -103.3496, "MX", "MXN", ("mexico_eq",)),
    City("Cancún", "MX", "QROO", "77500", 21.1619, -86.8515, "MX", "MXN", ("mexico_wind",)),
    City("Querétaro", "MX", "QRO", "76000", 20.5888, -100.3899, "MX", "MXN"),
    City("London", "GB", "ENG", "EC2V 7HH", 51.5074, -0.1278, "EU", "GBP", ("europe_flood", "europe_wind")),
    City("Hamburg", "DE", "HH", "20095", 53.5511, 9.9937, "EU", "EUR", ("europe_flood", "europe_wind")),
    City("Rotterdam", "NL", "ZH", "3011", 51.9244, 4.4777, "EU", "EUR", ("europe_flood", "europe_wind")),
    City("Paris", "FR", "IDF", "75001", 48.8566, 2.3522, "EU", "EUR", ("europe_wind",)),
    City("Milan", "IT", "LOM", "20121", 45.4642, 9.1900, "EU", "EUR", ("europe_flood",), ("Milano",)),
    City("Madrid", "ES", "MD", "28001", 40.4168, -3.7038, "EU", "EUR"),
    City("Tokyo", "JP", "Tokyo", "100-0001", 35.6762, 139.6503, "JP", "JPY", ("japan_eq", "japan_wind")),
    City("Osaka", "JP", "Osaka", "530-0001", 34.6937, 135.5023, "JP", "JPY", ("japan_eq", "japan_wind")),
    City("Nagoya", "JP", "Aichi", "450-0002", 35.1815, 136.9066, "JP", "JPY", ("japan_eq",)),
    City("Yokohama", "JP", "Kanagawa", "220-0011", 35.4437, 139.6380, "JP", "JPY", ("japan_eq", "japan_wind")),
)

COUNTRY_ALIASES = {
    "usa": "US", "united states": "US", "united states of america": "US", "estados unidos": "US",
    "mexico": "MX", "méxico": "MX", "united kingdom": "GB", "uk": "GB", "england": "GB",
    "germany": "DE", "deutschland": "DE", "netherlands": "NL", "france": "FR", "italy": "IT",
    "spain": "ES", "españa": "ES", "japan": "JP",
}


def fold(text: str) -> str:
    """Lower-case, strip accents and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).lower().split())


def country_code(text: str) -> str:
    value = str(text or "").strip()
    return COUNTRY_ALIASES.get(fold(value), COUNTRY_ALIASES.get(value.lower(), value.upper()))


_BY_NAME: dict[str, City] = {}
for _c in CITIES:
    for _n in (_c.name, *_c.aliases):
        _BY_NAME[fold(_n)] = _c


def find_city(name: str, country: str = "") -> City | None:
    city = _BY_NAME.get(fold(name))
    if city and country and country_code(country) != city.country:
        return None
    return city


def nearest_city(lat: float, lon: float) -> City:
    return min(CITIES, key=lambda c: (c.lat - lat) ** 2 + (c.lon - lon) ** 2)
