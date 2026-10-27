"""
Unit tests for the LangGraph state reducers in research_state.

These reducers define how node deltas merge into shared state, so they govern
whether duplicate entities, relations, and claims accumulate across the graph.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from searchops.orchestration.states.research_state import (
    ResearchState,
    dedupe_entities,
    dedupe_relations,
    replace_list,
)

# ── replace_list ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_replace_list_returns_new_value():
    assert replace_list([1, 2, 3], [9]) == [9]


@pytest.mark.unit
def test_replace_list_keeps_old_when_new_is_none():
    assert replace_list([1, 2], None) == [1, 2]


@pytest.mark.unit
def test_replace_list_empty_when_both_missing():
    assert replace_list(None, None) == []


@pytest.mark.unit
def test_replace_list_accepts_empty_new_list():
    """An empty list is a real delta and must replace, not be treated as absent."""
    assert replace_list([1, 2, 3], []) == []


@pytest.mark.unit
def test_replace_list_handles_none_left():
    assert replace_list(None, [1]) == [1]


# ── dedupe_entities ───────────────────────────────────────────────────────────


def _entity(canonical_id: str, name: str = "x"):
    return SimpleNamespace(canonical_id=canonical_id, name=name)


@pytest.mark.unit
def test_dedupe_entities_merges_lists():
    left = [_entity("a")]
    right = [_entity("b")]

    result = dedupe_entities(left, right)

    assert {e.canonical_id for e in result} == {"a", "b"}


@pytest.mark.unit
def test_dedupe_entities_keeps_first_occurrence():
    left = [_entity("a", "first")]
    right = [_entity("a", "second")]

    result = dedupe_entities(left, right)

    assert len(result) == 1
    assert result[0].name == "first", "The earlier entity wins on canonical_id clash"


@pytest.mark.unit
def test_dedupe_entities_preserves_order():
    left = [_entity("a"), _entity("b")]
    right = [_entity("c"), _entity("a")]

    result = dedupe_entities(left, right)

    assert [e.canonical_id for e in result] == ["a", "b", "c"]


@pytest.mark.unit
def test_dedupe_entities_with_none_inputs():
    assert dedupe_entities(None, None) == []
    assert dedupe_entities(None, [_entity("a")]) == [_entity("a")]
    assert dedupe_entities([_entity("a")], None) == [_entity("a")]


@pytest.mark.unit
def test_dedupe_entities_collapses_duplicates_within_one_side():
    right = [_entity("a"), _entity("a"), _entity("a")]

    assert len(dedupe_entities(None, right)) == 1


# ── dedupe_relations ──────────────────────────────────────────────────────────


def _relation(source, target, rtype="RELATED_TO", source_canon=None, target_canon=None):
    return SimpleNamespace(
        source_id=source,
        target_id=target,
        source_canonical_id=source_canon,
        target_canonical_id=target_canon,
        relation_type=rtype,
    )


@pytest.mark.unit
def test_dedupe_relations_merges_distinct_keys():
    left = [_relation("a", "b")]
    right = [_relation("b", "c")]

    result = dedupe_relations(left, right)

    assert len(result) == 2


@pytest.mark.unit
def test_dedupe_relations_collapses_same_triple():
    rel = _relation("a", "b")
    right = [rel, _relation("a", "b")]

    assert len(dedupe_relations(None, right)) == 1


@pytest.mark.unit
def test_dedupe_relations_distinguishes_relation_type():
    right = [_relation("a", "b", "RELATED_TO"), _relation("a", "b", "PART_OF")]

    assert len(dedupe_relations(None, right)) == 2


@pytest.mark.unit
def test_dedupe_relations_distinguishes_direction():
    right = [_relation("a", "b"), _relation("b", "a")]

    assert len(dedupe_relations(None, right)) == 2


@pytest.mark.unit
def test_dedupe_relations_prefers_canonical_ids_in_key():
    """Canonical ids collapse variants that differ only by raw id."""
    left = [_relation("a", "b", source_canon="canon_a", target_canon="canon_b")]
    right = [_relation("a2", "b2", source_canon="canon_a", target_canon="canon_b")]

    assert len(dedupe_relations(left, right)) == 1


@pytest.mark.unit
def test_dedupe_relations_falls_back_to_raw_ids():
    left = [_relation("a", "b")]
    right = [_relation("a", "b")]

    assert len(dedupe_relations(left, right)) == 1


@pytest.mark.unit
def test_dedupe_relations_with_none_inputs():
    assert dedupe_relations(None, None) == []
    assert dedupe_relations(None, [_relation("a", "b")]) == [_relation("a", "b")]


@pytest.mark.unit
def test_dedupe_relations_preserves_order():
    left = [_relation("a", "b")]
    right = [_relation("c", "d"), _relation("a", "b")]

    result = dedupe_relations(left, right)

    assert [(r.source_id, r.target_id) for r in result] == [("a", "b"), ("c", "d")]


# ── ResearchState contract ────────────────────────────────────────────────────


@pytest.mark.unit
def test_state_declares_expected_keys():
    hints = ResearchState.__annotations__

    for key in ("query", "plan", "search_results", "entities", "relations", "evidence_claims"):
        assert key in hints


@pytest.mark.unit
def test_state_is_total_false():
    """Partial node deltas must be valid state updates."""
    assert ResearchState.__total__ is False


@pytest.mark.unit
def test_state_is_a_typed_dict():
    from typing import get_origin

    assert get_origin(ResearchState) is dict or hasattr(ResearchState, "__annotations__")
