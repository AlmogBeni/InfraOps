"""Address-conflict checks degrade honestly and validate their inputs."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.schemas.provisioning import ConflictProviderStatus, IpConflictCheckRequest
from app.services.network.conflict import IcmpPingProvider


@pytest.mark.asyncio
async def test_icmp_reports_not_configured_when_ping_is_unavailable() -> None:
    with patch("app.services.network.conflict.shutil.which", return_value=None):
        result = await IcmpPingProvider().check("192.0.2.10", 24)

    assert result.status == ConflictProviderStatus.NOT_CONFIGURED
    assert "not installed" in result.detail


def test_ip_conflict_request_normalizes_and_validates_address() -> None:
    request = IpConflictCheckRequest(address=" 192.168.77.52 ", prefix=23)
    assert request.address == "192.168.77.52"

    with pytest.raises(ValidationError, match="valid IPv4"):
        IpConflictCheckRequest(address="999.168.77.52", prefix=23)
