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
    default_provider: str = "tavily"
    max_results_per_query: int = 10
    search_timeout: int = 30
    cache_ttl: int = 900
    arxiv_max_results: int = 20

    model_config = SettingsConfigDict(frozen=True, populate_by_name=True)

    @field_validator("default_provider")
    @classmethod
    def validate_default_provider(cls, v: str) -> str:
        if v not in {"tavily", "serper", "brave", "bing"}:
            raise ValueError("default_provider must be one of: tavily, serper, brave, bing")
        return v

    @field_validator("max_results_per_query")
    @classmethod
    def validate_max_results_per_query(cls, v: int) -> int:
        if not (1 <= v <= 100):
            raise ValueError("max_results_per_query must be between 1 and 100")
        return v
