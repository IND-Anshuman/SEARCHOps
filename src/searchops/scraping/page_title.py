"""Recover a page's title when the scraper that fetched it did not report one.

Only some engines fill ``ScrapeResult.title`` (Playwright, Crawl4AI, Firecrawl,
Bright Data Browser); the Unlocker, stealth HTTP and cache paths leave it empty.
An untitled page reaches the report's Sources list as a bare URL, so the title
is recovered here from what every engine does return: the HTML or the Markdown.
"""

from __future__ import annotations

import html as html_lib
import re

_MAX_TITLE = 200
_MAX_HEADING_SCAN_LINES = 500
_OG_TITLE = re.compile(
    r"<meta[^>]+(?:property|name)=[\"'](?:og:title|twitter:title)[\"'][^>]*content=[\"']([^\"']+)[\"']"
    r"|<meta[^>]+content=[\"']([^\"']+)[\"'][^>]*(?:property|name)=[\"'](?:og:title|twitter:title)[\"']",
    re.IGNORECASE,
)
_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_MD_HEADING = re.compile(r"^#\s+(.+?)\s*#*\s*$")
# Interstitial and error pages announce themselves in the title; they name nothing.
_JUNK = re.compile(
    r"^(?:just a moment|attention required|access denied|forbidden|not found|404|403|"
    r"page not found|error|loading|untitled|home|redirecting)\b",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", text))
    text = " ".join(text.split()).strip(" -|·")
    text = re.sub(r"[*_`]+", "", text)  # Markdown emphasis in a heading
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # [text](link) → text
    if not text or len(text) < 3 or _JUNK.match(text):
        return ""
    return text[:_MAX_TITLE].rstrip()


def infer_title(title: str | None, html: str | None, markdown: str | None) -> str:
    """The reported title, else og:title, else <title>, else the first Markdown H1."""
    if title and (cleaned := _clean(title)):
        return cleaned
    head = (html or "")[:200_000]
    og_m, title_m = _OG_TITLE.search(head), _HTML_TITLE.search(head)
    og = _clean(next(g for g in og_m.groups() if g)) if og_m else ""
    doc = _clean(title_m.group(1)) if title_m else ""
    # og:title is usually the better headline, but some sites set it to their
    # own name ("Onidel Cloud"); then the <title> that contains it says more.
    if og and not (doc and og.lower() in doc.lower() and len(doc) > len(og)):
        return og
    if doc:
        return doc
    # Extracted pages often open with a long navigation menu (Tavily Extract
    # returned one with its H1 at line 173), so look well past the top.
    for line in (markdown or "").splitlines()[:_MAX_HEADING_SCAN_LINES]:
        m = _MD_HEADING.match(line.strip())
        if m and (cleaned := _clean(m.group(1))):
            return cleaned
    return ""
