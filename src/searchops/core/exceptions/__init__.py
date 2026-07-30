"""Exception hierarchy for the SEARCHOps platform."""

from searchops.core.exceptions.base import (
    ErrorCode,
    SEARCHOpsError,
    RetryableError,
    NonRetryableError,
)
from searchops.core.exceptions.domain import (
    DomainError,
    EntityNotFoundError,
    DuplicateEntityError,
    BusinessRuleViolationError,
    InvalidStateTransitionError,
)
from searchops.core.exceptions.application import (
    ApplicationError,
    UseCaseError,
    ValidationError,
    AuthorizationError,
    AuthenticationError,
    RateLimitError,
    BudgetExceededError,
)
from searchops.core.exceptions.infrastructure import (
    InfrastructureError,
    DatabaseError,
    ConnectionError,
    TimeoutError,
    ExternalServiceError,
    ScrapingError,
    LLMError,
    CircuitBreakerOpenError,
    CacheError,
    GraphDatabaseError,
    VectorStoreError,
    MessagingError,
)

__all__ = [
    "ErrorCode",
    "SEARCHOpsError",
    "RetryableError",
    "NonRetryableError",
    "DomainError",
    "EntityNotFoundError",
    "DuplicateEntityError",
    "BusinessRuleViolationError",
    "InvalidStateTransitionError",
    "ApplicationError",
    "UseCaseError",
    "ValidationError",
    "AuthorizationError",
    "AuthenticationError",
    "RateLimitError",
    "BudgetExceededError",
    "InfrastructureError",
    "DatabaseError",
    "ConnectionError",
    "TimeoutError",
    "ExternalServiceError",
    "ScrapingError",
    "LLMError",
    "CircuitBreakerOpenError",
    "CacheError",
    "GraphDatabaseError",
    "VectorStoreError",
    "MessagingError",
]
