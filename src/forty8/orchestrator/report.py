"""Run metrics report (markdown) and a summary across runs."""

from __future__ import annotations

from collections import Counter

from forty8.models.common import ReviewStatus, RunStatus
from forty8.orchestrator.engine import Orchestrator
from forty8.orchestrator.metrics import run_metrics


def _table(headers: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    return lines + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    return f"{seconds:.2f}s" if seconds < 120 else f"{seconds / 60:.1f} min"


def render_report(orc: Orchestrator, run_id: str) -> str:
    run, m = orc.store.get_run(run_id), run_metrics(orc.store, run_id)
    target = orc.settings.metrics.target_minutes
    items = orc.store.review_items(run_id=run_id)
    events = orc.audit.for_run(run_id)
    learned = sum(1 for e in events if e.rule_or_model.startswith("learned_mapping:"))
    lines = [f"# Forty8 run report - {run_id}", ""]
    lines += _table(["Measure", "Value"], [
        ["Status", m.status.value],
        ["Broker", run["broker"]],
        ["Straight-through", "yes" if m.straight_through else "no"],
        ["Analyst touches", m.analyst_touches],
        ["Exceptions raised", m.exceptions_raised],
        ["Learned mappings reused", learned],
        ["Agent processing time", _duration(m.elapsed_seconds)],
        ["Wall clock, including analyst waiting", _duration(m.wall_clock_seconds)],
        [f"Against the {target:g}-minute target",
         "n/a (run not complete)" if m.wall_clock_seconds is None
         else ("within target" if m.wall_clock_seconds <= target * 60 else "over target")],
        ["Audit log entries", len(events)],
    ])
    lines += ["", "## Steps", ""]
    lines += _table(["Step", "Agent", "Status", "Attempts", "Seconds", "Exceptions", "Analyst touches"], [
        [s.number, s.name, s.status.value, s.attempts, f"{s.elapsed_seconds:.3f}", s.exceptions_raised, s.analyst_touches]
        for s in m.steps])

    if items:
        by_code = Counter(i.code for i in items)
        resolved = Counter(i.code for i in items if i.status == ReviewStatus.RESOLVED)
        lines += ["", "## Exceptions", ""]
        lines += _table(["Exception", "Step", "Raised", "Resolved"], [
            [code, next(i.step for i in items if i.code == code), n, resolved[code]] for code, n in sorted(by_code.items())])

    outcome: list[list] = []
    if (slip := orc.output(run_id, "contract")) is not None:
        outcome.append(["Slip confidence (minimum term)", f"{slip.overall_confidence:.0%} (threshold {slip.threshold:.0%})"])
    if (geo := orc.output(run_id, "geocoding")) is not None:
        outcome.append(["TIV at street level or better", f"{geo.tiv_street_share:.1%} (threshold {geo.threshold:.0%})"])
        outcome.append(["Geocoding providers", ", ".join(f"{k}: {v}" for k, v in geo.by_provider.items())])
    if (enriched := orc.output(run_id, "enrichment")) is not None:
        outcome.append(["Completeness", f"{enriched.completeness_before:.1%} -> {enriched.completeness_after:.1%}"])
        outcome.append(["Large year-on-year changes", len(enriched.yoy_flags)])
    if (gate := orc.output(run_id, "audit_gate")) is not None:
        outcome.append(["Data quality tests", ", ".join(
            f"{t.name}: {'pass' if t.passed and not t.waived else 'accepted by analyst' if t.passed else 'FAIL'}"
            for t in gate.tests)])
    if (policy := orc.output(run_id, "policy_mapping")) is not None:
        outcome.append(["Locations in exposure set", len(policy.exposure.locations)])
    if (model := orc.output(run_id, "model_track")) is not None:
        r = model.results
        outcome.append(["Total insured value", f"{model.currency} {model.total_tiv:,.0f}"])
        outcome.append(["Model job", f"{model.job_id} on {model.model}, {model.attempts} attempt(s)"])
        outcome.append(["AAL", f"{r.currency} {r.aal:,.0f}"])
        outcome.append(["OEP 100-year", f"{r.currency} {r.oep_100:,.0f}"])
        outcome.append(["OEP 250-year", f"{r.currency} {r.oep_250:,.0f}"])
    if (pricing := orc.output(run_id, "pricing")) is not None:
        outcome.append(["Pricing tool", f"accepted, reference {pricing.response.reference}" if pricing.response.accepted
                        else f"rejected: {pricing.response.reason}"])
    if outcome:
        lines += ["", "## Outcome", ""] + _table(["Item", "Result"], outcome)
    return "\n".join(lines) + "\n"


def summary_across_runs(orc: Orchestrator) -> dict:
    metrics = [run_metrics(orc.store, r["run_id"]) for r in orc.store.list_runs()]
    done = [m for m in metrics if m.status == RunStatus.COMPLETED]
    return {
        "runs": len(metrics),
        "completed": len(done),
        "straight_through": sum(m.straight_through for m in done),
        "straight_through_rate": round(sum(m.straight_through for m in done) / len(done), 3) if done else None,
        "average_analyst_touches": round(sum(m.analyst_touches for m in done) / len(done), 2) if done else None,
        "average_processing_seconds": round(sum(m.elapsed_seconds for m in done) / len(done), 3) if done else None,
    }
