"""
Declarative Base for SQLAlchemy ORM models.

Features:
- DeclarativeBase base class
- Automatic table naming (snake_case from class name)
- Shared Mixins: TimestampMixin, OptimisticLockingMixin, SoftDeleteMixin
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


def _to_snake_case(name: str) -> str:
    """Convert PascalCase class name to snake_case table name."""
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


class BaseORM(DeclarativeBase):
    """Root declarative base for all SQLAlchemy models in SEARCHOps."""

    @declared_attr.directive
    def __tablename__(cls) -> str: # noqa: N805
        """Generate table name automatically from model class name."""
        return _to_snake_case(cls.__name__)


class TimestampMixin:
    """Mixin that adds created_at and updated_at UTC timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class OptimisticLockingMixin:
    """Mixin for optimistic concurrency control using a version counter."""

    version: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )


class SoftDeleteMixin:
    """Mixin for soft deletion support."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        nullable=True,
    )

    @property
    def is_deleted(self) -> bool:
        """True if entity is soft-deleted."""
        return self.deleted_at is not None
