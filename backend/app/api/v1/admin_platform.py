"""Administration: credential references, platform settings and roles.

Credential endpoints accept pairs, encrypt them before storage, and return metadata only.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import ClientIp, DbSession, require
from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.auth.permissions import Permission
from app.core.errors import ConflictError, NotFoundError
from app.models.platform import SecretReference
from app.models.rbac import Role
from app.schemas.admin import (
    PlatformSettingsOut,
    PlatformSettingsUpdate,
    RoleOut,
    SecretReferenceCreate,
    SecretReferenceOut,
    SecretReferenceUpdate,
)
from app.secrets.encryption import encrypt_secret
from app.services.settings_store import (
    SETTING_ALLOWED_INSTALLER_ROOTS,
    SETTING_DEFAULT_TIMEOUTS,
    SETTING_ENVIRONMENT_LABEL,
    SETTING_VM_NAME_POLICY,
    load_effective,
    save_platform_setting,
)

router = APIRouter(prefix="/admin", tags=["admin-platform"])


# ── Credential references ────────────────────────────────────────────────────

@router.get("/credentials", response_model=list[SecretReferenceOut])
async def list_credentials(db: DbSession, user=require(Permission.ADMIN_CREDENTIALS)):
    result = await db.execute(select(SecretReference).order_by(SecretReference.name))
    return [
        SecretReferenceOut(
            id=str(row.id), name=row.name, provider=row.provider,
            purpose=row.purpose, description=row.description, meta=dict(row.meta or {}),
            configured=bool(row.encrypted_username and row.encrypted_password),
            revision=row.revision, created_at=row.created_at, updated_at=row.updated_at,
        )
        for row in result.scalars().all()
    ]


@router.post("/credentials", response_model=SecretReferenceOut, status_code=201)
async def create_credential(payload: SecretReferenceCreate, db: DbSession,
                            source_ip: ClientIp, user=require(Permission.ADMIN_CREDENTIALS)):
    exists = await db.execute(select(SecretReference).where(SecretReference.name == payload.name))
    if exists.scalar_one_or_none() is not None:
        raise ConflictError(f"A credential reference named '{payload.name}' already exists.")
    values = payload.model_dump(exclude={"username", "password"})
    row = SecretReference(
        **values,
        encrypted_username=encrypt_secret(payload.username),
        encrypted_password=encrypt_secret(payload.password),
        created_by=user.id if user else None,
    )
    db.add(row)
    await db.flush()
    await AuditRecorder(db).record(
        AuditAction.CREDENTIAL_CREATED, user=user, resource_type="credential_reference",
        resource_name=row.name, source_ip=source_ip,
        details={"provider": row.provider, "purpose": row.purpose},
    )
    return SecretReferenceOut(id=str(row.id), name=row.name, provider=row.provider,
                              purpose=row.purpose, description=row.description,
                              meta=dict(row.meta or {}), configured=True,
                              revision=row.revision, created_at=row.created_at,
                              updated_at=row.updated_at)


@router.put("/credentials/{credential_id}", response_model=SecretReferenceOut)
async def update_credential(
    credential_id: uuid.UUID,
    payload: SecretReferenceUpdate,
    db: DbSession,
    source_ip: ClientIp,
    user=require(Permission.ADMIN_CREDENTIALS),
):
    row = await db.get(SecretReference, credential_id)
    if row is None:
        raise NotFoundError("Credential not found.")
    changes = payload.model_dump(exclude_unset=True)
    username = changes.pop("username", None)
    password = changes.pop("password", None)
    for field, value in changes.items():
        setattr(row, field, value)
    if username is not None:
        row.encrypted_username = encrypt_secret(username)
    if password is not None:
        row.encrypted_password = encrypt_secret(password)
    if username is not None or password is not None:
        row.provider = "database"
        row.revision += 1
    await AuditRecorder(db).record(
        AuditAction.CREDENTIAL_UPDATED,
        user=user,
        resource_type="credential_reference",
        resource_name=row.name,
        source_ip=source_ip,
        details={"fields": sorted(payload.model_fields_set)},
    )
    await db.commit()
    await db.refresh(row)
    return SecretReferenceOut(
        id=str(row.id), name=row.name, provider=row.provider, purpose=row.purpose,
        description=row.description, meta=dict(row.meta or {}),
        configured=bool(row.encrypted_username and row.encrypted_password),
        revision=row.revision, created_at=row.created_at, updated_at=row.updated_at,
    )


@router.delete("/credentials/{credential_id}", status_code=204)
async def delete_credential(credential_id: uuid.UUID, db: DbSession, source_ip: ClientIp,
                            user=require(Permission.ADMIN_CREDENTIALS)):
    row = await db.get(SecretReference, credential_id)
    if row is None:
        raise NotFoundError("Credential reference not found.")
    name = row.name
    await db.delete(row)
    await AuditRecorder(db).record(
        AuditAction.CREDENTIAL_DELETED, user=user, resource_type="credential_reference",
        resource_name=name, source_ip=source_ip,
    )


# ── Platform settings ────────────────────────────────────────────────────────

def _settings_out(effective: dict[str, object]) -> PlatformSettingsOut:
    timeouts = effective.get(SETTING_DEFAULT_TIMEOUTS) or {}
    from app.schemas.admin import DefaultTimeouts

    return PlatformSettingsOut(
        vm_name_policy_regex=str(effective.get(SETTING_VM_NAME_POLICY) or ""),
        allowed_installer_roots=list(effective.get(SETTING_ALLOWED_INSTALLER_ROOTS) or []),
        default_timeouts=DefaultTimeouts(
            clone_minutes=int(timeouts.get("clone_minutes", 30)),
            vmware_tools_minutes=int(timeouts.get("vmware_tools_minutes", 15)),
            network_configuration_minutes=int(timeouts.get("network_configuration_minutes", 5)),
            guest_operations_minutes=int(timeouts.get("guest_operations_minutes", 10)),
        ),
        environment_label=str(effective.get(SETTING_ENVIRONMENT_LABEL) or "INTERNAL"),
    )


@router.get("/settings", response_model=PlatformSettingsOut)
async def get_settings_endpoint(db: DbSession, user=require(Permission.ADMIN_SETTINGS)):
    return _settings_out(await load_effective(db))


@router.put("/settings", response_model=PlatformSettingsOut)
async def update_settings(payload: PlatformSettingsUpdate, db: DbSession,
                          source_ip: ClientIp, user=require(Permission.ADMIN_SETTINGS)):
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if SETTING_DEFAULT_TIMEOUTS in changes:
        timeouts = dict(changes[SETTING_DEFAULT_TIMEOUTS])
        changes[SETTING_DEFAULT_TIMEOUTS] = timeouts
    for key, value in changes.items():
        await save_platform_setting(db, key, value, updated_by=user.id if user else None)
    await AuditRecorder(db).record(
        AuditAction.SETTINGS_UPDATED, user=user, resource_type="platform_settings",
        resource_name="platform", source_ip=source_ip, details={"fields": sorted(changes)},
    )
    await db.commit()
    return _settings_out(await load_effective(db))


# ── Roles ────────────────────────────────────────────────────────────────────

@router.get("/roles", response_model=list[RoleOut])
async def list_roles(db: DbSession, user=require(Permission.ADMIN_SETTINGS)):
    result = await db.execute(select(Role).order_by(Role.id))
    return [RoleOut(id=role.id, name=role.name, description=role.description)
            for role in result.scalars().all()]
