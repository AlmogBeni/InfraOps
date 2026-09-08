"""Filtered, paginated operational logs backed by durable job-step rows."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.jobs import ProvisioningJob, ProvisioningJobStep, StepStatus
from app.schemas.logs import LogEventOut, LogSeverity

LOGGABLE_STEP_STATUSES = (
    StepStatus.RUNNING,
    StepStatus.SUCCEEDED,
    StepStatus.FAILED,
    StepStatus.SKIPPED,
    StepStatus.WARNING,
    StepStatus.WAITING_FOR_PREREQUISITE,
    StepStatus.NOT_APPLICABLE,
    StepStatus.CANCELLED,
)


def severity_for_status(status: StepStatus | str) -> LogSeverity:
    value = status.value if isinstance(status, StepStatus) else str(status)
    if value == StepStatus.FAILED.value:
        return "ERROR"
    if value in (
        StepStatus.CANCELLED.value,
        StepStatus.WARNING.value,
        StepStatus.WAITING_FOR_PREREQUISITE.value,
    ):
        return "WARNING"
    if value in (StepStatus.SKIPPED.value, StepStatus.NOT_APPLICABLE.value):
        return "DEBUG"
    return "INFO"


def _normalized_timestamp(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


def _concise(value: str, *, limit: int = 240) -> str:
    first_line = next((line.strip() for line in value.splitlines() if line.strip()), "")
    normalized = " ".join(first_line.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def message_for_step(step: ProvisioningJobStep) -> str:
    if step.status == StepStatus.FAILED and step.error_human:
        return _concise(step.error_human)
    if step.output:
        return _concise(step.output)
    return f"{step.name}: {step.status.value.replace('_', ' ').title()}"


def details_for_step(
    step: ProvisioningJobStep,
    job: ProvisioningJob,
    *,
    include_technical: bool,
) -> dict:
    details = {
        "stage": step.stage_key,
        "status": step.status.value,
        "attempt": step.attempt,
    }
    if include_technical:
        details["datacenter_id"] = job.datacenter_id
        details["artifacts"] = step.artifacts or {}
        if step.error_technical:
            details["technical_error"] = step.error_technical
    return details


class LogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(
        self,
        *,
        page: int,
        page_size: int,
        severity: LogSeverity | None = None,
        component: str | None = None,
        datacenter: str | None = None,
        job_id: uuid.UUID | None = None,
        search: str | None = None,
        since: dt.datetime | None = None,
        until: dt.datetime | None = None,
        include_technical: bool = False,
    ) -> tuple[list[LogEventOut], int]:
        timestamp = func.coalesce(
            ProvisioningJobStep.finished_at,
            ProvisioningJobStep.started_at,
            ProvisioningJob.queued_at,
        )
        # PENDING rows describe future pipeline stages, not operational events.
        conditions = [ProvisioningJobStep.status.in_(LOGGABLE_STEP_STATUSES)]
        if severity == "ERROR":
            conditions.append(ProvisioningJobStep.status == StepStatus.FAILED)
        elif severity == "WARNING":
            conditions.append(
                ProvisioningJobStep.status.in_(
                    [
                        StepStatus.CANCELLED,
                        StepStatus.WARNING,
                        StepStatus.WAITING_FOR_PREREQUISITE,
                    ]
                )
            )
        elif severity == "DEBUG":
            conditions.append(
                ProvisioningJobStep.status.in_(
                    [StepStatus.SKIPPED, StepStatus.NOT_APPLICABLE]
                )
            )
        elif severity == "INFO":
            conditions.append(
                ProvisioningJobStep.status.in_(
                    [StepStatus.RUNNING, StepStatus.SUCCEEDED]
                )
            )
        elif severity == "CRITICAL":
            # Step records currently have no critical state; retain the filter
            # value in the public contract and return an intentional empty set.
            conditions.append(ProvisioningJobStep.id.is_(None))
        if component:
            stage_key = component.casefold().removeprefix("provisioning.").strip()
            if stage_key and stage_key != "provisioning":
                conditions.append(
                    ProvisioningJobStep.stage_key.ilike(
                        f"%{stage_key.replace(' ', '_')}%"
                    )
                )
        if datacenter:
            pattern = f"%{datacenter}%"
            conditions.append(
                or_(
                    ProvisioningJob.datacenter_name.ilike(pattern),
                    ProvisioningJob.datacenter_id == datacenter,
                )
            )
        if job_id:
            conditions.append(ProvisioningJob.id == job_id)
        if search:
            pattern = f"%{search}%"
            conditions.append(
                or_(
                    ProvisioningJobStep.name.ilike(pattern),
                    ProvisioningJobStep.output.ilike(pattern),
                    ProvisioningJobStep.error_human.ilike(pattern),
                    ProvisioningJob.vm_name.ilike(pattern),
                )
            )
        if since:
            conditions.append(timestamp >= _normalized_timestamp(since))
        if until:
            conditions.append(timestamp <= _normalized_timestamp(until))

        base = (
            select(ProvisioningJobStep, ProvisioningJob)
            .join(ProvisioningJob, ProvisioningJob.id == ProvisioningJobStep.job_id)
            .where(*conditions)
        )
        total_result = await self.session.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = int(total_result.scalar_one() or 0)
        rows = await self.session.execute(
            base.order_by(timestamp.desc(), ProvisioningJobStep.sequence.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items: list[LogEventOut] = []
        for step, job in rows.all():
            items.append(
                LogEventOut(
                    id=str(step.id),
                    timestamp=step.finished_at or step.started_at or job.queued_at,
                    severity=severity_for_status(step.status),
                    component=f"provisioning.{step.stage_key}",
                    message=message_for_step(step),
                    resource_name=job.vm_name,
                    datacenter_name=job.datacenter_name,
                    job_id=str(job.id),
                    details=details_for_step(
                        step,
                        job,
                        include_technical=include_technical,
                    ),
                )
            )
        return items, total
