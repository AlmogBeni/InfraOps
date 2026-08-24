"""Audit log search endpoint."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query

from app.api.deps import DbSession, require
from app.auth.permissions import Permission
from app.repositories.audit import AuditRepository
from app.schemas.audit import AuditEventOut, AuditListResponse

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=AuditListResponse)
async def search_audit(
    db: DbSession,
    user=require(Permission.AUDIT_READ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    action: str | None = Query(default=None),
    username: str | None = Query(default=None),
    job_id: uuid.UUID | None = Query(default=None),
    since: dt.datetime | None = Query(default=None),
    until: dt.datetime | None = Query(default=None),
) -> AuditListResponse:
    repo = AuditRepository(db)
    events, total = await repo.search(
        page=page, page_size=page_size, action=action, username=username,
        job_id=job_id, since=since, until=until,
    )
    return AuditListResponse(
        items=[
            AuditEventOut(
                id=str(event.id), timestamp=event.timestamp,
                user_id=str(event.user_id) if event.user_id else None,
                username=event.username, action=event.action,
                resource_type=event.resource_type, resource_name=event.resource_name,
                job_id=str(event.job_id) if event.job_id else None,
                result=event.result, source_ip=event.source_ip,
                details=event.details or {},
            )
            for event in events
        ],
        total=total, page=page, page_size=page_size,
    )
