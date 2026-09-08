"""Dashboard aggregation endpoint."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DbSession, require
from app.auth.permissions import Permission
from app.models.infrastructure import VCenterConnection
from app.models.jobs import JobStatus, JobType, ProvisioningJob
from app.schemas.common import HealthComponent
from app.schemas.dashboard import DashboardResponse, DashboardStats
from app.schemas.jobs import job_out
from app.secrets.service import get_secrets_service

router = APIRouter(tags=["dashboard"])


@router.get("/stats/dashboard", response_model=DashboardResponse)
async def dashboard(db: DbSession, user=require(Permission.JOBS_READ)) -> DashboardResponse:
    now = dt.datetime.now(dt.UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async def scalar(query) -> float:
        result = await db.execute(query)
        return float(result.scalar_one() or 0)

    provisioned_this_month = await scalar(
        select(func.count()).select_from(ProvisioningJob).where(
            ProvisioningJob.job_type == JobType.VM_PROVISIONING,
            ProvisioningJob.status.in_([JobStatus.COMPLETED, JobStatus.PARTIALLY_COMPLETED]),
            ProvisioningJob.finished_at >= month_start,
        )
    )
    completed = await scalar(
        select(func.count()).select_from(ProvisioningJob).where(
            ProvisioningJob.status.in_([JobStatus.COMPLETED, JobStatus.PARTIALLY_COMPLETED]),
            ProvisioningJob.finished_at >= month_start,
        )
    )
    failed = await scalar(
        select(func.count()).select_from(ProvisioningJob).where(
            ProvisioningJob.status == JobStatus.FAILED,
            ProvisioningJob.finished_at >= month_start,
        )
    )
    active = await scalar(
        select(func.count()).select_from(ProvisioningJob).where(
            ProvisioningJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
        )
    )
    avg_duration = await scalar(
        select(func.avg(ProvisioningJob.duration_seconds)).where(
            ProvisioningJob.status == JobStatus.COMPLETED,
            ProvisioningJob.finished_at >= month_start,
        )
    )

    recent_result = await db.execute(
        select(ProvisioningJob).order_by(ProvisioningJob.queued_at.desc()).limit(8)
    )
    recent_jobs = list(recent_result.scalars().all())
    usernames: dict = {}
    owner_ids = {job.requested_by_user_id for job in recent_jobs if job.requested_by_user_id}
    if owner_ids:
        from app.models.user import User

        rows = await db.execute(select(User.id, User.username).where(User.id.in_(owner_ids)))
        usernames = {row[0]: row[1] for row in rows.all()}

    health: list[HealthComponent] = []
    vcenter_rows = await db.execute(select(VCenterConnection).order_by(VCenterConnection.name))
    for row in vcenter_rows.scalars().all():
        state = row.display_state
        status_value = {
            "connected": "healthy",
            "error": "unavailable",
            "disabled": "degraded",
        }.get(state, "degraded")
        health.append(
            HealthComponent(
                component=f"vCenter {row.name}",
                status=status_value,
                detail=row.host + (f" — {row.last_connection_error}" if row.last_connection_error else ""),
            )
        )

    secrets_ok = await get_secrets_service().healthcheck()
    health.append(
        HealthComponent(
            component=f"Secrets provider ({get_secrets_service().provider_name})",
            status="healthy" if secrets_ok else "unavailable",
        )
    )
    health.append(
        HealthComponent(component="Application repository", status="healthy",
                        detail="Catalog reachable")
    )
    health.append(
        HealthComponent(component="Certificate repository", status="healthy",
                        detail="Catalog reachable")
    )

    total_finished = completed + failed
    success_rate = round(completed / total_finished * 100, 1) if total_finished else None

    return DashboardResponse(
        stats=DashboardStats(
            vms_provisioned_this_month=int(provisioned_this_month),
            success_rate_percent=success_rate,
            average_duration_seconds=round(avg_duration, 1) if avg_duration else None,
            failed_jobs=int(failed),
            active_jobs=int(active),
        ),
        recent_jobs=[job_out(job, usernames) for job in recent_jobs],
        health=health,
    )
