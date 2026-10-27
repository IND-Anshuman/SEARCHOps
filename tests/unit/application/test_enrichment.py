"""Tests for the enrichment pass.

The contract under test: one call produces four artifacts at real depth, and
nothing is ever invented. The parser must survive the shapes a real model
emits — fenced JSON, truncated JSON, prose fallback, and shallow fragments —
and must mark shallow output rather than shipping it silently.
"""

from __future__ import annotations

from searchops.application.exports.enrichment import (
    EnrichmentResult,
    build_corpus_text,
    parse_enrichment,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

CLEAN = """
{
  "glossary": [
    {"term": "Surface code", "entity_type": "Technology",
     "definition": "A two-dimensional quantum error-correcting code laid out on a lattice of physical qubits. It is the leading architecture for fault-tolerant quantum computing because its stabiliser checks require only nearest-neighbour interactions. The logical error rate falls exponentially with code distance.",
     "related": ["Transmon qubit", "Fault-tolerance threshold"]},
    {"term": "Transmon qubit", "entity_type": "Technology",
     "definition": "A superconducting charge qubit engineered to be insensitive to charge noise. It is the workhorse of current superconducting quantum processors and the physical substrate on which surface codes are implemented.",
     "related": ["Surface code"]}
  ],
  "study_guide": [
    {"question": "Why does the surface code scale logarithmically with code distance?",
     "answer": "Because the logical error rate falls exponentially with distance, so the number of physical qubits needed to reach a target logical error grows only logarithmically with the distance required.",
     "section": "Core Findings", "tests": "mechanism"},
    {"question": "What is the fault-tolerance threshold and why does it matter?",
     "answer": "It is the physical error rate below which error correction wins. Above it, adding more qubits makes things worse rather than better.",
     "section": "Core Findings", "tests": "concept"}
  ],
  "next_steps": [
    {"gap": "No measured data on thermal dissipation at the 15-millikelvin stage",
     "why_it_matters": "Without it, a reader cannot judge whether cryogenic control routing is the binding constraint on processor size.",
     "suggested_query": "cryogenic interconnect thermal dissipation 15mK quantum control",
     "prerequisites": ["Transmon qubit"], "priority": 1}
  ],
  "faq": [
    {"q": "What is a surface code?", "a": "A 2D quantum error-correcting code laid out on a lattice of physical qubits."}
  ]
}
"""

FENCED = """
Here are the study aids:

```json
{
  "glossary": [{"term": "Surface code", "entity_type": "Technology",
    "definition": "A two-dimensional quantum error-correcting code laid out on a lattice of physical qubits, requiring only nearest-neighbour interactions between them.",
    "related": []}],
  "study_guide": [{"question": "What is a surface code?", "answer": "A 2D quantum error-correcting code on a lattice of physical qubits.", "section": "Introduction", "tests": "concept"}],
  "next_steps": [],
  "faq": []
}
```
"""

TRUNCATED = """
{
  "glossary": [
    {"term": "Surface code", "entity_type": "Technology",
     "definition": "A two-dimensional quantum error-correcting code laid out on a lattice of physical qubits requiring only nearest-neighbour interactions.",
     "related": []}
  ],
  "study_guide": [
    {"question": "What is a surface code?", "answer": "A 2D quantum error-correcting code on a lattice of physical qubits.", "section": "Intro"
"""

PROSE = """
1. What is a surface code?
2. Why does the surface code scale with code distance?
3. What limits cryogenic control routing?

Here are the answers:
- A surface code is a two-dimensional quantum error-correcting code laid out on a lattice of physical qubits.
"""

SHALLOW = """
{
  "glossary": [{"term": "Surface code", "entity_type": "Technology", "definition": "A quantum code.", "related": []}],
  "study_guide": [{"question": "What is a surface code?", "answer": "A quantum code.", "section": "", "tests": "concept"}],
  "next_steps": [{"gap": "Cost per logical qubit", "why_it_matters": "Unknown.", "suggested_query": "qec cost per qubit", "prerequisites": [], "priority": 2}],
  "faq": [{"q": "What is a surface code?", "a": "A quantum code."}]
}
"""


# ── The happy path ───────────────────────────────────────────────────────────


def test_clean_envelope_produces_all_four_artifacts():
    result = parse_enrichment(CLEAN)
    assert len(result.glossary) == 2
    assert len(result.study_guide) == 2
    assert len(result.next_steps) == 1
    assert len(result.faq) == 1
    assert result.thin_fields == []
    assert result.note == ""


def test_definitions_are_real_not_one_liners():
    """The whole point of the module: a fragment must not pass as a definition."""
    result = parse_enrichment(CLEAN)
    for term in result.glossary:
        assert len(term.definition) >= 60, f"definition too short: {term.term}"
    assert result.glossary[0].related == ["Transmon qubit", "Fault-tolerance threshold"]


def test_answers_are_real_not_fragments():
    result = parse_enrichment(CLEAN)
    for q in result.study_guide:
        assert len(q.answer) >= 40, f"answer too short: {q.question}"


def test_gap_carries_context_not_just_a_string():
    result = parse_enrichment(CLEAN)
    gap = result.next_steps[0]
    assert len(gap.why_it_matters) >= 30
    assert gap.suggested_query
    assert gap.prerequisites == ["Transmon qubit"]


def test_gap_priority_is_clamped_to_range():
    raw = '{"next_steps": [{"gap": "x", "why_it_matters": "because it matters greatly here", "suggested_query": "q", "priority": 99}]}'
    result = parse_enrichment(raw)
    assert result.next_steps[0].priority == 5


def test_gaps_are_sorted_by_priority():
    raw = (
        '{"next_steps": ['
        '{"gap": "low", "why_it_matters": "matters somewhat here", "suggested_query": "a", "priority": 4},'
        '{"gap": "high", "why_it_matters": "matters a great deal here", "suggested_query": "b", "priority": 1}'
        "]}"
    )
    result = parse_enrichment(raw)
    assert [g.priority for g in result.next_steps] == [1, 4]


# ── Salvage tiers ────────────────────────────────────────────────────────────


def test_fenced_json_is_stripped_and_parsed():
    result = parse_enrichment(FENCED)
    assert len(result.glossary) == 1
    assert len(result.study_guide) == 1
    assert result.next_steps == []


def test_truncated_envelope_salvages_what_survived():
    """A truncated envelope must not discard the arrays that closed."""
    result = parse_enrichment(TRUNCATED)
    assert len(result.glossary) == 1
    assert len(result.study_guide) == 1


def test_prose_fallback_recovers_questions_verbatim():
    """The shape that made eight real questions get thrown away."""
    result = parse_enrichment(PROSE)
    assert len(result.study_guide) == 3
    assert result.study_guide[0].question == "What is a surface code?"
    # Answers arrive empty and are filled by a later pass — flagged, not silent.
    assert "study_guide" in result.thin_fields


def test_prose_questions_must_look_like_questions():
    """Headings and commentary must not become questions."""
    result = parse_enrichment("**Questions**\n\nSurface codes are important.\n")
    assert result.study_guide == []


# ── Honesty contract ─────────────────────────────────────────────────────────


def test_shallow_output_is_flagged_not_silently_shipped():
    result = parse_enrichment(SHALLOW)
    assert len(result.glossary) == 1
    assert set(result.thin_fields) == {"glossary", "study_guide", "next_steps", "faq"}
    assert "shallow" in result.note


def test_empty_input_returns_honest_note():
    result = parse_enrichment("")
    assert result.glossary == []
    assert "no output" in result.note.lower()


def test_garbage_returns_unrecoverable_note():
    result = parse_enrichment("I cannot help with that request.")
    assert result.glossary == []
    assert result.study_guide == []
    assert result.note


def test_empty_field_is_reported_not_invented():
    """A valid envelope with empty arrays is recovered, not 'unrecoverable'."""
    result = parse_enrichment('{"glossary": [], "study_guide": [], "next_steps": [], "faq": []}')
    assert "did not support" in result.note
    assert "could not parse" not in result.note.lower()


def test_duplicate_terms_are_deduplicated():
    raw = (
        '{"glossary": ['
        '{"term": "Surface code", "definition": "A two-dimensional quantum error-correcting code on a lattice of physical qubits.", "related": []},'
        '{"term": "surface-code", "definition": "A two-dimensional quantum error-correcting code on a lattice of physical qubits.", "related": []}'
        "]}"
    )
    result = parse_enrichment(raw)
    assert len(result.glossary) == 1


def test_duplicate_questions_are_deduplicated():
    raw = (
        '{"study_guide": ['
        '{"question": "What is a surface code?", "answer": "A two-dimensional quantum error-correcting code on a lattice of physical qubits.", "tests": "concept"},'
        '{"question": "What is a surface code?", "answer": "A two-dimensional quantum error-correcting code on a lattice of physical qubits.", "tests": "concept"}'
        "]}"
    )
    result = parse_enrichment(raw)
    assert len(result.study_guide) == 1


# ── Corpus bounding ──────────────────────────────────────────────────────────


def test_corpus_is_bounded_so_the_contract_survives():
    """The router truncates from the tail, where instructions live."""
    long_corpus = "x" * 40_000
    bounded = build_corpus_text(long_corpus)
    assert len(bounded) <= 26_000 + 200
    assert "truncated" in bounded


def test_short_corpus_is_returned_unchanged():
    assert build_corpus_text("short corpus") == "short corpus"


def test_empty_corpus_returns_empty():
    assert build_corpus_text("") == ""


def test_enrichment_result_serialises_for_the_api():
    result = parse_enrichment(CLEAN)
    payload = result.to_dict()
    assert set(payload) == {
        "glossary",
        "study_guide",
        "next_steps",
        "faq",
        "note",
        "thin_fields",
    }
    assert payload["glossary"][0]["term"] == "Surface code"


# ── Schema-level guarantees ──────────────────────────────────────────────────


def test_none_arrays_are_coerced_to_empty_lists():
    """A model that returns null for a list must not crash the parser."""
    raw = '{"glossary": null, "study_guide": null, "next_steps": null, "faq": null}'
    result = parse_enrichment(raw)
    assert isinstance(result, EnrichmentResult)
    assert result.glossary == []


def test_missing_required_definition_drops_the_term():
    raw = '{"glossary": [{"term": "Surface code", "definition": ""}]}'
    result = parse_enrichment(raw)
    assert result.glossary == []
