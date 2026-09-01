"""Audit repository — append-only writes and filtered searches."""

from __future__ import annotations

import datetime as dt
import re
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent
from app.models.jobs import ProvisioningJob


def _normalized_timestamp(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


def normalized_action_term(value: str) -> str:
    """Normalize display labels such as ``VM created`` to canonical action form."""
    return re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").upper()


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
        resource_type: str | None = None,
        result: str | None = None,
        datacenter: str | None = None,
        search: str | None = None,
        job_id: uuid.UUID | None = None,
        since: dt.datetime | None = None,
        until: dt.datetime | None = None,
    ) -> tuple[list[tuple[AuditEvent, str | None]], int]:
        conditions = []
        if action:
            canonical_action = normalized_action_term(action)
            conditions.append(AuditEvent.action == canonical_action)
        if username:
            conditions.append(AuditEvent.username.ilike(f"%{username}%"))
        if resource_type:
            conditions.append(AuditEvent.resource_type == resource_type)
        if result:
            conditions.append(AuditEvent.result == result)
        resolved_datacenter_name = func.coalesce(
            AuditEvent.datacenter_name,
            ProvisioningJob.datacenter_name,
        )
        if datacenter:
            pattern = f"%{datacenter}%"
            conditions.append(
                or_(
                    resolved_datacenter_name.ilike(pattern),
                    AuditEvent.datacenter_id == datacenter,
                    ProvisioningJob.datacenter_id == datacenter,
                )
            )
        if search:
            pattern = f"%{search}%"
            search_terms = [
                AuditEvent.action.ilike(pattern),
                AuditEvent.username.ilike(pattern),
                AuditEvent.resource_type.ilike(pattern),
                AuditEvent.resource_name.ilike(pattern),
                AuditEvent.detail_text.ilike(pattern),
            ]
            canonical_search = normalized_action_term(search)
            if canonical_search:
                search_terms.append(AuditEvent.action.ilike(f"%{canonical_search}%"))
            conditions.append(or_(*search_terms))
        if job_id is not None:
            conditions.append(AuditEvent.job_id == job_id)
        if since is not None:
            conditions.append(AuditEvent.timestamp >= _normalized_timestamp(since))
        if until is not None:
            conditions.append(AuditEvent.timestamp <= _normalized_timestamp(until))

        total_result = await self.session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .outerjoin(ProvisioningJob, ProvisioningJob.id == AuditEvent.job_id)
            .where(*conditions)
        )
        total = int(total_result.scalar_one() or 0)

        result = await self.session.execute(
            select(AuditEvent, resolved_datacenter_name.label("datacenter_name"))
            .outerjoin(ProvisioningJob, ProvisioningJob.id == AuditEvent.job_id)
            .where(*conditions)
            .order_by(AuditEvent.timestamp.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [(event, name) for event, name in result.all()], total
