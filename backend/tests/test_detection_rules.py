"""Detection rule validation and safe command construction tests."""

from __future__ import annotations

import pytest

from app.models.applications import DetectionMethod
from app.services.applications.detection_rules import (
    detection_programs,
    ps_quote,
    validate_detection_config,
)


class TestValidation:
    def test_msi_product_code_normalised(self):
        cleaned = validate_detection_config(
            DetectionMethod.MSI_PRODUCT_CODE, {"product_code": "{A1B2C3D4-E5F6-4A7B-8C9D-0E1F2A3B4C5D}"}
        )
        assert cleaned["product_code"] == "A1B2C3D4-E5F6-4A7B-8C9D-0E1F2A3B4C5D"

    def test_msi_invalid_guid_rejected(self):
        with pytest.raises(ValueError):
            validate_detection_config(DetectionMethod.MSI_PRODUCT_CODE, {"product_code": "nope"})

    def test_registry_key_requires_hive(self):
        with pytest.raises(ValueError):
            validate_detection_config(DetectionMethod.REGISTRY_KEY, {"key_path": "SOFTWARE\\Foo"})

    def test_file_exists_rejects_traversal(self):
        with pytest.raises(ValueError):
            validate_detection_config(DetectionMethod.FILE_EXISTS, {"path": r"C:\Windows\..\..\evil.exe"})

    def test_service_name_pattern(self):
        with pytest.raises(ValueError):
            validate_detection_config(DetectionMethod.SERVICE_EXISTS, {"service_name": "bad name!"})

    def test_script_required(self):
        with pytest.raises(ValueError):
            validate_detection_config(DetectionMethod.SCRIPT, {})

    def test_unknown_keys_dropped(self):
        cleaned = validate_detection_config(
            DetectionMethod.SERVICE_EXISTS,
            {"service_name": "Agent", "injected": "value"},
        )
        assert "injected" not in cleaned


class TestProgramConstruction:
    def test_msi_uses_reg_query(self):
        config = validate_detection_config(
            DetectionMethod.MSI_PRODUCT_CODE, {"product_code": "{A1B2C3D4-E5F6-4A7B-8C9D-0E1F2A3B4C5D}"}
        )
        programs = detection_programs(DetectionMethod.MSI_PRODUCT_CODE, config)
        assert all(p[0].endswith("reg.exe") for p in programs)
        assert any("/reg:32" in p[1] for p in programs)

    def test_file_exists_uses_test_path(self):
        config = validate_detection_config(DetectionMethod.FILE_EXISTS, {"path": r"C:\Tools\a.dll"})
        program, args = detection_programs(DetectionMethod.FILE_EXISTS, config)[0]
        assert "Test-Path" in args
        assert program.endswith("powershell.exe")

    def test_ps_quote_escapes_single_quotes(self):
        assert ps_quote("it's") == "'it''s'"


class TestInstallerCommandSafety:
    def test_control_characters_rejected(self):
        from app.core.errors import InfraOperationError
        from app.services.applications.installer import ApplicationDefinition, build_install_program

        app = ApplicationDefinition(
            id=__import__("uuid").uuid4(),
            name="Evil",
            installer_type=__import__("app.models.applications", fromlist=["InstallerType"]).InstallerType.MSI,
            installer_path=r"\\share\evil.msi",
            install_arguments="/qn\r\nmalicious",
            detection_method="SERVICE_EXISTS",
            detection_config={"service_name": "X"},
            timeout_seconds=60,
            reboot_required=False,
        )
        with pytest.raises(InfraOperationError):
            build_install_program(app)
