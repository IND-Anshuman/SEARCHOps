from __future__ import annotations

import pytest

from searchops.feature_flags.providers import EnvFeatureFlagProvider, InMemoryFeatureFlagProvider
from searchops.feature_flags.manager import FeatureFlagManager


@pytest.mark.unit
@pytest.mark.asyncio
async def test_env_feature_flag_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FEATURE_FIRECRAWL_ENABLED", "true")
    monkeypatch.setenv("FEATURE_TEST_FLAG", "1")
    provider = EnvFeatureFlagProvider()

    assert await provider.get("firecrawl_enabled") is True
    assert await provider.get("test_flag") is True
    assert await provider.get("non_existent") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_in_memory_feature_flag_provider():
    provider = InMemoryFeatureFlagProvider({"test_flag": True})
    assert await provider.get("test_flag") is True
    provider.set("test_flag", False)
    assert await provider.get("test_flag") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_feature_flag_manager():
    provider = InMemoryFeatureFlagProvider({"firecrawl_enabled": False})
    manager = FeatureFlagManager(providers=[provider])

    assert await manager.is_enabled("firecrawl_enabled") is False
    assert await manager.is_enabled("knowledge_graph_enabled") is True  # default
    assert await manager.is_disabled("firecrawl_enabled") is True
