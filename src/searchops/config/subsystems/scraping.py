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

    # ── Bright Data (Premium Tier) ────────────────────────────────────────
    brightdata_api_key: SecretStr | None = Field(
        default=None,
        alias="BRIGHTDATA_API_KEY",
        description="Bright Data API bearer token for Dataset and SERP APIs.",
    )
    brightdata_customer_id: str | None = Field(
        default=None,
        alias="BRIGHTDATA_CUSTOMER_ID",
        description="Bright Data customer ID (format: brd-customer-XXXXXX).",
    )
    brightdata_zone_unlocker: str = Field(
        default="unlocker",
        alias="BRIGHTDATA_ZONE_UNLOCKER",
        description="Bright Data zone name for Web Unlocker product.",
    )
    brightdata_zone_scraping_browser: str = Field(
        default="scraping_browser",
        alias="BRIGHTDATA_ZONE_SCRAPING_BROWSER",
        description="Bright Data zone name for Cloud Scraping Browser product.",
    )
    brightdata_zone_password: SecretStr | None = Field(
        default=None,
        alias="BRIGHTDATA_ZONE_PASSWORD",
        description="Zone password shared across Bright Data zones.",
    )

    # ── Bright Data Spend Governance ──────────────────────────────────────────
    bd_max_spend_per_job_usd: float = Field(
        default=0.50,
        alias="BD_MAX_SPEND_PER_JOB_USD",
        description="Hard spend limit per scraping job in USD. 0 = unlimited.",
    )
    bd_max_spend_per_agent_usd: float = Field(
        default=2.00,
        alias="BD_MAX_SPEND_PER_AGENT_USD",
        description="Hard spend limit per agent per day in USD. 0 = unlimited.",
    )
    bd_max_spend_per_hour_usd: float = Field(
        default=5.00,
        alias="BD_MAX_SPEND_PER_HOUR_USD",
        description="Platform-wide hourly spend limit in USD. 0 = unlimited.",
    )
    bd_max_spend_per_day_usd: float = Field(
        default=20.00,
        alias="BD_MAX_SPEND_PER_DAY_USD",
        description="Platform-wide daily spend limit in USD. 0 = unlimited.",
    )
    bd_budget_fail_closed: bool = Field(
        default=True,
        alias="BD_BUDGET_FAIL_CLOSED",
        description=(
            "If True, BD calls are rejected when Redis is unavailable. "
            "If False, calls are allowed through (fail-open). Default: True."
        ),
    )
    bd_cdp_pool_max_connections: int = Field(
        default=5,
        alias="BD_CDP_POOL_MAX_CONNECTIONS",
        description="Maximum concurrent BD Cloud Browser CDP connections in pool.",
    )

    model_config = SettingsConfigDict(
        env_prefix="SCRAPING_",
        frozen=True,
        populate_by_name=True,
        # Allow alias-based lookups without prefix (e.g. PROXY_URL_TIER1)
        env_ignore_empty=True,
    )
