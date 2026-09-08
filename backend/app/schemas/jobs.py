"""Job and job-step response schemas."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from app.models.jobs import JobStatus, JobType, ProvisioningJob, StepStatus


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
    datacenter_id: str | None = None
    datacenter_name: str | None = None
    requested_by_username: str | None = None
    current_stage: str | None = None
    progress: int
    infrastructure_status: str
    guest_os_status: str
    vmware_tools_status: str
    guest_provisioning_status: str
    action_required: str | None = None
    error_summary: str | None = None
    cancel_requested: bool = False
    queued_at: dt.datetime | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    duration_seconds: float | None = None


def job_out(
    job: ProvisioningJob,
    usernames: dict | None = None,
) -> JobOut:
    """Serialize a job consistently for every endpoint that embeds ``JobOut``."""
    return JobOut(
        id=str(job.id),
        job_type=job.job_type,
        status=job.status,
        vm_name=job.vm_name,
        datacenter_id=job.datacenter_id,
        datacenter_name=job.datacenter_name,
        requested_by_username=(usernames or {}).get(job.requested_by_user_id),
        current_stage=job.current_stage,
        progress=job.progress,
        infrastructure_status=getattr(job, "infrastructure_status", "PENDING"),
        guest_os_status=getattr(job, "guest_os_status", "UNKNOWN"),
        vmware_tools_status=getattr(job, "vmware_tools_status", "UNKNOWN"),
        guest_provisioning_status=getattr(job, "guest_provisioning_status", "PENDING"),
        action_required=getattr(job, "action_required", None),
        error_summary=job.error_summary,
        cancel_requested=job.cancel_requested,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_seconds=job.duration_seconds,
    )


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
