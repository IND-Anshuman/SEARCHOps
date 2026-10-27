"""
Tests for real component health probing.

The bug these guard against: `/health` used to return ``healthy`` with an empty
component list, and `/ready`` always returned ready. An operator therefore got a
green light while Redis or Neo4j was down.

Contract pinned here:
  - every probe is bounded by a timeout, so a wedged dependency cannot hang the
    endpoint
  - a probe that raises is recorded as unhealthy and never propagates
  - unconfigured components are reported (``configured: false``) rather than
    silently omitted
  - an optional, unconfigured dependency does not make the platform unhealthy
  - a configured-but-down dependency makes it unhealthy and readiness 503
"""

from __future__ import annotations

import asyncio

import pytest

from searchops.shared.contracts.health import ComponentHealth, HealthStatusValue
from searchops.shared.contracts.health_probes import (
    collect_component_health,
    overall_status,
    probe_neo4j,
    probe_postgres,
    probe_qdrant,
    probe_redis,
)

# ── Stub clients ─────────────────────────────────────────────────────────────


class _HealthyRedis:
    async def ping(self):
        return True


class _FalseRedis:
    async def ping(self):
        return False


class _BrokenRedis:
    async def ping(self):
        raise ConnectionRefusedError("connection refused")


class _RealShapedRedisCache:
    """Mirrors the REAL ``RedisCache``: no ``ping`` method itself; the raw
    redis-py client (which does have ``ping``) is held at ``.client``.

    The production ``/health`` failed with
    ``AttributeError: 'RedisCache' object has no attribute 'ping'`` precisely
    because the probe called ``ping()`` on this wrapper. The stub classes above
    all expose ``ping`` directly, so they never caught it.
    """

    def __init__(self) -> None:
        self.client = _HealthyRedis()


class _HangingRedis:
    async def ping(self):
        await asyncio.sleep(30)
        return True


class _HealthyPostgres:
    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, sql):
            return [(1,)]

    def connect(self):
        return self._Conn()


class _BrokenPostgres:
    def connect(self):
        raise RuntimeError("password authentication failed")


class _HealthyQdrant:
    async def get_collections(self):
        return {"collections": []}


class _HealthyNeo4j:
    async def verify_connectivity(self):
        return None


class _BrokenNeo4j:
    async def verify_connectivity(self):
        raise OSError("bolt connection reset")


# ── Individual probes ────────────────────────────────────────────────────────


class TestRedisProbe:
    @pytest.mark.asyncio
    async def test_healthy_redis(self):
        r = await probe_redis(_HealthyRedis())
        assert r.status == HealthStatusValue.HEALTHY
        assert r.details is not None and "latency_ms" in r.details

    @pytest.mark.asyncio
    async def test_non_truthy_ping_is_unhealthy(self):
        r = await probe_redis(_FalseRedis())
        assert r.status == HealthStatusValue.UNHEALTHY
        assert "truthy" in (r.message or "")

    @pytest.mark.asyncio
    async def test_connection_error_is_unhealthy_not_raised(self):
        r = await probe_redis(_BrokenRedis())
        assert r.status == HealthStatusValue.UNHEALTHY
        assert "ConnectionRefusedError" in (r.message or "")

    @pytest.mark.asyncio
    async def test_hanging_client_is_bounded_by_timeout(self):
        """A wedged Redis must not hang the health endpoint."""
        r = await probe_redis(_HangingRedis(), timeout=0.05)
        assert r.status == HealthStatusValue.UNHEALTHY
        assert "no response" in (r.message or "")

    @pytest.mark.asyncio
    async def test_real_rediscache_wrapper_is_probed_via_its_client(self):
        """The production regression: ``RedisCache`` holds the raw client at
        ``.client`` and has no ``ping`` of its own. The probe must reach
        through to the real client rather than raising AttributeError.
        """
        r = await probe_redis(_RealShapedRedisCache())
        assert r.status == HealthStatusValue.HEALTHY

    @pytest.mark.asyncio
    async def test_cache_wrapper_with_broken_inner_client_is_unhealthy(self):
        class _BrokenInner:
            def __init__(self) -> None:
                self.client = _BrokenRedis()

        r = await probe_redis(_BrokenInner())
        assert r.status == HealthStatusValue.UNHEALTHY


class TestPostgresProbe:
    @pytest.mark.asyncio
    async def test_healthy_postgres(self):
        r = await probe_postgres(_HealthyPostgres())
        assert r.status == HealthStatusValue.HEALTHY

    @pytest.mark.asyncio
    async def test_auth_failure_is_unhealthy(self):
        r = await probe_postgres(_BrokenPostgres())
        assert r.status == HealthStatusValue.UNHEALTHY
        assert "password authentication failed" in (r.message or "")


class TestQdrantProbe:
    @pytest.mark.asyncio
    async def test_healthy_qdrant(self):
        r = await probe_qdrant(_HealthyQdrant())
        assert r.status == HealthStatusValue.HEALTHY

    @pytest.mark.asyncio
    async def test_client_without_known_methods_is_unhealthy(self):
        class _NoApi:
            pass

        r = await probe_qdrant(_NoApi())
        assert r.status == HealthStatusValue.UNHEALTHY
        assert "AttributeError" in (r.message or "")


class TestNeo4jProbe:
    @pytest.mark.asyncio
    async def test_healthy_neo4j(self):
        r = await probe_neo4j(_HealthyNeo4j())
        assert r.status == HealthStatusValue.HEALTHY

    @pytest.mark.asyncio
    async def test_bolt_reset_is_unhealthy(self):
        r = await probe_neo4j(_BrokenNeo4j())
        assert r.status == HealthStatusValue.UNHEALTHY
        assert "bolt connection reset" in (r.message or "")


# ── collect_component_health ─────────────────────────────────────────────────


class _Container:
    def __init__(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestCollectComponentHealth:
    @pytest.mark.asyncio
    async def test_all_healthy(self):
        c = _Container(
            cache=_HealthyRedis(),
            db_engine=_HealthyPostgres(),
            vector_store=_HealthyQdrant(),
            graph_client=_HealthyNeo4j(),
        )
        comps = await collect_component_health(c)
        assert {x.name for x in comps} == {"redis", "postgres", "qdrant", "neo4j"}
        assert all(x.status == HealthStatusValue.HEALTHY for x in comps)

    @pytest.mark.asyncio
    async def test_one_down_is_reported_by_name(self):
        c = _Container(
            cache=_BrokenRedis(),
            db_engine=_HealthyPostgres(),
            vector_store=_HealthyQdrant(),
            graph_client=_HealthyNeo4j(),
        )
        comps = await collect_component_health(c)
        by_name = {x.name: x for x in comps}
        assert by_name["redis"].status == HealthStatusValue.UNHEALTHY
        assert by_name["postgres"].status == HealthStatusValue.HEALTHY

    @pytest.mark.asyncio
    async def test_unconfigured_components_are_not_omitted(self):
        """The response must describe the whole platform, not just its happy parts."""
        c = _Container(cache=_HealthyRedis())
        comps = await collect_component_health(c)
        assert {x.name for x in comps} == {"redis", "postgres", "qdrant", "neo4j"}
        for x in comps:
            if x.name != "redis":
                assert x.details["configured"] is False

    @pytest.mark.asyncio
    async def test_empty_container_reports_everything_unconfigured(self):
        comps = await collect_component_health(_Container())
        assert len(comps) == 4
        assert all(x.details["configured"] is False for x in comps)

    @pytest.mark.asyncio
    async def test_every_component_has_a_duration(self):
        comps = await collect_component_health(_Container(cache=_HealthyRedis()))
        assert all(hasattr(x, "duration_ms") for x in comps)


# ── overall_status ───────────────────────────────────────────────────────────


def _comp(name: str, status: HealthStatusValue, configured: bool = True) -> ComponentHealth:
    return ComponentHealth(name=name, status=status, duration_ms=1.0, details={"configured": configured})


class TestOverallStatus:
    def test_all_healthy(self):
        comps = [_comp("redis", HealthStatusValue.HEALTHY), _comp("neo4j", HealthStatusValue.HEALTHY)]
        assert overall_status(comps) == HealthStatusValue.HEALTHY

    def test_any_unhealthy_wins(self):
        comps = [
            _comp("redis", HealthStatusValue.HEALTHY),
            _comp("postgres", HealthStatusValue.UNHEALTHY),
        ]
        assert overall_status(comps) == HealthStatusValue.UNHEALTHY

    def test_degraded_without_unhealthy(self):
        comps = [
            _comp("redis", HealthStatusValue.HEALTHY),
            _comp("qdrant", HealthStatusValue.DEGRADED),
        ]
        assert overall_status(comps) == HealthStatusValue.DEGRADED

    def test_unhealthy_beats_degraded(self):
        comps = [
            _comp("a", HealthStatusValue.DEGRADED),
            _comp("b", HealthStatusValue.UNHEALTHY),
        ]
        assert overall_status(comps) == HealthStatusValue.UNHEALTHY

    def test_unconfigured_component_does_not_degrade_platform(self):
        """An optional dependency that was never built is not a fault."""
        comps = [
            _comp("redis", HealthStatusValue.HEALTHY),
            _comp("neo4j", HealthStatusValue.UNHEALTHY, configured=False),
        ]
        assert overall_status(comps) == HealthStatusValue.HEALTHY

    def test_empty_components_is_healthy(self):
        assert overall_status([]) == HealthStatusValue.HEALTHY

    def test_missing_configured_key_treated_as_configured(self):
        """Backward compatibility: components without the flag still count."""
        comps = [ComponentHealth(name="x", status=HealthStatusValue.UNHEALTHY, duration_ms=1.0)]
        assert overall_status(comps) == HealthStatusValue.UNHEALTHY
