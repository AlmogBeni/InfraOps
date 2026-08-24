"""Factory selecting the active VMware implementation from configuration."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import InfrastructureMode, get_settings
from app.secrets.service import SecretsService, get_secrets_service
from app.services.vmware.base import VMwareService
from app.services.vmware.mock import MockVMwareService


def build_vmware_service(
    mode: InfrastructureMode | None = None, secrets: SecretsService | None = None
) -> VMwareService:
    selected = mode or get_settings().infrastructure_mode
    if selected == InfrastructureMode.REAL:
        # Imported lazily so pyvmomi is optional in mock-only deployments.
        from app.services.vmware.vsphere import VsphereVMwareService

        return VsphereVMwareService(secrets or get_secrets_service())
    return MockVMwareService()


@lru_cache
def get_vmware_service() -> VMwareService:
    return build_vmware_service()
