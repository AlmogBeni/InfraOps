"""Environment-variable-backed secrets provider (development).

Convention: a secret named ``vcsa-prod/password`` resolves from the
environment variable ``SECRETS_VCSA_PROD_PASSWORD`` — dots, dashes and
slashes are normalised to underscores and the result is upper-cased.
"""

from __future__ import annotations

import os
import re

from app.core.logging import get_logger
from app.secrets.base import SecretNotFoundError, SecretsProvider

log = get_logger(__name__)

_NORMALISER = re.compile(r"[^A-Z0-9]")


class EnvSecretsProvider(SecretsProvider):
    name = "env"

    @staticmethod
    def _env_key(secret_name: str) -> str:
        return "SECRETS_" + _NORMALISER.sub("_", secret_name.upper())

    async def get_secret(self, secret_name: str) -> str:
        key = self._env_key(secret_name)
        value = os.environ.get(key)
        if value is None or value == "":
            raise SecretNotFoundError(secret_name, self.name)
        return value

    async def healthcheck(self) -> bool:
        return True
