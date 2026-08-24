"""Windows guest OS operations layer.

Implementations:

* :class:`app.services.guest.mock.MockGuestOperations` — simulated guest used
  in ``INFRASTRUCTURE_MODE=mock``.
* :class:`app.services.guest.vmware_tools.VMwareToolsGuestOperations` —
  production adapter executing controlled programs through the VMware Tools
  guest API (environment dependent).

Commands are always built from *structured, validated parameters*; free-form
operator input never reaches this layer.
"""

from app.services.guest.base import CommandResult, GuestCredentials, GuestOperations
from app.services.guest.factory import build_guest_operations

__all__ = ["CommandResult", "GuestCredentials", "GuestOperations", "build_guest_operations"]
