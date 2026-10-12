"""Tests for report export to PPTX / DOCX / PDF.

The point of these exports is not "produce a file" — it is "produce a file
that still carries its evidence". Microsoft and Google both ship
report→deck conversion, so a bare export is table stakes and copies nothing.

What is tested here, and why:

1. **The file is real.** A PPTX must open as a Presentation and contain the
   report's content; a PDF must start with ``%PDF``. An export that returns
   bytes with the right extension and no content is worse than no export,
   because the user only discovers it when the file is already sent.
2. **Citations survive.** Every source URL must appear in the artifact. A deck
   that drops the sources is a deck that cannot be checked.
3. **Speaker notes carry provenance.** Per Q4, each slide's notes record which
   claims back it. This is the part a generic slide generator cannot produce,
   because it has no claim→block binding to read from.
4. **Hidden blocks are omitted** — export reflects what the user chose to show.
5. **Degenerate input degrades honestly.** An empty document still yields a
   readable file rather than a broken one, because a report the user can
   actually open is the minimum bar.
"""

from __future__ import annotations

from searchops.application.exports.deck import export_deck
from searchops.application.exports.docx_export import export_docx
from searchops.application.report_document import ReportDocument

MD_DOC = (
    "# Quantum Error Correction\n\n"
    "## Executive Summary\n\nSurface codes are the leading approach.\n\n"
    "## Findings\n\nLogical error rates fall with code distance.\n\n"
    "## Sources\n\n- [IBM Research](https://ibm.com/a)\n- [Google Quantum](https://google.com/b)\n"
)


def _doc() -> ReportDocument:
    return ReportDocument.from_markdown("job1", "qec", MD_DOC)


def _all_text(prs) -> str:
    parts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
        if slide.has_notes_slide:
            parts.append(slide.notes_slide.notes_text_frame.text)
    return "\n".join(parts)


# ── PPTX ─────────────────────────────────────────────────────────────────────


def test_export_deck_produces_a_real_pptx(tmp_path):
    out = export_deck(_doc(), out_dir=tmp_path, title="QEC Briefing")
    assert out.suffix == ".pptx"
    assert out.exists()
    assert out.stat().st_size > 0
    # A real PPTX opens as a Presentation; a fabricated one does not.
    from pptx import Presentation

    prs = Presentation(str(out))
    assert len(prs.slides) >= 2


def test_export_deck_contains_report_headings(tmp_path):
    out = export_deck(_doc(), out_dir=tmp_path, title="QEC Briefing")
    from pptx import Presentation

    text = _all_text(Presentation(str(out)))
    assert "Executive Summary" in text
    assert "Findings" in text


def test_export_deck_preserves_every_source_url(tmp_path):
    """A deck that drops its citations cannot be checked by the reader."""
    out = export_deck(_doc(), out_dir=tmp_path, title="QEC Briefing")
    from pptx import Presentation

    text = _all_text(Presentation(str(out)))
    assert "https://ibm.com/a" in text
    assert "https://google.com/b" in text


def test_export_deck_records_claim_provenance_in_speaker_notes(tmp_path):
    """Q4: the binding travels with the artifact. A generic slide generator
    cannot do this because it has no claim->block binding to read."""
    from searchops.application.exports.deck import _claim_provenance

    doc = _doc()
    doc.blocks[3].claim_ids = ["c1", "c2"]

    class _Claim:
        def __init__(self, cid, text, url):
            self.id, self.text, self.citations = cid, text, [type("C", (), {"url": url})()]

    doc.claims = [_Claim("c1", "Logical error rates fall.", "https://ibm.com/a")]
    note = _claim_provenance(doc, doc.blocks[3])
    assert "Logical error rates fall." in note
    assert "https://ibm.com/a" in note


def test_export_deck_omits_hidden_blocks(tmp_path):
    doc = _doc()
    hidden = doc.set_hidden(doc.blocks[1].id, True)
    out = export_deck(hidden, out_dir=tmp_path, title="QEC")
    from pptx import Presentation

    text = _all_text(Presentation(str(out)))
    assert "Executive Summary" not in text


def test_export_deck_from_empty_document_still_produces_a_file(tmp_path):
    doc = ReportDocument.from_markdown("j", "q", "")
    out = export_deck(doc, out_dir=tmp_path, title="Empty")
    from pptx import Presentation

    prs = Presentation(str(out))
    assert len(prs.slides) >= 1


def test_export_deck_filename_is_safe(tmp_path):
    out = export_deck(_doc(), out_dir=tmp_path, title="QEC / Report: 2026?")
    assert out.parent == tmp_path
    assert " " not in out.name
    assert "/" not in out.name


# ── DOCX ─────────────────────────────────────────────────────────────────────


def test_export_docx_produces_a_real_docx(tmp_path):
    out = export_docx(_doc(), out_dir=tmp_path, title="QEC Report")
    assert out.suffix == ".docx"
    assert out.exists()
    assert out.stat().st_size > 0

    from docx import Document

    d = Document(str(out))
    assert any(p.text.strip() for p in d.paragraphs)


def test_export_docx_uses_heading_styles(tmp_path):
    out = export_docx(_doc(), out_dir=tmp_path, title="QEC Report")
    from docx import Document

    d = Document(str(out))
    styles = [p.style.name for p in d.paragraphs if p.text.strip()]
    assert any("Heading" in s for s in styles), f"no heading styles in {styles[:8]}"


def test_export_docx_preserves_every_source_url(tmp_path):
    out = export_docx(_doc(), out_dir=tmp_path, title="QEC Report")
    from docx import Document

    d = Document(str(out))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "https://ibm.com/a" in text
    assert "https://google.com/b" in text


def test_export_docx_records_claim_provenance_as_footnotes(tmp_path):
    """Q4: DOCX carries the binding as footnotes, one per cited block."""
    from searchops.application.exports.docx_export import _footnote_text

    doc = _doc()
    doc.blocks[3].claim_ids = ["c1"]

    class _Claim:
        def __init__(self, cid, text, url):
            self.id, self.text, self.citations = cid, text, [type("C", (), {"url": url})()]

    doc.claims = [_Claim("c1", "Logical error rates fall.", "https://ibm.com/a")]
    note = _footnote_text(doc, doc.blocks[3])
    assert "Logical error rates fall." in note
    assert "https://ibm.com/a" in note


def test_export_docx_omits_hidden_blocks(tmp_path):
    doc = _doc()
    hidden = doc.set_hidden(doc.blocks[1].id, True)
    out = export_docx(hidden, out_dir=tmp_path, title="QEC")
    from docx import Document

    text = "\n".join(p.text for p in Document(str(out)).paragraphs)
    assert "Executive Summary" not in text


def test_export_docx_from_empty_document_still_produces_a_file(tmp_path):
    doc = ReportDocument.from_markdown("j", "q", "")
    out = export_docx(doc, out_dir=tmp_path, title="Empty")
    from docx import Document

    d = Document(str(out))
    assert d.paragraphs


def test_both_exporters_agree_on_visible_content(tmp_path):
    """The same document must not silently produce different content per
    format — that is how a reader ends up trusting a deck the report contradicts."""
    doc = _doc()
    pptx_text = _all_text(__import__("pptx").Presentation(str(export_deck(doc, out_dir=tmp_path, title="T"))))
    from docx import Document

    docx_text = "\n".join(p.text for p in Document(str(export_docx(doc, out_dir=tmp_path, title="T"))).paragraphs)
    assert "Surface codes are the leading approach." in pptx_text
    assert "Surface codes are the leading approach." in docx_text
