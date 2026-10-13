"""Export a ReportDocument to Word (.docx).

Same contract as the deck exporter: the reader must be able to check what they
are given. Headings map to real Word heading styles (so the navigation pane and
any downstream tooling work), every source URL is preserved, and per Q4 the
claim provenance is attached to each cited block rather than discarded.

Word is the format that ends up in a compliance record or an email thread, so
fidelity matters more here than anywhere else in the export path.
"""

from __future__ import annotations

import re
from pathlib import Path  # noqa: TC003 — used at runtime for out_dir.mkdir()

import structlog
from docx import Document
from docx.shared import Pt

from searchops.application.report_document import (
    BlockKind,
    ReportBlock,
    ReportDocument,
    _field,
)

log = structlog.get_logger(__name__)


def _clean_markdown(text: str) -> str:
    """Sanitize markdown formatting syntax into clean document text."""
    if not text:
        return ""
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^>\s*", "", text)
    return text.strip()


def export_docx(
    doc: ReportDocument,
    *,
    out_dir: Path,
    title: str = "Research Report",
) -> Path:
    """Write the document as a .docx and return its path."""
    document = Document()

    document.add_heading(title, level=0)
    if doc.query:
        p = document.add_paragraph(doc.query)
        p.style = document.styles["Subtitle"] if "Subtitle" in [s.name for s in document.styles] else p.style

    # Honest provenance banner. A synthesised report must not be silently
    # indistinguishable from a model-written one once it leaves the app.
    if doc.quality and doc.quality != "ok":
        note = document.add_paragraph()
        note.add_run(f"Report quality: {doc.quality}").italic = True
        if doc.user_message:
            note.add_run(f"\n{doc.user_message}").italic = True

    for block in doc.visible_blocks():
        _render_block(document, block, doc)

    _render_sources(document, doc)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _safe_filename(title, ".docx")
    document.save(str(path))
    log.info("export.docx.written", path=str(path))
    return path


def _footnote_text(doc: ReportDocument, block: ReportBlock) -> str:
    """Provenance text for a cited block.

    Empty when the block carries no claims — never a fabricated attribution.
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
            url = str(_field(citation, "url", "") or "")
            if url.startswith("http"):
                break
        lines.append(f"{text}{f'  [{url}]' if url else ''}")
    if not lines:
        return ""
    return "Claims supporting this section: " + "; ".join(lines)


def _render_block(document: Document, block: ReportBlock, doc: ReportDocument) -> None:
    if block.kind is BlockKind.HEADING:
        level = min(block.level or 2, 9)
        document.add_heading(_clean_markdown(block.text), level=level)
        return

    if block.kind is BlockKind.SOURCE:
        return  # rendered once, at the end

    if block.kind is BlockKind.LIST:
        for line in block.text.split("\n"):
            item = _clean_markdown(line)
            if item:
                document.add_paragraph(item, style="List Bullet")
    elif block.kind is BlockKind.QUOTE:
        p = document.add_paragraph(_clean_markdown(block.text))
        p.style = document.styles["Intense Quote"] if "Intense Quote" in [s.name for s in document.styles] else p.style
    elif block.kind is BlockKind.CODE:
        p = document.add_paragraph(block.text.strip("`"))
        for run in p.runs:
            run.font.name = "Consolas"
            run.font.size = Pt(9)
    else:
        document.add_paragraph(_clean_markdown(block.text))

    footnote = _footnote_text(doc, block)
    if footnote:
        p = document.add_paragraph()
        run = p.add_run(footnote)
        run.italic = True
        run.font.size = Pt(9)


def _render_sources(document: Document, doc: ReportDocument) -> None:
    urls: list[str] = []
    seen: set[str] = set()
    for block in doc.visible_blocks():
        if block.kind is not BlockKind.SOURCE:
            continue
        for url in re.findall(r"https?://[^\s)\]\"]+", block.text):
            if url not in seen:
                seen.add(url)
                urls.append(url)

    if not urls:
        return

    document.add_heading("Sources", level=1)
    for url in urls:
        document.add_paragraph(url, style="List Bullet")


def _safe_filename(title: str, suffix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "report"
    return f"{slug[:60]}{suffix}"
