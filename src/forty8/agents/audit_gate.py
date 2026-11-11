"""Step 8 - Audit gate. Runs the data quality tests on the enriched locations:
no duplicates, year built in range, construction vs storeys plausible, TIV reconciles to the
SoV (row totals and stated tab totals), and at least 80% of TIV at street level or better.
Exception path: any failure goes to review; the analyst fixes the data or accepts the
difference, and every test is run again on resume."""

from __future__ import annotations

from datetime import date
from typing import Any

from forty8.agents.base import AgentContext
from forty8.agents.geocoding import query_for
from forty8.agents.util import finish, to_int
from forty8.models.common import AgentResult, AuditEntry, ExceptionItem
from forty8.models.exposure import Location, SourcedValue
from forty8.models.pipeline import AuditGateOutput, CleanTable, EnrichedLocations, MappedTable, QualityTest
from forty8.reference.gazetteer import fold

TIV_COMPONENTS = ("BuildingTIV", "ContentsTIV", "BITIV")
ACCEPT = "accept"


class AuditGateAgent:
    key = "audit_gate"
    output_model = AuditGateOutput

    def run(self, ctx: AgentContext) -> AgentResult:
        return _GateRun(ctx).run()


class _GateRun:
    """One execution of the gate; holds the working copy of the locations."""

    def __init__(self, ctx: AgentContext):
        self.ctx, self.fx, self.cfg = ctx, ctx.settings.fx, ctx.settings.audit

    def run(self) -> AgentResult:
        ctx = self.ctx
        enriched: EnrichedLocations = ctx.input("enrichment")
        self.locations = [loc.model_copy(deep=True) for loc in enriched.locations]
        self.audit: list[AuditEntry] = []
        self.exceptions: list[ExceptionItem] = []
        tests = [self._duplicates(), self._year_built(), self._construction_storeys(), self._row_totals(),
                 self._tab_totals(), self._geocode_tiv()]
        for test in tests:
            self.audit.append(AuditEntry(
                input_ref=f"test:{test.name}", confidence=1.0, rule_or_model=f"test:{test.name}",
                decision=("PASS" if test.passed else "FAIL") + f" - {test.detail}",
            ))
        return finish(AuditGateOutput(locations=self.locations, tests=tests), self.audit, self.exceptions)

    # ---- helpers
    def _base(self, loc: Location) -> float:
        return self.fx.to_base(loc.tiv, loc.LocCurrency)

    def _raise(self, code: str, subject: str, reason: str, priority: float, **payload: Any) -> None:
        self.exceptions.append(ExceptionItem(code=code, subject=subject, reason=reason, payload=payload, priority=priority))

    def _waived(self, subject: str, answers: tuple[str, ...] = (ACCEPT,)) -> bool:
        fix = self.ctx.correction_for(subject)
        if fix is not None and str(fix.value).strip().lower() in answers:
            self.audit.append(AuditEntry(input_ref=subject, confidence=1.0, rule_or_model=f"analyst:{fix.analyst}",
                                         decision=f"failure accepted by analyst ('{fix.value}')"))
            return True
        return False

    def _result(self, name: str, failures: int, waived: int, ok: str, bad: str) -> QualityTest:
        open_failures = failures - waived
        detail = ok if failures == 0 else bad + (f" ({waived} fixed or accepted by analyst)" if waived else "")
        return QualityTest(name=name, passed=open_failures == 0, detail=detail, failures=failures, waived=waived)

    def _set(self, loc: Location, field: str, value: Any, analyst: str) -> None:
        self.audit.append(AuditEntry(input_ref=f"loc:{loc.LocNumber}:{field}", confidence=1.0,
                                     rule_or_model=f"analyst:{analyst}",
                                     decision=f"{field} corrected from {getattr(loc, field)} to {value}"))
        setattr(loc, field, value)
        loc.sources[field] = SourcedValue(value=value, source=f"analyst:{analyst}", confidence=1.0)

    # ---- tests
    def _duplicates(self) -> QualityTest:
        groups: dict[str, list[Location]] = {}
        for loc in self.locations:
            groups.setdefault(f"LocNumber {loc.LocNumber}", []).append(loc)
        singles = [locs[0] for locs in groups.values() if len(locs) == 1]
        by_address: dict[tuple, list[Location]] = {}
        for loc in singles:  # same address and same values under different location numbers
            if loc.StreetAddress:
                by_address.setdefault((fold(query_for(loc).one_line()), round(loc.tiv, 2)), []).append(loc)
        for locs in by_address.values():
            if len(locs) > 1:
                groups["LocNumbers " + "+".join(l.LocNumber for l in locs)] = locs
        failures = waived = 0
        for label, locs in groups.items():
            if len(locs) < 2:
                continue
            failures += 1
            subject = "duplicate:" + label.split(" ", 1)[1]
            fix = self.ctx.correction_for(subject)
            answer = str(fix.value).strip().lower() if fix else ""
            if answer == "remove":
                for extra in locs[1:]:
                    self.locations.remove(extra)
                self.audit.append(AuditEntry(
                    input_ref=subject, confidence=1.0, rule_or_model=f"analyst:{fix.analyst}",
                    decision=f"removed {len(locs) - 1} duplicate row(s), kept the first"))
                waived += 1
            elif answer == "keep" and self._waived(subject, ("keep",)):
                waived += 1
            else:
                rows = [f"{l.lineage.tab} row {l.lineage.source_row}" for l in locs if l.lineage]
                self._raise("AUDIT_DUPLICATE", subject,
                            f"{label} appears {len(locs)} times ({'; '.join(rows)}). "
                            "Correct with 'remove' to keep one row, or 'keep' if they are separate locations.",
                            self._base(locs[0]), locations=[l.LocNumber for l in locs], rows=rows)
        return self._result("no_duplicates", failures, waived, "no duplicate locations",
                            f"{failures} duplicate group(s)")

    def _year_built(self) -> QualityTest:
        low, high = self.cfg.year_built_min, date.today().year + self.cfg.year_built_max_offset_years
        failures = waived = 0
        for loc in self.locations:
            if loc.YearBuilt is None or low <= loc.YearBuilt <= high:
                continue
            failures += 1
            subject = f"year_built:{loc.LocNumber}"
            fix = self.ctx.correction_for(subject)
            year = to_int(fix.value) if fix else None
            if fix and str(fix.value).strip().lower() == "clear":
                self._set(loc, "YearBuilt", None, fix.analyst)
                waived += 1
            elif year is not None and low <= year <= high:
                self._set(loc, "YearBuilt", year, fix.analyst)
                waived += 1
            else:
                note = f"'{fix.value}' is not a year between {low} and {high}. " if fix else ""
                self._raise("AUDIT_YEAR_BUILT", subject,
                            f"{note}Location {loc.LocNumber} has year built {loc.YearBuilt}, outside {low}-{high}. "
                            "Correct with the right year, or 'clear' to leave it blank.",
                            self._base(loc), loc_number=loc.LocNumber, year_built=loc.YearBuilt)
        return self._result("year_built_in_range", failures, waived, f"all years built within {low}-{high}",
                            f"{failures} location(s) outside {low}-{high}")

    def _construction_storeys(self) -> QualityTest:
        limits = self.cfg.max_storeys_by_construction
        failures = waived = 0
        for loc in self.locations:
            def implausible() -> bool:
                limit = limits.get(str(loc.ConstructionCode))
                return limit is not None and loc.NumberOfStoreys is not None and loc.NumberOfStoreys > limit
            if not implausible():
                continue
            failures += 1
            subject = f"plausibility:{loc.LocNumber}"
            fix = self.ctx.correction_for(subject)
            if fix and isinstance(fix.value, dict):
                for field in ("ConstructionCode", "NumberOfStoreys"):
                    if (value := to_int(fix.value.get(field))) is not None:
                        self._set(loc, field, value, fix.analyst)
            if not implausible() or self._waived(subject):
                waived += 1
                continue
            self._raise("AUDIT_CONSTRUCTION_STOREYS", subject,
                        f"Location {loc.LocNumber}: {loc.NumberOfStoreys} storeys is implausible for construction "
                        f"code {loc.ConstructionCode} (maximum {limits[str(loc.ConstructionCode)]}). Correct with "
                        "{\"NumberOfStoreys\": n} and/or {\"ConstructionCode\": code}, or 'accept'.",
                        self._base(loc), loc_number=loc.LocNumber, construction_code=loc.ConstructionCode,
                        storeys=loc.NumberOfStoreys)
        return self._result("construction_vs_storeys", failures, waived, "construction and storeys are plausible",
                            f"{failures} implausible location(s)")

    def _row_totals(self) -> QualityTest:
        tolerance = self.cfg.tiv_reconcile_tolerance
        failures = waived = tested = 0
        for loc in self.locations:
            reverted = any(s.source.startswith("prior_year") for f, s in loc.sources.items() if f in TIV_COMPONENTS)
            if loc.stated_tiv is None or reverted:
                continue
            tested += 1
            if abs(loc.stated_tiv - loc.tiv) <= tolerance * max(abs(loc.stated_tiv), 1.0):
                continue
            failures += 1
            subject = f"tiv_row:{loc.LocNumber}"
            if self._waived(subject):
                waived += 1
                continue
            self._raise("AUDIT_TIV_ROW", subject,
                        f"Location {loc.LocNumber}: the SoV total column says {loc.stated_tiv:,.0f} but building + "
                        f"contents + BI is {loc.tiv:,.0f}. Correct with 'accept' to use the components.",
                        self._base(loc), loc_number=loc.LocNumber, stated=loc.stated_tiv, components=loc.tiv)
        return self._result("tiv_row_totals", failures, waived,
                            f"{tested} row total(s) agree with their components" if tested else "no row total column to test",
                            f"{failures} row(s) where the total differs from its components")

    def _tab_totals(self) -> QualityTest:
        clean: CleanTable = self.ctx.input("cleaning")
        mapped: MappedTable = self.ctx.input("schema_mapping")
        target = {(m.tab, m.source_header): m.target_field for m in mapped.mappings if m.target_field}
        tolerance = self.cfg.tiv_reconcile_tolerance
        failures = waived = tested = 0
        for tab in dict.fromkeys(t.tab for t in clean.stated_totals):
            rows = [t for t in clean.stated_totals if t.tab == tab]
            totals = [t for t in rows if t.kind == "total"] or rows  # no TOTAL row: add up the subtotals
            stated = 0.0
            for row in totals:
                by_field = {target.get((tab, h)): v for h, v in row.values.items()}
                stated += by_field["stated_tiv"] if "stated_tiv" in by_field else \
                    sum(v for f, v in by_field.items() if f in TIV_COMPONENTS)
            if not stated:
                continue
            tested += 1
            pipeline = sum(loc.tiv for loc in self.locations if loc.lineage and loc.lineage.tab == tab)
            if abs(stated - pipeline) <= tolerance * abs(stated):
                continue
            failures += 1
            subject = f"tiv_reconcile:{tab}"
            if self._waived(subject):
                waived += 1
                continue
            self._raise("AUDIT_TIV_RECONCILE", subject,
                        f"Tab '{tab}': the SoV states a total of {stated:,.0f} but the locations add up to "
                        f"{pipeline:,.0f} (difference {pipeline - stated:+,.0f}). Correct with 'accept' if the "
                        "location values are right.",
                        abs(stated - pipeline), tab=tab, stated_total=stated, pipeline_total=pipeline)
        return self._result("tiv_reconciles_to_sov", failures, waived,
                            f"{tested} tab total(s) reconcile" if tested else "the SoV states no totals to reconcile",
                            f"{failures} tab(s) do not reconcile to the stated total")

    def _geocode_tiv(self) -> QualityTest:
        threshold = self.ctx.settings.thresholds.geocode_min_tiv_street_level
        good = set(self.ctx.settings.geocoding.street_level_or_better)
        total = sum(self._base(loc) for loc in self.locations)
        street = sum(self._base(loc) for loc in self.locations if loc.geocode and loc.geocode.resolution.value in good)
        share = street / total if total else 1.0
        failed = share < threshold
        waived = int(failed and self._waived("geocode_tiv"))
        if failed and not waived:
            self._raise("AUDIT_GEOCODE_TIV", "geocode_tiv",
                        f"Only {share:.1%} of TIV is at street level or better (threshold {threshold:.0%}). "
                        "Correct with 'accept' to proceed at this resolution.", total - street, share=share)
        return self._result("tiv_at_street_level", int(failed), waived,
                            f"{share:.1%} of TIV at street level or better (threshold {threshold:.0%})",
                            f"{share:.1%} of TIV at street level or better, below {threshold:.0%}")
