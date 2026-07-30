"""
System-wide constants.

All magic values must live here. Never use bare numbers/strings in application code.
"""
from __future__ import annotations

# ─── General ─────────────────────────────────────────────────────────────────

PLATFORM_VERSION: str = "0.1.0"
PLATFORM_NAME: str = "SEARCHOps"
DEFAULT_ENCODING: str = "utf-8"

# ─── Pagination ───────────────────────────────────────────────────────────

DEFAULT_PAGE_SIZE: int = 20
MAX_PAGE_SIZE: int = 100

# ─── Networking ──────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_SECONDS: float = 30.0
CONNECT_TIMEOUT_SECONDS: float = 5.0
READ_TIMEOUT_SECONDS: float = 30.0
WRITE_TIMEOUT_SECONDS: float = 10.0
POOL_TIMEOUT_SECONDS: float = 5.0

# ─── Retry ──────────────────────────────────────────────────────────────────

MAX_RETRIES: int = 3
RETRY_MIN_WAIT_SECONDS: float = 1.0
RETRY_MAX_WAIT_SECONDS: float = 30.0
RETRY_MULTIPLIER: float = 2.0

# ─── Content limits ──────────────────────────────────────────────────────────

MAX_CONTENT_LENGTH: int = 10 * 1024 * 1024   # 10 MB
MAX_SCRAPING_CONTENT_LENGTH: int = 5 * 1024 * 1024  # 5 MB
MAX_PROMPT_LENGTH: int = 100_000             # characters
MAX_REPORT_LENGTH: int = 500_000            # characters

# ─── Cache key prefixes ─────────────────────────────────────────────────────────

CACHE_PREFIX_SCRAPING: str = "scrape:"
CACHE_PREFIX_LLM: str = "llm:"
CACHE_PREFIX_SEARCH: str = "search:"
CACHE_PREFIX_RATE_LIMIT: str = "rl:"
CACHE_PREFIX_SESSION: str = "session:"
CACHE_PREFIX_EXECUTION: str = "exec:"

# ─── Event topics ─────────────────────────────────────────────────────────────

TOPIC_RESEARCH_EVENTS: str = "research.events"
TOPIC_AGENT_EVENTS: str = "agent.events"
TOPIC_KG_EVENTS: str = "kg.events"
TOPIC_SYSTEM_EVENTS: str = "system.events"
TOPIC_NOTIFICATION_EVENTS: str = "notification.events"

# ─── HTTP headers ────────────────────────────────────────────────────────────

HEADER_REQUEST_ID: str = "X-Request-ID"
HEADER_CORRELATION_ID: str = "X-Correlation-ID"
HEADER_TRACE_ID: str = "X-Trace-ID"
HEADER_API_VERSION: str = "X-API-Version"

# ─── Agent defaults ───────────────────────────────────────────────────────────

DEFAULT_AGENT_TIMEOUT_SECONDS: float = 300.0
DEFAULT_AGENT_MAX_RECURSION: int = 25
DEFAULT_AGENT_HEARTBEAT_SECONDS: float = 30.0

# ─── Vector store ───────────────────────────────────────────────────────────

DEFAULT_EMBEDDING_DIMENSION: int = 3072    # text-embedding-3-large
DEFAULT_VECTOR_TOP_K: int = 10
DEFAULT_SIMILARITY_THRESHOLD: float = 0.75

# ─── Knowledge graph ──────────────────────────────────────────────────────────

KG_DEFAULT_CONFIDENCE: float = 0.5
KG_MIN_CONFIDENCE: float = 0.3
KG_MAX_HOPS: int = 3
