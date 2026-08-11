"""
Exact Match Response Cache for LLM Prompts.
"""

from __future__ import annotations

import hashlib
from typing import Any

import structlog

from searchops.core.interfaces.memory import ICache

log = structlog.get_logger(__name__)


class LLMResponseCache:
    """Exact-match LLM response cache backed by ICache."""

    def __init__(self, cache_backend: ICache, default_ttl_seconds: int = 86400) -> None:
        self.cache_backend = cache_backend
        self.default_ttl_seconds = default_ttl_seconds

    @staticmethod
    def _make_key(model_name: str, prompt: str, temperature: float) -> str:
        raw = f"{model_name}:{temperature}:{prompt}"
        hashed = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"llm:cache:{hashed}"

    async def get(self, model_name: str, prompt: str, temperature: float) -> str | None:
        """Lookup cached completion for prompt."""
        key = self._make_key(model_name, prompt, temperature)
        val = await self.cache_backend.get(key)
        if val:
            log.debug("LLM cache hit", model=model_name)
            return str(val)
        return None

    async def set(
        self,
        model_name: str,
        prompt: str,
        temperature: float,
        response_text: str,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Cache prompt completion."""
        key = self._make_key(model_name, prompt, temperature)
        ttl = ttl_seconds or self.default_ttl_seconds
        return await self.cache_backend.set(key, response_text, ttl_seconds=ttl)
