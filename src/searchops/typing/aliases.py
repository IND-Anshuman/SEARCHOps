"""
Type aliases for domain identifiers.

Using distinct type aliases for each identifier type prevents accidental
mixing of IDs across domain boundaries (UserId != AgentId).
"""
from __future__ import annotations

from typing import TypeAlias

# ─── Identifier aliases ───────────────────────────────────────────────────────

#: Universally unique identifier for any entity (UUID string)
EntityId: TypeAlias = str

#: User identifier
UserId: TypeAlias = str

#: Research session identifier
ResearchId: TypeAlias = str

#: Individual agent identifier (A2A agent card ID)
AgentId: TypeAlias = str

#: Asynchronous task identifier
TaskId: TypeAlias = str

#: Single graph execution identifier
ExecutionId: TypeAlias = str

#: OpenTelemetry trace ID (hex string)
TraceId: TypeAlias = str

#: Cross-service correlation ID
CorrelationId: TypeAlias = str

#: HTTP request identifier
RequestId: TypeAlias = str

#: Knowledge graph node identifier
NodeId: TypeAlias = str

#: Knowledge graph edge identifier
EdgeId: TypeAlias = str

#: Vector embedding identifier in Qdrant
VectorId: TypeAlias = str

#: Plugin identifier
PluginId: TypeAlias = str

#: Scraping job identifier
ScrapeJobId: TypeAlias = str

#: Report identifier
ReportId: TypeAlias = str
