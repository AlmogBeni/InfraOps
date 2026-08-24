"""Audit repository — append-only writes and filtered searches."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(self, event: AuditEvent) -> AuditEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def search(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        action: str | None = None,
        username: str | None = None,
        job_id: uuid.UUID | None = None,
        since: dt.datetime | None = None,
        until: dt.datetime | None = None,
    ) -> tuple[list[AuditEvent], int]:
        conditions = []
        if action:
            conditions.append(AuditEvent.action == action)
        if username:
            conditions.append(AuditEvent.username.ilike(f"%{username}%"))
        if job_id is not None:
            conditions.append(AuditEvent.job_id == job_id)
        if since is not None:
            conditions.append(AuditEvent.timestamp >= since)
        if until is not None:
            conditions.append(AuditEvent.timestamp <= until)

        total_result = await self.session.execute(
            select(func.count()).select_from(AuditEvent).where(*conditions)
        )
        total = int(total_result.scalar_one() or 0)

        result = await self.session.execute(
            select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.timestamp.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total
