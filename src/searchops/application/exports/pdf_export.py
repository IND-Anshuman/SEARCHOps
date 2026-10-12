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

The contract: a real PDF (``%PDF`` header) whose text is selectable. A scanned
-image PDF cannot be searched or copied, and the recipient only finds out
after forwarding it.
"""

from __future__ import annotations

import html as html_mod
import re
from pathlib import Path  # noqa: TC003 — used at runtime for out_dir.mkdir()
from typing import Any

import structlog
from playwright.async_api import async_playwright

from searchops.application.report_document import BlockKind, ReportBlock, ReportDocument

log = structlog.get_logger(__name__)

_CSS = """
body { font-family: Georgia, 'Times New Roman', serif; font-size: 11pt;
       line-height: 1.55; color: #1a1a1a; margin: 2.2cm; }
h1 { font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: 6px; }
h2 { font-size: 14pt; margin-top: 18px; color: #222; }
h3 { font-size: 12pt; margin-top: 14px; }
p  { margin: 8px 0; }
ul, ol { margin: 8px 0 8px 18px; }
blockquote { border-left: 3px solid #999; margin-left: 0; padding-left: 12px;
             color: #444; font-style: italic; }
pre { background: #f4f4f4; padding: 8px; font-size: 9pt; overflow-x: auto; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 9.5pt; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
th, td { border: 1px solid #bbb; padding: 4px 6px; text-align: left; }
.provenance { border: 1px solid #c9a227; background: #fdf6e3; padding: 8px 10px;
              font-size: 9.5pt; margin-bottom: 14px; }
.footnote { color: #555; font-size: 8.5pt; font-style: italic; margin: 2px 0 10px 0; }
.sources a { color: #1a4d8f; word-break: break-all; }
""".strip()


class ExportUnavailableError(RuntimeError):
    """Raised when PDF export cannot run — e.g. Chromium is not installed.

    Deliberately not swallowed: an export that quietly returns an empty file
    is worse than one that fails, because the user discovers the problem after
    sending the document.
    """


def render_html(doc: ReportDocument, *, title: str) -> str:
    """Render the document as self-contained HTML.

    No external stylesheets, fonts, or scripts — a print job must not depend
    on the network. All report text is escaped: it is LLM output, and
    injecting it raw would be an injection vector in anything that opens the
    exported file in a browser.
    """
    parts: list[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{html_mod.escape(title)}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{html_mod.escape(title)}</h1>",
    ]
    if doc.query:
        parts.append(f"<p class='query'>{html_mod.escape(doc.query)}</p>")

    # Honest provenance: a synthesised report must not be indistinguishable
    # from a model-written one once it leaves the app.
    if doc.quality and doc.quality != "ok":
        msg = doc.user_message or f"Report quality: {doc.quality}"
        parts.append(f"<div class='provenance'>{html_mod.escape(msg)}</div>")

    for block in doc.visible_blocks():
        rendered = _render_block(block, doc)
        if rendered:
            parts.append(rendered)

    parts.append(_render_sources(doc))
    parts.append("</body></html>")
    return "".join(parts)


async def export_pdf(
    doc: ReportDocument,
    *,
    out_dir: Path,
    title: str = "Research Report",
) -> Path:
    """Render the document and print it to PDF via Chromium."""
    html = render_html(doc, title=title)

    try:
        async with async_playwright() as p:
            browser = await _launch(p)
            try:
                page = await browser.new_page()
                await page.set_content(html, wait_until="load")
                pdf_bytes = await page.pdf(format="A4", print_background=True)
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
        return f"<h{level}>{html_mod.escape(block.text.lstrip('# ').strip())}</h{level}>"

    if block.kind is BlockKind.SOURCE:
        return ""  # rendered once, at the end

    body = _render_body(block)
    if not body:
        return ""

    footnote = _footnote(block, doc)
    if footnote:
        body += f"<p class='footnote'>{html_mod.escape(footnote)}</p>"
    return body


def _render_body(block: ReportBlock) -> str:
    text = block.text.strip()
    if not text:
        return ""

    if block.kind is BlockKind.LIST:
        items = [ln.lstrip("-*• ").strip() for ln in text.split("\n")]
        items = [i for i in items if i]
        if not items:
            return ""
        lis = "".join(f"<li>{html_mod.escape(i)}</li>" for i in items)
        return f"<ul>{lis}</ul>"

    if block.kind is BlockKind.QUOTE:
        return f"<blockquote>{html_mod.escape(text.lstrip('> ').strip())}</blockquote>"

    if block.kind is BlockKind.CODE:
        return f"<pre><code>{html_mod.escape(text.strip('`'))}</code></pre>"

    if block.kind is BlockKind.TABLE:
        return _render_table(text)

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) > 1:
        return "".join(f"<p>{html_mod.escape(p)}</p>" for p in paragraphs)
    return f"<p>{html_mod.escape(text)}</p>"


def _render_table(text: str) -> str:
    rows = [ln.strip() for ln in text.split("\n") if ln.strip().startswith("|")]
    if not rows:
        return f"<p>{html_mod.escape(text)}</p>"

    out = ["<table>"]
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # markdown alignment row
        tag = "th" if i == 0 else "td"
        tds = "".join(f"<{tag}>{html_mod.escape(c)}</{tag}>" for c in cells)
        out.append(f"<tr>{tds}</tr>")
    out.append("</table>")
    return "".join(out)


def _render_sources(doc: ReportDocument) -> str:
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
        return ""
    lis = "".join(f"<li><a href='{html_mod.escape(u)}'>{html_mod.escape(u)}</a></li>" for u in urls)
    return f"<div class='sources'><h2>Sources</h2><ul>{lis}</ul></div>"


def _footnote(block: ReportBlock, doc: ReportDocument) -> str:
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
    return "Supporting claims: " + "; ".join(lines)


def _safe_filename(title: str, suffix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "report"
    return f"{slug[:60]}{suffix}"
