"""
Feature Flag Manager.

Composes multiple providers with priority ordering and provides
a clean interface for checking flags anywhere in the codebase.

Usage:
    from searchops.feature_flags import FeatureFlagManager
    
    flags = FeatureFlagManager(providers=[env_provider])
    
    if await flags.is_enabled("firecrawl"):
        # use firecrawl
        ...
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import final

import structlog

from searchops.feature_flags.providers import FeatureFlagProvider

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FeatureFlagSnapshot:
    """
    Immutable point-in-time snapshot of all feature flag values.

    Created once per pipeline invocation via FeatureFlagManager.snapshot().
    Allows multiple flag checks in the same request with zero additional I/O.

    Usage:
        flags = await feature_flags.snapshot()
        if flags.get("firecrawl_enabled"):
            ...
        if flags.get("brightdata_unlocker_enabled"):
            ...
    """

    _values: dict[str, bool]

    def get(self, flag_name: str, default: bool = False) -> bool:
        """
        Return the value of a feature flag from the snapshot.

        Args:
            flag_name: The feature flag name.
            default: Value to return if flag is not in snapshot.

        Returns:
            bool flag value.
        """
        return self._values.get(flag_name, default)

    def __contains__(self, flag_name: str) -> bool:
        return flag_name in self._values

    @classmethod
    def empty(cls) -> "FeatureFlagSnapshot":
        """Return a snapshot with all flags disabled (safe default)."""
        return cls(_values={})


# Platform-wide default flag values.
# If a flag is not set by any provider, these defaults apply.
_DEFAULT_FLAGS: dict[str, bool] = {
    "firecrawl_enabled": True,
    "playwright_enabled": True,
    "github_agent_enabled": True,
    "academic_agent_enabled": True,
    "news_agent_enabled": True,
    "reddit_agent_enabled": True,
    "youtube_agent_enabled": True,
    "patent_agent_enabled": True,
    "verification_enabled": True,
    "contradiction_detection_enabled": True,
    "knowledge_graph_enabled": True,
    "vector_search_enabled": True,
    "langfuse_enabled": True,
    "prometheus_enabled": True,
    "rate_limiting_enabled": True,
    "prompt_injection_detection_enabled": True,
    "output_validation_enabled": True,
    "human_review_enabled": False,  # Disabled by default
    "cost_limit_enforcement_enabled": True,
    # ── Bright Data Premium Tier ─────────────────────────────────────────────────────────────
    # All BD flags default to False — opt-in when credentials are configured
    "brightdata_unlocker_enabled": False,   # Tier 1.5: Web Unlocker proxy scraper
    "brightdata_browser_enabled": False,    # Tier 2.5: Cloud Scraping Browser CDP
    "brightdata_serp_enabled": False,       # BD SERP provider in search routing
    "brightdata_datasets_enabled": False,   # BD Dataset API (GitHub/LinkedIn/Reddit)
}


@final
class FeatureFlagManager:
    """Evaluates feature flags by querying providers in priority order.
    
    Providers are queried in the order they are given. The first provider
    that returns a non-None value wins. Falls back to _DEFAULT_FLAGS.
    """
    
    def __init__(self, providers: list[FeatureFlagProvider]) -> None:
        """Create a FeatureFlagManager with the given providers.
        
        Args:
            providers: Ordered list of flag providers (first = highest priority).
        """
        self._providers = providers
    
    async def is_enabled(self, flag_name: str) -> bool:
        """Return True if the flag is enabled.
        
        Queries providers in priority order. Falls back to defaults.
        """
        for provider in self._providers:
            value = await provider.get(flag_name)
            if value is not None:
                return value
        
        default = _DEFAULT_FLAGS.get(flag_name)
        if default is None:
            log.warning(
                "Unknown feature flag queried, defaulting to False",
                flag_name=flag_name,
            )
            return False
        return default
    
    async def is_disabled(self, flag_name: str) -> bool:
        """Return True if the flag is disabled."""
        return not await self.is_enabled(flag_name)
    
    async def get_all(self) -> dict[str, bool]:
        """Return all flag values, merging all providers and defaults."""
        merged = dict(_DEFAULT_FLAGS)
        # Apply providers in reverse order so highest-priority wins
        for provider in reversed(self._providers):
            provider_flags = await provider.get_all()
            merged.update(provider_flags)
        return merged

    async def snapshot(self) -> "FeatureFlagSnapshot":
        """
        Evaluate all feature flags ONCE and return an immutable snapshot.

        Use this at the start of a pipeline invocation to avoid 5+ individual
        async flag lookups in the hot path. The snapshot is safe to pass to
        synchronous code (no further I/O required).

        Returns:
            FeatureFlagSnapshot with all flag values resolved at this instant.

        Example:
            flags = await feature_flags.snapshot()
            # All subsequent checks are pure dict lookups — zero I/O
            if flags.get("brightdata_unlocker_enabled"):
                ...
            if flags.get("firecrawl_enabled"):
                ...
        """
        values = await self.get_all()
        return FeatureFlagSnapshot(_values=values)
