"""Idempotent certificate deployment into Windows guests.

For every certificate:

1. Skip when explicitly disabled or already present (thumbprint match).
2. Refuse to deploy certificates that are already expired.
3. Stage the public PEM inside the guest's managed temp directory.
4. Import into ``Cert:\\LocalMachine\\Root`` or ``...\\CA`` (computer account).
5. Re-probe the store and verify the exact thumbprint exists.

Private keys never pass through this layer — only public certificates.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from dataclasses import dataclass

from app.core.errors import InfraOperationError
from app.core.logging import get_logger
from app.models.certificates import CertificateType
from app.services.certificates.store_logic import (
    GUEST_TEMP_DIR,
    STORE_FOR_TYPE,
    import_program,
    is_expired,
    make_guest_cert_file_name,
    parse_presence_output,
    presence_program,
)
from app.services.guest.base import GuestCredentials, GuestOperations
from app.services.vmware.base import VCenterTarget

log = get_logger(__name__)

_PRESENCE_TIMEOUT_SECONDS = 90
_IMPORT_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class CertificateToDeploy:
    friendly_name: str
    certificate_type: CertificateType
    thumbprint: str
    pem_body: str
    not_after: dt.date | None = None


@dataclass
class CertificateInstallRecord:
    friendly_name: str
    store: str
    thumbprint: str
    action: str  # INSTALLED | ALREADY_PRESENT | FAILED
    verified: bool
    detail: str = ""


class CertificateDeployer:
    name = "certificate-deployer"

    def __init__(self, guest_ops: GuestOperations) -> None:
        self._guest = guest_ops

    async def deploy(
        self,
        target: VCenterTarget,
        vm_name: str,
        credentials: GuestCredentials,
        certificates: list[CertificateToDeploy],
    ) -> list[CertificateInstallRecord]:
        records: list[CertificateInstallRecord] = []
        for cert in certificates:
            store = STORE_FOR_TYPE[cert.certificate_type].value
            record = await self._deploy_one(target, vm_name, credentials, cert, store)
            records.append(record)
        return records

    async def verify_all(
        self,
        target: VCenterTarget,
        vm_name: str,
        credentials: GuestCredentials,
        certificates: list[CertificateToDeploy],
    ) -> list[CertificateInstallRecord]:
        """Post-install verification pass used by the validation stage."""
        results: list[CertificateInstallRecord] = []
        for cert in certificates:
            store = STORE_FOR_TYPE[cert.certificate_type].value
            present = await self._probe_presence(target, vm_name, credentials, store, cert.thumbprint)
            results.append(
                CertificateInstallRecord(
                    friendly_name=cert.friendly_name,
                    store=store,
                    thumbprint=cert.thumbprint,
                    action="ALREADY_PRESENT" if present else "FAILED",
                    verified=present,
                    detail="" if present else "Thumbprint not found after installation.",
                )
            )
        return results

    # ── internals ────────────────────────────────────────────────────────────

    async def _deploy_one(
        self,
        target: VCenterTarget,
        vm_name: str,
        credentials: GuestCredentials,
        cert: CertificateToDeploy,
        store: str,
    ) -> CertificateInstallRecord:
        log.info("Certificate deploy: '%s' -> LocalMachine\\%s on %s",
                 cert.friendly_name, store, vm_name)
        try:
            if is_expired(cert.not_after):
                return CertificateInstallRecord(
                    friendly_name=cert.friendly_name, store=store, thumbprint=cert.thumbprint,
                    action="FAILED", verified=False,
                    detail=f"The certificate expired on {cert.not_after}.",
                )

            already = await self._probe_presence(target, vm_name, credentials, store, cert.thumbprint)
            if already:
                return CertificateInstallRecord(
                    friendly_name=cert.friendly_name, store=store, thumbprint=cert.thumbprint,
                    action="ALREADY_PRESENT", verified=True,
                    detail="Thumbprint already present — skipped reinstall.",
                )

            guest_path = rf"{GUEST_TEMP_DIR}\{make_guest_cert_file_name(cert.friendly_name, cert.thumbprint)}"
            await self._guest.upload_file(
                target, vm_name, credentials, cert.pem_body.encode("utf-8"), guest_path
            )
            try:
                program, arguments = import_program(store, guest_path)
                result = await self._guest.run_program(
                    target, vm_name, credentials, program, arguments, _IMPORT_TIMEOUT_SECONDS
                )
                if not result.succeeded:
                    raise InfraOperationError(
                        f"Import of '{cert.friendly_name}' failed inside the guest.",
                        reason=f"certutil exited with code {result.exit_code}.",
                        recommended_action="Review the stage output and retry certificate installation.",
                        technical_detail=result.stdout[-2000:] or result.stderr[-2000:],
                        retryable=True,
                    )
            finally:
                try:
                    await self._guest.delete_file(target, vm_name, credentials, guest_path)
                except InfraOperationError:
                    pass

            verified = await self._probe_presence(target, vm_name, credentials, store, cert.thumbprint)
            return CertificateInstallRecord(
                friendly_name=cert.friendly_name, store=store, thumbprint=cert.thumbprint,
                action="INSTALLED", verified=verified,
                detail="" if verified else "Import reported success but verification probe failed.",
            )
        except InfraOperationError as exc:
            return CertificateInstallRecord(
                friendly_name=cert.friendly_name, store=store, thumbprint=cert.thumbprint,
                action="FAILED", verified=False, detail=exc.human_message,
            )

    async def _probe_presence(
        self,
        target: VCenterTarget,
        vm_name: str,
        credentials: GuestCredentials,
        store: str,
        thumbprint: str,
    ) -> bool:
        program, arguments = presence_program(store, thumbprint)
        result = await self._guest.run_program(
            target, vm_name, credentials, program, arguments, _PRESENCE_TIMEOUT_SECONDS
        )
        if not result.succeeded:
            raise InfraOperationError(
                "The certificate presence probe could not run inside the guest.",
                reason=f"PowerShell exited with code {result.exit_code}.",
                recommended_action="Verify VMware Tools health and retry.",
                technical_detail=result.stdout[-1000:] + result.stderr[-1000:],
                retryable=True,
            )
        return parse_presence_output(result.stdout)
