"""In-app notifications telling the requesting engineer how a job ended."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.jobs import ProvisioningJob
from app.models.notifications import Notification, NotificationKind

_MAX_MESSAGE_CHARS = 2000


def completed_message(job: ProvisioningJob, summary: dict, checks_passed: int) -> tuple[str, str]:
    identity = summary.get("fqdn") or summary.get("computer_name") or job.vm_name
    address = summary.get("ip_address")
    details = f"{identity}" + (f" · {address}" if address else "")
    checks = f" All {checks_passed} final checks passed." if checks_passed else ""
    return (
        f"{job.vm_name} is ready",
        f"Deployment finished and was verified: {details}.{checks}",
    )


def failed_message(job: ProvisioningJob, stage_name: str, reason: str) -> tuple[str, str]:
    return (
        f"{job.vm_name} deployment failed",
        f"'{stage_name}' failed: {reason}",
    )


def interrupted_message(job: ProvisioningJob, reason: str) -> tuple[str, str]:
    return (
        f"{job.vm_name} deployment was interrupted",
        f"{reason} Retry the job; stages that already finished are not repeated.",
    )


def notify_requester(
    db: AsyncSession,
    job: ProvisioningJob,
    kind: NotificationKind,
    title: str,
    message: str,
) -> Notification | None:
    """Queue a notification for the job's requester (committed by the caller)."""
    if job.requested_by_user_id is None:
        return None
    notification = Notification(
        user_id=job.requested_by_user_id,
        job_id=job.id,
        kind=kind.value,
        title=title[:200],
        message=message[:_MAX_MESSAGE_CHARS],
    )
    db.add(notification)
    return notification
