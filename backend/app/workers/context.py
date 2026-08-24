"""Per-job execution context shared by pipeline stages."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.infrastructure import VCenterConnection
from app.models.jobs import ProvisioningJob
from app.schemas.provisioning import ProvisioningRequest
from app.secrets.service import SecretsService
from app.services.certificates.deployer import CertificateDeployer
from app.services.applications.installer import ApplicationInstaller
from app.services.guest.base import GuestCredentials, GuestOperations
from app.services.vmware.base import VMwareService, VmRef, VCenterTarget
from app.workers.events import JobEventPublisher


@dataclass
class JobRunContext:
    """Everything a pipeline stage may need. Stages stay small by delegating
    to this context instead of receiving long parameter lists."""

    db: AsyncSession
    job: ProvisioningJob
    request: ProvisioningRequest
    target: VCenterTarget
    vmware: VMwareService
    guest_ops: GuestOperations
    cert_deployer: CertificateDeployer
    app_installer: ApplicationInstaller
    secrets: SecretsService
    publisher: JobEventPublisher
    settings: Settings = field(default_factory=get_settings)

    # Populated during execution:
    vm_ref: VmRef | None = None
    guest_credentials: GuestCredentials | None = None
    reboot_required: bool = False
    hostname_changed: bool = False

    @property
    def job_id(self) -> uuid.UUID:
        return self.job.id

    @property
    def vm_name(self) -> str:
        return self.request.vm.name

    @property
    def steps_by_key(self) -> dict:
        return {step.stage_key: step for step in self.job.steps}

    async def resolve_guest_credentials(self) -> GuestCredentials:
        """Resolve guest credentials once per run (never logged, never persisted)."""
        if self.guest_credentials is not None:
            return self.guest_credentials
        # VMware Tools authenticates to the guest before it has joined the
        # domain. Domain-join credentials are used only by Add-Computer inside
        # the guest and must never replace the template's local administrator.
        base = "guest-local-admin"
        username_ref = f"{base}/username"
        password_ref = f"{base}/password"
        username = await self.secrets.get_secret(username_ref)
        password = await self.secrets.get_secret(password_ref)
        self.guest_credentials = GuestCredentials(username=username, password=password)
        return self.guest_credentials


async def load_request_payload(job: ProvisioningJob) -> ProvisioningRequest:
    if job.request is None:
        raise RuntimeError(f"Job {job.id} has no stored provisioning request.")
    return ProvisioningRequest.model_validate(job.request.payload)


async def build_vcenter_target(db: AsyncSession, vcenter_id: uuid.UUID) -> VCenterTarget:
    from sqlalchemy import select

    result = await db.execute(select(VCenterConnection).where(VCenterConnection.id == vcenter_id))
    connection = result.scalar_one_or_none()
    if connection is None:
        raise LookupError(f"vCenter connection '{vcenter_id}' is no longer registered.")
    if not connection.enabled:
        raise LookupError(f"vCenter connection '{connection.name}' is disabled.")
    return VCenterTarget(
        id=str(connection.id),
        name=connection.name,
        host=connection.host,
        port=connection.port,
        username_secret_ref=connection.username_secret_ref,
        password_secret_ref=connection.password_secret_ref,
        verify_ssl=connection.verify_ssl,
    )
