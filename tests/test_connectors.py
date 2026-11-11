"""Mock connectors behave deterministically and satisfy their interfaces."""

from __future__ import annotations

import pytest

from forty8.config import load_settings
from forty8.connectors import interfaces
from forty8.connectors.registry import build_connectors
from forty8.models.exposure import Account, AddressQuery, ExposureSet, GeocodeResolution, Location
from forty8.models.results import JobState, PricingSubmission


@pytest.fixture
def connectors(settings):
    return build_connectors(settings)


def test_every_connector_satisfies_its_interface(connectors):
    expected = {
        "moodys_geocoder": interfaces.Geocoder, "bing_geocoder": interfaces.Geocoder,
        "web_search": interfaces.WebSearch, "building_data": interfaces.BuildingData,
        "nathan": interfaces.HazardData, "athenium": interfaces.HazardData, "h3": interfaces.H3Indexer,
        "survey_store": interfaces.SurveyStore, "prior_year_store": interfaces.PriorYearStore,
        "cat_model": interfaces.CatModel, "pricing_tool": interfaces.PricingTool, "llm": interfaces.LLMProvider,
    }
    for name, protocol in expected.items():
        assert isinstance(getattr(connectors, name), protocol), name


def test_unknown_implementation_is_a_clear_error():
    settings = load_settings(overrides={"connectors": {"cat_model": {"impl": "moodys_live"}}})
    with pytest.raises(KeyError, match="no implementation 'moodys_live'"):
        build_connectors(settings)


def test_geocoder_cascade_behaviour(connectors):
    moodys, bing, web = connectors.moodys_geocoder, connectors.bing_geocoder, connectors.web_search
    rooftop = AddressQuery(street="1200 Harbor St", city="Miami", postal_code="33131", country="USA")
    estate = AddressQuery(street="Nave 3, Parque Industrial Atlas", city="Monterrey", postal_code="64000", country="México")
    marker = AddressQuery(street="Km 14 Carretera 57", city="Querétaro", country="México")
    vague = AddressQuery(street="Address to follow", city="Houston", country="USA")

    assert moodys.geocode(rooftop).resolution == GeocodeResolution.ROOFTOP
    assert moodys.geocode(estate).resolution == GeocodeResolution.POSTCODE
    assert bing.geocode(estate).resolution == GeocodeResolution.STREET
    assert moodys.geocode(marker).resolution == GeocodeResolution.CITY
    assert bing.geocode(marker).resolution == GeocodeResolution.CITY
    assert "Coordinates:" in web.search(marker.one_line())[0].snippet
    assert bing.geocode(vague).resolution == GeocodeResolution.CITY
    assert web.search(vague.one_line()) == []
    assert moodys.geocode(AddressQuery(street="1 Nowhere Rd", city="Atlantis")).resolution == GeocodeResolution.UNMATCHED
    assert moodys.geocode(rooftop) == moodys.geocode(rooftop)  # deterministic


def test_hazard_h3_and_building_data(connectors):
    assert connectors.nathan.lookup(25.76, -80.19)["tropical_cyclone_zone"] == 4.0   # Miami
    assert connectors.nathan.lookup(35.68, 139.65)["earthquake_zone"] == 4.0         # Tokyo
    assert connectors.athenium.lookup(25.76, -80.19) is not None
    assert connectors.athenium.lookup(35.68, 139.65) is None                         # US only
    assert len(connectors.h3.cell(25.76, -80.19, 8)) == 15
    hits = [connectors.building_data.lookup(25.0 + i / 100, -80.0) for i in range(50)]
    assert any(h is None for h in hits) and any(h is not None for h in hits)


def test_stores_read_generated_files(connectors, messy_submission):
    prior = connectors.prior_year_store.get(messy_submission.insured)
    assert prior is not None and len(prior.locations) == len(messy_submission.locations) - 1
    assert connectors.survey_store.get(messy_submission.insured, "1").verified
    assert connectors.prior_year_store.get("Nobody Ltd") is None


def test_cat_model_and_pricing(settings):
    settings.connectors["cat_model"].options["fail_first_n_jobs"] = 1
    c = build_connectors(settings)
    exposure = ExposureSet(
        account=Account(AccNumber="A1", AccCurrency="USD", PolLimit=50_000_000),
        locations=[Location(LocNumber="1", BuildingTIV=20_000_000, ContentsTIV=5_000_000)],
    )

    def run_job():
        job = c.cat_model.submit(exposure)
        while (status := c.cat_model.status(job)).state == JobState.RUNNING:
            pass
        return job, status

    assert run_job()[1].state == JobState.FAILED
    job, status = run_job()
    assert status.state == JobState.COMPLETED
    loss = c.cat_model.results(job)
    assert 0 < loss.aal < loss.oep_100 < loss.oep_250 <= 50_000_000

    submission = PricingSubmission(account_number="A1", insured="x", currency="USD", aal=loss.aal,
                                   oep_100=loss.oep_100, oep_250=loss.oep_250, total_tiv=exposure.total_tiv)
    assert c.pricing_tool.submit(submission).accepted
    assert not c.pricing_tool.submit(submission.model_copy(update={"aal": 0})).accepted
