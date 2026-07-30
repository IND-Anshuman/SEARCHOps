"""
Domain-layer exceptions.

Raised by domain services and aggregates when business rules are violated.
These exceptions are pure domain concepts and must NOT reference any infrastructure.
"""
from __future__ import annotations

from typing import Any

from searchops.core.exceptions.base import ErrorCode, NonRetryableError


class DomainError(NonRetryableError):
    """Base class for all domain-layer exceptions."""
    code: ErrorCode = ErrorCode.INTERNAL_ERROR


class EntityNotFoundError(DomainError):
    """Raised when an expected entity does not exist in the domain."""
    code: ErrorCode = ErrorCode.ENTITY_NOT_FOUND
    
    def __init__(
        self,
        entity_type: str,
        entity_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            f"{entity_type} with id '{entity_id}' not found",
            context={"entity_type": entity_type, "entity_id": entity_id},
            **kwargs,
        )
        self.entity_type = entity_type
        self.entity_id = entity_id


class DuplicateEntityError(DomainError):
    """Raised when an entity already exists and uniqueness is required."""
    code: ErrorCode = ErrorCode.DUPLICATE_ENTITY
    
    def __init__(
        self,
        entity_type: str,
        conflicting_field: str,
        conflicting_value: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            f"{entity_type} with {conflicting_field}='{conflicting_value}' already exists",
            context={
                "entity_type": entity_type,
                "conflicting_field": conflicting_field,
                "conflicting_value": str(conflicting_value),
            },
            **kwargs,
        )


class BusinessRuleViolationError(DomainError):
    """Raised when a business invariant is violated."""
    code: ErrorCode = ErrorCode.BUSINESS_RULE_VIOLATION
    
    def __init__(self, rule: str, details: str | None = None, **kwargs: Any) -> None:
        message = f"Business rule violated: {rule}"
        if details:
            message += f" — {details}"
        super().__init__(message, context={"rule": rule}, **kwargs)
        self.rule = rule


class InvalidStateTransitionError(DomainError):
    """Raised when an aggregate is asked to transition to an illegal state."""
    code: ErrorCode = ErrorCode.INVALID_STATE_TRANSITION
    
    def __init__(
        self,
        entity_type: str,
        from_state: str,
        to_state: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            f"{entity_type} cannot transition from '{from_state}' to '{to_state}'",
            context={
                "entity_type": entity_type,
                "from_state": from_state,
                "to_state": to_state,
            },
            **kwargs,
        )
