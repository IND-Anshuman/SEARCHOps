"""
Application DI Container.

All singleton services are registered here. The container is created once
during startup and stored in `app.state.container`. Route handlers access
shared infrastructure via `request.app.state.container` — never by calling
constructors directly.

Lifecycle:
  - container.py: defines what singletons exist
  - startup.py:   creates and wires them (I/O-bound init happens there)
  - lifespan.py:  attaches container to app.state
  - research.py / websocket.py: access via request.app.state.container
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from searchops.config.settings import Settings, get_settings
from searchops.feature_flags.manager import FeatureFlagManager
from searchops.feature_flags.providers import EnvFeatureFlagProvider
from searchops.platform.registry.agent_registry import AgentRegistry
from searchops.platform.registry.capability_registry import CapabilityRegistry
from searchops.platform.registry.tool_registry import ToolRegistry

if TYPE_CHECKING:
    from searchops.application.job_state_manager import JobStateManager
    from searchops.application.research_service import ResearchApplicationService
    from searchops.infrastructure.cache.redis import RedisCache
    from searchops.infrastructure.events.bus import RedisEventBus

log = structlog.get_logger(__name__)


class ApplicationContainer:
    """Central DI container for the SEARCHOps platform.

    Holds all singleton services. Created once during startup and available
    for the lifetime of the process. Accessed via request.app.state.container
    in route handlers and WebSocket endpoints.
    """

    def __init__(
        self,
        settings: Settings,
        cache: "RedisCache",
        event_bus: "RedisEventBus",
        job_state_manager: "JobStateManager",
        research_service: "ResearchApplicationService",
    ) -> None:
        self.settings = settings

        # Infrastructure singletons
        self.cache = cache
        self.event_bus = event_bus

        # Application singletons
        self.job_state_manager = job_state_manager
        self.research_service = research_service

        # Platform registries
        self.feature_flags = FeatureFlagManager(providers=[EnvFeatureFlagProvider()])
        self.agent_registry = AgentRegistry()
        self.tool_registry = ToolRegistry()
        self.capability_registry = CapabilityRegistry()

        log.info("ApplicationContainer initialized with all singletons")

    @classmethod
    def create(cls) -> "ApplicationContainer":
        """Factory method — creates and wires the full container.

        All I/O-bound initialization (Redis ping, etc.) is done in startup.py
        before this is called. This method only wires already-initialized
        objects together.
        """
        from searchops.application.job_state_manager import JobStateManager
        from searchops.application.research_service import ResearchApplicationService
        from searchops.infrastructure.cache.redis import RedisCache, get_redis_client
        from searchops.infrastructure.events.bus import RedisEventBus

        settings = get_settings()
        redis_client = get_redis_client(settings)
        cache = RedisCache(client=redis_client)
        event_bus = RedisEventBus(cache=cache)
        job_state_manager = JobStateManager(cache=cache, event_bus=event_bus)
        research_service = ResearchApplicationService(
            cache=cache,
            job_state_manager=job_state_manager,
        )

        return cls(
            settings=settings,
            cache=cache,
            event_bus=event_bus,
            job_state_manager=job_state_manager,
            research_service=research_service,
        )


# Module-level singleton (initialized during startup, never before)
_container: ApplicationContainer | None = None


def get_container() -> ApplicationContainer:
    """Return the application container singleton.

    Raises:
        RuntimeError: If called before startup() has initialized the container.
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
