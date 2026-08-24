"""Provisioning endpoints: dry-run validation, IP conflict checks, job
submission/inspection/retry/cancellation and the SSE live-event stream."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import ClientIp, DbSession, require
from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.auth.permissions import Permission
from app.core.errors import NotFoundError
from app.models.infrastructure import VCenterConnection
from app.models.jobs import ProvisioningJob, ProvisioningJobStep
from app.models.user import User
from app.repositories.jobs import JobRepository
from app.schemas.jobs import JobDetailOut, JobListResponse, JobOut, JobStepOut, RetryRequest
from app.schemas.provisioning import (
    IpConflictCheckRequest,
    IpConflictReport,
    PreflightReport,
    ProvisioningRequest,
)
from app.services.network.conflict import (
    DnsForwardProvider,
    IcmpPingProvider,
    IpamProvider,
    VMwareInventoryProvider,
    run_conflict_check,
)
from app.services.provisioning.preflight import PreflightValidator
from app.services.provisioning.service import cancel_job, retry_stages, submit_provisioning
from app.services.settings_store import SETTING_IPAM_ENABLED, load_effective
from app.services.vmware.base import VCenterTarget
from app.services.vmware.factory import get_vmware_service

router = APIRouter(prefix="/provisioning", tags=["provisioning"])


# ── serialisation helpers ────────────────────────────────────────────────────

def _job_out(job: ProvisioningJob, usernames: dict[uuid.UUID, str] | None = None) -> JobOut:
    return JobOut(
        id=str(job.id),
        job_type=job.job_type,
        status=job.status,
        vm_name=job.vm_name,
        requested_by_username=(usernames or {}).get(job.requested_by_user_id),
        current_stage=job.current_stage,
        progress=job.progress,
        error_summary=job.error_summary,
        cancel_requested=job.cancel_requested,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_seconds=job.duration_seconds,
    )


def _step_out(step: ProvisioningJobStep) -> JobStepOut:
    return JobStepOut(
        id=str(step.id),
        job_id=str(step.job_id),
        stage_key=step.stage_key,
        name=step.name,
        sequence=step.sequence,
        status=step.status,
        attempt=step.attempt,
        max_attempts=step.max_attempts,
        retryable=step.retryable,
        started_at=step.started_at,
        finished_at=step.finished_at,
        output=step.output,
        error_human=step.error_human,
        error_technical=step.error_technical,
        artifacts=step.artifacts or {},
    )


async def _username_map(db, jobs: list[ProvisioningJob]) -> dict[uuid.UUID, str]:
    ids = {job.requested_by_user_id for job in jobs if job.requested_by_user_id}
    if not ids:
        return {}
    result = await db.execute(select(User.id, User.username).where(User.id.in_(ids)))
    return {row[0]: row[1] for row in result.all()}


async def _get_job(db, job_id: uuid.UUID) -> ProvisioningJob:
    job = await JobRepository(db).get(job_id)
    if job is None:
        raise NotFoundError("Provisioning job not found.")
    return job


# ── dry run / validation ─────────────────────────────────────────────────────

@router.post("/validate", response_model=PreflightReport)
async def validate_provisioning_request(
    payload: ProvisioningRequest,
    db: DbSession,
    source_ip: ClientIp,
    user=require(Permission.PROVISIONING_VALIDATE),
) -> PreflightReport:
    report = await PreflightValidator(db, get_vmware_service()).validate(payload)
    failures = sum(1 for c in report.checks if c.status.value == "FAIL")
    warnings = sum(1 for c in report.checks if c.status.value == "WARN")
    await AuditRecorder(db).record(
        AuditAction.DRY_RUN_PERFORMED,
        user=user,
        resource_type="provisioning_request",
        resource_name=payload.vm.name,
        result="ready" if report.ready else "blocked",
        source_ip=source_ip,
        details={"checks": len(report.checks), "failures": failures, "warnings": warnings},
    )
    await db.commit()
    return report


@router.post("/ip-check", response_model=IpConflictReport)
async def check_ip_conflict(
    payload: IpConflictCheckRequest,
    db: DbSession,
    source_ip: ClientIp,
    user=require(Permission.PROVISIONING_VALIDATE),
    vcenter_id: uuid.UUID | None = Query(default=None),
) -> IpConflictReport:
    settings_rows = await load_effective(db)
    target: VCenterTarget | None = None
    if vcenter_id is not None:
        row = await db.get(VCenterConnection, vcenter_id)
        if row is not None and row.enabled:
            target = VCenterTarget(
                id=str(row.id), name=row.name, host=row.host, port=row.port,
                username_secret_ref=row.username_secret_ref,
                password_secret_ref=row.password_secret_ref,
                verify_ssl=row.verify_ssl,
            )

    providers = [
        IcmpPingProvider(),
        DnsForwardProvider(),
    ]
    if target is not None:
        providers.append(VMwareInventoryProvider(get_vmware_service(), target))
    providers.append(IpamProvider(enabled=bool(settings_rows.get(SETTING_IPAM_ENABLED))))

    report = await run_conflict_check(payload.address, payload.prefix, providers)
    await AuditRecorder(db).record(
        AuditAction.IP_CONFLICT_CHECK_PERFORMED,
        user=user,
        resource_type="ip_address",
        resource_name=payload.address,
        result="conflict" if report.conflict_detected else "clear",
        source_ip=source_ip,
    )
    await db.commit()
    return report


# ── job submission & inspection ──────────────────────────────────────────────

@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def submit_job(
    payload: ProvisioningRequest,
    response: Response,
    db: DbSession,
    source_ip: ClientIp,
    user=require(Permission.PROVISIONING_SUBMIT),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    job, created = await submit_provisioning(
        db, user=user, request=payload,
        idempotency_key=(idempotency_key or None)[:120] if idempotency_key else None,
        source_ip=source_ip,
    )
    await db.commit()
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _job_out(job)


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    db: DbSession,
    user=require(Permission.JOBS_READ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    vm_name: str | None = Query(default=None),
    job_status: str | None = Query(default=None, alias="status"),
):
    from app.models.jobs import JobStatus as JobStatusEnum

    parsed_status = None
    if job_status:
        try:
            parsed_status = JobStatusEnum(job_status.upper())
        except ValueError as exc:
            raise NotFoundError(f"Unknown job status '{job_status}'.") from exc

    repo = JobRepository(db)
    jobs, total = await repo.list_jobs(page=page, page_size=page_size,
                                       status=parsed_status, vm_name_contains=vm_name)
    usernames = await _username_map(db, jobs)
    return JobListResponse(
        items=[_job_out(job, usernames) for job in jobs],
        total=total, page=page, page_size=page_size,
    )


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
async def get_job(job_id: uuid.UUID, db: DbSession, user=require(Permission.JOBS_READ)):
    job = await _get_job(db, job_id)
    usernames = await _username_map(db, [job])
    base = _job_out(job, usernames)
    detail = JobDetailOut(
        **base.model_dump(),
        steps=[_step_out(step) for step in sorted(job.steps, key=lambda s: s.sequence)],
        request_payload=job.request.payload if job.request else None,
    )
    # Technical error details are administrator-visible only.
    current = user  # permission already guarantees at least viewer
    from app.auth.permissions import Permission, roles_grant

    if not roles_grant(current.role_names, Permission.ADMIN_SETTINGS):
        for step in detail.steps:
            step.error_technical = None
    return detail


@router.get("/jobs/{job_id}/steps", response_model=list[JobStepOut])
async def get_job_steps(job_id: uuid.UUID, db: DbSession, user=require(Permission.JOBS_READ)):
    job = await _get_job(db, job_id)
    return [_step_out(step) for step in sorted(job.steps, key=lambda s: s.sequence)]


@router.get("/jobs/{job_id}/request")
async def get_job_request(job_id: uuid.UUID, db: DbSession, user=require(Permission.JOBS_READ)):
    job = await _get_job(db, job_id)
    if job.request is None:
        raise NotFoundError("No stored request for this job.")
    return job.request.payload


# ── retry / cancel ───────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: uuid.UUID,
    db: DbSession,
    source_ip: ClientIp,
    user=require(Permission.JOBS_RETRY),
    payload: RetryRequest | None = None,
):
    job = await _get_job(db, job_id)
    reset = await retry_stages(
        db, user=user, job=job,
        stage_key=payload.stage_key if payload else None,
        source_ip=source_ip,
    )
    await db.commit()
    log_message = f"Retrying: {', '.join(reset)}"
    await get_publisher().publish_stage(str(job.id), stage=None, status="QUEUED",
                                        progress=job.progress, message=log_message)
    return _job_out(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job_endpoint(
    job_id: uuid.UUID, db: DbSession, source_ip: ClientIp,
    user=require(Permission.JOBS_CANCEL),
):
    job = await _get_job(db, job_id)
    await cancel_job(db, user=user, job=job, source_ip=source_ip)
    await db.commit()
    return _job_out(job)


# ── live event stream (SSE) ──────────────────────────────────────────────────

_publisher_singleton: JobEventPublisher | None = None


def get_publisher() -> JobEventPublisher:
    global _publisher_singleton
    if _publisher_singleton is None:
        _publisher_singleton = JobEventPublisher()
    return _publisher_singleton


@router.get("/jobs/{job_id}/events")
async def stream_job_events(
    job_id: uuid.UUID,
    request: Request,
    db: DbSession,
    user=require(Permission.JOBS_READ),
) -> StreamingResponse:
    job = await _get_job(db, job_id)

    async def event_stream():
        publisher = get_publisher()
        pubsub = await publisher.subscribe(str(job.id))
        try:
            snapshot = {
                "type": "snapshot",
                "job": json.loads(_job_out(job).model_dump_json()),
                "steps": [json.loads(_step_out(s).model_dump_json())
                          for s in sorted(job.steps, key=lambda x: x.sequence)],
            }
            yield f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if message is None:
                    yield ": keep-alive\n\n"
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                yield f"event: job-update\ndata: {data}\n\n"
        finally:
            try:
                await pubsub.unsubscribe()
                await pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
