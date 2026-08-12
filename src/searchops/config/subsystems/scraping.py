"""Scraping subsystem configuration."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScrapingSettings(BaseSettings):
    """Scraping configuration settings."""

    # ── Firecrawl ──────────────────────────────────────────────────────────
    firecrawl_api_key: SecretStr | None = Field(default=None, alias="FIRECRAWL_API_KEY")
    firecrawl_api_url: str = "https://api.firecrawl.dev"

    # ── HTTP behaviour ────────────────────────────────────────────────────
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

    # ── Phase 2: Stealth transport (curl_cffi + optional proxy) ───────────
    stealth_enabled: bool = Field(
        default=True,
        alias="SCRAPING_STEALTH_ENABLED",
        description="Enable curl_cffi JA4 TLS impersonation as Tier 0 scraper.",
    )
    stealth_impersonate: str = Field(
        default="chrome124",
        alias="SCRAPING_STEALTH_IMPERSONATE",
        description="curl_cffi impersonation target. Options: chrome124, firefox133, safari18.",
    )
    proxy_enabled: bool = Field(
        default=False,
        alias="SCRAPING_PROXY_ENABLED",
        description="When True, Tier 0b stealth scraper routes through proxy_url_tier1.",
    )
    proxy_url_tier1: SecretStr | None = Field(
        default=None,
        alias="PROXY_URL_TIER1",
        description=(
            "DataImpulse rotating residential proxy ($1/GB). "
            "Format: http://user:pass@gate.dc.dataimpulse.com:823"
        ),
    )
    proxy_url_tier2: SecretStr | None = Field(
        default=None,
        alias="PROXY_URL_TIER2",
        description=(
            "Decodo/Smartproxy residential proxy ($2/GB) for high-security targets. "
            "Format: http://user:pass@gate.smartproxy.com:10000"
        ),
    )
    proxy_connect_timeout: float = Field(
        default=10.0,
        alias="SCRAPING_PROXY_CONNECT_TIMEOUT",
        description="Connection timeout (seconds) when routing through proxy.",
    )

    model_config = SettingsConfigDict(
        env_prefix="SCRAPING_",
        frozen=True,
        populate_by_name=True,
        # Allow alias-based lookups without prefix (e.g. PROXY_URL_TIER1)
        env_ignore_empty=True,
    )
