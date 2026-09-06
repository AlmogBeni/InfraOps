"""Application configuration.

Root runtime configuration is sourced from environment variables (12-factor).
Operational credentials are encrypted in PostgreSQL and never placed here.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import model_validator
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
    environment: Environment = Environment.PRODUCTION
    secret_key: str = ""
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
    infrastructure_mode: InfrastructureMode = InfrastructureMode.REAL
    vcenter_ca_file: str = ""

    # ── Authentication ───────────────────────────────────────────────────────
    auth_mode: AuthMode = AuthMode.LOCAL
    access_token_expire_minutes: int = 15
    refresh_token_expire_minutes: int = 720
    cookie_secure: bool = True
    rate_limit_login_per_minute: int = 10

    # ── Job worker ───────────────────────────────────────────────────────────
    worker_concurrency: int = 2
    worker_poll_interval_seconds: float = 2.0

    # ── HTTP ─────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed browser origins (kept as a raw string so
    # environment parsing never requires JSON).
    cors_origins: str = ""

    # ── One-time local administrator bootstrap ───────────────────────────────
    # These are read only when the database has no administrator. Remove the
    # password from the runtime environment after the first successful start.
    bootstrap_admin_username: str = ""
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        """Parsed CORS allow-list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @model_validator(mode="after")
    def _validate_production_safety(self) -> "Settings":
        if not self.is_production:
            return self

        problems: list[str] = []
        if self.infrastructure_mode != InfrastructureMode.REAL:
            problems.append("INFRASTRUCTURE_MODE must be 'real' in production")
        if len(self.secret_key) < 32:
            problems.append("SECRET_KEY must contain at least 32 characters in production")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        if self.auth_mode != AuthMode.LOCAL:
            problems.append(
                "only AUTH_MODE=local is implemented; LDAP/OIDC must not be selected"
            )
        origins = self.cors_origin_list
        if "*" in origins:
            problems.append("CORS_ORIGINS must not contain '*' in production")
        insecure_origins = [origin for origin in origins if not origin.startswith("https://")]
        if insecure_origins:
            problems.append("all configured CORS_ORIGINS must use https in production")
        if self.database_url == "postgresql+asyncpg://infraops:infraops@localhost:5432/infraops":
            problems.append("DATABASE_URL must not use the development database credentials")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached singleton settings instance."""
    return Settings()
