"""Pure Windows certificate-store logic (fully unit-testable).

Every command fragment is constructed exclusively from server-generated or
strictly pattern-validated values (thumbprints are hex-only, store names come
from an enum, guest file paths are generated here) — no free-form input ever
reaches a command string.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import re

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from app.core.errors import DomainValidationError
from app.models.certificates import CertificateStore, CertificateType
from app.schemas.provisioning import THUMBPRINT_PATTERN

POWERSHELL_PATH = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
CERTUTIL_PATH = r"C:\Windows\System32\certutil.exe"
GUEST_TEMP_DIR = r"C:\Windows\Temp"

STORE_FOR_TYPE: dict[CertificateType, CertificateStore] = {
    CertificateType.ROOT: CertificateStore.ROOT,
    CertificateType.INTERMEDIATE: CertificateStore.CA,
}


def parse_certificate(pem_body: str) -> x509.Certificate:
    """Parse and structurally validate a PEM certificate body."""
    try:
        return x509.load_pem_x509_certificate(pem_body.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise DomainValidationError(
            "The provided certificate is not valid PEM-encoded X.509.",
            details={"reason": str(exc)},
        ) from exc


def certificate_metadata(pem_body: str) -> dict:
    """Extract fingerprint, subject CN and validity window from a PEM body."""
    cert = parse_certificate(pem_body)
    der = cert.public_bytes(serialization.Encoding.DER)
    fingerprint = binascii.hexlify(cert.fingerprint(hashes.SHA256())).decode("ascii").upper()
    cn_attributes = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    subject_cn = cn_attributes[0].value if cn_attributes else ""

    def _as_date(value: dt.datetime | None) -> dt.date | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(dt.timezone.utc).date()
        return value.date()

    not_before = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before
    not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
    return {
        "fingerprint_sha256": fingerprint,
        "subject_cn": str(subject_cn),
        "not_before": _as_date(not_before),
        "not_after": _as_date(not_after),
    }


def is_expired(not_after: dt.date | None, *, today: dt.date | None = None) -> bool:
    if not_after is None:
        return False
    return not_after < (today or dt.date.today())


def build_presence_script(store: str, thumbprint: str) -> str:
    """PowerShell probe that reports PRESENT/ABSENT for a thumbprint."""
    if not THUMBPRINT_PATTERN.fullmatch(thumbprint):
        raise ValueError("Thumbprint must be 40–64 hexadecimal characters.")
    if store not in (CertificateStore.ROOT.value, CertificateStore.CA.value):
        raise ValueError("Invalid certificate store.")
    return (
        "$match = Get-ChildItem Cert:\\LocalMachine\\"
        f"{store} -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.Thumbprint -eq '{thumbprint.upper()}' }}; "
        "if ($match) { 'PRESENT' } else { 'ABSENT' }"
    )


def presence_program(store: str, thumbprint: str) -> tuple[str, str]:
    script = build_presence_script(store, thumbprint)
    return POWERSHELL_PATH, f"-NoProfile -NonInteractive -Command {script}"


def import_program(store: str, guest_cert_path: str) -> tuple[str, str]:
    """certutil import into the LocalMachine computer-account store."""
    if store not in (CertificateStore.ROOT.value, CertificateStore.CA.value):
        raise ValueError("Invalid certificate store.")
    if not guest_cert_path.startswith(GUEST_TEMP_DIR + "\\"):
        raise ValueError("Certificate files must be staged inside the managed temp directory.")
    return CERTUTIL_PATH, f'-addstore -f {store} "{guest_cert_path}"'


def parse_presence_output(stdout: str) -> bool:
    return "PRESENT" in stdout.upper()


def make_guest_cert_file_name(friendly_name: str, thumbprint: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", friendly_name)[:40]
    return f"{safe}_{thumbprint[:12].lower()}.cer"
