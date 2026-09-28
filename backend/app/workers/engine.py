"""Background provisioning worker.

Claims queued jobs from PostgreSQL (SKIP LOCKED — safe with multiple workers),
builds the execution context and drives the pipeline with bounded concurrency.

Liveness model:

* A worker claims only as many jobs as it has free execution slots, so it
  never holds RUNNING jobs it is not executing.
* Every running job carries ``worker_id`` and a ``heartbeat_at`` refreshed by
  the worker. The heartbeat also delivers user cancellation to the running
  stage.
* A reaper (in every worker) marks RUNNING jobs whose heartbeat went stale as
  ``INTERRUPTED`` so they can be retried or cancelled, and removes any
  unattended answer media they left behind.
* On SIGTERM the worker stops claiming, waits for running jobs up to a grace
  period and then interrupts them cleanly.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import socket
import time
import uuid

from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.core.config import get_settings
from app.core.logging import bind_logging_context, get_logger
from app.core.metrics import jobs_total, render_metrics, worker_jobs_in_flight
from app.db.session import session_factory
from app.models.jobs import JobStatus, ProvisioningJob, StepStatus
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
from app.workers.stages import release_unattended_media
from app.workers.state_machine import stage_definitions

log = get_logger(__name__)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class JobEngine:
    def __init__(self) -> None:
        settings = get_settings()
        self._concurrency = max(1, settings.worker_concurrency)
        self._poll_interval = settings.worker_poll_interval_seconds
        self._heartbeat_interval = max(1.0, settings.worker_heartbeat_interval_seconds)
        self._heartbeat_timeout = max(
            self._heartbeat_interval * 3, settings.worker_heartbeat_timeout_seconds
        )
        self._reaper_interval = max(5.0, settings.worker_reaper_interval_seconds)
        self._shutdown_grace = max(0.0, settings.worker_shutdown_grace_seconds)
        self._metrics_port = settings.worker_metrics_port
        self.worker_id = make_worker_id()
        self._publisher = JobEventPublisher()
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._stopping = asyncio.Event()
        self._last_tick = time.monotonic()
        self._next_reap = 0.0
        self._metrics_server: asyncio.AbstractServer | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def free_slots(self) -> int:
        return max(0, self._concurrency - len(self._tasks))

    async def run_forever(self) -> None:
        settings = get_settings()
        log.info(
            "Provisioning worker %s starting (mode=%s concurrency=%d poll=%.1fs heartbeat=%.0fs/%.0fs)",
            self.worker_id, settings.infrastructure_mode.value, self._concurrency,
            self._poll_interval, self._heartbeat_interval, self._heartbeat_timeout,
        )
        await self._start_metrics_server()
        try:
            while not self._stopping.is_set():
                self._last_tick = time.monotonic()
                if time.monotonic() >= self._next_reap:
                    self._next_reap = time.monotonic() + self._reaper_interval
                    try:
                        await self.reap_stale_jobs()
                    except Exception:  # noqa: BLE001 - keep the worker alive on transient DB errors
                        log.exception("Failed to reap stale jobs")
                try:
                    job_ids = await self._claim_due_jobs()
                except Exception:  # noqa: BLE001 - keep the worker alive on transient DB errors
                    log.exception("Failed to claim due jobs")
                    job_ids = []
                for job_id in job_ids:
                    self._spawn(job_id)
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
                except TimeoutError:
                    pass
        finally:
            await self.drain()

    async def stop(self) -> None:
        """Stop claiming new work; running jobs are drained by ``run_forever``."""
        if not self._stopping.is_set():
            log.info("Worker %s stopping: no new jobs will be claimed.", self.worker_id)
        self._stopping.set()

    async def drain(self) -> None:
        tasks = list(self._tasks.values())
        if tasks:
            log.info(
                "Waiting up to %.0fs for %d running job(s) to finish...", self._shutdown_grace, len(tasks)
            )
            _, pending = await asyncio.wait(tasks, timeout=self._shutdown_grace or None)
            if pending:
                log.warning("Interrupting %d job(s) still running after the grace period.", len(pending))
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        if self._metrics_server is not None:
            self._metrics_server.close()
            await self._metrics_server.wait_closed()
        await self._publisher.close()

    # ── claiming & processing ────────────────────────────────────────────────

    async def _claim_due_jobs(self) -> list[uuid.UUID]:
        slots = self.free_slots
        if slots <= 0:
            return []
        async with session_factory() as db:
            repo = JobRepository(db)
            jobs = await repo.claim_due_jobs(limit=slots, worker_id=self.worker_id)
            return [job.id for job in jobs]

    def _spawn(self, job_id: uuid.UUID) -> None:
        task = asyncio.create_task(self._process_job(job_id))
        self._tasks[job_id] = task
        worker_jobs_in_flight.set(len(self._tasks))

        def _done(_task: asyncio.Task) -> None:
            self._tasks.pop(job_id, None)
            worker_jobs_in_flight.set(len(self._tasks))

        task.add_done_callback(_done)

    async def _build_context(self, db, job: ProvisioningJob) -> JobRunContext:
        request = await load_request_payload(job)
        target = await build_vcenter_target(db, request.compute.vcenter_id)
        guest_ops = get_guest_operations()
        requester = (
            await db.get(User, job.requested_by_user_id) if job.requested_by_user_id else None
        )
        return JobRunContext(
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

    async def _process_job(self, job_id: uuid.UUID) -> None:
        try:
            await self._execute_job(job_id)
        except asyncio.CancelledError:
            await self._mark_interrupted(job_id, "The worker shut down before the job finished.")
            raise

    async def _execute_job(self, job_id: uuid.UUID) -> None:
        async with session_factory() as db:
            repo = JobRepository(db)
            job = await repo.get(job_id)
            if job is None or job.status != JobStatus.RUNNING or job.worker_id != self.worker_id:
                return

            bind_logging_context(job_id=str(job_id))
            await repo.ensure_steps(job, stage_definitions())
            pipeline = ProvisioningPipeline(self._publisher)

            try:
                ctx = await self._build_context(db, job)
            except Exception as exc:  # noqa: BLE001 - configuration problems fail the job cleanly
                await self._fail_before_pipeline(db, job, exc)
                return

            jobs_total.inc(status="STARTED")
            await AuditRecorder(db).record(
                AuditAction.JOB_STARTED,
                resource_type="virtual_machine",
                resource_name=ctx.request.vm.name,
                job_id=job_id,
                username=ctx.actor_username,
                datacenter_id=ctx.request.compute.datacenter_id,
                datacenter_name=job.datacenter_name,
                result="running",
            )
            await db.commit()

            pipeline_task = asyncio.create_task(pipeline.execute(ctx))
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(job_id, ctx, pipeline_task))
            try:
                await pipeline_task
                await db.commit()
            except asyncio.CancelledError:
                if pipeline_task.cancelled() and not self._stopping.is_set():
                    # Ownership was lost (the reaper interrupted this job);
                    # the database already reflects that.
                    log.warning("Job %s abandoned: this worker no longer owns it.", job_id)
                    return
                raise
            except Exception:  # noqa: BLE001 - last-resort guard: never lose the failure
                log.exception("Pipeline crashed for job %s", job_id)
                await db.rollback()
                refreshed = await repo.get(job_id)
                if refreshed is not None:
                    await self._fail_before_pipeline(
                        db, refreshed, RuntimeError("Pipeline crashed unexpectedly")
                    )
            finally:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)

    async def _heartbeat_loop(
        self, job_id: uuid.UUID, ctx: JobRunContext, pipeline_task: asyncio.Task
    ) -> None:
        """Refresh the job heartbeat; deliver cancellation; stop on lost ownership."""
        while not pipeline_task.done():
            await asyncio.sleep(self._heartbeat_interval)
            try:
                async with session_factory() as hb_db:
                    cancel_requested = await JobRepository(hb_db).heartbeat(job_id, self.worker_id)
            except Exception:  # noqa: BLE001 - a missed beat is tolerated until the timeout
                log.warning("Heartbeat for job %s failed; will retry.", job_id)
                continue
            if cancel_requested is None:
                if not pipeline_task.done():
                    pipeline_task.cancel()
                return
            if cancel_requested:
                ctx.cancel_event.set()

    async def _fail_before_pipeline(self, db, job, exc: Exception) -> None:
        now = _utcnow()
        human = str(exc) or type(exc).__name__
        job.status = JobStatus.FAILED
        job.error_summary = human[:500]
        job.error_detail = f"{human}\n\n{type(exc).__name__}: {exc}"[:4000]
        job.finished_at = now
        job.worker_id = None
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

    # ── interruption & reaping ───────────────────────────────────────────────

    async def _interrupt(self, db, job: ProvisioningJob, reason: str) -> None:
        """Park a RUNNING job as INTERRUPTED (caller holds the row lock)."""
        now = _utcnow()
        for step in job.steps:
            if step.status == StepStatus.RUNNING:
                step.status = StepStatus.FAILED
                step.finished_at = now
                step.retryable = True
                step.error_human = (
                    f"{reason}\n\nReason:\nThe worker executing this stage stopped.\n\n"
                    "Recommended action:\nRetry the job; completed stages are not repeated."
                )
        job.status = JobStatus.INTERRUPTED
        job.error_summary = reason
        job.worker_id = None
        job.heartbeat_at = None
        job.finished_at = now
        if job.started_at:
            job.duration_seconds = (now - job.started_at).total_seconds()
        job.progress = JobRepository.compute_progress(job)
        try:
            ctx = await self._build_context(db, job)
            await release_unattended_media(ctx, reason="job interrupted")
        except Exception:  # noqa: BLE001 - never block the state transition
            log.exception("Unattended media cleanup failed for interrupted job %s", job.id)
        jobs_total.inc(status=JobStatus.INTERRUPTED.value)
        await AuditRecorder(db).record(
            AuditAction.JOB_INTERRUPTED,
            resource_type="virtual_machine",
            resource_name=job.vm_name,
            job_id=job.id,
            datacenter_name=job.datacenter_name,
            result="interrupted",
            detail_text=reason,
        )
        await db.commit()
        await self._publisher.publish_stage(
            str(job.id), stage=job.current_stage, status=JobStatus.INTERRUPTED.value,
            progress=job.progress, message=reason,
        )

    async def _mark_interrupted(self, job_id: uuid.UUID, reason: str) -> None:
        try:
            async with session_factory() as db:
                job = await JobRepository(db).get(job_id)
                if job is None or job.status != JobStatus.RUNNING or job.worker_id != self.worker_id:
                    return
                await self._interrupt(db, job, reason)
        except Exception:  # noqa: BLE001 - the reaper is the fallback
            log.exception("Could not mark job %s as interrupted; the reaper will.", job_id)

    async def reap_stale_jobs(self, *, max_jobs: int = 20) -> int:
        """Interrupt RUNNING jobs whose worker stopped heartbeating.

        One job per transaction: the row lock is held until that job's
        transition commits, so concurrent reapers never double-process a job.
        """
        reaped = 0
        while reaped < max_jobs:
            stale_before = _utcnow() - dt.timedelta(seconds=self._heartbeat_timeout)
            async with session_factory() as db:
                jobs = await JobRepository(db).claim_stale_running_jobs(
                    stale_before=stale_before, limit=1, exclude_ids=list(self._tasks)
                )
                if not jobs:
                    await db.commit()
                    return reaped
                job = jobs[0]
                log.warning(
                    "Job %s lost its worker (%s, last heartbeat %s); marking INTERRUPTED.",
                    job.id, job.worker_id or "unknown", job.heartbeat_at,
                )
                await self._interrupt(db, job, "The worker executing this job stopped responding.")
                reaped += 1
        return reaped

    # ── metrics & liveness endpoint ──────────────────────────────────────────

    async def _start_metrics_server(self) -> None:
        if not self._metrics_port:
            return
        try:
            self._metrics_server = await asyncio.start_server(
                self._serve_metrics, host="0.0.0.0", port=self._metrics_port  # noqa: S104
            )
        except OSError:
            log.exception("Worker metrics endpoint could not bind port %d", self._metrics_port)

    def healthy(self) -> bool:
        stall_limit = max(60.0, self._poll_interval * 10)
        return time.monotonic() - self._last_tick < stall_limit

    async def _serve_metrics(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            parts = request_line.decode("latin-1").split()
            path = parts[1] if len(parts) > 1 else "/"
            if path.startswith("/metrics"):
                status, body, ctype = "200 OK", render_metrics(), "text/plain; version=0.0.4"
            elif path.startswith("/health"):
                ok = self.healthy()
                status = "200 OK" if ok else "503 Service Unavailable"
                body = "ok\n" if ok else "stalled\n"
                ctype = "text/plain"
            else:
                status, body, ctype = "404 Not Found", "not found\n", "text/plain"
            payload = body.encode("utf-8")
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(payload)}\r\n"
                "Connection: close\r\n\r\n".encode("ascii") + payload
            )
            await writer.drain()
        except Exception:  # noqa: BLE001 - never let a scrape affect the worker
            pass
        finally:
            writer.close()
