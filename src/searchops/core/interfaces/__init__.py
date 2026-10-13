"""Core interface definitions (ports) following Hexagonal Architecture.

All external dependencies implement these interfaces.
Domain and application layers depend ONLY on these abstractions.
"""

from searchops.core.interfaces.agent import AgentCapability, AgentCard, IAgent
from searchops.core.interfaces.event_bus import IEventBus, IEventHandler
from searchops.core.interfaces.health import HealthCheckResult, HealthStatus, IHealthCheck
from searchops.core.interfaces.memory import ICache, IMemoryStore
from searchops.core.interfaces.repository import IReadRepository, IRepository
from searchops.core.interfaces.scraper import IScraper, ScrapeRequest, ScrapeResult
from searchops.core.interfaces.service import IService

__all__ = [
    "AgentCapability",
    "AgentCard",
    "HealthCheckResult",
    "HealthStatus",
    "IAgent",
    "ICache",
    "IEventBus",
    "IEventHandler",
    "IHealthCheck",
    "IMemoryStore",
    "IReadRepository",
    "IRepository",
    "IScraper",
    "IService",
    "ScrapeRequest",
    "ScrapeResult",
]
