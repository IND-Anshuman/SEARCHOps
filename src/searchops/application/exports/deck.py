"""Export a ReportDocument to PowerPoint.

A deck is only useful if the reader can still check it. Every source URL from
the report is carried into the deck, and each slide's speaker notes record the
claims that back it (Q4) — that provenance binding is the part a generic
"markdown to slides" converter cannot produce, because it has no claim→block
binding to read from.

Design notes:
- One slide per HEADING block, with the content beneath it as bullets. This
  mirrors how the report is actually structured rather than paginating by
  character count, so a reader who knows the report can navigate the deck.
- SOURCE blocks are collected into a final Sources slide. They are never
  folded into a content slide, because a citation belongs in one findable
  place.
- Hidden blocks are omitted: the export reflects what the user chose to show.
- An empty document still yields a deck with a single explanatory slide. A
  file that cannot be opened is worse than no file, because the user finds out
  after sending it.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime for out_dir.mkdir()

import structlog
from pptx import Presentation
from pptx.util import Inches, Pt

from searchops.application.report_document import BlockKind, ReportBlock, ReportDocument

log = structlog.get_logger(__name__)

_MAX_BULLETS_PER_SLIDE = 8


def export_deck(
    doc: ReportDocument,
    *,
    out_dir: Path,
    title: str = "Research Report",
) -> Path:
    """Write the document as a .pptx and return its path.

    Raises on a genuine failure — an export that silently returns nothing is
    how a user ends up sending an empty deck.
    """
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)

    _add_title_slide(prs, doc, title)

    sections = _group_by_heading(doc)
    if not sections:
        _add_plain_slide(prs, title, [_EMPTY_NOTE])
    for heading, blocks in sections:
        _add_content_slide(prs, heading, blocks, doc)

    _add_sources_slide(prs, doc)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _safe_filename(title, ".pptx")
    prs.save(str(path))
    log.info("export.deck.written", path=str(path), slides=len(prs.slides))
    return path


def _claim_provenance(doc: ReportDocument, block: ReportBlock) -> str:
    """Speaker-note text recording the claims that back this block.

    Returns an empty string when the block carries no claims — never a
    fabricated attribution.
    """
    if not block.claim_ids:
        return ""
    by_id = {str(getattr(c, "id", "")): c for c in doc.claims}
    lines: list[str] = []
    for cid in block.claim_ids:
        claim = by_id.get(cid)
        if claim is None:
            continue
        text = str(getattr(claim, "text", "") or "").strip()
        if not text:
            continue
        url = ""
        for citation in getattr(claim, "citations", None) or []:
            url = str(getattr(citation, "url", "") or "")
            if url.startswith("http"):
                break
        lines.append(f"{text}{f'  [{url}]' if url else ''}")
    if not lines:
        return ""
    return "Claims backing this slide:\n" + "\n".join(f"- {ln}" for ln in lines)


# ── Internals ────────────────────────────────────────────────────────────────

_EMPTY_NOTE = "This report has no content. The research run produced no readable output."


def _add_title_slide(prs: Presentation, doc: ReportDocument, title: str) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
    if len(slide.placeholders) > 1:
        subtitle = slide.placeholders[1].text_frame
        subtitle.text = doc.query or "Research report"
        if doc.quality and doc.quality != "ok":
            subtitle.text += f"\n\nReport quality: {doc.quality}"
            if doc.user_message:
                subtitle.text += f"\n{doc.user_message}"


def _add_plain_slide(prs: Presentation, title: str, lines: list[str]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = title
    body = slide.placeholders[1].text_frame
    body.text = lines[0] if lines else _EMPTY_NOTE


def _group_by_heading(doc: ReportDocument) -> list[tuple[str, list[ReportBlock]]]:
    """Group visible blocks under their most recent heading."""
    sections: list[tuple[str, list[ReportBlock]]] = []
    current: tuple[str, list[ReportBlock]] | None = None

    for block in doc.visible_blocks():
        if block.kind is BlockKind.HEADING:
            current = (block.text.lstrip("# ").strip(), [])
            sections.append(current)
        elif block.kind is BlockKind.SOURCE:
            continue  # collected into the Sources slide
        elif current is not None:
            current[1].append(block)

    return [(h, b) for h, b in sections if b]


def _add_content_slide(
    prs: Presentation,
    heading: str,
    blocks: list[ReportBlock],
    doc: ReportDocument,
) -> None:
    bullets: list[str] = []
    for block in blocks:
        bullets.extend(_bullets_for(block))
        if len(bullets) >= _MAX_BULLETS_PER_SLIDE:
            break

    if not bullets:
        return

    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = heading or "Details"
    body = slide.placeholders[1].text_frame
    body.text = bullets[0]
    for line in bullets[1:]:
        body.add_paragraph().text = line
    for para in body.paragraphs:
        for run in para.runs:
            run.font.size = Pt(16)

    provenance = "\n\n".join(note for note in (_claim_provenance(doc, b) for b in blocks) if note)
    if provenance and slide.has_notes_slide:
        slide.notes_slide.notes_text_frame.text = provenance


def _bullets_for(block: ReportBlock) -> list[str]:
    text = block.text.strip()
    if not text:
        return []
    if block.kind is BlockKind.LIST:
        return [ln.lstrip("-*• ").strip() for ln in text.split("\n") if ln.strip().lstrip("-*• ").strip()]
    return [text]


def _add_sources_slide(prs: Presentation, doc: ReportDocument) -> None:
    urls: list[str] = []
    seen: set[str] = set()
    for block in doc.visible_blocks():
        if block.kind is not BlockKind.SOURCE:
            continue
        for url in _urls_in(block.text):
            if url not in seen:
                seen.add(url)
                urls.append(url)

    if not urls:
        return

    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Sources"
    body = slide.placeholders[1].text_frame
    body.text = urls[0]
    for url in urls[1:]:
        body.add_paragraph().text = url
    for para in body.paragraphs:
        for run in para.runs:
            run.font.size = Pt(14)


def _urls_in(text: str) -> list[str]:
    """Extract http(s) URLs, including markdown link targets."""
    import re

    return re.findall(r"https?://[^\s)\]\"]+", text)


def _safe_filename(title: str, suffix: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "report"
    return f"{slug[:60]}{suffix}"
