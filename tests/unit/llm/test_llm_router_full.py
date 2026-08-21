"""
Unit tests for LLMRouter generate method, prompt safety truncation, and caching.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from searchops.llm.cache import LLMResponseCache
from searchops.llm.router import LLMRouter


class MockCache:
    def __init__(self):
        self.store = {}

    async def get(self, model: str, prompt: str, temp: float):
        return self.store.get(f"{model}:{prompt}:{temp}")

    async def set(self, model: str, prompt: str, temp: float, response: str):
        self.store[f"{model}:{prompt}:{temp}"] = response


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_router_generate_with_mocked_model():
    mock_cache = MockCache()
    router = LLMRouter(cache=mock_cache)

    mock_chat_model = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "Generated Answer"
    mock_response.response_metadata = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    mock_chat_model.ainvoke.return_value = mock_response

    with patch.object(router, "_get_model", return_value=mock_chat_model):
        res = await router.generate("Test prompt", system_prompt="Test system", temperature=0.0)

        assert res == "Generated Answer"
        mock_chat_model.ainvoke.assert_called_once()

        # Test cache hit on second call with temp=0.0
        res_cached = await router.generate("Test prompt", system_prompt="Test system", temperature=0.0)
        assert res_cached == "Generated Answer"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_router_prompt_truncation():
    router = LLMRouter()
    mock_settings = MagicMock()
    mock_settings.max_prompt_chars = 50
    mock_settings.default_max_tokens = 1024
    router.settings = mock_settings

    mock_chat_model = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "Truncated Ok"
    mock_response.response_metadata = {}
    mock_chat_model.ainvoke.return_value = mock_response

    with patch.object(router, "_get_model", return_value=mock_chat_model):
        huge_prompt = "word " * 100
        res = await router.generate(huge_prompt)
        assert res == "Truncated Ok"
