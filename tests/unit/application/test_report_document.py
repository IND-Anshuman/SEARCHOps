"""Tests for the ReportDocument block model.

The report used to be one opaque Markdown string in ``final_report``.
Nothing server-side could address a paragraph, a heading, or a citation, so
every user-facing feature that needs to act on *part* of a report (export to
deck/PDF, "where to go next", study guide, trust header) had to re-parse
Markdown in its own way.

ReportDocument makes the report addressable. Markdown becomes a pure
projection of the block list — ``to_markdown()`` — and is never hand-edited
again. The contracts pinned down here:

1. **Round-trip.** ``from_markdown`` → ``to_markdown`` is identity for a
   well-formed document. If this breaks, every export drifts from what the
   user saw.
2. **Stable ids.** Blocks get deterministic ids from their heading path and
   ordinal, so the same document always yields the same ids and a UI can
   hold a reference to a block across reloads.
3. **SOURCE blocks are immutable.** A citation line must never be rewritten
   by an LLM operation — that is how a report silently loses its evidence.
4. **claim_ids bind evidence to blocks.** A block carries the ids of the
   claims it contains, which is what makes per-block verification possible.
5. **Hidden blocks vanish from the projection** but persist in the model, so
   "hide this" is reversible and an export omits them.
6. **Empty input is never empty output.** A report always renders something
   readable.
"""

from __future__ import annotations

import pytest

from searchops.application.report_document import (
    BlockKind,
    ImmutableBlockError,
    ReportBlock,
    ReportDocument,
    trust_headline,
    trust_summary,
)

MD = "# Title\n\n## Findings\n\nSurface codes scale logarithmically.\n\nMore detail here.\n\n- item one\n- item two\n"

MD_WITH_SOURCES = (
    "# Title\n\n"
    "## Executive Summary\n\nTwo sources were retrieved and read.\n\n"
    "## Findings\n\nSurface codes scale logarithmically.\n\n"
    "## Sources\n\n- [A](https://a.com)\n- [B](https://b.com)\n"
)


class _Claim:
    """Mirrors searchops.orchestration.evidence.Claim for the fields used here."""

    def __init__(
        self,
        text: str,
        claim_id: str = "c1",
        status: str = "supported",
        confidence: float = 0.9,
        url: str = "",
    ) -> None:
        self.id = claim_id
        self.text = text
        self.status = status
        self.confidence = confidence
        self.citations = [type("C", (), {"url": url})()] if url else []


# ── Round-trip ───────────────────────────────────────────────────────────────


def test_markdown_to_blocks_classifies_each_chunk():
    doc = ReportDocument.from_markdown("job1", "qec", MD)
    assert [b.kind for b in doc.blocks] == [
        BlockKind.HEADING,
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.PARAGRAPH,
        BlockKind.LIST,
    ]


def test_markdown_round_trips_exactly():
    doc = ReportDocument.from_markdown("job1", "qec", MD)
    assert doc.to_markdown().strip() == MD.strip()


def test_heading_levels_are_captured():
    doc = ReportDocument.from_markdown("job1", "q", MD)
    assert doc.blocks[0].level == 1
    assert doc.blocks[1].level == 2


def test_list_block_keeps_every_item():
    doc = ReportDocument.from_markdown("job1", "q", MD)
    list_block = next(b for b in doc.blocks if b.kind is BlockKind.LIST)
    assert "item one" in list_block.text
    assert "item two" in list_block.text


def test_empty_input_still_produces_a_readable_report():
    doc = ReportDocument.from_markdown("job1", "q", "")
    assert doc.to_markdown().strip() != ""


def test_whitespace_only_input_still_produces_a_readable_report():
    doc = ReportDocument.from_markdown("job1", "q", "   \n\n  \n")
    assert doc.to_markdown().strip() != ""


# ── Stable ids ───────────────────────────────────────────────────────────────


def test_ids_are_deterministic_across_parses():
    a = ReportDocument.from_markdown("job1", "q", MD)
    b = ReportDocument.from_markdown("job1", "q", MD)
    assert [blk.id for blk in a.blocks] == [blk.id for blk in b.blocks]


def test_ids_are_unique():
    doc = ReportDocument.from_markdown("job1", "q", MD)
    ids = [blk.id for blk in doc.blocks]
    assert len(ids) == len(set(ids))


def test_ids_are_stable_when_an_unrelated_block_changes():
    """Renaming one heading must not re-key blocks under a different heading."""
    doc = ReportDocument.from_markdown("job1", "q", MD)
    before = {blk.text: blk.id for blk in doc.blocks}
    changed = MD.replace("More detail here.", "Completely different text.")
    after = ReportDocument.from_markdown("job1", "q", changed)
    after_ids = {blk.text: blk.id for blk in after.blocks}
    assert before["Surface codes scale logarithmically."] == after_ids["Surface codes scale logarithmically."]


# ── SOURCE immutability ──────────────────────────────────────────────────────


def test_sources_section_blocks_are_classified_as_source():
    doc = ReportDocument.from_markdown("job1", "q", MD_WITH_SOURCES)
    source_blocks = [b for b in doc.blocks if b.kind is BlockKind.SOURCE]
    assert source_blocks, "citation lines under ## Sources must be SOURCE blocks"
    assert "https://a.com" in source_blocks[0].text


def test_apply_block_op_refuses_to_rewrite_a_source_block():
    doc = ReportDocument.from_markdown("job1", "q", MD_WITH_SOURCES)
    src = next(b for b in doc.blocks if b.kind is BlockKind.SOURCE)
    with pytest.raises(ImmutableBlockError):
        doc.apply_block_op(src.id, "condense", new_text="rewritten citation")


def test_apply_block_op_allows_editing_a_normal_block():
    doc = ReportDocument.from_markdown("job1", "q", MD)
    para = next(b for b in doc.blocks if b.text == "Surface codes scale logarithmically.")
    updated = doc.apply_block_op(para.id, "condense", new_text="Shortened.")
    assert updated is not None
    target = next(b for b in updated.blocks if b.id == para.id)
    assert target.text == "Shortened."
    assert target.origin == "condensed"


def test_apply_block_op_on_unknown_id_returns_none():
    doc = ReportDocument.from_markdown("job1", "q", MD)
    assert doc.apply_block_op("no-such-block", "condense", new_text="x") is None


# ── claim binding ────────────────────────────────────────────────────────────


def test_claim_ids_bind_claims_to_their_block():
    claims = [_Claim("Surface codes scale logarithmically.", claim_id="c1", url="https://a.com")]
    doc = ReportDocument.from_markdown("job1", "q", MD, claims=claims)
    para = next(b for b in doc.blocks if b.text == "Surface codes scale logarithmically.")
    assert para.claim_ids == ["c1"]


def test_claim_binding_requires_a_substantive_match():
    """A claim whose text does not appear in the document must not be attached
    to an arbitrary block — that would fabricate provenance."""
    claims = [_Claim("Quantum teleportation works at city scale.", claim_id="c9")]
    doc = ReportDocument.from_markdown("job1", "q", MD, claims=claims)
    assert all("c9" not in b.claim_ids for b in doc.blocks)


def test_blocks_without_claims_have_empty_claim_ids():
    doc = ReportDocument.from_markdown("job1", "q", MD)
    assert all(b.claim_ids == [] for b in doc.blocks)


# ── Hidden blocks ────────────────────────────────────────────────────────────


def test_hidden_block_disappears_from_markdown_but_persists():
    doc = ReportDocument.from_markdown("job1", "q", MD)
    target = next(b for b in doc.blocks if b.text == "More detail here.")
    hidden = doc.set_hidden(target.id, True)
    assert "More detail here." not in hidden.to_markdown()
    assert any(b.id == target.id for b in hidden.blocks), "block must persist for un-hiding"


def test_unhiding_restores_the_block():
    doc = ReportDocument.from_markdown("job1", "q", MD)
    target = next(b for b in doc.blocks if b.text == "More detail here.")
    restored = doc.set_hidden(target.id, True).set_hidden(target.id, False)
    assert "More detail here." in restored.to_markdown()


def test_visible_blocks_helper_skips_hidden():
    doc = ReportDocument.from_markdown("job1", "q", MD)
    target = next(b for b in doc.blocks if b.text == "More detail here.")
    hidden = doc.set_hidden(target.id, True)
    assert target.id not in {b.id for b in hidden.visible_blocks()}


# ── Trust summary ────────────────────────────────────────────────────────────


def test_trust_summary_counts_real_claim_statuses():
    claims = [
        _Claim("A", claim_id="c1", status="supported"),
        _Claim("B", claim_id="c2", status="contested"),
        _Claim("C", claim_id="c3", status="unverified"),
        _Claim("D", claim_id="c4", status="refuted"),
    ]
    doc = ReportDocument.from_markdown("job1", "q", MD, claims=claims)
    s = trust_summary(doc)
    assert s["total"] == 4
    assert s["supported"] == 1
    assert s["contested"] == 1
    assert s["unverified"] == 1
    assert s["refuted"] == 1


def test_trust_summary_reads_claims_after_a_dict_round_trip():
    """Regression: evidence crosses Redis as plain dicts.

    trust_summary used getattr only, so after a round-trip every claim read as
    "unverified" and a supported claim was reported as unsupported. The trust
    header is the feature users judge the product by, so this had to be right.
    """
    claims = [
        {"id": "c1", "text": "A", "status": "supported", "confidence": 0.9, "citations": []},
        {"id": "c2", "text": "B", "status": "contested", "confidence": 0.7, "citations": []},
    ]
    doc = ReportDocument.from_markdown("j", "q", MD, claims=claims)
    rebuilt = ReportDocument.from_dict(doc.to_dict())

    assert all(isinstance(c, dict) for c in rebuilt.claims), "precondition"
    s = trust_summary(rebuilt)
    assert s["supported"] == 1
    assert s["contested"] == 1
    assert s["unverified"] == 0


def test_glossary_reads_entities_after_a_dict_round_trip():
    """Same trap on the entity side: a populated glossary came back empty."""
    from searchops.application.exports.glossary import build_glossary

    entities = [
        {"name": "Surface code", "entity_type": "Concept", "description": "A 2D lattice code.", "confidence": 0.95},
    ]
    doc = ReportDocument.from_markdown("j", "q", MD, entities=entities)
    rebuilt = ReportDocument.from_dict(doc.to_dict())

    assert all(isinstance(e, dict) for e in rebuilt.entities), "precondition"
    terms = build_glossary(rebuilt)
    assert terms, "entities survived the round-trip, so a glossary must be produced"
    assert terms[0].term == "Surface code"
    assert terms[0].definition == "A 2D lattice code."


def test_trust_headline_mentions_every_non_zero_status():
    claims = [
        _Claim("A", claim_id="c1", status="supported"),
        _Claim("B", claim_id="c2", status="contested"),
    ]
    doc = ReportDocument.from_markdown("job1", "q", MD, claims=claims)
    line = trust_headline(trust_summary(doc))
    assert "2 findings" in line
    assert "1 verified against sources" in line
    assert "1 contested" in line


def test_trust_headline_with_no_claims_says_so():
    doc = ReportDocument.from_markdown("job1", "q", MD)
    line = trust_headline(trust_summary(doc))
    assert "No individual claims" in line


def test_trust_summary_never_rounds_up_or_hides_a_contested_claim():
    """A contested claim must be visible in the headline even when it is the
    only problem — the whole point is not flattering the report."""
    claims = [_Claim("A", claim_id="c1", status="supported"), _Claim("B", claim_id="c2", status="contested")]
    doc = ReportDocument.from_markdown("job1", "q", MD, claims=claims)
    assert "contested" in trust_headline(trust_summary(doc))


# ── Serialisation ────────────────────────────────────────────────────────────


def test_to_dict_and_from_dict_round_trip():
    doc = ReportDocument.from_markdown("job1", "qec", MD_WITH_SOURCES)
    rebuilt = ReportDocument.from_dict(doc.to_dict())
    assert rebuilt.to_markdown().strip() == doc.to_markdown().strip()
    assert [b.id for b in rebuilt.blocks] == [b.id for b in doc.blocks]


def test_to_dict_is_json_serialisable():
    """JobStateManager serialises with orjson, so the dict must be plain data."""
    import json

    doc = ReportDocument.from_markdown("job1", "q", MD)
    payload = json.dumps(doc.to_dict())
    assert "Findings" in payload


def test_hidden_state_survives_a_dict_round_trip():
    doc = ReportDocument.from_markdown("job1", "q", MD)
    target = next(b for b in doc.blocks if b.text == "More detail here.")
    rebuilt = ReportDocument.from_dict(doc.set_hidden(target.id, True).to_dict())
    assert "More detail here." not in rebuilt.to_markdown()


# ── Block defaults ───────────────────────────────────────────────────────────


def test_block_defaults_are_safe():
    b = ReportBlock(id="x", kind=BlockKind.PARAGRAPH, text="t")
    assert b.claim_ids == []
    assert b.hidden is False
    assert b.origin == "model"
    assert b.level is None
    assert b.parent_id is None


def test_block_claim_ids_are_not_shared_between_instances():
    """A mutable default would silently cross-link blocks from different docs."""
    a = ReportBlock(id="a", kind=BlockKind.PARAGRAPH, text="t")
    b = ReportBlock(id="b", kind=BlockKind.PARAGRAPH, text="t")
    a.claim_ids.append("c1")
    assert b.claim_ids == []


def test_document_defaults_are_not_shared_between_instances():
    a = ReportDocument(job_id="j", query="q", blocks=[])
    b = ReportDocument(job_id="j", query="q", blocks=[])
    a.claims.append(_Claim("x"))  # type: ignore[arg-type]
    assert b.claims == []
