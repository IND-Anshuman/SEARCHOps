"""
Scraper interface.
"""
from __future__ import annotations

import enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ScrapeMode(enum.StrEnum):
    """Scraping strategy — ordered from lightest to heaviest cost/latency."""

    FIRECRAWL = "firecrawl"      # Managed Firecrawl API ($$$, ~3–5s)
    PLAYWRIGHT = "playwright"     # Pooled headless browser (local, ~1.5s)
    HTTP = "http"                # Plain httpx (local, ~500ms, last resort)
    STEALTH_HTTP = "stealth_http" # curl_cffi BoringSSL JA4 bypass (local, ~150ms)
    CRAWL4AI = "crawl4ai"        # Local async AI crawler with BM25 pruning (~800ms)
    DOCLING_PDF = "docling_pdf"  # IBM Docling CPU layout transformer for PDFs
    AUTO = "auto"                # Let the pipeline decide based on target URL


class ScrapeRequest(BaseModel):
    """Request to scrape a URL."""
    
    url: str = Field(description="URL to scrape")
    mode: ScrapeMode = Field(default=ScrapeMode.AUTO)
    wait_for_selector: str | None = Field(
        default=None,
        description="CSS selector to wait for before capturing (Playwright only)",
    )
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    extract_markdown: bool = Field(default=True)
    extract_links: bool = Field(default=True)
    extract_metadata: bool = Field(default=True)
    take_screenshot: bool = Field(default=False)
    headers: dict[str, str] = Field(default_factory=dict)
    cache_ttl_seconds: int | None = Field(
        default=3600,
        description="Cache TTL. None disables caching for this request.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScrapeResult(BaseModel):
    """Result of a scraping operation."""
    
    url: str
    final_url: str = Field(description="URL after redirects")
    status_code: int
    markdown: str | None = None
    html: str | None = None
    title: str | None = None
    description: str | None = None
    links: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    screenshot_base64: str | None = None
    content_hash: str | None = None
    word_count: int = 0
    was_cached: bool = False
    scrape_mode_used: ScrapeMode = ScrapeMode.HTTP
    duration_ms: float = 0.0
    # Phase 4+: structured data extracted from tables / PDF documents
    dataframes_json: list[dict[str, Any]] | None = Field(
        default=None,
        description="List of {headers, rows, shape} dicts extracted from HTML/PDF tables.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class IScraper(Protocol):
    """Contract for all web scraping implementations."""
    
    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """Scrape a URL and return structured content."""
        ...
    
    async def scrape_many(
        self, requests: list[ScrapeRequest], *, max_concurrency: int = 5
    ) -> list[ScrapeResult]:
        """Scrape multiple URLs concurrently."""
        ...
    
    async def health_check(self) -> bool:
        """Return True if the scraper backend is operational."""
        ...
