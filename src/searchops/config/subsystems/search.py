"""Search subsystem configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, SecretStr


class SearchSettings(BaseSettings):
    """Search configuration settings."""

    tavily_api_key: SecretStr | None = Field(default=None, alias="TAVILY_API_KEY")
    serper_api_key: SecretStr | None = Field(default=None, alias="SERPER_API_KEY")
    brave_search_api_key: SecretStr | None = Field(default=None, alias="BRAVE_SEARCH_API_KEY")
    bing_search_api_key: SecretStr | None = Field(default=None, alias="BING_SEARCH_API_KEY")
    github_token: SecretStr | None = Field(default=None, alias="GITHUB_TOKEN")
    brightdata_api_key: SecretStr | None = Field(default=None, alias="BRIGHTDATA_API_KEY")
    brightdata_zone_serp: str = Field(default="serp", alias="BRIGHTDATA_ZONE_SERP")
    default_provider: str = "tavily"
    max_results_per_query: int = 10
    search_timeout: int = 30
    cache_ttl: int = 900
    searxng_base_url: str = Field(default="http://localhost:8080", alias="SEARXNG_BASE_URL")
    searxng_verify_ssl: bool = Field(default=True, alias="SEARXNG_VERIFY_SSL")
    playwright_search_engine: str = Field(default="duckduckgo", alias="PLAYWRIGHT_SEARCH_ENGINE")

    # ── PAA (People Also Ask) governance ────────────────────────────────────
    max_paa_depth: int = Field(
        default=3,
        alias="SEARCH_MAX_PAA_DEPTH",
        description=(
            "Maximum recursion depth for PAA sub-query expansion. "
            "Prevents autonomous agents from entering unbounded PAA expansion loops."
        ),
    )
    max_paa_per_serp: int = Field(
        default=5,
        alias="SEARCH_MAX_PAA_PER_SERP",
        description="Maximum PAA sub-queries to extract per SERP response.",
    )

    model_config = SettingsConfigDict(frozen=True, populate_by_name=True)

    @field_validator("default_provider")
    @classmethod
    def validate_default_provider(cls, v: str) -> str:
        if v not in {"tavily", "serper", "brave", "bing", "searxng", "playwright", "brightdata_serp"}:
            raise ValueError("default_provider must be one of: tavily, serper, brave, bing, searxng, playwright, brightdata_serp")
        return v

    @field_validator("max_results_per_query")
    @classmethod
    def validate_max_results_per_query(cls, v: int) -> int:
        if not (1 <= v <= 100):
            raise ValueError("max_results_per_query must be between 1 and 100")
        return v
