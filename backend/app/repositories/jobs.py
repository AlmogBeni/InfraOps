"""Job repository — queue claiming, querying and retry bookkeeping."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.jobs import (
    ACTIVE_JOB_STATUSES,
    IP_RESERVING_JOB_STATUSES,
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

    async def has_active_job_for_vm(
        self, vm_name: str, *, exclude_job_id: uuid.UUID | None = None
    ) -> bool:
        conditions = [
            func.lower(ProvisioningJob.vm_name) == vm_name.lower(),
            ProvisioningJob.status.in_(ACTIVE_JOB_STATUSES),
        ]
        if exclude_job_id is not None:
            conditions.append(ProvisioningJob.id != exclude_job_id)
        result = await self.session.execute(
            select(func.count()).select_from(ProvisioningJob).where(*conditions)
        )
        return bool((result.scalar_one() or 0) > 0)

    async def has_ipv4_reservation(
        self, address: str, *, exclude_job_id: uuid.UUID | None = None
    ) -> bool:
        conditions = [
            ProvisioningJob.reserved_ipv4 == address,
            ProvisioningJob.status.in_(IP_RESERVING_JOB_STATUSES),
        ]
        if exclude_job_id is not None:
            conditions.append(ProvisioningJob.id != exclude_job_id)
        result = await self.session.execute(
            select(func.count()).select_from(ProvisioningJob).where(*conditions)
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
        request_fingerprint: str | None = None,
        reserved_ipv4: str | None = None,
    ) -> ProvisioningJob:
        job = ProvisioningJob(
            job_type=JobType.VM_PROVISIONING,
            status=JobStatus.QUEUED,
            vm_name=vm_name,
            datacenter_id=datacenter_id,
            requested_by_user_id=requested_by_user_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            reserved_ipv4=reserved_ipv4,
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

    async def claim_due_jobs(self, limit: int, *, worker_id: str) -> list[ProvisioningJob]:
        """Atomically claim up to ``limit`` queued jobs (SKIP LOCKED — safe for N workers).

        Callers pass only their number of free execution slots, so a worker
        never holds RUNNING jobs it is not actually executing.
        """
        if limit <= 0:
            return []
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
            job.worker_id = worker_id
            job.heartbeat_at = now
            if job.started_at is None:
                job.started_at = now
        await self.session.commit()
        return jobs

    async def heartbeat(self, job_id: uuid.UUID, worker_id: str) -> bool | None:
        """Refresh a RUNNING job's heartbeat.

        Returns the job's ``cancel_requested`` flag; ``False`` once this worker
        has itself moved the job to a terminal state; ``None`` when this worker
        no longer owns the job (e.g. the reaper interrupted it).
        """
        now = dt.datetime.now(dt.UTC)
        result = await self.session.execute(
            update(ProvisioningJob)
            .where(
                ProvisioningJob.id == job_id,
                ProvisioningJob.worker_id == worker_id,
                ProvisioningJob.status == JobStatus.RUNNING,
            )
            .values(heartbeat_at=now)
            .returning(ProvisioningJob.cancel_requested)
        )
        row = result.first()
        await self.session.commit()
        if row is not None:
            return bool(row[0])
        current = (
            await self.session.execute(
                select(ProvisioningJob.status, ProvisioningJob.worker_id).where(
                    ProvisioningJob.id == job_id
                )
            )
        ).first()
        if current is not None and current.worker_id == worker_id and current.status in TERMINAL_JOB_STATUSES:
            return False  # finished by this worker; nothing to do
        return None

    async def claim_stale_running_jobs(
        self,
        *,
        stale_before: dt.datetime,
        limit: int = 20,
        exclude_ids: list[uuid.UUID] | None = None,
    ) -> list[ProvisioningJob]:
        """Lock RUNNING jobs whose worker stopped heartbeating (SKIP LOCKED).

        Jobs without any heartbeat (claimed by a pre-heartbeat release) fall
        back to ``updated_at``.
        """
        conditions = [
            ProvisioningJob.status == JobStatus.RUNNING,
            func.coalesce(ProvisioningJob.heartbeat_at, ProvisioningJob.updated_at) < stale_before,
        ]
        if exclude_ids:
            conditions.append(ProvisioningJob.id.not_in(exclude_ids))
        result = await self.session.execute(
            select(ProvisioningJob)
            .options(selectinload(ProvisioningJob.steps), selectinload(ProvisioningJob.request))
            .where(*conditions)
            .order_by(ProvisioningJob.queued_at)
            .limit(limit)
            .with_for_update(skip_locked=True, of=ProvisioningJob)
        )
        return list(result.scalars().all())

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
                # A stage interrupted by a dead worker is resumable.
                StepStatus.RUNNING,
            ) and (step.status != StepStatus.RUNNING or job.status == JobStatus.INTERRUPTED)
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
                # ``attempt`` is incremented when the stage starts again.
                step.status = StepStatus.PENDING
                reset.append(step.stage_key)
        if reset:
            job.status = JobStatus.QUEUED
            job.cancel_requested = False
            job.worker_id = None
            job.heartbeat_at = None
            job.error_summary = None
            job.error_detail = None
            job.finished_at = None
            job.duration_seconds = None
            job.action_required = None
            job.queued_at = now
            job.progress = self.compute_progress(job)
            await self.session.commit()
        return reset

    @staticmethod
    def may_hold_unattended_media(job: ProvisioningJob) -> bool:
        """True when temporary answer media may still be attached to the VM."""
        steps = {step.stage_key: step for step in job.steps}
        prepare = steps.get("prepare_unattended_install")
        cleanup = steps.get("cleanup_unattended_media")
        artifacts = (prepare.artifacts or {}) if prepare is not None else {}
        return bool(
            artifacts.get("datastore_path")
            and not artifacts.get("media_removed")
            and (cleanup is None or cleanup.status != StepStatus.SUCCEEDED)
        )

    async def request_cancel(self, job: ProvisioningJob) -> None:
        job.cancel_requested = True
        # No worker executes QUEUED, INTERRUPTED or paused jobs, so nothing
        # would ever read the flag: finalise them immediately.
        if job.status in (JobStatus.QUEUED, JobStatus.INTERRUPTED, JobStatus.ACTION_REQUIRED):
            job.status = JobStatus.CANCELLED
            job.action_required = None
            job.finished_at = dt.datetime.now(dt.UTC)
            for step in job.steps:
                if step.status in (
                    StepStatus.PENDING,
                    StepStatus.RUNNING,
                    StepStatus.WAITING_FOR_PREREQUISITE,
                ):
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
