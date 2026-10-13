"""Type aliases, NewTypes, and annotated validators — avoid primitive obsession."""

from searchops.typing.aliases import (
    AgentId,
    CorrelationId,
    EntityId,
    ExecutionId,
    RequestId,
    ResearchId,
    TaskId,
    TraceId,
    UserId,
)
from searchops.typing.newtypes import (
    CostUSD,
    PercentFloat,
    PositiveFloat,
    PositiveInt,
    TokenCount,
)
from searchops.typing.validators import (
    NonEmptyStr,
    UrlStr,
)

__all__ = [
    "AgentId",
    "CorrelationId",
    "CostUSD",
    "EntityId",
    "ExecutionId",
    "NonEmptyStr",
    "PercentFloat",
    "PositiveFloat",
    "PositiveInt",
    "RequestId",
    "ResearchId",
    "TaskId",
    "TokenCount",
    "TraceId",
    "UrlStr",
    "UserId",
]
