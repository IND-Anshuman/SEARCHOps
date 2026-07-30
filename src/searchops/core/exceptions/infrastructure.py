"""
Infrastructure-layer exceptions.

Raised by adapters, repositories, and external service clients.
These should NEVER leak into the domain or application layers raw —
always wrap them in a domain or application exception at the adapter boundary.
"""
from __future__ import annotations

from typing import Any

from searchops.core.exceptions.base import (
    ErrorCode,
    RetryableError,
    NonRetryableError,
    SEARCHOpsError,
)


class InfrastructureError(RetryableError):
    """Base for all infrastructure exceptions."""
    code: ErrorCode = ErrorCode.INTERNAL_ERROR


class DatabaseError(InfrastructureError):
    """Raised on PostgreSQL/SQLAlchemy failures."""
    code: ErrorCode = ErrorCode.DATABASE_ERROR


class ConnectionError(InfrastructureError):  # noqa: A001
    """Raised when a connection to an external service cannot be established."""
    code: ErrorCode = ErrorCode.CONNECTION_ERROR


class TimeoutError(RetryableError):  # noqa: A001
    """Raised when an operation exceeds its time budget."""
    code: ErrorCode = ErrorCode.TIMEOUT_ERROR
    
    def __init__(
        self,
        operation: str,
        timeout_seconds: float,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            f"Operation '{operation}' timed out after {timeout_seconds}s",
            context={"operation": operation, "timeout_seconds": timeout_seconds},
            **kwargs,
        )


class ExternalServiceError(InfrastructureError):
    """Raised when an external HTTP API returns an error response."""
    code: ErrorCode = ErrorCode.EXTERNAL_SERVICE_ERROR
    
    def __init__(
        self,
        service: str,
        status_code: int | None = None,
        response_body: str | None = None,
        **kwargs: Any,
    ) -> None:
        message = f"External service '{service}' returned an error"
        if status_code:
            message += f" (HTTP {status_code})"
        super().__init__(
            message,
            context={
                "service": service,
                "status_code": status_code,
                "response_body": response_body,
            },
            **kwargs,
        )


class ScrapingError(ExternalServiceError):
    """Raised when web scraping fails."""
    code: ErrorCode = ErrorCode.SCRAPING_ERROR


class LLMError(ExternalServiceError):
    """Raised when an LLM API call fails."""
    code: ErrorCode = ErrorCode.LLM_ERROR


class CircuitBreakerOpenError(RetryableError):
    """Raised when a circuit breaker is open and blocking requests."""
    code: ErrorCode = ErrorCode.CIRCUIT_BREAKER_OPEN
    
    def __init__(self, service: str, recovery_timeout: float, **kwargs: Any) -> None:
        super().__init__(
            f"Circuit breaker for '{service}' is OPEN. Will attempt recovery in {recovery_timeout}s",
            context={"service": service, "recovery_timeout": recovery_timeout},
            retry_after_seconds=recovery_timeout,
            **kwargs,
        )
        self.recovery_timeout = recovery_timeout


class CacheError(InfrastructureError):
    """Raised on Redis/cache operation failures."""
    code: ErrorCode = ErrorCode.CACHE_ERROR


class GraphDatabaseError(InfrastructureError):
    """Raised on Neo4j operation failures."""
    code: ErrorCode = ErrorCode.GRAPH_DATABASE_ERROR


class VectorStoreError(InfrastructureError):
    """Raised on Qdrant operation failures."""
    code: ErrorCode = ErrorCode.VECTOR_STORE_ERROR


class MessagingError(InfrastructureError):
    """Raised on event bus / messaging failures."""
    code: ErrorCode = ErrorCode.MESSAGING_ERROR
