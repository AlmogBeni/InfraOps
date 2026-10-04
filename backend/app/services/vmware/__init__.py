"""VMware vSphere integration layer.

Implementations:

* :class:`app.services.vmware.mock.MockVMwareService` — fully functional
  in-memory simulation used in ``INFRASTRUCTURE_MODE=mock``.
* :class:`app.services.vmware.vsphere.VsphereVMwareService` — production
  pyvmomi-based adapter (environment dependent).
"""

from app.services.vmware.base import (
    PowerStateInfo,
    VCenterTarget,
    VmCreateSpec,
    VmRef,
    VMwareService,
)
from app.services.vmware.factory import build_vmware_service, get_vmware_service

__all__ = [
    "PowerStateInfo",
    "VCenterTarget",
    "VmCreateSpec",
    "VMwareService",
    "VmRef",
    "build_vmware_service",
    "get_vmware_service",
]
