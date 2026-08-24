"""Provisioning pipeline stage registry.

The ordered stage list *is* the state machine driving execution, progress
computation, resume-after-retry behaviour and timeout defaults. Future
automation modules define their own registries against the same machinery.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageDefinition:
    key: str
    name: str
    retryable: bool = True
    destructive: bool = False  # stages that create/change infrastructure objects


ORDERED_STAGES: tuple[StageDefinition, ...] = (
    StageDefinition("validate_request", "Validate request"),
    StageDefinition("connect_vcenter", "Connect to vCenter"),
    StageDefinition("validate_infrastructure", "Validate infrastructure configuration"),
    StageDefinition("clone_vm", "Clone VM from template", destructive=True),
    StageDefinition("configure_hardware", "Configure hardware"),
    StageDefinition("attach_network_adapter", "Attach network adapter"),
    StageDefinition("power_on", "Power on VM"),
    StageDefinition("wait_for_tools", "Wait for VMware Tools"),
    StageDefinition("configure_guest_network", "Configure guest network"),
    StageDefinition("validate_network", "Validate network connectivity"),
    StageDefinition("configure_hostname", "Configure hostname"),
    StageDefinition("join_domain", "Join domain"),
    StageDefinition("reboot_guest", "Reboot guest"),
    StageDefinition("wait_guest_ready", "Wait for guest availability"),
    StageDefinition("install_root_certificates", "Install trusted root certificates"),
    StageDefinition("install_intermediate_certificates", "Install intermediate certificates"),
    StageDefinition("validate_certificates", "Verify certificates"),
    StageDefinition("resolve_dependencies", "Resolve application dependencies"),
    StageDefinition("install_applications", "Install applications"),
    StageDefinition("validate_applications", "Validate application installations"),
    StageDefinition("final_validation", "Final validation"),
)

STAGES_BY_KEY: dict[str, StageDefinition] = {stage.key: stage for stage in ORDERED_STAGES}

# Default timeouts (seconds) — overridable through platform settings.
DEFAULT_STAGE_TIMEOUTS: dict[str, int] = {
    "validate_request": 60,
    "connect_vcenter": 120,
    "validate_infrastructure": 300,
    "clone_vm": 1800,
    "configure_hardware": 600,
    "attach_network_adapter": 600,
    "power_on": 600,
    "wait_for_tools": 900,
    "configure_guest_network": 300,
    "validate_network": 300,
    "configure_hostname": 300,
    "join_domain": 600,
    "reboot_guest": 600,
    "wait_guest_ready": 600,
    "install_root_certificates": 600,
    "install_intermediate_certificates": 600,
    "validate_certificates": 300,
    "resolve_dependencies": 120,
    "install_applications": 3600,
    "validate_applications": 600,
    "final_validation": 600,
}


def stage_definitions() -> list[tuple[str, str, bool]]:
    """(key, display name, retryable) tuples for step-row creation."""
    return [(s.key, s.name, s.retryable) for s in ORDERED_STAGES]


def stage_index(key: str) -> int:
    for index, stage in enumerate(ORDERED_STAGES):
        if stage.key == key:
            return index
    return -1


def is_destructive(key: str) -> bool:
    return STAGES_BY_KEY.get(key, StageDefinition(key, key)).destructive


def stage_timeout(key: str, timeouts_override: dict[str, int] | None = None) -> int:
    if timeouts_override and key in timeouts_override:
        return int(timeouts_override[key])
    return DEFAULT_STAGE_TIMEOUTS.get(key, 600)
