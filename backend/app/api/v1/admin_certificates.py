"""Administration: certificate packages and certificates."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import ClientIp, DbSession, require
from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.auth.permissions import Permission
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.models.certificates import Certificate, CertificatePackage, CertificateStore
from app.schemas.certificates import (
    CertificateCreate,
    CertificateOut,
    CertificatePackageCreate,
    CertificatePackageOut,
    CertificatePackageUpdate,
    CertificateUpdate,
)
from app.services.certificates.store_logic import (
    STORE_FOR_TYPE,
    certificate_metadata,
    make_guest_cert_file_name,
)

router = APIRouter(prefix="/admin", tags=["admin-certificates"])


# ── packages ─────────────────────────────────────────────────────────────────

@router.get("/certificate-packages", response_model=list[CertificatePackageOut])
async def list_packages(db: DbSession, user=require(Permission.ADMIN_CERTIFICATES)):
    result = await db.execute(select(CertificatePackage).order_by(CertificatePackage.name))
    return [
        CertificatePackageOut(
            id=str(p.id), name=p.name, description=p.description, enabled=p.enabled,
            certificates=[
                CertificateOut(
                    id=str(c.id), package_id=str(c.package_id),
                    friendly_name=c.friendly_name, certificate_type=c.certificate_type,
                    destination_store=c.destination_store, subject_cn=c.subject_cn,
                    fingerprint_sha256=c.fingerprint_sha256, not_before=c.not_before,
                    not_after=c.not_after, file_name=c.file_name, enabled=c.enabled,
                )
                for c in p.certificates
            ],
            created_at=p.created_at,
        )
        for p in result.scalars().all()
    ]


@router.post("/certificate-packages", response_model=CertificatePackageOut, status_code=201)
async def create_package(payload: CertificatePackageCreate, db: DbSession,
                         source_ip: ClientIp, user=require(Permission.ADMIN_CERTIFICATES)):
    exists = await db.execute(
        select(CertificatePackage).where(CertificatePackage.name == payload.name)
    )
    if exists.scalar_one_or_none() is not None:
        raise ConflictError(f"Package '{payload.name}' already exists.")
    package = CertificatePackage(**payload.model_dump())
    db.add(package)
    await db.flush()
    await AuditRecorder(db).record(
        AuditAction.CERTIFICATE_PACKAGE_CREATED, user=user, resource_type="certificate_package",
        resource_name=package.name, source_ip=source_ip,
    )
    return CertificatePackageOut(id=str(package.id), name=package.name,
                                 description=package.description, enabled=package.enabled)


@router.put("/certificate-packages/{package_id}", response_model=CertificatePackageOut)
async def update_package(package_id: uuid.UUID, payload: CertificatePackageUpdate,
                         db: DbSession, source_ip: ClientIp,
                         user=require(Permission.ADMIN_CERTIFICATES)):
    package = await db.get(CertificatePackage, package_id)
    if package is None:
        raise NotFoundError("Certificate package not found.")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(package, field, value)
    await AuditRecorder(db).record(
        AuditAction.CERTIFICATE_PACKAGE_UPDATED, user=user, resource_type="certificate_package",
        resource_name=package.name, source_ip=source_ip, details={"fields": sorted(changes)},
    )
    await db.commit()
    await db.refresh(package)
    return CertificatePackageOut(id=str(package.id), name=package.name,
                                 description=package.description, enabled=package.enabled)


@router.delete("/certificate-packages/{package_id}", status_code=204)
async def delete_package(package_id: uuid.UUID, db: DbSession, source_ip: ClientIp,
                         user=require(Permission.ADMIN_CERTIFICATES)):
    package = await db.get(CertificatePackage, package_id)
    if package is None:
        raise NotFoundError("Certificate package not found.")
    name = package.name
    await db.delete(package)
    await AuditRecorder(db).record(
        AuditAction.CERTIFICATE_PACKAGE_DELETED, user=user, resource_type="certificate_package",
        resource_name=name, source_ip=source_ip,
    )


# ── certificates ─────────────────────────────────────────────────────────────

@router.post("/certificates", response_model=CertificateOut, status_code=201)
async def register_certificate(payload: CertificateCreate, db: DbSession,
                               source_ip: ClientIp, user=require(Permission.ADMIN_CERTIFICATES)):
    package = await db.get(CertificatePackage, payload.package_id)
    if package is None:
        raise NotFoundError("Target certificate package does not exist.")

    try:
        meta = certificate_metadata(payload.pem_body)
    except DomainValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DomainValidationError(f"The certificate could not be parsed: {exc}") from exc

    store = STORE_FOR_TYPE[payload.certificate_type]
    cert = Certificate(
        package_id=payload.package_id,
        friendly_name=payload.friendly_name,
        certificate_type=payload.certificate_type,
        destination_store=store,
        subject_cn=meta["subject_cn"],
        fingerprint_sha256=meta["fingerprint_sha256"],
        not_before=meta["not_before"],
        not_after=meta["not_after"],
        pem_body=payload.pem_body.strip(),
        file_name=make_guest_cert_file_name(payload.friendly_name, meta["fingerprint_sha256"]),
        enabled=payload.enabled,
    )
    duplicate = await db.execute(
        select(Certificate).where(Certificate.fingerprint_sha256 == cert.fingerprint_sha256)
    )
    if duplicate.scalar_one_or_none() is not None:
        raise ConflictError("A certificate with this SHA-256 fingerprint is already registered.")

    db.add(cert)
    await db.flush()
    await AuditRecorder(db).record(
        AuditAction.CERTIFICATE_CREATED, user=user, resource_type="certificate",
        resource_name=cert.friendly_name, source_ip=source_ip,
        details={"store": f"LocalMachine\\{store.value}",
                 "thumbprint": cert.fingerprint_sha256},
    )
    return CertificateOut(
        id=str(cert.id), package_id=str(cert.package_id), friendly_name=cert.friendly_name,
        certificate_type=cert.certificate_type, destination_store=cert.destination_store,
        subject_cn=cert.subject_cn, fingerprint_sha256=cert.fingerprint_sha256,
        not_before=cert.not_before, not_after=cert.not_after,
        file_name=cert.file_name, enabled=cert.enabled,
    )


@router.put("/certificates/{certificate_id}", response_model=CertificateOut)
async def update_certificate(certificate_id: uuid.UUID, payload: CertificateUpdate,
                             db: DbSession, source_ip: ClientIp,
                             user=require(Permission.ADMIN_CERTIFICATES)):
    cert = await db.get(Certificate, certificate_id)
    if cert is None:
        raise NotFoundError("Certificate not found.")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(cert, field, value)
    await AuditRecorder(db).record(
        AuditAction.CERTIFICATE_UPDATED, user=user, resource_type="certificate",
        resource_name=cert.friendly_name, source_ip=source_ip, details={"fields": sorted(changes)},
    )
    await db.commit()
    return CertificateOut(
        id=str(cert.id), package_id=str(cert.package_id), friendly_name=cert.friendly_name,
        certificate_type=cert.certificate_type, destination_store=cert.destination_store,
        subject_cn=cert.subject_cn, fingerprint_sha256=cert.fingerprint_sha256,
        not_before=cert.not_before, not_after=cert.not_after,
        file_name=cert.file_name, enabled=cert.enabled,
    )


@router.delete("/certificates/{certificate_id}", status_code=204)
async def delete_certificate(certificate_id: uuid.UUID, db: DbSession, source_ip: ClientIp,
                             user=require(Permission.ADMIN_CERTIFICATES)):
    cert = await db.get(Certificate, certificate_id)
    if cert is None:
        raise NotFoundError("Certificate not found.")
    name = cert.friendly_name
    await db.delete(cert)
    await AuditRecorder(db).record(
        AuditAction.CERTIFICATE_DELETED, user=user, resource_type="certificate",
        resource_name=name, source_ip=source_ip,
    )
