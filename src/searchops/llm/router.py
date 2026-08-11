"""
Cost-Aware Multi-Provider LLM Router — free-tier hardened.

Supported providers
────────────────────
  openai    - ChatOpenAI  (gpt-4o-mini default)
  anthropic - ChatAnthropic  (claude-haiku-4-5 default)
  google    - ChatGoogleGenerativeAI  (gemini-2.0-flash default, free)
  nvidia    - ChatOpenAI with NVIDIA NIM base_url  (OpenAI-compatible)
  bedrock   - ChatBedrock  (nova-lite default)
  zhipu     - ChatOpenAI with Z.AI base_url  (glm-4-flash default, free)

Free-tier protections applied on every call:
  1. Prompt truncated to settings.llm.max_prompt_chars before sending
  2. max_tokens capped to settings.llm.default_max_tokens (1024 default)
  3. Cache-checked first (deterministic temp=0.0 hits only)
  4. Budget-gated   (token + USD limits enforced)
  5. Metrics-recorded  (OTel + Prometheus counters)
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from searchops.config.settings import Settings, get_settings
from searchops.core.context.execution import ExecutionContext
from searchops.core.exceptions.infrastructure import LLMError
from searchops.core.observability.metrics import (
    LLM_COST_USD_TOTAL,
    LLM_REQUESTS_TOTAL,
    LLM_TOKENS_TOTAL,
)
from searchops.llm.budget import LLMBudgetTracker
from searchops.llm.cache import LLMResponseCache
from searchops.llm.tokenizer import count_tokens

log = structlog.get_logger(__name__)

# ── Provider detection helpers ─────────────────────────────────────────────────

def _is_claude(model: str) -> bool:
    return "claude" in model.lower()

def _is_gemini(model: str) -> bool:
    return "gemini" in model.lower()

def _is_nvidia(model: str) -> bool:
    # NVIDIA NIM models
    m = model.lower()
    return m.startswith("nvidia/") or "nemotron" in m or "llama" in m or "deepseek" in m or "mistral" in m or "meta/" in m

def _is_glm(model: str) -> bool:
    m = model.lower()
    return (
        m.startswith("glm")
        or "zhipu" in m
        or "zai" in m
        or "codegeex" in m
    )

def _is_bedrock(model: str) -> bool:
    # Bedrock model IDs always contain a dot, e.g. anthropic.claude-…
    m = model.lower()
    return ("amazon." in m or "anthropic." in m or "cohere." in m or "meta.llama" in m) and not m.startswith("http")


class LLMRouter:
    """Multi-Provider LLM Router with model caching, cost tracking, and metrics."""

    def __init__(
        self,
        cache: LLMResponseCache | None = None,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or get_settings()
        self.settings = cfg.llm
        self.cache = cache
        self._models_cache: dict[tuple[str, float], Any] = {}

    # ── Provider factory ───────────────────────────────────────────────────

    def _get_model(self, model_name: str, temperature: float, max_tokens: int | None = None) -> Any:
        """Instantiate or return cached LangChain Chat Model for the given model name & temp."""
        cache_key = (model_name, temperature, max_tokens)
        if cache_key in self._models_cache:
            return self._models_cache[cache_key]

        s = self.settings
        max_tok = max_tokens or s.default_max_tokens

        # ── NVIDIA NIM ────────────────────────────────────────────────────
        if _is_nvidia(model_name):
            try:
                from langchain_nvidia_ai_endpoints import ChatNVIDIA
            except ImportError as exc:
                raise ImportError(
                    "Install langchain-nvidia-ai-endpoints: uv add langchain-nvidia-ai-endpoints"
                ) from exc
            model = ChatNVIDIA(
                model=model_name,
                temperature=temperature,
                max_tokens=max_tok,
                nvidia_api_key=s.nvidia_api_key.get_secret_value() if s.nvidia_api_key else None,
                base_url=s.nvidia_base_url,
            )
        # ── Zhipu AI / Z.AI (GLM, OpenAI-compatible) ─────────────────────
        elif _is_glm(model_name):
            clean_model = model_name
            for prefix in ("zhipu/", "zai/", "z.ai/"):
                if clean_model.lower().startswith(prefix):
                    clean_model = clean_model[len(prefix):]
                    break

            api_key = s.zhipu_api_key.get_secret_value() if s.zhipu_api_key else None
            model = ChatOpenAI(
                model_name=clean_model,
                temperature=temperature,
                max_tokens=max_tok,
                api_key=api_key,
                base_url=s.zhipu_base_url,
                timeout=s.request_timeout,
                max_retries=s.max_retries,
            )
        # ── Anthropic ─────────────────────────────────────────────────────
        elif _is_claude(model_name) and not _is_bedrock(model_name):
            model = ChatAnthropic(
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tok,
                api_key=s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else None,
                timeout=s.request_timeout,
                max_retries=s.max_retries,
            )
        # ── Google Gemini ──────────────────────────────────────────────────
        elif _is_gemini(model_name):
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import]
            except ImportError as exc:
                raise ImportError(
                    "Install langchain-google-genai: uv add langchain-google-genai"
                ) from exc
            model = ChatGoogleGenerativeAI(
                model=model_name,
                temperature=temperature,
                max_output_tokens=max_tok,
                google_api_key=s.google_api_key.get_secret_value() if s.google_api_key else None,
                convert_system_message_to_human=True,
                max_retries=s.max_retries,
            )
        # ── Amazon Bedrock ────────────────────────────────────────────────
        elif _is_bedrock(model_name):
            try:
                from langchain_aws import ChatBedrock  # type: ignore[import]
                import boto3  # type: ignore[import]
            except ImportError as exc:
                raise ImportError(
                    "Install langchain-aws and boto3: uv add langchain-aws boto3"
                ) from exc
            session_kwargs: dict[str, Any] = {"region_name": s.bedrock_aws_region}
            if s.bedrock_aws_access_key_id and s.bedrock_aws_secret_access_key:
                session_kwargs["aws_access_key_id"] = s.bedrock_aws_access_key_id.get_secret_value()
                session_kwargs["aws_secret_access_key"] = s.bedrock_aws_secret_access_key.get_secret_value()
            boto_session = boto3.Session(**session_kwargs)
            model = ChatBedrock(
                model_id=model_name,
                client=boto_session.client("bedrock-runtime"),
                model_kwargs={"temperature": temperature, "max_tokens": max_tok},
            )
            api_key = s.zhipu_api_key.get_secret_value() if s.zhipu_api_key else None
            model = ChatOpenAI(
                model_name=clean_model,
                temperature=temperature,
                max_tokens=max_tok,
                api_key=api_key,
                base_url=s.zhipu_base_url,
                timeout=s.request_timeout,
                max_retries=s.max_retries,
            )
        # ── OpenAI (default fallback) ─────────────────────────────────────
        else:
            default_headers = {}
            if s.openai_base_url and "openrouter.ai" in s.openai_base_url.lower():
                default_headers = {
                    "HTTP-Referer": "https://github.com/IND-Anshuman/SEARCHOps",
                    "X-Title": "SEARCHOps",
                }
            model = ChatOpenAI(
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tok,
                api_key=s.openai_api_key.get_secret_value() if s.openai_api_key else None,
                base_url=s.openai_base_url,
                organization=s.openai_org_id,
                timeout=s.request_timeout,
                max_retries=s.max_retries,
                default_headers=default_headers if default_headers else None,
            )

        self._models_cache[cache_key] = model
        return model

    def _resolve_provider_label(self, model_name: str) -> str:
        """Return a short provider label for metrics/logging."""
        if _is_claude(model_name) and not _is_bedrock(model_name):
            return "anthropic"
        if _is_gemini(model_name):
            return "google"
        if _is_bedrock(model_name):
            return "bedrock"
        if _is_nvidia(model_name):
            return "nvidia"
        if _is_glm(model_name):
            return "zhipu"
        return "openai"

    def _resolve_default_model(self) -> str:
        """Pick the configured default model for the active provider."""
        s = self.settings
        mapping = {
            "openai":    s.openai_default_model,
            "anthropic": s.anthropic_default_model,
            "google":    s.google_default_model,
            "nvidia":    s.nvidia_default_model,
            "bedrock":   s.bedrock_default_model,
            "zhipu":     s.zhipu_default_model,
        }
        return mapping.get(s.default_provider, s.nvidia_default_model or "glm-4-flash")

    # ── Public API ─────────────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        context: ExecutionContext | None = None,
    ) -> str:
        """Generate a response using the resolved provider with SystemMessage separation."""
        model_name = model or self._resolve_default_model()
        exec_ctx = context or ExecutionContext.create()
        provider = self._resolve_provider_label(model_name)

        cache_prompt_key = f"{system_prompt or ''}||{prompt}"

        # 1. Cache check (deterministic requests only)
        if self.cache and temperature == 0.0:
            cached = await self.cache.get(model_name, cache_prompt_key, temperature)
            if cached is not None:
                log.debug("LLM cache hit", model=model_name)
                return cached

        # 2. Max prompt length safety cap (word-boundary truncated)
        max_chars = getattr(self.settings, "max_prompt_chars", 12_000)
        if len(prompt) > max_chars:
            prompt = prompt[:max_chars].rsplit(" ", 1)[0]
            log.debug("Prompt safe-truncated to char limit", limit=max_chars)

        # Build dynamic fallback candidates based on actually configured API keys
        s = self.settings
        candidate_list = [model_name]
        
        if s.nvidia_api_key and "CHANGE_ME" not in s.nvidia_api_key.get_secret_value():
            candidate_list.append(s.nvidia_default_model)
        if s.zhipu_api_key and "CHANGE_ME" not in s.zhipu_api_key.get_secret_value():
            candidate_list.append(s.zhipu_default_model)
        if s.openai_api_key and "CHANGE_ME" not in s.openai_api_key.get_secret_value():
            candidate_list.append(s.openai_default_model)
        if s.anthropic_api_key and "CHANGE_ME" not in s.anthropic_api_key.get_secret_value():
            candidate_list.append(s.anthropic_default_model)
        if s.google_api_key and "CHANGE_ME" not in s.google_api_key.get_secret_value():
            candidate_list.append(s.google_default_model)
        # Remove duplicates while preserving order
        unique_models: list[str] = []
        for m in candidate_list:
            if m not in unique_models:
                unique_models.append(m)

        last_exception: Exception | None = None
        for active_model in unique_models:
            active_provider = self._resolve_provider_label(active_model)
            try:
                chat_model = self._get_model(active_model, temperature, max_tokens)
                messages: list[BaseMessage] = []
                if system_prompt:
                    messages.append(SystemMessage(content=system_prompt))
                messages.append(HumanMessage(content=prompt))

                response = await chat_model.ainvoke(messages)
                response_text = str(response.content)

                # Extract token usage from metadata if present and valid dict, otherwise count
                meta = getattr(response, "response_metadata", None)
                prompt_tokens = None
                completion_tokens = None
                if isinstance(meta, dict):
                    usage = meta.get("token_usage") or meta.get("usage")
                    if isinstance(usage, dict):
                        p_tok = usage.get("prompt_tokens") or usage.get("input_tokens")
                        c_tok = usage.get("completion_tokens") or usage.get("output_tokens")
                        if isinstance(p_tok, int):
                            prompt_tokens = p_tok
                        if isinstance(c_tok, int):
                            completion_tokens = c_tok

                if prompt_tokens is None:
                    prompt_tokens = int(count_tokens(prompt, active_model))
                if completion_tokens is None:
                    completion_tokens = int(count_tokens(response_text, active_model))

                # Budget enforcement
                cost = LLMBudgetTracker.record_and_check(
                    context=exec_ctx,
                    model_name=active_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )

                # Prometheus / OTel metrics
                LLM_REQUESTS_TOTAL.labels(provider=active_provider, model=active_model, status="success").inc()
                LLM_TOKENS_TOTAL.labels(provider=active_provider, model=active_model, token_type="prompt").inc(prompt_tokens)
                LLM_TOKENS_TOTAL.labels(provider=active_provider, model=active_model, token_type="completion").inc(completion_tokens)
                LLM_COST_USD_TOTAL.labels(provider=active_provider, model=active_model).inc(cost)

                # Store in cache
                if self.cache and temperature == 0.0:
                    await self.cache.set(active_model, cache_prompt_key, temperature, response_text)

                return response_text

            except Exception as exc:
                last_exception = exc
                LLM_REQUESTS_TOTAL.labels(provider=active_provider, model=active_model, status="error").inc()
                log.warning("LLM model call failed, trying fallback cascade", model=active_model, provider=active_provider, error=str(exc))

        log.error("All fallback models failed in LLM router cascade", models=unique_models, error=str(last_exception))
        raise LLMError(service=f"LLM:{provider}", cause=last_exception) from last_exception
