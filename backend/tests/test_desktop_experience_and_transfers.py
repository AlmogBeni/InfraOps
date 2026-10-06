"""Desktop Experience editions, ESXi file-transfer diagnostics and the HTTPS preflight."""

from __future__ import annotations

import socket
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.core.errors import InfraOperationError
from app.schemas.provisioning import (
    DESKTOP_EXPERIENCE_EDITIONS,
    CheckStatus,
    IpMode,
    ProvisioningSubmissionRequest,
    parse_stored_request,
)
from app.services.vmware.base import PowerStateInfo, VCenterTarget
from app.workers.stages import stage_final_validation
from tests.conftest import make_request

TARGET = VCenterTarget(
    id="vc", name="vc", host="vc.example.test", port=443,
    username_secret_ref="u", password_secret_ref="p", verify_ssl=True,
)


# ── Editions ─────────────────────────────────────────────────────────────────


def test_editions_are_the_desktop_experience_images_of_microsoft_media() -> None:
    assert DESKTOP_EXPERIENCE_EDITIONS == {
        2: "Standard (Desktop Experience)",
        4: "Datacenter (Desktop Experience)",
    }
    assert make_request().guest.windows_image_index == 2


@pytest.mark.parametrize("index", [1, 3, 5])
def test_server_core_editions_are_rejected_for_new_requests(index: int) -> None:
    payload = make_request().model_dump(mode="json")
    payload["guest"]["windows_image_index"] = index

    with pytest.raises(ValidationError, match="Desktop Experience"):
        ProvisioningSubmissionRequest.model_validate(payload)


@pytest.mark.parametrize("index", [2, 4])
def test_desktop_experience_editions_are_accepted(index: int) -> None:
    payload = make_request().model_dump(mode="json")
    payload["guest"]["windows_image_index"] = index
    assert ProvisioningSubmissionRequest.model_validate(payload).guest.windows_image_index == index


def test_stored_jobs_keep_the_edition_they_were_submitted_with() -> None:
    payload = make_request().model_dump(mode="json")
    payload["guest"]["windows_image_index"] = 1
    assert parse_stored_request(payload).guest.windows_image_index == 1


def _final_context(index: int, installation_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        vmware=SimpleNamespace(
            get_vm_info=AsyncMock(
                return_value=PowerStateInfo(power_state="poweredOn", tools_status="toolsOk", ip_addresses=["10.0.0.9"])
            )
        ),
        target=object(),
        vm_name="SERVER-001",
        request=SimpleNamespace(
            effective_computer_name="SERVER-001",
            guest=SimpleNamespace(domain_join=None, windows_image_index=index),
            network=SimpleNamespace(network_id="net", mode=IpMode.DHCP, ipv4=None),
        ),
        resolve_guest_credentials=AsyncMock(return_value=SimpleNamespace(username="Administrator", password="x")),
        guest_ops=SimpleNamespace(
            run_powershell=AsyncMock(
                return_value=SimpleNamespace(succeeded=True, exit_code=0, stdout=f"{installation_type}\r\n", stderr="")
            )
        ),
        steps_by_key={},
    )


async def test_final_validation_confirms_the_desktop_experience() -> None:
    outcome = await stage_final_validation(_final_context(4, "Server"))

    windows = next(item for item in outcome.artifacts["checklist"] if item["group"] == "Windows")
    assert windows == {
        "group": "Windows", "label": "Datacenter (Desktop Experience) installed", "status": "PASS", "detail": "Server",
    }


async def test_server_core_installation_fails_final_validation_clearly() -> None:
    with pytest.raises(InfraOperationError, match="without the Desktop Experience") as caught:
        await stage_final_validation(_final_context(2, "Server Core"))

    assert caught.value.retryable is False
    assert "cannot be added to a Server Core installation" in caught.value.recommended_action


async def test_jobs_submitted_with_another_edition_are_not_rechecked() -> None:
    ctx = _final_context(1, "Server Core")

    await stage_final_validation(ctx)

    ctx.guest_ops.run_powershell.assert_not_awaited()


# ── File transfers to ESXi ───────────────────────────────────────────────────

URL = "https://esx01.corp.example:443/guestFile?id=1&token=abc"


def test_untrusted_esxi_certificate_is_named_with_the_fix() -> None:
    from app.services.guest.vmware_tools import _transfer_error

    cause = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    error = _transfer_error("upload-file", URL, httpx.ConnectError(str(cause)))

    assert error.human_message == "The TLS certificate of ESXi host 'esx01.corp.example' is not trusted."
    assert "VMCA" in error.recommended_action
    assert "token=abc" not in (error.technical_detail or "")


def test_unreachable_esxi_host_is_named_with_the_fix() -> None:
    from app.services.guest.vmware_tools import _transfer_error

    error = _transfer_error("upload-file", URL, httpx.ConnectError("[Errno 111] Connection refused"))

    assert error.human_message == "InfraOps could not connect to ESXi host 'esx01.corp.example' on port 443."
    assert "directly to the ESXi host" in error.reason
    assert "TCP 443" in error.recommended_action
    assert error.retryable is True


def test_refused_transfer_reports_the_http_status() -> None:
    from app.services.guest.vmware_tools import _transfer_error

    error = _transfer_error("download-file", URL, RuntimeError("HTTP 403"))

    assert error.human_message == "ESXi host 'esx01.corp.example' refused the VMware Tools file transfer."
    assert "HTTP 403" in error.reason


# ── HTTPS preflight to the ESXi hosts ────────────────────────────────────────


async def _host_checks(results: dict[str, str | None]) -> list[tuple]:
    from app.services.provisioning.preflight import PreflightValidator

    validator = PreflightValidator(
        db=None,
        vmware=SimpleNamespace(check_host_https=AsyncMock(return_value=results)),
        secrets=SimpleNamespace(),
    )
    checks: list[tuple] = []
    await validator._check_host_file_transfers(TARGET, list(results), lambda *args, **kwargs: checks.append(args))
    return checks


async def test_reachable_esxi_hosts_pass_preflight() -> None:
    (check,) = await _host_checks({"esx01": None, "esx02": None})
    assert check[0] == "esxi_https" and check[2] == CheckStatus.PASS


async def test_unreachable_esxi_host_blocks_submission() -> None:
    (check,) = await _host_checks({"esx01": None, "esx02": "no answer on TCP 443 within 5 seconds"})

    assert check[2] == CheckStatus.FAIL
    assert "esx02: no answer on TCP 443" in check[3]
    assert "esx01" not in check[3]


async def test_esxi_https_probe_classifies_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.vmware import vsphere

    class Writer:
        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def open_connection(host, port, *, ssl, server_hostname):
        assert port == 443 and server_hostname == host
        if host == "ok":
            return object(), Writer()
        raise {
            "untrusted": vsphere.ssl.SSLCertVerificationError(1, "certificate verify failed"),
            "unknown": socket.gaierror(-2, "Name or service not known"),
            "refused": ConnectionRefusedError(111, "Connection refused"),
        }[host]

    monkeypatch.setattr(vsphere.asyncio, "open_connection", open_connection)
    service = object.__new__(vsphere.VsphereVMwareService)

    results = await service.check_host_https(TARGET, ["ok", "untrusted", "unknown", "refused"])

    assert results["ok"] is None
    assert results["untrusted"].startswith("certificate not trusted")
    assert results["unknown"] == "the name does not resolve"
    assert results["refused"] == "connection failed (Connection refused)"
