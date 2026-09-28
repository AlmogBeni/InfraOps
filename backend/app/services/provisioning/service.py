"""Provisioning domain service — submission, retry and cancellation rules."""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.core.logging import get_logger
from app.models.jobs import TERMINAL_JOB_STATUSES, JobStatus, ProvisioningJob, StepStatus
from app.models.user import User
from app.repositories.jobs import JobRepository
from app.schemas.provisioning import IdentityPolicyVersion, IpMode, ProvisioningRequest
from app.services.provisioning.preflight import PreflightValidator
from app.services.vmware.base import VMwareService
from app.workers.events import JobEventPublisher
from app.workers.state_machine import STAGES_BY_KEY

log = get_logger(__name__)

# A lightweight API-side publisher for immediate queue/cancel notifications.
_api_publisher = JobEventPublisher()

_VM_NAME_INDEX = "uq_provisioning_jobs_active_vm_name"
_IPV4_INDEX = "uq_provisioning_jobs_reserved_ipv4"
_IDEMPOTENCY_CONSTRAINT = "uq_provisioning_jobs_idempotency_key"


def request_fingerprint(request: ProvisioningRequest) -> str:
    """Stable SHA-256 of the canonical request body."""
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reserved_ipv4_for(request: ProvisioningRequest) -> str | None:
    if request.network.mode == IpMode.STATIC and request.network.ipv4 is not None:
        return request.network.ipv4.address
    return None


def _violated_constraint(exc: IntegrityError) -> str:
    original = getattr(exc, "orig", None)
    name = getattr(original, "constraint_name", None)
    if name is None:
        cause = getattr(original, "__cause__", None)
        name = getattr(cause, "constraint_name", None)
    return str(name or original or exc)


def reservation_conflict(exc: IntegrityError, request_or_job) -> ConflictError | None:
    """Translate a unique-index violation into a user-facing conflict."""
    violated = _violated_constraint(exc)
    vm_name = getattr(getattr(request_or_job, "vm", None), "name", None) or getattr(
        request_or_job, "vm_name", "?"
    )
    if _VM_NAME_INDEX in violated:
        return ConflictError(
            f"An active provisioning job for '{vm_name}' already exists.",
            details={"vm_name": vm_name},
        )
    if _IPV4_INDEX in violated:
        return ConflictError(
            "Another active provisioning job has already reserved this IPv4 address.",
            details={"vm_name": vm_name},
        )
    return None


def _check_idempotent_replay(
    existing: ProvisioningJob, fingerprint: str, user: User | None
) -> ProvisioningJob:
    same_user = existing.requested_by_user_id == (user.id if user else None)
    if not same_user or (
        existing.request_fingerprint is not None and existing.request_fingerprint != fingerprint
    ):
        raise DomainValidationError(
            "This Idempotency-Key was already used for a different request.",
            details={"reason": "idempotency_key_reused"},
        )
    return existing


async def submit_provisioning(
    db: AsyncSession,
    *,
    user: User | None,
    request: ProvisioningRequest,
    idempotency_key: str | None,
    source_ip: str | None,
    vmware: VMwareService | None = None,
) -> tuple[ProvisioningJob, bool]:
    """Create a provisioning job; returns ``(job, created)``.

    Duplicate protection: an idempotency key replays the original job only for
    the same user and an identical body; concurrent active jobs for the same
    VM name or static IPv4 address are rejected (enforced by unique indexes).
    When ``vmware`` is supplied the full preflight runs and blocks submission.
    """
    if request.identity_policy_version != IdentityPolicyVersion.V2:
        raise DomainValidationError(
            "Identity policy v1 is reserved for stored legacy jobs; "
            "new provisioning submissions must use v2.",
            details={"identity_policy_version": request.identity_policy_version.value},
        )

    repo = JobRepository(db)
    fingerprint = request_fingerprint(request)

    if idempotency_key:
        existing = await repo.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return _check_idempotent_replay(existing, fingerprint, user), False

    if await repo.has_active_job_for_vm(request.vm.name):
        raise ConflictError(
            f"An active provisioning job for '{request.vm.name}' already exists.",
            details={"vm_name": request.vm.name},
        )
    reserved_ipv4 = reserved_ipv4_for(request)
    if reserved_ipv4 and await repo.has_ipv4_reservation(reserved_ipv4):
        raise ConflictError(
            "Another active provisioning job has already reserved this IPv4 address.",
            details={"vm_name": request.vm.name},
        )

    # The preflight is authoritative on the server: a client that skips
    # /validate (or validates an older body) can never queue a blocked request.
    if vmware is not None:
        report = await PreflightValidator(db, vmware).validate(request)
        if not report.ready:
            blocking = [
                {"code": check.code, "label": check.label, "detail": check.detail}
                for check in report.checks
                if check.status.value == "FAIL" and check.blocking
            ]
            await AuditRecorder(db).record(
                AuditAction.PROVISIONING_REQUEST_REJECTED,
                user=user,
                resource_type="provisioning_request",
                resource_name=request.vm.name,
                datacenter_id=request.compute.datacenter_id,
                result="blocked",
                source_ip=source_ip,
                details={"blocking_checks": [item["code"] for item in blocking]},
            )
            await db.commit()
            raise DomainValidationError(
                "The request did not pass server-side preflight validation.",
                details={"summary": report.summary, "blocking_checks": blocking},
            )

    try:
        async with db.begin_nested():
            job = await repo.create_job(
                vm_name=request.vm.name,
                datacenter_id=request.compute.datacenter_id,
                requested_by_user_id=user.id if user else None,
                idempotency_key=idempotency_key,
                request_payload=request.model_dump(mode="json"),
                request_fingerprint=fingerprint,
                reserved_ipv4=reserved_ipv4,
            )
    except IntegrityError as exc:
        # A concurrent request won the race; the partial unique indexes and the
        # idempotency-key constraint are the source of truth.
        if idempotency_key and _IDEMPOTENCY_CONSTRAINT in _violated_constraint(exc):
            existing = await repo.find_by_idempotency_key(idempotency_key)
            if existing is not None:
                return _check_idempotent_replay(existing, fingerprint, user), False
        conflict = reservation_conflict(exc, request)
        if conflict is not None:
            raise conflict from exc
        raise
    await AuditRecorder(db).record(
        AuditAction.PROVISIONING_REQUEST_CREATED,
        user=user,
        resource_type="provisioning_job",
        resource_name=request.vm.name,
        job_id=job.id,
        datacenter_id=request.compute.datacenter_id,
        result="queued",
        source_ip=source_ip,
        details={
            "source_type": request.source_type.value,
            "vcenter_id": str(request.compute.vcenter_id),
            "cluster_id": request.compute.cluster_id,
            "template_id": request.guest.template_id,
            "network_mode": request.network.mode.value,
            "computer_name": request.effective_computer_name or None,
            "requested_fqdn": request.effective_fqdn,
        },
        detail_text=(
            f"Provisioning request queued for '{request.effective_fqdn}'."
            if request.effective_fqdn
            else f"Provisioning request queued for '{request.vm.name}'."
        ),
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
    if job.status not in (
        JobStatus.FAILED,
        JobStatus.PARTIALLY_COMPLETED,
        JobStatus.ACTION_REQUIRED,
        JobStatus.CANCELLED,
        JobStatus.INTERRUPTED,
    ):
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
        if step.status not in (
            StepStatus.FAILED,
            StepStatus.CANCELLED,
            StepStatus.WAITING_FOR_PREREQUISITE,
        ):
            raise DomainValidationError(
                f"Stage '{stage_key}' is '{step.status.value}' — only failed stages can be retried."
            )
        stage_keys = [stage_key]

    if job.status == JobStatus.ACTION_REQUIRED:
        waiting = [
            step for step in sorted(job.steps, key=lambda item: item.sequence)
            if step.status == StepStatus.WAITING_FOR_PREREQUISITE
        ]
        resumed_step = (
            steps_by_key.get(stage_key) if stage_key is not None else (waiting[0] if waiting else None)
        )
        if resumed_step is not None and resumed_step.stage_key == "wait_for_guest_os":
            artifacts = dict(resumed_step.artifacts or {})
            artifacts["administrator_confirmed"] = True
            resumed_step.artifacts = artifacts

    repo = JobRepository(db)
    if await repo.has_active_job_for_vm(job.vm_name, exclude_job_id=job.id):
        raise ConflictError(
            f"Another active provisioning job for '{job.vm_name}' exists; it must finish first.",
            details={"vm_name": job.vm_name},
        )
    try:
        reset = await repo.reset_for_retry(job, stage_keys)
    except IntegrityError as exc:
        await db.rollback()
        conflict = reservation_conflict(exc, job)
        if conflict is not None:
            raise conflict from exc
        raise
    if not reset:
        raise DomainValidationError("No eligible failed or waiting stages were found to resume.")

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
    repo = JobRepository(db)
    if job.status == JobStatus.ACTION_REQUIRED:
        # A paused job may still hold temporary answer media (plaintext
        # password) on the datastore. Hand it to a worker, which applies the
        # cancellation and removes the media before finalising.
        job.cancel_requested = True
        job.status = JobStatus.QUEUED
        job.queued_at = dt.datetime.now(dt.UTC)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            conflict = reservation_conflict(exc, job)
            raise (conflict or exc) from exc
    elif job.status in TERMINAL_JOB_STATUSES:
        raise ConflictError(
            f"Job is already '{job.status.value}' and cannot be cancelled.",
            details={"status": job.status.value},
        )
    else:
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
