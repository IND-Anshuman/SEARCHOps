"""
Feature flag providers.

Providers are read-only data sources for feature flag values.
The FeatureFlagManager composes multiple providers with priority ordering.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class FeatureFlagProvider(ABC):
    """Abstract base for all feature flag providers."""
    
    @abstractmethod
    async def get(self, flag_name: str) -> bool | None:
        """Return the flag value or None if not set by this provider."""
        ...
    
    @abstractmethod
    async def get_all(self) -> dict[str, bool]:
        """Return all flags known to this provider."""
        ...


class EnvFeatureFlagProvider(FeatureFlagProvider):
    """Read feature flags from environment variables.
    
    Convention: FEATURE_{FLAG_NAME_UPPER}=true|false|1|0
    Example: FEATURE_FIRECRAWL_ENABLED=true
    """
    
    _PREFIX = "FEATURE_"
    
    async def get(self, flag_name: str) -> bool | None:
        """Read a flag from the environment."""
        env_key = f"{self._PREFIX}{flag_name.upper().replace('-', '_')}"
        value = os.environ.get(env_key)
        if value is None:
            return None
        return value.lower() in ("true", "1", "yes", "on")
    
    async def get_all(self) -> dict[str, bool]:
        """Return all feature flags defined in the environment."""
        flags: dict[str, bool] = {}
        for key, value in os.environ.items():
            if key.startswith(self._PREFIX):
                flag_name = key[len(self._PREFIX):].lower()
                flags[flag_name] = value.lower() in ("true", "1", "yes", "on")
        return flags


class InMemoryFeatureFlagProvider(FeatureFlagProvider):
    """In-memory provider for testing."""
    
    def __init__(self, flags: dict[str, bool] | None = None) -> None:
        self._flags: dict[str, bool] = flags or {}
    
    def set(self, flag_name: str, value: bool) -> None:
        """Set a flag value."""
        self._flags[flag_name] = value
    
    async def get(self, flag_name: str) -> bool | None:
        return self._flags.get(flag_name)
    
    async def get_all(self) -> dict[str, bool]:
        return dict(self._flags)
