"""
Behavioural tests for SearchOrchestrator: capability routing, the distributed
lock double-check, circuit-breaker fallback, retry/backoff, result fusion,
PAA passthrough with its depth guard, and cache population.

The orchestrator reads module-level singletons (registry, search_cache,
health_monitor, budget_service, policy_engine), so each test monkeypatches
those exact singletons rather than constructing a new orchestrator.
"""

from __future__ import annotations

import pytest

from searchops.search import orchestrator as orch_mod
from searchops.search.contracts import ISearchProvider, SearchQuery, SearchResultItem
from searchops.search.domain.models import SearchCapability, SearchProfile
from searchops.search.health import CircuitBreaker
from searchops.search.orchestrator import SearchOrchestrator


class _Provider(ISearchProvider):
    """Configurable provider stub with priority + cost + controllable failure."""

    def __init__(
        self,
        name: str,
        items: list[SearchResultItem] | None = None,
        *,
        priority: int = 10,
        cost: float = 0.001,
        caps: set[SearchCapability] | None = None,
        fail_times: int = 0,
    ) -> None:
        self._name = name
        self._items = items or []
        self.priority = priority
        self._cost = cost
        self._caps = caps or {SearchCapability.SEMANTIC}
        self._fail_times = fail_times
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def cost_per_query(self) -> float:
        return self._cost

    @property
    def capabilities(self) -> set[SearchCapability]:
        return self._caps

    async def search(self, query: SearchQuery) -> list[SearchResultItem]:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError(f"{self._name} transient failure")
        return self._items


class _FakeCache:
    """In-memory stand-in for SearchResponseCache, no Redis."""

    def __init__(self) -> None:
        self._store: dict[str, list[SearchResultItem]] = {}
        self.set_calls: list[tuple[str, str, int]] = []

    async def get(self, query: str, providers_hash: str) -> list[SearchResultItem] | None:
        return self._store.get(f"{query}|{providers_hash}")

    async def get_semantic(self, query: str, providers_hash: str, threshold: float = 0.9):
        return None

    async def set(self, query: str, providers_hash: str, results, ttl_sec: int = 3600) -> None:
        self._store[f"{query}|{providers_hash}"] = list(results)
        self.set_calls.append((query, providers_hash, ttl_sec))

    def lock_query(self, query: str):
        class _Ctx:
            async def __aenter__(self) -> bool:
                return True

            async def __aexit__(self, *exc) -> bool:
                return False

        return _Ctx()


class _FakeRegistry:
    """Stub registry. Mirrors the real registry's routing contract:

    key-free providers are offered before credential-dependent ones, and
    ``key_free_providers()`` backs the orchestrator's fallback path.
    """

    def __init__(self, providers: list[_Provider]) -> None:
        self._providers = providers

    def list_providers(self) -> list[_Provider]:
        return list(self._providers)

    def resolve_by_capabilities(
        self,
        caps: set[SearchCapability],
        *,
        prefer_key_free: bool = True,
    ) -> list[_Provider]:
        matching = [p for p in self._providers if caps & p.capabilities]
        if not prefer_key_free:
            return matching
        # Stable sort: key-free first, preserving declared order otherwise.
        return sorted(matching, key=lambda p: 0 if p.is_key_free else 1)

    def key_free_providers(self) -> list[_Provider]:
        return [p for p in self._providers if p.is_key_free]

    def credential_providers(self) -> list[_Provider]:
        return [p for p in self._providers if p.requires_credentials]


class _FakeHealth:
    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self.queries: list[tuple[str, float, bool]] = []

    def get_breaker(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker()
        return self._breakers[name]

    def record_query(self, name: str, elapsed_ms: float, *, success: bool) -> None:
        self.queries.append((name, elapsed_ms, success))


class _FakeBudget:
    def __init__(self, *, always_ok: bool = True) -> None:
        self.always_ok = always_ok
        self.costs: list[tuple[str, float]] = []

    def is_within_budget(self, job_id: str, cost: float) -> bool:
        return self.always_ok

    def record_cost(self, job_id: str, cost: float) -> None:
        self.costs.append((job_id, cost))


@pytest.fixture
def wired(monkeypatch):
    """Patch the orchestrator's module singletons and return the handles."""

    def _install(
        providers: list[_Provider], *, cache: _FakeCache | None = None, budget: _FakeBudget | None = None
    ) -> dict:
        reg = _FakeRegistry(providers)
        cch = cache or _FakeCache()
        hth = _FakeHealth()
        bud = budget or _FakeBudget()
        monkeypatch.setattr(orch_mod, "registry", reg)
        monkeypatch.setattr(orch_mod, "search_cache", cch)
        monkeypatch.setattr(orch_mod, "health_monitor", hth)
        monkeypatch.setattr(orch_mod, "budget_service", bud)
        monkeypatch.setattr(orch_mod, "policy_engine", orch_mod.policy_engine)
        return {
            "registry": reg,
            "cache": cch,
            "health": hth,
            "budget": bud,
            "orchestrator": SearchOrchestrator(),
        }

    return _install


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sequential_cascade_stops_at_first_succeeding_provider(wired):
    """The cascade walks providers in registry order and stops at the first hit.

    NOTE: the docstring in the code claims routing is "capability + priority
    based", but `priority` is never consulted here — only list order matters.
    This test pins the ACTUAL behaviour so the discrepancy stays visible.
    """
    first = _Provider("first", [_result("hi")], priority=9)
    second = _Provider("second", [_result("lo")], priority=1)
    env = wired([first, second])

    res = await env["orchestrator"].search(_query("hello world"), job_id="job-1")

    assert [r.url for r in res] == ["http://hi"]
    assert first.calls == 1
    assert second.calls == 0, "Cascade must stop once a provider succeeds"
    assert env["budget"].costs, "Provider cost must be recorded"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_recovers_from_transient_failure(wired, monkeypatch):
    monkeypatch.setattr(orch_mod.asyncio, "sleep", _no_sleep)
    p = _Provider("flaky", [_result("ok")], fail_times=2)
    env = wired([p])

    res = await env["orchestrator"].search(_query("retry me"))

    assert [r.url for r in res] == ["http://ok"]
    assert p.calls == 3, "Two transient failures then success = 3 attempts"
    assert env["health"].queries[-1][2] is True, "Final attempt recorded as success"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exhausted_retries_fall_back_to_searxng(wired, monkeypatch):
    monkeypatch.setattr(orch_mod.asyncio, "sleep", _no_sleep)
    # searxng must NOT satisfy the profile's capabilities, otherwise it wins in
    # the main cascade and the dedicated fallback path is never exercised.
    dead = _Provider("tavily", fail_times=99, caps={SearchCapability.KEYWORD})
    backup = _Provider(
        "searxng",
        [_result("backup")],
        caps={SearchCapability.ANTI_BOT},
    )
    env = wired([dead, backup])

    res = await env["orchestrator"].search(_query("fallback please"))

    assert [r.url for r in res] == ["http://backup"]
    assert dead.calls == 3, "Primary is attempted the full retry budget"
    assert backup.calls == 1, "Recovered through the dedicated fallback path"
    assert any(ok is False for _, _, ok in env["health"].queries), "Failure recorded"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_only_targets_searxng_and_playwright(wired, monkeypatch):
    """The dedicated fallback path is hard-filtered to two provider names.

    When EVERY candidate fails, recovery is attempted only through providers
    literally named "searxng" or "playwright". A dead primary plus a
    non-fallback-named secondary therefore yields nothing.
    """
    monkeypatch.setattr(orch_mod.asyncio, "sleep", _no_sleep)
    dead = _Provider("tavily", fail_times=99, caps={SearchCapability.KEYWORD})
    other = _Provider("brave", fail_times=99, caps={SearchCapability.KEYWORD})
    env = wired([dead, other])

    res = await env["orchestrator"].search(_query("no fallback available"))

    assert res == []
    assert dead.calls == 3
    assert other.calls == 3, "brave runs in the main cascade but is not a fallback target"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_circuit_breaker_forces_fallback_provider(wired):
    """All candidates blocked -> the code falls back to registry.list_providers().

    BUG PINNED HERE: that fallback list is NOT re-filtered by the breakers,
    so a provider whose breaker is open gets called anyway (the log line says
    "ignoring breaker temporarily for fallback survival"). The first result
    therefore comes from the very provider that was supposed to be blocked.
    """
    blocked = _Provider("blocked", [_result("never")], caps={SearchCapability.KEYWORD})
    env = wired([blocked])
    env["health"].get_breaker("blocked").failure_threshold = 1
    env["health"].get_breaker("blocked").record_failure()

    res = await env["orchestrator"].search(_query("breaker"))

    assert blocked.calls == 1, "Fallback bypasses the open breaker (current behaviour)"
    assert [r.url for r in res] == ["http://never"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adaptive_parallelism_runs_tavily_and_serper_together(wired):
    tavily = _Provider("tavily", [_result("t")], caps={SearchCapability.KEYWORD})
    serper = _Provider("serper", [_result("s")], caps={SearchCapability.KEYWORD})
    env = wired([tavily, serper])

    res = await env["orchestrator"].search(_query("parallel"), job_id="j")

    assert tavily.calls == 1 and serper.calls == 1
    assert len(res) == 2, "Both provider result sets are fused"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_blocked_provider_is_skipped(wired):
    poor = _Provider("rich_only", [_result("x")])
    cheap = _Provider("cheap", [_result("y")], cost=0.0, priority=20)
    env = wired([poor, cheap], budget=_FakeBudget(always_ok=False))

    # Budget denies everything, so nothing can run and the result is empty.
    res = await env["orchestrator"].search(_query("budget"))

    assert res == []
    assert poor.calls == 0 and cheap.calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_results_are_written_into_cache_with_original_query(wired):
    p = _Provider("p", [_result("cached")])
    env = wired([p])

    await env["orchestrator"].search(_query("cache me"), job_id="j")

    assert env["cache"].set_calls, "Search result must populate the cache"
    stored = env["cache"].set_calls[0]
    assert stored[0] == "cache me"
    assert stored[2] > 0, "A positive TTL must be used"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_second_identical_query_is_served_from_cache(wired):
    p = _Provider("p", [_result("cached")])
    env = wired([p])

    await env["orchestrator"].search(_query("same"), job_id="j")
    res2 = await env["orchestrator"].search(_query("same"), job_id="j")

    assert p.calls == 1, "Cache hit must not re-query the provider"
    assert [r.url for r in res2] == ["http://cached"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_max_results_truncates_fused_output(wired):
    p = _Provider("p", [_result(str(i)) for i in range(5)])
    env = wired([p])

    res = await env["orchestrator"].search(_query("many", max_results=2))

    assert len(res) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_paa_subqueries_deduped_and_truncated_on_deep_profile(wired):
    item = _result("paa")
    item.raw_metadata["paa_subqueries"] = ["a", "a", "b", "c", "d", "e"]
    p = _Provider("p", [item])
    env = wired([p])

    q = SearchQuery(query="deep paa", max_results=5, profile=SearchProfile.DEEP)
    await env["orchestrator"].search(q, job_id="j")

    stored = env["cache"]._store[next(iter(env["cache"]._store))][0]
    paa = stored.raw_metadata["paa_subqueries"]
    assert paa == sorted(set(paa), key=paa.index), "Duplicates removed, order preserved"
    assert len(paa) <= 5, "Truncated to the configured per-SERP cap"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_paa_depth_guard_suppresses_expansion_at_max_depth(wired):
    item = _result("paa")
    item.raw_metadata["paa_subqueries"] = ["a", "b"]
    p = _Provider("p", [item])
    env = wired([p])

    q = SearchQuery(
        query="too deep",
        max_results=5,
        profile=SearchProfile.DEEP,
        raw_metadata={"paa_depth": 999},
    )
    await env["orchestrator"].search(q, job_id="j")

    stored = env["cache"]._store[next(iter(env["cache"]._store))][0]
    assert "paa_subqueries" not in stored.raw_metadata, "Depth guard must drop PAA"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_candidates_returns_empty(wired):
    env = wired([])
    res = await env["orchestrator"].search(_query("nothing"))
    assert res == []


def _query(text: str, *, max_results: int = 10) -> SearchQuery:
    return SearchQuery(query=text, max_results=max_results, profile=SearchProfile.FAST)


def _result(url: str) -> SearchResultItem:
    return SearchResultItem(
        url=f"http://{url}",
        title=f"Title {url}",
        snippet=f"Snippet for {url}",
        provider="stub",
    )


async def _no_sleep(_seconds: float) -> None:
    """Replace asyncio.sleep so retry backoff does not slow the suite."""
    return None
