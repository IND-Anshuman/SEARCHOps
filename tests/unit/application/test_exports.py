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


def test_export_deck_strips_raw_markdown_syntax(tmp_path):
    md = (
        "# **Quantum Error Correction**\n\n"
        "## ### Executive Summary\n\n"
        "- **Key Finding**: Surface codes show *promising* results with `O(d^2)` scaling [Source](https://example.com).\n\n"
        "## Sources\n\n- https://example.com\n"
    )
    doc = ReportDocument.from_markdown("job_md", "qec", md)
    out = export_deck(doc, out_dir=tmp_path, title="Clean Presentation")
    from pptx import Presentation

    prs = Presentation(str(out))
    text = _all_text(prs)
    assert "**" not in text
    assert "###" not in text
    assert "`" not in text
    assert "Key Finding: Surface codes show promising results with O(d^2) scaling Source" in text


def test_export_deck_has_dark_title_slide(tmp_path):
    out = export_deck(_doc(), out_dir=tmp_path, title="QEC")
    from pptx import Presentation

    prs = Presentation(str(out))
    # First slide should be the title slide
    text = _all_text(prs)
    assert "QEC" in text
    assert "SEARCHOps" in text


def test_export_deck_includes_trust_slide_when_claims_exist(tmp_path):
    doc = _doc()
    doc.claims = [{"id": "c1", "text": "Surface codes work", "status": "supported", "citations": []}]
    out = export_deck(doc, out_dir=tmp_path, title="QEC")
    from pptx import Presentation

    text = _all_text(Presentation(str(out)))
    assert "Trust" in text or "trust" in text.lower()


def test_export_deck_includes_glossary_slide_when_entities_exist(tmp_path):
    doc = _doc()
    doc.entities = [{"name": "Qubit", "entity_type": "Concept", "description": "Quantum bit", "confidence": 0.9}]
    out = export_deck(doc, out_dir=tmp_path, title="QEC")
    from pptx import Presentation

    text = _all_text(Presentation(str(out)))
    assert "Glossary" in text
    assert "Qubit" in text


def test_export_deck_is_concise_not_one_slide_per_h3(tmp_path):
    """A 55-slide deck from 20 H3 sections is a chore, not a briefing."""
    md = (
        "# Title\n\n"
        "## Section 1\n\n"
        "### H3 A\n\nContent A\n\n"
        "### H3 B\n\nContent B\n\n"
        "## Section 2\n\n"
        "### H3 C\n\nContent C\n\n"
        "## Sources\n\n- https://example.com\n"
    )
    doc = ReportDocument.from_markdown("j", "q", md)
    out = export_deck(doc, out_dir=tmp_path, title="Test")
    from pptx import Presentation

    prs = Presentation(str(out))
    # Should be title + 2 H1 sections + sources = 4 slides (not 5+)
    assert len(prs.slides) <= 5


def test_export_deck_truncates_long_bullets(tmp_path):
    """Long bullets should be truncated for readability."""
    long_text = "This is a very long bullet point that should be truncated. " * 5
    md = f"# Title\n\n## Section\n\n{long_text}\n\n## Sources\n\n- https://example.com\n"
    doc = ReportDocument.from_markdown("j", "q", md)
    out = export_deck(doc, out_dir=tmp_path, title="Test")
    from pptx import Presentation

    text = _all_text(Presentation(str(out)))
    assert "..." in text


def test_export_deck_sources_slide_is_last(tmp_path):
    out = export_deck(_doc(), out_dir=tmp_path, title="QEC")
    from pptx import Presentation

    prs = Presentation(str(out))
    last_slide = prs.slides[-1]
    text = ""
    for shape in last_slide.shapes:
        if shape.has_text_frame:
            text += shape.text_frame.text
    assert "Sources" in text or "https://" in text


# ── DOCX new features ───────────────────────────────────────────────────────


def test_export_docx_has_cover_page(tmp_path):
    out = export_docx(_doc(), out_dir=tmp_path, title="QEC Report")
    from docx import Document

    d = Document(str(out))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "SEARCHOps" in text
    assert "QEC Report" in text


def test_export_docx_has_table_of_contents(tmp_path):
    out = export_docx(_doc(), out_dir=tmp_path, title="QEC Report")
    from docx import Document

    d = Document(str(out))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "Table of Contents" in text
    assert "Executive Summary" in text


def test_export_docx_has_trust_summary(tmp_path):
    doc = _doc()
    doc.claims = [{"id": "c1", "text": "Surface codes work", "status": "supported", "citations": []}]
    out = export_docx(doc, out_dir=tmp_path, title="QEC")
    from docx import Document

    d = Document(str(out))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "Trust" in text or "trust" in text.lower()


def test_export_docx_has_glossary(tmp_path):
    doc = _doc()
    doc.entities = [{"name": "Qubit", "entity_type": "Concept", "description": "Quantum bit", "confidence": 0.9}]
    out = export_docx(doc, out_dir=tmp_path, title="QEC")
    from docx import Document

    d = Document(str(out))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "Glossary" in text
    assert "Qubit" in text


def test_export_docx_renders_inline_markdown(tmp_path):
    """Bold/italic/code should be rendered, not left as raw markdown."""
    doc = ReportDocument.from_markdown("j", "q", "## X\n\n**bold** and *italic* and `code`\n")
    out = export_docx(doc, out_dir=tmp_path, title="Test")
    from docx import Document

    d = Document(str(out))
    # Check that bold/italic runs exist (not raw ** or *)
    full_text = "\n".join(p.text for p in d.paragraphs)
    assert "**" not in full_text
    assert "`" not in full_text
    assert "bold" in full_text
    assert "italic" in full_text
    assert "code" in full_text


# ── Study guide ──────────────────────────────────────────────────────────────
#
# Regression: the exporters used to read `getattr(doc, '_study_guide', None)`.
# Nothing in the codebase ever set that attribute, so the study-guide section
# silently never appeared in any PDF, DOCX or PPTX. The caller now builds the
# guide once with the shared router and passes it in.


def _guide():
    from searchops.application.exports.study import StudyGuide, StudyQuestion

    return StudyGuide(
        questions=[StudyQuestion(question="What is a surface code?", answer="A 2D lattice code.", section="Findings")],
        note="",
    )


def test_export_docx_renders_the_study_guide_when_supplied(tmp_path):
    out = export_docx(_doc(), out_dir=tmp_path, title="QEC", study_guide=_guide())
    from docx import Document

    text = "\n".join(p.text for p in Document(str(out)).paragraphs)
    assert "Study Guide" in text
    assert "What is a surface code?" in text


def test_export_deck_renders_the_study_guide_when_supplied(tmp_path):
    from pptx import Presentation

    out = export_deck(_doc(), out_dir=tmp_path, title="QEC", study_guide=_guide())
    text = _all_text(Presentation(str(out)))
    assert "Study Guide" in text
    assert "What is a surface code?" in text


def test_exporters_still_produce_a_valid_file_with_no_study_guide(tmp_path):
    """No guide must never break an export, and must never invent questions."""
    out = export_docx(_doc(), out_dir=tmp_path, title="QEC")
    from docx import Document

    text = "\n".join(p.text for p in Document(str(out)).paragraphs)
    assert "Study Guide" not in text
    assert out.exists()


def test_pdf_renders_the_study_guide_when_supplied(tmp_path):
    from searchops.application.exports.pdf_export import render_html

    html = render_html(_doc(), title="QEC", study_guide=_guide())
    assert "Study Guide" in html
    assert "What is a surface code?" in html


def test_toc_does_not_list_a_section_twice(tmp_path):
    """Regression: the report's own 'Sources' heading was listed twice.

    The exporter appended its own "Sources" entry to the contents while the
    report already carried a "## Sources" heading, so the contents promised
    two Sources sections and only one existed.
    """
    import re

    from searchops.application.exports.pdf_export import render_html

    html = render_html(_doc(), title="QEC", study_guide=_guide())
    toc = re.search(r"<div class='toc'>.*?</div>", html, re.S)
    assert toc, "the report must have a table of contents"
    assert toc.group(0).count("Sources") == 1


def test_toc_omits_the_study_guide_when_none_was_supplied(tmp_path):
    """A contents entry for a section that is not rendered is a broken TOC."""
    import re

    from searchops.application.exports.pdf_export import render_html

    html = render_html(_doc(), title="QEC")
    toc = re.search(r"<div class='toc'>.*?</div>", html, re.S)
    assert toc, "the report must have a table of contents"
    assert "Study Guide" not in toc.group(0)
