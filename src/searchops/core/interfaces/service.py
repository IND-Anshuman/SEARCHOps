"""
Base service protocol.

All application services implement this interface to enable
DI container resolution and health checking.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IService(Protocol):
    """Base protocol for all platform services."""
    
    async def initialize(self) -> None:
        """Perform async initialization. Called once during startup."""
        ...
    
    async def shutdown(self) -> None:
        """Perform graceful shutdown. Called once during application teardown."""
        ...
