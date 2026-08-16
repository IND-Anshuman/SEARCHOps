"""
Domain-Aware Rate Limiter with Adaptive Backoff.

Provides per-domain rate limiting with sliding window algorithm and
dynamic exponential backoff on HTTP 429/403 responses.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class DomainConfig:
    """Per-domain rate limit configuration."""

    requests_per_window: int = 10
    """Maximum requests per time window."""

    window_seconds: float = 60.0
    """Time window in seconds."""

    backoff_base_seconds: float = 1.0
    """Base backoff time for retries."""

    backoff_max_seconds: float = 60.0
    """Maximum backoff time."""

    backoff_multiplier: float = 2.0
    """Exponential backoff multiplier."""

    backoff_jitter: float = 0.1
    """Random jitter factor (0.1 = 10%)."""

    recovery_timeout_seconds: float = 300.0
    """Time to wait before retrying a blocked domain."""

    # Default domain configs for common sites - initialized in __post_init__
    default_config: dict[str, "DomainConfig"] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Initialize default configs after dataclass initialization."""
        pass  # Default configs set in DomainRateLimiter instead


@dataclass
class DomainState:
    """State tracking for a domain."""

    requests: list[float] = field(default_factory=list)
    """Timestamps of recent requests."""

    consecutive_failures: int = 0
    """Number of consecutive 429/403 errors."""

    is_blocked: bool = False
    """Whether domain is currently blocked."""

    blocked_until: float = 0.0
    """Unix timestamp when block expires."""

    last_request_time: float = 0.0
    """Timestamp of last request to this domain."""

    current_backoff: float = 1.0
    """Current backoff interval."""

    retry_after_seconds: int = 0
    """Server-suggested retry-after time."""


class DomainRateLimiter:
    """
    Domain-aware rate limiter with adaptive backoff.

    Features:
    - Per-domain sliding window rate limiting
    - Dynamic exponential backoff on 429/403 responses
    - Automatic recovery after blocked periods
    - Respect for Retry-After headers

    Example:
        limiter = DomainRateLimiter()

        # Check if request is allowed
        can_proceed, wait_time = await limiter.check("example.com")
        if not can_proceed:
            await asyncio.sleep(wait_time)

        # Report response for backoff adjustment
        await limiter.record_response("example.com", status_code=200)
    """

    def __init__(self, config: DomainConfig | None = None) -> None:
        self.config = config or DomainConfig()
        # Ensure default configs are initialized
        if not self.config.default_config:
            self.config.default_config = {
                "default": DomainConfig(requests_per_window=10, window_seconds=60.0),
                "strict": DomainConfig(requests_per_window=2, window_seconds=60.0),
                "relaxed": DomainConfig(requests_per_window=30, window_seconds=60.0),
                "api": DomainConfig(requests_per_window=100, window_seconds=60.0),
            }
        self._domains: dict[str, DomainState] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None

    async def check(self, domain: str) -> tuple[bool, float]:
        """
        Check if a request to the domain is allowed.

        Args:
            domain: The domain to check (e.g., "example.com")

        Returns:
            Tuple of (allowed: bool, wait_time: float)
            - allowed: True if request can proceed
            - wait_time: Seconds to wait if not allowed (0 if allowed)
        """
        async with self._lock:
            state = self._get_or_create_domain(domain)
            now = time.time()

            # Check if domain is blocked
            if state.is_blocked:
                if now < state.blocked_until:
                    wait_time = state.blocked_until - now
                    log.debug(
                        "Domain blocked, waiting",
                        domain=domain,
                        wait_seconds=wait_time,
                    )
                    return False, wait_time
                else:
                    # Block expired, reset state
                    state.is_blocked = False
                    state.consecutive_failures = 0
                    state.current_backoff = self.config.backoff_base_seconds

            # Get domain-specific config
            domain_config = self._get_domain_config(domain)

            # Clean old requests outside window
            window_start = now - domain_config.window_seconds
            state.requests = [t for t in state.requests if t > window_start]

            # Check rate limit
            if len(state.requests) >= domain_config.requests_per_window:
                oldest_request = min(state.requests)
                wait_time = domain_config.window_seconds - (now - oldest_request)
                log.debug(
                    "Rate limit reached",
                    domain=domain,
                    requests=len(state.requests),
                    limit=domain_config.requests_per_window,
                    wait_seconds=wait_time,
                )
                return False, max(0.1, wait_time)

            # Allow request
            state.requests.append(now)
            state.last_request_time = now
            return True, 0.0

    async def record_response(
        self,
        domain: str,
        status_code: int,
        retry_after: int | None = None,
    ) -> None:
        """
        Record the response to adjust backoff.

        Args:
            domain: The domain that was requested
            status_code: HTTP status code of response
            retry_after: Optional Retry-After header value
        """
        async with self._lock:
            state = self._domains.get(domain)
            if not state:
                return

            if status_code == 429 or status_code == 403:
                # Rate limited or forbidden
                state.consecutive_failures += 1

                # Use server-suggested retry-after if available
                if retry_after:
                    state.retry_after_seconds = retry_after
                    state.blocked_until = time.time() + retry_after
                    state.is_blocked = True
                    log.warning(
                        "Domain rate limited (server-suggested)",
                        domain=domain,
                        retry_after=retry_after,
                    )
                else:
                    # Calculate exponential backoff
                    backoff = state.current_backoff * (self.config.backoff_multiplier ** (state.consecutive_failures - 1))
                    backoff = min(backoff, self.config.backoff_max_seconds)

                    # Add jitter
                    jitter = backoff * self.config.backoff_jitter * random.uniform(-1, 1)
                    backoff = max(0.1, backoff + jitter)

                    state.current_backoff = backoff
                    state.blocked_until = time.time() + backoff
                    state.is_blocked = True

                    log.warning(
                        "Domain rate limited (adaptive backoff)",
                        domain=domain,
                        failures=state.consecutive_failures,
                        backoff_seconds=backoff,
                    )

            elif 200 <= status_code < 400:
                # Success - reset failure count and reduce backoff
                if state.consecutive_failures > 0:
                    state.consecutive_failures = max(0, state.consecutive_failures - 1)

                # Reduce backoff on success
                if state.current_backoff > self.config.backoff_base_seconds:
                    state.current_backoff = max(
                        self.config.backoff_base_seconds,
                        state.current_backoff / self.config.backoff_multiplier,
                    )

                # Clear block if we had success after failures
                if state.is_blocked and state.consecutive_failures == 0:
                    state.is_blocked = False

    def _get_or_create_domain(self, domain: str) -> DomainState:
        """Get or create domain state."""
        if domain not in self._domains:
            self._domains[domain] = DomainState()
        return self._domains[domain]

    def _get_domain_config(self, domain: str) -> DomainConfig:
        """Get configuration for a domain."""
        # Check for exact match
        if domain in self.config.default_config:
            return self.config.default_config[domain]

        # Check for suffix match (e.g., "api.example.com" matches "example.com")
        for suffix in self.config.default_config:
            if domain.endswith(suffix) and suffix != "default":
                return self.config.default_config[suffix]

        # Default configuration
        return self.config.default_config.get("default", DomainConfig())

    def set_domain_config(self, domain: str, config: DomainConfig) -> None:
        """Set custom configuration for a domain."""
        self.config.default_config[domain] = config

    def get_stats(self, domain: str) -> dict[str, Any]:
        """Get statistics for a domain."""
        state = self._domains.get(domain)
        if not state:
            return {"domain": domain, "status": "no_requests"}

        now = time.time()
        return {
            "domain": domain,
            "is_blocked": state.is_blocked,
            "blocked_until": state.blocked_until - now if state.is_blocked else 0,
            "consecutive_failures": state.consecutive_failures,
            "current_backoff": state.current_backoff,
            "recent_requests": len(state.requests),
            "last_request_ago": now - state.last_request_time if state.last_request_time else 0,
        }

    def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """Get statistics for all domains."""
        return {domain: self.get_stats(domain) for domain in self._domains}

    async def unblock_domain(self, domain: str) -> None:
        """Manually unblock a domain."""
        async with self._lock:
            if domain in self._domains:
                self._domains[domain].is_blocked = False
                self._domains[domain].consecutive_failures = 0
                log.info("Domain manually unblocked", domain=domain)

    async def cleanup_old_domains(self, max_age_seconds: float = 3600) -> None:
        """Remove domains with no recent activity."""
        now = time.time()
        async with self._lock:
            to_remove = []
            for domain, state in self._domains.items():
                if state.last_request_time > 0 and (now - state.last_request_time) > max_age_seconds:
                    if not state.is_blocked and state.consecutive_failures == 0:
                        to_remove.append(domain)

            for domain in to_remove:
                del self._domains[domain]
                log.debug("Cleaned up inactive domain", domain=domain)


# Global rate limiter instance
_rate_limiter: DomainRateLimiter | None = None


def get_rate_limiter(config: DomainConfig | None = None) -> DomainRateLimiter:
    """Get or create the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = DomainRateLimiter(config)
    return _rate_limiter