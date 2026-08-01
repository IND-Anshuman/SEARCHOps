"""
A2A Transports abstraction (HTTP & WebSockets).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx
import structlog

from searchops.a2a.protocol.envelopes import A2AMessageEnvelope
from searchops.core.exceptions.infrastructure import ExternalServiceError

log = structlog.get_logger(__name__)


class IA2ATransport(ABC):
    """Abstract transport port for A2A communication."""

    @abstractmethod
    async def send(self, target_endpoint: str, envelope: A2AMessageEnvelope) -> A2AMessageEnvelope:
        """Send message envelope over transport and receive response."""
        ...


class HTTPA2ATransport(IA2ATransport):
    """HTTP Client transport for remote A2A calls."""

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def send(self, target_endpoint: str, envelope: A2AMessageEnvelope) -> A2AMessageEnvelope:
        """Send envelope via HTTP POST to remote agent endpoint."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(
                    target_endpoint,
                    json=envelope.model_dump(mode="json"),
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
                return A2AMessageEnvelope.model_validate(data)
        except Exception as exc:
            log.error("HTTP A2A transport error", endpoint=target_endpoint, error=str(exc))
            raise ExternalServiceError(
                service="A2AAgent",
                response_body=str(exc),
                cause=exc,
            ) from exc
