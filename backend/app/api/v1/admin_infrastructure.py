"""Administration: VMware connections."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import ClientIp, DbSession, require
from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.auth.permissions import Permission
from app.core.errors import ConflictError, NotFoundError
from app.models.infrastructure import VCenterConnection
from app.schemas.admin import (
    VCenterConnectionAdminOut,
    VCenterConnectionCreate,
    VCenterConnectionUpdate,
)
from app.schemas.infrastructure import ConnectionTestResult
from app.services.vmware.factory import get_vmware_service

router = APIRouter(prefix="/admin", tags=["admin-infrastructure"])


async def _get_vcenter(db, vcenter_id: uuid.UUID) -> VCenterConnection:
    row = await db.get(VCenterConnection, vcenter_id)
    if row is None:
        raise NotFoundError("vCenter connection not found.")
    return row


# ── vCenters ─────────────────────────────────────────────────────────────────

@router.get("/vcenters", response_model=list[VCenterConnectionAdminOut])
async def list_vcenters(db: DbSession, user=require(Permission.ADMIN_VCENTERS)):
    result = await db.execute(select(VCenterConnection).order_by(VCenterConnection.name))
    return [
        VCenterConnectionAdminOut(
            id=str(row.id), name=row.name, host=row.host, port=row.port,
            username_secret_ref=row.username_secret_ref,
            password_secret_ref=row.password_secret_ref,
            verify_ssl=row.verify_ssl, enabled=row.enabled, notes=row.notes or "",
            connection_state=row.display_state,
            last_connection_error=row.last_connection_error,
            last_checked_at=row.last_checked_at,
        )
        for row in result.scalars().all()
    ]


@router.post("/vcenters", response_model=VCenterConnectionAdminOut, status_code=201)
async def create_vcenter(
    payload: VCenterConnectionCreate, db: DbSession, source_ip: ClientIp,
    user=require(Permission.ADMIN_VCENTERS),
):
    existing = await db.execute(
        select(VCenterConnection).where(VCenterConnection.name == payload.name)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"A vCenter connection named '{payload.name}' already exists.")
    row = VCenterConnection(**payload.model_dump())
    db.add(row)
    await db.flush()
    await AuditRecorder(db).record(
        AuditAction.VCENTER_CREATED, user=user, resource_type="vcenter",
        resource_name=row.name, source_ip=source_ip,
        details={"host": row.host, "port": row.port},
    )
    return VCenterConnectionAdminOut(
        id=str(row.id), name=row.name, host=row.host, port=row.port,
        username_secret_ref=row.username_secret_ref,
        password_secret_ref=row.password_secret_ref,
        verify_ssl=row.verify_ssl, enabled=row.enabled, notes=row.notes or "",
        connection_state=row.display_state, last_connection_error=None, last_checked_at=None,
    )


@router.put("/vcenters/{vcenter_id}", response_model=VCenterConnectionAdminOut)
async def update_vcenter(
    vcenter_id: uuid.UUID, payload: VCenterConnectionUpdate, db: DbSession,
    source_ip: ClientIp, user=require(Permission.ADMIN_VCENTERS),
):
    row = await _get_vcenter(db, vcenter_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(row, field, value)
    await AuditRecorder(db).record(
        AuditAction.VCENTER_UPDATED, user=user, resource_type="vcenter",
        resource_name=row.name, source_ip=source_ip, details={"fields": sorted(changes)},
    )
    await db.commit()
    await db.refresh(row)
    return VCenterConnectionAdminOut(
        id=str(row.id), name=row.name, host=row.host, port=row.port,
        username_secret_ref=row.username_secret_ref,
        password_secret_ref=row.password_secret_ref,
        verify_ssl=row.verify_ssl, enabled=row.enabled, notes=row.notes or "",
        connection_state=row.display_state,
        last_connection_error=row.last_connection_error,
        last_checked_at=row.last_checked_at,
    )


@router.delete("/vcenters/{vcenter_id}", status_code=204)
async def delete_vcenter(vcenter_id: uuid.UUID, db: DbSession, source_ip: ClientIp,
                         user=require(Permission.ADMIN_VCENTERS)):
    row = await _get_vcenter(db, vcenter_id)
    name = row.name
    await db.delete(row)
    await AuditRecorder(db).record(
        AuditAction.VCENTER_DELETED, user=user, resource_type="vcenter",
        resource_name=name, source_ip=source_ip,
    )


@router.post("/vcenters/{vcenter_id}/test", response_model=ConnectionTestResult)
async def test_vcenter(vcenter_id: uuid.UUID, db: DbSession, source_ip: ClientIp,
                       user=require(Permission.ADMIN_VCENTERS)):
    from app.services.vmware.base import VCenterTarget

    row = await _get_vcenter(db, vcenter_id)
    target = VCenterTarget(
        id=str(row.id), name=row.name, host=row.host, port=row.port,
        username_secret_ref=row.username_secret_ref,
        password_secret_ref=row.password_secret_ref,
        verify_ssl=row.verify_ssl,
    )
    result = await get_vmware_service().test_connection(target)
    row.last_checked_at = dt.datetime.now(dt.timezone.utc)
    row.last_connection_state = "connected" if result.ok else "error"
    row.last_connection_error = None if result.ok else result.detail[:2000]
    await AuditRecorder(db).record(
        AuditAction.VCENTER_TESTED, user=user, resource_type="vcenter",
        resource_name=row.name, result="success" if result.ok else "failure",
        source_ip=source_ip, detail_text=result.detail,
    )
    await db.commit()
    return result
