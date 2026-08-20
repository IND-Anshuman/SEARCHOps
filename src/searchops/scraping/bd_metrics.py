"""
Bright Data Prometheus Metrics Registry.

Centralised metric definitions for all Bright Data premium-tier operations.
Import and use these counters/histograms/gauges in BD scrapers, spend guard,
circuit breakers, and the scraping pipeline.

Label cardinality rules:
  - tier:    bounded enum (serp / unlocker / browser / dataset)
  - outcome: bounded enum (success / failure / rejected / circuit_open)
  - scope:   bounded enum (job / agent / hour / day)
  - trigger_status: bounded set of HTTP codes (403 / 429 / 503 / 500 / 0)
  - NO raw URLs, NO query strings, NO tenant IDs (too high cardinality)
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── Request Counters ──────────────────────────────────────────────────────────

BD_REQUESTS = Counter(
    "bd_requests_total",
    "Total Bright Data API/proxy requests",
    ["tier", "outcome"],
    # tier: serp | unlocker | browser | dataset
    # outcome: success | failure | rejected | circuit_open
)

BD_FALLBACK_ACTIVATIONS = Counter(
    "bd_tier_fallback_total",
    "Number of times a BD tier was activated as fallback from a lower tier",
    ["tier", "trigger_status"],
    # trigger_status: HTTP code that caused escalation (e.g. '403', '429', '503')
)

# ── Latency Histograms ────────────────────────────────────────────────────────

BD_LATENCY = Histogram(
    "bd_request_latency_seconds",
    "End-to-end Bright Data request latency in seconds",
    ["tier"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)

BD_BROWSER_POOL_WAIT = Histogram(
    "bd_browser_pool_wait_seconds",
    "Time spent waiting to acquire a slot from the BD CDP connection pool",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0, 10.0, 30.0],
)

# ── Cost / Spend ──────────────────────────────────────────────────────────────

BD_SPEND_USD = Counter(
    "bd_spend_usd_total",
    "Estimated Bright Data spend in USD (based on list pricing)",
    ["tier"],
)

BD_BUDGET_REJECTIONS = Counter(
    "bd_budget_rejections_total",
    "Number of BD calls rejected by the spend guard before executing",
    ["scope", "tier"],
    # scope: job | agent | hour | day
)

# ── Circuit Breaker ───────────────────────────────────────────────────────────

BD_CIRCUIT_TRIPS = Counter(
    "bd_circuit_open_total",
    "Number of times a BD circuit breaker transitioned to OPEN state",
    ["tier"],
)

BD_CIRCUIT_RECOVERIES = Counter(
    "bd_circuit_closed_total",
    "Number of times a BD circuit breaker recovered back to CLOSED state",
    ["tier"],
)

# ── Resource Gauges ───────────────────────────────────────────────────────────

BD_BROWSER_ACTIVE_CONNECTIONS = Gauge(
    "bd_browser_active_connections",
    "Current number of active Bright Data CDP browser sessions",
)

BD_BROWSER_POOL_SIZE = Gauge(
    "bd_browser_pool_size",
    "Current size of the BD CDP browser connection pool",
)

# ── PAA (People Also Ask) ─────────────────────────────────────────────────────

BD_PAA_SUBQUERIES = Counter(
    "bd_serp_paa_subqueries_total",
    "Total PAA sub-queries extracted from BD SERP responses",
)

BD_PAA_DEPTH_REJECTIONS = Counter(
    "bd_paa_depth_rejections_total",
    "Number of times PAA expansion was blocked by depth guard",
)

# ── Health Check ──────────────────────────────────────────────────────────────

BD_HEALTH_CHECK_FAILURES = Counter(
    "bd_health_check_failures_total",
    "Number of failed BD health checks",
    ["tier"],
)


# ── Helper functions ──────────────────────────────────────────────────────────

def record_bd_request(
    tier: str,
    outcome: str,
    latency_seconds: float,
    cost_usd: float = 0.0,
    trigger_status: str | None = None,
) -> None:
    """
    Convenience function to record a BD request in all relevant metrics.

    Args:
        tier: BD product tier (serp/unlocker/browser/dataset).
        outcome: Result outcome (success/failure/rejected/circuit_open).
        latency_seconds: Request wall-clock time.
        cost_usd: Estimated cost of this request.
        trigger_status: HTTP status code that triggered this tier activation.
    """
    BD_REQUESTS.labels(tier=tier, outcome=outcome).inc()
    BD_LATENCY.labels(tier=tier).observe(latency_seconds)
    if cost_usd > 0:
        BD_SPEND_USD.labels(tier=tier).inc(cost_usd)
    if trigger_status is not None:
        BD_FALLBACK_ACTIVATIONS.labels(
            tier=tier, trigger_status=str(trigger_status)
        ).inc()
