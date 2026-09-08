"""Pipeline state machine integrity tests."""

from __future__ import annotations

from app.workers.state_machine import (
    DEFAULT_STAGE_TIMEOUTS,
    ORDERED_STAGES,
    STAGES_BY_KEY,
    is_destructive,
    stage_definitions,
    stage_index,
    stage_timeout,
)
from app.workers.stages import STAGE_HANDLERS


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

    def test_clone_is_the_only_destructive_stage(self):
        destructive = [s.key for s in ORDERED_STAGES if s.destructive]
        assert destructive == ["clone_vm"]

    def test_ordering_matches_documented_workflow(self):
        expected_prefix = [
            "validate_request", "connect_vcenter", "validate_infrastructure",
            "clone_vm", "configure_hardware", "attach_network_adapter",
            "prepare_unattended_install", "power_on", "wait_for_guest_os", "wait_for_tools",
            "cleanup_unattended_media", "configure_guest_network", "validate_network",
        ]
        actual = [s.key for s in ORDERED_STAGES][: len(expected_prefix)]
        assert actual == expected_prefix

    def test_final_validation_is_last(self):
        assert ORDERED_STAGES[-1].key == "final_validation"


class TestLookups:
    def test_stage_index(self):
        assert stage_index("clone_vm") > stage_index("connect_vcenter")
        assert stage_index("does-not-exist") == -1

    def test_is_destructive_defaults_false(self):
        assert is_destructive("power_on") is False

    def test_stage_definitions_shape(self):
        definitions = stage_definitions()
        assert all(len(entry) == 3 for entry in definitions)


class TestTimeouts:
    def test_clone_timeout_is_thirty_minutes(self):
        assert DEFAULT_STAGE_TIMEOUTS["clone_vm"] == 1800

    def test_tools_timeout_fifteen_minutes(self):
        assert DEFAULT_STAGE_TIMEOUTS["wait_for_tools"] == 900

    def test_override_wins(self):
        assert stage_timeout("clone_vm", {"clone_vm": 60}) == 60

    def test_unknown_stage_default(self):
        assert stage_timeout("mystery") == 600

    def test_all_stages_have_timeouts(self):
        for key in STAGES_BY_KEY:
            assert key in DEFAULT_STAGE_TIMEOUTS
