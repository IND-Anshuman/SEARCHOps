"""
Unit tests for LLM Tokenizer, Budget Tracker, Cache, Router, and Z.AI / GLM integration.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from searchops.config.subsystems.llm import LLMSettings
from searchops.core.context.execution import ExecutionContext
from searchops.core.exceptions.application import BudgetExceededError
from searchops.infrastructure.cache.redis import RedisCache
from searchops.llm.budget import LLMBudgetTracker
from searchops.llm.cache import LLMResponseCache
from searchops.llm.router import LLMRouter
from searchops.llm.tokenizer import count_tokens


@pytest.mark.unit
def test_tokenizer():
    count = count_tokens("Hello world from SEARCHOps", "gpt-4o")
    assert count > 0


@pytest.mark.unit
def test_budget_tracker_cost_calculation():
    cost = LLMBudgetTracker.calculate_cost("gpt-4o", prompt_tokens=1000, completion_tokens=500)
    assert cost > 0.0


@pytest.mark.unit
def test_budget_tracker_enforcement():
    ctx = ExecutionContext.create(max_tokens=100)
    with pytest.raises(BudgetExceededError):
        LLMBudgetTracker.record_and_check(
            context=ctx,
            model_name="gpt-4o",
            prompt_tokens=150,
            completion_tokens=50,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_cache():
    mock_redis = AsyncMock(spec=RedisCache)
    mock_redis.get.return_value = "Cached output"

    cache = LLMResponseCache(mock_redis)
    res = await cache.get("gpt-4o", "What is AI?", 0.0)
    assert res == "Cached output"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_router_generate():
    router = LLMRouter()
    with patch.object(router, "_get_model") as mock_get_model:
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value.content = "Synthetic LLM output"
        mock_get_model.return_value = mock_model

        output = await router.generate("Explain quantum computing", model="gpt-4o")
        assert output == "Synthetic LLM output"


@pytest.mark.unit
def test_zai_api_key_alias_resolution(monkeypatch):
    """Verify Z.AI API key is picked up from ZAI_API_KEY or ZHIPU_API_KEY environment variables."""
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.setenv("ZAI_API_KEY", "test-zai-key-12345")
    settings = LLMSettings()
    assert settings.zhipu_api_key is not None
    assert settings.zhipu_api_key.get_secret_value() == "test-zai-key-12345"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_router_zai_glm_generation():
    """Verify routing to Z.AI GLM models works seamlessly."""
    router = LLMRouter()
    with patch.object(router, "_get_model") as mock_get_model:
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value.content = "GLM-4 Flash Response"
        mock_get_model.return_value = mock_model

        output = await router.generate("Hello GLM", model="glm-4-flash")
        assert output == "GLM-4 Flash Response"
        mock_get_model.assert_called_once()
        assert router._resolve_provider_label("glm-4-flash") == "zhipu"
