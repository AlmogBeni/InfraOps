"""Application configuration.

All runtime configuration is sourced from environment variables (12-factor).
Secrets are *never* placed here — they are resolved through the secrets
provider abstraction (see ``app.secrets``).
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class InfrastructureMode(str, Enum):
    MOCK = "mock"
    REAL = "real"


class AuthMode(str, Enum):
    LOCAL = "local"
    LDAP = "ldap"  # reserved for future enterprise integration
    OIDC = "oidc"  # reserved for future enterprise integration


class SecretsProviderKind(str, Enum):
    ENV = "env"
    VAULT = "vault"


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "InfraOps"
    environment: Environment = Environment.DEVELOPMENT
    secret_key: str = "dev-only-secret-key-change-me-in-production"
    api_v1_prefix: str = "/api/v1"

    # ── Logging ──────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "json"  # json | console

    # ── Database / cache ─────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://infraops:infraops@localhost:5432/infraops"
    redis_url: str = "redis://localhost:6379/0"
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # ── Infrastructure integrations ──────────────────────────────────────────
    infrastructure_mode: InfrastructureMode = InfrastructureMode.MOCK

    # ── Authentication ───────────────────────────────────────────────────────
    auth_mode: AuthMode = AuthMode.LOCAL
    access_token_expire_minutes: int = 15
    refresh_token_expire_minutes: int = 720
    cookie_secure: bool = False
    rate_limit_login_per_minute: int = 10

    # ── Secrets management ───────────────────────────────────────────────────
    secrets_provider: SecretsProviderKind = SecretsProviderKind.ENV
    vault_addr: str = ""
    vault_token: str = ""
    vault_kv_mount: str = "secret"

    # ── Guest OS automation defaults ─────────────────────────────────────────
    guest_default_username: str = "administrator"

    # ── Job worker ───────────────────────────────────────────────────────────
    worker_concurrency: int = 2
    worker_poll_interval_seconds: float = 2.0

    # ── HTTP ─────────────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    # ── Development seed data ────────────────────────────────────────────────
    dev_admin_username: str = "admin"
    dev_admin_email: str = "admin@example.internal"
    dev_admin_password: str = "ChangeMe_DevOnly!123"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    """Return the cached singleton settings instance."""
    return Settings()
