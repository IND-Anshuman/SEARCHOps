"""
Secret provider abstraction.

In production, swap EnvSecretProvider for VaultSecretProvider or
KubernetesSecretProvider without touching any consuming code.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import final

import structlog

log = structlog.get_logger(__name__)


class SecretProvider(ABC):
    """Abstract base for all secret providers."""
    
    @abstractmethod
    async def get(self, key: str) -> str | None:
        """Retrieve a secret by key. Returns None if not found."""
        ...
    
    @abstractmethod
    async def get_required(self, key: str) -> str:
        """Retrieve a required secret. Raises ValueError if not found."""
        ...
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is reachable."""
        ...


@final
class EnvSecretProvider(SecretProvider):
    """Reads secrets from environment variables. Suitable for development and testing."""
    
    async def get(self, key: str) -> str | None:
        """Read a secret from environment variables."""
        value = os.environ.get(key)
        if value is None:
            log.debug("Secret not found in environment", key=key)
        return value
    
    async def get_required(self, key: str) -> str:
        """Read a required secret from environment variables."""
        value = os.environ.get(key)
        if value is None:
            raise ValueError(f"Required secret '{key}' not found in environment")
        return value
    
    async def health_check(self) -> bool:
        """Always healthy for env provider."""
        return True


@final
class InMemorySecretProvider(SecretProvider):
    """In-memory provider for testing. Never use in production."""
    
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets: dict[str, str] = secrets or {}
    
    def set(self, key: str, value: str) -> None:
        """Set a secret. Only available in testing."""
        self._secrets[key] = value
    
    async def get(self, key: str) -> str | None:
        return self._secrets.get(key)
    
    async def get_required(self, key: str) -> str:
        value = self._secrets.get(key)
        if value is None:
            raise ValueError(f"Required secret '{key}' not found")
        return value
    
    async def health_check(self) -> bool:
        return True
