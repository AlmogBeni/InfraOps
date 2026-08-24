"""Certificate store logic tests (pure functions)."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.errors import DomainValidationError
from app.models.certificates import CertificateType
from app.services.certificates.deployer import CertificateToDeploy
from app.services.certificates.store_logic import (
    STORE_FOR_TYPE,
    build_presence_script,
    certificate_metadata,
    import_program,
    is_expired,
    make_guest_cert_file_name,
    parse_presence_output,
    presence_program,
)


def _dev_pem(cn: str) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
        .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


class TestMetadataExtraction:
    def test_fingerprint_and_subject_extracted(self):
        pem = _dev_pem("Test Corp Root CA")
        meta = certificate_metadata(pem)
        assert len(meta["fingerprint_sha256"]) == 64
        assert meta["subject_cn"] == "Test Corp Root CA"
        assert meta["not_after"] is not None

    def test_invalid_pem_raises_domain_error(self):
        with pytest.raises(DomainValidationError):
            certificate_metadata("this is not a certificate")


class TestStoreMapping:
    def test_root_maps_to_localmachine_root(self):
        assert STORE_FOR_TYPE[CertificateType.ROOT].value == "Root"

    def test_intermediate_maps_to_ca_store(self):
        assert STORE_FOR_TYPE[CertificateType.INTERMEDIATE].value == "CA"


class TestCommandConstruction:
    def test_presence_script_contains_thumbprint_and_store(self):
        script = build_presence_script("Root", "AB" * 32)
        assert "Cert:\\LocalMachine\\Root" in script
        assert ("AB" * 32).upper() in script

    def test_presence_script_rejects_injection(self):
        with pytest.raises(ValueError):
            build_presence_script("Root", "'; Remove-Item C:\\ -Recurse; '")

    def test_import_program_targets_managed_temp_only(self):
        program, args = import_program("Root", r"C:\Windows\Temp\abc.cer")
        assert program.endswith("certutil.exe")
        assert "-addstore -f Root" in args
        with pytest.raises(ValueError):
            import_program("Root", r"C:\Users\evil\cert.cer")

    def test_presence_program_shape(self):
        program, args = presence_program("CA", "CD" * 32)
        assert program.endswith("powershell.exe")
        assert "-NoProfile" in args


class TestPresenceParsing:
    def test_present_detected(self):
        assert parse_presence_output("PRESENT") is True
        assert parse_presence_output("some noise\nPRESENT\n") is True

    def test_absent_detected(self):
        assert parse_presence_output("ABSENT") is False
        assert parse_presence_output("") is False


class TestExpiry:
    def test_expired_flagged(self):
        yesterday = dt.date.today() - dt.timedelta(days=1)
        assert is_expired(yesterday) is True

    def test_future_not_expired(self):
        assert is_expired(dt.date.today() + dt.timedelta(days=30)) is False

    def test_unknown_treated_as_valid(self):
        assert is_expired(None) is False


class TestFileNaming:
    def test_safe_file_names(self):
        name = make_guest_cert_file_name("Corporate/Root CA: v1", "AB" * 32)
        assert "/" not in name and ":" not in name
        assert name.endswith(".cer")


class TestDeployRecordShape:
    def test_record_defaults(self):
        record = CertificateToDeploy(
            friendly_name="X", certificate_type=CertificateType.ROOT,
            thumbprint="AB" * 32, pem_body="-----BEGIN CERTIFICATE-----",
        )
        assert record.not_after is None
