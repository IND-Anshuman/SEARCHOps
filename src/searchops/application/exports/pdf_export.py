"""Export a ReportDocument to PDF.

No PDF library is available in this project (reportlab, weasyprint and fpdf2
are all absent), so the report is rendered to self-contained HTML and printed
through Playwright's Chromium — already a dependency, and installed in the
Dockerfile.

Why HTML-first rather than a PDF library:
- The report is Markdown with tables, lists and code. HTML expresses those
  natively; a PDF library would mean re-implementing layout.
- Chromium is already required by the scraping tier, so this adds no new
  runtime dependency to the image.

The contract: a real PDF (``%PDF`` header) whose text is selectable. A scanned-
image PDF cannot be searched or copied, and the recipient only finds out
after forwarding it.

This module produces a complete, professional report artifact with:
- A cover page with title, date, and trust summary
- An automatic table of contents
- Headers and footers with page numbers
- A glossary of extracted entities
- A study guide derived from the report content
- All formatting syntax properly rendered (no raw ** or __ leaking)
"""

from __future__ import annotations

import html as html_mod
import re
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 — used at runtime for out_dir.mkdir()
from typing import Any

import structlog

from searchops.application.exports.glossary import build_glossary
from searchops.application.report_document import (
    BlockKind,
    ReportBlock,
    ReportDocument,
    _field,
    trust_headline,
    trust_summary,
)

log = structlog.get_logger(__name__)

# ── Theme variants ──────────────────────────────────────────────────────────
# Each theme overrides the base CSS with its own typography, spacing, and color
# choices. The base _CSS is always applied; the theme CSS is appended after it
# so theme rules win on equal specificity.

_CSS_THEMES: dict[str, str] = {
    "default": "",
    "minimal": """
        body { font-size: 10pt; line-height: 1.35; }
        .cover h1 { font-size: 26pt; }
        h2 { font-size: 12pt; margin: 10px 0 4px 0; }
        h3 { font-size: 10.5pt; margin: 8px 0 3px 0; }
        p { margin: 4px 0; }
    """,
    "academic": """
        body { font-size: 11pt; line-height: 1.5; }
        .cover h1 { font-size: 28pt; }
        h2 { font-size: 14pt; margin: 18px 0 8px 0; counter-increment: sec; }
        h3 { font-size: 12pt; margin: 12px 0 6px 0; }
        p { margin: 8px 0; text-align: justify; }
    """,
    "executive": """
        body { font-size: 10.5pt; line-height: 1.4; color: #111; }
        .cover h1 { font-size: 32pt; color: #000; }
        .cover .cover-subtitle { color: #333; }
        h2 { font-size: 14pt; margin: 16px 0 6px 0; color: #000; border-bottom: 3px solid #000; padding-bottom: 4px; }
        h3 { font-size: 11.5pt; margin: 10px 0 4px 0; color: #222; }
        p { margin: 5px 0; }
        .trust-box { border-left: 4px solid #000; }
    """,
}

_CSS = """
@page {
    size: A4;
    margin: 2cm 1.8cm 2cm 1.8cm;
}
/* The cover deliberately does NOT use a named page. A named page plus
   page-break-after emitted an extra leaf carrying only the running header, and
   at full A4 height the cover overflowed the 25.7cm printable box (29.7cm
   minus 2cm margins), pushing the contents to page 3 behind a blank page.
   Measured: 25.5cm -> contents on page 2, no blank. */
body { font-family: Georgia, 'Times New Roman', serif; font-size: 10.5pt;
       line-height: 1.42; color: #1a1a1a; }
.cover {
    height: 25.5cm;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    text-align: center;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    color: #fff;
    page-break-after: always;
}
.cover h1 { font-size: 30pt; border: none; padding: 0; margin: 0 0 20px 0;
            line-height: 1.2; }
.cover .cover-subtitle { font-size: 13pt; color: #ccc; margin: 10px 0 24px 0; }
.cover .cover-meta { font-size: 10pt; color: #999; margin-top: 32px; }
.cover .cover-badge { display: inline-block; padding: 4px 16px; margin: 6px;
                       border: 1px solid #4a9eff; border-radius: 3px;
                       font-size: 9pt; color: #4a9eff; }
.cover .trust-box { margin-top: 32px; padding: 18px 26px; text-align: left;
                    background: rgba(255,255,255,0.1); border-radius: 6px;
                    font-size: 10pt; max-width: 400px; }
.toc { page-break-after: always; }
.toc h2 { font-size: 17pt; border-bottom: 2px solid #333; padding-bottom: 8px; }
.toc table { border: none; font-size: 10.5pt; }
.toc td { border: none; padding: 2px 6px; }
.toc .toc-num { color: #666; width: 30px; }
.toc .toc-title { }
.toc .toc-dots { color: #999; }
h1 { font-size: 17pt; border-bottom: 2px solid #333; padding-bottom: 5px;
      string-set: section content(); page-break-before: always; }
h1:first-of-type { page-break-before: avoid; }
h2 { font-size: 14pt; margin: 18px 0 8px 0; color: #1a1a2e;
      border-bottom: 1px solid #ddd; padding-bottom: 4px; }
h3 { font-size: 11.5pt; margin: 12px 0 5px 0; color: #333;
      font-weight: bold; }
h4 { font-size: 10.5pt; margin: 10px 0 4px 0; color: #444;
      font-style: italic; }
p  { margin: 6px 0; }
ul, ol { margin: 6px 0 6px 18px; }
blockquote { border-left: 3px solid #999; margin-left: 0; padding-left: 12px;
             color: #444; font-style: italic; }
pre { background: #f4f4f4; padding: 8px; font-size: 9pt; overflow-x: auto;
      border-left: 3px solid #666; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 9.5pt; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt;
        page-break-inside: avoid; }
th { background: #f0f0f0; font-weight: bold; border: 1px solid #bbb;
     padding: 5px 8px; text-align: left; }
td { border: 1px solid #bbb; padding: 4px 6px; text-align: left; }
tr:nth-child(even) td { background: #fafafa; }
.provenance { border: 1px solid #c9a227; background: #fdf6e3; padding: 10px 14px;
              font-size: 9.5pt; margin: 12px 0 16px 0; border-radius: 4px; }
.trust-header { background: #e8f4e8; border: 1px solid #4a4; padding: 10px 14px;
                margin: 12px 0 16px 0; border-radius: 4px; font-size: 10pt; }
.footnote { color: #555; font-size: 8.5pt; font-style: italic; margin: 2px 0 10px 0; }
.math-display { margin: 12px 0; text-align: center; font-size: 1.1em; }
.math-inline { font-style: normal; }
.sources a { color: #1a4d8f; word-break: break-all; }
.sources ul { font-size: 9pt; }
.sources li { margin: 1px 0; }
.glossary { page-break-before: always; }
.glossary h2 { font-size: 17pt; border-bottom: 2px solid #333; padding-bottom: 8px; }
.glossary table { font-size: 9.5pt; }
.study-guide { page-break-before: always; }
.study-guide h2 { font-size: 17pt; border-bottom: 2px solid #333; padding-bottom: 8px; }
.study-guide .question { font-weight: bold; margin-top: 12px; }
.study-guide .answer { color: #333; margin: 4px 0 8px 16px; }
.section-avoid-break { page-break-inside: avoid; }
h2, h3, h4 { page-break-after: avoid; break-after: avoid; }
blockquote.callout { font-style: normal; color: #1a1a2e; background: #f3f5f9;
                     border-left: 4px solid #1a1a2e; padding: 8px 14px; }
sup.cite { font-size: 7pt; color: #777; line-height: 0; }
a { color: #1a4d8f; text-decoration: none; }
.summary-box { background: #f0f7ff; border: 1px solid #4a9eff; padding: 14px 18px;
               margin: 12px 0 16px 0; border-radius: 4px; }
""".strip()


def pdf_export_available() -> bool:
    """Whether this deployment can print PDFs on the server.

    False in the API-only image, which leaves Playwright and Chromium out; the
    web app then prints the report page through the browser instead.
    """
    import importlib.util

    return importlib.util.find_spec("playwright") is not None


class ExportUnavailableError(RuntimeError):
    """Raised when PDF export cannot run — e.g. Chromium is not installed.

    Deliberately not swallowed: an export that quietly returns an empty file
    is worse than one that fails, because the user discovers the problem after
    sending the document.
    """


def render_html(doc: ReportDocument, *, title: str, theme: str = "default", study_guide: Any = None) -> str:
    """Render the document as self-contained HTML.

    No external stylesheets, fonts, or scripts — a print job must not depend
    on the network. All report text is escaped: it is LLM output, and
    injecting it raw would be an injection vector in anything that opens the
    exported file in a browser.

    Produces a complete report with:
    1. Cover page with title, date, trust summary
    2. Table of contents
    3. Report body (all sections)
    4. Glossary of extracted entities
    5. Study guide with questions and answers

    ``theme`` selects a CSS variant from ``_CSS_THEMES``. Unknown themes
    fall back to the default silently — a bad theme string should never
    prevent a report from being exported.

    ``study_guide`` is a pre-built :class:`StudyGuide`. The exporter makes no
    LLM call itself: the export endpoint builds it once with the shared
    router and passes it in. When omitted, the study-guide section is skipped
    rather than fabricated.
    """
    theme_css = _CSS_THEMES.get(theme, "")
    parts: list[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{html_mod.escape(title)}</title>",
        f"<style>{_CSS}{theme_css}</style></head><body>",
    ]

    # 1. Cover page
    parts.append(_render_cover(doc, title))

    # 2. Table of contents
    parts.append(_render_toc(doc, study_guide))

    # 3. Report body
    parts.append(_render_body(doc))

    # 4. Glossary
    parts.append(_render_glossary(doc))

    # 5. Study guide
    parts.append(_render_study_guide(doc, study_guide))

    # 6. Sources (only from SOURCE blocks, not duplicated)
    parts.append(_render_sources(doc))

    parts.append("</body></html>")
    return "".join(parts)


def _render_cover(doc: ReportDocument, title: str) -> str:
    """Render a professional cover page."""
    today = datetime.now(UTC).strftime("%B %d, %Y")

    trust = trust_summary(doc)
    trust_line = trust_headline(trust)

    badges = []
    if trust["total"]:
        badges.append(f"<span class='cover-badge'>{trust['total']} findings</span>")
        if trust["supported"]:
            badges.append(f"<span class='cover-badge'>{trust['supported']} verified against sources</span>")
        if trust["contested"]:
            badges.append(f"<span class='cover-badge'>{trust['contested']} contested</span>")
        if trust["refuted"]:
            badges.append(f"<span class='cover-badge'>{trust['refuted']} refuted</span>")
        if trust["unverified"]:
            badges.append(f"<span class='cover-badge'>{trust['unverified']} unverified</span>")
    if doc.gaps:
        badges.append(f"<span class='cover-badge'>{len(doc.gaps)} gaps identified</span>")

    badge_html = " ".join(badges) if badges else ""

    quality_note = ""
    if doc.quality and doc.quality != "ok":
        msg = doc.user_message or f"Report quality: {doc.quality}"
        quality_note = f"<p class='cover-subtitle' style='color: #ff9999;'>⚠ {html_mod.escape(msg)}</p>"

    # The API passes the query as the title, so title and query are usually the
    # same string. Printing both put the identical sentence on the cover twice.
    subtitle = ""
    if doc.query and doc.query.strip() != title.strip():
        subtitle = f"<p class='cover-subtitle'>{html_mod.escape(doc.query)}</p>"

    return (
        f"<div class='cover'>"
        f"<h1>{html_mod.escape(title)}</h1>"
        f"{subtitle}"
        f"{quality_note}"
        f"<p>{badge_html}</p>"
        f"<div class='trust-box'>"
        f"<strong>Trust Summary</strong><br>{html_mod.escape(trust_line)}"
        f"</div>"
        f"<p class='cover-meta'>Generated {today} · SEARCHOps Research Platform</p>"
        f"</div>"
    )


def _render_toc(doc: ReportDocument, study_guide: Any = None) -> str:
    """Render a table of contents from the document's top-level headings.

    Only H1 and H2 are listed. The report has ~55 H3 subsections, and a
    contents page enumerating every one of them is not a contents page — it is
    a second copy of the document. It also overflowed its own page, leaving a
    blank leaf behind it.
    """
    toc_entries: list[tuple[str, int]] = []
    for block in doc.visible_blocks():
        if block.kind is BlockKind.HEADING:
            level = block.level or 1
            if level > 2:
                continue  # H3+ belongs in the body, not the contents
            text = _strip_markdown(block.text)
            # The generated "# Research Report" title is not a section.
            if level == 1 and text.lower() in (
                "research report",
                doc.query.strip().lower(),
                doc.report_title().lower(),
            ):
                continue
            if text:
                toc_entries.append((text, level))

    if not toc_entries:
        return ""

    rows = []
    for i, (text, level) in enumerate(toc_entries, 1):
        indent = "&nbsp;&nbsp;" * (level - 1)
        rows.append(
            f"<tr><td class='toc-num'>{i}.</td>"
            f"<td class='toc-title'>{indent}{html_mod.escape(text)}</td>"
            f"<td class='toc-dots'>· · · · · · · · · · ·</td></tr>"
        )

    # Append the sections the exporter itself adds, so the contents matches the
    # document a reader is about to page through. A section that will not be
    # rendered must not be listed — the study guide is only present when the
    # caller supplied one.
    #
    # A section the report already provides as its own heading is NOT appended
    # again: the report's "## Sources" heading is already in ``toc_entries``,
    # and appending "Sources" on top of it listed the section twice.
    already_listed = {text.strip().lower() for text, _ in toc_entries}

    extras: list[str] = []
    if doc.entities and "glossary" not in already_listed:
        extras.append("Glossary")
    if study_guide is not None and getattr(study_guide, "questions", None) and "study guide" not in already_listed:
        extras.append("Study Guide")
    if "sources" not in already_listed:
        extras.append("Sources")

    for offset, name in enumerate(extras, len(toc_entries) + 1):
        rows.append(
            f"<tr><td class='toc-num'>{offset}.</td>"
            f"<td class='toc-title'>{html_mod.escape(name)}</td>"
            f"<td class='toc-dots'>· · · · · · · · · · ·</td></tr>"
        )

    return f"<div class='toc'><h2>Table of Contents</h2><table>{''.join(rows)}</table></div>"


def _render_body(doc: ReportDocument) -> str:
    """Render the report body blocks."""
    parts: list[str] = []

    # Trust header at the top of the body — unless the report carries its own
    # evidence table, which says the same thing in more detail.
    trust = trust_summary(doc)
    has_glance = any(
        b.kind is BlockKind.HEADING and "evidence at a glance" in b.text.lower() for b in doc.visible_blocks()
    )
    if trust["total"] > 0 and not has_glance:
        parts.append(
            f"<div class='trust-header'><strong>📊 Trust Summary:</strong> "
            f"{html_mod.escape(trust_headline(trust))}</div>"
        )

    if doc.quality and doc.quality != "ok":
        msg = doc.user_message or f"Report quality: {doc.quality}"
        parts.append(f"<div class='provenance'>⚠ {html_mod.escape(msg)}</div>")

    for block in doc.visible_blocks():
        rendered = _render_block(block, doc)
        if rendered:
            parts.append(rendered)

    return "\n".join(parts)


def _render_glossary(doc: ReportDocument) -> str:
    """Render the glossary of extracted entities."""
    entries = build_glossary(doc)
    if not entries:
        return ""

    rows = []
    for entry in entries:
        defn = html_mod.escape(entry.definition) if entry.definition else "<em>No description available</em>"
        rows.append(
            f"<tr><td><strong>{html_mod.escape(entry.term)}</strong></td>"
            f"<td>{html_mod.escape(entry.entity_type)}</td>"
            f"<td>{defn}</td></tr>"
        )

    return (
        f"<div class='glossary'><h2>Glossary</h2>"
        f"<table><thead><tr><th>Term</th><th>Type</th><th>Definition</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _render_study_guide(doc: ReportDocument, guide: Any = None) -> str:
    """Render the study guide with questions and answers.

    ``guide`` is a :class:`StudyGuide` supplied by the caller. The exporter
    deliberately does NOT reach for ``doc._study_guide`` — nothing ever set
    that attribute, so the previous version always returned an empty string
    and the study-guide section silently vanished from every PDF. When no
    guide is supplied, the section is omitted rather than fabricated.
    """
    if not guide or not getattr(guide, "questions", None):
        return ""

    parts = ["<div class='study-guide'><h2>Study Guide</h2>"]
    for i, q in enumerate(guide.questions, 1):
        section_html = f" <em>({html_mod.escape(q.section)})</em>" if q.section else ""
        parts.append(
            f"<p class='question'>Q{i}. {html_mod.escape(q.question)}</p>"
            f"<p class='answer'>A. {html_mod.escape(q.answer)}{section_html}</p>"
        )

    if guide.note:
        parts.append(f"<p class='footnote'>{html_mod.escape(guide.note)}</p>")

    parts.append("</div>")
    return "\n".join(parts)


def _render_sources(doc: ReportDocument) -> str:
    """Render source URLs from SOURCE blocks."""
    urls: list[str] = []
    seen: set[str] = set()
    for block in doc.visible_blocks():
        if block.kind is not BlockKind.SOURCE:
            continue
        for url in re.findall(r"https?://[^\s)\]\"']+", block.text):
            if url not in seen:
                seen.add(url)
                urls.append(url)

    if not urls:
        return ""
    lis = "".join(f"<li><a href='{html_mod.escape(u)}'>{html_mod.escape(u)}</a></li>" for u in urls)
    return f"<div class='sources'><h1>Sources</h1><ul>{lis}</ul></div>"


async def export_pdf(
    doc: ReportDocument,
    *,
    out_dir: Path,
    title: str = "Research Report",
    theme: str = "default",
    study_guide: Any = None,
) -> Path:
    """Render the document and print it to PDF via Chromium.

    ``study_guide`` is built by the caller (the export endpoint) so the
    exporter itself makes no LLM call. When omitted, the study-guide section
    is simply not part of the PDF.
    """
    html = render_html(doc, title=title, theme=theme, study_guide=study_guide)

    # Imported here: Playwright is part of the optional `local-tools` set, and
    # the slim (API-only) image leaves it and Chromium out.
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ExportUnavailableError(
            "PDF export is not included in this deployment: it prints the report through a headless "
            "browser, which the API-only build leaves out. Download the Word (DOCX) or PowerPoint version, "
            "or print the report page from your browser."
        ) from exc

    try:
        async with async_playwright() as p:
            browser = await _launch(p)
            try:
                page = await browser.new_page()
                await page.set_content(html, wait_until="load")
                pdf_bytes = await page.pdf(
                    format="A4",
                    print_background=True,
                    display_header_footer=True,
                    # Must match the @page margin in _CSS. Playwright's own
                    # margin wins for the printable area, so a mismatch here
                    # silently overrides the stylesheet.
                    margin={"top": "2cm", "bottom": "2cm", "left": "1.8cm", "right": "1.8cm"},
                    header_template="""
                        <div style="font-family: Georgia, serif; font-size: 8.5pt; color: #888; width: 100%; padding: 0.4cm 1.8cm 0.15cm; display: flex; justify-content: space-between; border-bottom: 1px solid #ddd;">
                            <span>SEARCHOps Research Report</span>
                        </div>
                    """,
                    footer_template="""
                        <div style="font-family: Georgia, serif; font-size: 9pt; color: #666; width: 100%; padding: 0.15cm 1.8cm 0.4cm; border-top: 1px solid #ddd; text-align: center;">
                            <span class='pageNumber'></span> / <span class='totalPages'></span>
                        </div>
                    """,
                )
            finally:
                await browser.close()
    except ExportUnavailableError:
        raise
    except Exception as exc:
        raise ExportUnavailableError(
            "PDF export failed: Playwright/Chromium could not render the report. "
            "Run `playwright install --with-deps chromium`. "
            f"Underlying error: {exc}"
        ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _safe_filename(title, ".pdf")
    path.write_bytes(pdf_bytes)
    log.info("export.pdf.written", path=str(path), size=len(pdf_bytes))
    return path


async def _launch(playwright: Any) -> Any:
    """Launch Chromium, translating a missing browser into a clear error."""
    try:
        return await playwright.chromium.launch()
    except Exception as exc:
        raise ExportUnavailableError(
            "PDF export needs Chromium, which is not installed. "
            "Run `playwright install --with-deps chromium`. "
            f"Underlying error: {exc}"
        ) from exc


# ── Internals ────────────────────────────────────────────────────────────────


def _render_block(block: ReportBlock, doc: ReportDocument) -> str:
    if block.kind is BlockKind.HEADING:
        level = min(block.level or 2, 6)
        text = _strip_markdown(block.text)
        return f"<h{level}>{_render_inline(text)}</h{level}>"

    if block.kind is BlockKind.SOURCE:
        return ""  # rendered once, at the end

    body = _render_block_body(block)
    if not body:
        return ""

    # Provenance is shown inline through [n] markers; a block without any
    # carries its supporting claims as a footnote so the binding is never lost.
    footnote = "" if re.search(r"\[\d{1,3}\](?!\()", block.text) else _footnote(block, doc)
    if footnote:
        body += f"<p class='footnote'>{html_mod.escape(footnote)}</p>"
    return body


def _render_block_body(block: ReportBlock) -> str:
    text = block.text.strip()
    if not text:
        return ""

    if block.kind is BlockKind.LIST:
        lines = [ln for ln in text.split("\n") if ln.strip()]
        numbered = bool(lines) and bool(re.match(r"^\s*\d+[.)]\s", lines[0]))
        items = [re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", ln).strip() for ln in lines]
        items = [i for i in items if i]
        if not items:
            return ""
        lis = "".join(f"<li>{_render_inline(html_mod.escape(i))}</li>" for i in items)
        tag = "ol" if numbered else "ul"
        return f"<{tag}>{lis}</{tag}>"

    if block.kind is BlockKind.QUOTE:
        inner = " ".join(ln.lstrip("> ").strip() for ln in text.split("\n"))
        cls = " class='callout'" if re.match(r"\**\s*(key takeaway|bottom line)", inner, re.IGNORECASE) else ""
        return f"<blockquote{cls}>{_render_inline(html_mod.escape(inner))}</blockquote>"

    if block.kind is BlockKind.CODE:
        return f"<pre><code>{html_mod.escape(text.strip('`'))}</code></pre>"

    if block.kind is BlockKind.TABLE:
        return _render_table(text)

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) > 1:
        return "".join(f"<p>{_render_inline(html_mod.escape(p))}</p>" for p in paragraphs)
    return f"<p>{_render_inline(html_mod.escape(text))}</p>"


def _render_table(text: str) -> str:
    """Render a markdown table as an HTML table.

    Handles the full markdown table format including alignment rows.
    Also handles the case where a table is preceded by a horizontal rule (---)
    which the markdown parser may have included in the block text.
    """
    lines = text.strip().split("\n")
    rows = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue  # markdown alignment row
            rows.append(cells)

    if not rows:
        return f"<p>{_render_inline(html_mod.escape(text))}</p>"

    out = ["<table>"]
    # First row is header
    header = rows[0]
    ths = "".join(f"<th>{_render_inline(html_mod.escape(c))}</th>" for c in header)
    out.append(f"<thead><tr>{ths}</tr></thead>")

    # Body rows
    out.append("<tbody>")
    for row in rows[1:]:
        tds = "".join(f"<td>{_render_inline(html_mod.escape(c))}</td>" for c in row)
        out.append(f"<tr>{tds}</tr>")
    out.append("</tbody>")
    out.append("</table>")
    return "".join(out)


def _render_inline(escaped_text: str) -> str:
    """Render inline markdown syntax in already-escaped text.

    Handles bold (**text**), italic (*text*), and inline code (`text`).
    Input is already HTML-escaped, so we operate on the escaped form.
    LaTeX math is converted to MathML for Chromium rendering.
    """
    from searchops.application.exports.math_render import render_math_for_html

    # LaTeX math → MathML (before markdown processing)
    result = render_math_for_html(escaped_text)

    # Bold: **text** (on escaped text, asterisks are unchanged)
    result = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", result)
    # Italic: *text* or _text_
    result = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", result)
    result = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"<em>\1</em>", result)
    # Inline code: `text`
    result = re.sub(r"`([^`]+)`", r"<code>\1</code>", result)
    # Links: [label](url) — the url is already escaped, so it is safe in an attribute.
    result = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<a href='\2'>\1</a>", result)
    # Citation markers: [n] → superscript.
    result = re.sub(r"\[(\d{1,3})\](?!\()", r"<sup class='cite'>[\1]</sup>", result)
    return result


def _strip_markdown(text: str) -> str:
    """Remove markdown syntax from text for clean display."""
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text)  # second pass for nested markers
    text = re.sub(r"\*\*\*([^*]+)\*\*\*", r"\1", text)  # bold+italic
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text.strip()


def _footnote(block: ReportBlock, doc: ReportDocument) -> str:
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
    return "Supporting claims: " + "; ".join(lines)


def _safe_filename(title: str, suffix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "report"
    return f"{slug[:60]}{suffix}"
