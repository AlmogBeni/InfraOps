"""In-app notification contracts."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel

from app.models.notifications import NotificationKind


class NotificationOut(BaseModel):
    id: str
    kind: NotificationKind
    title: str
    message: str
    job_id: str | None
    created_at: dt.datetime
    read_at: dt.datetime | None


class NotificationListResponse(BaseModel):
    items: list[NotificationOut]
    unread_count: int
