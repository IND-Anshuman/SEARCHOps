"""
Regression tests for the backend topology contract.

The LangGraph compiled in graphs/deep_research.py is the source of truth for
node ids. ResearchApplicationService._TOPOLOGY must mirror it exactly, because
_execute_research() looks every streamed delta up by node name and silently
drops any that does not match.
"""

from __future__ import annotations

import pytest

from searchops.application.research_service import _TOPOLOGY
from searchops.orchestration.graphs.deep_research import build_deep_research_graph


def _graph_node_ids() -> set[str]:
    """Node ids registered on the compiled deep-research graph."""
    graph = build_deep_research_graph()
    return set(graph.get_graph().nodes) - {"__start__", "__end__"}


def _topology_ids() -> set[str]:
    return {entry[0] for entry in _TOPOLOGY}


@pytest.mark.unit
def test_topology_matches_graph_node_ids():
    """Every graph node must have a topology entry and vice versa.

    A mismatch means _execute_research() discards that node's delta via
    `if node_name not in nodes_map: continue`, so its latency, token cost and
    output never reach the UI.
    """
    graph_ids = _graph_node_ids()
    topology_ids = _topology_ids()

    assert topology_ids == graph_ids, (
        f"topology/graph drift — "
        f"missing from topology: {sorted(graph_ids - topology_ids)}; "
        f"not in graph: {sorted(topology_ids - graph_ids)}"
    )


@pytest.mark.unit
def test_topology_has_no_duplicate_ids():
    ids = [entry[0] for entry in _TOPOLOGY]

    assert len(ids) == len(set(ids)), "Duplicate node ids would clobber the nodes map"


@pytest.mark.unit
def test_topology_is_imported_from_the_graph_module():
    """_TOPOLOGY must BE the graph's list, not a copy of it.

    Two copies can drift independently; an alias cannot. This is what makes the
    fix structural rather than just "currently correct".
    """
    from searchops.orchestration.graphs.deep_research import GRAPH_TOPOLOGY

    assert _TOPOLOGY is GRAPH_TOPOLOGY


@pytest.mark.unit
def test_build_fails_when_topology_and_nodes_disagree(monkeypatch):
    """The graph build must refuse to compile on topology drift.

    This is the fail-fast guard that replaces the old silent-drop behaviour,
    where a mismatch only surfaced as nodes missing from the API payload.
    """
    from searchops.orchestration.graphs import deep_research as dr_mod

    good = build_deep_research_graph()
    assert set(good.get_graph().nodes) >= {"planner", "report_writer"}

    # Declare a node that the builder never registers.
    monkeypatch.setattr(
        dr_mod,
        "GRAPH_TOPOLOGY",
        [*dr_mod.GRAPH_TOPOLOGY, ("ghost_node", "Ghost", "extract", "ghost.jinja2")],
    )

    with pytest.raises(RuntimeError, match="disagree"):
        build_deep_research_graph()


@pytest.mark.unit
def test_topology_entries_are_well_formed():
    for entry in _TOPOLOGY:
        assert len(entry) == 4, f"Each entry needs (id, label, type, prompt): {entry!r}"
        node_id, label, node_type, prompt = entry
        assert node_id and label and node_type and prompt, f"Incomplete entry: {entry!r}"


@pytest.mark.unit
def test_topology_node_types_are_frontend_renderable():
    """The frontend LangGraphNode.type union must accept every backend type."""
    from pathlib import Path

    # tests/unit/application/test_topology_contract.py -> repo root
    repo_root = Path(__file__).resolve().parents[3]
    frontend_types = repo_root / "frontend" / "src" / "shared" / "types" / "workbench.ts"
    if not frontend_types.exists():
        pytest.skip("frontend types file not present in this checkout")

    allowed = {
        "planner",
        "search",
        "scrape",
        "extract",
        "graph_rag",
        "verify",
        "report",
    }
    for entry in _TOPOLOGY:
        assert entry[2] in allowed, (
            f"Node '{entry[0]}' has type '{entry[2]}' which the frontend LangGraphNode union cannot render"
        )


@pytest.mark.unit
def test_graph_emits_entities_for_the_knowledge_explorer():
    """Some node in the live graph must produce the `entities`/`relations` state.

    The frontend KnowledgeGraphExplorer renders state.entities/relations, which
    only extract_knowledge_node emits. If that node is absent from the compiled
    graph, the KG panel can never show anything but an empty graph.
    """
    graph = build_deep_research_graph()
    node_ids = set(graph.get_graph().nodes)

    assert "extract_evidence" in node_ids, (
        "No node in the deep-research graph emits entities/relations, so the "
        "KnowledgeGraphExplorer is permanently empty"
    )
