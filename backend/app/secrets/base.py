"""Secrets provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class SecretNotFoundError(KeyError):
    """Raised when a named secret cannot be resolved by the provider."""

    def __init__(self, secret_name: str, provider_name: str) -> None:
        super().__init__(secret_name)
        self.secret_name = secret_name
        self.provider_name = provider_name

    def __str__(self) -> str:  # pragma: no cover
        return f"Secret '{self.secret_name}' was not found in the '{self.provider_name}' secrets provider."


class SecretsProviderError(RuntimeError):
    """Raised when the provider itself is misconfigured or unreachable."""


class SecretsProvider(ABC):
    """Abstract secrets source.

    Implementations must never log secret values and must resolve secrets
    lazily at call time (supporting rotation).
    """

    name: str = "abstract"

    @abstractmethod
    async def get_secret(self, secret_name: str) -> str:
        """Return the secret value for ``secret_name``."""

    @abstractmethod
    async def healthcheck(self) -> bool:
        """Return True when the provider backend is reachable/usable."""
