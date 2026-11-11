"""Mock cat model and pricing tool. Losses are deterministic and plausibly shaped, not real modelling."""

from __future__ import annotations

from forty8.models.exposure import ExposureSet
from forty8.models.results import JobState, JobStatus, LossResults, PricingResponse, PricingSubmission


class MockCatModel:
    name = "mock_cat_model"

    def __init__(self, options: dict | None = None):
        options = options or {}
        self.fail_first_n_jobs = int(options.get("fail_first_n_jobs", 0))
        self.polls_until_complete = int(options.get("polls_until_complete", 2))
        self._jobs: dict[str, dict] = {}

    def submit(self, exposure: ExposureSet) -> str:
        job_id = f"job-{len(self._jobs) + 1:04d}"
        self._jobs[job_id] = {"exposure": exposure, "polls": 0, "fails": len(self._jobs) < self.fail_first_n_jobs}
        return job_id

    def status(self, job_id: str) -> JobStatus:
        job = self._jobs[job_id]
        job["polls"] += 1
        if job["polls"] < self.polls_until_complete:
            return JobStatus(job_id=job_id, state=JobState.RUNNING)
        if job["fails"]:
            return JobStatus(job_id=job_id, state=JobState.FAILED, message="mock analysis failure")
        return JobStatus(job_id=job_id, state=JobState.COMPLETED)

    def results(self, job_id: str) -> LossResults:
        exposure: ExposureSet = self._jobs[job_id]["exposure"]
        gross_aal = 0.0
        for loc in exposure.locations:
            hazard = max((float(v.value) for v in loc.hazard.values() if isinstance(v.value, (int, float))), default=1.0)
            gross_aal += loc.tiv * 0.0004 * (1 + min(hazard, 4.0))
        limit = exposure.account.PolLimit or float("inf")
        return LossResults(
            job_id=job_id,
            currency=exposure.account.AccCurrency or "USD",
            aal=round(min(gross_aal, limit), 2),
            oep_100=round(min(gross_aal * 35, limit), 2),
            oep_250=round(min(gross_aal * 60, limit), 2),
        )


class MockPricingTool:
    def __init__(self, options: dict | None = None):
        self.reject = bool((options or {}).get("reject", False))
        self.received: list[PricingSubmission] = []

    def submit(self, submission: PricingSubmission) -> PricingResponse:
        if self.reject:
            return PricingResponse(accepted=False, reason="mock pricing tool configured to reject")
        if submission.aal <= 0 or submission.oep_250 < submission.oep_100:
            return PricingResponse(accepted=False, reason="loss metrics failed validation (AAL > 0, OEP250 >= OEP100)")
        self.received.append(submission)
        return PricingResponse(accepted=True, reference=f"PRC-{len(self.received):05d}")
