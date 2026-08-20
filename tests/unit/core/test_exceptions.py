from __future__ import annotations

import pytest

from searchops.core.exceptions.application import (
    ApplicationError,
    AuthorizationError,
    BudgetExceededError,
    ValidationError,
)
from searchops.core.exceptions.base import (
    ErrorCode,
    RetryableError,
    SEARCHOpsError,
)
from searchops.core.exceptions.domain import (
    BusinessRuleViolationError,
    DuplicateEntityError,
    EntityNotFoundError,
)
from searchops.core.exceptions.infrastructure import (
    CircuitBreakerOpenError,
    ExternalServiceError,
    InfrastructureError,
    TimeoutError,
)


@pytest.mark.unit
def test_searchops_error_raised_caught():
    try:
        raise SEARCHOpsError("Test error", code=ErrorCode.INTERNAL_ERROR)
    except SEARCHOpsError as e:
        assert str(e) == "Test error"
        assert e.code == ErrorCode.INTERNAL_ERROR


@pytest.mark.unit
def test_searchops_error_with_context():
    error = SEARCHOpsError("Test error", code=ErrorCode.INTERNAL_ERROR)
    error.with_context(user_id="123", action="test")
    assert error.context == {"user_id": "123", "action": "test"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "enum_member, expected_value",
    [
        (ErrorCode.INTERNAL_ERROR, "INTERNAL_ERROR"),
        (ErrorCode.VALIDATION_ERROR, "VALIDATION_ERROR"),
        (ErrorCode.ENTITY_NOT_FOUND, "ENTITY_NOT_FOUND"),
        (ErrorCode.RATE_LIMIT_EXCEEDED, "RATE_LIMIT_EXCEEDED"),
        (ErrorCode.TIMEOUT_ERROR, "TIMEOUT_ERROR"),
    ],
)
def test_error_code_enum_values(enum_member: ErrorCode, expected_value: str):
    assert enum_member.value == expected_value


@pytest.mark.unit
def test_entity_not_found_error_message():
    err = EntityNotFoundError(entity_type="User", entity_id="usr_123")
    assert err.code == ErrorCode.ENTITY_NOT_FOUND
    assert "User with id 'usr_123' not found" in err.message
    assert err.context == {"entity_type": "User", "entity_id": "usr_123"}


@pytest.mark.unit
def test_duplicate_entity_error():
    err = DuplicateEntityError(
        entity_type="User", conflicting_field="email", conflicting_value="test@example.com"
    )
    assert err.code == ErrorCode.DUPLICATE_ENTITY
    assert "User with email='test@example.com' already exists" in err.message


@pytest.mark.unit
def test_authorization_error_message():
    err = AuthorizationError(action="delete", resource="report_1")
    assert "Principal is not authorized to perform 'delete' on 'report_1'" in err.message


@pytest.mark.unit
def test_budget_exceeded_error():
    err = BudgetExceededError(budget_type="Token", limit=100.0, consumed=150.0)
    assert "Token budget exceeded: consumed 150.00 of limit 100.00" in err.message


@pytest.mark.unit
def test_circuit_breaker_open_error_retry_after():
    err = CircuitBreakerOpenError(service="Firecrawl", recovery_timeout=60.0)
    assert err.retry_after_seconds == 60.0
    assert isinstance(err, RetryableError)


@pytest.mark.unit
def test_timeout_error():
    err = TimeoutError(operation="web_scrape", timeout_seconds=30.0)
    assert "Operation 'web_scrape' timed out after 30.0s" in err.message


@pytest.mark.unit
def test_external_service_error_status_code():
    err = ExternalServiceError(service="OpenAI", status_code=503)
    assert "External service 'OpenAI' returned an error (HTTP 503)" in err.message
    assert err.context["status_code"] == 503
