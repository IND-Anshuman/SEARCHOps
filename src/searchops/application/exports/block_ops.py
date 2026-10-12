"""Block-level operations on a report: deepen, condense, rewrite, hide.

This is the interactive layer the report never had. A report was one opaque
Markdown string; now it is addressable blocks, and a user can act on any one
of them.

Design contract — the same honesty rules that govern the rest of the report
pipeline:

1. **An operation that cannot produce usable text does not change the
   document.** If the model refuses or returns nothing, the block is left
   exactly as it was and the caller is told why. Silently replacing a
   paragraph with a failed generation is how a report quietly loses content.
2. **SOURCE blocks are immutable.** ``apply_block_op`` already enforces this;
   nothing here bypasses it. A citation is the evidence.
3. **No invented content.** ``deepen`` and ``rewrite`` are told, in the system
   prompt, to work only from the block's own text and its bound claims, and to
   return nothing rather than invent. The refusal path is tested.
4. **Every edit records provenance.** The block's ``origin`` becomes the
   operation verb, so a later reader can tell model prose from an edit. This
   is what makes the trust header meaningful after editing.
5. **Pure functions where possible.** Only ``deepen`` and ``rewrite`` call the
   model; ``condense``, ``hide`` and ``split`` are deterministic, so they are
   cheap, testable, and cannot hallucinate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import structlog

from searchops.application.report_document import (
    BlockKind,
    ImmutableBlockError,
    ReportBlock,
    ReportDocument,
    _field,
)
from searchops.llm.token_budget import truncate_to_tokens

log = structlog.get_logger(__name__)

OpName = Literal["condense", "deepen", "rewrite", "hide", "unhide", "split"]

# Bounds. A "condensed" block that is nearly as long as the original has not
# condensed anything.
_CONDENSE_TARGET_RATIO = 0.6
_CONDENSE_MAX_TOKENS = 300
_DEEPEN_MAX_TOKENS = 800
_REWRITE_MAX_TOKENS = 600

# Below this, a block is too small to be worth an LLM call.
_MIN_OP_CHARS = 12


@dataclass
class BlockOpResult:
    """The outcome of one block operation.

    ``ok`` is False whenever the document was left untouched, and ``reason``
    always explains why in language a caller can show a user. An operation
    that fails must never look like a successful edit.
    """

    ok: bool
    document: ReportDocument
    op: str
    reason: str = ""
    # Blocks created by the op (split produces two, deepen may append).
    touched_ids: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "op": self.op,
            "reason": self.reason,
            "touched_ids": self.touched_ids or [],
            "document": _serialisable_document(self.document),
        }


def _serialisable_document(doc: ReportDocument) -> dict[str, Any]:
    """A JSON-safe projection of the document.

    ``ReportDocument.to_dict`` deliberately preserves the caller's claim and
    entity objects untouched, because the live pipeline passes pydantic models
    and a test may pass doubles. That is fine inside the process but not on the
    wire: pydantic cannot serialise an arbitrary object, so the endpoint would
    raise a 500 on an otherwise successful edit.

    Claims, entities and gaps are therefore projected to primitives here. A
    field that cannot be projected is dropped rather than guessed at — never
    invented to fill the response.
    """
    payload = doc.to_dict()
    for key in ("claims", "entities", "gaps"):
        payload[key] = [_as_primitive(item) for item in payload.get(key) or []]
    return payload


def _as_primitive(item: Any) -> Any:
    """Coerce one evidence record to JSON-safe primitives.

    Two shapes must be handled, because evidence arrives as pydantic models
    from the live pipeline and as plain dicts after a Redis round-trip. The
    dict branch has to be explicit: a generic recursion turns a ``citations``
    *list* into an empty dict and silently drops every source URL, which is
    exactly the kind of quiet data loss this module exists to prevent.
    """
    if item is None or isinstance(item, (str, int, float, bool)):
        return item
    if isinstance(item, dict):
        return _project_dict(item)

    record: dict[str, Any] = {}
    for name in ("id", "text", "status", "confidence", "name", "entity_type", "description"):
        value = _field(item, name, None)
        if value is None:
            continue
        record[name] = _as_primitive(value)

    projected = _project_citations(_field(item, "citations", None))
    if projected:
        record["citations"] = projected

    return record


def _project_dict(item: dict[str, Any]) -> dict[str, Any]:
    """Project a dict-shaped evidence record, preserving citations."""
    record: dict[str, Any] = {}
    for name in ("id", "text", "status", "confidence", "name", "entity_type", "description"):
        value = item.get(name)
        if value is None:
            continue
        record[name] = _as_primitive(value)

    projected = _project_citations(item.get("citations"))
    if projected:
        record["citations"] = projected
    return record


def _project_citations(citations: Any) -> list[dict[str, str]]:
    """Keep only the source URL of each citation.

    Anything without a URL is dropped rather than rendered as an empty entry —
    a citation with no link is not evidence.
    """
    if not citations:
        return []
    projected: list[dict[str, str]] = []
    for citation in citations:
        url = citation.get("url") if isinstance(citation, dict) else getattr(citation, "url", None)
        if url:
            projected.append({"url": str(url)})
    return projected


# ── Deterministic operations (no model call) ─────────────────────────────────


def condense(doc: ReportDocument, block_id: str) -> BlockOpResult:
    """Shorten a block by trimming to its leading sentences.

    Deterministic on purpose: no model call means no hallucination risk and no
    cost. It keeps whole sentences only, so the result never ends mid-thought,
    and it never touches a SOURCE block.

    If the block is already short, the document is unchanged and ``ok`` is
    False — a "condense" that changes nothing should not claim success.
    """
    block = doc._find(block_id)
    if block is None:
        return BlockOpResult(False, doc, "condense", reason="That block no longer exists.")
    if block.kind is BlockKind.SOURCE:
        return BlockOpResult(False, doc, "condense", reason="Citations cannot be condensed — they are the evidence.")

    text = block.text.strip()
    if len(text) < _MIN_OP_CHARS:
        return BlockOpResult(False, doc, "condense", reason="That block is already very short.")

    target = max(1, int(len(text) * _CONDENSE_TARGET_RATIO))
    shortened = _truncate_to_sentences(text, target)

    if not shortened or shortened == text:
        return BlockOpResult(False, doc, "condense", reason="That block is already as short as it can be.")

    updated = doc.apply_block_op(block_id, "condense", new_text=shortened)
    if updated is None:
        return BlockOpResult(False, doc, "condense", reason="That block no longer exists.")
    return BlockOpResult(True, updated, "condense", touched_ids=[block_id])


def hide(doc: ReportDocument, block_id: str) -> BlockOpResult:
    """Hide a block from the report. Reversible, and never deletes evidence."""
    block = doc._find(block_id)
    if block is None:
        return BlockOpResult(False, doc, "hide", reason="That block no longer exists.")
    if block.hidden:
        return BlockOpResult(False, doc, "hide", reason="That block is already hidden.")

    updated = doc.set_hidden(block_id, True)
    return BlockOpResult(True, updated, "hide", touched_ids=[block_id])


def unhide(doc: ReportDocument, block_id: str) -> BlockOpResult:
    """Restore a hidden block."""
    block = doc._find(block_id)
    if block is None:
        return BlockOpResult(False, doc, "unhide", reason="That block no longer exists.")
    if not block.hidden:
        return BlockOpResult(False, doc, "unhide", reason="That block is not hidden.")

    updated = doc.set_hidden(block_id, False)
    return BlockOpResult(True, updated, "unhide", touched_ids=[block_id])


def split(doc: ReportDocument, block_id: str, *, at_char: int) -> BlockOpResult:
    """Split a block into two at a character offset.

    Deterministic. The second half becomes a new block immediately after the
    first, inheriting its kind and claim bindings — splitting a paragraph must
    not orphan the claims that supported it.
    """
    block = doc._find(block_id)
    if block is None:
        return BlockOpResult(False, doc, "split", reason="That block no longer exists.")
    if block.kind is BlockKind.SOURCE:
        return BlockOpResult(False, doc, "split", reason="Citations cannot be split — they are the evidence.")

    text = block.text
    if at_char <= 0 or at_char >= len(text):
        return BlockOpResult(False, doc, "split", reason="Pick a position inside the block to split at.")

    # Break on a space so no word is severed.
    boundary = text.rfind(" ", 0, at_char)
    if boundary <= 0:
        boundary = at_char

    left = text[:boundary].strip()
    right = text[boundary:].strip()
    if not left or not right:
        return BlockOpResult(False, doc, "split", reason="That position would leave an empty block.")

    updated = ReportDocument.from_dict(doc.to_dict())
    clone = next(b for b in updated.blocks if b.id == block_id)
    clone.text = left
    clone.origin = "user"

    tail = ReportBlock(
        id=_new_block_id(updated),
        kind=clone.kind,
        text=right,
        level=clone.level,
        claim_ids=list(clone.claim_ids),
        hidden=clone.hidden,
        origin="user",
        parent_id=clone.parent_id,
    )
    index = next(i for i, b in enumerate(updated.blocks) if b.id == block_id)
    updated.blocks.insert(index + 1, tail)

    return BlockOpResult(True, updated, "split", touched_ids=[block_id, tail.id])


# ── Model-backed operations ──────────────────────────────────────────────────

_DEEPEN_SYSTEM = (
    "You expand one section of a research report. Using ONLY the section text "
    "and the supporting claims provided, add relevant detail, context, or "
    "consequences. Every sentence must be supported by the provided material. "
    "If the material does not support further detail, say so in one short "
    "sentence rather than inventing anything."
)

_REWRITE_SYSTEM = (
    "You rewrite one section of a research report for clarity and flow. Keep "
    "every factual claim exactly as supported by the provided claims. Do not "
    "add new facts, and do not remove the meaning. Return only the rewritten "
    "section."
)


async def deepen(doc: ReportDocument, block_id: str, *, router: Any) -> BlockOpResult:
    """Expand a block with detail drawn from its own bound claims.

    The claims bound to the block are the only grounding. If the block has no
    claims, the operation is refused rather than answered from model memory —
    inventing supporting detail is exactly the failure this product exists to
    avoid.
    """
    block = doc._find(block_id)
    if block is None:
        return BlockOpResult(False, doc, "deepen", reason="That block no longer exists.")
    if block.kind is BlockKind.SOURCE:
        return BlockOpResult(False, doc, "deepen", reason="Citations cannot be expanded — they are the evidence.")

    claim_lines = _claim_brief(doc, block)
    if not claim_lines:
        return BlockOpResult(
            False,
            doc,
            "deepen",
            reason=(
                "This section has no supporting claims to build on, so it cannot be expanded without inventing content."
            ),
        )

    prompt = (
        f"REPORT TOPIC: {doc.query or 'this report'}\n\n"
        f"SECTION:\n{truncate_to_tokens(block.text, 600)}\n\n"
        f"SUPPORTING CLAIMS:\n{claim_lines}\n\n"
        "Return only the expanded section as Markdown."
    )

    raw = await _generate(router, prompt, _DEEPEN_SYSTEM, _DEEPEN_MAX_TOKENS)
    if not _is_usable(raw):
        return BlockOpResult(
            False,
            doc,
            "deepen",
            reason="The model could not expand this section, so it was left unchanged.",
        )

    updated = doc.apply_block_op(block_id, "deepen", new_text=str(raw).strip())
    if updated is None:
        return BlockOpResult(False, doc, "deepen", reason="That block no longer exists.")
    return BlockOpResult(True, updated, "deepen", touched_ids=[block_id])


async def rewrite(doc: ReportDocument, block_id: str, *, router: Any) -> BlockOpResult:
    """Rewrite a block for clarity, grounded in its bound claims.

    A block with no claims is still rewritten — clarity editing does not need
    new facts — but the prompt is told not to add any.
    """
    block = doc._find(block_id)
    if block is None:
        return BlockOpResult(False, doc, "rewrite", reason="That block no longer exists.")
    if block.kind is BlockKind.SOURCE:
        return BlockOpResult(False, doc, "rewrite", reason="Citations cannot be rewritten — they are the evidence.")
    if len(block.text.strip()) < _MIN_OP_CHARS:
        return BlockOpResult(False, doc, "rewrite", reason="That block is too short to rewrite.")

    claim_lines = _claim_brief(doc, block)
    claims_section = f"SUPPORTING CLAIMS:\n{claim_lines}\n\n" if claim_lines else ""

    prompt = (
        f"SECTION:\n{truncate_to_tokens(block.text, 600)}\n\n"
        f"{claims_section}"
        "Return only the rewritten section as Markdown."
    )

    raw = await _generate(router, prompt, _REWRITE_SYSTEM, _REWRITE_MAX_TOKENS)
    if not _is_usable(raw):
        return BlockOpResult(
            False,
            doc,
            "rewrite",
            reason="The model could not rewrite this section, so it was left unchanged.",
        )

    updated = doc.apply_block_op(block_id, "rewrite", new_text=str(raw).strip())
    if updated is None:
        return BlockOpResult(False, doc, "rewrite", reason="That block no longer exists.")
    return BlockOpResult(True, updated, "rewrite", touched_ids=[block_id])


# ── Dispatch ─────────────────────────────────────────────────────────────────

_DETERMINISTIC = {"condense", "hide", "unhide", "split"}


async def apply_op(
    doc: ReportDocument,
    block_id: str,
    op: str,
    *,
    router: Any = None,
    at_char: int = 0,
) -> BlockOpResult:
    """Dispatch one operation by name.

    Unknown operations are refused with a clear reason rather than silently
    doing nothing.
    """
    if op in _DETERMINISTIC:
        if op == "condense":
            return condense(doc, block_id)
        if op == "hide":
            return hide(doc, block_id)
        if op == "unhide":
            return unhide(doc, block_id)
        if op == "split":
            return split(doc, block_id, at_char=at_char)

    if op in {"deepen", "rewrite"}:
        if router is None:
            return BlockOpResult(False, doc, op, reason="That operation needs a language model, which is unavailable.")
        if op == "deepen":
            return await deepen(doc, block_id, router=router)
        return await rewrite(doc, block_id, router=router)

    return BlockOpResult(False, doc, op, reason=f"Unknown operation: {op}")


# ── Internals ────────────────────────────────────────────────────────────────


def _claim_brief(doc: ReportDocument, block: ReportBlock) -> str:
    """The claims bound to this block, one per line, with citations.

    Empty when the block carries no claims — which is what makes ``deepen``
    refuse rather than invent.
    """
    if not block.claim_ids:
        return ""
    by_id = {str(_field(c, "id", "")): c for c in doc.claims}
    lines: list[str] = []
    for cid in block.claim_ids:
        claim = by_id.get(cid)
        if claim is None:
            continue
        text = str(_field(claim, "text", "") or "").strip()
        if not text:
            continue
        url = ""
        for citation in _field(claim, "citations", None) or []:
            candidate = str(_field(citation, "url", "") or "")
            if candidate.startswith("http"):
                url = candidate
                break
        lines.append(f"- {text}{f'  [{url}]' if url else ''}")
    return "\n".join(lines)


def _truncate_to_sentences(text: str, target: int) -> str:
    """Keep whole sentences up to roughly ``target`` characters.

    Never cuts mid-sentence, so a condensed block never ends mid-thought. If
    even the first sentence exceeds the target, that one sentence is kept.
    """
    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return ""

    out: list[str] = []
    total = 0
    for sentence in sentences:
        if out and total + len(sentence) > target:
            break
        out.append(sentence)
        total += len(sentence) + 1
    return " ".join(out).strip()


def _split_sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation, keeping the punctuation attached."""
    import re

    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _is_usable(raw: Any) -> bool:
    """Whether model output is worth applying.

    A refusal, an empty string, or a fragment is not usable — the block stays
    as it was and the user is told.
    """
    if raw is None:
        return False
    text = str(raw).strip()
    if len(text) < _MIN_OP_CHARS:
        return False
    lowered = text.lower()
    for marker in ("i cannot", "i can't", "i'm sorry", "i am sorry", "i'm unable", "unable to"):
        if lowered.startswith(marker):
            return False
    return True


async def _generate(router: Any, prompt: str, system_prompt: str, max_tokens: int) -> str | None:
    try:
        raw = await router.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.2,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        log.warning("block_op.generate_failed", error=str(exc))
        return None
    return str(raw) if raw is not None else None


def _new_block_id(doc: ReportDocument) -> str:
    """A block id that does not collide with any existing id in ``doc``."""
    import hashlib

    existing = {b.id for b in doc.blocks}
    base = f"{doc.job_id}|{len(doc.blocks)}|{doc.query}"
    candidate = f"b_{hashlib.sha256(base.encode()).hexdigest()[:10]}"
    suffix = 1
    while candidate in existing:
        candidate = f"b_{hashlib.sha256(f'{base}|{suffix}'.encode()).hexdigest()[:10]}"
        suffix += 1
    return candidate


__all__ = [
    "BlockOpResult",
    "ImmutableBlockError",
    "OpName",
    "apply_op",
    "condense",
    "deepen",
    "hide",
    "rewrite",
    "split",
    "unhide",
]
