"""
Event bus protocol.

Defines the abstraction for publishing and subscribing to domain events.
Implementations include Redis Streams (production) and InMemory (testing).
"""
from __future__ import annotations

from typing import Any, Callable, Coroutine, Protocol, runtime_checkable

from searchops.shared.domain.event import DomainEvent


HandlerCallable = Callable[[DomainEvent], Coroutine[Any, Any, None]]


@runtime_checkable
class IEventHandler(Protocol):
    """An async event handler."""
    
    async def handle(self, event: DomainEvent) -> None:
        """Handle a domain event."""
        ...


@runtime_checkable
class IEventBus(Protocol):
    """Contract for the platform event bus."""
    
    async def publish(self, event: DomainEvent) -> None:
        """Publish a single domain event."""
        ...
    
    async def publish_many(self, events: list[DomainEvent]) -> None:
        """Publish multiple domain events atomically where possible."""
        ...
    
    async def subscribe(
        self,
        event_type: type[DomainEvent],
        handler: IEventHandler | HandlerCallable,
        *,
        consumer_group: str | None = None,
    ) -> None:
        """Subscribe a handler to a specific event type."""
        ...
    
    async def unsubscribe(
        self,
        event_type: type[DomainEvent],
        handler: IEventHandler | HandlerCallable,
    ) -> None:
        """Remove a handler subscription."""
        ...
    
    async def start(self) -> None:
        """Start consuming events."""
        ...
    
    async def stop(self) -> None:
        """Stop consuming events gracefully."""
        ...
