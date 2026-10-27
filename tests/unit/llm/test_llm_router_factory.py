"""
Unit tests for LLMRouter: provider detection, the provider-label resolver,
default-model selection, prompt truncation, cache behaviour, token/cost
extraction from response metadata, and the fallback cascade.

All provider SDKs are stubbed — no network calls and no billed API usage.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from searchops.config.subsystems.llm import LLMSettings
from searchops.core.exceptions.application import BudgetExceededError
from searchops.llm import router as router_mod
from searchops.llm.router import (
    LLMRouter,
    _is_bedrock,
    _is_claude,
    _is_gemini,
    _is_glm,
    _is_nvidia,
)

# ── Provider detection helpers ────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-3-5-sonnet", True),
        ("anthropic.claude-3-haiku", True),  # Bedrock ids also contain "claude"
        ("CLAUDE-OPUS", True),  # case-insensitive
        ("gpt-4o", False),
        ("gemini-1.5-flash", False),
    ],
)
def test_is_claude(model, expected):
    assert _is_claude(model) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "expected"),
    [("gemini-1.5-flash", True), ("Gemini-Pro", True), ("gpt-4o", False), ("claude-3", False)],
)
def test_is_gemini(model, expected):
    assert _is_gemini(model) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("nvidia/llama-3.1-nemotron-70b", True),
        ("nemotron-mini", True),
        ("meta/llama-3.1-8b-instruct", True),
        ("deepseek-v3", True),
        ("mistral-large", True),
        ("gpt-4o", False),
        ("claude-3", False),
    ],
)
def test_is_nvidia(model, expected):
    assert _is_nvidia(model) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("glm-4-flash", True),
        ("zhipu/glm-4-plus", True),
        ("zai-glm", True),
        ("codegeex-4", True),
        ("gpt-4o", False),
    ],
)
def test_is_glm(model, expected):
    assert _is_glm(model) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("anthropic.claude-3-sonnet", True),
        ("amazon.nova-pro", True),
        ("cohere.command-r", True),
        ("meta.llama3-70b", True),
        ("claude-3-5-sonnet", False),  # direct Anthropic API, not Bedrock
        ("gpt-4o", False),
    ],
)
def test_is_bedrock(model, expected):
    assert _is_bedrock(model) is expected


@pytest.mark.unit
def test_is_bedrock_rejects_http_prefixed_ids():
    assert _is_bedrock("https://meta.llama.example/model") is False


# ── Provider label + default model resolution ─────────────────────────────────


@pytest.fixture
def settings() -> LLMSettings:
    return LLMSettings()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-3-5-sonnet", "anthropic"),
        ("gemini-1.5-flash", "google"),
        ("anthropic.claude-3-haiku", "bedrock"),
        ("nvidia/llama-3.1-nemotron-70b", "nvidia"),
        ("glm-4-flash", "zhipu"),
        ("gpt-4o", "openai"),
        ("some-unknown-model", "openai"),  # default branch
    ],
)
def test_resolve_provider_label(settings, model, expected):
    assert LLMRouter(settings=_root_settings(settings))._resolve_provider_label(model) == expected


@pytest.mark.unit
def test_resolve_default_model_follows_configured_provider():
    # LLMSettings is frozen and validates default_provider against a whitelist,
    # so instances must be built with the values they need.
    llm = LLMSettings(default_provider="anthropic", anthropic_default_model="claude-haiku-4-5")

    assert LLMRouter(settings=_root_settings(llm))._resolve_default_model() == "claude-haiku-4-5"


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "nvidia", "bedrock", "zhipu"])
def test_resolve_default_model_covers_every_supported_provider(provider):
    kwargs = {f"{provider}_default_model": f"model-for-{provider}"}
    llm = LLMSettings(default_provider=provider, **kwargs)

    assert LLMRouter(settings=_root_settings(llm))._resolve_default_model() == f"model-for-{provider}"


@pytest.mark.unit
def test_resolve_default_model_unknown_provider_falls_back_to_nvidia():
    """An unvalidated provider string must not crash resolution."""
    llm = LLMSettings()
    # Bypass the field validator to simulate a stale/corrupt stored setting.
    object.__setattr__(llm, "default_provider", "does-not-exist")
    object.__setattr__(llm, "nvidia_default_model", "nvidia/nemotron")

    assert LLMRouter(settings=_root_settings(llm))._resolve_default_model() == "nvidia/nemotron"


@pytest.mark.unit
def test_resolve_default_model_ultimate_fallback_is_glm():
    llm = LLMSettings()
    object.__setattr__(llm, "default_provider", "does-not-exist")
    object.__setattr__(llm, "nvidia_default_model", "")

    assert LLMRouter(settings=_root_settings(llm))._resolve_default_model() == "glm-4-flash"


# ── generate(): happy path and metadata handling ──────────────────────────────


def _root_settings(llm: LLMSettings):
    """LLMRouter.__init__ reads `settings.llm`, so wrap the subsystem settings."""
    return SimpleNamespace(llm=llm)


def _make_router(settings, cache=None):
    return LLMRouter(cache=cache, settings=_root_settings(settings))


def _response(text: str, metadata: dict | None = None):
    return SimpleNamespace(content=text, response_metadata=metadata or {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_returns_content_and_separates_system_prompt(settings):
    router = _make_router(settings)
    fake_model = AsyncMock()
    fake_model.ainvoke.return_value = _response("ANSWER")

    with patch.object(LLMRouter, "_get_model", return_value=fake_model):
        out = await router.generate("hello", system_prompt="be terse", model="gpt-4o-mini")

    assert out == "ANSWER"
    messages = fake_model.ainvoke.call_args[0][0]
    assert len(messages) == 2, "SystemMessage and HumanMessage must be separate"
    assert messages[0].content == "be terse"
    assert messages[1].content == "hello"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_without_system_prompt_sends_single_message(settings):
    router = _make_router(settings)
    fake_model = AsyncMock()
    fake_model.ainvoke.return_value = _response("OK")

    with patch.object(LLMRouter, "_get_model", return_value=fake_model):
        await router.generate("hello", model="gpt-4o-mini")

    assert len(fake_model.ainvoke.call_args[0][0]) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_uses_explicit_token_usage_from_metadata(settings):
    router = _make_router(settings)
    fake_model = AsyncMock()
    fake_model.ainvoke.return_value = _response(
        "ANS",
        {"token_usage": {"prompt_tokens": 120, "completion_tokens": 30}},
    )

    with (
        patch.object(LLMRouter, "_get_model", return_value=fake_model),
        patch.object(router_mod.LLMBudgetTracker, "record_and_check", return_value=0.01) as rec,
    ):
        await router.generate("hello", model="gpt-4o-mini")

    kwargs = rec.call_args.kwargs
    assert kwargs["prompt_tokens"] == 120
    assert kwargs["completion_tokens"] == 30


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_accepts_provider_style_usage_keys(settings):
    router = _make_router(settings)
    fake_model = AsyncMock()
    fake_model.ainvoke.return_value = _response("ANS", {"token_usage": {"input_tokens": 7, "output_tokens": 3}})

    with (
        patch.object(LLMRouter, "_get_model", return_value=fake_model),
        patch.object(router_mod.LLMBudgetTracker, "record_and_check", return_value=0.0) as rec,
    ):
        await router.generate("hello", model="gpt-4o-mini")

    assert rec.call_args.kwargs["prompt_tokens"] == 7
    assert rec.call_args.kwargs["completion_tokens"] == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_counts_tokens_when_metadata_missing(settings):
    router = _make_router(settings)
    fake_model = AsyncMock()
    fake_model.ainvoke.return_value = _response("ANS")  # no metadata at all

    with (
        patch.object(LLMRouter, "_get_model", return_value=fake_model),
        patch.object(router_mod.LLMBudgetTracker, "record_and_check", return_value=0.0) as rec,
    ):
        await router.generate("hello world", model="gpt-4o-mini")

    assert rec.call_args.kwargs["prompt_tokens"] > 0, "Tokenizer must be used as fallback"
    assert rec.call_args.kwargs["completion_tokens"] > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_ignores_non_integer_token_usage(settings):
    router = _make_router(settings)
    fake_model = AsyncMock()
    fake_model.ainvoke.return_value = _response(
        "ANS", {"token_usage": {"prompt_tokens": "many", "completion_tokens": None}}
    )

    with (
        patch.object(LLMRouter, "_get_model", return_value=fake_model),
        patch.object(router_mod.LLMBudgetTracker, "record_and_check", return_value=0.0) as rec,
    ):
        await router.generate("hello", model="gpt-4o-mini")

    assert rec.call_args.kwargs["prompt_tokens"] > 0, "Bad values fall back to the tokenizer"


# ── generate(): prompt truncation ─────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_over_long_prompt_is_truncated_on_word_boundary(settings):
    settings = LLMSettings(max_prompt_chars=50)
    router = _make_router(settings)
    fake_model = AsyncMock()
    fake_model.ainvoke.return_value = _response("OK")

    long_prompt = "word " * 200

    with patch.object(LLMRouter, "_get_model", return_value=fake_model):
        await router.generate(long_prompt, model="gpt-4o-mini")

    sent = fake_model.ainvoke.call_args[0][0][-1].content
    assert len(sent) <= 50
    assert not sent.endswith(" "), "Truncation must cut on a word boundary"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_short_prompt_is_sent_untouched(settings):
    settings = LLMSettings(max_prompt_chars=12_000)
    router = _make_router(settings)
    fake_model = AsyncMock()
    fake_model.ainvoke.return_value = _response("OK")

    with patch.object(LLMRouter, "_get_model", return_value=fake_model):
        await router.generate("short prompt", model="gpt-4o-mini")

    assert fake_model.ainvoke.call_args[0][0][-1].content == "short prompt"


# ── generate(): caching ───────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deterministic_request_is_served_from_cache():
    cache = AsyncMock()
    cache.get.return_value = "CACHED"
    router = LLMRouter(cache=cache, settings=_root_settings(LLMSettings()))

    out = await router.generate("same", model="gpt-4o-mini", temperature=0.0)

    assert out == "CACHED"
    cache.get.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_deterministic_request_skips_cache_lookup():
    cache = AsyncMock()
    router = LLMRouter(cache=cache, settings=_root_settings(LLMSettings()))
    fake_model = AsyncMock()
    fake_model.ainvoke.return_value = _response("FRESH")

    with patch.object(LLMRouter, "_get_model", return_value=fake_model):
        out = await router.generate("same", model="gpt-4o-mini", temperature=0.9)

    assert out == "FRESH"
    cache.get.assert_not_awaited(), "Cache is only consulted at temperature 0"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_successful_deterministic_response_is_cached():
    cache = AsyncMock()
    cache.get.return_value = None
    router = LLMRouter(cache=cache, settings=_root_settings(LLMSettings()))
    fake_model = AsyncMock()
    fake_model.ainvoke.return_value = _response("STORED")

    with patch.object(LLMRouter, "_get_model", return_value=fake_model):
        await router.generate("q", system_prompt="s", model="gpt-4o-mini", temperature=0.0)

    cache.set.assert_awaited_once()
    args = cache.set.call_args[0]
    assert args[0] == "gpt-4o-mini"
    assert args[1] == "s||q", "Cache key combines system prompt and prompt"
    assert args[3] == "STORED"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_deterministic_response_is_not_cached():
    cache = AsyncMock()
    cache.get.return_value = None
    router = LLMRouter(cache=cache, settings=_root_settings(LLMSettings()))
    fake_model = AsyncMock()
    fake_model.ainvoke.return_value = _response("NOPE")

    with patch.object(LLMRouter, "_get_model", return_value=fake_model):
        await router.generate("q", model="gpt-4o-mini", temperature=0.7)

    cache.set.assert_not_awaited()


# ── generate(): fallback cascade ──────────────────────────────────────────────


def _settings_with_keys(**overrides) -> LLMSettings:
    """Build LLMSettings with real (non-placeholder) keys for every provider.

    Overrides replace the defaults rather than duplicating them, so a caller can
    downgrade any single provider to a placeholder or unset it entirely.
    """
    base: dict = {
        "nvidia_api_key": SecretStr("nv-real-key"),
        "nvidia_default_model": "nvidia/nemotron-mini",
        "zhipu_api_key": SecretStr("zhipu-real-key"),
        "zhipu_default_model": "glm-4-flash",
        "openai_api_key": SecretStr("sk-real-key"),
        "openai_default_model": "gpt-4o-mini",
    }
    base.update(overrides)
    return LLMSettings(**base)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_falls_back_to_next_provider_when_first_fails():
    s = _settings_with_keys()
    router = LLMRouter(settings=_root_settings(s))
    failing = AsyncMock()
    failing.ainvoke.side_effect = RuntimeError("provider down")
    working = AsyncMock()
    working.ainvoke.return_value = _response("RECOVERED")

    calls: dict[str, AsyncMock] = {
        "gpt-4o-mini": failing,
        "nvidia/nemotron-mini": working,
        "glm-4-flash": working,
    }

    with patch.object(LLMRouter, "_get_model", side_effect=lambda m, *a, **k: calls[m]):
        out = await router.generate("q", model="gpt-4o-mini")

    assert out == "RECOVERED"
    assert failing.ainvoke.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_candidates_failing_raises_llm_error():
    from searchops.core.exceptions.infrastructure import LLMError

    s = _settings_with_keys(
        nvidia_api_key=SecretStr("CHANGE_ME"),
        zhipu_api_key=SecretStr("CHANGE_ME"),
        openai_api_key=SecretStr("CHANGE_ME"),
    )
    router = LLMRouter(settings=_root_settings(s))

    failing = AsyncMock()
    failing.ainvoke.side_effect = RuntimeError("all down")

    with patch.object(LLMRouter, "_get_model", return_value=failing):
        with pytest.raises(LLMError):
            await router.generate("q", model="gpt-4o-mini")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_literal_placeholder_keys_are_excluded_from_candidates():
    """A key whose value is literally "CHANGE_ME" is skipped."""
    s = LLMSettings(
        nvidia_api_key=SecretStr("CHANGE_ME"),
        nvidia_default_model="nvidia/x",
        zhipu_api_key=SecretStr("CHANGE_ME"),
        zhipu_default_model="glm-x",
        openai_api_key=SecretStr("CHANGE_ME"),
        openai_default_model="gpt-x",
    )
    router = LLMRouter(settings=_root_settings(s))

    seen: list[str] = []

    def track(m, *a, **k):
        seen.append(m)
        model = AsyncMock()
        model.ainvoke.return_value = SimpleNamespace(content="OK", response_metadata={})
        return model

    with patch.object(LLMRouter, "_get_model", side_effect=track):
        await router.generate("q", model="only-model")

    assert seen == ["only-model"], "All placeholder-keyed providers are excluded"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unset_keys_are_excluded_from_candidates():
    """A provider with no key configured is never added to the cascade.

    The guard is `if s.<key> and "CHANGE_ME" not in ...`, so None short-circuits
    to falsy and the provider is skipped — the same outcome as a placeholder.
    """
    s = LLMSettings(
        nvidia_api_key=None,
        nvidia_default_model="nvidia/x",
        zhipu_api_key=None,
        zhipu_default_model="glm-x",
        openai_api_key=None,
        openai_default_model="gpt-x",
    )
    router = LLMRouter(settings=_root_settings(s))

    seen: list[str] = []

    def track(m, *a, **k):
        seen.append(m)
        model = AsyncMock()
        model.ainvoke.return_value = SimpleNamespace(content="OK", response_metadata={})
        return model

    with patch.object(LLMRouter, "_get_model", side_effect=track):
        await router.generate("q", model="only-model")

    assert seen == ["only-model"], "Unconfigured providers are correctly excluded"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_exceeded_is_swallowed_and_cascades():
    """BudgetExceededError must not abort the job — the next model is tried."""
    s = _settings_with_keys()
    router = LLMRouter(settings=_root_settings(s))
    working = AsyncMock()
    working.ainvoke.return_value = _response("OK")

    def fake_record(**kwargs):
        if kwargs["model_name"] == "gpt-4o-mini":
            raise BudgetExceededError("budget blown")
        return 0.0

    with (
        patch.object(LLMRouter, "_get_model", return_value=working),
        patch.object(router_mod.LLMBudgetTracker, "record_and_check", side_effect=fake_record),
    ):
        out = await router.generate("q", model="gpt-4o-mini")

    assert out == "OK"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_candidate_list_is_deduplicated_preserving_order():
    s = _settings_with_keys()  # openai_default_model already equals gpt-4o-mini
    router = LLMRouter(settings=_root_settings(s))

    seen: list[str] = []

    def fake_get_model(m, *a, **k):
        seen.append(m)
        model = AsyncMock()
        model.ainvoke.return_value = _response("OK")
        return model

    with patch.object(LLMRouter, "_get_model", side_effect=fake_get_model):
        await router.generate("q", model="gpt-4o-mini")

    assert seen == list(dict.fromkeys(seen)), "Duplicate candidates removed, order kept"
    assert seen[0] == "gpt-4o-mini"


# ── _get_model caching ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_model_is_memoized_per_temperature_and_max_tokens(settings):
    router = LLMRouter(settings=_root_settings(settings))
    sentinel = object()

    with patch.object(LLMRouter, "_get_model", wraps=router._get_model):
        with patch.object(router_mod, "ChatOpenAI", return_value=sentinel):
            a = router._get_model("gpt-4o-mini", 0.0, None)
            b = router._get_model("gpt-4o-mini", 0.0, None)

    assert a is b is sentinel


@pytest.mark.unit
def test_get_model_distinguishes_temperature(settings):
    router = LLMRouter(settings=_root_settings(settings))
    created = []

    def fake_factory(*a, **k):
        obj = MagicMock()
        created.append(obj)
        return obj

    with patch.object(router_mod, "ChatOpenAI", side_effect=fake_factory):
        router._get_model("gpt-4o-mini", 0.0, None)
        router._get_model("gpt-4o-mini", 0.9, None)

    assert len(created) == 2, "Different temperatures need different model instances"


@pytest.mark.unit
def test_get_model_applies_default_max_tokens():
    router = LLMRouter(settings=_root_settings(LLMSettings(default_max_tokens=4096)))

    with patch.object(router_mod, "ChatOpenAI") as factory:
        router._get_model("gpt-4o-mini", 0.0, None)

    assert factory.call_args.kwargs["max_tokens"] == 4096


@pytest.mark.unit
def test_get_model_honours_explicit_max_tokens():
    router = LLMRouter(settings=_root_settings(LLMSettings(default_max_tokens=4096)))

    with patch.object(router_mod, "ChatOpenAI") as factory:
        router._get_model("gpt-4o-mini", 0.0, 128)

    assert factory.call_args.kwargs["max_tokens"] == 128, "Explicit value wins over the default"
