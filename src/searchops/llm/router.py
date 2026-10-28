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

import re
import time
from typing import TYPE_CHECKING, Any, TypeVar, cast

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from searchops.config.settings import Settings, get_settings
from searchops.core.context.execution import ExecutionContext
from searchops.core.exceptions.infrastructure import LLMError
from searchops.core.observability.metrics import (
    LLM_COST_USD_TOTAL,
    LLM_REQUESTS_TOTAL,
    LLM_TOKENS_TOTAL,
)
from searchops.llm.budget import LLMBudgetTracker
from searchops.llm.tokenizer import count_tokens

if TYPE_CHECKING:
    from searchops.llm.cache import LLMResponseCache

log = structlog.get_logger(__name__)

_SchemaT = TypeVar("_SchemaT", bound=BaseModel)

# ── Provider detection helpers ─────────────────────────────────────────────────


class _EmptyCompletionError(RuntimeError):
    """A model call succeeded but returned no content."""


def _is_claude(model: str) -> bool:
    return "claude" in model.lower()


def _is_gemini(model: str) -> bool:
    return "gemini" in model.lower()


def _is_nvidia(model: str) -> bool:
    # NVIDIA NIM models
    m = model.lower()
    return (
        m.startswith("nvidia/") or "nemotron" in m or "llama" in m or "deepseek" in m or "mistral" in m or "meta/" in m
    )


def _is_glm(model: str) -> bool:
    m = model.lower()
    return m.startswith("glm") or "zhipu" in m or "zai" in m or "codegeex" in m


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
        self._models_cache: dict[tuple[str, float, int | None], Any] = {}
        # Cumulative usage across every call this router served. The research
        # service diffs it around each graph node to report real per-node
        # token use and cost (nodes return strings, not LangChain messages,
        # so usage cannot be read from the state delta).
        self.usage: dict[str, float] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}

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
            model: Any = ChatNVIDIA(
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
                    clean_model = clean_model[len(prefix) :]
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
                model=model_name,
                temperature=temperature,
                max_tokens=max_tok,
                api_key=s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else None,
                timeout=s.request_timeout,
                max_retries=s.max_retries,
            )
        # ── Google Gemini ──────────────────────────────────────────────────
        elif _is_gemini(model_name):
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
            except ImportError as exc:
                raise ImportError("Install langchain-google-genai: uv add langchain-google-genai") from exc
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
                import boto3
                from langchain_aws import ChatBedrock
            except ImportError as exc:
                raise ImportError("Install langchain-aws and boto3: uv add langchain-aws boto3") from exc
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
            "openai": s.openai_default_model,
            "anthropic": s.anthropic_default_model,
            "google": s.google_default_model,
            "nvidia": s.nvidia_default_model,
            "bedrock": s.bedrock_default_model,
            "zhipu": s.zhipu_default_model,
        }
        return mapping.get(s.default_provider, s.nvidia_default_model or "glm-4-flash")

    def _candidate_models(self, model_name: str) -> list[str]:
        """Ordered fallback cascade: the requested model, then each provider
        whose API key is actually configured, de-duplicated preserving order."""
        s = self.settings
        candidate_list = [model_name]
        if s.openai_api_key and "CHANGE_ME" not in s.openai_api_key.get_secret_value():
            candidate_list.append(s.openai_default_model)
        if s.anthropic_api_key and "CHANGE_ME" not in s.anthropic_api_key.get_secret_value():
            candidate_list.append(s.anthropic_default_model)
        if s.google_api_key and "CHANGE_ME" not in s.google_api_key.get_secret_value():
            candidate_list.append(s.google_default_model)
        if s.nvidia_api_key and "CHANGE_ME" not in s.nvidia_api_key.get_secret_value():
            candidate_list.append(s.nvidia_default_model)
        if s.zhipu_api_key and "CHANGE_ME" not in s.zhipu_api_key.get_secret_value():
            candidate_list.append(s.zhipu_default_model)
        # Remove duplicates while preserving order
        unique_models: list[str] = []
        for m in candidate_list:
            if m not in unique_models:
                unique_models.append(m)
        return unique_models

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
        unique_models = self._candidate_models(model_name)

        last_exception: Exception | None = None
        # A model that fails with a permanent error (410 Gone, unknown model,
        # invalid key) is skipped for the rest of the process lifetime. Without
        # this, a dead model costs a full round trip on EVERY call — and a
        # sectioned report makes 9 calls, so one dead entry in the chain
        # silently multiplies into 9 wasted requests per report.
        self._dead_models: set[str] = getattr(self, "_dead_models", set())
        self._dead_until: dict[str, float] = getattr(self, "_dead_until", {})

        def _is_permanent(msg: str) -> bool:
            low = msg.lower()
            return any(
                t in low
                for t in (
                    "410",
                    "gone",
                    "does not exist",
                    "not found",
                    "invalid api key",
                    "incorrect api key",
                    "unsupported model",
                    "unknown model",
                    "no such model",
                    "401",
                )
            )

        def _is_rate_limit(msg: str) -> bool:
            low = msg.lower()
            return any(t in low for t in ("429", "resource_exhausted", "rate limit", "quota"))

        now = time.time()
        only_empty = True
        for active_model in list(unique_models):
            # Skip permanently-dead models; honour a temporary backoff for
            # rate-limited ones so the cascade moves straight to a live model.
            if active_model in self._dead_models:
                continue
            cooldown = self._dead_until.get(active_model, 0.0)
            if cooldown > now:
                continue
            active_provider = self._resolve_provider_label(active_model)
            try:
                chat_model = self._get_model(active_model, temperature, max_tokens)
                messages: list[BaseMessage] = []
                if system_prompt:
                    messages.append(SystemMessage(content=system_prompt))
                messages.append(HumanMessage(content=prompt))

                response = await chat_model.ainvoke(messages)
                response_text = str(response.content)
                if not response_text.strip():
                    # Reasoning models can spend the whole token budget
                    # thinking and return no content. Retry once, then fall
                    # through the cascade instead of handing callers "".
                    response = await chat_model.ainvoke(messages)
                    response_text = str(response.content)
                if not response_text.strip():
                    raise _EmptyCompletionError(f"{active_model} returned an empty completion")

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
                LLM_TOKENS_TOTAL.labels(provider=active_provider, model=active_model, token_type="prompt").inc(
                    prompt_tokens
                )
                LLM_TOKENS_TOTAL.labels(provider=active_provider, model=active_model, token_type="completion").inc(
                    completion_tokens
                )
                LLM_COST_USD_TOTAL.labels(provider=active_provider, model=active_model).inc(cost)
                usage = getattr(self, "usage", None)
                if isinstance(usage, dict):
                    usage["calls"] = usage.get("calls", 0) + 1
                    usage["prompt_tokens"] = usage.get("prompt_tokens", 0) + prompt_tokens
                    usage["completion_tokens"] = usage.get("completion_tokens", 0) + completion_tokens
                    usage["cost_usd"] = usage.get("cost_usd", 0.0) + float(cost or 0.0)

                # Store in cache
                if self.cache and temperature == 0.0:
                    await self.cache.set(active_model, cache_prompt_key, temperature, response_text)

                return response_text

            except Exception as exc:
                last_exception = exc
                if isinstance(exc, _EmptyCompletionError):
                    log.warning("LLM returned empty completion; trying fallback cascade", model=active_model)
                    continue
                only_empty = False
                LLM_REQUESTS_TOTAL.labels(provider=active_provider, model=active_model, status="error").inc()
                err_text = str(exc)
                if _is_permanent(err_text):
                    # Retired for the rest of the process — a model that has
                    # reached end of life will not come back.
                    self._dead_models.add(active_model)
                    log.warning(
                        "LLM model retired permanently; skipping on all later calls",
                        model=active_model,
                        provider=active_provider,
                    )
                elif _is_rate_limit(err_text):
                    # Honour the provider's own retry hint when present, else
                    # back off briefly, so the cascade tries a live model first.
                    retry_secs = 300.0
                    retry_match = re.search(r"retry in (\d+)m", err_text, re.IGNORECASE)
                    if retry_match:
                        retry_secs = min(float(retry_match.group(1)) * 60.0, 3600.0)
                    self._dead_until[active_model] = time.time() + retry_secs
                    log.warning(
                        "LLM model rate-limited; backing off",
                        model=active_model,
                        provider=active_provider,
                        backoff_seconds=int(retry_secs),
                    )
                log.warning(
                    "LLM model call failed, trying fallback cascade",
                    model=active_model,
                    provider=active_provider,
                    error=err_text,
                )

        if only_empty and isinstance(last_exception, _EmptyCompletionError):
            # Every model answered, but with nothing. Callers treat "" as "no
            # output" and degrade honestly; that is the existing contract.
            log.error("All models returned empty completions", models=unique_models)
            return ""
        log.error(
            "All fallback models failed in LLM router cascade",
            models=unique_models,
            error=str(last_exception),
        )
        raise LLMError(service=f"LLM:{provider}", cause=last_exception) from last_exception

    async def generate_structured(
        self,
        prompt: str,
        schema: type[_SchemaT],
        *,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        context: ExecutionContext | None = None,
    ) -> _SchemaT | None:
        """Return a provider-validated instance of ``schema`` — no JSON repair.

        Uses LangChain's native ``.with_structured_output(schema)``, so the
        provider constrains generation to the schema at the API level and the
        caller never parses free-form text or runs the repair ladder. Mirrors
        :meth:`generate` for caching, the fallback cascade, budget enforcement
        and metrics.

        Returns ``None`` (never raises) when no configured model can produce a
        valid instance, so callers can degrade to the free-form path instead of
        failing the whole run.
        """
        model_name = model or self._resolve_default_model()
        exec_ctx = context or ExecutionContext.create()

        schema_name = getattr(schema, "__name__", "schema")
        cache_prompt_key = f"[structured:{schema_name}]{system_prompt or ''}||{prompt}"

        # 1. Cache check (deterministic requests only). Structured results are
        #    cached as their JSON dump and re-validated on read.
        if self.cache and temperature == 0.0:
            cached = await self.cache.get(model_name, cache_prompt_key, temperature)
            if cached is not None:
                try:
                    return schema.model_validate_json(cached)
                except Exception:  # pragma: no cover - stale/incompatible cache entry
                    log.debug("Structured cache entry failed re-validation; ignoring", schema=schema_name)

        # 2. Max prompt length safety cap (word-boundary truncated)
        max_chars = getattr(self.settings, "max_prompt_chars", 12_000)
        if len(prompt) > max_chars:
            prompt = prompt[:max_chars].rsplit(" ", 1)[0]

        unique_models = self._candidate_models(model_name)
        self._dead_models = getattr(self, "_dead_models", set())
        self._dead_until = getattr(self, "_dead_until", {})

        last_exception: Exception | None = None
        now = time.time()
        for active_model in list(unique_models):
            if active_model in self._dead_models:
                continue
            if self._dead_until.get(active_model, 0.0) > now:
                continue
            active_provider = self._resolve_provider_label(active_model)
            try:
                chat_model = self._get_model(active_model, temperature, max_tokens)
                structured_model = chat_model.with_structured_output(schema, include_raw=True)

                messages: list[BaseMessage] = []
                if system_prompt:
                    messages.append(SystemMessage(content=system_prompt))
                messages.append(HumanMessage(content=prompt))

                result = await structured_model.ainvoke(messages)

                # include_raw=True yields {"raw": AIMessage, "parsed": obj, "parsing_error": ...}
                parsed_obj: Any = None
                raw_message: Any = None
                if isinstance(result, dict):
                    parsed_obj = result.get("parsed")
                    raw_message = result.get("raw")
                    if result.get("parsing_error"):
                        raise _EmptyCompletionError(f"{active_model} structured parse error")
                else:  # some providers return the parsed object directly
                    parsed_obj = result

                if parsed_obj is None:
                    raise _EmptyCompletionError(f"{active_model} returned no structured output")
                if not isinstance(parsed_obj, schema):
                    parsed_obj = schema.model_validate(parsed_obj)

                # Token accounting from the raw message when present, else estimate.
                prompt_tokens: int | None = None
                completion_tokens: int | None = None
                meta = getattr(raw_message, "response_metadata", None)
                if isinstance(meta, dict):
                    usage = meta.get("token_usage") or meta.get("usage")
                    if isinstance(usage, dict):
                        p_tok = usage.get("prompt_tokens") or usage.get("input_tokens")
                        c_tok = usage.get("completion_tokens") or usage.get("output_tokens")
                        if isinstance(p_tok, int):
                            prompt_tokens = p_tok
                        if isinstance(c_tok, int):
                            completion_tokens = c_tok
                completion_text = parsed_obj.model_dump_json()
                if prompt_tokens is None:
                    prompt_tokens = int(count_tokens(prompt, active_model))
                if completion_tokens is None:
                    completion_tokens = int(count_tokens(completion_text, active_model))

                cost = LLMBudgetTracker.record_and_check(
                    context=exec_ctx,
                    model_name=active_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )

                LLM_REQUESTS_TOTAL.labels(provider=active_provider, model=active_model, status="success").inc()
                LLM_TOKENS_TOTAL.labels(provider=active_provider, model=active_model, token_type="prompt").inc(
                    prompt_tokens
                )
                LLM_TOKENS_TOTAL.labels(provider=active_provider, model=active_model, token_type="completion").inc(
                    completion_tokens
                )
                LLM_COST_USD_TOTAL.labels(provider=active_provider, model=active_model).inc(cost)
                usage_acc = getattr(self, "usage", None)
                if isinstance(usage_acc, dict):
                    usage_acc["calls"] = usage_acc.get("calls", 0) + 1
                    usage_acc["prompt_tokens"] = usage_acc.get("prompt_tokens", 0) + prompt_tokens
                    usage_acc["completion_tokens"] = usage_acc.get("completion_tokens", 0) + completion_tokens
                    usage_acc["cost_usd"] = usage_acc.get("cost_usd", 0.0) + float(cost or 0.0)

                if self.cache and temperature == 0.0:
                    await self.cache.set(active_model, cache_prompt_key, temperature, completion_text)

                return cast("_SchemaT", parsed_obj)

            except Exception as exc:
                last_exception = exc
                if isinstance(exc, _EmptyCompletionError):
                    log.warning("Structured output empty/invalid; trying fallback", model=active_model)
                    continue
                LLM_REQUESTS_TOTAL.labels(provider=active_provider, model=active_model, status="error").inc()
                err_text = str(exc)
                low = err_text.lower()
                if any(t in low for t in ("429", "resource_exhausted", "rate limit", "quota")):
                    self._dead_until[active_model] = time.time() + 300.0
                log.warning(
                    "Structured LLM call failed, trying fallback cascade",
                    model=active_model,
                    provider=active_provider,
                    error=err_text,
                )

        # No model produced a valid instance. Return None so the caller can fall
        # back to free-form generation + repair rather than failing the run.
        log.warning(
            "Structured output unavailable from all candidates; caller should degrade",
            schema=schema_name,
            error=str(last_exception) if last_exception else None,
        )
        return None
