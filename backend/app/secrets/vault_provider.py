"""HashiCorp Vault KV-v2 secrets provider (production adapter).

NOTE — environment dependent: this adapter is fully implemented against the
Vault HTTP API but requires a reachable Vault instance configured through
``VAULT_ADDR`` / ``VAULT_TOKEN``. Convention: each KV-v2 secret must expose
the credential under a ``value`` key (additional keys are ignored).
"""

from __future__ import annotations

import json

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.secrets.base import SecretNotFoundError, SecretsProvider, SecretsProviderError

log = get_logger(__name__)

_REQUEST_TIMEOUT_SECONDS = 10.0


class VaultSecretsProvider(SecretsProvider):
    name = "vault"

    def _require_config(self) -> tuple[str, str, str]:
        settings = get_settings()
        if not settings.vault_addr or not settings.vault_token:
            raise SecretsProviderError(
                "Vault secrets provider selected but VAULT_ADDR/VAULT_TOKEN are not configured."
            )
        return settings.vault_addr.rstrip("/"), settings.vault_token, settings.vault_kv_mount

    async def get_secret(self, secret_name: str) -> str:
        addr, token, mount = self._require_config()
        url = f"{addr}/v1/{mount}/data/{secret_name}"
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(url, headers={"X-Vault-Token": token})
        except httpx.HTTPError as exc:
            log.error("Vault request failed for secret '%s': %s", secret_name, exc)
            raise SecretsProviderError(f"Vault is unreachable while resolving '{secret_name}'.") from exc

        if response.status_code == 404:
            raise SecretNotFoundError(secret_name, self.name)
        if response.status_code != 200:
            raise SecretsProviderError(
                f"Vault returned HTTP {response.status_code} while resolving '{secret_name}'."
            )

        payload = response.json().get("data", {}).get("data", {})
        value = payload.get("value")
        if value is None:
            # Fall back to the whole secret body serialised (never logged).
            value = json.dumps(payload)
        return str(value)

    async def healthcheck(self) -> bool:
        try:
            addr, _, _ = self._require_config()
        except SecretsProviderError:
            return False
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(f"{addr}/v1/sys/health?standbyok=true")
        except httpx.HTTPError:
            return False
        return response.status_code < 500
