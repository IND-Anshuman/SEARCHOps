"""Scraping subsystem configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


class ScrapingSettings(BaseSettings):
    """Scraping configuration settings."""

    firecrawl_api_key: SecretStr | None = Field(default=None, alias="FIRECRAWL_API_KEY")
    firecrawl_api_url: str = "https://api.firecrawl.dev"
    request_timeout: int = 30
    max_retries: int = 3
    retry_min_wait: float = 1.0
    retry_max_wait: float = 10.0
    cache_ttl: int = 3600
    rate_limit_rps: float = 5.0
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout: int = 60
    max_content_length_bytes: int = 5_000_000
    chunk_size: int = 2000
    chunk_overlap: int = 200
    allowed_domains: list[str] = []

    model_config = SettingsConfigDict(env_prefix="SCRAPING_", frozen=True, populate_by_name=True)
