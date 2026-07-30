"""
FastAPI exception handlers.

Registered during application startup. Translates platform exceptions
into structured JSON error responses following RFC 7807 (Problem Details).
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from searchops.core.exceptions.base import ErrorCode, SEARCHOpsError
from searchops.core.exceptions.application import (
    AuthenticationError,
    AuthorizationError,
    BudgetExceededError,
    RateLimitError,
    ValidationError,
)
from searchops.core.exceptions.domain import EntityNotFoundError
from searchops.core.exceptions.infrastructure import (
    CircuitBreakerOpenError,
    TimeoutError,
)

log = structlog.get_logger(__name__)

_ERROR_CODE_TO_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.ENTITY_NOT_FOUND: 404,
    ErrorCode.DUPLICATE_ENTITY: 409,
    ErrorCode.BUSINESS_RULE_VIOLATION: 422,
    ErrorCode.INVALID_STATE_TRANSITION: 422,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.AUTHENTICATION_ERROR: 401,
    ErrorCode.AUTHORIZATION_ERROR: 403,
    ErrorCode.RATE_LIMIT_EXCEEDED: 429,
    ErrorCode.BUDGET_EXCEEDED: 402,
    ErrorCode.TIMEOUT_ERROR: 504,
    ErrorCode.CIRCUIT_BREAKER_OPEN: 503,
    ErrorCode.EXTERNAL_SERVICE_ERROR: 502,
    ErrorCode.SCRAPING_ERROR: 502,
    ErrorCode.LLM_ERROR: 502,
    ErrorCode.DATABASE_ERROR: 503,
    ErrorCode.CONNECTION_ERROR: 503,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.UNKNOWN_ERROR: 500,
}


def _problem_detail(
    status: int,
    code: str,
    title: str,
    detail: str,
    request: Request,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an RFC 7807 Problem Detail response body."""
    body: dict[str, Any] = {
        "type": f"https://searchops.io/errors/{code.lower()}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": str(request.url),
    }
    if extra:
        body.update(extra)
    return body


async def searchops_exception_handler(
    request: Request, exc: SEARCHOpsError
) -> ORJSONResponse:
    """Handle all SEARCHOpsError subclasses."""
    status = _ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500)
    
    log.warning(
        "Platform exception",
        error_code=exc.code,
        error_message=exc.message,
        http_status=status,
        path=str(request.url),
        context=exc.context,
        correlation_id=exc.correlation_id,
    )
    
    extra: dict[str, Any] = {"error_code": exc.code}
    if exc.context:
        extra["context"] = exc.context
    if exc.correlation_id:
        extra["correlation_id"] = exc.correlation_id
    if isinstance(exc, RateLimitError) and exc.retry_after_seconds:
        extra["retry_after"] = exc.retry_after_seconds
    if isinstance(exc, CircuitBreakerOpenError):
        extra["retry_after"] = exc.retry_after_seconds
    
    return ORJSONResponse(
        status_code=status,
        content=_problem_detail(
            status=status,
            code=exc.code,
            title=exc.code.replace("_", " ").title(),
            detail=exc.message,
            request=request,
            extra=extra,
        ),
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> ORJSONResponse:
    """Handle standard Starlette HTTP exceptions."""
    log.info(
        "HTTP exception",
        status_code=exc.status_code,
        detail=exc.detail,
        path=str(request.url),
    )
    return ORJSONResponse(
        status_code=exc.status_code,
        content=_problem_detail(
            status=exc.status_code,
            code="HTTP_ERROR",
            title=f"HTTP {exc.status_code}",
            detail=str(exc.detail),
            request=request,
        ),
    )


async def pydantic_validation_exception_handler(
    request: Request, exc: PydanticValidationError
) -> ORJSONResponse:
    """Handle Pydantic v2 validation errors from request parsing."""
    field_errors: dict[str, list[str]] = {}
    for error in exc.errors():
        loc = " -> ".join(str(l) for l in error["loc"])
        field_errors.setdefault(loc, []).append(error["msg"])
    
    log.info(
        "Pydantic validation error",
        field_errors=field_errors,
        path=str(request.url),
    )
    return ORJSONResponse(
        status_code=422,
        content=_problem_detail(
            status=422,
            code="VALIDATION_ERROR",
            title="Validation Error",
            detail="Request body or parameters failed validation",
            request=request,
            extra={"field_errors": field_errors},
        ),
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> ORJSONResponse:
    """Catch-all for unhandled exceptions. Never leaks stack traces."""
    log.exception(
        "Unhandled exception",
        exc_type=type(exc).__name__,
        path=str(request.url),
    )
    return ORJSONResponse(
        status_code=500,
        content=_problem_detail(
            status=500,
            code="INTERNAL_ERROR",
            title="Internal Server Error",
            detail="An unexpected error occurred. Please contact support.",
            request=request,
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI application.
    
    Called during application startup from the bootstrap layer.
    """
    app.add_exception_handler(SEARCHOpsError, searchops_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(PydanticValidationError, pydantic_validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
    
    log.info("Exception handlers registered")
