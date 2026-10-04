"""Provisioning endpoints: dry-run validation, IP conflict checks, job
submission/inspection/retry/cancellation and the SSE live-event stream."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import uuid

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import ClientIp, DbSession, require
from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.auth.permissions import Permission, roles_grant
from app.auth.service import auth_service
from app.auth.stream_tickets import TICKET_TTL_SECONDS, get_stream_ticket_store
from app.core.config import get_settings
from app.core.errors import AuthenticationError, AuthorizationError, NotFoundError
from app.db.session import session_factory
from app.models.infrastructure import VCenterConnection
from app.models.jobs import ProvisioningJob, ProvisioningJobStep
from app.models.platform import SecretReference
from app.models.user import User
from app.repositories.jobs import JobRepository
from app.schemas.jobs import (
    JobDetailOut,
    JobListResponse,
    JobOut,
    JobStepOut,
    RetryRequest,
    StreamTicketOut,
    job_out,
)
from app.schemas.provisioning import (
    CredentialOptionOut,
    IpConflictCheckRequest,
    IpConflictReport,
    PreflightReport,
    ProvisioningSubmissionRequest,
    parse_stored_request,
)
from app.services.network.conflict import (
    DnsForwardProvider,
    IcmpPingProvider,
    VMwareInventoryProvider,
    run_conflict_check,
)
from app.services.provisioning.preflight import PreflightValidator
from app.services.provisioning.service import cancel_job, retry_stages, submit_provisioning
from app.services.vmware.base import VCenterTarget
from app.services.vmware.factory import get_vmware_service
from app.workers.context import build_vcenter_target
from app.workers.events import JobEventPublisher

router = APIRouter(prefix="/provisioning", tags=["provisioning"])


@router.get("/credentials", response_model=list[CredentialOptionOut])
async def list_provisioning_credentials(
    db: DbSession,
    user=require(Permission.PROVISIONING_VALIDATE),
    purpose: str | None = Query(default=None),
):
    query = select(SecretReference).where(
        SecretReference.provider == "database",
        SecretReference.encrypted_username.is_not(None),
        SecretReference.encrypted_password.is_not(None),
    )
    if purpose:
        query = query.where(SecretReference.purpose == purpose)
    result = await db.execute(query.order_by(SecretReference.name))
    return [
        CredentialOptionOut(
            name=row.name,
            purpose=row.purpose,
            revision=row.revision,
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        )
        for row in result.scalars().all()
    ]


# ── serialisation helpers ────────────────────────────────────────────────────

def _request_view(payload: dict | None) -> tuple[dict | None, bool]:
    """The stored request in the current contract, or — for a job created by a
    workflow that no longer exists — the raw stored JSON (read-only)."""
    if not isinstance(payload, dict):
        return None, False
    parsed = parse_stored_request(copy.deepcopy(payload))
    if parsed is None:
        return copy.deepcopy(payload), True
    return parsed.model_dump(mode="json"), False


def _step_out(step: ProvisioningJobStep, *, include_technical: bool = False) -> JobStepOut:
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
        error_technical=step.error_technical if include_technical else None,
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
    payload: ProvisioningSubmissionRequest,
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
        datacenter_id=payload.compute.datacenter_id,
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
    payload: ProvisioningSubmissionRequest,
    response: Response,
    db: DbSession,
    source_ip: ClientIp,
    user=require(Permission.PROVISIONING_SUBMIT),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    job, created = await submit_provisioning(
        db, user=user, request=payload,
        idempotency_key=idempotency_key[:120] if idempotency_key else None,
        source_ip=source_ip,
        vmware=get_vmware_service(),
    )
    await db.commit()
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return job_out(job)


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
        items=[job_out(job, usernames) for job in jobs],
        total=total, page=page, page_size=page_size,
    )


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
async def get_job(job_id: uuid.UUID, db: DbSession, user=require(Permission.JOBS_READ)):
    job = await _get_job(db, job_id)
    usernames = await _username_map(db, [job])
    base = job_out(job, usernames)
    request_payload, legacy = _request_view(job.request.payload if job.request else None)
    detail = JobDetailOut(
        **base.model_dump(),
        steps=[
            _step_out(
                step,
                include_technical=roles_grant(user.role_names, Permission.ADMIN_SETTINGS),
            )
            for step in sorted(job.steps, key=lambda s: s.sequence)
        ],
        request_payload=request_payload,
        legacy_request=legacy,
    )
    return detail


@router.get("/jobs/{job_id}/steps", response_model=list[JobStepOut])
async def get_job_steps(job_id: uuid.UUID, db: DbSession, user=require(Permission.JOBS_READ)):
    job = await _get_job(db, job_id)
    include_technical = roles_grant(user.role_names, Permission.ADMIN_SETTINGS)
    return [
        _step_out(step, include_technical=include_technical)
        for step in sorted(job.steps, key=lambda s: s.sequence)
    ]


@router.get("/jobs/{job_id}/request")
async def get_job_request(job_id: uuid.UUID, db: DbSession, user=require(Permission.JOBS_READ)):
    job = await _get_job(db, job_id)
    if job.request is None:
        raise NotFoundError("No stored request for this job.")
    return _request_view(job.request.payload)[0]


# Written by the worker from CreateScreenshot_Task's result: "[datastore] folder/file.png".
_SCREENSHOT_PATH = re.compile(r"^\[[^\]/\\]+\] (?!.*\.\.)[^\x00]+\.png$")
_MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024


@router.get("/jobs/{job_id}/steps/{stage_key}/console-screenshot")
async def get_console_screenshot(
    job_id: uuid.UUID,
    stage_key: str,
    db: DbSession,
    user=require(Permission.ADMIN_SETTINGS),
) -> Response:
    """The VM console as captured when this installation stage failed."""
    job = await _get_job(db, job_id)
    step = next((item for item in job.steps if item.stage_key == stage_key), None)
    path = (step.artifacts or {}).get("console_screenshot") if step is not None else None
    if not isinstance(path, str) or not _SCREENSHOT_PATH.fullmatch(path):
        raise NotFoundError("No console screenshot was captured for this stage.")
    payload = job.request.payload if job.request else {}
    compute = payload.get("compute", {}) if isinstance(payload, dict) else {}
    try:
        vcenter_id = uuid.UUID(str(compute.get("vcenter_id")))
        target = await build_vcenter_target(db, vcenter_id)
    except (ValueError, LookupError) as exc:
        raise NotFoundError("The vCenter that holds this screenshot is no longer registered.") from exc
    content = await get_vmware_service().read_datastore_file(
        target,
        str(compute.get("datacenter_id") or job.datacenter_id or ""),
        path,
        max_bytes=_MAX_SCREENSHOT_BYTES,
    )
    return Response(content=content, media_type="image/png", headers={"Cache-Control": "no-store"})


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
    await retry_stages(
        db, user=user, job=job,
        stage_key=payload.stage_key if payload else None,
        source_ip=source_ip,
    )
    await db.commit()
    return job_out(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job_endpoint(
    job_id: uuid.UUID, db: DbSession, source_ip: ClientIp,
    user=require(Permission.JOBS_CANCEL),
):
    job = await _get_job(db, job_id)
    await cancel_job(db, user=user, job=job, source_ip=source_ip)
    await db.commit()
    return job_out(job)


# ── live event stream (SSE) ──────────────────────────────────────────────────

_publisher_singleton: JobEventPublisher | None = None


def get_publisher() -> JobEventPublisher:
    global _publisher_singleton
    if _publisher_singleton is None:
        _publisher_singleton = JobEventPublisher()
    return _publisher_singleton


@router.post("/jobs/{job_id}/events/ticket", response_model=StreamTicketOut)
async def issue_event_stream_ticket(
    job_id: uuid.UUID, db: DbSession, user=require(Permission.JOBS_READ)
) -> StreamTicketOut:
    """Exchange the bearer token for a single-use ticket for one SSE connection."""
    await _get_job(db, job_id)
    ticket = await get_stream_ticket_store().issue(user_id=user.id, job_id=job_id)
    return StreamTicketOut(ticket=ticket, expires_in=TICKET_TTL_SECONDS)


async def _user_from_ticket(db, job_id: uuid.UUID, ticket: str | None) -> User:
    user_id = await get_stream_ticket_store().consume(ticket or "", job_id=job_id)
    user = await auth_service.get_active_user(db, str(user_id)) if user_id else None
    if user is None:
        raise AuthenticationError("Invalid or expired stream ticket.")
    if not roles_grant(user.role_names, Permission.JOBS_READ):
        raise AuthorizationError("Your roles do not grant 'jobs.read'.")
    return user


async def _snapshot_event(job_id: uuid.UUID, include_technical: bool) -> str | None:
    async with session_factory() as snapshot_db:
        job = await JobRepository(snapshot_db).get(job_id)
        if job is None:
            return None
        usernames = await _username_map(snapshot_db, [job])
        snapshot = {
            "type": "snapshot",
            "job": json.loads(job_out(job, usernames).model_dump_json()),
            "steps": [
                json.loads(_step_out(s, include_technical=include_technical).model_dump_json())
                for s in sorted(job.steps, key=lambda x: x.sequence)
            ],
        }
    return f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"


@router.get("/jobs/{job_id}/events")
async def stream_job_events(
    job_id: uuid.UUID,
    request: Request,
    db: DbSession,
    ticket: str | None = Query(default=None, max_length=128),
) -> StreamingResponse:
    user = await _user_from_ticket(db, job_id, ticket)
    await _get_job(db, job_id)
    include_technical = roles_grant(user.role_names, Permission.ADMIN_SETTINGS)
    # Re-authenticate periodically: the client reconnects with a new ticket,
    # which requires a currently valid access token.
    max_lifetime = max(60, get_settings().access_token_expire_minutes * 60)

    async def event_stream():
        publisher = get_publisher()
        # Subscribe *before* reading the snapshot so no event published in
        # between is lost (duplicates are harmless; the client refetches).
        pubsub = await publisher.subscribe(str(job_id))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_lifetime
        try:
            snapshot = await _snapshot_event(job_id, include_technical)
            if snapshot is None:
                return
            yield snapshot

            while loop.time() < deadline:
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
            yield "event: reauthenticate\ndata: {}\n\n"
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
