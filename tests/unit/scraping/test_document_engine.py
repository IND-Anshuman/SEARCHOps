"""
Unit tests for searchops.scraping.document_engine (Phase 4).

All fitz / docling / network calls are mocked where appropriate.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from searchops.core.interfaces.scraper import ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.scraping.document_engine import (
    DocumentIngestionEngine,
    PdfScraper,
    TableStore,
    build_pdf_scraper,
    is_pdf_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_fitz_doc(pages_text: list[str]) -> MagicMock:
    """Build a mock fitz.Document containing given page texts."""
    doc = MagicMock()
    doc.__len__.return_value = len(pages_text)

    pages = []
    for text in pages_text:
        page = MagicMock()
        page.get_text.return_value = text
        pages.append(page)

    doc.__getitem__.side_effect = lambda i: pages[i]
    return doc


# ---------------------------------------------------------------------------
# TestTableStore
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTableStore:

    def test_register_tables_success(self) -> None:
        tables = [
            {
                "headers": ["Name", "Score"],
                "rows": [["Alice", 95], ["Bob", 88]],
                "shape": [2, 2],
            }
        ]
        with TableStore() as store:
            registered = store.register_tables(tables)
            assert registered == ["table_0"]
            assert store.table_names == ["table_0"]

            df = store.query("SELECT * FROM table_0 WHERE Score > 90")
            assert len(df) == 1
            assert df.iloc[0]["Name"] == "Alice"

    def test_register_empty_or_invalid_tables(self) -> None:
        tables = [
            {"headers": [], "rows": []},
            {"headers": ["A"], "rows": None},
        ]
        with TableStore() as store:
            registered = store.register_tables(tables)
            assert registered == []
            assert store.table_names == []

    def test_query_returns_dataframe(self) -> None:
        tables = [
            {"headers": ["X", "Y"], "rows": [[1, 2], [3, 4]], "shape": [2, 2]}
        ]
        with TableStore() as store:
            store.register_tables(tables)
            df = store.query("SELECT SUM(X) as total_x FROM table_0")
            assert df.iloc[0]["total_x"] == 4

    def test_context_manager_closes(self) -> None:
        store = TableStore()
        with store:
            store.register_tables([{"headers": ["A"], "rows": [[1]]}])
        with pytest.raises(Exception):
            store.query("SELECT 1")


# ---------------------------------------------------------------------------
# TestDocumentIngestionEngine
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDocumentIngestionEngine:

    def test_parse_pdf_fast_path(self) -> None:
        engine = DocumentIngestionEngine(use_docling=False)
        mock_doc = _make_mock_fitz_doc([
            "Header text\n" + "Word " * 50,  # >100 chars
            "Page 2 content\n" + "Data " * 40,
        ])

        with patch("searchops.scraping.document_engine.fitz.open", return_value=mock_doc):
            res = engine.parse_pdf("dummy.pdf")

        assert res["parser_used"] == "pymupdf_fast_stream"
        assert res["page_count"] == 2
        assert "Page 1" in res["markdown"]
        assert "Page 2" in res["markdown"]

    def test_parse_pdf_fallback_low_density_no_docling(self) -> None:
        engine = DocumentIngestionEngine(use_docling=False)
        mock_doc = _make_mock_fitz_doc(["short"])  # <100 chars

        with patch("searchops.scraping.document_engine.fitz.open", return_value=mock_doc):
            res = engine.parse_pdf("dummy.pdf")

        assert res["parser_used"] == "pymupdf_fallback"
        assert res["page_count"] == 1

    def test_parse_pdf_fallback_bytes_input(self) -> None:
        engine = DocumentIngestionEngine(use_docling=True)
        mock_doc = _make_mock_fitz_doc(["short"])

        with patch("searchops.scraping.document_engine.fitz.open", return_value=mock_doc):
            res = engine.parse_pdf(b"%PDF-1.4 dummy bytes")

        assert res["parser_used"] == "pymupdf_bytes_fallback"

    def test_parse_pdf_docling_path(self) -> None:
        engine = DocumentIngestionEngine(use_docling=True)
        mock_doc = _make_mock_fitz_doc(["short"])

        # Mock Docling conversion
        mock_table = MagicMock()
        mock_table_df = MagicMock()
        mock_table_df.columns = ["Col1", "Col2"]
        mock_table_df.values.tolist.return_value = [["A", "B"]]
        mock_table_df.shape = (1, 2)
        mock_table.export_to_dataframe.return_value = mock_table_df

        mock_docling_doc = MagicMock()
        mock_docling_doc.export_to_markdown.return_value = "# Docling Markdown"
        mock_docling_doc.tables = [mock_table]

        mock_converter_res = MagicMock()
        mock_converter_res.document = mock_docling_doc

        mock_converter = MagicMock()
        mock_converter.convert.return_value = mock_converter_res

        with patch("searchops.scraping.document_engine.fitz.open", return_value=mock_doc):
            with patch.object(engine, "_get_docling_converter", return_value=mock_converter):
                res = engine.parse_pdf("dummy.pdf")

        assert res["parser_used"] == "docling_cpu"
        assert res["markdown"] == "# Docling Markdown"
        assert len(res["tables"]) == 1
        assert res["tables"][0]["headers"] == ["Col1", "Col2"]

    def test_export_tables_to_duckdb(self) -> None:
        engine = DocumentIngestionEngine(use_docling=False)
        tables = [{"headers": ["A"], "rows": [[10]], "shape": [1, 1]}]
        store = engine.export_tables_to_duckdb(tables)
        df = store.query("SELECT * FROM table_0")
        assert df.iloc[0]["A"] == 10
        store.close()


# ---------------------------------------------------------------------------
# TestPdfScraper
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestPdfScraper:

    @pytest.mark.asyncio
    async def test_scrape_success(self) -> None:
        scraper = PdfScraper(use_docling=False)
        mock_bytes = b"%PDF-1.4 test"

        parsed_data = {
            "markdown": "# PDF Title\nContent",
            "tables": [{"headers": ["H"], "rows": [["V"]]}],
            "page_count": 5,
            "parser_used": "pymupdf_fast_stream",
            "char_density": 250.0,
        }

        with patch.object(scraper, "_download", AsyncMock(return_value=mock_bytes)):
            with patch.object(scraper._engine, "parse_pdf", return_value=parsed_data):
                result = await scraper.scrape(ScrapeRequest(url="https://example.com/doc.pdf"))

        assert result.status_code == 200
        assert result.markdown == "# PDF Title\nContent"
        assert result.scrape_mode_used == ScrapeMode.DOCLING_PDF
        assert result.metadata["parser_used"] == "pymupdf_fast_stream"
        assert result.metadata["page_count"] == 5
        assert len(result.metadata["tables"]) == 1

    @pytest.mark.asyncio
    async def test_scrape_download_failure(self) -> None:
        scraper = PdfScraper(use_docling=False)

        with patch.object(scraper, "_download", AsyncMock(side_effect=RuntimeError("Connection refused"))):
            result = await scraper.scrape(ScrapeRequest(url="https://example.com/doc.pdf"))

        assert result.status_code == 500
        assert "Connection refused" in result.metadata.get("error", "")

    @pytest.mark.asyncio
    async def test_scrape_parse_failure(self) -> None:
        scraper = PdfScraper(use_docling=False)

        with patch.object(scraper, "_download", AsyncMock(return_value=b"bad pdf")):
            with patch.object(scraper._engine, "parse_pdf", side_effect=ValueError("Corrupt PDF")):
                result = await scraper.scrape(ScrapeRequest(url="https://example.com/doc.pdf"))

        assert result.status_code == 500
        assert "Corrupt PDF" in result.metadata.get("error", "")

    @pytest.mark.asyncio
    async def test_scrape_many(self) -> None:
        scraper = PdfScraper(use_docling=False)
        ok_res = ScrapeResult(
            url="https://example.com/doc.pdf",
            final_url="https://example.com/doc.pdf",
            status_code=200,
            scrape_mode_used=ScrapeMode.DOCLING_PDF,
        )

        with patch.object(scraper, "scrape", AsyncMock(return_value=ok_res)):
            results = await scraper.scrape_many([
                ScrapeRequest(url=f"https://example.com/doc{i}.pdf") for i in range(5)
            ])

        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_health_check(self) -> None:
        assert await PdfScraper().health_check() is True


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestHelpers:

    def test_is_pdf_url(self) -> None:
        assert is_pdf_url("https://example.com/report.pdf") is True
        assert is_pdf_url("https://example.com/REPORT.PDF") is True
        assert is_pdf_url("https://example.com/doc.pdf?v=1&auth=abc") is True
        assert is_pdf_url("https://example.com/page") is False
        assert is_pdf_url("https://example.com/pdf_viewer") is False

    def test_build_pdf_scraper(self) -> None:
        scraper = build_pdf_scraper(use_docling=False)
        assert isinstance(scraper, PdfScraper)
        assert scraper._engine._use_docling is False


# ---------------------------------------------------------------------------
# TestPipelinePdfIntegration
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestPipelinePdfIntegration:

    @pytest.mark.asyncio
    async def test_pipeline_routes_pdf_url(self) -> None:
        from searchops.scraping.pipeline import ScrapingPipeline

        mock_pdf_scraper = AsyncMock()
        mock_pdf_scraper.scrape.return_value = ScrapeResult(
            url="https://example.com/paper.pdf",
            final_url="https://example.com/paper.pdf",
            status_code=200,
            markdown="# Paper Content",
            scrape_mode_used=ScrapeMode.DOCLING_PDF,
        )

        pipeline = ScrapingPipeline(pdf_scraper=mock_pdf_scraper)
        req = ScrapeRequest(url="https://example.com/paper.pdf")
        result = await pipeline.execute(req)

        assert result.status_code == 200
        assert result.scrape_mode_used == ScrapeMode.DOCLING_PDF
        mock_pdf_scraper.scrape.assert_called_once_with(req)
