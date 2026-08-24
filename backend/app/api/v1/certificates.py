"""Operator-facing certificate package catalog (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DbSession, require
from app.auth.permissions import Permission
from app.models.certificates import CertificatePackage
from app.schemas.certificates import CertificateOut, CertificatePackageOut

router = APIRouter(tags=["certificates"])


def _package_out(package: CertificatePackage, enabled_only: bool) -> CertificatePackageOut:
    certificates = [
        CertificateOut(
            id=str(cert.id),
            package_id=str(cert.package_id),
            friendly_name=cert.friendly_name,
            certificate_type=cert.certificate_type,
            destination_store=cert.destination_store,
            subject_cn=cert.subject_cn,
            fingerprint_sha256=cert.fingerprint_sha256,
            not_before=cert.not_before,
            not_after=cert.not_after,
            file_name=cert.file_name,
            enabled=cert.enabled,
        )
        for cert in package.certificates
        if (cert.enabled or not enabled_only)
    ]
    return CertificatePackageOut(
        id=str(package.id),
        name=package.name,
        description=package.description,
        enabled=package.enabled,
        certificates=certificates,
        created_at=package.created_at,
    )


@router.get("/certificate-packages", response_model=list[CertificatePackageOut])
async def list_certificate_packages(
    db: DbSession,
    user=require(Permission.JOBS_READ),
    enabled_only: bool = Query(default=True),
) -> list[CertificatePackageOut]:
    query = select(CertificatePackage).order_by(CertificatePackage.name)
    if enabled_only:
        query = query.where(CertificatePackage.enabled.is_(True))
    result = await db.execute(query)
    return [_package_out(package, enabled_only) for package in result.scalars().all()]
