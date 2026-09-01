"""Audit log search endpoint."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query

from app.api.deps import DbSession, require
from app.audit.actions import audit_action_label
from app.auth.permissions import Permission, roles_grant
from app.repositories.audit import AuditRepository
from app.schemas.audit import AuditEventOut, AuditListResponse

router = APIRouter(tags=["audit"])

_INTERNAL_DETAIL_KEYS = {"id", "artifacts", "technical_error", "technical_detail"}


def _public_details(value):
    """Remove internal identifiers/artifacts from non-administrator detail views."""
    if isinstance(value, dict):
        return {
            key: _public_details(item)
            for key, item in value.items()
            if key.lower() not in _INTERNAL_DETAIL_KEYS
            and not key.lower().endswith(("_id", "_ids"))
        }
    if isinstance(value, list):
        return [_public_details(item) for item in value]
    return value


def _audit_out(
    event,
    datacenter_name: str | None,
    *,
    include_technical: bool,
) -> AuditEventOut:
    return AuditEventOut(
        id=str(event.id),
        timestamp=event.timestamp,
        user_id=str(event.user_id) if event.user_id else None,
        username=event.username,
        action=event.action,
        action_label=audit_action_label(event.action),
        resource_type=event.resource_type,
        resource_name=event.resource_name,
        job_id=str(event.job_id) if event.job_id else None,
        datacenter_id=event.datacenter_id,
        datacenter_name=datacenter_name,
        result=event.result,
        source_ip=event.source_ip,
        details=(event.details or {}) if include_technical else _public_details(event.details or {}),
        # Recorder summaries are deliberately human-facing. Only structured
        # identifiers, artifacts, and technical fields remain admin-only.
        detail_text=event.detail_text,
    )


@router.get("/audit", response_model=AuditListResponse)
async def search_audit(
    db: DbSession,
    user=require(Permission.AUDIT_READ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    action: str | None = Query(default=None),
    username: str | None = Query(default=None),
    resource_type: str | None = Query(default=None, max_length=60),
    result: str | None = Query(default=None, max_length=30),
    datacenter: str | None = Query(default=None, max_length=255),
    datacenter_id: str | None = Query(default=None, max_length=120, deprecated=True),
    search: str | None = Query(default=None, max_length=200),
    job_id: uuid.UUID | None = Query(default=None),
    since: dt.datetime | None = Query(default=None),
    until: dt.datetime | None = Query(default=None),
) -> AuditListResponse:
    repo = AuditRepository(db)
    events, total = await repo.search(
        page=page, page_size=page_size, action=action, username=username,
        resource_type=resource_type, result=result, datacenter=datacenter or datacenter_id,
        search=search,
        job_id=job_id, since=since, until=until,
    )
    include_technical = roles_grant(user.role_names, Permission.ADMIN_SETTINGS)
    return AuditListResponse(
        items=[
            _audit_out(
                event,
                datacenter_name,
                include_technical=include_technical,
            )
            for event, datacenter_name in events
        ],
        total=total, page=page, page_size=page_size,
    )
