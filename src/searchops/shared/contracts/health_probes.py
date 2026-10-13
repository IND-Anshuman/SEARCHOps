"""
Real component health probes for the platform health endpoints.

The health endpoints previously returned ``healthy`` with an empty component
list and reported ``ready`` unconditionally. That is the worst possible failure
mode for an operator: a green light while Redis or Neo4j is down, so a broken
job looks like a healthy platform.

Each probe here is:

- **Bounded** — a hard timeout, so an unreachable service delays the probe by
  milliseconds rather than hanging the endpoint.
- **Non-fatal** — a probe that itself raises is recorded as ``unhealthy`` with
  the exception text; it never propagates and never takes the endpoint down.
- **Honest** — the reported status is what was actually observed, never a
  default. ``components`` is never empty.

Overall status rules:
    any component unhealthy → UNHEALTHY
    any component degraded  → DEGRADED
    all healthy             → HEALTHY
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import structlog

from searchops.shared.contracts.health import ComponentHealth, HealthStatusValue

log = structlog.get_logger(__name__)

# Per-probe budget. Health endpoints are polled by orchestrators, so they must
# answer fast even when a dependency is wedged.
_DEFAULT_PROBE_TIMEOUT = 2.0


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single component probe."""

    status: HealthStatusValue
    message: str | None = None
    details: dict[str, Any] | None = None


def _unhealthy(exc: BaseException) -> ProbeResult:
    return ProbeResult(status=HealthStatusValue.UNHEALTHY, message=f"{type(exc).__name__}: {exc}")


async def probe_redis(client: Any, *, timeout: float = _DEFAULT_PROBE_TIMEOUT) -> ProbeResult:
    """Ping Redis. A reachable Redis that answers PONG is healthy.

    Resolves the ping method through the object: the container passes a
    ``RedisCache`` *wrapper*, which holds the raw redis-py client at
    ``.client`` and has no ``ping`` of its own. Calling ``client.ping()`` on
    the wrapper raised ``AttributeError`` and reported a healthy Redis as
    unhealthy — so look for ``ping`` on the object first, then on ``.client``.
    """

    async def _run() -> Any:
        target = client
        ping = getattr(target, "ping", None)
        if ping is None:
            inner = getattr(target, "client", None)
            if inner is None:
                raise AttributeError("neither the cache wrapper nor its .client exposes ping()")
            ping = getattr(inner, "ping", None)
            if ping is None:
                raise AttributeError("cache .client exposes no ping()")
        result = ping()
        if asyncio.iscoroutine(result):
            result = await result
        return result

    try:
        start = time.perf_counter()
        ok = await asyncio.wait_for(_run(), timeout=timeout)
        elapsed = (time.perf_counter() - start) * 1000
        if ok:
            return ProbeResult(HealthStatusValue.HEALTHY, details={"latency_ms": round(elapsed, 2)})
        return ProbeResult(HealthStatusValue.UNHEALTHY, message="PING did not return truthy")
    except TimeoutError:
        return ProbeResult(HealthStatusValue.UNHEALTHY, message=f"no response within {timeout}s")
    except Exception as exc:
        return _unhealthy(exc)


async def probe_postgres(client: Any, *, timeout: float = _DEFAULT_PROBE_TIMEOUT) -> ProbeResult:
    """Run the cheapest possible query against Postgres."""

    async def _run() -> Any:
        # Support both an async engine's connect() and a raw connection.
        if hasattr(client, "connect"):
            async with client.connect() as conn:
                return await conn.execute(_trivial_sql())
        return await client.execute(_trivial_sql())

    try:
        start = time.perf_counter()
        await asyncio.wait_for(_run(), timeout=timeout)
        elapsed = (time.perf_counter() - start) * 1000
        return ProbeResult(HealthStatusValue.HEALTHY, details={"latency_ms": round(elapsed, 2)})
    except TimeoutError:
        return ProbeResult(HealthStatusValue.UNHEALTHY, message=f"no response within {timeout}s")
    except Exception as exc:
        return _unhealthy(exc)


def _trivial_sql() -> Any:
    from sqlalchemy import text

    return text("SELECT 1")


async def probe_qdrant(client: Any, *, timeout: float = _DEFAULT_PROBE_TIMEOUT) -> ProbeResult:
    """Ask Qdrant for its collection list — exercises the real transport."""

    async def _run() -> Any:
        for attr in ("get_collections", "health"):
            fn = getattr(client, attr, None)
            if fn is not None:
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
                return True
        raise AttributeError("client exposes neither get_collections nor health")

    try:
        start = time.perf_counter()
        await asyncio.wait_for(_run(), timeout=timeout)
        elapsed = (time.perf_counter() - start) * 1000
        return ProbeResult(HealthStatusValue.HEALTHY, details={"latency_ms": round(elapsed, 2)})
    except TimeoutError:
        return ProbeResult(HealthStatusValue.UNHEALTHY, message=f"no response within {timeout}s")
    except Exception as exc:
        return _unhealthy(exc)


async def probe_neo4j(client: Any, *, timeout: float = _DEFAULT_PROBE_TIMEOUT) -> ProbeResult:
    """Run ``RETURN 1`` over Bolt — the only reliable Neo4j readiness signal."""

    async def _run() -> Any:
        for attr in ("verify_connectivity", "ping"):
            fn = getattr(client, attr, None)
            if fn is not None:
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
                return True
        raise AttributeError("client exposes neither verify_connectivity nor ping")

    try:
        start = time.perf_counter()
        await asyncio.wait_for(_run(), timeout=timeout)
        elapsed = (time.perf_counter() - start) * 1000
        return ProbeResult(HealthStatusValue.HEALTHY, details={"latency_ms": round(elapsed, 2)})
    except TimeoutError:
        return ProbeResult(HealthStatusValue.UNHEALTHY, message=f"no response within {timeout}s")
    except Exception as exc:
        return _unhealthy(exc)


def _skipped(name: str, reason: str) -> ComponentHealth:
    """A component the container did not build (optional dependency absent)."""
    return ComponentHealth(
        name=name,
        status=HealthStatusValue.HEALTHY,
        message=f"not configured: {reason}",
        duration_ms=0.0,
        details={"configured": False},
    )


async def collect_component_health(
    container: Any,
    *,
    timeout: float = _DEFAULT_PROBE_TIMEOUT,
) -> list[ComponentHealth]:
    """Probe every component the container actually built, concurrently.

    Components that were never constructed (optional dependencies) are reported
    as ``configured: false`` rather than being silently omitted, so the response
    always explains the whole platform rather than only its happy parts.
    """
    probes: list[tuple[str, Any, Any]] = [
        ("redis", getattr(container, "cache", None), probe_redis),
        ("postgres", getattr(container, "db_engine", None), probe_postgres),
        ("qdrant", getattr(container, "vector_store", None), probe_qdrant),
        ("neo4j", getattr(container, "graph_client", None), probe_neo4j),
    ]

    async def _one(name: str, client: Any, fn: Any) -> ComponentHealth:
        if client is None:
            return _skipped(name, "no client built")
        start = time.perf_counter()
        result = await fn(client, timeout=timeout)
        elapsed = (time.perf_counter() - start) * 1000
        return ComponentHealth(
            name=name,
            status=result.status,
            message=result.message,
            duration_ms=round(elapsed, 2),
            details=result.details or {},
        )

    return list(await asyncio.gather(*[_one(n, c, f) for n, c, f in probes], return_exceptions=False))


def overall_status(components: list[ComponentHealth]) -> HealthStatusValue:
    """Roll component statuses up into one platform status.

    Only *configured* components count toward degradation: an optional
    dependency that was never built must not make the platform look unhealthy.
    """
    statuses = [c.status for c in components if c.details.get("configured", True)]
    if any(s == HealthStatusValue.UNHEALTHY for s in statuses):
        return HealthStatusValue.UNHEALTHY
    if any(s == HealthStatusValue.DEGRADED for s in statuses):
        return HealthStatusValue.DEGRADED
    return HealthStatusValue.HEALTHY
