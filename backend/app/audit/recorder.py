"""Audit event recorder.

Writes append-only rows to ``audit_events`` (protected by a database trigger)
and emits a structured log line. Details payloads pass through defensive
redaction so secrets can never reach the audit trail even if a caller
accidentally includes them.
"""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, redact
from app.models.audit import AuditEvent
from app.models.user import User

log = get_logger("infraops.audit")

_MAX_DETAIL_JSON = 8000


class AuditRecorder:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        action: str,
        *,
        user: User | None = None,
        username: str | None = None,
        resource_type: str | None = None,
        resource_name: str | None = None,
        job_id=None,
        datacenter_id: str | None = None,
        datacenter_name: str | None = None,
        result: str | None = None,
        source_ip: str | None = None,
        details: dict | None = None,
        detail_text: str | None = None,
    ) -> AuditEvent:
        safe_details = redact(details or {})
        serialized = json.dumps(safe_details, default=str)[:_MAX_DETAIL_JSON]

        event = AuditEvent(
            user_id=user.id if user else None,
            username=username or (user.username if user else None),
            action=action,
            resource_type=resource_type,
            resource_name=(resource_name[:255] if resource_name else None),
            job_id=job_id,
            datacenter_id=datacenter_id,
            datacenter_name=datacenter_name,
            result=result,
            source_ip=source_ip,
            details=safe_details,
            detail_text=detail_text,
        )
        self.session.add(event)
        await self.session.flush()

        log.info(
            "audit action=%s user=%s resource=%s/%s job=%s datacenter=%s result=%s details=%s",
            action,
            event.username,
            resource_type,
            resource_name,
            str(job_id) if job_id else "-",
            datacenter_name or datacenter_id or "-",
            result or "-",
            serialized,
        )
        return event


def record_audit(session: AsyncSession) -> AuditRecorder:
    return AuditRecorder(session)


def client_ip(request) -> str | None:
    if request.client is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host
