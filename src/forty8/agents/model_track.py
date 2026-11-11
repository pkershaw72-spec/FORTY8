"""Step 10 - Model and track. Submits the exposure set to the cat model and tracks the job to
completion. A failed or timed-out job is retried (count from config).
Exception path: still failing after the retries goes to review; the analyst answers 'retry'."""

from __future__ import annotations

import time

from forty8.agents.base import AgentContext
from forty8.agents.util import finish
from forty8.models.common import AgentResult, AuditEntry, ExceptionItem
from forty8.models.exposure import ExposureSet
from forty8.models.pipeline import ModelOutput, PolicyMapping
from forty8.models.results import JobState


def in_account_currency(exposure: ExposureSet, fx) -> ExposureSet:
    """Model-ready copy with every location's values converted to the account currency."""
    currency = (exposure.account.AccCurrency or fx.base).upper()
    per_base = fx.rates_to_base.get(currency, 1.0)
    converted = exposure.model_copy(deep=True)
    for loc in converted.locations:
        rate = fx.rates_to_base.get((loc.LocCurrency or currency).upper(), 1.0) / per_base
        loc.BuildingTIV, loc.ContentsTIV, loc.BITIV = (round(v * rate, 2) for v in (loc.BuildingTIV, loc.ContentsTIV, loc.BITIV))
        loc.LocCurrency = currency
    return converted


class ModelTrackAgent:
    key = "model_track"
    output_model = ModelOutput

    def run(self, ctx: AgentContext) -> AgentResult:
        mapping: PolicyMapping = ctx.input("policy_mapping")
        cfg, model = ctx.settings.model, ctx.connectors.cat_model
        exposure = in_account_currency(mapping.exposure, ctx.settings.fx)
        currency = exposure.account.AccCurrency or ctx.settings.fx.base
        audit = [AuditEntry(
            input_ref=f"account:{exposure.account.AccNumber}", confidence=1.0, rule_or_model="rule:fx_static_table",
            decision=f"exposure prepared: {len(exposure.locations)} locations, TIV {currency} {exposure.total_tiv:,.0f}",
        )]
        attempts, last_error = 0, ""
        for attempt in range(1, cfg.max_retries + 2):  # first try plus the configured retries
            attempts = attempt
            job_id = model.submit(exposure)
            deadline = time.monotonic() + cfg.timeout_seconds
            state, message = JobState.RUNNING, ""
            while True:
                status = model.status(job_id)
                state, message = status.state, status.message
                if state in (JobState.COMPLETED, JobState.FAILED):
                    break
                if time.monotonic() >= deadline:
                    state, message = JobState.FAILED, f"timed out after {cfg.timeout_seconds:g}s"
                    break
                time.sleep(cfg.poll_interval_seconds)
            if state == JobState.COMPLETED:
                results = model.results(job_id)
                audit.append(AuditEntry(
                    input_ref=f"job:{job_id}", confidence=1.0, rule_or_model=model.name,
                    decision=(f"job completed on attempt {attempt}: AAL {results.aal:,.0f}, OEP100 "
                              f"{results.oep_100:,.0f}, OEP250 {results.oep_250:,.0f} {results.currency}"),
                ))
                out = ModelOutput(job_id=job_id, attempts=attempt, model=model.name, results=results,
                                  total_tiv=round(exposure.total_tiv, 2), currency=currency)
                return finish(out, audit, [])
            last_error = message or "job failed"
            more = attempt <= cfg.max_retries
            audit.append(AuditEntry(
                input_ref=f"job:{job_id}", confidence=1.0, rule_or_model=model.name,
                decision=f"job failed on attempt {attempt}: {last_error}" + ("; retrying" if more else "; retries exhausted"),
            ))
        exception = ExceptionItem(
            code="MODEL_JOB_FAILED", subject="model_job", priority=exposure.total_tiv,
            reason=(f"The cat model job failed {attempts} times (last error: {last_error}). "
                    "Correct with 'retry' once the model is available."),
            payload={"attempts": attempts, "last_error": last_error, "model": model.name},
        )
        return finish(None, audit, [exception])
