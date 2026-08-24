"""Administration: application catalog CRUD with dependency management."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import ClientIp, DbSession, require
from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.auth.permissions import Permission
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.models.applications import Application, ApplicationDependency
from app.schemas.applications import ApplicationCreate, ApplicationOut, ApplicationUpdate
from app.services.applications.resolver import AppNode, resolve_install_order

router = APIRouter(prefix="/admin/applications", tags=["admin-applications"])


def _app_out(app: Application) -> ApplicationOut:
    return ApplicationOut(
        id=str(app.id), name=app.name, version=app.version, description=app.description,
        installer_type=app.installer_type, installer_path=app.installer_path,
        install_arguments=app.install_arguments, detection_method=app.detection_method,
        detection_config=dict(app.detection_config or {}),
        timeout_seconds=app.timeout_seconds, reboot_required=app.reboot_required,
        enabled=app.enabled,
        dependency_ids=[str(dep.depends_on_id) for dep in app.dependencies],
        created_at=app.created_at, updated_at=app.updated_at,
    )


async def _assert_acyclic(db, *, edited_id: uuid.UUID | None, dependency_ids: list[uuid.UUID]) -> None:
    """Validate the whole dependency graph stays acyclic after a change."""
    result = await db.execute(select(Application))
    catalog: dict[uuid.UUID, AppNode] = {}
    for app in result.scalars().all():
        deps = {dep.depends_on_id for dep in app.dependencies}
        if edited_id is not None and app.id == edited_id:
            deps = set(dependency_ids)
        catalog[app.id] = AppNode(id=app.id, name=app.name, enabled=app.enabled,
                                  dependency_ids=frozenset(deps))
    if edited_id is not None and edited_id not in catalog:
        catalog[edited_id] = AppNode(id=edited_id, name="(new)", enabled=True,
                                     dependency_ids=frozenset(dependency_ids))
    _, errors = resolve_install_order(list(catalog), catalog)
    cycle_errors = [e for e in errors if "Circular" in e or "does not exist" in e]
    if cycle_errors:
        raise DomainValidationError("Dependency validation failed: " + " ".join(cycle_errors))


async def _replace_dependencies(db, app: Application, dependency_ids: list[uuid.UUID]) -> None:
    unique_ids = list(dict.fromkeys(dependency_ids))
    if app.id in unique_ids:
        raise DomainValidationError("An application cannot depend on itself.")
    for dep_id in unique_ids:
        target = await db.get(Application, dep_id)
        if target is None:
            raise DomainValidationError(f"Dependency '{dep_id}' does not exist.")
    app.dependencies = [
        ApplicationDependency(app_id=app.id, depends_on_id=dep_id) for dep_id in unique_ids
    ]


@router.get("", response_model=list[ApplicationOut])
async def list_applications(db: DbSession, user=require(Permission.ADMIN_APPLICATIONS)):
    result = await db.execute(select(Application).order_by(Application.name))
    return [_app_out(app) for app in result.scalars().all()]


@router.post("", response_model=ApplicationOut, status_code=201)
async def create_application(payload: ApplicationCreate, db: DbSession, source_ip: ClientIp,
                             user=require(Permission.ADMIN_APPLICATIONS)):
    exists = await db.execute(select(Application).where(Application.name == payload.name))
    if exists.scalar_one_or_none() is not None:
        raise ConflictError(f"An application named '{payload.name}' already exists.")

    data = payload.model_dump(exclude={"dependency_ids"})
    data.pop("created_by", None)
    app = Application(**data, created_by=user.id if user else None)
    db.add(app)
    await db.flush()
    await _replace_dependencies(db, app, [uuid.UUID(str(d)) for d in payload.dependency_ids])
    await _assert_acyclic(db, edited_id=app.id,
                          dependency_ids=[uuid.UUID(str(d)) for d in payload.dependency_ids])
    await AuditRecorder(db).record(
        AuditAction.APPLICATION_CREATED, user=user, resource_type="application",
        resource_name=app.name, source_ip=source_ip,
        details={"installer_type": app.installer_type.value},
    )
    await db.commit()
    await db.refresh(app)
    return _app_out(app)


@router.put("/{application_id}", response_model=ApplicationOut)
async def update_application(application_id: uuid.UUID, payload: ApplicationUpdate,
                             db: DbSession, source_ip: ClientIp,
                             user=require(Permission.ADMIN_APPLICATIONS)):
    app = await db.get(Application, application_id)
    if app is None:
        raise NotFoundError("Application not found.")
    changes = payload.model_dump(exclude_unset=True)
    new_dependency_ids = changes.pop("dependency_ids", None)
    for field, value in changes.items():
        setattr(app, field, value)
    if new_dependency_ids is not None:
        parsed = [uuid.UUID(str(d)) for d in new_dependency_ids]
        await _replace_dependencies(db, app, parsed)
        await _assert_acyclic(db, edited_id=app.id, dependency_ids=parsed)
    await AuditRecorder(db).record(
        AuditAction.APPLICATION_UPDATED, user=user, resource_type="application",
        resource_name=app.name, source_ip=source_ip,
        details={"fields": sorted(set(changes) | ({"dependency_ids"} if new_dependency_ids is not None else set()))},
    )
    await db.commit()
    await db.refresh(app)
    return _app_out(app)


@router.delete("/{application_id}", status_code=204)
async def delete_application(application_id: uuid.UUID, db: DbSession, source_ip: ClientIp,
                             user=require(Permission.ADMIN_APPLICATIONS)):
    app = await db.get(Application, application_id)
    if app is None:
        raise NotFoundError("Application not found.")
    dependents = await db.execute(
        select(ApplicationDependency).where(ApplicationDependency.depends_on_id == application_id)
    )
    if dependents.first() is not None:
        raise ConflictError("Other applications depend on this entry — remove those dependencies first.")
    name = app.name
    await db.delete(app)
    await AuditRecorder(db).record(
        AuditAction.APPLICATION_DELETED, user=user, resource_type="application",
        resource_name=name, source_ip=source_ip,
    )
