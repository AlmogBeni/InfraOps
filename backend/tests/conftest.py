"""Shared test fixtures."""

from __future__ import annotations

import os
import uuid

import pytest

# Tests explicitly opt into development/mock mode. Runtime defaults are
# production-safe and deliberately fail closed without real configuration.
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("INFRASTRUCTURE_MODE", "mock")
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-with-at-least-32-characters")
os.environ.setdefault("COOKIE_SECURE", "false")

from app.schemas.provisioning import (
    AdapterType,
    ComputeSpec,
    DiskProvisioning,
    DiskSpec,
    FirmwareType,
    GuestSpec,
    HardwareSpec,
    IpMode,
    Ipv4Config,
    NetworkSpec,
    ProvisioningRequest,
    VmSpec,
)


def make_ipv4(**overrides) -> Ipv4Config:
    values = dict(
        address="10.20.30.45",
        prefix=24,
        gateway="10.20.30.1",
        dns_servers=["10.20.1.10", "10.20.1.11"],
    )
    values.update(overrides)
    return Ipv4Config(**values)


def make_request(**overrides) -> ProvisioningRequest:
    """Build a fully valid provisioning request; override any section."""
    request = ProvisioningRequest(
        vm=VmSpec(name="SERVER-PROD-042", description="Test VM"),
        compute=ComputeSpec(
            vcenter_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            datacenter_id="datacenter-21",
            cluster_id="domain-c7",
            host_id="host-11",
            resource_pool_id="resgroup-33",
        ),
        hardware=HardwareSpec(
            cpu=4,
            memory_mb=16384,
            firmware=FirmwareType.EFI,
            secure_boot=True,
            disks=[DiskSpec(size_gb=100, provisioning=DiskProvisioning.THIN)],
        ),
        guest=GuestSpec(
            template_id="ova-corp-windows-2025",
            hostname="SERVER-PROD-042",
        ),
        network=NetworkSpec(
            network_id="dvportgroup-51",
            adapter_type=AdapterType.VMXNET3,
            mode=IpMode.STATIC,
            ipv4=make_ipv4(),
        ),
        certificate_package_ids=[],
        application_ids=[],
    )
    for key, value in overrides.items():
        setattr(request, key, value)
    return request


@pytest.fixture
def sample_request() -> ProvisioningRequest:
    return make_request()
