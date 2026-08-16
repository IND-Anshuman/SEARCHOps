"""
Multimodal Vision Ingestion Engine (Phase 7).

Fallback ingestion engine for un-parseable visual documents, infographics, scanned forms,
handwritten PDFs, diagrams, or web pages requiring visual understanding.

Uses Gemini 2.0 Flash VLM via Google Generative AI API to transcribe visual content directly into
clean, structured Markdown tables, lists, and hierarchical headings.

Features:
- Page rendering: converts PDF pages to high-resolution PNG images via PyMuPDF (fitz).
- Multi-image vision prompt payload construction.
- Direct structured Markdown transcription of visual charts, diagrams, and scanned forms.
- Works as a last-resort visual extraction tier in the Scraping Pipeline.

Usage::

    from searchops.scraping.vision_extractor import VisionIngestionEngine

    engine = VisionIngestionEngine(api_key="GEMINI_API_KEY")
    result = await engine.extract_from_pdf_bytes(pdf_bytes)
    print(result["markdown"])
"""

from __future__ import annotations

import base64
import io
import time
from typing import Any

import structlog

from searchops.config.settings import get_settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level lazy imports
# ---------------------------------------------------------------------------

try:
    import fitz  # PyMuPDF for rendering PDF pages as images
except ImportError:  # pragma: no cover
    fitz = None  # type: ignore[assignment]

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# VisionIngestionEngine
# ---------------------------------------------------------------------------

class VisionIngestionEngine:
    """
    Multimodal Vision-to-Markdown Extraction Engine powered by Gemini 2.0 Flash.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str = "gemini-2.0-flash",
        dpi: int = 150,
    ) -> None:
        settings = get_settings()
        llm_cfg = getattr(settings, "llm", None)
        self.api_key = api_key or (getattr(llm_cfg, "api_key", "") if llm_cfg else "")
        self.model_name = model_name
        self.dpi = dpi


    def render_pdf_to_images(
        self,
        pdf_bytes: bytes,
        max_pages: int = 10,
    ) -> list[bytes]:
        """
        Render pages of a PDF byte stream into PNG image byte arrays.
        """
        if fitz is None:  # pragma: no cover
            raise RuntimeError("PyMuPDF (fitz) is required to render PDF pages as images.")

        doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        images: list[bytes] = []
        page_count = min(len(doc), max_pages)

        # Scale factor for DPI (72 DPI baseline)
        zoom = self.dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)

        for i in range(page_count):
            page = doc[i]
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes = pix.tobytes("png")
            images.append(img_bytes)

        doc.close()
        log.debug("vision_extractor.rendered_pages", count=len(images), dpi=self.dpi)
        return images

    def build_vision_prompt(self, page_number: int | None = None) -> str:
        """Construct system prompt instructing VLM to transcribe visual elements into Markdown."""
        page_str = f" for Page {page_number}" if page_number else ""
        return (
            f"You are an expert document transcription AI specializing in visual layout parsing{page_str}.\n"
            f"Transcribe the contents of this image into clean GitHub-Flavored Markdown.\n"
            f"Rules:\n"
            f"1. Transcribe all text, headings, and lists in correct reading order.\n"
            f"2. Convert any data tables, charts, or visual matrices into standard Markdown tables.\n"
            f"3. Describe any visual diagrams or infographics briefly in [Visual Diagram: ...] blocks.\n"
            f"4. Do NOT include conversational commentary or intro text. Output Markdown directly.\n"
        )

    async def extract_from_image_bytes(self, image_bytes: bytes) -> dict[str, Any]:
        """
        Send an image payload to Gemini 2.0 Flash VLM for vision extraction.
        """
        if not self.api_key:
            log.warning("vision_extractor.no_api_key", message="GEMINI_API_KEY not configured")
            return {
                "markdown": "<!-- Vision extraction skipped: GEMINI_API_KEY missing -->",
                "status": "skipped",
            }

        start = time.perf_counter()
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": self.build_vision_prompt()},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": img_b64,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 4096,
            },
        }

        if httpx is None:  # pragma: no cover
            raise RuntimeError("httpx is required for vision API calls.")

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)

            if resp.status_code != 200:
                log.error("vision_extractor.api_error", status=resp.status_code, body=resp.text[:200])
                return {
                    "markdown": f"<!-- Vision API Error: HTTP {resp.status_code} -->",
                    "status": "error",
                }

            data = resp.json()
            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                text = "<!-- Vision API returned empty response -->"

            elapsed = round((time.perf_counter() - start) * 1000, 1)
            log.info("vision_extractor.done", model=self.model_name, duration_ms=elapsed)

            return {
                "markdown": text,
                "status": "success",
                "duration_ms": elapsed,
            }

    async def extract_from_pdf_bytes(
        self,
        pdf_bytes: bytes,
        max_pages: int = 5,
    ) -> dict[str, Any]:
        """
        Render PDF pages as images and process each page through the VLM.
        """
        images = self.render_pdf_to_images(pdf_bytes, max_pages=max_pages)
        if not images:
            return {"markdown": "", "page_count": 0, "status": "empty"}

        page_markdowns: list[str] = []
        for idx, img in enumerate(images):
            res = await self.extract_from_image_bytes(img)
            page_md = res.get("markdown", "")
            page_markdowns.append(f"<!-- Page {idx + 1} (Vision) -->\n\n{page_md}")

        full_md = "\n\n---\n\n".join(page_markdowns)
        return {
            "markdown": full_md,
            "page_count": len(images),
            "status": "success",
        }


def build_vision_engine(api_key: str | None = None) -> VisionIngestionEngine:
    """Factory function for VisionIngestionEngine."""
    return VisionIngestionEngine(api_key=api_key)

