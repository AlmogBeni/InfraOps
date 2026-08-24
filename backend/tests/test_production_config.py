"""Production configuration must fail closed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Environment, InfrastructureMode, Settings


def production_settings(**overrides) -> Settings:
    values = {
        "environment": Environment.PRODUCTION,
        "secret_key": "a-unique-production-signing-secret-of-32-plus-characters",
        "database_url": "postgresql+asyncpg://infraops:encoded-secret@postgres:5432/infraops",
        "cookie_secure": True,
        "infrastructure_mode": InfrastructureMode.REAL,
        "cors_origins": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_safe_production_configuration_is_accepted() -> None:
    assert production_settings().is_production is True


@pytest.mark.parametrize(
    "override",
    [
        {"secret_key": "short"},
        {"cookie_secure": False},
        {"infrastructure_mode": InfrastructureMode.MOCK},
        {"cors_origins": "*"},
        {"cors_origins": "http://infraops.internal"},
    ],
)
def test_unsafe_production_configuration_is_rejected(override) -> None:
    with pytest.raises(ValidationError, match="Unsafe production configuration"):
        production_settings(**override)
