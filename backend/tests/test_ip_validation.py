"""Tests for pure IPv4 validation helpers."""

from __future__ import annotations

import pytest

from app.services.network.validation import (
    describe_subnet,
    mask_to_prefix,
    prefix_to_mask,
    validate_static_ipv4,
)


class TestMaskConversion:
    @pytest.mark.parametrize(
        ("mask", "prefix"),
        [
            ("255.255.255.0", 24),
            ("255.255.0.0", 16),
            ("255.0.0.0", 8),
            ("255.255.255.128", 25),
        ],
    )
    def test_valid_masks(self, mask, prefix):
        assert mask_to_prefix(mask) == prefix

    def test_non_contiguous_mask_rejected(self):
        assert mask_to_prefix("255.0.255.0") is None

    def test_garbage_mask(self):
        assert mask_to_prefix("not-a-mask") is None

    def test_prefix_to_mask_roundtrip(self):
        assert prefix_to_mask(24) == "255.255.255.0"


class TestStaticValidation:
    def test_valid_configuration_passes(self):
        issues = validate_static_ipv4("10.20.30.45", 24, "10.20.30.1",
                                      ["10.20.1.10", "10.20.1.11"])
        assert issues == []

    def test_invalid_address_detected(self):
        issues = validate_static_ipv4("999.1.1.1", 24, "10.20.30.1", [])
        assert any("address" in i.field for i in issues)

    def test_gateway_outside_subnet(self):
        issues = validate_static_ipv4("10.20.30.45", 24, "10.20.99.1", [])
        assert any(i.field == "gateway" for i in issues)

    def test_network_address_rejected(self):
        issues = validate_static_ipv4("10.20.30.0", 24, "10.20.30.1", [])
        assert any("network address" in i.message for i in issues)

    def test_broadcast_address_rejected(self):
        issues = validate_static_ipv4("10.20.30.255", 24, "10.20.30.1", [])
        assert any("broadcast" in i.message for i in issues)

    def test_gateway_equals_address_rejected(self):
        issues = validate_static_ipv4("10.20.30.45", 24, "10.20.30.45", [])
        assert any("differ" in i.message for i in issues)

    def test_duplicate_dns_flagged(self):
        issues = validate_static_ipv4("10.20.30.45", 24, "10.20.30.1",
                                      ["10.20.1.10", "10.20.1.10"])
        assert any("Duplicate DNS" in i.message for i in issues)

    def test_prefix_out_of_range(self):
        issues = validate_static_ipv4("10.20.30.45", 33, "10.20.30.1", [])
        assert any(i.field == "prefix" for i in issues)

    def test_describe_subnet(self):
        assert describe_subnet("10.20.30.45", 24) == "10.20.30.0/24"
