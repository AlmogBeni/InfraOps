"""Pipeline orchestration: executes stages with timeouts, resume, cancellation
and full persistence of progress, output and errors."""

from __future__ import annotations

import asyncio
import datetime as dt
import traceback

from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.core.errors import InfraOperationError
from app.core.logging import bind_logging_context, get_logger
from app.core.metrics import job_duration_seconds, jobs_total, stage_failures_total
from app.models.jobs import (
    GuestOsStatus,
    GuestProvisioningStatus,
    InfrastructureStatus,
    JobStatus,
    StepStatus,
    VMwareToolsStatus,
)
from app.repositories.jobs import JobRepository
from app.workers.context import JobRunContext
from app.workers.events import JobEventPublisher
from app.workers.stages import STAGE_HANDLERS, effective_timeout_seconds
from app.workers.state_machine import ORDERED_STAGES, StageDefinition

log = get_logger(__name__)

_MAX_OUTPUT_CHARS = 8000


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class ProvisioningPipeline:
    """Executes the ordered stage list against a :class:`JobRunContext`.

    Resume semantics: stages already SUCCEEDED/SKIPPED are skipped, so retries
    never repeat destructive work that completed successfully.
    """

    def __init__(self, publisher: JobEventPublisher) -> None:
        self._publisher = publisher

    async def execute(self, ctx: JobRunContext) -> None:
        for stage in ORDERED_STAGES:
            step = ctx.steps_by_key.get(stage.key)
            if step is None:
                continue
            if step.status in (
                StepStatus.SUCCEEDED,
                StepStatus.SKIPPED,
                StepStatus.WARNING,
                StepStatus.NOT_APPLICABLE,
            ):
                continue

            await ctx.db.refresh(ctx.job)
            if ctx.job.cancel_requested:
                await self._apply_cancellation(ctx)
                return

            await self._run_stage(ctx, stage, step)
            if step.status in (StepStatus.FAILED, StepStatus.WAITING_FOR_PREREQUISITE):
                return  # failure/action-required path finalised or paused the job

        await self._finalize_success(ctx)

    # ── single-stage execution ───────────────────────────────────────────────

    async def _run_stage(self, ctx: JobRunContext, stage: StageDefinition, step) -> None:
        started_at = _utcnow()
        step.status = StepStatus.RUNNING
        step.attempt += 1
        step.started_at = started_at
        step.error_human = None
        step.error_technical = None
        ctx.job.current_stage = stage.key
        ctx.job.progress = JobRepository.compute_progress(ctx.job)
        await ctx.db.commit()

        bind_logging_context(job_id=str(ctx.job_id))
        await self._publish(ctx, status="RUNNING", message=f"Started: {stage.name}")

        handler = STAGE_HANDLERS.get(stage.key)
        timeout = await effective_timeout_seconds(ctx, stage.key)
        failure: InfraOperationError | None = None
        outcome = None
        try:
            if handler is None:
                raise InfraOperationError(
                    f"Stage '{stage.key}' has no registered handler.",
                    reason="Internal pipeline misconfiguration.",
                    recommended_action="Report this incident to the platform administrators.",
                    retryable=False,
                )
            outcome = await asyncio.wait_for(handler(ctx), timeout=timeout)
        except TimeoutError:
            failure = InfraOperationError(
                f"'{stage.name}' exceeded its {int(timeout)} second timeout.",
                reason="The operation did not complete within the configured time limit.",
                recommended_action=f"Retry the '{stage.name}' stage; increase the timeout in Settings if needed.",
                technical_detail=f"timeout_seconds={timeout}",
                retryable=True,
            )
        except InfraOperationError as exc:
            failure = exc
        except Exception:  # noqa: BLE001 - unexpected errors must still be surfaced clearly
            log.exception("Unexpected error in stage %s", stage.key)
            failure = InfraOperationError(
                f"'{stage.name}' failed unexpectedly.",
                reason="An unexpected internal error interrupted the stage.",
                recommended_action="Review the technical details and retry the stage.",
                technical_detail=traceback.format_exc()[-4000:],
                retryable=True,
            )

        finished_at = _utcnow()
        if failure is not None:
            await self._handle_failure(ctx, stage, step, failure, started_at, finished_at)
            return

        assert outcome is not None
        step.status = {
            "SUCCEEDED": StepStatus.SUCCEEDED,
            "SKIPPED": StepStatus.SKIPPED,
            "WARNING": StepStatus.WARNING,
            "NOT_APPLICABLE": StepStatus.NOT_APPLICABLE,
            "WAITING_FOR_PREREQUISITE": StepStatus.WAITING_FOR_PREREQUISITE,
        }.get(outcome.status, StepStatus.SUCCEEDED)
        step.finished_at = finished_at
        step.output = outcome.output[:_MAX_OUTPUT_CHARS]
        if outcome.artifacts:
            merged = dict(step.artifacts or {})
            merged.update(outcome.artifacts)
            step.artifacts = merged
        if step.status == StepStatus.WAITING_FOR_PREREQUISITE:
            await self._handle_action_required(ctx, stage, step, outcome.output, finished_at)
            return
        ctx.job.progress = JobRepository.compute_progress(ctx.job)
        await ctx.db.commit()

        await self._publish(
            ctx,
            status=step.status.value,
            message=outcome.output.splitlines()[0][:200] if outcome.output else stage.name,
        )

    async def _handle_failure(
        self,
        ctx: JobRunContext,
        stage: StageDefinition,
        step,
        failure: InfraOperationError,
        started_at: dt.datetime,
        finished_at: dt.datetime,
    ) -> None:
        step.status = StepStatus.FAILED
        step.finished_at = finished_at
        step.retryable = failure.retryable and stage.retryable
        step.error_human = (
            f"{failure.human_message}\n\nReason:\n{failure.reason}\n\n"
            f"Recommended action:\n{failure.recommended_action}"
        )
        step.error_technical = failure.technical_detail

        clone_step = ctx.steps_by_key.get("clone_vm")
        vm_created = clone_step is not None and clone_step.status in (
            StepStatus.SUCCEEDED,
            StepStatus.SKIPPED,
        )
        ctx.job.status = (
            JobStatus.PARTIALLY_COMPLETED if vm_created else JobStatus.FAILED
        )
        ctx.job.action_required = None
        if stage.key in {"clone_vm", "configure_hardware", "attach_network_adapter"}:
            ctx.job.infrastructure_status = InfrastructureStatus.FAILED.value
        elif stage.key == "wait_for_guest_os":
            ctx.job.guest_os_status = GuestOsStatus.ERROR.value
            ctx.job.guest_provisioning_status = GuestProvisioningStatus.FAILED.value
        elif stage.key == "wait_for_tools":
            ctx.job.vmware_tools_status = VMwareToolsStatus.ERROR.value
            ctx.job.guest_provisioning_status = GuestProvisioningStatus.FAILED.value
        elif stage.key in {
            "configure_guest_network", "validate_network", "configure_hostname",
            "join_domain", "reboot_guest", "wait_guest_ready",
        }:
            ctx.job.guest_provisioning_status = GuestProvisioningStatus.FAILED.value
        ctx.job.error_summary = failure.human_message
        ctx.job.error_detail = (
            f"{failure.human_message}\nReason: {failure.reason}\n"
            f"Recommended action: {failure.recommended_action}\n\n"
            f"Technical detail:\n{failure.technical_detail}"
        )[:6000]
        ctx.job.current_stage = stage.key
        ctx.job.finished_at = finished_at
        if ctx.job.started_at:
            ctx.job.duration_seconds = (finished_at - ctx.job.started_at).total_seconds()
        ctx.job.progress = JobRepository.compute_progress(ctx.job)

        stage_failures_total.inc(stage=stage.key)
        jobs_total.inc(status=ctx.job.status.value)

        recorder = AuditRecorder(ctx.db)
        await recorder.record(
            AuditAction.JOB_STAGE_FAILED,
            resource_type="provisioning_stage",
            resource_name=stage.name,
            job_id=ctx.job_id,
            username=ctx.actor_username,
            datacenter_id=ctx.request.compute.datacenter_id,
            datacenter_name=ctx.job.datacenter_name,
            result="failure",
            details={"stage": stage.key, "attempt": step.attempt},
            detail_text=failure.summary(),
        )
        await ctx.db.commit()
        await self._publisher.publish_stage(
            str(ctx.job_id), stage=stage.key, status="FAILED",
            progress=ctx.job.progress, message=failure.human_message,
        )
        log.warning("Job %s stage %s failed: %s", ctx.job_id, stage.key, failure.summary())

    # ── terminal transitions ─────────────────────────────────────────────────

    async def _finalize_success(self, ctx: JobRunContext) -> None:
        finished_at = _utcnow()
        ctx.job.status = JobStatus.COMPLETED
        ctx.job.action_required = None
        ctx.job.finished_at = finished_at
        ctx.job.progress = 100
        ctx.job.current_stage = "final_validation"
        if ctx.job.started_at:
            ctx.job.duration_seconds = (finished_at - ctx.job.started_at).total_seconds()

        summary_artifact = {}
        final_step = ctx.steps_by_key.get("final_validation")
        if final_step is not None:
            summary_artifact = (final_step.artifacts or {}).get("summary", {})

        jobs_total.inc(status=JobStatus.COMPLETED.value)
        if ctx.job.duration_seconds:
            job_duration_seconds.observe(ctx.job.duration_seconds, job_type="vm_provisioning")

        await AuditRecorder(ctx.db).record(
            AuditAction.JOB_COMPLETED,
            resource_type="virtual_machine",
            resource_name=ctx.vm_name,
            job_id=ctx.job_id,
            username=ctx.actor_username,
            datacenter_id=ctx.request.compute.datacenter_id,
            datacenter_name=ctx.job.datacenter_name,
            result="success",
            details={
                "computer_name": summary_artifact.get("computer_name"),
                "duration_seconds": ctx.job.duration_seconds,
                "fqdn": summary_artifact.get("fqdn"),
            },
            detail_text=(
                f"Provisioned '{summary_artifact['fqdn']}' successfully."
                if summary_artifact.get("fqdn")
                else f"Provisioned '{ctx.vm_name}' successfully."
            ),
        )
        await ctx.db.commit()
        await self._publisher.publish_stage(
            str(ctx.job_id), stage="final_validation", status="COMPLETED",
            progress=100,
            message=f"Successfully provisioned {ctx.vm_name}"
                    + (f" ({summary_artifact.get('ip_address')})" if summary_artifact.get("ip_address") else ""),
        )
        log.info("Job %s completed for VM %s", ctx.job_id, ctx.vm_name)

    async def _handle_action_required(
        self,
        ctx: JobRunContext,
        stage: StageDefinition,
        step,
        message: str,
        finished_at: dt.datetime,
    ) -> None:
        """Pause a resumable deployment without misreporting a failure."""
        downstream = False
        for candidate in sorted(ctx.job.steps, key=lambda item: item.sequence):
            if candidate.stage_key == stage.key:
                downstream = True
                continue
            if downstream and candidate.status == StepStatus.PENDING:
                candidate.status = StepStatus.WAITING_FOR_PREREQUISITE
                candidate.output = f"Waiting for prerequisite: {stage.name}."
        ctx.job.status = JobStatus.ACTION_REQUIRED
        ctx.job.action_required = message[:2000]
        ctx.job.error_summary = None
        ctx.job.error_detail = None
        ctx.job.current_stage = stage.key
        ctx.job.finished_at = finished_at
        if ctx.job.started_at:
            ctx.job.duration_seconds = (finished_at - ctx.job.started_at).total_seconds()
        ctx.job.progress = JobRepository.compute_progress(ctx.job)
        await ctx.db.commit()
        await self._publisher.publish_stage(
            str(ctx.job_id),
            stage=stage.key,
            status=JobStatus.ACTION_REQUIRED.value,
            progress=ctx.job.progress,
            message=message,
        )

    async def _apply_cancellation(self, ctx: JobRunContext) -> None:
        finished_at = _utcnow()
        for step in ctx.job.steps:
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                step.status = StepStatus.CANCELLED
                step.finished_at = finished_at
        ctx.job.status = JobStatus.CANCELLED
        ctx.job.finished_at = finished_at
        if ctx.job.started_at:
            ctx.job.duration_seconds = (finished_at - ctx.job.started_at).total_seconds()
        ctx.job.progress = JobRepository.compute_progress(ctx.job)

        jobs_total.inc(status=JobStatus.CANCELLED.value)
        await AuditRecorder(ctx.db).record(
            AuditAction.JOB_CANCELLED,
            resource_type="virtual_machine",
            resource_name=ctx.vm_name,
            job_id=ctx.job_id,
            username=ctx.actor_username,
            datacenter_id=ctx.request.compute.datacenter_id,
            datacenter_name=ctx.job.datacenter_name,
            result="cancelled",
        )
        await ctx.db.commit()
        await self._publisher.publish_stage(
            str(ctx.job_id), stage=ctx.job.current_stage, status="CANCELLED",
            progress=ctx.job.progress, message="Provisioning cancelled by user request.",
        )

    async def _publish(self, ctx: JobRunContext, *, status: str, message: str) -> None:
        await self._publisher.publish_stage(
            str(ctx.job_id),
            stage=ctx.job.current_stage,
            status=status,
            progress=ctx.job.progress,
            message=message,
        )
