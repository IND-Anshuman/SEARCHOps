"""Shared JSON serialisation for anything written to Redis.

Every write to Redis goes through ``json_safe`` before ``orjson.dumps``.

Why: orjson raises ``TypeError: Type is not JSON serializable`` on Pydantic
models, datetimes, Decimals, UUIDs and enums. When that happens inside
``RedisCache.set`` or ``EventBus.publish`` the exception is caught and logged,
the write is skipped, and the caller has no idea the value never landed. That
failure is silent: the job still reports COMPLETED while its state was never
persisted, so the client polls and gets a job with no report.

Normalising at the boundary fixes every present and future caller at once
instead of relying on each call site to remember ``model_dump()``.
"""

from __future__ import annotations

import datetime
import enum
import uuid
from decimal import Decimal
from typing import Any

try:  # Pydantic v2
    from pydantic import BaseModel

    def _as_dict(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump()
        return value

except ImportError:  # pragma: no cover - pydantic is a hard dependency

    def _as_dict(value: Any) -> Any:
        return value


def json_safe(value: Any) -> Any:
    """Recursively convert ``value`` into JSON-serialisable primitives.

    Handles Pydantic models, dataclass-like objects exposing ``model_dump``
    or ``__dict__``, enums (by value), Decimal, UUID, datetime/date/time, sets
    and tuples. Anything unknown is passed through so orjson can still reject
    it loudly rather than silently coercing.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value

    if isinstance(value, enum.Enum):
        return json_safe(value.value)

    if isinstance(value, BaseModel):
        return json_safe(value.model_dump())

    # datetime before date: datetime subclasses date.
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date | datetime.time):
        return value.isoformat()

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, uuid.UUID):
        return str(value)

    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}

    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(v) for v in value]

    # Dataclasses and other model types that expose a dump method.
    for method in ("model_dump", "to_dict", "dict"):
        fn = getattr(value, method, None)
        if callable(fn):
            try:
                return json_safe(fn())
            except TypeError:
                continue

    return value


__all__ = ["json_safe"]
