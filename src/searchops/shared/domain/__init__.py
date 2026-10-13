"""Shared domain building blocks (DDD primitives)."""

from searchops.shared.domain.aggregate import AggregateRoot
from searchops.shared.domain.command import Command
from searchops.shared.domain.entity import BaseEntity
from searchops.shared.domain.event import DomainEvent
from searchops.shared.domain.value_object import BaseValueObject

__all__ = [
    "AggregateRoot",
    "BaseEntity",
    "BaseValueObject",
    "Command",
    "DomainEvent",
]
