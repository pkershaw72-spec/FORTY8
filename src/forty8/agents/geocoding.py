"""Step 6 - Geocoding. Cascade: Moody's for every location; Bing only for those not at rooftop;
web search for addresses still below street level. Check: the share of TIV at street level or
better must reach the configured threshold (80%).
Exception path: if the check still fails, the analyst is asked as a last resort, highest TIV
first, and only for as many locations as are needed to pass."""

from __future__ import annotations

import math
import re
from typing import Any

from forty8.agents.base import AgentContext
from forty8.agents.util import finish
from forty8.models.common import AgentResult, AuditEntry, ExceptionItem, LearnSpec
from forty8.models.exposure import AddressQuery, GeocodeResolution, GeocodeResult, Location, SourcedValue
from forty8.models.pipeline import ClassifiedLocations, GeocodedLocations

RANK = {GeocodeResolution.ROOFTOP: 4, GeocodeResolution.STREET: 3, GeocodeResolution.POSTCODE: 2,
        GeocodeResolution.CITY: 1, GeocodeResolution.UNMATCHED: 0}
_COORDS = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")


def query_for(loc: Location) -> AddressQuery:
    return AddressQuery(street=loc.StreetAddress or "", city=loc.City or "", area=loc.AreaCode or "",
                        postal_code=loc.PostalCode or "", country=loc.CountryCode or "")


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def parse_coordinates(value: Any) -> tuple[float, float] | None:
    """Accepts 'lat, lon', [lat, lon] or {'lat': .., 'lon': ..}. None if not valid coordinates."""
    lat = lon = None
    if isinstance(value, dict):
        lat = value.get("lat", value.get("latitude"))
        lon = value.get("lon", value.get("lng", value.get("longitude")))
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        lat, lon = value
    elif isinstance(value, str) and (match := _COORDS.search(value)):
        lat, lon = match.groups()
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


class GeocodingAgent:
    key = "geocoding"
    output_model = GeocodedLocations

    def run(self, ctx: AgentContext) -> AgentResult:
        source: ClassifiedLocations = ctx.input("ml_classify")
        threshold = ctx.settings.thresholds.geocode_min_tiv_street_level
        good = set(ctx.settings.geocoding.street_level_or_better)
        fx = ctx.settings.fx
        out = GeocodedLocations(locations=[loc.model_copy(deep=True) for loc in source.locations], threshold=threshold)
        audit: list[AuditEntry] = []

        groups: dict[str, list[Location]] = {}
        for loc in out.locations:
            groups.setdefault(query_for(loc).one_line(), []).append(loc)

        notes: dict[str, str] = {}
        for address, locs in groups.items():
            result, trail, note = self._resolve(ctx, address, query_for(locs[0]))
            if note:
                notes[address] = note
            for loc in locs:
                loc.geocode = result
                loc.Latitude, loc.Longitude = result.latitude, result.longitude
                if result.latitude is not None:
                    source_value = SourcedValue(value=result.resolution.value, source=result.provider,
                                                confidence=result.confidence)
                    loc.sources["Latitude"] = loc.sources["Longitude"] = source_value
            audit.append(AuditEntry(
                input_ref=f"address:{address}", confidence=result.confidence, rule_or_model=result.provider,
                decision=f"{result.resolution.value} level for {len(locs)} location(s) via {' > '.join(trail)}",
            ))
            out.by_resolution[result.resolution.value] = out.by_resolution.get(result.resolution.value, 0) + len(locs)
            out.by_provider[result.provider] = out.by_provider.get(result.provider, 0) + len(locs)

        def tiv(loc: Location) -> float:
            return fx.to_base(loc.tiv, loc.LocCurrency)

        unknown_ccy = sorted({str(loc.LocCurrency) for loc in out.locations if not fx.known(loc.LocCurrency)})
        if unknown_ccy:
            audit.append(AuditEntry(input_ref="fx", confidence=0.5, rule_or_model="rule:fx_static_table",
                                    decision=f"no FX rate for {unknown_ccy}; treated as {fx.base}"))
        out.total_tiv_base = sum(tiv(loc) for loc in out.locations)
        street_tiv = sum(tiv(loc) for loc in out.locations if loc.geocode.resolution.value in good)
        out.tiv_street_share = street_tiv / out.total_tiv_base if out.total_tiv_base else 1.0
        passed = out.tiv_street_share >= threshold
        audit.append(AuditEntry(
            input_ref="geocoding", confidence=1.0, rule_or_model="rule:tiv_street_level_check",
            decision=(f"{out.tiv_street_share:.1%} of TIV at street level or better "
                      f"({'meets' if passed else 'below'} threshold {threshold:.0%})"),
            detail={"by_resolution": out.by_resolution, "by_provider": out.by_provider},
        ))
        exceptions: list[ExceptionItem] = []
        if not passed:
            poor = sorted(
                ((sum(tiv(l) for l in locs), address, locs) for address, locs in groups.items()
                 if locs[0].geocode.resolution.value not in good),
                key=lambda item: -item[0],
            )
            running = street_tiv
            for group_tiv, address, locs in poor:  # only as many as are needed to pass, highest TIV first
                if running / out.total_tiv_base >= threshold:
                    break
                running += group_tiv
                exceptions.append(ExceptionItem(
                    code="LOW_TIV_GEOCODE", subject=f"address:{address}",
                    reason=(notes.get(address, "") +
                            f"Only {out.tiv_street_share:.1%} of TIV is at street level or better (threshold "
                            f"{threshold:.0%}). Location {', '.join(l.LocNumber for l in locs)} '{address}' "
                            f"({fx.base} {group_tiv:,.0f}) is at {locs[0].geocode.resolution.value} level only. "
                            "Correct with 'lat, lon' or a better street address."),
                    payload={"address": address, "locations": [l.LocNumber for l in locs],
                             "tiv_base": round(group_tiv, 2), "resolution": locs[0].geocode.resolution.value,
                             "best_guess": [locs[0].Latitude, locs[0].Longitude]},
                    priority=group_tiv, learn=LearnSpec(kind="address", key=address),
                ))
        return finish(out, audit, exceptions)

    def _resolve(self, ctx: AgentContext, address: str, query: AddressQuery) -> tuple[GeocodeResult, list[str], str]:
        """Returns (result, providers tried, note for the analyst if their correction did not work)."""
        note = ""
        fix = ctx.correction_for(f"address:{address}")
        learned = None if fix else ctx.learned.lookup("address", address)
        answer, who = (fix.value, f"analyst:{fix.analyst}") if fix else \
                      (learned.value, f"learned_mapping:{learned.id}") if learned else (None, "")
        trail: list[str] = []
        if answer is not None:
            if coords := parse_coordinates(answer):
                result = GeocodeResult(resolution=GeocodeResolution.ROOFTOP, provider=who, confidence=1.0,
                                       latitude=coords[0], longitude=coords[1], matched_address=address)
                return result, [who], note
            # otherwise the answer is a better street address: run it through the cascade
            street = answer.get("street") if isinstance(answer, dict) else str(answer)
            if street:
                query = query.model_copy(update={"street": street})
                trail.append(f"{who} (address '{street}')")

        c = ctx.connectors
        best = c.moodys_geocoder.geocode(query)
        trail.append(f"{c.moodys_geocoder.name}:{best.resolution.value}")
        if best.resolution != GeocodeResolution.ROOFTOP:
            second = c.bing_geocoder.geocode(query)
            trail.append(f"{c.bing_geocoder.name}:{second.resolution.value}")
            if RANK[second.resolution] > RANK[best.resolution]:
                best = second
        if RANK[best.resolution] < RANK[GeocodeResolution.STREET]:
            found = self._web_search(ctx, query, best)
            trail.append("web_search:" + (found.resolution.value if found else "no usable result"))
            if found:
                best = found
        if answer is not None and RANK[best.resolution] < RANK[GeocodeResolution.STREET]:
            note = f"The correction '{answer}' still only resolves to {best.resolution.value} level. "
        return best, trail, note

    def _web_search(self, ctx: AgentContext, query: AddressQuery, best: GeocodeResult) -> GeocodeResult | None:
        """Take coordinates from a search result only if they are close to the stated city."""
        limit = ctx.settings.geocoding.web_max_distance_km
        for hit in ctx.connectors.web_search.search(query.one_line()):
            coords = parse_coordinates(f"{hit.title} {hit.snippet}")
            if coords is None or best.latitude is None:
                continue
            if distance_km(coords[0], coords[1], best.latitude, best.longitude) <= limit:
                return GeocodeResult(resolution=GeocodeResolution.STREET, provider="web_search", confidence=0.75,
                                     latitude=coords[0], longitude=coords[1], matched_address=hit.title)
        return None
