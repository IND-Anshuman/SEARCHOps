"""Tests for PDF export.

No PDF library is installed (reportlab, weasyprint, fpdf2 are all absent), so
the export renders HTML and prints it through Playwright's Chromium, which is
already a dependency and ships in the Dockerfile.

The contract that matters: a PDF must be a *real* PDF whose text is
selectable, not an image of one. A user who receives a scanned-image PDF
cannot search or copy it, and only discovers that after forwarding it.
"""

from __future__ import annotations

import pytest

from searchops.application.exports.pdf_export import (
    ExportUnavailableError,
    export_pdf,
    render_html,
)
from searchops.application.report_document import ReportDocument

MD_DOC = (
    "# Quantum Error Correction\n\n"
    "## Executive Summary\n\nSurface codes are the leading approach.\n\n"
    "## Findings\n\nLogical error rates fall with code distance.\n\n"
    "## Sources\n\n- [IBM](https://ibm.com/a)\n"
)


def _doc() -> ReportDocument:
    return ReportDocument.from_markdown("job1", "qec", MD_DOC)


# ── HTML rendering (no browser needed) ───────────────────────────────────────


def test_render_html_includes_report_content():
    html = render_html(_doc(), title="QEC")
    assert "Executive Summary" in html
    assert "Surface codes are the leading approach." in html


def test_render_html_includes_every_source_url():
    html = render_html(_doc(), title="QEC")
    assert "https://ibm.com/a" in html


def test_render_html_is_self_contained():
    """No external CSS or fonts: a print job must not depend on the network."""
    html = render_html(_doc(), title="QEC")
    assert "fonts.googleapis" not in html
    assert "<style>" in html
    assert "<link" not in html


def test_render_html_escapes_untrusted_content():
    """Report text is LLM output; injecting it raw would be an XSS vector in
    any browser that opens the exported HTML."""
    doc = ReportDocument.from_markdown("j", "q", "## X\n\n<script>alert(1)</script>\n")
    html = render_html(doc, title="X")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_html_omits_hidden_blocks():
    doc = _doc()
    hidden = doc.set_hidden(doc.blocks[1].id, True)
    html = render_html(hidden, title="QEC")
    assert "Executive Summary" not in html


def test_render_html_shows_provenance_banner_for_degraded_report():
    doc = _doc()
    doc.quality = "refusal"
    doc.user_message = "The model declined, so this was built from sources."
    html = render_html(doc, title="QEC")
    assert "The model declined" in html


# ── PDF output (needs Chromium) ──────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_export_pdf_produces_a_real_pdf_with_selectable_text(tmp_path):
    pdf = await export_pdf(_doc(), out_dir=tmp_path, title="QEC")
    assert pdf.suffix == ".pdf"
    assert pdf.exists()
    assert pdf.stat().st_size > 0

    raw = pdf.read_bytes()
    # A real PDF starts with the header; anything else is not a PDF.
    assert raw[:5] == b"%PDF-", raw[:20]

    text = _extract_text(raw)
    assert text.strip(), "PDF must contain selectable text, not an image"
    assert "Surface codes" in text


@pytest.mark.asyncio
async def test_export_pdf_preserves_source_urls(tmp_path):
    pdf = await export_pdf(_doc(), out_dir=tmp_path, title="QEC")
    assert "https://ibm.com/a" in _extract_text(pdf.read_bytes())


@pytest.mark.asyncio
async def test_export_pdf_from_empty_document_still_produces_a_file(tmp_path):
    doc = ReportDocument.from_markdown("j", "q", "")
    pdf = await export_pdf(doc, out_dir=tmp_path, title="Empty")
    assert pdf.exists()
    assert pdf.read_bytes()[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_export_pdf_filename_is_safe(tmp_path):
    pdf = await export_pdf(_doc(), out_dir=tmp_path, title="QEC / Report: 2026?")
    assert pdf.parent == tmp_path
    assert " " not in pdf.name
    assert "/" not in pdf.name


# ── Failure mode ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_chromium_raises_a_clear_error(tmp_path, monkeypatch):
    """An export that silently returns an empty file is worse than one that
    fails: the user finds out after sending it."""
    import searchops.application.exports.pdf_export as mod

    def _boom(*_a, **_k):
        raise RuntimeError("Executable doesn't exist")

    monkeypatch.setattr(mod, "_launch", _boom)
    with pytest.raises(ExportUnavailableError) as exc:
        await export_pdf(_doc(), out_dir=tmp_path, title="QEC")
    assert "Chromium" in str(exc.value)


def _extract_text(raw: bytes) -> str:
    """Extract text from a PDF using pymupdf.

    Chromium compresses its text streams, so scanning the raw bytes for text
    operators finds nothing even though the text is present and selectable.
    pymupdf is already a project dependency (the scraper uses it), so this
    stays honest about "is there selectable text" without adding anything.
    """

    import pymupdf

    with pymupdf.open(stream=raw, filetype="pdf") as doc:
        return "".join(page.get_text() for page in doc)
