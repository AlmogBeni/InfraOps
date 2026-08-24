"""Simulated Windows guest operations (``INFRASTRUCTURE_MODE=mock``).

Maintains per-VM state (installed certificates, applications, executed
commands, network configuration) so deployment logic — including idempotency
checks — behaves exactly as it would against a real Windows guest.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re

from app.core.logging import get_logger
from app.services.guest.base import CommandResult, GuestCredentials, GuestOperations
from app.services.vmware.base import VCenterTarget

log = get_logger(__name__)

_LATENCY_PROGRAM = 0.6
_LATENCY_FILE = 0.2

_SECRET_IN_SCRIPT = re.compile(r"(-AsPlainText\s+')[^']*(')")


def _scrub_for_log(text: str) -> str:
    """Never retain credential material inside simulated command logs."""
    return _SECRET_IN_SCRIPT.sub(r"\1[REDACTED]\2", text)


class _MockGuestState:
    def __init__(self) -> None:
        self.cert_thumbprints: set[str] = set()
        self.installed_apps: set[str] = set()
        self.executed_commands: list[str] = []
        self.hostname: str | None = None
        self.ip_address: str | None = None
        self.reboot_pending: bool = False
        self.files: dict[str, bytes] = {}


_MOCK_GUESTS: dict[str, _MockGuestState] = {}


def mock_guest_state(vm_name: str) -> _MockGuestState:
    key = vm_name.upper()
    if key not in _MOCK_GUESTS:
        _MOCK_GUESTS[key] = _MockGuestState()
    return _MOCK_GUESTS[key]


def reset_mock_guests() -> None:
    _MOCK_GUESTS.clear()


class MockGuestOperations(GuestOperations):
    name = "mock-guest"

    async def run_program(
        self,
        target: VCenterTarget,
        vm_name: str,
        credentials: GuestCredentials,
        program_path: str,
        arguments: str,
        timeout_seconds: float,
        working_directory: str | None = None,
    ) -> CommandResult:
        started = dt.datetime.now(dt.timezone.utc)
        await asyncio.sleep(_LATENCY_PROGRAM)

        state = mock_guest_state(vm_name)
        state.executed_commands.append(_scrub_for_log(f"{program_path} {arguments}".strip()))

        # Deterministic failure hook used by tests and failure demonstrations.
        if "__FAIL__" in arguments:
            return CommandResult(
                exit_code=1603,
                stdout="",
                stderr="Simulated installation failure (mock).",
                duration_seconds=(dt.datetime.now(dt.timezone.utc) - started).total_seconds(),
            )

        lowered = f"{program_path} {arguments}".lower()
        stdout = self._simulate(lowered, arguments, state, target, vm_name)
        duration = (dt.datetime.now(dt.timezone.utc) - started).total_seconds()
        log.info("MOCK guest exec on %s (%.2fs)", vm_name, duration)
        return CommandResult(exit_code=0, stdout=stdout, stderr="", duration_seconds=duration)

    # ── behaviour simulation ─────────────────────────────────────────────────

    def _simulate(
        self, lowered: str, arguments: str, state: _MockGuestState,
        target: VCenterTarget, vm_name: str,
    ) -> str:
        # Certificate presence probe (thumbprint embedded in script text).
        if "get-childitem" in lowered and "thumbprint" in lowered:
            probe = re.search(r"([0-9a-f]{40,64})", lowered)
            installed = {t.upper() for t in state.cert_thumbprints}
            return "PRESENT" if (probe and probe.group(1).upper() in installed) else "ABSENT"

        # Certificate import via Import-Certificate / certutil -addstore.
        if "import-certificate" in lowered or ("certutil" in lowered and "-addstore" in lowered):
            probe = re.search(r"([0-9a-f]{40,64})", arguments.lower())
            if probe:
                state.cert_thumbprints.add(probe.group(1).upper())
            else:
                path_match = re.search(r'"([^\"]+\.cer)"', arguments, re.IGNORECASE)
                content = state.files.get(path_match.group(1).lower()) if path_match else None
                if content:
                    from app.services.certificates.store_logic import certificate_metadata

                    fingerprint = certificate_metadata(content.decode("utf-8"))["fingerprint_sha256"]
                    state.cert_thumbprints.add(fingerprint)
            return "MOCK: certificate imported into store"

        # Guest network configuration.
        if "new-netipaddress" in lowered:
            ip_match = re.search(r"-ipaddress\s+'([^']+)'", lowered)
            if ip_match:
                state.ip_address = ip_match.group(1)
                self._sync_ip_to_inventory(target, vm_name, state.ip_address)
            return "NETWORK-CONFIGURED"
        if "set-netipinterface" in lowered and "-dhcp enabled" in lowered:
            state.ip_address = "10.20.30.200"  # simulated DHCP lease
            self._sync_ip_to_inventory(target, vm_name, state.ip_address)
            return "NETWORK-DHCP-ENABLED"

        # Connectivity validation.
        if "test-connection" in lowered or "resolve-dnsname" in lowered:
            return "GW-REACHABLE" if "test-connection" in lowered else "DNS-OK"

        # Hostname / domain operations.
        if "rename-computer" in lowered:
            state.hostname = vm_name.lower()
            return "RENAMED"
        if "add-computer" in lowered:
            return "DOMAIN-JOINED"
        if "restart-computer" in lowered or "shutdown" in lowered:
            state.reboot_pending = False
            return "MOCK: guest restarting"

        # Computername probe used by the hostname stage.
        if "$env:computername" in lowered:
            return state.hostname or vm_name.upper()

        # Generic installer success (MSI/EXE/PowerShell packages).
        if "msiexec" in lowered or ".exe" in lowered or ".ps1" in lowered:
            return "MOCK: installer completed exit 0"

        return "MOCK-OK"

    @staticmethod
    def _sync_ip_to_inventory(target: VCenterTarget, vm_name: str, ip_address: str) -> None:
        try:
            from app.services.vmware.mock import get_mock_inventory

            inventory = get_mock_inventory(target.id)
            for vm in inventory.existing_vms.values():
                if vm.name.upper() == vm_name.upper():
                    vm.ip_address = ip_address
                    break
        except Exception:  # noqa: BLE001 - inventory sync is best-effort simulation
            pass

    # ── file operations ──────────────────────────────────────────────────────

    async def upload_file(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials,
        content: bytes, guest_path: str,
    ) -> None:
        await asyncio.sleep(_LATENCY_FILE)
        mock_guest_state(vm_name).files[guest_path.lower()] = content
        log.info("MOCK guest upload to %s: %s (%d bytes)", vm_name, guest_path, len(content))

    async def download_file(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials, guest_path: str
    ) -> bytes:
        await asyncio.sleep(_LATENCY_FILE)
        return mock_guest_state(vm_name).files.get(guest_path.lower(), b"MOCK-FILE-CONTENT")

    async def delete_file(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials, guest_path: str
    ) -> None:
        await asyncio.sleep(_LATENCY_FILE)
        mock_guest_state(vm_name).files.pop(guest_path.lower(), None)
        log.info("MOCK guest delete on %s: %s", vm_name, guest_path)

    async def file_exists(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials, guest_path: str
    ) -> bool:
        await asyncio.sleep(_LATENCY_FILE)
        return guest_path.lower() in mock_guest_state(vm_name).files
