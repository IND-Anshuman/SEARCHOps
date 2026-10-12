"""Tests for block-level report operations.

The contracts that matter:

1. **A failed operation never looks like a successful edit.** Refusals, empty
   output and unknown blocks leave the document untouched with a reason the UI
   can show. Replacing a paragraph with a failed generation is how a report
   quietly loses content.
2. **SOURCE blocks are immutable.** Enforced by ``apply_block_op``; nothing
   here bypasses it. A citation is the evidence.
3. **deepen refuses without claims.** Expanding from model memory instead of
   the block's own claims would invent supporting detail — the exact failure
   this product exists to avoid.
4. **Deterministic ops cannot hallucinate.** condense/hide/split make no model
   call, so they are cheap and cannot fabricate.
5. **Every edit records provenance**, so the trust header stays meaningful
   after editing.
"""

from __future__ import annotations

import pytest

from searchops.application.exports.block_ops import (
    _REWRITE_SYSTEM,
    apply_op,
    condense,
    deepen,
    hide,
    rewrite,
    split,
    unhide,
)
from searchops.application.report_document import ReportDocument

MD_DOC = (
    "# Quantum Error Correction\n\n"
    "## Findings\n\n"
    "Surface codes scale logarithmically with code distance. "
    "Logical error rates fall as the lattice grows. "
    "Overhead remains the practical limit today.\n\n"
    "## Sources\n\n- [IBM](https://ibm.com/a)\n"
)


class _Claim:
    def __init__(self, cid, text, url="https://ibm.com/a"):
        self.id, self.text = cid, text
        self.citations = [type("C", (), {"url": url})()]


class _Router:
    """Router stub that returns a fixed reply or raises."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def generate(self, prompt, *, system_prompt=None, temperature=0.0, max_tokens=None):
        self.calls.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _doc() -> ReportDocument:
    claims = [_Claim("c1", "Surface codes scale logarithmically with code distance.")]
    return ReportDocument.from_markdown("job-1", "qec", MD_DOC, claims=claims)


def _para(doc):
    return next(b for b in doc.blocks if b.kind.value == "paragraph" and "Surface codes" in b.text)


# ── condense (deterministic) ─────────────────────────────────────────────────


def test_condense_shortens_and_keeps_whole_sentences():
    doc = _doc()
    para = _para(doc)
    result = condense(doc, para.id)

    assert result.ok
    assert len(result.document.blocks) == len(doc.blocks)
    clone = next(b for b in result.document.blocks if b.id == para.id)
    assert len(clone.text) < len(para.text)
    assert clone.text.endswith((".", "!", "?")), "never cut mid-sentence"
    assert clone.origin == "condensed"


def test_condense_refuses_an_already_short_block():
    doc = ReportDocument.from_markdown("j", "q", "## A\n\nShort.\n")
    block = next(b for b in doc.blocks if b.kind.value == "paragraph")
    result = condense(doc, block.id)

    assert not result.ok
    assert result.reason
    assert result.document.to_dict() == doc.to_dict(), "document must be untouched"


def test_condense_on_unknown_block_is_refused():
    result = condense(_doc(), "no-such-block")
    assert not result.ok
    assert "no longer exists" in result.reason


def test_condense_refuses_a_source_block():
    doc = _doc()
    src = next(b for b in doc.blocks if b.kind.value == "source")
    result = condense(doc, src.id)
    assert not result.ok
    assert "evidence" in result.reason.lower()


# ── hide / unhide ────────────────────────────────────────────────────────────


def test_hide_removes_the_block_from_markdown_but_keeps_it():
    doc = _doc()
    para = _para(doc)
    result = hide(doc, para.id)

    assert result.ok
    assert "Surface codes" not in result.document.to_markdown()
    assert any(b.id == para.id for b in result.document.blocks), "reversible"


def test_unhide_restores_the_block():
    doc = _doc()
    para = _para(doc)
    hidden = hide(doc, para.id)
    result = unhide(hidden.document, para.id)

    assert result.ok
    assert "Surface codes" in result.document.to_markdown()


def test_hide_is_idempotent_refused():
    doc = _doc()
    para = _para(doc)
    hidden = hide(doc, para.id)
    again = hide(hidden.document, para.id)

    assert not again.ok
    assert "already hidden" in again.reason


def test_unhide_of_a_visible_block_is_refused():
    doc = _doc()
    para = _para(doc)
    assert not unhide(doc, para.id).ok


# ── split (deterministic) ────────────────────────────────────────────────────


def test_split_produces_two_blocks_and_preserves_text():
    doc = _doc()
    para = _para(doc)
    result = split(doc, para.id, at_char=40)

    assert result.ok
    assert len(result.document.blocks) == len(doc.blocks) + 1
    joined = " ".join(b.text for b in result.document.blocks if b.id in result.touched_ids)
    assert "Surface codes" in joined and "Logical error rates" in joined


def test_split_carries_claim_bindings_to_both_halves():
    """Splitting a paragraph must not orphan the claims that supported it."""
    doc = _doc()
    para = _para(doc)
    assert para.claim_ids, "precondition"
    result = split(doc, para.id, at_char=40)

    for bid in result.touched_ids:
        block = next(b for b in result.document.blocks if b.id == bid)
        assert block.claim_ids == para.claim_ids


def test_split_refuses_a_source_block():
    doc = _doc()
    src = next(b for b in doc.blocks if b.kind.value == "source")
    assert not split(doc, src.id, at_char=5).ok


def test_split_refuses_out_of_range_positions():
    doc = _doc()
    para = _para(doc)
    assert not split(doc, para.id, at_char=0).ok
    assert not split(doc, para.id, at_char=len(para.text) + 10).ok


# ── deepen (model-backed) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deepen_applies_model_output_and_records_provenance():
    doc = _doc()
    para = _para(doc)
    result = await deepen(doc, para.id, router=_Router("Expanded detail about surface codes."))

    assert result.ok
    clone = next(b for b in result.document.blocks if b.id == para.id)
    assert clone.text == "Expanded detail about surface codes."
    assert clone.origin == "deepened"


@pytest.mark.asyncio
async def test_deepen_refuses_without_claims_rather_than_inventing():
    """The core honesty rule: no grounding means no expansion."""
    doc = ReportDocument.from_markdown("j", "q", MD_DOC)
    para = _para(doc)
    assert not para.claim_ids, "precondition"
    router = _Router("Invented detail.")
    result = await deepen(doc, para.id, router=router)

    assert not result.ok
    assert "no supporting claims" in result.reason.lower()
    assert not router.calls, "no model call should be made"
    assert result.document.to_dict() == doc.to_dict()


@pytest.mark.asyncio
async def test_deepen_leaves_the_block_unchanged_on_a_refusal():
    doc = _doc()
    para = _para(doc)
    result = await deepen(doc, para.id, router=_Router("I'm sorry, I cannot help with that."))

    assert not result.ok
    assert "left unchanged" in result.reason.lower()
    clone = next(b for b in result.document.blocks if b.id == para.id)
    assert clone.text == para.text, "a refusal must not replace the text"


@pytest.mark.asyncio
async def test_deepen_survives_a_router_error():
    doc = _doc()
    para = _para(doc)
    result = await deepen(doc, para.id, router=_Router(RuntimeError("provider down")))

    assert not result.ok
    clone = next(b for b in result.document.blocks if b.id == para.id)
    assert clone.text == para.text


@pytest.mark.asyncio
async def test_deepen_refuses_a_source_block():
    doc = _doc()
    src = next(b for b in doc.blocks if b.kind.value == "source")
    assert not (await deepen(doc, src.id, router=_Router("x"))).ok


@pytest.mark.asyncio
async def test_deepen_prompt_cites_the_grounding_claims():
    doc = _doc()
    para = _para(doc)
    router = _Router("Expanded.")
    await deepen(doc, para.id, router=router)

    prompt = router.calls[0]
    assert "Surface codes scale logarithmically" in prompt
    assert "https://ibm.com/a" in prompt


# ── rewrite (model-backed) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rewrite_applies_output_and_records_provenance():
    doc = _doc()
    para = _para(doc)
    result = await rewrite(doc, para.id, router=_Router("Surface codes scale with distance."))

    assert result.ok
    clone = next(b for b in result.document.blocks if b.id == para.id)
    assert clone.text == "Surface codes scale with distance."
    assert clone.origin == "user", "a rewrite is a user-level edit, not model prose"


@pytest.mark.asyncio
async def test_rewrite_works_without_claims_but_forbids_new_facts():
    """Clarity editing needs no grounding, but the prompt must forbid new facts."""
    doc = ReportDocument.from_markdown("j", "q", MD_DOC)
    para = _para(doc)
    router = _Router("Surface codes scale with code distance, and logical error rates fall.")
    result = await rewrite(doc, para.id, router=router)

    assert result.ok
    clone = next(b for b in result.document.blocks if b.id == para.id)
    assert clone.text.startswith("Surface codes scale")
    # The system prompt is the guard against invention.
    assert "Do not add new facts" in _REWRITE_SYSTEM


@pytest.mark.asyncio
async def test_rewrite_leaves_the_block_unchanged_on_empty_output():
    doc = _doc()
    para = _para(doc)
    result = await rewrite(doc, para.id, router=_Router("   "))

    assert not result.ok
    clone = next(b for b in result.document.blocks if b.id == para.id)
    assert clone.text == para.text


# ── dispatch ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_routes_each_op():
    doc = _doc()
    para = _para(doc)

    assert apply_op is not None
    assert (await apply_op(doc, para.id, "hide")).ok
    assert (await apply_op(doc, para.id, "condense")).ok


@pytest.mark.asyncio
async def test_dispatch_refuses_unknown_ops():
    result = await apply_op(_doc(), _para(_doc()).id, "teleport")
    assert not result.ok
    assert "Unknown operation" in result.reason


@pytest.mark.asyncio
async def test_dispatch_refuses_model_ops_without_a_router():
    doc = _doc()
    para = _para(doc)
    result = await apply_op(doc, para.id, "deepen", router=None)

    assert not result.ok
    assert "language model" in result.reason.lower()


def test_result_to_dict_is_serialisable():
    result = hide(_doc(), _para(_doc()).id)
    payload = result.to_dict()
    assert payload["ok"] is True
    assert payload["op"] == "hide"
    assert "document" in payload and "blocks" in payload["document"]


def test_result_to_dict_is_json_safe_with_object_claims():
    """Regression: the endpoint returns this dict straight through FastAPI.

    ReportDocument.to_dict keeps the caller's claim objects untouched, which is
    right inside the process but not on the wire — pydantic cannot serialise an
    arbitrary object, so a successful edit would have raised a 500.
    """
    import json

    result = hide(_doc(), _para(_doc()).id)
    payload = result.to_dict()

    # Must not raise.
    encoded = json.dumps(payload)
    assert "Surface codes scale logarithmically" in encoded
    assert "https://ibm.com/a" in encoded


def test_serialised_claims_keep_their_citations():
    import json

    result = hide(_doc(), _para(_doc()).id)
    claims = json.loads(json.dumps(result.to_dict()))["document"]["claims"]

    assert claims, "precondition"
    assert claims[0]["id"] == "c1"
    assert claims[0]["citations"][0]["url"] == "https://ibm.com/a"


def test_serialisation_never_invents_missing_fields():
    """A claim with no confidence must not gain one."""
    doc = ReportDocument.from_markdown("j", "q", MD_DOC, claims=[{"id": "c1", "text": "Surface codes scale."}])
    result = hide(doc, _para(doc).id)
    claim = result.to_dict()["document"]["claims"][0]

    assert claim["id"] == "c1"
    assert "confidence" not in claim
    assert "status" not in claim


def test_dict_shaped_claims_keep_their_citation_urls():
    """Regression: dict claims take a different branch, and the generic
    recursion turned a citations LIST into an empty dict — silently dropping
    every source URL. Live jobs hit this path, because evidence crosses Redis
    as plain dicts.
    """
    import json

    doc = ReportDocument.from_markdown(
        "j",
        "q",
        MD_DOC,
        claims=[
            {
                "id": "c1",
                "text": "Surface codes scale logarithmically.",
                "status": "supported",
                "citations": [{"url": "https://ibm.com/a"}],
            }
        ],
    )
    result = hide(doc, _para(doc).id)
    claims = json.loads(json.dumps(result.to_dict()))["document"]["claims"]

    assert claims[0]["citations"] == [{"url": "https://ibm.com/a"}], "a dict-shaped claim must keep its source URL"
