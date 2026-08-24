"""IP conflict validation with pluggable providers.

A failed ICMP ping does **not** prove an address is unused — firewalls drop
echo requests routinely. The orchestrator therefore aggregates several
independent sources and reports an explicit confidence statement so engineers
understand exactly what was (and was not) checked.
"""

from __future__ import annotations

import asyncio
import socket
from abc import ABC, abstractmethod

from app.core.logging import get_logger
from app.schemas.provisioning import ConflictProviderStatus, IpConflictReport, ProviderResult
from app.services.vmware.base import VCenterTarget, VMwareService

log = get_logger(__name__)

_ICMP_TIMEOUT_SECONDS = 1.5


class ConflictCheckProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def check(self, address: str, prefix: int) -> ProviderResult: ...


class IcmpPingProvider(ConflictCheckProvider):
    """Best-effort ICMP echo probe using the platform ping utility."""

    name = "ICMP"

    async def check(self, address: str, prefix: int) -> ProviderResult:
        import asyncio
        import os
        import re

        if os.name == "nt":
            cmd = ["ping", "-n", "1", "-w", str(int(_ICMP_TIMEOUT_SECONDS * 1000)), address]
        else:
            cmd = ["ping", "-c", "1", "-W", "1", address]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_ICMP_TIMEOUT_SECONDS + 2)
        except (asyncio.TimeoutError, OSError) as exc:
            return ProviderResult(provider=self.name, status=ConflictProviderStatus.ERROR,
                                  detail=f"Ping could not be executed: {exc}")
        text = stdout.decode(errors="replace")
        replied = (
            proc.returncode == 0
            and ("ttl=" in text.lower() or "time<" in text.lower() or "time=" in text.lower())
        )
        # Windows ping exits 0 even on "Destination host unreachable" — inspect text.
        unreachable = re.search(r"(unreachable|timed out|could not find host)", text, re.IGNORECASE)
        if replied and not unreachable:
            return ProviderResult(provider=self.name, status=ConflictProviderStatus.CONFLICT_DETECTED,
                                  detail=f"A device responded to ICMP echo at {address}.")
        return ProviderResult(provider=self.name, status=ConflictProviderStatus.NO_CONFLICT,
                              detail="No ICMP response received (may simply be filtered).")


class DnsForwardProvider(ConflictCheckProvider):
    name = "DNS"

    async def check(self, address: str, prefix: int) -> ProviderResult:
        def lookup() -> str | None:
            try:
                return socket.gethostbyaddr(address)[0]
            except (socket.herror, socket.gaierror, OSError):
                return None

        hostname = await asyncio.to_thread(lookup)
        if hostname:
            return ProviderResult(provider=self.name, status=ConflictProviderStatus.CONFLICT_DETECTED,
                                  detail=f"Reverse lookup resolves to '{hostname}'.")
        return ProviderResult(provider=self.name, status=ConflictProviderStatus.NO_CONFLICT,
                              detail="No PTR record found.")


class ReverseDnsProvider(DnsForwardProvider):
    """Alias kept explicit for reporting clarity (forward/PTR behaviour)."""

    name = "Reverse DNS"


class VMwareInventoryProvider(ConflictCheckProvider):
    """Checks addresses registered on running VMs in the vCenter inventory."""

    name = "VMware inventory"

    def __init__(self, vmware: VMwareService, target: VCenterTarget) -> None:
        self._vmware = vmware
        self._target = target

    async def check(self, address: str, prefix: int) -> ProviderResult:
        try:
            used = await self._vmware.get_used_ips(self._target)
        except Exception as exc:  # noqa: BLE001
            log.warning("VMware inventory IP check failed: %s", exc)
            return ProviderResult(provider=self.name, status=ConflictProviderStatus.ERROR,
                                  detail="Inventory could not be queried.")
        owner = used.get(address)
        if owner:
            return ProviderResult(provider=self.name, status=ConflictProviderStatus.CONFLICT_DETECTED,
                                  detail=f"Address is assigned to VM '{owner}'.")
        return ProviderResult(provider=self.name, status=ConflictProviderStatus.NO_CONFLICT,
                              detail="Not present in the vCenter guest inventory.")


async def run_conflict_check(
    address: str,
    prefix: int,
    providers: list[ConflictCheckProvider],
) -> IpConflictReport:
    results: list[ProviderResult] = []
    for provider in providers:
        try:
            results.append(await provider.check(address, prefix))
        except Exception as exc:  # noqa: BLE001 - a broken provider must never abort the job
            log.exception("Conflict provider '%s' crashed", provider.name)
            results.append(
                ProviderResult(provider=provider.name, status=ConflictProviderStatus.ERROR,
                               detail=f"Provider failed: {type(exc).__name__}")
            )

    conflict = any(r.status == ConflictProviderStatus.CONFLICT_DETECTED for r in results)
    definitive = sum(1 for r in results if r.status in (ConflictProviderStatus.NO_CONFLICT, ConflictProviderStatus.CONFLICT_DETECTED))
    available = sum(1 for r in results if r.status != ConflictProviderStatus.NOT_CONFIGURED)

    if conflict:
        note = "A potential conflict WAS detected — choose a different address."
    elif available == 0:
        note = "No validation source was available; the address is unverified."
    else:
        note = (
            f"No conflict detected with {available} available validation method(s) "
            f"({definitive} returned a definitive answer). Validation confidence depends "
            "on the configured sources; a filtered network can hide active devices."
        )

    return IpConflictReport(
        address=address,
        prefix=prefix,
        providers=results,
        conflict_detected=conflict,
        confidence_note=note,
    )


def default_providers(vmware: VMwareService | None = None,
                      target: VCenterTarget | None = None) -> list[ConflictCheckProvider]:
    providers: list[ConflictCheckProvider] = [
        IcmpPingProvider(),
        DnsForwardProvider(),
        ReverseDnsProvider(),
    ]
    if vmware is not None and target is not None:
        providers.append(VMwareInventoryProvider(vmware, target))
    return providers
