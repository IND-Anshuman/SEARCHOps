"""Exception hierarchy for the SEARCHOps platform."""

from searchops.core.exceptions.application import (
    ApplicationError,
    AuthenticationError,
    AuthorizationError,
    BudgetExceededError,
    RateLimitError,
    UseCaseError,
    ValidationError,
)
from searchops.core.exceptions.base import (
    ErrorCode,
    NonRetryableError,
    RetryableError,
    SEARCHOpsError,
)
from searchops.core.exceptions.domain import (
    BusinessRuleViolationError,
    DomainError,
    DuplicateEntityError,
    EntityNotFoundError,
    InvalidStateTransitionError,
)
from searchops.core.exceptions.infrastructure import (
    CacheError,
    CircuitBreakerOpenError,
    ConnectionError,
    DatabaseError,
    ExternalServiceError,
    GraphDatabaseError,
    InfrastructureError,
    LLMError,
    MessagingError,
    ScrapingError,
    TimeoutError,
    VectorStoreError,
)

__all__ = [
    "ApplicationError",
    "AuthenticationError",
    "AuthorizationError",
    "BudgetExceededError",
    "BusinessRuleViolationError",
    "CacheError",
    "CircuitBreakerOpenError",
    "ConnectionError",
    "DatabaseError",
    "DomainError",
    "DuplicateEntityError",
    "EntityNotFoundError",
    "ErrorCode",
    "ExternalServiceError",
    "GraphDatabaseError",
    "InfrastructureError",
    "InvalidStateTransitionError",
    "LLMError",
    "MessagingError",
    "NonRetryableError",
    "RateLimitError",
    "RetryableError",
    "SEARCHOpsError",
    "ScrapingError",
    "TimeoutError",
    "UseCaseError",
    "ValidationError",
    "VectorStoreError",
]
