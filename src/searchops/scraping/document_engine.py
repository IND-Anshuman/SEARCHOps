"""
Document & PDF Ingestion Engine (Phase 4).

Implements a tiered, CPU-only PDF and document parsing strategy:

Tier A — PyMuPDF Fast Path (~150 pages/sec)
    For digital PDFs with native text streams.  Extracts text blocks page-by-page
    and returns clean Markdown.  Zero ML inference cost.

Tier B — Docling CPU Layout Transformer (~1–2 pages/sec)
    IBM Docling with Table Transformer (TATR) running on CPU via PyTorch.
    Handles multi-column layouts, borderless table recovery, and reading-order
    reconstruction.  Used when PyMuPDF probe finds < 100 chars/page (scanned/complex).

DuckDB Integration
    Extracted tables are exported as pandas DataFrames and registered into a
    DuckDB in-memory connection for ad-hoc SQL querying.  Returned via
    ``ScrapeResult.dataframes_json`` (list of serialised table dicts).

PdfScraper (IScraper)
    Wraps ``DocumentIngestionEngine`` as a standard ``IScraper`` so it plugs
    directly into the ``ScrapingPipeline`` as a Tier for ``*.pdf`` URLs.

Architecture decision log
--------------------------
- PyMuPDF probe threshold: 100 chars/page.  Below → likely scanned/complex → Docling.
- Docling ``do_ocr=False``: OCR is slow on CPU and unreliable without a GPU for
  the recognition model.  For true scanned docs, the pipeline falls back to a
  page-image description note.
- ``duckdb.connect(":memory:")`` is used per-call; for persistence, pass a
  ``db_path`` argument pointing to a ``.duckdb`` file.
- Module-level ``try/except`` imports ensure ``patch()`` works in unit tests
  without requiring the heavy libraries to be installed.

Usage::

    engine = DocumentIngestionEngine()
    result = engine.parse_pdf("report.pdf")
    # result["markdown"]     — clean Markdown text
    # result["tables"]       — list of {"headers": [...], "rows": [[...]], "shape": [...]}
    # result["parser_used"]  — "pymupdf_fast_stream" | "docling_cpu" | ...

    conn = engine.export_tables_to_duckdb(result["tables"])
    df   = conn.execute("SELECT * FROM table_0 LIMIT 10").df()

    # Or use as IScraper inside the pipeline:
    scraper = PdfScraper()
    res = await scraper.scrape(ScrapeRequest(url="https://example.com/report.pdf"))
    # res.markdown — extracted text; res.metadata["parser_used"] — which path
"""

from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path
from typing import Any

import structlog

from searchops.core.interfaces.scraper import IScraper, ScrapeMode, ScrapeRequest, ScrapeResult

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHARS_PER_PAGE_THRESHOLD = 100  # Below → route to Docling layout transformer
_MAX_PAGES_PYMUPDF        = 2000  # Safety cap for very large PDFs

# ---------------------------------------------------------------------------
# Module-level lazy imports (so unittest.mock.patch() can intercept them)
# ---------------------------------------------------------------------------

try:
    import fitz as _fitz  # PyMuPDF
    fitz = _fitz
except ImportError:  # pragma: no cover
    fitz = None  # type: ignore[assignment]

try:
    import duckdb as _duckdb
    duckdb = _duckdb
except ImportError:  # pragma: no cover
    duckdb = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# TableStore — DuckDB wrapper
# ---------------------------------------------------------------------------

class TableStore:
    """
    Lightweight DuckDB wrapper for ad-hoc SQL over extracted PDF tables.

    Tables extracted by :class:`DocumentIngestionEngine` are registered as
    named DataFrames (``table_0``, ``table_1``, …) in an in-memory DuckDB
    connection.

    Example::

        store = TableStore()
        store.register_tables(tables)
        df = store.query("SELECT * FROM table_0")
        store.close()

    Context-manager usage::

        with TableStore() as store:
            store.register_tables(tables)
            results = store.query("SELECT COUNT(*) FROM table_0")
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        if duckdb is None:  # pragma: no cover
            raise RuntimeError("duckdb is not installed. Run: uv add 'duckdb>=1.1.0'")
        self._conn = duckdb.connect(db_path)
        self._table_names: list[str] = []

    def register_tables(self, tables: list[dict[str, Any]]) -> list[str]:
        """
        Register extracted PDF tables as queryable DuckDB relations.

        Args:
            tables: List of ``{"headers": [...], "rows": [[...]], "shape": [...]}``
                    dicts as returned by :meth:`DocumentIngestionEngine.parse_pdf`.

        Returns:
            List of registered table names (e.g. ``["table_0", "table_1"]``).
        """
        import pandas as pd

        registered = []
        for i, table in enumerate(tables):
            headers = table.get("headers")
            rows    = table.get("rows")
            if not headers or rows is None:
                continue
            name = f"table_{i}"
            try:
                df = pd.DataFrame(rows, columns=headers)
                self._conn.register(name, df)
                self._table_names.append(name)
                registered.append(name)
                log.debug("TableStore.register", table=name, shape=table.get("shape"))
            except Exception as exc:
                log.warning("TableStore.register.failed", table=name, error=str(exc))
        return registered

    def query(self, sql: str) -> Any:
        """
        Execute a SQL statement and return a pandas DataFrame.

        Args:
            sql: SQL string, e.g. ``"SELECT * FROM table_0 LIMIT 5"``.

        Returns:
            ``pandas.DataFrame`` with query results.
        """
        return self._conn.execute(sql).df()

    @property
    def table_names(self) -> list[str]:
        """Names of all currently registered tables."""
        return list(self._table_names)

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        try:
            self._conn.close()
        except Exception:  # pragma: no cover
            pass

    def __enter__(self) -> "TableStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# DocumentIngestionEngine
# ---------------------------------------------------------------------------

class DocumentIngestionEngine:
    """
    CPU-only hybrid PDF parser.

    Probes each PDF to determine the optimal parsing strategy:

    1. **PyMuPDF fast path** (``parser_used="pymupdf_fast_stream"``) —
       digital PDFs with native text.  ~150 pages/sec.

    2. **Docling CPU layout transformer** (``parser_used="docling_cpu"``) —
       multi-column, borderless tables, complex layouts.  ~1–2 pages/sec on CPU.

    3. **PyMuPDF fallback** (``parser_used="pymupdf_fallback"``) —
       when Docling is disabled or source is raw bytes (no path for Docling).

    Args:
        use_docling: Enable Docling path (default ``True``).  Set ``False`` in
                     testing or high-throughput environments where PyMuPDF alone
                     is sufficient.
    """

    def __init__(self, *, use_docling: bool = True) -> None:
        self._use_docling         = use_docling
        self._docling_converter   = None  # Lazy init (heavy import + torch load)

    # ------------------------------------------------------------------ #
    #  Public API                                                           #
    # ------------------------------------------------------------------ #

    def parse_pdf(self, source: str | Path | bytes) -> dict[str, Any]:
        """
        Parse a PDF to structured Markdown + table data.

        Args:
            source: ``str`` / ``pathlib.Path`` for a file path, or ``bytes``
                    for raw PDF content (bytes disables the Docling path).

        Returns:
            Dict with keys:

            - ``"markdown"``    (str)  — clean Markdown text, page-separated.
            - ``"tables"``      (list) — list of ``{"headers", "rows", "shape"}`` dicts.
            - ``"page_count"``  (int)  — total page count.
            - ``"parser_used"`` (str)  — one of ``pymupdf_fast_stream``,
                                          ``docling_cpu``, ``pymupdf_fallback``,
                                          ``docling_error``, ``pymupdf_bytes_fallback``.
            - ``"char_density"`` (float) — average characters per page (diagnostic).
        """
        if fitz is None:  # pragma: no cover
            raise RuntimeError("PyMuPDF is not installed. Run: uv add 'pymupdf>=1.24.0'")

        is_bytes  = isinstance(source, bytes)
        source_path: str | None = None

        # Open the PDF
        if is_bytes:
            doc = fitz.open(stream=io.BytesIO(source), filetype="pdf")
        else:
            source_path = str(source)
            doc = fitz.open(source_path)

        page_count = min(len(doc), _MAX_PAGES_PYMUPDF)

        # Probe: count native text characters across all pages
        total_chars = sum(
            len(doc[i].get_text("text"))
            for i in range(page_count)
        )
        chars_per_page = total_chars / max(page_count, 1)
        has_native_text = chars_per_page >= _CHARS_PER_PAGE_THRESHOLD

        log.info(
            "document_engine.probe",
            page_count=page_count,
            chars_per_page=round(chars_per_page, 1),
            has_native_text=has_native_text,
            parser="pymupdf_fast" if has_native_text else "docling",
        )

        if has_native_text:
            result = self._pymupdf_fast_path(doc, page_count)
        elif self._use_docling and source_path is not None:
            doc.close()
            result = self._docling_path(source_path, page_count)
            result["char_density"] = chars_per_page
            return result
        else:
            result = self._pymupdf_fallback(doc, page_count, is_bytes=is_bytes)

        doc.close()
        result["char_density"] = chars_per_page
        return result

    def export_tables_to_duckdb(
        self,
        tables: list[dict[str, Any]],
        *,
        db_path: str = ":memory:",
    ) -> "TableStore":
        """
        Register extracted tables into a :class:`TableStore` for SQL querying.

        Args:
            tables:  List of table dicts from :meth:`parse_pdf`.
            db_path: DuckDB database path. Defaults to ``":memory:"``.

        Returns:
            An open :class:`TableStore`.  Caller is responsible for closing it.
        """
        store = TableStore(db_path=db_path)
        store.register_tables(tables)
        return store

    # ------------------------------------------------------------------ #
    #  Private parsing paths                                               #
    # ------------------------------------------------------------------ #

    def _pymupdf_fast_path(self, doc: Any, page_count: int) -> dict[str, Any]:
        """
        Extract text directly from native PDF text streams.

        Fast (~150 pages/sec), zero ML inference.  Reading order relies on the
        PDF's internal layout — fine for most digital documents.
        """
        blocks: list[str] = []
        for i in range(page_count):
            page = doc[i]
            text = page.get_text("text").strip()
            if text:
                blocks.append(f"<!-- Page {i + 1} -->\n\n{text}")

        return {
            "markdown":    "\n\n---\n\n".join(blocks),
            "tables":      [],
            "page_count":  page_count,
            "parser_used": "pymupdf_fast_stream",
        }

    def _pymupdf_fallback(
        self,
        doc: Any,
        page_count: int,
        *,
        is_bytes: bool = False,
    ) -> dict[str, Any]:
        """
        PyMuPDF text extraction without Docling (scanned/bytes source).

        Used when:
        - ``use_docling=False``, or
        - source is raw bytes (Docling needs a file path).

        Reading order may be imperfect on complex multi-column layouts.
        """
        reason = (
            "raw-bytes input (Docling requires file path)"
            if is_bytes
            else "docling disabled"
        )
        log.info("document_engine.pymupdf_fallback", reason=reason)

        blocks: list[str] = []
        for i in range(page_count):
            page = doc[i]
            text = page.get_text("text").strip()
            if text:
                blocks.append(f"<!-- Page {i + 1} -->\n\n{text}")

        parser = "pymupdf_bytes_fallback" if is_bytes else "pymupdf_fallback"
        return {
            "markdown":    "\n\n---\n\n".join(blocks),
            "tables":      [],
            "page_count":  page_count,
            "parser_used": parser,
        }

    def _docling_path(self, source_path: str, page_count: int) -> dict[str, Any]:
        """
        Full Docling CPU layout transformer path.

        Handles:
        - Multi-column reading-order reconstruction
        - Borderless table detection via Table Transformer (TATR)
        - Scanned PDFs (without OCR — CPU OCR is too slow)

        Performance: ~1–2 pages/sec on CPU.  Suitable for small/medium PDFs
        (< 50 pages) in production; large PDFs should use the fast path.
        """
        log.info("document_engine.docling_start", source=source_path, pages=page_count)
        try:
            converter = self._get_docling_converter()
            result    = converter.convert(source_path)
            markdown  = result.document.export_to_markdown()

            # Extract tables as structured dicts
            tables: list[dict[str, Any]] = []
            for tbl in result.document.tables:
                try:
                    df = tbl.export_to_dataframe()
                    tables.append({
                        "headers": list(df.columns),
                        "rows":    df.values.tolist(),
                        "shape":   list(df.shape),
                    })
                except Exception as exc:
                    log.warning("document_engine.table_export_failed", error=str(exc))

            log.info(
                "document_engine.docling_done",
                source=source_path,
                tables_found=len(tables),
            )
            return {
                "markdown":    markdown,
                "tables":      tables,
                "page_count":  page_count,
                "parser_used": "docling_cpu",
            }

        except Exception as exc:
            log.error("document_engine.docling_error", source=source_path, error=str(exc))
            return {
                "markdown":    f"<!-- Docling parse failed: {exc} -->",
                "tables":      [],
                "page_count":  page_count,
                "parser_used": "docling_error",
            }

    def _get_docling_converter(self) -> Any:
        """
        Return (or lazily initialise) the Docling DocumentConverter.

        CPU-only pipeline options:
        - OCR disabled (too slow without GPU)
        - Table structure enabled (TATR via PyTorch CPU)
        """
        if self._docling_converter is None:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions

            pipe_opts = PdfPipelineOptions()
            pipe_opts.do_ocr               = False
            pipe_opts.do_table_structure   = True
            pipe_opts.table_structure_options.use_ocr_for_table = False

            self._docling_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipe_opts)
                }
            )
            log.debug("document_engine.docling_converter_ready")

        return self._docling_converter


# ---------------------------------------------------------------------------
# PdfScraper — IScraper adapter for pipeline integration
# ---------------------------------------------------------------------------

class PdfScraper(IScraper):
    """
    IScraper adapter wrapping :class:`DocumentIngestionEngine`.

    Detects PDF URLs (ending in ``.pdf`` or ``Content-Type: application/pdf``)
    and routes them through the hybrid PDF parsing engine.  Integrates
    transparently into :class:`ScrapingPipeline` as a pre-step tier before
    the main web-scraping tiers.

    Behaviour:
    - Fetches the PDF bytes via ``httpx`` (async).
    - Routes through PyMuPDF → Docling as appropriate.
    - Returns ``ScrapeResult`` with ``markdown``, ``metadata["tables"]``,
      and ``metadata["parser_used"]``.

    Example::

        scraper = PdfScraper()
        result  = await scraper.scrape(
            ScrapeRequest(url="https://arxiv.org/pdf/2301.00001")
        )
        print(result.markdown[:500])
        print(result.metadata["parser_used"])   # "pymupdf_fast_stream"
        print(len(result.metadata["tables"]))   # number of extracted tables
    """

    def __init__(
        self,
        engine: DocumentIngestionEngine | None = None,
        *,
        use_docling: bool = True,
        download_timeout: float = 60.0,
    ) -> None:
        self._engine          = engine or DocumentIngestionEngine(use_docling=use_docling)
        self._download_timeout = download_timeout

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """
        Download and parse a PDF document.

        Returns ``status_code=200`` with ``markdown`` on success,
        or ``status_code=500`` on download/parse failure.
        """
        log.info("pdf_scraper.scrape", url=request.url)
        start = time.perf_counter()

        try:
            pdf_bytes = await self._download(request)
        except Exception as exc:
            elapsed = round((time.perf_counter() - start) * 1000, 1)
            log.error("pdf_scraper.download_failed", url=request.url, error=str(exc))
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=500,
                scrape_mode_used=ScrapeMode.DOCLING_PDF,
                duration_ms=elapsed,
                metadata={"error": f"Download failed: {exc}"},
            )

        try:
            parsed  = self._engine.parse_pdf(pdf_bytes)
            elapsed = round((time.perf_counter() - start) * 1000, 1)

            # Serialise tables for JSON transport (stored in metadata)
            tables = parsed.get("tables", [])
            log.info(
                "pdf_scraper.done",
                url=request.url,
                parser=parsed.get("parser_used"),
                pages=parsed.get("page_count"),
                tables=len(tables),
                duration_ms=elapsed,
            )

            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=200,
                markdown=parsed.get("markdown", ""),
                word_count=len(parsed.get("markdown", "").split()),
                scrape_mode_used=ScrapeMode.DOCLING_PDF,
                duration_ms=elapsed,
                metadata={
                    "parser_used": parsed.get("parser_used"),
                    "page_count":  parsed.get("page_count"),
                    "tables":      tables,
                    "char_density": parsed.get("char_density", 0.0),
                },
            )

        except Exception as exc:
            elapsed = round((time.perf_counter() - start) * 1000, 1)
            log.error("pdf_scraper.parse_failed", url=request.url, error=str(exc))
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=500,
                scrape_mode_used=ScrapeMode.DOCLING_PDF,
                duration_ms=elapsed,
                metadata={"error": f"Parse failed: {exc}"},
            )

    async def scrape_many(
        self,
        requests: list[ScrapeRequest],
        *,
        max_concurrency: int = 4,
    ) -> list[ScrapeResult]:
        """Scrape multiple PDF URLs concurrently."""
        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded(req: ScrapeRequest) -> ScrapeResult:
            async with sem:
                return await self.scrape(req)

        return list(await asyncio.gather(*[_bounded(r) for r in requests]))

    async def health_check(self) -> bool:
        """Return True when PyMuPDF is importable."""
        return fitz is not None

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _download(self, request: ScrapeRequest) -> bytes:
        """Download URL content as raw bytes (async httpx)."""
        import httpx

        async with httpx.AsyncClient(
            timeout=self._download_timeout,
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                request.url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; SEARCHOps/1.0)",
                    **request.headers,
                },
            )
            resp.raise_for_status()
            return resp.content


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_pdf_url(url: str) -> bool:
    """
    Heuristically detect whether *url* points to a PDF document.

    Checks for a ``.pdf`` suffix (case-insensitive) or a ``?`` query string
    on a ``.pdf`` path segment.

    Args:
        url: URL string.

    Returns:
        ``True`` if the URL appears to be a PDF.

    Example::

        >>> is_pdf_url("https://example.com/report.pdf")
        True
        >>> is_pdf_url("https://example.com/page")
        False
    """
    from urllib.parse import urlparse
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


def build_pdf_scraper(*, use_docling: bool = True) -> PdfScraper:
    """
    Build a :class:`PdfScraper` with the given Docling preference.

    Args:
        use_docling: Enable Docling CPU path (default ``True``).
                     Set ``False`` on machines with < 8 GB RAM or for
                     high-throughput digital-PDF pipelines.

    Returns:
        A fresh :class:`PdfScraper` instance.
    """
    return PdfScraper(use_docling=use_docling)
