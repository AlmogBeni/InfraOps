"""Application configuration.

Root runtime configuration is sourced from environment variables (12-factor).
Operational credentials are encrypted in PostgreSQL and never placed here.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, model_validator
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
    # Root of the credential-encryption key (defaults to SECRET_KEY for
    # backward compatibility). Former roots stay decryptable while rotating.
    credential_encryption_key: str = ""
    credential_encryption_previous_keys: str = ""
    api_v1_prefix: str = "/api/v1"

    # ── Logging ──────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "json"  # json | console

    # ── Database / cache ─────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://infraops:infraops@localhost:5432/infraops"
    # Schema-owner connection used only by migrations and app.db.roles. When
    # set, DATABASE_URL should be a separate least-privilege runtime role.
    migration_database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # ── Infrastructure integrations ──────────────────────────────────────────
    infrastructure_mode: InfrastructureMode = InfrastructureMode.REAL
    vcenter_ca_file: str = ""
    # Explicit opt-in for vCenters whose certificate cannot be verified (e.g. the
    # default self-signed certificate). Traffic stays encrypted, but the server
    # identity is not checked, so credentials could reach an impostor.
    allow_insecure_vcenter_tls: bool = False

    # ── Authentication ───────────────────────────────────────────────────────
    auth_mode: AuthMode = AuthMode.LOCAL
    access_token_expire_minutes: int = 15
    refresh_token_expire_minutes: int = 720
    cookie_secure: bool = True
    # Client-side inactivity policy: after the timeout the UI asks the user to
    # stay signed in and signs them out when the warning period elapses.
    session_idle_timeout_seconds: int = Field(default=300, ge=60, le=86400)
    session_idle_warning_seconds: int = Field(default=60, ge=10, le=600)
    rate_limit_login_per_minute: int = 10

    # ── Job worker ───────────────────────────────────────────────────────────
    worker_concurrency: int = 2
    worker_poll_interval_seconds: float = 2.0
    # A RUNNING job whose heartbeat is older than the timeout is considered
    # abandoned (worker crash / SIGKILL) and is marked INTERRUPTED.
    worker_heartbeat_interval_seconds: float = 15.0
    worker_heartbeat_timeout_seconds: float = 120.0
    worker_reaper_interval_seconds: float = 30.0
    # After SIGTERM the worker stops claiming work and waits this long for
    # running jobs before interrupting them (keep below compose stop_grace_period).
    worker_shutdown_grace_seconds: float = 60.0
    # Prometheus metrics + liveness for the worker process (0 disables).
    worker_metrics_port: int = 9102

    # ── HTTP ─────────────────────────────────────────────────────────────────
    # Direct peers allowed to supply X-Forwarded-For (comma-separated CIDRs).
    # Only the bundled nginx should be in this range; see core/client_ip.py.
    trusted_proxy_cidrs: str = "127.0.0.1/32,::1/128"
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
    def _validate_production_safety(self) -> Settings:
        if not self.is_production:
            return self

        problems: list[str] = []
        if self.infrastructure_mode != InfrastructureMode.REAL:
            problems.append("INFRASTRUCTURE_MODE must be 'real' in production")
        if len(self.secret_key) < 32:
            problems.append("SECRET_KEY must contain at least 32 characters in production")
        if self.credential_encryption_key and len(self.credential_encryption_key) < 32:
            problems.append("CREDENTIAL_ENCRYPTION_KEY must contain at least 32 characters")
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
