"""Job repository — queue claiming, querying and retry bookkeeping."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.jobs import (
    TERMINAL_JOB_STATUSES,
    JobStatus,
    JobType,
    ProvisioningJob,
    ProvisioningJobStep,
    StepStatus,
    VmProvisioningRequest,
)


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Creation ─────────────────────────────────────────────────────────────

    async def find_by_idempotency_key(self, key: str) -> ProvisioningJob | None:
        result = await self.session.execute(
            select(ProvisioningJob).where(ProvisioningJob.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def has_active_job_for_vm(self, vm_name: str) -> bool:
        result = await self.session.execute(
            select(func.count())
            .select_from(ProvisioningJob)
            .where(
                ProvisioningJob.vm_name == vm_name,
                ProvisioningJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
        )
        return bool((result.scalar_one() or 0) > 0)

    async def create_job(
        self,
        *,
        vm_name: str,
        datacenter_id: str,
        requested_by_user_id: uuid.UUID | None,
        idempotency_key: str | None,
        request_payload: dict,
    ) -> ProvisioningJob:
        job = ProvisioningJob(
            job_type=JobType.VM_PROVISIONING,
            status=JobStatus.QUEUED,
            vm_name=vm_name,
            datacenter_id=datacenter_id,
            requested_by_user_id=requested_by_user_id,
            idempotency_key=idempotency_key,
            queued_at=dt.datetime.now(dt.UTC),
        )
        self.session.add(job)
        await self.session.flush()
        self.session.add(
            VmProvisioningRequest(
                job_id=job.id, payload=request_payload, requested_by_user_id=requested_by_user_id
            )
        )
        await self.session.flush()
        return job

    # ── Queue operations ─────────────────────────────────────────────────────

    async def claim_due_jobs(self, limit: int) -> list[ProvisioningJob]:
        """Atomically claim queued jobs (SKIP LOCKED — safe for N workers)."""
        now = dt.datetime.now(dt.UTC)
        result = await self.session.execute(
            select(ProvisioningJob)
            .where(ProvisioningJob.status == JobStatus.QUEUED, ProvisioningJob.queued_at <= now)
            .order_by(ProvisioningJob.queued_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list(result.scalars().all())
        for job in jobs:
            job.status = JobStatus.RUNNING
            if job.started_at is None:
                job.started_at = now
        await self.session.commit()
        return jobs

    # ── Queries ──────────────────────────────────────────────────────────────

    async def get(self, job_id: uuid.UUID) -> ProvisioningJob | None:
        result = await self.session.execute(
            select(ProvisioningJob)
            .options(
                selectinload(ProvisioningJob.steps),
                selectinload(ProvisioningJob.request),
            )
            .where(ProvisioningJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def list_jobs(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        status: JobStatus | None = None,
        vm_name_contains: str | None = None,
    ) -> tuple[list[ProvisioningJob], int]:
        conditions = []
        if status is not None:
            conditions.append(ProvisioningJob.status == status)
        if vm_name_contains:
            conditions.append(ProvisioningJob.vm_name.ilike(f"%{vm_name_contains}%"))

        total_result = await self.session.execute(
            select(func.count()).select_from(ProvisioningJob).where(*conditions)
        )
        total = int(total_result.scalar_one() or 0)

        result = await self.session.execute(
            select(ProvisioningJob)
            .where(*conditions)
            .order_by(ProvisioningJob.queued_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    # ── Steps ────────────────────────────────────────────────────────────────

    async def ensure_steps(
        self, job: ProvisioningJob, stage_definitions: list[tuple[str, str, bool]]
    ) -> None:
        """Create any missing step rows (idempotent across retries/restarts)."""
        existing = {step.stage_key for step in job.steps}
        for sequence, (stage_key, name, retryable) in enumerate(stage_definitions):
            if stage_key in existing:
                continue
            job.steps.append(
                ProvisioningJobStep(
                    job_id=job.id,
                    stage_key=stage_key,
                    name=name,
                    sequence=sequence,
                    status=StepStatus.PENDING,
                    retryable=retryable,
                )
            )
        job.steps.sort(key=lambda step: step.sequence)
        await self.session.flush()

    async def step_by_key(self, job_id: uuid.UUID, stage_key: str) -> ProvisioningJobStep | None:
        result = await self.session.execute(
            select(ProvisioningJobStep).where(
                ProvisioningJobStep.job_id == job_id,
                ProvisioningJobStep.stage_key == stage_key,
            )
        )
        return result.scalar_one_or_none()

    # ── Retry / cancel ───────────────────────────────────────────────────────

    async def reset_for_retry(
        self, job: ProvisioningJob, stage_keys: list[str] | None = None
    ) -> list[str]:
        """Reset failed/cancelled steps back to PENDING and requeue the job.

        Succeeded stages are intentionally left untouched — retries resume the
        pipeline and never repeat destructive work that already completed.
        """
        now = dt.datetime.now(dt.UTC)
        reset: list[str] = []
        selected_sequences = {
            step.sequence for step in job.steps
            if stage_keys is not None and step.stage_key in stage_keys
        }
        first_selected = min(selected_sequences) if selected_sequences else None
        for step in job.steps:
            eligible_status = step.status in (
                StepStatus.FAILED,
                StepStatus.CANCELLED,
                StepStatus.WAITING_FOR_PREREQUISITE,
            )
            selected = (
                stage_keys is None
                or step.stage_key in stage_keys
                or (
                    first_selected is not None
                    and step.status == StepStatus.WAITING_FOR_PREREQUISITE
                    and step.sequence > first_selected
                )
            )
            should_reset = eligible_status and selected
            if should_reset:
                step.status = StepStatus.PENDING
                step.attempt += 0  # attempt incremented when the stage starts
                reset.append(step.stage_key)
        if reset:
            job.status = JobStatus.QUEUED
            job.cancel_requested = False
            job.finished_at = None
            job.duration_seconds = None
            job.action_required = None
            job.queued_at = now
            job.progress = self.compute_progress(job)
            await self.session.commit()
        return reset

    async def request_cancel(self, job: ProvisioningJob) -> None:
        job.cancel_requested = True
        if job.status == JobStatus.QUEUED:
            job.status = JobStatus.CANCELLED
            job.finished_at = dt.datetime.now(dt.UTC)
            for step in job.steps:
                if step.status == StepStatus.PENDING:
                    step.status = StepStatus.CANCELLED
        await self.session.commit()

    # ── Progress helpers ─────────────────────────────────────────────────────

    @staticmethod
    def compute_progress(job: ProvisioningJob) -> int:
        actionable = [
            s for s in job.steps
            if s.status not in (StepStatus.SKIPPED, StepStatus.NOT_APPLICABLE)
        ]
        if not actionable:
            return 0
        done = sum(
            1 for s in actionable
            if s.status in (StepStatus.SUCCEEDED, StepStatus.WARNING, StepStatus.CANCELLED)
        )
        running = sum(1 for s in actionable if s.status == StepStatus.RUNNING)
        percent = int(((done + 0.5 * running) / len(actionable)) * 100)
        return max(0, min(100, percent))

    @staticmethod
    def is_terminal(status: JobStatus) -> bool:
        return status in TERMINAL_JOB_STATUSES
