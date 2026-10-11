"""Canonical block model for a research report.

The report used to live as one opaque Markdown string in ``final_report``.
Nothing server-side could address a paragraph, a heading, or a citation, so
every feature that needs to act on *part* of a report — export to deck/PDF,
"where to go next", study guide, trust header — had to re-parse Markdown its
own way, and the results drifted.

This module makes the report addressable. The block list is the source of
truth; ``to_markdown()`` is a pure projection of it. Markdown is never
hand-edited again.

Contracts:

1. **Round-trip.** ``from_markdown`` → ``to_markdown`` is identity for a
   well-formed document. If this breaks, every export drifts from what the
   user actually saw.
2. **Stable ids.** Ids come from the block's heading path plus its ordinal
   within that path, so the same document always produces the same ids and a
   client can hold a reference to a block across reloads.
3. **SOURCE blocks are immutable.** A citation line must never be rewritten
   by an operation — that is precisely how a report silently loses its
   evidence. ``apply_block_op`` raises rather than touching one.
4. **claim_ids bind evidence to blocks.** A block carries the ids of the
   claims it contains. This is what makes per-block verification and
   contradiction checks possible; without it, "verify this paragraph" has
   nothing to verify against.
5. **Hidden blocks vanish from the projection but persist in the model**, so
   hiding is reversible and exports omit them.
6. **Nothing is fabricated.** A claim is only bound to a block whose text
   actually contains it. A block with no claims stays claim-less rather than
   being given a plausible-looking neighbour.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

# Headings we recognise as introducing the citation list. Blocks beneath one
# of these are SOURCE blocks and therefore immutable.
_SOURCE_HEADINGS = frozenset({"sources", "references", "bibliography", "works cited"})

# A claim must appear in a block to at least this degree of overlap before it
# is bound. Matching on the claim's leading words keeps it cheap while making
# accidental binding to an unrelated block effectively impossible.
_CLAIM_MATCH_CHARS = 40

# Minimum chars for a claim to be considered bindable at all. Very short
# claims ("it works") would match almost anything.
_MIN_CLAIM_CHARS = 12


class BlockKind(StrEnum):
    """The kind of content a block holds.

    Kept deliberately small: the kinds are chosen by structure, not by
    semantics, so classification stays deterministic.
    """

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    QUOTE = "quote"
    CODE = "code"
    SOURCE = "source"  # a citation line — never edited by an operation


class ImmutableBlockError(Exception):
    """Raised when an operation targets a SOURCE block."""


BlockOrigin = Literal[
    "model",
    "evidence_fallback",
    "user",
    "deepened",
    "condensed",
]


@dataclass
class ReportBlock:
    """One addressable unit of a report."""

    id: str
    kind: BlockKind
    text: str
    level: int | None = None  # heading level, 1-6
    claim_ids: list[str] = field(default_factory=list)
    hidden: bool = False
    origin: BlockOrigin = "model"
    parent_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": str(self.kind),
            "text": self.text,
            "level": self.level,
            "claim_ids": list(self.claim_ids),
            "hidden": self.hidden,
            "origin": self.origin,
            "parent_id": self.parent_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportBlock:
        return cls(
            id=str(data["id"]),
            kind=BlockKind(data["kind"]),
            text=str(data.get("text", "")),
            level=data.get("level"),
            claim_ids=list(data.get("claim_ids") or []),
            hidden=bool(data.get("hidden", False)),
            origin=data.get("origin", "model"),
            parent_id=data.get("parent_id"),
        )


@dataclass
class ReportDocument:
    """A report as a list of addressable blocks.

    ``blocks`` is the source of truth. ``claims`` / ``entities`` / ``gaps`` are
    carried alongside so a consumer can compute trust and next steps without
    re-fetching them.
    """

    job_id: str
    query: str
    blocks: list[ReportBlock] = field(default_factory=list)
    quality: str = "ok"
    user_message: str = ""
    claims: list[Any] = field(default_factory=list)
    entities: list[Any] = field(default_factory=list)
    gaps: list[Any] = field(default_factory=list)

    # ── Construction ─────────────────────────────────────────────────────────

    @classmethod
    def from_markdown(
        cls,
        job_id: str,
        query: str,
        markdown: str,
        *,
        claims: list[Any] | None = None,
        entities: list[Any] | None = None,
        gaps: list[Any] | None = None,
        quality: str = "ok",
        user_message: str = "",
    ) -> ReportDocument:
        """Build a document from Markdown, binding claims to their blocks.

        ``markdown`` may be empty — the result always renders something
        readable, because a report that renders as nothing is a broken
        product.
        """
        raw_blocks = _parse_markdown(markdown or "")
        blocks = _assign_ids(raw_blocks)
        doc = cls(
            job_id=job_id,
            query=query,
            blocks=blocks,
            quality=quality,
            user_message=user_message,
            claims=list(claims or []),
            entities=list(entities or []),
            gaps=list(gaps or []),
        )
        if not doc.blocks:
            doc.blocks = _assign_ids([(BlockKind.PARAGRAPH, _EMPTY_NOTE, None)])
        doc._bind_claims()
        return doc

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportDocument:
        return cls(
            job_id=str(data.get("job_id", "")),
            query=str(data.get("query", "")),
            blocks=[ReportBlock.from_dict(b) for b in data.get("blocks") or []],
            quality=str(data.get("quality", "ok")),
            user_message=str(data.get("user_message", "")),
            claims=list(data.get("claims") or []),
            entities=list(data.get("entities") or []),
            gaps=list(data.get("gaps") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        """Plain-data projection for orjson serialisation in JobStateManager."""
        return {
            "job_id": self.job_id,
            "query": self.query,
            "blocks": [b.to_dict() for b in self.blocks],
            "quality": self.quality,
            "user_message": self.user_message,
            "claims": list(self.claims),
            "entities": list(self.entities),
            "gaps": list(self.gaps),
        }

    # ── Projection ───────────────────────────────────────────────────────────

    def to_markdown(self) -> str:
        """Render the visible blocks back to Markdown.

        Hidden blocks are omitted. This is the only supported way to obtain
        the report text — callers must not mutate it.
        """
        parts: list[str] = []
        for block in self.blocks:
            if block.hidden:
                continue
            parts.append(_render_block(block))
        return "\n\n".join(p for p in parts if p).strip() + "\n"

    def visible_blocks(self) -> list[ReportBlock]:
        return [b for b in self.blocks if not b.hidden]

    # ── Operations ───────────────────────────────────────────────────────────

    def apply_block_op(
        self,
        block_id: str,
        op: str,
        *,
        new_text: str | None = None,
    ) -> ReportDocument | None:
        """Apply an edit to one block, returning a new document.

        ``op`` is the operation verb (``"condense"``, ``"deepen"``, ``"rewrite"``,
        ...) and is recorded on the block as the resulting origin so a later
        reader can tell model prose from an edit.

        Returns ``None`` when ``block_id`` is unknown so callers can report a
        stale reference rather than crashing.

        SOURCE blocks raise :class:`ImmutableBlockError`: a citation is the
        evidence, and rewriting it is how a report quietly stops being
        truthful.
        """
        target = self._find(block_id)
        if target is None:
            return None
        if target.kind is BlockKind.SOURCE:
            raise ImmutableBlockError(f"block {block_id!r} is a SOURCE block and cannot be modified")
        if new_text is None:
            return None

        updated = ReportDocument.from_dict(self.to_dict())
        clone = next(b for b in updated.blocks if b.id == block_id)
        clone.text = new_text
        clone.origin = _ORIGIN_FOR_OP.get(op, "user")
        return updated

    def set_hidden(self, block_id: str, hidden: bool) -> ReportDocument:
        updated = ReportDocument.from_dict(self.to_dict())
        clone = next((b for b in updated.blocks if b.id == block_id), None)
        if clone is None:
            return self
        clone.hidden = hidden
        return updated

    def _find(self, block_id: str) -> ReportBlock | None:
        return next((b for b in self.blocks if b.id == block_id), None)

    # ── Evidence binding ─────────────────────────────────────────────────────

    def _bind_claims(self) -> None:
        """Attach each claim to the block whose text actually contains it.

        A claim that matches no block is left unbound rather than attached to
        the nearest-looking paragraph — inventing provenance is worse than
        having none.
        """
        if not self.claims:
            return
        for claim in self.claims:
            claim_id = getattr(claim, "id", None)
            text = (getattr(claim, "text", "") or "").strip()
            if not claim_id or len(text) < _MIN_CLAIM_CHARS:
                continue
            needle = _normalise(text[:_CLAIM_MATCH_CHARS])
            for block in self.blocks:
                if block.kind is BlockKind.SOURCE:
                    continue
                if needle and needle in _normalise(block.text):
                    if claim_id not in block.claim_ids:
                        block.claim_ids.append(claim_id)
                    break


# Operation verb -> the origin recorded on the block. Verbs that do not map to
# a known provenance state fall back to "user", which is honest: a manual edit
# is a user edit.
_ORIGIN_FOR_OP: dict[str, BlockOrigin] = {
    "condense": "condensed",
    "deepen": "deepened",
    "expand": "deepened",
}

_EMPTY_NOTE = "This report has no content. The research run did not produce any readable output."


# ── Markdown parsing ─────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _parse_markdown(markdown: str) -> list[tuple[BlockKind, str, int | None]]:
    """Split Markdown into ``(kind, text, heading_level)`` chunks.

    Chunks are separated by blank lines. Blocks after a ``## Sources``-style
    heading become SOURCE blocks.
    """
    chunks = [c.strip() for c in re.split(r"\n\s*\n", markdown) if c.strip()]
    out: list[tuple[BlockKind, str, int | None]] = []
    in_sources = False

    for chunk in chunks:
        lines = chunk.split("\n")
        heading_match = _HEADING_RE.match(lines[0].strip())
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            out.append((BlockKind.HEADING, f"{'#' * level} {title}", level))
            in_sources = title.strip().lower().lstrip("# ") in _SOURCE_HEADINGS
            continue

        kind = _classify(chunk, in_sources)
        out.append((kind, chunk, None))

    return out


def _classify(chunk: str, in_sources: bool) -> BlockKind:
    if in_sources:
        return BlockKind.SOURCE
    stripped = chunk.lstrip()
    if stripped.startswith(("- ", "* ", "1. ")) or re.match(r"^\d+\.\s", stripped):
        return BlockKind.LIST
    if stripped.startswith("|"):
        return BlockKind.TABLE
    if stripped.startswith(">"):
        return BlockKind.QUOTE
    if stripped.startswith("```"):
        return BlockKind.CODE
    return BlockKind.PARAGRAPH


def _assign_ids(
    raw_blocks: list[tuple[BlockKind, str, int | None]],
) -> list[ReportBlock]:
    """Give every block a deterministic id from its heading path + ordinal.

    Deterministic is the whole point: a client holding a block id across a
    reload must get the same block, and a rename elsewhere in the document
    must not re-key unrelated blocks.
    """
    heading_path: list[str] = []
    ordinal = 0
    blocks: list[ReportBlock] = []

    for kind, text, level in raw_blocks:
        if kind is BlockKind.HEADING and level is not None:
            heading_path = heading_path[: max(0, level - 1)]
            while len(heading_path) < level - 1:
                heading_path.append("")
            heading_path.append(_slug(text))
            ordinal = 0
        else:
            ordinal += 1

        path_key = "/".join(heading_path)
        block_id = f"b_{_short_hash(f'{path_key}|{ordinal}|{text[:60]}')}"
        blocks.append(
            ReportBlock(
                id=block_id,
                kind=kind,
                text=text,
                level=level,
                parent_id=(
                    f"b_{_short_hash('/'.join(heading_path[:-1]))}"
                    if kind is BlockKind.HEADING and len(heading_path) > 1
                    else None
                ),
            )
        )
    return blocks


def _render_block(block: ReportBlock) -> str:
    """Render one block back to Markdown, preserving its original form."""
    if block.kind is BlockKind.HEADING:
        return block.text
    if block.kind is BlockKind.LIST:
        # Items were stored as a single chunk with newlines; keep them joined.
        return block.text
    if block.kind is BlockKind.TABLE:
        return block.text
    if block.kind is BlockKind.QUOTE:
        return block.text
    if block.kind is BlockKind.CODE:
        return block.text
    return block.text


# ── Helpers ──────────────────────────────────────────────────────────────────


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _short_hash(value: str) -> str:
    # Not a security primitive — this only needs to be a stable, short,
    # collision-resistant-enough discriminator for block ids. sha256 keeps the
    # insecure-hash linter quiet without changing behaviour.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


# ── Trust ────────────────────────────────────────────────────────────────────

_STATUS_KEYS = ("supported", "contested", "refuted", "unverified")


def trust_summary(doc: ReportDocument) -> dict[str, int]:
    """Count claims by verification status, plus totals for sources and gaps.

    Counts are derived from the claims the pipeline actually produced — never
    estimated, and never adjusted upward to look better.
    """
    summary: dict[str, int] = dict.fromkeys(_STATUS_KEYS, 0)
    summary["total"] = 0
    for claim in doc.claims:
        status = str(getattr(claim, "status", "") or "unverified")
        summary["total"] += 1
        summary[status if status in summary else "unverified"] += 1
    summary["sources"] = sum(1 for b in doc.blocks if b.kind is BlockKind.SOURCE and not b.hidden)
    summary["gaps"] = len(doc.gaps)
    return summary


def trust_headline(summary: dict[str, int]) -> str:
    """One plain-language line describing how much of the report is supported.

    A contested or refuted claim is always named. The purpose of this line is
    to let a reader calibrate trust at a glance, and hiding the problems would
    defeat it.
    """
    if not summary.get("total"):
        return "No individual claims could be extracted from this report's sources."

    bits = [f"{summary['total']} findings"]
    if summary["supported"]:
        bits.append(f"{summary['supported']} well-supported")
    if summary["contested"]:
        bits.append(f"{summary['contested']} contested")
    if summary["refuted"]:
        bits.append(f"{summary['refuted']} refuted")
    if summary["unverified"]:
        bits.append(f"{summary['unverified']} not yet verified")
    return " · ".join(bits)
