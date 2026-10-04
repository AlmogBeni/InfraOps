"""Pipeline state machine integrity tests."""

from __future__ import annotations

from app.models.jobs import (
    ACTIVE_JOB_STATUSES,
    IP_RESERVING_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    JobStatus,
    StepStatus,
)
from app.models.notifications import NotificationKind
from app.workers.stages import _TIMEOUT_KEY_MAP, STAGE_HANDLERS
from app.workers.state_machine import (
    CONSOLE_STAGES,
    DEFAULT_STAGE_TIMEOUTS,
    ORDERED_STAGES,
    STAGES_BY_KEY,
    is_destructive,
    stage_definitions,
    stage_index,
    stage_timeout,
)


class TestStageRegistry:
    def test_every_stage_has_a_handler(self):
        missing = [s.key for s in ORDERED_STAGES if s.key not in STAGE_HANDLERS]
        assert missing == []

    def test_no_orphan_handlers(self):
        extra = set(STAGE_HANDLERS) - {s.key for s in ORDERED_STAGES}
        assert extra == set()

    def test_keys_are_unique(self):
        keys = [s.key for s in ORDERED_STAGES]
        assert len(keys) == len(set(keys))

    def test_only_creating_the_vm_and_adding_disks_change_infrastructure(self):
        destructive = [s.key for s in ORDERED_STAGES if s.destructive]
        assert destructive == ["create_vm", "add_data_disks"]

    def test_every_vm_is_installed_from_an_iso(self):
        assert [s.key for s in ORDERED_STAGES] == [
            "validate_request", "connect_vcenter", "validate_infrastructure",
            "create_vm", "configure_hardware", "attach_network_adapter",
            "prepare_unattended_install", "power_on", "wait_for_guest_os", "wait_for_tools",
            "cleanup_unattended_media", "add_data_disks", "initialize_data_disks",
            "configure_guest_network", "validate_network", "configure_hostname", "join_domain",
            "reboot_guest", "wait_guest_ready", "install_root_certificates",
            "install_intermediate_certificates", "validate_certificates", "resolve_dependencies",
            "install_applications", "validate_applications", "final_validation",
        ]

    def test_data_disks_are_added_only_after_windows_is_installed(self):
        assert stage_index("add_data_disks") > stage_index("cleanup_unattended_media")
        assert stage_index("initialize_data_disks") == stage_index("add_data_disks") + 1
        assert stage_index("initialize_data_disks") < stage_index("configure_guest_network")

    def test_screenshots_are_taken_for_stages_that_own_the_console(self):
        assert CONSOLE_STAGES == {"power_on", "wait_for_guest_os", "wait_for_tools"}
        assert CONSOLE_STAGES <= set(STAGES_BY_KEY)

    def test_final_validation_is_last(self):
        assert ORDERED_STAGES[-1].key == "final_validation"


class TestNoHumanGate:
    def test_no_job_or_step_status_waits_for_a_person(self):
        assert {status.value for status in JobStatus} == {
            "QUEUED", "RUNNING", "COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED",
            "INTERRUPTED",
        }
        assert {status.value for status in StepStatus} == {
            "PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED", "WARNING", "NOT_APPLICABLE",
            "CANCELLED",
        }
        assert {kind.value for kind in NotificationKind} == {"JOB_COMPLETED", "JOB_FAILED"}

    def test_every_job_either_runs_or_has_finished(self):
        assert set(ACTIVE_JOB_STATUSES) | TERMINAL_JOB_STATUSES == set(JobStatus)
        assert not set(ACTIVE_JOB_STATUSES) & TERMINAL_JOB_STATUSES
        assert IP_RESERVING_JOB_STATUSES == ACTIVE_JOB_STATUSES


class TestLookups:
    def test_stage_index(self):
        assert stage_index("create_vm") > stage_index("connect_vcenter")
        assert stage_index("does-not-exist") == -1

    def test_is_destructive_defaults_false(self):
        assert is_destructive("power_on") is False

    def test_stage_definitions_shape(self):
        definitions = stage_definitions()
        assert all(len(entry) == 3 for entry in definitions)


class TestTimeouts:
    def test_create_timeout_is_thirty_minutes(self):
        assert DEFAULT_STAGE_TIMEOUTS["create_vm"] == 1800

    def test_windows_installation_gets_two_hours(self):
        assert DEFAULT_STAGE_TIMEOUTS["wait_for_guest_os"] == 7200

    def test_tools_timeout_fifteen_minutes(self):
        assert DEFAULT_STAGE_TIMEOUTS["wait_for_tools"] == 900

    def test_override_wins(self):
        assert stage_timeout("create_vm", {"create_vm": 60}) == 60

    def test_unknown_stage_default(self):
        assert stage_timeout("mystery") == 600

    def test_all_stages_have_timeouts(self):
        for key in STAGES_BY_KEY:
            assert key in DEFAULT_STAGE_TIMEOUTS

    def test_settings_overrides_map_to_known_stages_and_settings(self):
        from app.schemas.admin import DefaultTimeouts

        assert set(_TIMEOUT_KEY_MAP) <= set(STAGES_BY_KEY)
        settings = {key for keys in _TIMEOUT_KEY_MAP.values() for key in keys}
        assert settings <= set(DefaultTimeouts.model_fields)
