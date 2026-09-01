"""Background provisioning worker.

Claims queued jobs from PostgreSQL (SKIP LOCKED — safe with multiple workers),
builds the execution context and drives the pipeline with bounded concurrency.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.core.config import get_settings
from app.core.logging import bind_logging_context, configure_logging, get_logger
from app.core.metrics import jobs_total
from app.db.session import session_factory
from app.models.jobs import JobStatus, StepStatus
from app.models.user import User
from app.repositories.jobs import JobRepository
from app.secrets.service import get_secrets_service
from app.services.applications.installer import ApplicationInstaller
from app.services.certificates.deployer import CertificateDeployer
from app.services.guest.factory import get_guest_operations
from app.services.vmware.factory import get_vmware_service
from app.workers.context import JobRunContext, build_vcenter_target, load_request_payload
from app.workers.events import JobEventPublisher
from app.workers.pipeline import ProvisioningPipeline
from app.workers.state_machine import stage_definitions

log = get_logger(__name__)


class JobEngine:
    def __init__(self) -> None:
        settings = get_settings()
        self._concurrency = max(1, settings.worker_concurrency)
        self._poll_interval = settings.worker_poll_interval_seconds
        self._publisher = JobEventPublisher()
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._tasks: set[asyncio.Task] = set()
        self._stopping = asyncio.Event()

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def run_forever(self) -> None:
        settings = get_settings()
        log.info(
            "Provisioning worker starting (mode=%s concurrency=%d poll=%.1fs)",
            settings.infrastructure_mode.value, self._concurrency, self._poll_interval,
        )
        while not self._stopping.is_set():
            try:
                job_ids = await self._claim_due_jobs()
            except Exception:  # noqa: BLE001 - keep the worker alive on transient DB errors
                log.exception("Failed to claim due jobs")
                job_ids = []
            for job_id in job_ids:
                self._spawn(job_id)
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._poll_interval
                )
            except TimeoutError:
                pass
        await self.drain()

    async def stop(self) -> None:
        self._stopping.set()

    async def drain(self) -> None:
        if self._tasks:
            log.info("Waiting for %d running job task(s)...", len(self._tasks))
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._publisher.close()

    # ── claiming & processing ────────────────────────────────────────────────

    async def _claim_due_jobs(self) -> list[uuid.UUID]:
        async with session_factory() as db:
            repo = JobRepository(db)
            jobs = await repo.claim_due_jobs(limit=self._concurrency * 2)
            return [job.id for job in jobs]

    def _spawn(self, job_id: uuid.UUID) -> None:
        task = asyncio.create_task(self._process_job(job_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _process_job(self, job_id: uuid.UUID) -> None:
        async with self._semaphore:
            async with session_factory() as db:
                repo = JobRepository(db)
                job = await repo.get(job_id)
                if job is None or job.status != JobStatus.RUNNING:
                    return

                bind_logging_context(job_id=str(job_id))
                await repo.ensure_steps(job, stage_definitions())
                pipeline = ProvisioningPipeline(self._publisher)

                try:
                    request = await load_request_payload(job)
                    target = await build_vcenter_target(db, request.compute.vcenter_id)
                except Exception as exc:  # noqa: BLE001 - configuration problems fail the job cleanly
                    await self._fail_before_pipeline(db, job, exc)
                    return

                guest_ops = get_guest_operations()
                requester = (
                    await db.get(User, job.requested_by_user_id)
                    if job.requested_by_user_id
                    else None
                )
                ctx = JobRunContext(
                    db=db,
                    job=job,
                    request=request,
                    target=target,
                    vmware=get_vmware_service(),
                    guest_ops=guest_ops,
                    cert_deployer=CertificateDeployer(guest_ops),
                    app_installer=ApplicationInstaller(guest_ops),
                    secrets=get_secrets_service(),
                    publisher=self._publisher,
                    actor_username=requester.username if requester else None,
                )

                jobs_total.inc(status="STARTED")
                await AuditRecorder(db).record(
                    AuditAction.JOB_STARTED,
                    resource_type="virtual_machine",
                    resource_name=request.vm.name,
                    job_id=job_id,
                    username=requester.username if requester else None,
                    datacenter_id=request.compute.datacenter_id,
                    datacenter_name=job.datacenter_name,
                    result="running",
                )
                await db.commit()

                try:
                    await pipeline.execute(ctx)
                    await db.commit()
                except Exception:  # noqa: BLE001 - last-resort guard: never lose the failure
                    log.exception("Pipeline crashed for job %s", job_id)
                    await self._fail_before_pipeline(db, job, RuntimeError("Pipeline crashed unexpectedly"))

    async def _fail_before_pipeline(self, db, job, exc: Exception) -> None:
        now = dt.datetime.now(dt.UTC)
        human = str(exc) or type(exc).__name__
        job.status = JobStatus.FAILED
        job.error_summary = human[:500]
        job.error_detail = f"{human}\n\n{type(exc).__name__}: {exc}"[:4000]
        job.finished_at = now
        if job.started_at:
            job.duration_seconds = (now - job.started_at).total_seconds()
        for step in job.steps:
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                step.status = StepStatus.CANCELLED
        jobs_total.inc(status=JobStatus.FAILED.value)
        requester = (
            await db.get(User, job.requested_by_user_id)
            if job.requested_by_user_id
            else None
        )
        payload = job.request.payload if job.request else {}
        compute = payload.get("compute", {}) if isinstance(payload, dict) else {}
        if not isinstance(compute, dict):
            compute = {}
        await AuditRecorder(db).record(
            AuditAction.JOB_FAILED,
            resource_type="virtual_machine",
            resource_name=job.vm_name,
            job_id=job.id,
            username=requester.username if requester else None,
            datacenter_id=compute.get("datacenter_id"),
            datacenter_name=job.datacenter_name,
            result="failure",
            detail_text=human,
        )
        await db.commit()
        await self._publisher.publish_stage(
            str(job.id), stage=None, status="FAILED", progress=job.progress, message=human
        )


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    engine = JobEngine()
    try:
        await engine.run_forever()
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover - operator interrupt
        await engine.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
