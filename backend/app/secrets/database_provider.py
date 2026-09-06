"""PostgreSQL-backed encrypted credential provider."""

from __future__ import annotations

from sqlalchemy import select

from app.db.session import session_factory
from app.models.platform import SecretReference
from app.secrets.base import SecretNotFoundError, SecretsProvider
from app.secrets.encryption import decrypt_secret


class DatabaseSecretsProvider(SecretsProvider):
    """Resolve a credential pair on every call so rotations apply live."""

    name = "database"

    @staticmethod
    def _split(secret_name: str) -> tuple[str, str]:
        base, separator, field = secret_name.rpartition("/")
        if not separator or field not in {"username", "password"} or not base:
            raise SecretNotFoundError(secret_name, DatabaseSecretsProvider.name)
        return base, field

    async def get_secret(self, secret_name: str) -> str:
        base, field = self._split(secret_name)
        async with session_factory() as db:
            result = await db.execute(
                select(SecretReference).where(
                    SecretReference.name == base,
                    SecretReference.provider == self.name,
                )
            )
            credential = result.scalar_one_or_none()
        encrypted = getattr(credential, f"encrypted_{field}", None) if credential else None
        if not encrypted:
            raise SecretNotFoundError(secret_name, self.name)
        return decrypt_secret(encrypted)

    async def healthcheck(self) -> bool:
        try:
            async with session_factory() as db:
                await db.execute(select(1))
            return True
        except Exception:  # noqa: BLE001
            return False
