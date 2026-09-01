"""Structured operational log search endpoint."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query

from app.api.deps import DbSession, require
from app.auth.permissions import Permission, roles_grant
from app.repositories.logs import LogRepository
from app.schemas.logs import LogListResponse, LogSeverity

router = APIRouter(tags=["logs"])


@router.get("/logs", response_model=LogListResponse)
async def search_logs(
    db: DbSession,
    user=require(Permission.JOBS_READ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    severity: LogSeverity | None = Query(default=None),
    component: str | None = Query(default=None, max_length=80),
    datacenter: str | None = Query(default=None, max_length=255),
    datacenter_id: str | None = Query(default=None, max_length=120, deprecated=True),
    job_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    since: dt.datetime | None = Query(default=None),
    until: dt.datetime | None = Query(default=None),
) -> LogListResponse:
    items, total = await LogRepository(db).search(
        page=page,
        page_size=page_size,
        severity=severity,
        component=component,
        datacenter=datacenter or datacenter_id,
        job_id=job_id,
        search=search,
        since=since,
        until=until,
        include_technical=roles_grant(user.role_names, Permission.ADMIN_SETTINGS),
    )
    return LogListResponse(items=items, total=total, page=page, page_size=page_size)
