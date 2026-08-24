"""Factory selecting the active guest operations implementation."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import InfrastructureMode, get_settings
from app.services.guest.base import GuestOperations
from app.services.guest.mock import MockGuestOperations


def build_guest_operations(mode: InfrastructureMode | None = None) -> GuestOperations:
    selected = mode or get_settings().infrastructure_mode
    if selected == InfrastructureMode.REAL:
        from app.secrets.service import get_secrets_service
        from app.services.guest.vmware_tools import VMwareToolsGuestOperations

        return VMwareToolsGuestOperations(get_secrets_service())
    return MockGuestOperations()


@lru_cache
def get_guest_operations() -> GuestOperations:
    return build_guest_operations()
