"""Step 9 - Policy mapping. Applies the coded slip terms to the locations, including sublimits
by zone, and assembles the model-ready exposure set. Runs only once the slip lane has passed.
Exception path: a missing term goes to review - a required account term with no value, a
sublimit whose zone wording has no definition, or a location whose zone cannot be determined."""

from __future__ import annotations

from forty8.agents.base import AgentContext
from forty8.agents.util import finish, to_float
from forty8.models.common import AgentResult, AuditEntry, ExceptionItem, LearnSpec
from forty8.models.exposure import Account, ExposureSet, Location, LocationTerms, Sublimit
from forty8.models.pipeline import AuditGateOutput, CodedSlip, PolicyMapping
from forty8.reference.zones import ZONE_BY_KEY, resolve_zone

REQUIRED = ("currency", "inception", "expiry", "limit", "deductible", "perils")


class PolicyMappingAgent:
    key = "policy_mapping"
    output_model = PolicyMapping

    def run(self, ctx: AgentContext) -> AgentResult:
        gate: AuditGateOutput = ctx.input("audit_gate")
        slip: CodedSlip = ctx.input("contract")
        audit: list[AuditEntry] = []
        exceptions: list[ExceptionItem] = []
        locations = [loc.model_copy(deep=True) for loc in gate.locations]

        for name in REQUIRED:
            if slip.value(name) in (None, "", []):
                exceptions.append(ExceptionItem(
                    code="MISSING_POLICY_TERM", subject=f"term:{name}", priority=3.0,
                    reason=f"The coded slip has no value for '{name}', so it cannot be applied to the locations. "
                           "The slip needs to be corrected and re-run.", payload={"term": name}))
        account = Account(
            AccNumber=str(slip.value("umr") or ctx.run_id), AccName=slip.value("insured"),
            AccCurrency=slip.value("currency"), PolInceptionDate=slip.value("inception"),
            PolExpiryDate=slip.value("expiry"), PolPerilsCovered=list(slip.value("perils") or []),
            PolLimit=to_float(slip.value("limit")), PolDed=to_float(slip.value("deductible")),
        )
        audit.append(AuditEntry(
            input_ref="slip", confidence=slip.overall_confidence, rule_or_model="rule:account_from_slip",
            decision=(f"account {account.AccNumber}: limit {account.PolLimit}, deductible {account.PolDed}, "
                      f"perils {account.PolPerilsCovered}, {account.PolInceptionDate} to {account.PolExpiryDate}"),
        ))

        out = PolicyMapping(exposure=ExposureSet(account=account, locations=locations))
        for term in slip.sublimits:
            value = term.value or {}
            zone_text = str(value.get("zone_text", ""))
            subject = f"zone:{zone_text}"
            key, method = resolve_zone(zone_text), "rule:zone_alias"
            if fix := ctx.correction_for(subject):
                key, method = resolve_zone(str(fix.value)), f"analyst:{fix.analyst}"
            elif key is None and (learned := ctx.learned.lookup("zone", zone_text)):
                key, method = resolve_zone(str(learned.value)), f"learned_mapping:{learned.id}"
            if key is None:
                note = f"'{fix.value}' is not a defined zone. " if fix else ""
                exceptions.append(ExceptionItem(
                    code="UNDEFINED_SUBLIMIT_ZONE", subject=subject, priority=float(value.get("limit") or 0.0),
                    reason=(f"{note}The {value.get('peril')} sublimit of {value.get('limit'):,.0f} applies to "
                            f"'{zone_text}', which is not a defined zone. Correct with one of: "
                            f"{', '.join(ZONE_BY_KEY)}."),
                    payload={"term": term.name, "zone_text": zone_text, "source_text": term.source_text,
                             "zones": {k: z.description for k, z in ZONE_BY_KEY.items()}},
                    learn=LearnSpec(kind="zone", key=zone_text),
                ))
                continue
            out.zone_resolution[zone_text] = key
            account.sublimits.append(Sublimit(peril=value["peril"], zone=key, limit=float(value["limit"]),
                                              zone_text=zone_text, basis=value.get("basis", "occurrence")))
            audit.append(AuditEntry(input_ref=f"sublimit:{zone_text}", confidence=1.0, rule_or_model=method,
                                    decision=f"zone wording '{zone_text}' resolved to zone '{key}'"))

        used = {s.zone for s in account.sublimits}
        for loc in locations:
            zones = self._zones_for(ctx, loc, used, exceptions, audit)
            loc.zones = sorted(zones)
            if account.PolLimit is not None and account.PolDed is not None and account.PolPerilsCovered:
                loc.terms = LocationTerms(
                    perils=account.PolPerilsCovered, limit=account.PolLimit, deductible=account.PolDed,
                    sublimits=[s for s in account.sublimits if s.zone in zones],
                )
        for zone in sorted(used):
            out.locations_by_zone[zone] = sum(1 for loc in locations if zone in loc.zones)
        with_terms = sum(1 for loc in locations if loc.terms is not None)
        audit.append(AuditEntry(
            input_ref="exposure", confidence=1.0, rule_or_model="rule:terms_on_all_locations",
            decision=f"terms applied to {with_terms} of {len(locations)} locations; sublimit zones {out.locations_by_zone}",
        ))
        return finish(out, audit, exceptions)

    def _zones_for(self, ctx: AgentContext, loc: Location, used: set[str], exceptions: list[ExceptionItem],
                   audit: list[AuditEntry]) -> set[str]:
        needs_hazard = sorted(z for z in used if ZONE_BY_KEY[z].needs_hazard)
        if needs_hazard and not loc.hazard:  # not geocoded, so hazard-based zones cannot be tested
            subject = f"zones:{loc.LocNumber}"
            fix = ctx.correction_for(subject)
            if fix is not None:
                answer = [z.strip() for z in str(fix.value).split(",") if z.strip() and z.strip().lower() != "none"]
                if all(z in ZONE_BY_KEY for z in answer):
                    audit.append(AuditEntry(input_ref=subject, confidence=1.0, rule_or_model=f"analyst:{fix.analyst}",
                                            decision=f"zones set to {answer or 'none'}"))
                    simple = {z for z in used if not ZONE_BY_KEY[z].needs_hazard and ZONE_BY_KEY[z].test(loc)}
                    return simple | set(answer)
            exceptions.append(ExceptionItem(
                code="ZONE_UNDETERMINED", subject=subject, priority=ctx.settings.fx.to_base(loc.tiv, loc.LocCurrency),
                reason=(f"Location {loc.LocNumber} has no hazard data (not geocoded), so it cannot be tested "
                        f"against zone(s) {', '.join(needs_hazard)}. Correct with the zones that apply, "
                        "comma-separated, or 'none'."),
                payload={"loc_number": loc.LocNumber, "zones": needs_hazard},
            ))
            return set()
        return {z for z in used if ZONE_BY_KEY[z].test(loc)}
