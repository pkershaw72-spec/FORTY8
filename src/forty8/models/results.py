"""Cat-model job, loss results and pricing models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobStatus(BaseModel):
    job_id: str
    state: JobState
    message: str = ""


class LossResults(BaseModel):
    job_id: str
    currency: str
    aal: float
    oep_100: float
    oep_250: float


class PricingSubmission(BaseModel):
    account_number: str
    insured: str | None
    currency: str
    aal: float
    oep_100: float
    oep_250: float
    total_tiv: float


class PricingResponse(BaseModel):
    accepted: bool
    reference: str | None = None
    reason: str = ""


class SurveyRecord(BaseModel):
    """Verified engineering survey data for one location."""

    loc_number: str
    survey_date: str
    verified: bool = True
    attributes: dict[str, int | float | str]


class PriorYearLocation(BaseModel):
    loc_number: str
    attributes: dict[str, int | float | str | None]


class PriorYearSubmission(BaseModel):
    insured: str
    year: int
    locations: list[PriorYearLocation]


class LLMResponse(BaseModel):
    data: dict
    model: str
