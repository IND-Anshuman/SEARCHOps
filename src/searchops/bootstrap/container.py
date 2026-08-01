"""
Application DI Container.

Uses lagom for lightweight, type-safe dependency injection.
All singletons are registered here. Nothing else calls constructors directly.

Design:
- Singleton services (initialized once per process)
- Request-scoped services (initialized once per HTTP request) are handled via FastAPI Depends()
- Container is created in startup.py and passed to the lifespan
"""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import structlog

from searchops.config.settings import Settings, get_settings
from searchops.feature_flags.manager import FeatureFlagManager
from searchops.feature_flags.providers import EnvFeatureFlagProvider
from searchops.platform.registry.agent_registry import AgentRegistry
from searchops.platform.registry.capability_registry import CapabilityRegistry
from searchops.platform.registry.tool_registry import ToolRegistry

log = structlog.get_logger(__name__)


class ApplicationContainer:
    """Central DI container for the SEARCHOps platform.
    
    Holds all singleton services. Created once during startup and
    available for the lifetime of the process.
    
    Services added in later phases:
    - database: AsyncSession factory (Phase 2)
    - cache: RedisCache (Phase 2)
    - event_bus: RedisStreamsEventBus (Phase 2)
    - llm_router: LLMRouter (Phase 7)
    - knowledge_graph: Neo4jGraphRepository (Phase 8)
    """
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        
        # Feature flags
        self.feature_flags = FeatureFlagManager(
            providers=[EnvFeatureFlagProvider()]
        )
        
        # Platform registries
        self.agent_registry = AgentRegistry()
        self.tool_registry = ToolRegistry()
        self.capability_registry = CapabilityRegistry()
        
        log.info("ApplicationContainer created")
    
    @classmethod
    def create(cls) -> ApplicationContainer:
        """Factory method. Creates container from environment settings."""
        settings = get_settings()
        return cls(settings=settings)


# Module-level singleton (initialized during startup, never before)
_container: ApplicationContainer | None = None


def get_container() -> ApplicationContainer:
    """Return the application container singleton.
    
    Raises:
        RuntimeError: If called before startup.py has initialized the container.
    """
    if _container is None:
        raise RuntimeError(
            "ApplicationContainer has not been initialized. "
            "Ensure startup() has been called before accessing the container."
        )
    return _container


def _set_container(container: ApplicationContainer) -> None:
    """Set the global container. Called only by startup.py."""
    global _container
    _container = container
