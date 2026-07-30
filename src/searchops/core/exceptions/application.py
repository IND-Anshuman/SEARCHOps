"""
Application-layer exceptions.

Raised by use cases, command/query handlers, and application services.
These map directly to HTTP error responses via FastAPI exception handlers.
"""
from __future__ import annotations

from typing import Any

from searchops.core.exceptions.base import (
    ErrorCode,
    NonRetryableError,
    RetryableError,
)


class ApplicationError(NonRetryableError):
    """Base class for all application-layer exceptions."""
    code: ErrorCode = ErrorCode.INTERNAL_ERROR


class UseCaseError(ApplicationError):
    """Raised when a use case fails to execute."""
    code: ErrorCode = ErrorCode.USE_CASE_ERROR


class ValidationError(ApplicationError):
    """Raised when input validation fails at the application boundary."""
    code: ErrorCode = ErrorCode.VALIDATION_ERROR
    
    def __init__(
        self,
        message: str,
        field_errors: dict[str, list[str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.field_errors: dict[str, list[str]] = field_errors or {}


class AuthenticationError(NonRetryableError):
    """Raised when a request cannot be authenticated."""
    code: ErrorCode = ErrorCode.AUTHENTICATION_ERROR


class AuthorizationError(NonRetryableError):
    """Raised when an authenticated principal lacks required permissions."""
    code: ErrorCode = ErrorCode.AUTHORIZATION_ERROR
    
    def __init__(
        self,
        action: str,
        resource: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            f"Principal is not authorized to perform '{action}' on '{resource}'",
            context={"action": action, "resource": resource},
            **kwargs,
        )


class RateLimitError(RetryableError):
    """Raised when a rate limit is exceeded."""
    code: ErrorCode = ErrorCode.RATE_LIMIT_EXCEEDED


class BudgetExceededError(NonRetryableError):
    """Raised when a token or cost budget is exceeded."""
    code: ErrorCode = ErrorCode.BUDGET_EXCEEDED
    
    def __init__(
        self,
        budget_type: str,
        limit: float,
        consumed: float,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            f"{budget_type} budget exceeded: consumed {consumed:.2f} of limit {limit:.2f}",
            context={"budget_type": budget_type, "limit": limit, "consumed": consumed},
            **kwargs,
        )
