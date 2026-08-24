"""Idempotent development seed data.

Run via ``python -m app.seed`` (the backend container does this automatically
after migrations). Contains NO real credentials — passwords come from
environment configuration and all certificates are generated locally.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.session import session_factory
from app.models.applications import Application, ApplicationDependency, DetectionMethod, InstallerType
from app.models.certificates import Certificate, CertificatePackage, CertificateStore, CertificateType
from app.models.infrastructure import Site, VCenterConnection
from app.models.platform import SecretReference
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.services.certificates.store_logic import certificate_metadata, make_guest_cert_file_name

log = get_logger(__name__)

MOCK_VCENTER_ID = "11111111-1111-4111-8111-111111111111"


def _generate_development_certificates() -> list[tuple[str, CertificateType, str]]:
    """Generate a small local root/intermediate chain for development."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    def name(cn: str):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    now = dt.datetime.now(dt.timezone.utc)

    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(name("InfraOps Development Root CA"))
        .issuer_name(name("InfraOps Development Root CA"))
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(root_key, hashes.SHA256())
    )

    issuing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuing_cert = (
        x509.CertificateBuilder()
        .subject_name(name("InfraOps Development Issuing CA 01"))
        .issuer_name(root_cert.subject)
        .public_key(issuing_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1825))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(root_key, hashes.SHA256())
    )

    def pem(cert) -> str:
        return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")

    return [
        ("InfraOps Development Root CA", CertificateType.ROOT, pem(root_cert)),
        ("InfraOps Development Issuing CA 01", CertificateType.INTERMEDIATE, pem(issuing_cert)),
    ]


async def seed() -> None:
    settings = get_settings()
    async with session_factory() as db:
        # ── Roles ────────────────────────────────────────────────────────────
        roles: dict[str, Role] = {}
        for role_name, description in (
            ("viewer", "Read-only access to jobs, history and audit logs."),
            ("operator", "Can provision VMs and manage guest configuration."),
            ("administrator", "Full administrative access to the platform."),
        ):
            existing = await db.execute(select(Role).where(Role.name == role_name))
            role = existing.scalar_one_or_none()
            if role is None:
                role = Role(name=role_name, description=description)
                db.add(role)
                await db.flush()
            roles[role_name] = role

        # ── Users ────────────────────────────────────────────────────────────
        for username, email, full_name, role_name in (
            (settings.dev_admin_username, settings.dev_admin_email, "Platform Administrator", "administrator"),
            ("operator", "operator@example.internal", "Infrastructure Operator", "operator"),
            ("viewer", "viewer@example.internal", "Read-Only Analyst", "viewer"),
        ):
            existing = await db.execute(select(User).where(User.username == username))
            user = existing.scalar_one_or_none()
            if user is None:
                user = User(
                    username=username,
                    email=email,
                    full_name=full_name,
                    password_hash=hash_password(settings.dev_admin_password),
                )
                db.add(user)
                await db.flush()
                db.add(UserRole(user_id=user.id, role_id=roles[role_name].id))
                log.info("Seeded user '%s' (%s)", username, role_name)

        # ── Mock vCenter + site ──────────────────────────────────────────────
        import uuid as uuid_module

        mock_id = uuid_module.UUID(MOCK_VCENTER_ID)
        vcenter = await db.get(VCenterConnection, mock_id)
        if vcenter is None:
            vcenter = VCenterConnection(
                id=mock_id,
                name="Mock Production vCenter",
                host="vcsa-prod.company.local",
                port=443,
                username_secret_ref="vcsa-prod/username",
                password_secret_ref="vcsa-prod/password",
                verify_ssl=True,
                enabled=True,
                notes="Simulated estate used when INFRASTRUCTURE_MODE=mock.",
                last_connection_state="connected",
            )
            db.add(vcenter)
            await db.flush()

        site_exists = await db.execute(
            select(Site).where(Site.vcenter_id == mock_id, Site.name == "HQ")
        )
        if site_exists.scalar_one_or_none() is None:
            db.add(Site(name="HQ", description="Corporate headquarters",
                        vcenter_id=mock_id, datacenter_moref="datacenter-21"))
            log.info("Seeded site HQ")

        # ── Certificates ─────────────────────────────────────────────────────
        package_exists = await db.execute(
            select(CertificatePackage).where(
                CertificatePackage.name == "Corporate Standard Certificates"
            )
        )
        package = package_exists.scalar_one_or_none()
        if package is None:
            package = CertificatePackage(
                name="Corporate Standard Certificates",
                description="Development trust chain deployed to every provisioned server.",
            )
            db.add(package)
            await db.flush()

        cert_count = await db.execute(
            select(Certificate).where(Certificate.package_id == package.id)
        )
        if cert_count.scalars().first() is None:
            for friendly_name, cert_type, pem_body in _generate_development_certificates():
                meta = certificate_metadata(pem_body)
                db.add(
                    Certificate(
                        package_id=package.id,
                        friendly_name=friendly_name,
                        certificate_type=cert_type,
                        destination_store=CertificateStore.ROOT
                        if cert_type == CertificateType.ROOT
                        else CertificateStore.CA,
                        subject_cn=meta["subject_cn"],
                        fingerprint_sha256=meta["fingerprint_sha256"],
                        not_before=meta["not_before"],
                        not_after=meta["not_after"],
                        pem_body=pem_body,
                        file_name=make_guest_cert_file_name(friendly_name, meta["fingerprint_sha256"]),
                    )
                )
            log.info("Seeded development certificate chain")

        # ── Applications ─────────────────────────────────────────────────────
        catalog: dict[str, dict] = {
            "VC++ Runtime": dict(
                version="14.40",
                description="Microsoft Visual C++ runtime redistributable.",
                installer_type=InstallerType.EXE,
                installer_path=r"\\software.company.local\packages\runtime\vc_redist.x64.exe",
                install_arguments="/install /quiet /norestart",
                detection_method=DetectionMethod.FILE_EXISTS,
                detection_config={"path": r"C:\Windows\System32\vcruntime140.dll"},
                reboot_required=False,
            ),
            "Monitoring Agent": dict(
                version="6.2.1",
                description="Enterprise monitoring agent.",
                installer_type=InstallerType.MSI,
                installer_path=r"\\software.company.local\packages\monitoring\agent.msi",
                install_arguments="/qn /norestart",
                detection_method=DetectionMethod.MSI_PRODUCT_CODE,
                detection_config={"product_code": "{A1B2C3D4-E5F6-4A7B-8C9D-0E1F2A3B4C5D}"},
                reboot_required=False,
            ),
            "Backup Agent": dict(
                version="9.0.4",
                description="Central backup client.",
                installer_type=InstallerType.MSI,
                installer_path=r"\\software.company.local\packages\backup\agent.msi",
                install_arguments="/qn /norestart",
                detection_method=DetectionMethod.SERVICE_EXISTS,
                detection_config={"service_name": "BackupAgentSvc"},
                reboot_required=False,
            ),
            "EDR Agent": dict(
                version="4.8.0",
                description="Endpoint detection and response agent.",
                installer_type=InstallerType.EXE,
                installer_path=r"\\software.company.local\packages\edr\agent-setup.exe",
                install_arguments="/S /noreboot",
                detection_method=DetectionMethod.SERVICE_EXISTS,
                detection_config={"service_name": "EdrSensor"},
                reboot_required=True,
            ),
            "7-Zip": dict(
                version="24.08",
                description="File archiver.",
                installer_type=InstallerType.MSI,
                installer_path=r"\\software.company.local\packages\tools\7z2408-x64.msi",
                install_arguments="/qn /norestart",
                detection_method=DetectionMethod.REGISTRY_KEY,
                detection_config={
                    "key_path": r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{23170F69-40C1-2702-2408-000001000000}",
                    "value_name": "DisplayName",
                },
                reboot_required=False,
            ),
        }

        apps_by_name: dict[str, Application] = {}
        for name, spec in catalog.items():
            existing = await db.execute(select(Application).where(Application.name == name))
            app = existing.scalar_one_or_none()
            if app is None:
                app = Application(name=name, **spec)
                db.add(app)
                await db.flush()
                log.info("Seeded application '%s'", name)
            apps_by_name[name] = app

        monitoring = apps_by_name["Monitoring Agent"]
        has_dep = await db.execute(
            select(ApplicationDependency).where(
                ApplicationDependency.app_id == monitoring.id,
                ApplicationDependency.depends_on_id == apps_by_name["VC++ Runtime"].id,
            )
        )
        if has_dep.scalar_one_or_none() is None:
            db.add(ApplicationDependency(
                app_id=monitoring.id, depends_on_id=apps_by_name["VC++ Runtime"].id
            ))

        # ── Secret references ────────────────────────────────────────────────
        for ref_name, description in (
            ("vcsa-prod", "Mock vCenter service account (SECRETS_VCSA_PROD_* env vars)."),
            ("guest-local-admin", "Local Windows administrator used for guest automation."),
            ("domain-join", "Domain account used for automatic domain joins."),
        ):
            exists = await db.execute(select(SecretReference).where(SecretReference.name == ref_name))
            if exists.scalar_one_or_none() is None:
                db.add(SecretReference(name=ref_name, provider="env", description=description))

        await db.commit()
        log.info("Seed data verified/created successfully.")


if __name__ == "__main__":
    configure_logging(get_settings().log_level, "console")
    asyncio.run(seed())
