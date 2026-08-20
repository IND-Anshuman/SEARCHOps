"""
Declarative Scrape Tier Architecture.

Replaces the fractional tier numbering (1.5, 2.5) with an explicit,
integer-priority ordered registry of ScrapeTier objects.

Design:
  - ScrapeTier is a pure dataclass — no business logic
  - ScrapingPipeline iterates list[ScrapeTier] sorted by priority
  - Adding a new tier requires NO changes to pipeline.execute()
  - FailureClass drives smart fallback: premium tiers only on ACCESS failures

Tier priorities (integer, no fractions):
  0 — StealthHTTP (curl_cffi JA4)
  1 — ProxyRouter (residential proxy)
  2 — Crawl4AI (BM25+entropy pruning)
  3 — PooledPlaywright (full JS)
  4 — BrightDataUnlocker ★ PREMIUM (ACCESS failures only)
  5 — Firecrawl (managed API)
  6 — BrightDataBrowser ★ PREMIUM (ACCESS failures only)
  7 — BasicHTTP (last resort)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from searchops.core.interfaces.scraper import IScraper


class FailureClass(str, Enum):
    """Classification of why a scraping tier failed.

    Used to determine whether escalating to a premium tier is appropriate.
    Premium tiers (BD Unlocker, BD Browser) must only activate on ACCESS
    failures, not on content failures (404) or network infrastructure issues.
    """

    ACCESS = "access"
    """403 / 429 / 503 — Bot/CAPTCHA block. Premium bypass is appropriate."""

    CONTENT = "content"
    """404 / 410 — Content genuinely does not exist. Do NOT invoke premium."""

    NETWORK = "network"
    """Timeout / connection reset / DNS failure. Use retry/CB policy."""

    UNSUPPORTED = "unsupported"
    """Binary / video / oversized content. Route via content policy."""

    UNKNOWN = "unknown"
    """Catch-all for unexpected status codes."""


# HTTP status → FailureClass mapping
_STATUS_TO_FAILURE: dict[int, FailureClass] = {
    400: FailureClass.UNKNOWN,
    401: FailureClass.ACCESS,
    403: FailureClass.ACCESS,
    404: FailureClass.CONTENT,
    405: FailureClass.UNKNOWN,
    408: FailureClass.NETWORK,
    410: FailureClass.CONTENT,
    429: FailureClass.ACCESS,
    500: FailureClass.NETWORK,
    502: FailureClass.NETWORK,
    503: FailureClass.ACCESS,   # Usually bot/CAPTCHA detection
    504: FailureClass.NETWORK,
}


def classify_failure(status_code: int) -> FailureClass:
    """
    Classify a failed HTTP status code into a FailureClass.

    Args:
        status_code: HTTP status code returned by a scraping tier.

    Returns:
        FailureClass indicating the nature of the failure.

    Examples:
        >>> classify_failure(403)
        FailureClass.ACCESS
        >>> classify_failure(404)
        FailureClass.CONTENT
        >>> classify_failure(500)
        FailureClass.NETWORK
    """
    if status_code == 0:
        return FailureClass.NETWORK  # Connection-level failure
    if 200 <= status_code < 300:
        return FailureClass.UNKNOWN  # Should not happen — success codes
    return _STATUS_TO_FAILURE.get(status_code, FailureClass.UNKNOWN)


def is_premium_eligible(status_code: int) -> bool:
    """
    Return True if a status code warrants escalating to a premium BD tier.

    Only ACCESS failures (403/429/503) should trigger premium bypasses.
    Content failures (404) and network failures should not waste BD credits.

    Args:
        status_code: HTTP status code from the previous tier.

    Returns:
        True if premium escalation is appropriate.
    """
    return classify_failure(status_code) == FailureClass.ACCESS


@dataclass
class ScrapeTier:
    """
    Declarative specification for a single scraping tier.

    The ScrapingPipeline iterates a sorted list[ScrapeTier] and evaluates
    each tier's eligibility based on:
      1. feature_flag: whether the tier is enabled
      2. trigger_on: whether the previous failure class warrants escalation
      3. is_premium: whether budget/circuit-breaker checks are required

    Attributes:
        priority:       Integer ordering. Lower = tried first. No fractions.
        name:           Human-readable identifier used in logs and metrics.
        is_premium:     If True, requires spend-guard + circuit-breaker checks.
        trigger_on:     Set of FailureClass values that allow this tier to run.
                        Empty frozenset means "always eligible after previous fails".
        feature_flag:   Feature flag name to check. None = always enabled.
        timeout_seconds: Tier-specific timeout budget in seconds.
        cost_usd:        Estimated cost per successful scrape (for spend guard).
    """

    priority: int
    name: str
    is_premium: bool = False
    trigger_on: frozenset[FailureClass] = field(
        default_factory=lambda: frozenset()
    )
    feature_flag: str | None = None
    timeout_seconds: float = 30.0
    cost_usd: float = 0.0

    # Injected at construction time by ScrapingPipeline
    scraper: "IScraper | None" = field(default=None, repr=False)

    def is_eligible(
        self,
        last_failure_class: FailureClass | None,
        flag_snapshot: "dict[str, bool]",
    ) -> bool:
        """
        Check if this tier is eligible to run given the current pipeline state.

        Args:
            last_failure_class: The FailureClass of the previous tier's result,
                                or None if no prior tier has run.
            flag_snapshot: Pre-evaluated feature flag dict (from snapshot()).

        Returns:
            True if this tier should be attempted.
        """
        # Check feature flag first
        if self.feature_flag is not None:
            if not flag_snapshot.get(self.feature_flag, False):
                return False

        # Check scraper is available
        if self.scraper is None:
            return False

        # If no trigger_on constraints, tier is eligible if previous failed
        if not self.trigger_on:
            return True

        # Otherwise, require the last failure to match one of our triggers
        return last_failure_class in self.trigger_on

    def __post_init__(self) -> None:
        """Validate tier configuration at construction time."""
        if self.priority < 0:
            raise ValueError(f"ScrapeTier priority must be >= 0, got {self.priority}")
        if not self.name:
            raise ValueError("ScrapeTier name must not be empty")


# ── Standard tier definitions ─────────────────────────────────────────────────
# These are templates — ScrapingPipeline injects the actual scraper instances.

TIER_DEFINITIONS: list[ScrapeTier] = [
    ScrapeTier(
        priority=0,
        name="stealth_http",
        feature_flag=None,   # Always enabled
        timeout_seconds=15.0,
        cost_usd=0.0,
    ),
    ScrapeTier(
        priority=1,
        name="proxy_router",
        feature_flag=None,   # Enabled if proxy_router is not None
        timeout_seconds=20.0,
        cost_usd=0.0,
    ),
    ScrapeTier(
        priority=2,
        name="crawl4ai",
        feature_flag="crawl4ai_enabled",
        timeout_seconds=30.0,
        cost_usd=0.0,
    ),
    ScrapeTier(
        priority=3,
        name="playwright",
        feature_flag="playwright_enabled",
        timeout_seconds=30.0,
        cost_usd=0.0,
    ),
    ScrapeTier(
        priority=4,
        name="bd_unlocker",
        is_premium=True,
        feature_flag="brightdata_unlocker_enabled",
        # Only activates on ACCESS failures (403/429/503)
        trigger_on=frozenset({FailureClass.ACCESS}),
        timeout_seconds=30.0,
        cost_usd=0.001,      # ~$1 / 1000 pages (BD list pricing)
    ),
    ScrapeTier(
        priority=5,
        name="firecrawl",
        feature_flag="firecrawl_enabled",
        timeout_seconds=60.0,
        cost_usd=0.0,
    ),
    ScrapeTier(
        priority=6,
        name="bd_browser",
        is_premium=True,
        feature_flag="brightdata_browser_enabled",
        # Only activates on ACCESS failures — not 404
        trigger_on=frozenset({FailureClass.ACCESS}),
        timeout_seconds=60.0,
        cost_usd=0.01,       # ~$10 / 1000 pages (BD Cloud Browser pricing)
    ),
    ScrapeTier(
        priority=7,
        name="basic_http",
        feature_flag=None,   # Last resort, always enabled
        timeout_seconds=15.0,
        cost_usd=0.0,
    ),
]
