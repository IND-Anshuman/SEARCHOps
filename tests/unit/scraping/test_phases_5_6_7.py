"""
Unit tests for Phases 5, 6, and 7:
- Phase 5: SchemaConstraintEngine
- Phase 6: MinHashDeduplicator
- Phase 7: VisionIngestionEngine
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from searchops.scraping.deduplicator import MinHashDeduplicator, build_deduplicator
from searchops.scraping.schema_extractor import SchemaConstraintEngine, build_schema_engine
from searchops.scraping.vision_extractor import VisionIngestionEngine, build_vision_engine


# ---------------------------------------------------------------------------
# Test Schemas for Phase 5
# ---------------------------------------------------------------------------

class UserProfile(BaseModel):
    name: str
    age: int
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# TestPhase5_SchemaConstraintEngine
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSchemaConstraintEngine:

    def test_get_json_schema(self) -> None:
        engine = SchemaConstraintEngine()
        schema = engine.get_json_schema(UserProfile)
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "age" in schema["properties"]

    def test_parse_and_validate_valid_json(self) -> None:
        engine = SchemaConstraintEngine()
        raw = '{"name": "Alice", "age": 30, "tags": ["python", "ai"]}'
        profile = engine.parse_and_validate(raw, UserProfile)
        assert profile.name == "Alice"
        assert profile.age == 30
        assert profile.tags == ["python", "ai"]

    def test_parse_and_validate_markdown_fenced_json(self) -> None:
        engine = SchemaConstraintEngine()
        raw = """```json
{
  "name": "Bob",
  "age": 25
}
```"""
        profile = engine.parse_and_validate(raw, UserProfile)
        assert profile.name == "Bob"
        assert profile.age == 25

    def test_repair_json_string_trailing_comma(self) -> None:
        engine = SchemaConstraintEngine()
        raw = '{"name": "Charlie", "age": 40,}'
        repaired = engine.repair_json_string(raw)
        data = json.loads(repaired)
        assert data["name"] == "Charlie"
        assert data["age"] == 40

    def test_build_extraction_prompt(self) -> None:
        engine = SchemaConstraintEngine()
        prompt = engine.build_extraction_prompt("Extract user: Dave, 35", UserProfile)
        assert "UserProfile" in prompt or "properties" in prompt
        assert "Dave, 35" in prompt

    def test_factory_helper(self) -> None:
        engine = build_schema_engine()
        assert isinstance(engine, SchemaConstraintEngine)


# ---------------------------------------------------------------------------
# TestPhase6_MinHashDeduplicator
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMinHashDeduplicator:

    def test_create_shingles(self) -> None:
        dedup = MinHashDeduplicator(shingle_n=3)
        text = "The quick brown fox jumps over the lazy dog"
        shingles = dedup.create_shingles(text, n=3)
        assert len(shingles) > 0
        assert shingles[0] == "the quick brown"

    def test_index_and_is_duplicate(self) -> None:
        dedup = MinHashDeduplicator(threshold=0.3, shingle_n=2)
        doc1 = "Artificial intelligence and machine learning are revolutionizing web scraping and data extraction."
        doc2 = "Artificial intelligence and machine learning are transforming web scraping and content extraction."
        doc3 = "Quantum computing relies on qubits and superposition to process information at unprecedented speeds."

        dedup.index_document("doc1", doc1)

        is_dup, matches = dedup.is_duplicate(doc2)
        assert is_dup is True
        assert "doc1" in matches

        is_dup_diff, matches_diff = dedup.is_duplicate(doc3)
        assert is_dup_diff is False
        assert len(matches_diff) == 0

    def test_jaccard_similarity(self) -> None:
        dedup = MinHashDeduplicator(shingle_n=2)
        t1 = "Python web scraping pipeline with proxies and rate limiting."
        t2 = "Python web scraping pipeline with proxy rotation and rate limiting."
        sim = dedup.jaccard_similarity(t1, t2)
        assert 0.4 <= sim <= 1.0


    def test_clear_index(self) -> None:
        dedup = MinHashDeduplicator(threshold=0.5)
        dedup.index_document("d1", "Test content string for indexing.")
        is_dup, matches = dedup.is_duplicate("Test content string for indexing.")
        assert is_dup is True

        dedup.clear()
        is_dup_after, matches_after = dedup.is_duplicate("Test content string for indexing.")
        assert is_dup_after is False

    def test_factory_helper(self) -> None:
        dedup = build_deduplicator(threshold=0.8)
        assert isinstance(dedup, MinHashDeduplicator)
        assert dedup.threshold == 0.8


# ---------------------------------------------------------------------------
# TestPhase7_VisionIngestionEngine
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestVisionIngestionEngine:

    def test_build_vision_prompt(self) -> None:
        engine = VisionIngestionEngine(api_key="test-key")
        prompt = engine.build_vision_prompt(page_number=2)
        assert "Page 2" in prompt
        assert "GitHub-Flavored Markdown" in prompt

    @pytest.mark.asyncio
    async def test_extract_from_image_bytes_no_key(self) -> None:
        engine = VisionIngestionEngine(api_key="")
        result = await engine.extract_from_image_bytes(b"dummy image bytes")
        assert result["status"] == "skipped"
        assert "missing" in result["markdown"]

    @pytest.mark.asyncio
    async def test_extract_from_image_bytes_success(self) -> None:
        engine = VisionIngestionEngine(api_key="test-api-key")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "# Transcribed Heading\n\n| Col1 | Col2 |\n|---|---|\n| A | B |"}
                        ]
                    }
                }
            ]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        with patch("searchops.scraping.vision_extractor.httpx.AsyncClient", return_value=mock_client):
            result = await engine.extract_from_image_bytes(b"dummy image bytes")

        assert result["status"] == "success"
        assert "# Transcribed Heading" in result["markdown"]
        assert "| Col1 | Col2 |" in result["markdown"]

    @pytest.mark.asyncio
    async def test_extract_from_pdf_bytes(self) -> None:
        engine = VisionIngestionEngine(api_key="test-api-key")

        mock_images = [b"img1", b"img2"]
        with patch.object(engine, "render_pdf_to_images", return_value=mock_images):
            with patch.object(
                engine,
                "extract_from_image_bytes",
                AsyncMock(return_value={"status": "success", "markdown": "Page text"}),
            ):
                result = await engine.extract_from_pdf_bytes(b"%PDF dummy")

        assert result["status"] == "success"
        assert result["page_count"] == 2
        assert "Page 1 (Vision)" in result["markdown"]
        assert "Page 2 (Vision)" in result["markdown"]

    def test_factory_helper(self) -> None:
        engine = build_vision_engine(api_key="key")
        assert isinstance(engine, VisionIngestionEngine)
        assert engine.api_key == "key"

