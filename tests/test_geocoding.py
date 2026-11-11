"""Step 6 - Geocoding cascade and the 80% TIV check."""

import pytest

from forty8.agents.geocoding import GeocodingAgent, parse_coordinates
from forty8.connectors.registry import build_connectors
from forty8.models.common import ResultStatus
from forty8.models.exposure import Location, SearchHit
from forty8.models.pipeline import ClassifiedLocations
from helpers import make_ctx

ROOFTOP = dict(StreetAddress="1200 Harbor St", City="Miami", PostalCode="33131", CountryCode="US")
ESTATE = dict(StreetAddress="Nave 3, Parque Industrial Atlas", City="Monterrey", PostalCode="64000", CountryCode="MX")
MARKER = dict(StreetAddress="Km 14 Carretera 57", City="Querétaro", CountryCode="MX")
VAGUE = dict(StreetAddress="Address to follow", City="Houston", CountryCode="US")
VAGUE_ADDRESS = "Address to follow, Houston, US"


def loc(number, tiv, currency="USD", **address):
    return Location(LocNumber=str(number), BuildingTIV=tiv, LocCurrency=currency, **address)


def run(settings, locations, connectors=None, **kw):
    ctx = make_ctx(settings, inputs={"ml_classify": ClassifiedLocations(locations=locations)},
                   connectors=connectors or build_connectors(settings), **kw)
    return GeocodingAgent().run(ctx), ctx


def test_cascade_moodys_then_bing_then_web(settings):
    result, ctx = run(settings, [loc(1, 100, **ROOFTOP), loc(2, 100, **ESTATE), loc(3, 100, **MARKER)])
    assert result.status == ResultStatus.PASS
    a, b, c = result.output.locations
    assert (a.geocode.resolution, a.geocode.provider) == ("rooftop", "moodys_geocoder")
    assert (b.geocode.resolution, b.geocode.provider) == ("street", "bing_geocoder")
    assert (c.geocode.resolution, c.geocode.provider) == ("street", "web_search")
    assert all(l.Latitude is not None and l.sources["Latitude"].source == l.geocode.provider for l in (a, b, c))
    conn = ctx.connectors
    assert len(conn.moodys_geocoder.calls) == 3          # Moody's first, for every location
    assert len(conn.bing_geocoder.calls) == 2            # Bing only for those not at rooftop
    assert len(conn.web_search.calls) == 1               # web search only for what is still poor
    assert result.output.tiv_street_share == 1.0
    assert result.output.by_provider == {"moodys_geocoder": 1, "bing_geocoder": 1, "web_search": 1}
    trail = next(a for a in result.audit if a.input_ref.startswith("address:Km 14"))
    assert "moodys_geocoder:city > bing_geocoder:city > web_search:street" in trail.decision


@pytest.mark.parametrize("vague_tiv,expected", [(20.0, ResultStatus.PASS), (20.1, ResultStatus.EXCEPTION)])
def test_tiv_check_boundary_at_80_percent(settings, vague_tiv, expected):
    result, _ = run(settings, [loc(1, 80, **ROOFTOP), loc(2, vague_tiv, **VAGUE)])
    assert result.status == expected
    check = next(a for a in result.audit if a.rule_or_model == "rule:tiv_street_level_check")
    assert ("meets" in check.decision) == (expected == ResultStatus.PASS)


def test_tiv_check_uses_base_currency(settings):
    # 100 USD at rooftop vs 3,000 JPY (about 20 USD) unresolved: 83% in USD terms, 3% in raw numbers
    result, _ = run(settings, [loc(1, 100, **ROOFTOP), loc(2, 3000, currency="JPY", **VAGUE)])
    assert result.status == ResultStatus.PASS
    assert result.output.tiv_street_share == pytest.approx(100 / 119.8, abs=1e-3)


def test_analyst_is_last_resort_highest_tiv_first_and_only_as_many_as_needed(settings):
    houston2 = dict(VAGUE, StreetAddress="Varios - ver anexo")
    houston3 = dict(VAGUE, StreetAddress="Site under construction")
    locations = [loc(1, 70, **ROOFTOP), loc(2, 5, **VAGUE), loc(3, 15, **houston2), loc(4, 10, **houston3)]
    result, _ = run(settings, locations)
    assert result.status == ResultStatus.EXCEPTION            # 70% at street level
    [item] = result.exceptions                                # fixing the 15 alone reaches 85%
    assert item.code == "LOW_TIV_GEOCODE" and item.payload["locations"] == ["3"] and item.priority == 15
    assert item.payload["resolution"] == "city" and item.learn.kind == "address"
    assert "70.0% of TIV" in item.reason

    settings.thresholds.geocode_min_tiv_street_level = 0.90
    result, _ = run(settings, locations)
    assert [e.payload["locations"] for e in result.exceptions] == [["3"], ["4"]]   # 15 then 10, not the 5


def test_analyst_coordinates_and_better_address(settings):
    locations = [loc(1, 50, **ROOFTOP), loc(2, 50, **VAGUE)]
    fixed, _ = run(settings, locations, corrections={f"address:{VAGUE_ADDRESS}": "29.7604, -95.3698"})
    assert fixed.status == ResultStatus.PASS
    geo = fixed.output.locations[1].geocode
    assert (geo.provider, geo.resolution, geo.latitude) == ("analyst:alice", "rooftop", 29.7604)

    readdressed, ctx = run(settings, locations, corrections={f"address:{VAGUE_ADDRESS}": "900 Commerce Blvd"})
    assert readdressed.status == ResultStatus.PASS
    assert readdressed.output.locations[1].geocode.provider == "moodys_geocoder"
    assert ctx.connectors.moodys_geocoder.calls[-1].street == "900 Commerce Blvd"

    still_bad, _ = run(settings, locations, corrections={f"address:{VAGUE_ADDRESS}": "Somewhere nearby"})
    assert still_bad.status == ResultStatus.EXCEPTION
    assert "still only resolves to city level" in still_bad.exceptions[0].reason


def test_learned_address_is_reused(settings):
    locations = [loc(1, 50, **ROOFTOP), loc(2, 50, **VAGUE)]
    result, _ = run(settings, locations, learned={("address", VAGUE_ADDRESS): "29.7604, -95.3698"})
    assert result.status == ResultStatus.PASS
    assert result.output.locations[1].geocode.provider == "learned_mapping:99"


def test_duplicate_addresses_are_geocoded_once(settings):
    result, ctx = run(settings, [loc(1, 10, **ROOFTOP), loc(1, 10, **ROOFTOP)])
    assert len(ctx.connectors.moodys_geocoder.calls) == 1
    assert result.output.locations[0].geocode == result.output.locations[1].geocode


def test_web_result_far_from_the_stated_city_is_rejected(settings):
    class FarAwaySearch:
        def search(self, query):
            return [SearchHit(title="Wrong site", snippet="Coordinates: 48.8566, 2.3522", url="https://example.invalid")]

    connectors = build_connectors(settings)
    connectors.web_search = FarAwaySearch()
    result, _ = run(settings, [loc(1, 100, **MARKER)], connectors=connectors)   # Paris is not near Querétaro
    assert result.output.locations[0].geocode.resolution == "city"
    assert result.status == ResultStatus.EXCEPTION


def test_unknown_city_is_unmatched(settings):
    result, _ = run(settings, [loc(1, 90, **ROOFTOP), loc(2, 10, StreetAddress="1 Nowhere Rd", City="Atlantis")])
    lost = result.output.locations[1]
    assert lost.geocode.resolution == "unmatched" and lost.Latitude is None and "Latitude" not in lost.sources
    assert result.status == ResultStatus.PASS      # 90% of TIV is still at street level


def test_parse_coordinates():
    assert parse_coordinates("25.76, -80.19") == (25.76, -80.19)
    assert parse_coordinates({"lat": 1.5, "lon": 2.5}) == (1.5, 2.5)
    assert parse_coordinates([35.6, 139.7]) == (35.6, 139.7)
    assert parse_coordinates("95.0, 10.0") is None and parse_coordinates("12 High St") is None
