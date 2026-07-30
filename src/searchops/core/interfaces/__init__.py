"""Core interface definitions (ports) following Hexagonal Architecture.

All external dependencies implement these interfaces.
Domain and application layers depend ONLY on these abstractions.
"""

from searchops.core.interfaces.repository import IRepository, IReadRepository
from searchops.core.interfaces.service import IService
from searchops.core.interfaces.agent import IAgent, AgentCapability, AgentCard
from searchops.core.interfaces.memory import IMemoryStore, ICache
from searchops.core.interfaces.event_bus import IEventBus, IEventHandler
from searchops.core.interfaces.health import IHealthCheck, HealthStatus, HealthCheckResult
from searchops.core.interfaces.scraper import IScraper, ScrapeRequest, ScrapeResult

__all__ = [
    "IRepository", "IReadRepository",
    "IService",
    "IAgent", "AgentCapability", "AgentCard",
    "IMemoryStore", "ICache",
    "IEventBus", "IEventHandler",
    "IHealthCheck", "HealthStatus", "HealthCheckResult",
    "IScraper", "ScrapeRequest", "ScrapeResult",
]
