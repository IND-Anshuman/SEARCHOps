"""Observability package exports."""

from searchops.core.observability.tracer import get_tracer, setup_tracer_provider
from searchops.core.observability.metrics import (
    HTTP_REQUESTS_TOTAL,
    HTTP_REQUEST_DURATION_SECONDS,
    LLM_REQUESTS_TOTAL,
    LLM_TOKENS_TOTAL,
    LLM_COST_USD_TOTAL,
    AGENT_TASKS_TOTAL,
    GRAPH_EXECUTIONS_TOTAL,
    SCRAPING_REQUESTS_TOTAL,
)

__all__ = [
    "get_tracer",
    "setup_tracer_provider",
    "HTTP_REQUESTS_TOTAL",
    "HTTP_REQUEST_DURATION_SECONDS",
    "LLM_REQUESTS_TOTAL",
    "LLM_TOKENS_TOTAL",
    "LLM_COST_USD_TOTAL",
    "AGENT_TASKS_TOTAL",
    "GRAPH_EXECUTIONS_TOTAL",
    "SCRAPING_REQUESTS_TOTAL",
]
