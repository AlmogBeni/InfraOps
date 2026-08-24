"""Audit log schemas."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    timestamp: dt.datetime
    user_id: str | None = None
    username: str | None = None
    action: str
    resource_type: str | None = None
    resource_name: str | None = None
    job_id: str | None = None
    result: str | None = None
    source_ip: str | None = None
    details: dict = {}


class AuditListResponse(BaseModel):
    items: list[AuditEventOut]
    total: int
    page: int
    page_size: int
