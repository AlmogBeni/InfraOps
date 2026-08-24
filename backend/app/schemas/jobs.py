"""Job and job-step response schemas."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from app.models.jobs import JobStatus, JobType, StepStatus


class JobStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    stage_key: str
    name: str
    sequence: int
    status: StepStatus
    attempt: int
    max_attempts: int
    retryable: bool
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    output: str | None = None
    error_human: str | None = None
    error_technical: str | None = None
    artifacts: dict = {}


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_type: JobType
    status: JobStatus
    vm_name: str
    requested_by_username: str | None = None
    current_stage: str | None = None
    progress: int
    error_summary: str | None = None
    cancel_requested: bool = False
    queued_at: dt.datetime | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    duration_seconds: float | None = None


class JobDetailOut(JobOut):
    steps: list[JobStepOut] = []
    request_payload: dict | None = None


class JobListResponse(BaseModel):
    items: list[JobOut]
    total: int
    page: int
    page_size: int


class RetryRequest(BaseModel):
    """Retry a specific failed stage, or every failed stage when omitted."""

    model_config = ConfigDict(extra="forbid")

    stage_key: str | None = Field(default=None, max_length=80)
