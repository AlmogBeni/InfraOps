"""Provisioning domain service — submission, retry and cancellation rules."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.core.logging import get_logger
from app.models.jobs import TERMINAL_JOB_STATUSES, JobStatus, ProvisioningJob, StepStatus
from app.models.user import User
from app.repositories.jobs import JobRepository
from app.schemas.provisioning import ProvisioningRequest
from app.workers.events import JobEventPublisher
from app.workers.state_machine import STAGES_BY_KEY

log = get_logger(__name__)

# A lightweight API-side publisher for immediate queue/cancel notifications.
_api_publisher = JobEventPublisher()


async def submit_provisioning(
    db: AsyncSession,
    *,
    user: User | None,
    request: ProvisioningRequest,
    idempotency_key: str | None,
    source_ip: str | None,
) -> tuple[ProvisioningJob, bool]:
    """Create a provisioning job; returns ``(job, created)``.

    Duplicate protection: identical idempotency keys return the original job;
    concurrent active jobs for the same VM name are rejected.
    """
    repo = JobRepository(db)

    if idempotency_key:
        existing = await repo.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing, False

    if await repo.has_active_job_for_vm(request.vm.name):
        raise ConflictError(
            f"An active provisioning job for '{request.vm.name}' already exists.",
            details={"vm_name": request.vm.name},
        )

    job = await repo.create_job(
        vm_name=request.vm.name,
        requested_by_user_id=user.id if user else None,
        idempotency_key=idempotency_key,
        request_payload=request.model_dump(mode="json"),
    )
    await AuditRecorder(db).record(
        AuditAction.PROVISIONING_REQUEST_CREATED,
        user=user,
        resource_type="provisioning_job",
        resource_name=request.vm.name,
        job_id=job.id,
        result="queued",
        source_ip=source_ip,
        details={
            "source_type": request.source_type.value,
            "vcenter_id": str(request.compute.vcenter_id),
            "cluster_id": request.compute.cluster_id,
            "template_id": request.guest.template_id,
            "network_mode": request.network.mode.value,
        },
    )
    await db.flush()
    await _api_publisher.publish_stage(
        str(job.id), stage=None, status="QUEUED", progress=0,
        message=f"Provisioning request for {request.vm.name} queued.",
    )
    return job, True


async def retry_stages(
    db: AsyncSession,
    *,
    user: User | None,
    job: ProvisioningJob,
    stage_key: str | None,
    source_ip: str | None,
) -> list[str]:
    """Reset failed stage(s) to PENDING and requeue the job."""
    if job.status not in (JobStatus.FAILED, JobStatus.PARTIALLY_COMPLETED, JobStatus.CANCELLED):
        raise DomainValidationError(
            f"Jobs in status '{job.status.value}' cannot be retried.",
            details={"status": job.status.value},
        )

    steps_by_key = {step.stage_key: step for step in job.steps}
    stage_keys: list[str] | None = None
    if stage_key is not None:
        definition = STAGES_BY_KEY.get(stage_key)
        step = steps_by_key.get(stage_key)
        if definition is None or step is None:
            raise NotFoundError(f"Unknown stage '{stage_key}' for this job.")
        if not (definition.retryable and step.retryable):
            raise DomainValidationError(f"Stage '{stage_key}' is not retryable.")
        if step.status not in (StepStatus.FAILED, StepStatus.CANCELLED):
            raise DomainValidationError(
                f"Stage '{stage_key}' is '{step.status.value}' — only failed stages can be retried."
            )
        stage_keys = [stage_key]

    repo = JobRepository(db)
    reset = await repo.reset_for_retry(job, stage_keys)
    if not reset:
        raise DomainValidationError("No eligible failed stages were found to retry.")

    await AuditRecorder(db).record(
        AuditAction.JOB_STAGE_RETRIED,
        user=user,
        resource_type="provisioning_job",
        resource_name=job.vm_name,
        job_id=job.id,
        result="requeued",
        source_ip=source_ip,
        details={"stages": reset},
    )
    await _api_publisher.publish_stage(
        str(job.id), stage=None, status="QUEUED", progress=job.progress,
        message=f"Retrying stage(s): {', '.join(reset)}",
    )
    return reset


async def cancel_job(
    db: AsyncSession,
    *,
    user: User | None,
    job: ProvisioningJob,
    source_ip: str | None,
) -> None:
    if job.status in TERMINAL_JOB_STATUSES:
        raise ConflictError(
            f"Job is already '{job.status.value}' and cannot be cancelled.",
            details={"status": job.status.value},
        )
    repo = JobRepository(db)
    await repo.request_cancel(job)
    await AuditRecorder(db).record(
        AuditAction.JOB_CANCELLED,
        user=user,
        resource_type="provisioning_job",
        resource_name=job.vm_name,
        job_id=job.id,
        result="cancel_requested",
        source_ip=source_ip,
    )
    await _api_publisher.publish_stage(
        str(job.id), stage=job.current_stage, status="CANCELLING",
        progress=job.progress, message="Cancellation requested.",
    )
