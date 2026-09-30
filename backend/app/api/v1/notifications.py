"""The signed-in user's own in-app notifications."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, select, update

from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.models.notifications import Notification
from app.schemas.notifications import NotificationListResponse, NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _out(notification: Notification) -> NotificationOut:
    return NotificationOut(
        id=str(notification.id),
        kind=notification.kind,
        title=notification.title,
        message=notification.message,
        job_id=str(notification.job_id) if notification.job_id else None,
        created_at=notification.created_at,
        read_at=notification.read_at,
    )


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
) -> NotificationListResponse:
    rows = await db.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id)
        .limit(limit)
    )
    unread = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return NotificationListResponse(
        items=[_out(row) for row in rows.scalars().all()],
        unread_count=int(unread or 0),
    )


@router.post(
    "/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def mark_notification_read(
    notification_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    result = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user.id)
        .values(read_at=func.coalesce(Notification.read_at, dt.datetime.now(dt.UTC)))
    )
    if result.rowcount == 0:
        # Another user's notification is indistinguishable from a missing one.
        raise NotFoundError("Notification not found.")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def mark_all_notifications_read(db: DbSession, user: CurrentUser) -> Response:
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=dt.datetime.now(dt.UTC))
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
