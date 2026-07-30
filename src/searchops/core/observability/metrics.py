"""
Prometheus metrics registry for the SEARCHOps platform.

Design decisions:
- All metrics are defined here in one place (single source of truth)
- Metrics are lazily initialized with a registry to allow test isolation
- Follows Prometheus naming conventions: namespace_subsystem_unit_suffix
- Counter, Gauge, Histogram, and Summary types used appropriately

Consumers import metric objects directly:
    from searchops.core.observability.metrics import HTTP_REQUESTS_TOTAL
    HTTP_REQUESTS_TOTAL.labels(method="GET", path="/health", status="200").inc()
"""

from __future__ import annotations

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
)

# ── Registry ──────────────────────────────────────────────────────────────────
# Using the default REGISTRY for production. Tests can use a custom registry
# via the metrics_registry fixture.
_REGISTRY = REGISTRY

# ── HTTP metrics ──────────────────────────────────────────────────────────────

HTTP_REQUESTS_TOTAL = Counter(
    name="searchops_http_requests_total",
    documentation="Total number of HTTP requests received",
    labelnames=["method", "path", "status_code"],
    registry=_REGISTRY,
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    name="searchops_http_request_duration_seconds",
    documentation="HTTP request latency in seconds",
    labelnames=["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=_REGISTRY,
)

HTTP_REQUEST_SIZE_BYTES = Histogram(
    name="searchops_http_request_size_bytes",
    documentation="HTTP request body size in bytes",
    labelnames=["method", "path"],
    buckets=(64, 256, 1024, 4096, 16384, 65536, 262144, 1048576),
    registry=_REGISTRY,
)

HTTP_RESPONSE_SIZE_BYTES = Histogram(
    name="searchops_http_response_size_bytes",
    documentation="HTTP response body size in bytes",
    labelnames=["method", "path"],
    buckets=(64, 256, 1024, 4096, 16384, 65536, 262144, 1048576),
    registry=_REGISTRY,
)

HTTP_ACTIVE_REQUESTS = Gauge(
    name="searchops_http_active_requests",
    documentation="Number of currently active HTTP requests",
    labelnames=["method", "path"],
    registry=_REGISTRY,
)

# ── LLM metrics ───────────────────────────────────────────────────────────────

LLM_REQUESTS_TOTAL = Counter(
    name="searchops_llm_requests_total",
    documentation="Total LLM API requests made",
    labelnames=["provider", "model", "status"],
    registry=_REGISTRY,
)

LLM_TOKENS_TOTAL = Counter(
    name="searchops_llm_tokens_total",
    documentation="Total LLM tokens consumed",
    labelnames=["provider", "model", "token_type"],  # token_type: prompt|completion
    registry=_REGISTRY,
)

LLM_COST_USD_TOTAL = Counter(
    name="searchops_llm_cost_usd_total",
    documentation="Total LLM cost in USD",
    labelnames=["provider", "model"],
    registry=_REGISTRY,
)

LLM_REQUEST_DURATION_SECONDS = Histogram(
    name="searchops_llm_request_duration_seconds",
    documentation="LLM API request latency in seconds",
    labelnames=["provider", "model"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    registry=_REGISTRY,
)

LLM_ACTIVE_REQUESTS = Gauge(
    name="searchops_llm_active_requests",
    documentation="Number of currently active LLM API requests",
    labelnames=["provider", "model"],
    registry=_REGISTRY,
)

# ── Agent metrics ─────────────────────────────────────────────────────────────

AGENT_TASKS_TOTAL = Counter(
    name="searchops_agent_tasks_total",
    documentation="Total agent tasks executed",
    labelnames=["agent_id", "capability", "status"],
    registry=_REGISTRY,
)

AGENT_TASK_DURATION_SECONDS = Histogram(
    name="searchops_agent_task_duration_seconds",
    documentation="Agent task execution time in seconds",
    labelnames=["agent_id", "capability"],
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
    registry=_REGISTRY,
)

AGENT_ACTIVE_TASKS = Gauge(
    name="searchops_agent_active_tasks",
    documentation="Number of currently executing agent tasks",
    labelnames=["agent_id"],
    registry=_REGISTRY,
)

# ── Graph execution metrics ───────────────────────────────────────────────────

GRAPH_EXECUTIONS_TOTAL = Counter(
    name="searchops_graph_executions_total",
    documentation="Total LangGraph graph executions",
    labelnames=["graph_name", "status"],
    registry=_REGISTRY,
)

GRAPH_EXECUTION_DURATION_SECONDS = Histogram(
    name="searchops_graph_execution_duration_seconds",
    documentation="LangGraph execution time in seconds",
    labelnames=["graph_name"],
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
    registry=_REGISTRY,
)

GRAPH_NODE_EXECUTIONS_TOTAL = Counter(
    name="searchops_graph_node_executions_total",
    documentation="Total graph node executions",
    labelnames=["graph_name", "node_name", "status"],
    registry=_REGISTRY,
)

# ── Scraping metrics ──────────────────────────────────────────────────────────

SCRAPING_REQUESTS_TOTAL = Counter(
    name="searchops_scraping_requests_total",
    documentation="Total scraping requests made",
    labelnames=["mode", "status"],  # mode: firecrawl|playwright|http
    registry=_REGISTRY,
)

SCRAPING_CACHE_HITS_TOTAL = Counter(
    name="searchops_scraping_cache_hits_total",
    documentation="Total scraping cache hits",
    registry=_REGISTRY,
)

SCRAPING_DURATION_SECONDS = Histogram(
    name="searchops_scraping_duration_seconds",
    documentation="Scraping request duration in seconds",
    labelnames=["mode"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    registry=_REGISTRY,
)

SCRAPING_CONTENT_BYTES = Histogram(
    name="searchops_scraping_content_bytes",
    documentation="Scraped content size in bytes",
    labelnames=["mode"],
    buckets=(1024, 10240, 102400, 1048576, 5242880),
    registry=_REGISTRY,
)

# ── Knowledge graph metrics ───────────────────────────────────────────────────

KG_ENTITIES_TOTAL = Gauge(
    name="searchops_kg_entities_total",
    documentation="Total entities in the knowledge graph",
    labelnames=["entity_type"],
    registry=_REGISTRY,
)

KG_RELATIONS_TOTAL = Gauge(
    name="searchops_kg_relations_total",
    documentation="Total relations in the knowledge graph",
    labelnames=["relation_type"],
    registry=_REGISTRY,
)

KG_OPERATIONS_TOTAL = Counter(
    name="searchops_kg_operations_total",
    documentation="Total knowledge graph operations",
    labelnames=["operation", "status"],
    registry=_REGISTRY,
)

# ── Cache metrics ─────────────────────────────────────────────────────────────

CACHE_OPERATIONS_TOTAL = Counter(
    name="searchops_cache_operations_total",
    documentation="Total cache operations",
    labelnames=["operation", "status"],  # operation: get|set|delete, status: hit|miss|error
    registry=_REGISTRY,
)

# ── Circuit breaker metrics ───────────────────────────────────────────────────

CIRCUIT_BREAKER_STATE = Gauge(
    name="searchops_circuit_breaker_state",
    documentation="Circuit breaker state (0=closed, 1=open, 2=half-open)",
    labelnames=["service"],
    registry=_REGISTRY,
)

# ── System information ────────────────────────────────────────────────────────

SERVICE_INFO = Info(
    name="searchops_service",
    documentation="SEARCHOps service information",
    registry=_REGISTRY,
)
