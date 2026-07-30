"""
Root exception hierarchy for SEARCHOps.

All platform exceptions derive from SEARCHOpsError. This enables:
- Typed exception handling
- Structured error codes for API responses  
- Correlation ID threading
- Retryability classification
"""
from __future__ import annotations

import enum
from typing import Any


class ErrorCode(enum.StrEnum):
    """Platform-wide error codes. Maps to HTTP status codes in exception handlers."""
    
    # Generic
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    
    # Domain
    ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
    DUPLICATE_ENTITY = "DUPLICATE_ENTITY"
    BUSINESS_RULE_VIOLATION = "BUSINESS_RULE_VIOLATION"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    
    # Application
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    USE_CASE_ERROR = "USE_CASE_ERROR"
    
    # Infrastructure
    DATABASE_ERROR = "DATABASE_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
    SCRAPING_ERROR = "SCRAPING_ERROR"
    LLM_ERROR = "LLM_ERROR"
    CIRCUIT_BREAKER_OPEN = "CIRCUIT_BREAKER_OPEN"
    CACHE_ERROR = "CACHE_ERROR"
    GRAPH_DATABASE_ERROR = "GRAPH_DATABASE_ERROR"
    VECTOR_STORE_ERROR = "VECTOR_STORE_ERROR"
    MESSAGING_ERROR = "MESSAGING_ERROR"
    
    # Agent
    AGENT_ERROR = "AGENT_ERROR"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    AGENT_BUDGET_EXCEEDED = "AGENT_BUDGET_EXCEEDED"
    AGENT_RECURSION_LIMIT = "AGENT_RECURSION_LIMIT"
    PROMPT_INJECTION_DETECTED = "PROMPT_INJECTION_DETECTED"
    
    # Research
    RESEARCH_NOT_FOUND = "RESEARCH_NOT_FOUND"
    RESEARCH_CANCELLED = "RESEARCH_CANCELLED"
    RESEARCH_FAILED = "RESEARCH_FAILED"
    
    # Knowledge Graph
    ENTITY_EXTRACTION_FAILED = "ENTITY_EXTRACTION_FAILED"
    GRAPH_BUILD_FAILED = "GRAPH_BUILD_FAILED"
    
    # Scraping
    DOMAIN_NOT_ALLOWED = "DOMAIN_NOT_ALLOWED"
    CONTENT_TOO_LARGE = "CONTENT_TOO_LARGE"
    SCRAPING_BLOCKED = "SCRAPING_BLOCKED"


class SEARCHOpsError(Exception):
    """Root exception for all SEARCHOps platform errors.
    
    Every platform exception derives from this class. This ensures:
    - A single catch point at API boundaries
    - Structured error information (code, context, correlation_id)
    - Clear separation between retryable and non-retryable failures
    
    Attributes:
        code: Machine-readable error code
        message: Human-readable description
        context: Arbitrary key-value context for debugging
        correlation_id: Optional request/trace correlation ID
    """
    
    code: ErrorCode = ErrorCode.UNKNOWN_ERROR
    
    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.code
        self.context: dict[str, Any] = context or {}
        self.correlation_id = correlation_id
        if cause is not None:
            self.__cause__ = cause
    
    def with_context(self, **kwargs: Any) -> SEARCHOpsError:
        """Return a copy of this exception enriched with additional context."""
        self.context.update(kwargs)
        return self
    
    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"code={self.code!r}, "
            f"message={self.message!r}, "
            f"context={self.context!r}"
            f")"
        )


class RetryableError(SEARCHOpsError):
    """Base for errors that may succeed if retried (e.g., transient network failures)."""
    
    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class NonRetryableError(SEARCHOpsError):
    """Base for errors that will not succeed on retry (e.g., invalid input, auth failure)."""
