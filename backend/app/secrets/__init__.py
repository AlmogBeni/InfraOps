"""Secrets management abstraction.

Infrastructure integrations never receive raw credentials from configuration
or the database — they resolve named secrets through a ``SecretsProvider``.
Supported providers:

* ``env``   — development provider backed by ``SECRETS_*`` environment variables
* ``vault`` — HashiCorp Vault KV v2 (production; requires a reachable Vault)
"""

from app.secrets.base import SecretNotFoundError, SecretsProvider
from app.secrets.service import SecretsService, get_secrets_service

__all__ = ["SecretNotFoundError", "SecretsProvider", "SecretsService", "get_secrets_service"]
