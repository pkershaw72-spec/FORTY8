"""Step 7 - Enrichment. Applies verified engineering survey data, fills COPE gaps from the prior
year's submission and then third-party building data, attaches hazard data (NATHAN, Athenium)
and H3 cell IDs, and compares TIV with the prior year. Every value records its source.
Passes when the completeness score is reported.
Exception path: a large year-on-year TIV change is flagged for the analyst to confirm."""

from __future__ import annotations

from forty8.agents.base import AgentContext
from forty8.agents.util import finish
from forty8.models.common import AgentResult, AuditEntry, ExceptionItem, LearnSpec
from forty8.models.exposure import Location, SourcedValue
from forty8.models.pipeline import EnrichedLocations, GeocodedLocations, IntakeOutput, YoYFlag
from forty8.models.results import PriorYearLocation

COPE_FIELDS = ["OccupancyCode", "ConstructionCode", "YearBuilt", "NumberOfStoreys", "FloorArea"]
COMPLETENESS_FIELDS = [*COPE_FIELDS, "Latitude"]
SOV_FIELDS = ["LocName", "StreetAddress", "City", "AreaCode", "PostalCode", "CountryCode", "YearBuilt",
              "NumberOfStoreys", "FloorArea", "BuildingTIV", "ContentsTIV", "BITIV", "LocCurrency"]
TIV_FIELDS = ["BuildingTIV", "ContentsTIV", "BITIV"]
BUILDING_DATA_MAP = {"year_built": "YearBuilt", "number_of_storeys": "NumberOfStoreys",
                     "construction_code": "ConstructionCode", "occupancy_code": "OccupancyCode",
                     "floor_area_m2": "FloorArea"}
YOY_ANSWERS = ("confirm", "use_prior")


def completeness(locations: list[Location], weights: list[float] | None = None) -> float:
    if not locations:
        return 1.0
    weights = weights or [1.0] * len(locations)
    filled = sum(w * sum(getattr(loc, f) is not None for f in COMPLETENESS_FIELDS) for loc, w in zip(locations, weights))
    total = sum(weights) * len(COMPLETENESS_FIELDS)
    return filled / total if total else 1.0


class EnrichmentAgent:
    key = "enrichment"
    output_model = EnrichedLocations

    def run(self, ctx: AgentContext) -> AgentResult:
        geocoded: GeocodedLocations = ctx.input("geocoding")
        intake: IntakeOutput = ctx.input("intake")
        insured = intake.insured
        c, fx = ctx.connectors, ctx.settings.fx
        good = set(ctx.settings.geocoding.street_level_or_better)
        out = EnrichedLocations(locations=[loc.model_copy(deep=True) for loc in geocoded.locations])
        out.completeness_before = round(completeness(out.locations), 4)
        audit: list[AuditEntry] = []
        exceptions: list[ExceptionItem] = []

        prior = c.prior_year_store.get(insured) if insured else None
        prior_by_loc: dict[str, PriorYearLocation] = {p.loc_number: p for p in prior.locations} if prior else {}
        audit.append(AuditEntry(
            input_ref=f"insured:{insured}", confidence=1.0, rule_or_model="prior_year_store",
            decision=(f"prior-year submission {prior.year} found ({len(prior.locations)} locations)" if prior
                      else "no prior-year submission found; year-on-year comparison skipped"),
        ))

        def fill(loc: Location, field: str, value, source: str, confidence: float, why: str) -> None:
            setattr(loc, field, value)
            loc.sources[field] = SourcedValue(value=value, source=source, confidence=confidence)
            family = source.split(":")[0]
            out.filled_by_source[family] = out.filled_by_source.get(family, 0) + 1
            audit.append(AuditEntry(input_ref=f"loc:{loc.LocNumber}:{field}", confidence=confidence,
                                    rule_or_model=source, decision=f"{why}: {field} = {value}"))

        seen_yoy: set[str] = set()
        for loc in out.locations:
            origin = f"sov:{loc.lineage.tab}!row{loc.lineage.source_row}" if loc.lineage else "sov"
            for field in SOV_FIELDS:
                if getattr(loc, field) is not None and field not in loc.sources:
                    loc.sources[field] = SourcedValue(value=getattr(loc, field), source=origin, confidence=1.0)

            # 1. verified engineering survey data takes precedence over the broker's values
            survey = c.survey_store.get(insured, loc.LocNumber) if insured else None
            if survey is not None and survey.verified:
                source = f"survey:{survey.survey_date}"
                for attr, value in survey.attributes.items():
                    if attr not in COPE_FIELDS:
                        loc.extra[attr] = SourcedValue(value=value, source=source, confidence=1.0)
                        continue
                    current = getattr(loc, attr)
                    if current is None:
                        fill(loc, attr, value, source, 1.0, "filled from verified survey")
                    elif current != value:
                        fill(loc, attr, value, source, 1.0, f"verified survey overrides submitted value {current}")
                    else:
                        loc.sources[attr] = SourcedValue(value=value, source=source, confidence=1.0)

            # 2. fields missing this year, from the prior year's submission
            before = prior_by_loc.get(loc.LocNumber)
            if before is not None:
                for field in COPE_FIELDS:
                    if getattr(loc, field) is None and before.attributes.get(field) is not None:
                        fill(loc, field, before.attributes[field], f"prior_year:{prior.year}", 0.9,
                             "missing this year, filled from prior year")

            # 3. remaining gaps, from third-party building data (needs a precise location)
            gaps = [f for f in COPE_FIELDS if getattr(loc, f) is None]
            precise = loc.geocode is not None and loc.geocode.resolution.value in good
            if gaps and precise:
                building = c.building_data.lookup(loc.Latitude, loc.Longitude)
                for attr, field in BUILDING_DATA_MAP.items():
                    if building is not None and field in gaps and getattr(building, attr) is not None:
                        fill(loc, field, getattr(building, attr), c.building_data.name, 0.7,
                             "gap filled from third-party building data")
                if building is None:
                    audit.append(AuditEntry(input_ref=f"loc:{loc.LocNumber}", confidence=1.0,
                                            rule_or_model=c.building_data.name,
                                            decision=f"no third-party building record; gaps remain: {gaps}"))
            elif gaps:
                audit.append(AuditEntry(input_ref=f"loc:{loc.LocNumber}", confidence=1.0,
                                        rule_or_model="rule:building_data_needs_street_level",
                                        decision=f"location not at street level; gaps remain: {gaps}"))

            # 4. hazard data and H3
            if loc.Latitude is not None and loc.Longitude is not None:
                attached = []
                for provider in (c.nathan, c.athenium):
                    scores = provider.lookup(loc.Latitude, loc.Longitude)
                    for name, value in (scores or {}).items():
                        loc.hazard[f"{provider.name}.{name}"] = SourcedValue(value=value, source=provider.name)
                    attached.append(f"{provider.name} ({len(scores)} scores)" if scores else f"{provider.name} (no coverage)")
                resolution = ctx.settings.geocoding.h3_resolution
                loc.h3_cell = c.h3.cell(loc.Latitude, loc.Longitude, resolution)
                loc.sources["h3_cell"] = SourcedValue(value=loc.h3_cell, source=f"h3:res{resolution}")
                audit.append(AuditEntry(input_ref=f"loc:{loc.LocNumber}", confidence=1.0, rule_or_model="hazard_lookup",
                                        decision=f"attached {', '.join(attached)}; H3 cell {loc.h3_cell}"))

            # 5. year-on-year comparison
            if before is not None and loc.LocNumber not in seen_yoy:
                seen_yoy.add(loc.LocNumber)
                if item := self._compare(ctx, loc, before, insured, prior.year, out, audit):
                    exceptions.append(item)

        if prior:
            current_ids = {loc.LocNumber for loc in out.locations}
            out.new_locations = sorted(current_ids - set(prior_by_loc), key=_natural)
            out.dropped_locations = sorted(set(prior_by_loc) - current_ids, key=_natural)
            audit.append(AuditEntry(
                input_ref=f"prior_year:{prior.year}", confidence=1.0, rule_or_model="rule:prior_year_comparison",
                decision=(f"{len(out.new_locations)} new location(s), {len(out.dropped_locations)} dropped, "
                          f"{len(out.yoy_flags)} large TIV change(s)"),
                detail={"new": out.new_locations, "dropped": out.dropped_locations},
            ))

        weights = [fx.to_base(loc.tiv, loc.LocCurrency) for loc in out.locations]
        out.completeness_after = round(completeness(out.locations), 4)
        out.completeness_tiv_weighted = round(completeness(out.locations, weights), 4)
        n = len(out.locations) or 1
        out.completeness_by_field = {
            f: round(sum(getattr(loc, f) is not None for loc in out.locations) / n, 4) for f in COMPLETENESS_FIELDS}
        audit.append(AuditEntry(
            input_ref="enrichment", confidence=1.0, rule_or_model="rule:completeness_score",
            decision=(f"completeness {out.completeness_before:.1%} -> {out.completeness_after:.1%} "
                      f"({out.completeness_tiv_weighted:.1%} TIV-weighted)"),
            detail={"by_field": out.completeness_by_field, "filled_by_source": out.filled_by_source},
        ))
        return finish(out, audit, exceptions)

    def _compare(self, ctx: AgentContext, loc: Location, before: PriorYearLocation, insured: str, year: int,
                 out: EnrichedLocations, audit: list[AuditEntry]) -> ExceptionItem | None:
        attrs = before.attributes
        prior_tiv = sum(float(attrs.get(f) or 0.0) for f in TIV_FIELDS)
        if prior_tiv <= 0 or (attrs.get("LocCurrency") or loc.LocCurrency) != loc.LocCurrency:
            return None
        current = loc.tiv  # a repeated LocNumber is compared once; duplicates are step 8's test
        change = (current - prior_tiv) / prior_tiv
        threshold = ctx.settings.thresholds.yoy_tiv_change_flag
        if abs(change) <= threshold:
            return None
        flag = YoYFlag(loc_number=loc.LocNumber, prior_tiv=prior_tiv, current_tiv=current,
                       change=round(change, 4), currency=loc.LocCurrency)
        out.yoy_flags.append(flag)
        subject = f"yoy:{loc.LocNumber}"
        key = f"{insured}|{loc.LocNumber}|{prior_tiv:.0f}|{current:.0f}"
        fix = ctx.correction_for(subject)
        learned = None if fix else ctx.learned.lookup("yoy_change", key)
        answer = str(fix.value if fix else learned.value if learned else "").strip().lower()
        who = f"analyst:{fix.analyst}" if fix else f"learned_mapping:{learned.id}" if learned else ""
        note = ""
        if answer == "confirm":
            flag.resolution = "confirmed"
            audit.append(AuditEntry(input_ref=subject, confidence=1.0, rule_or_model=who,
                                    decision=f"TIV change of {change:+.0%} vs {year} confirmed"))
            return None
        if answer == "use_prior":
            flag.resolution = "reverted to prior"
            for field in TIV_FIELDS:
                value = float(attrs.get(field) or 0.0)
                setattr(loc, field, value)
                loc.sources[field] = SourcedValue(value=value, source=f"prior_year:{year}", confidence=1.0)
            audit.append(AuditEntry(input_ref=subject, confidence=1.0, rule_or_model=who,
                                    decision=f"TIV change of {change:+.0%} rejected; prior-year values used"))
            return None
        if answer:
            note = f"'{answer}' is not a valid answer. "
        audit.append(AuditEntry(input_ref=subject, confidence=1.0, rule_or_model="rule:yoy_tiv_change",
                                decision=f"TIV changed {change:+.0%} vs {year} (threshold {threshold:.0%}); flagged"))
        return ExceptionItem(
            code="LARGE_YOY_CHANGE", subject=subject,
            reason=(f"{note}Location {loc.LocNumber} TIV changed {change:+.0%} against {year} "
                    f"({loc.LocCurrency} {prior_tiv:,.0f} -> {current:,.0f}; threshold {threshold:.0%}). "
                    "Correct with 'confirm' to accept this year's values or 'use_prior' to keep last year's."),
            payload={"loc_number": loc.LocNumber, "prior_tiv": prior_tiv, "current_tiv": current,
                     "change": round(change, 4), "currency": loc.LocCurrency},
            priority=abs(ctx.settings.fx.to_base(current - prior_tiv, loc.LocCurrency)),
            learn=LearnSpec(kind="yoy_change", key=key),
        )


def _natural(text: str):
    return (0, int(text)) if text.isdigit() else (1, text)
