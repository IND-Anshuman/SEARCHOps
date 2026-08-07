"""
LLM subsystem configuration — free-tier defaults with multi-provider integration.

Providers:
    openai    → gpt-4o-mini          (cheapest OpenAI model)
    anthropic → claude-haiku-4-5     (cheapest Claude)
    google    → gemini-2.0-flash     (free in AI Studio)
    nvidia    → nvidia/llama-3.1-nemotron-ultra-253b-v1  (free credits on NIM)
    bedrock   → amazon.nova-lite-v1:0 (lowest Bedrock cost)
    zhipu     → glm-4-flash          (completely free on Z.AI / Zhipu BigModel)
"""

from __future__ import annotations

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_PROVIDERS = frozenset(
    {"openai", "anthropic", "google", "nvidia", "bedrock", "zhipu"}
)


class LLMSettings(BaseSettings):
    """Unified LLM provider configuration — tuned for free-tier and multi-provider usage."""

    model_config = SettingsConfigDict(frozen=True, populate_by_name=True)

    # ── OpenAI ────────────────────────────────────────────────────────────
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_org_id: str | None = Field(default=None, alias="OPENAI_ORG_ID")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_default_model: str = "gpt-4o-mini"          # 15x cheaper than gpt-4o
    openai_embedding_model: str = "text-embedding-3-small"  # 5x cheaper than large

    # ── Anthropic ─────────────────────────────────────────────────────────
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_default_model: str = "claude-haiku-4-5"  # cheapest Claude

    # ── Google AI Studio (Gemini) ─────────────────────────────────────────
    google_api_key: SecretStr | None = Field(default=None, alias="GOOGLE_API_KEY")
    google_default_model: str = "gemini-2.0-flash"     # free tier in AI Studio

    # ── NVIDIA NIM ────────────────────────────────────────────────────────
    nvidia_api_key: SecretStr | None = Field(default=None, alias="NVIDIA_API_KEY")
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_default_model: str = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

    # ── Amazon Bedrock ────────────────────────────────────────────────────
    bedrock_aws_access_key_id: SecretStr | None = Field(
        default=None, alias="AWS_ACCESS_KEY_ID"
    )
    bedrock_aws_secret_access_key: SecretStr | None = Field(
        default=None, alias="AWS_SECRET_ACCESS_KEY"
    )
    bedrock_aws_region: str = Field(default="us-east-1", alias="AWS_DEFAULT_REGION")
    bedrock_default_model: str = "amazon.nova-lite-v1:0"  # lowest-cost Bedrock model

    # ── Zhipu AI / Z.AI (GLM) ────────────────────────────────────────────
    zhipu_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ZHIPU_API_KEY", "ZAI_API_KEY", "ZHIPUAI_API_KEY"
        ),
    )
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    zhipu_default_model: str = "glm-4-flash"           # 100% free on Z.AI

    # ── Global router settings — tightened for free tier ─────────────────
    default_provider: str = "google"                   # Gemini free tier is most generous
    default_temperature: float = 0.0                   # deterministic = cache hits
    default_max_tokens: int = 1_024                    # was 8192; capped hard
    max_prompt_chars: int = 32_000                     # chars fed to LLM per call (was 6,000)
    request_timeout: float = 60.0                      # shorter timeout
    max_retries: int = 2                               # fewer retries = fewer tokens

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("default_provider")
    @classmethod
    def validate_default_provider(cls, v: str) -> str:
        if v not in _VALID_PROVIDERS:
            raise ValueError(
                f"default_provider must be one of {sorted(_VALID_PROVIDERS)}"
            )
        return v

    @field_validator("default_temperature")
    @classmethod
    def validate_default_temperature(cls, v: float) -> float:
        if not (0.0 <= v <= 2.0):
            raise ValueError("default_temperature must be between 0.0 and 2.0")
        return v

    @field_validator("default_max_tokens")
    @classmethod
    def validate_default_max_tokens(cls, v: int) -> int:
        if not (1 <= v <= 200_000):
            raise ValueError("default_max_tokens must be between 1 and 200000")
        return v

    @field_validator("max_retries")
    @classmethod
    def validate_max_retries(cls, v: int) -> int:
        if not (0 <= v <= 10):
            raise ValueError("max_retries must be between 0 and 10")
        return v
