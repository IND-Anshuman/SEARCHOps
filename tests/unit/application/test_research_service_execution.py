"""
Unit tests for ResearchApplicationService execution internals.

Covers the LangGraph streaming loop (node completion, latency, token/cost
accumulation, entity/relation/chunk/citation harvesting, report capture),
the terminal COMPLETED and FAILED transitions, and the pure topology helpers.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from searchops.application.job_state_manager import JobStateManager
from searchops.application.research_service import (
    ResearchApplicationService,
    ResearchJobStatus,
    _build_initial_nodes,
    _extract_token_usage,
    _next_pending_node,
)
from searchops.core.context.execution import ExecutionContext
from searchops.infrastructure.events.bus import RedisEventBus
from searchops.orchestration.passages import Passage

# ── Test doubles ──────────────────────────────────────────────────────────────


class MemoryCache:
    """Minimal ICache-shaped double backed by a dict."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.client = MagicMock()

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: dict, ttl_seconds: int = 3600) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class RecordingJobStateManager(JobStateManager):
    """JobStateManager that records every update instead of hitting Redis."""

    def __init__(self) -> None:
        cache = MemoryCache()
        bus = MagicMock(spec=RedisEventBus)
        bus.publish = AsyncMock()
        super().__init__(cache=cache, event_bus=bus)
        self.updates: list[dict] = []
        self.bus = bus  # exposed so tests can inspect published job state

    async def update_job(self, job_id: str, updates: dict) -> None:
        self.updates.append(updates)


class FakeAIMessage:
    """Stands in for a LangChain AIMessage carrying usage metadata."""

    def __init__(self, input_tokens: int, output_tokens: int, model: str | None = None) -> None:
        self.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        self.response_metadata = {"model_name": model} if model else {}


class FakeGraph:
    """Graph double whose astream() yields a scripted sequence of updates."""

    def __init__(self, updates: list[dict], *, raise_error: Exception | None = None) -> None:
        self._updates = updates
        self._raise_error = raise_error

    def astream(self, initial_state, stream_mode: str = "updates"):
        async def _gen():
            for chunk in self._updates:
                yield chunk
            if self._raise_error is not None:
                raise self._raise_error

        return _gen()


def _make_service(graph: FakeGraph) -> tuple[ResearchApplicationService, RecordingJobStateManager]:
    cache = MemoryCache()
    jsm = RecordingJobStateManager()
    service = ResearchApplicationService(cache=cache, job_state_manager=jsm)
    service._build_graph = lambda: graph  # type: ignore[method-assign]
    return service, jsm


def _search_item(url: str, title: str = "Doc", snippet: str = "Snip") -> SimpleNamespace:
    return SimpleNamespace(url=url, title=title, snippet=snippet, score=0.9)


def _entity(eid: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=eid,
        canonical_id=f"canon_{eid}",
        name=f"Entity {eid}",
        entity_type="ORG",
        description="desc",
        confidence=0.95,
    )


def _relation(rid: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid,
        source_id="a",
        target_id="b",
        source_canonical_id="canon_a",
        target_canonical_id="canon_b",
        relation_type="RELATED_TO",
        description="rel desc",
    )


# ── Tests: the streaming loop ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_node_completion_records_status_and_latency():
    graph = FakeGraph([{"planner": {"messages": [], "plan": {"steps": ["a", "b"]}}}])
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    # The final update is the terminal COMPLETED write (which stamps every node
    # "completed"), so the per-node measurements are checked on the first one.
    first = jsm.updates[0]
    planner = next(n for n in first["nodes"] if n["id"] == "planner")
    assert planner["status"] == "completed"
    assert isinstance(planner["latency_ms"], int)
    # "messages" is deliberately excluded from the captured payload.
    assert planner["output_payload"] == {"plan": "{'steps': ['a', 'b']}"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_node_in_stream_is_ignored():
    graph = FakeGraph([{"not_in_topology": {"messages": []}}])
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    # No node should have been completed by an unknown node name. The terminal
    # write force-completes everything, so check the FIRST progress update.
    completed = [n for n in jsm.updates[0]["nodes"] if n["status"] == "completed"]
    assert completed == [], "Unknown nodes must not complete topology nodes"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_token_usage_is_accumulated_from_message_metadata():
    graph = FakeGraph(
        [
            {
                "planner": {
                    "messages": [FakeAIMessage(100, 50, "gpt-4o-mini")],
                }
            },
            {
                "search": {
                    "messages": [FakeAIMessage(200, 75, "gpt-4o-mini")],
                }
            },
        ]
    )
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    assert jsm.updates[-1]["token_used"] == 425, "Input+output tokens summed across messages"
    assert jsm.updates[-1]["cost_current"] > 0, "Cost must be derived from the pricing table"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entities_relations_chunks_and_citations_are_harvested():
    passage = Passage(id="p_a_0", url="https://a.test", title="Doc A", text="Qubits hold quantum state for q.")
    graph = FakeGraph(
        [
            {
                "extract_evidence": {
                    "messages": [],
                    "entities": [_entity("e1")],
                    "relations": [_relation("r1")],
                    "passages": [passage],
                }
            },
            {
                "report_writer": {
                    "final_report": "# R\n\nQubits [1].",
                    "report_sources": [{"n": 1, "url": "https://a.test", "title": "Doc A"}],
                    "citations": ["https://a.test"],
                }
            },
        ]
    )
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    final = jsm.updates[-1]
    assert final["entities"][0]["canonical_id"] == "canon_e1"
    assert final["relations"][0]["relation_type"] == "RELATED_TO"
    # Chunks are the passages the run read, not search snippets.
    assert final["chunks"][0]["source_url"] == "https://a.test"
    assert final["chunks"][0]["chunk_preview"].startswith("Qubits")
    assert final["citations"] == ["https://a.test"]
    assert final["report_sources"][0]["n"] == 1
    assert final["entity_count"] == 1
    assert final["source_count"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chunk_scores_are_measured_not_defaulted():
    passages = [
        Passage(id="p1", url="https://b.test", text="Quantum error correction protects qubits from noise."),
        Passage(id="p2", url="https://b.test", text="An unrelated paragraph about cooking pasta at home."),
    ]
    graph = FakeGraph([{"extract_evidence": {"messages": [], "passages": passages}}])
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "quantum error correction", "standard", 5, ExecutionContext.create())

    chunks = {c["id"]: c for c in jsm.updates[-1]["chunks"]}
    assert chunks["p1"]["similarity_score"] == 1.0
    assert chunks["p2"]["similarity_score"] == 0.0
    assert chunks["p1"]["token_count"] > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_search_hits_are_not_reported_as_citations():
    graph = FakeGraph(
        [
            {
                "search": {
                    "messages": [],
                    "search_results": [_search_item("https://dup.test"), _search_item("https://x.test")],
                }
            }
        ]
    )
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    # A page the run never cited is not a citation.
    assert jsm.updates[-1]["citations"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_report_writer_output_is_captured_as_final_report():
    graph = FakeGraph(
        [
            {
                "report_writer": {
                    "messages": [],
                    "final_report": "REAL LLM REPORT BODY",
                    "citations": ["https://cited.test"],
                }
            }
        ]
    )
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    final = jsm.updates[-1]
    assert final["final_report"] == "REAL LLM REPORT BODY"
    assert "https://cited.test" in final["citations"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_final_report_from_non_report_node_is_not_captured():
    graph = FakeGraph([{"planner": {"messages": [], "final_report": "IGNORED"}}])
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    assert jsm.updates[-1]["final_report"] == "", "Only report_writer may set the report"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_terminal_state_is_completed_with_full_progress_and_budgets():
    graph = FakeGraph([{"planner": {"messages": []}}])
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    final = jsm.updates[-1]
    assert final["status"] == ResearchJobStatus.COMPLETED
    assert final["progress"] == 100
    assert all(n["status"] == "completed" for n in final["nodes"])
    assert final["token_budget"] == 150000
    assert final["cost_budget"] == 5.0
    assert final["completed_at"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_graph_failure_marks_job_failed_and_records_error():
    graph = FakeGraph([], raise_error=RuntimeError("langgraph exploded"))
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    final = jsm.updates[-1]
    assert final["status"] == ResearchJobStatus.FAILED
    assert final["error"] == "langgraph exploded"
    assert final["progress"] == 100
    assert any(entry["event_type"] == "JOB_FAILED" for entry in final["logs"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_progress_is_monotonic_across_updates():
    graph = FakeGraph(
        [
            {"planner": {"messages": []}},
            {"search": {"messages": []}},
            {"scrape": {"messages": []}},
        ]
    )
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    progress = [u["progress"] for u in jsm.updates if "progress" in u]
    assert progress == sorted(progress), "Progress must never go backwards"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_next_pending_node_is_marked_running_and_logged():
    graph = FakeGraph([{"planner": {"messages": []}}])
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    # Check the first progress update; the terminal write clears "running".
    first = jsm.updates[0]
    running = [n for n in first["nodes"] if n["status"] == "running"]
    assert len(running) == 1, "Exactly one node should be in flight"
    assert running[0]["id"] == "search", "Next pending node after planner is search"
    assert any("START" in entry["event_type"] for entry in first["logs"])


# ── Tests: pure helpers ───────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_token_usage_survives_none_delta():
    """LangGraph emits None for a node that returns an empty dict.

    gap_analyst_node returns {} on its no-gaps path, and _execute_research()
    feeds the resulting delta straight into _extract_token_usage(). A None
    delta must be treated as "no usage" rather than raising AttributeError.
    """
    tokens, cost = _extract_token_usage(None)

    assert (tokens, cost) == (0, 0.0)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_token_usage_handles_message_without_response_metadata():
    class Bare:
        usage_metadata: ClassVar[dict[str, int]] = {"input_tokens": 5, "output_tokens": 5}
        response_metadata = None

    tokens, cost = _extract_token_usage({"messages": [Bare()]})

    assert tokens == 10
    assert cost > 0, "A missing model name falls back to the conservative default price"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_research_survives_node_returning_empty_dict(monkeypatch):
    """A node returning {} must not crash the streaming loop.

    Regression: gap_analyst_node returns {} when it has nothing to expand,
    LangGraph surfaces that as a None delta, and _execute_research() called
    state_delta.get(...) on it — raising 'NoneType' object has no attribute
    'get' and failing the whole job after the first reflection loop.
    """
    graph = FakeGraph([{"gap_analyst": None}])
    service, jsm = _make_service(graph)

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())

    assert jsm.updates[-1]["status"] != ResearchJobStatus.FAILED, "An empty node delta must not fail the job"


@pytest.mark.unit
def test_build_initial_nodes_are_all_pending():
    nodes = _build_initial_nodes()

    assert nodes, "Topology must define nodes"
    assert all(n["status"] == "pending" for n in nodes)
    assert all(n["latency_ms"] == 0 and n["token_cost"] == 0.0 for n in nodes)
    assert all({"id", "label", "type", "prompt"} <= set(n) for n in nodes)


@pytest.mark.unit
def test_next_pending_node_returns_first_in_topology_order():
    nodes = {n["id"]: n for n in _build_initial_nodes()}
    first_id = nodes[next(iter(nodes))]["id"]

    assert _next_pending_node(nodes) == first_id


@pytest.mark.unit
def test_next_pending_node_returns_none_when_all_done():
    nodes = {n["id"]: {**n, "status": "completed"} for n in _build_initial_nodes()}

    assert _next_pending_node(nodes) is None


@pytest.mark.unit
def test_next_pending_node_skips_completed_prefix():
    nodes = {n["id"]: n for n in _build_initial_nodes()}
    order = list(nodes)
    nodes[order[0]]["status"] = "completed"

    assert _next_pending_node(nodes) == order[1]


@pytest.mark.unit
def test_extract_token_usage_sums_tokens_and_costs():
    tokens, cost = _extract_token_usage(
        {"messages": [FakeAIMessage(100, 50, "gpt-4o-mini"), FakeAIMessage(10, 5, "gpt-4o-mini")]}
    )

    assert tokens == 165
    assert cost > 0


@pytest.mark.unit
def test_extract_token_usage_without_usage_metadata_is_zero():
    class Bare:
        usage_metadata = None

    tokens, cost = _extract_token_usage({"messages": [Bare()]})

    assert (tokens, cost) == (0, 0.0)


@pytest.mark.unit
def test_extract_token_usage_ignores_missing_messages_key():
    tokens, cost = _extract_token_usage({})

    assert (tokens, cost) == (0, 0.0)


@pytest.mark.unit
def test_extract_token_usage_uses_fallback_model_when_name_absent():
    tokens, cost = _extract_token_usage({"messages": [FakeAIMessage(100, 50)]})

    assert tokens == 150
    assert cost > 0, "Fallback model must still be priced, not silently free"


# ── Tests: research mode plumbing ─────────────────────────────────────────


class TestStartResearchMode:
    """``mode`` selects the run shape and its plan is stored on the job."""

    @pytest.mark.asyncio
    async def test_default_mode_is_precision(self) -> None:
        cache = MemoryCache()
        jsm = RecordingJobStateManager()
        service = ResearchApplicationService(cache=cache, job_state_manager=jsm)

        await service.start_research(query="what is a vector database")

        # create_job publishes the initial state on the event bus.
        published = jsm.bus.publish.call_args[0][1]
        assert published["mode"] == "precision"

    @pytest.mark.asyncio
    async def test_corpus_mode_stores_a_corpus_plan(self) -> None:
        cache = MemoryCache()
        jsm = RecordingJobStateManager()
        service = ResearchApplicationService(cache=cache, job_state_manager=jsm)

        await service.start_research(query="collect dataset about vector dbs", mode="corpus")

        published = jsm.bus.publish.call_args[0][1]
        assert published["mode"] == "corpus"
        plan = published["scrape_plan"]
        assert plan["relevance_filter"] is True
        assert plan["max_depth"] == 3
        assert plan["report_shape"] == "dataset"

    @pytest.mark.asyncio
    async def test_precision_plan_enables_the_filter(self) -> None:
        cache = MemoryCache()
        jsm = RecordingJobStateManager()
        service = ResearchApplicationService(cache=cache, job_state_manager=jsm)

        await service.start_research(query="what is a vector database", mode="precision")

        published = jsm.bus.publish.call_args[0][1]
        assert published["scrape_plan"]["relevance_filter"] is True
        assert published["scrape_plan"]["report_shape"] == "narrative"

    @pytest.mark.asyncio
    async def test_max_sources_flows_into_the_plan(self) -> None:
        cache = MemoryCache()
        jsm = RecordingJobStateManager()
        service = ResearchApplicationService(cache=cache, job_state_manager=jsm)

        await service.start_research(query="collect a dataset here", mode="corpus", max_sources=25)

        published = jsm.bus.publish.call_args[0][1]
        assert published["scrape_plan"]["max_pages"] == 25

    @pytest.mark.asyncio
    async def test_invalid_mode_is_rejected_before_any_job_exists(self) -> None:
        cache = MemoryCache()
        jsm = RecordingJobStateManager()
        service = ResearchApplicationService(cache=cache, job_state_manager=jsm)

        with pytest.raises(ValueError):
            await service.start_research(query="something to look up", mode="bogus")

        # No job was created, so nothing was ever published.
        jsm.bus.publish.assert_not_called()
