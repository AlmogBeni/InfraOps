"""Operator-facing application catalog (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DbSession, require
from app.auth.permissions import Permission
from app.models.applications import Application
from app.schemas.applications import ApplicationOut

router = APIRouter(tags=["applications"])


def _app_out(app: Application) -> ApplicationOut:
    return ApplicationOut(
        id=str(app.id),
        name=app.name,
        version=app.version,
        description=app.description,
        installer_type=app.installer_type,
        installer_path=app.installer_path,
        install_arguments=app.install_arguments,
        detection_method=app.detection_method,
        detection_config=dict(app.detection_config or {}),
        timeout_seconds=app.timeout_seconds,
        reboot_required=app.reboot_required,
        enabled=app.enabled,
        dependency_ids=[str(dep.depends_on_id) for dep in app.dependencies],
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


@router.get("/applications", response_model=list[ApplicationOut])
async def list_applications(
    db: DbSession,
    user=require(Permission.JOBS_READ),
    enabled_only: bool = Query(default=True),
) -> list[ApplicationOut]:
    query = select(Application).order_by(Application.name)
    if enabled_only:
        query = query.where(Application.enabled.is_(True))
    result = await db.execute(query)
    return [_app_out(app) for app in result.scalars().all()]
