"""Real-PDF structural checks.

These render an actual PDF through Chromium and inspect the page map. The
defects they guard are invisible to an HTML-string assertion: a blank leaf
produced by a page-break interaction, and a report whose length drifts far from
what the profile declares. Both shipped once and were only found by opening the
finished file.
"""

import re

import pytest

from searchops.application.exports.pdf_export import export_pdf, render_html
from searchops.application.report_document import ReportDocument

MD = (
    "# Research Report\n\n"
    "## Abstract\n\n" + ("Abstract body text. " * 40) + "\n\n"
    "## Introduction\n\n" + ("Introduction body text. " * 60) + "\n\n"
    "### A subsection\n\n" + ("Subsection body. " * 40) + "\n\n"
    "## Main Results\n\n" + ("Results body text. " * 60) + "\n\n"
    "## Sources\n\n- [A](https://a.example/x)\n- [B](https://b.example/y)\n"
)


def _doc() -> ReportDocument:
    return ReportDocument.from_markdown("j", "a test query", MD)


# ── Page map (needs Chromium) ───────────────────────────────────────────────


def _page_texts(path) -> list[str]:
    import pymupdf

    with pymupdf.open(str(path)) as pdf:
        return [p.get_text().strip() for p in pdf]


@pytest.mark.asyncio
async def test_no_blank_page_after_the_cover(tmp_path):
    """A cover with a named page emitted an empty leaf behind it.

    The leaf carried only the running header, so it looked like a defect in the
    finished document rather than a stylesheet bug.
    """
    pdf = await export_pdf(_doc(), out_dir=tmp_path, title="Structural Test")
    texts = _page_texts(pdf)
    assert len(texts) >= 3, "cover + contents + body at minimum"
    # Page 1 is the cover; the contents must start on page 2 with no gap.
    assert "Table of Contents" in texts[1], f"contents not on page 2: {texts[1][:80]!r}"
    blank = [i + 1 for i, t in enumerate(texts) if not t]
    assert not blank, f"blank pages in the finished PDF: {blank}"


@pytest.mark.asyncio
async def test_cover_title_is_not_duplicated(tmp_path):
    """The API passes the query as the title; printing both repeated it."""
    title = "a test query"
    pdf = await export_pdf(_doc(), out_dir=tmp_path, title=title)
    cover = _page_texts(pdf)[0]
    assert cover.lower().count(title.lower()) == 1, f"title repeated on cover: {cover[:200]!r}"


@pytest.mark.asyncio
async def test_cover_shows_a_distinct_subtitle_when_title_differs(tmp_path):
    pdf = await export_pdf(_doc(), out_dir=tmp_path, title="A Distinct Title")
    cover = _page_texts(pdf)[0]
    assert "A Distinct Title" in cover
    assert "a test query" in cover


@pytest.mark.asyncio
async def test_contents_lists_sections_but_not_subsections(tmp_path):
    """Enumerating 55 subsections is a second copy of the document, and it
    overflowed its own page."""
    pdf = await export_pdf(_doc(), out_dir=tmp_path, title="Structural Test")
    toc = _page_texts(pdf)[1]
    assert "Abstract" in toc
    assert "Introduction" in toc
    assert "A subsection" not in toc, "H3 belongs in the body, not the contents"


@pytest.mark.asyncio
async def test_every_page_is_numbered_except_the_cover(tmp_path):
    pdf = await export_pdf(_doc(), out_dir=tmp_path, title="Structural Test")
    texts = _page_texts(pdf)
    for i, t in enumerate(texts[1:], start=2):
        assert str(i) in t, f"page {i} carries no page number"


# ── HTML-level checks (no browser) ──────────────────────────────────────────


def test_contents_excludes_subsections_in_html():
    html = render_html(_doc(), title="T")
    toc = html.split("Table of Contents", 1)[1].split("</table>", 1)[0]
    assert "Abstract" in toc
    assert "A subsection" not in toc


def test_cover_does_not_use_a_named_page():
    """Regression guard: the named page is what produced the blank leaf."""
    html = render_html(_doc(), title="T")
    assert "page: cover;" not in html


def test_cover_fits_inside_the_printable_box():
    """A cover taller than the printable area overflows onto a second page,
    which pushes the contents back and leaves a blank leaf behind."""
    from pathlib import Path

    src = Path("src/searchops/application/exports/pdf_export.py").read_text(encoding="utf-8")
    height = float(re.search(r"\.cover \{\s*height:\s*([\d.]+)cm;", src).group(1))
    top_margin = float(re.search(r"margin:\s*([\d.]+)cm", src).group(1))
    printable = 29.7 - (top_margin * 2)  # A4 height minus top and bottom
    assert height < printable, f"cover {height}cm exceeds the {printable:.1f}cm printable box"


def test_print_margin_matches_the_stylesheet():
    """A mismatch lets Playwright's margin silently override the @page rule."""
    from pathlib import Path

    src = Path("src/searchops/application/exports/pdf_export.py").read_text(encoding="utf-8")
    css_margin = re.search(r"margin:\s*([\d.]+cm [\d.]+cm [\d.]+cm [\d.]+cm);", src)
    assert css_margin, "no @page margin found"
    assert css_margin.group(1) == "2cm 1.8cm 2cm 1.8cm", "stylesheet margin changed; update the pdf() call"
    assert '"top": "2cm"' in src, "Playwright margin must match the @page margin"
