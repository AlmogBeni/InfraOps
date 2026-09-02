"""Secrets service facade used by all infrastructure integrations."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import SecretsProviderKind, get_settings
from app.secrets.base import SecretsProvider
from app.secrets.env_provider import EnvSecretsProvider
from app.secrets.vault_provider import VaultSecretsProvider


def build_provider(kind: SecretsProviderKind | None = None) -> SecretsProvider:
    selected = kind or get_settings().secrets_provider
    if selected == SecretsProviderKind.VAULT:
        return VaultSecretsProvider()
    return EnvSecretsProvider()


class SecretsService:
    """Request credentials through this service — never store or log them."""

    def __init__(self, provider: SecretsProvider | None = None) -> None:
        self.provider = provider or build_provider()

    async def get_secret(self, secret_name: str) -> str:
        return await self.provider.get_secret(secret_name)

    async def get_credentials(self, username_ref: str, password_ref: str) -> tuple[str, str]:
        username = await self.provider.get_secret(username_ref)
        password = await self.provider.get_secret(password_ref)
        return username, password

    async def healthcheck(self) -> bool:
        return await self.provider.healthcheck()

    @property
    def provider_name(self) -> str:
        return self.provider.name


@lru_cache
def get_secrets_service() -> SecretsService:
    return SecretsService()
