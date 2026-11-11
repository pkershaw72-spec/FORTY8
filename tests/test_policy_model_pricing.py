"""Steps 9, 10 and 11 - Policy mapping, model and track, pricing."""

import pytest

from forty8.agents.model_track import ModelTrackAgent
from forty8.agents.policy_mapping import PolicyMappingAgent
from forty8.agents.pricing import PricingAgent
from forty8.connectors.registry import build_connectors
from forty8.models.common import ResultStatus
from forty8.models.exposure import Account, ExposureSet, Location, SourcedValue
from forty8.models.pipeline import AuditGateOutput, CodedSlip, ModelOutput, PolicyMapping, SlipTerm
from forty8.models.results import LossResults
from helpers import make_ctx


def loc(number, country, area, tc_zone=0, flood_zone=1, hazard=True, tiv=10_000_000.0, currency="USD"):
    scores = {"nathan.tropical_cyclone_zone": tc_zone, "nathan.river_flood_zone": flood_zone} if hazard else {}
    return Location(LocNumber=str(number), CountryCode=country, AreaCode=area, BuildingTIV=tiv, LocCurrency=currency,
                    hazard={k: SourcedValue(value=float(v), source="nathan") for k, v in scores.items()})


LOCATIONS = [loc(1, "US", "FL", tc_zone=4, flood_zone=3), loc(2, "US", "CA"), loc(3, "US", "TX"), loc(4, "JP", "Tokyo")]


def slip(zones=(("earthquake", "California", 5e7), ("earthquake", "Japan", 3e7),
                ("windstorm", "Tier 1 counties", 7.5e7), ("flood", "all locations", 2.5e7)), **overrides):
    values = {"umr": "B0000TEST", "insured": "Acme", "currency": "USD", "inception": "2027-01-01",
              "expiry": "2027-12-31", "limit": 2.5e8, "deductible": 2.5e5, "perils": ["windstorm", "earthquake", "flood"]}
    values.update(overrides)
    terms = [SlipTerm(name=n, value=v, confidence=0.95, method="llm:mock-llm") for n, v in values.items()]
    terms += [SlipTerm(name=f"sublimit:{p}:{z}", confidence=0.93, source_text=f"{p} - {z}: USD {l:,.0f}",
                       value={"peril": p, "zone_text": z, "limit": l, "basis": "aggregate"}) for p, z, l in zones]
    return CodedSlip(terms=terms, overall_confidence=0.93, threshold=0.85)


def map_policy(settings, locations=LOCATIONS, coded=None, **kw):
    inputs = {"audit_gate": AuditGateOutput(locations=locations), "contract": coded or slip()}
    return PolicyMappingAgent().run(make_ctx(settings, inputs=inputs, **kw))


# ---- step 9
def test_terms_and_sublimits_by_zone_are_applied_to_every_location(settings):
    result = map_policy(settings)
    assert result.status == ResultStatus.PASS
    exposure = result.output.exposure
    account = exposure.account
    assert (account.AccNumber, account.PolLimit, account.PolDed, account.AccCurrency) == ("B0000TEST", 2.5e8, 2.5e5, "USD")
    assert account.PolPerilsCovered == ["windstorm", "earthquake", "flood"] and len(account.sublimits) == 4
    florida, california, texas, japan = exposure.locations
    assert florida.zones == ["all", "us_tier1_wind"] and california.zones == ["all", "california_eq"]
    assert texas.zones == ["all"] and japan.zones == ["all", "japan_eq"]
    assert all(l.terms.limit == 2.5e8 and l.terms.deductible == 2.5e5 and len(l.terms.perils) == 3 for l in exposure.locations)
    assert {(s.peril, s.limit) for s in florida.terms.sublimits} == {("windstorm", 7.5e7), ("flood", 2.5e7)}
    assert [(s.peril, s.zone) for s in texas.terms.sublimits] == [("flood", "all")]
    assert result.output.locations_by_zone == {"all": 4, "california_eq": 1, "japan_eq": 1, "us_tier1_wind": 1}
    assert "terms applied to 4 of 4 locations" in result.audit[-1].decision


def test_undefined_sublimit_zone_goes_to_review_and_is_learnable(settings):
    wording = "High Hazard Flood Zones as per Schedule 2"
    coded = slip(zones=(("flood", wording, 2.5e7),))
    result = map_policy(settings, coded=coded)
    [item] = result.exceptions
    assert item.code == "UNDEFINED_SUBLIMIT_ZONE" and item.subject == f"zone:{wording}"
    assert (item.learn.kind, item.learn.key) == ("zone", wording) and "high_flood" in item.payload["zones"]

    fixed = map_policy(settings, coded=coded, corrections={f"zone:{wording}": "high_flood"})
    assert fixed.status == ResultStatus.PASS and fixed.output.zone_resolution == {wording: "high_flood"}
    assert [l.LocNumber for l in fixed.output.exposure.locations if l.terms.sublimits] == ["1"]   # flood zone 3
    learned = map_policy(settings, coded=coded, learned={("zone", wording): "all"})
    assert learned.status == ResultStatus.PASS and learned.output.locations_by_zone == {"all": 4}
    invalid = map_policy(settings, coded=coded, corrections={f"zone:{wording}": "atlantis"})
    assert "'atlantis' is not a defined zone" in invalid.exceptions[0].reason


def test_missing_account_term_goes_to_review(settings):
    result = map_policy(settings, coded=slip(deductible=None))
    assert [e.code for e in result.exceptions] == ["MISSING_POLICY_TERM"] and result.exceptions[0].subject == "term:deductible"
    assert all(l.terms is None for l in result.output.exposure.locations)


def test_location_without_hazard_data_cannot_be_zoned(settings):
    locations = [LOCATIONS[0], loc(9, "US", "FL", hazard=False)]
    result = map_policy(settings, locations=locations)
    [item] = result.exceptions
    assert item.code == "ZONE_UNDETERMINED" and item.subject == "zones:9" and item.payload["zones"] == ["us_tier1_wind"]
    fixed = map_policy(settings, locations=locations, corrections={"zones:9": "us_tier1_wind"})
    assert fixed.status == ResultStatus.PASS and fixed.output.exposure.locations[1].zones == ["all", "us_tier1_wind"]
    none = map_policy(settings, locations=locations, corrections={"zones:9": "none"})
    assert none.status == ResultStatus.PASS and none.output.exposure.locations[1].zones == ["all"]


# ---- step 10
def exposure_mapping(locations=None):
    account = Account(AccNumber="A1", AccName="Acme", AccCurrency="USD", PolLimit=2.5e8, PolDed=2.5e5)
    return PolicyMapping(exposure=ExposureSet(account=account, locations=locations or LOCATIONS))


def run_model(settings, connectors=None, mapping=None, **options):
    settings.connectors["cat_model"].options.update(options)
    connectors = connectors or build_connectors(settings)
    ctx = make_ctx(settings, inputs={"policy_mapping": mapping or exposure_mapping()}, connectors=connectors)
    return ModelTrackAgent().run(ctx), connectors


def test_model_job_is_tracked_to_completion(settings):
    result, _ = run_model(settings)
    assert result.status == ResultStatus.PASS
    out = result.output
    assert (out.job_id, out.attempts, out.total_tiv, out.currency) == ("job-0001", 1, 40_000_000, "USD")
    assert 0 < out.results.aal < out.results.oep_100 < out.results.oep_250


def test_values_are_converted_to_the_account_currency(settings):
    mapping = exposure_mapping([loc(1, "DE", "HH", tiv=1_000_000.0, currency="EUR"), loc(2, "US", "FL", tiv=1_000_000.0)])
    result, _ = run_model(settings, mapping=mapping)
    assert result.output.total_tiv == pytest.approx(2_080_000)
    assert mapping.exposure.locations[0].LocCurrency == "EUR"   # the stored exposure set is not altered


def test_failed_job_is_retried_before_anyone_is_asked(settings):
    result, _ = run_model(settings, fail_first_n_jobs=2)
    assert result.status == ResultStatus.PASS and result.output.attempts == 3
    assert sum("retrying" in a.decision for a in result.audit) == 2


def test_retries_exhausted_goes_to_review_then_retry_succeeds(settings):
    result, connectors = run_model(settings, fail_first_n_jobs=3)
    assert result.status == ResultStatus.EXCEPTION and result.output is None
    [item] = result.exceptions
    assert item.code == "MODEL_JOB_FAILED" and item.payload["attempts"] == settings.model.max_retries + 1
    assert "retries exhausted" in result.audit[-1].decision
    again, _ = run_model(settings, connectors=connectors)
    assert again.status == ResultStatus.PASS and again.output.job_id == "job-0004"


def test_timeout_counts_as_a_failure(settings):
    settings.model.timeout_seconds, settings.model.max_retries = 0.01, 0
    result, _ = run_model(settings, polls_until_complete=10**9)
    assert result.status == ResultStatus.EXCEPTION and "timed out after 0.01s" in result.exceptions[0].reason


# ---- step 11
def price(settings, connectors=None, aal=1e6, **options):
    settings.connectors["pricing_tool"].options.update(options)
    connectors = connectors or build_connectors(settings)
    model = ModelOutput(job_id="job-0001", attempts=1, model="mock", total_tiv=4e7, currency="USD",
                        results=LossResults(job_id="job-0001", currency="USD", aal=aal, oep_100=3e7, oep_250=5e7))
    ctx = make_ctx(settings, inputs={"model_track": model, "policy_mapping": exposure_mapping()}, connectors=connectors)
    return PricingAgent().run(ctx), connectors


def test_loss_metrics_are_sent_and_accepted(settings):
    result, connectors = price(settings)
    assert result.status == ResultStatus.PASS and result.output.response.reference == "PRC-00001"
    sent = connectors.pricing_tool.received[0]
    assert (sent.aal, sent.oep_100, sent.oep_250, sent.account_number, sent.total_tiv) == (1e6, 3e7, 5e7, "A1", 4e7)


def test_rejection_goes_to_review_then_retry(settings):
    result, connectors = price(settings, reject_first_n=1)
    assert [e.code for e in result.exceptions] == ["PRICING_REJECTED"] and not result.output.response.accepted
    again, _ = price(settings, connectors=connectors)
    assert again.status == ResultStatus.PASS

    invalid, _ = price(settings, aal=0.0, reject_first_n=0)
    assert "failed validation" in invalid.exceptions[0].reason
