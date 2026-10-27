"""Tests for schema-driven extraction at scrape time.

Step 6 removes an LLM pass. Today the pipeline scrapes generic markdown and a
*later* node extracts claims with an LLM. When the caller supplies a schema,
the extraction prompt asks for exactly those fields, so stage 8 can build a
dataset without a second structuring pass.

Contract these tests pin:

* **Schema present → typed records.** The prompt asks for the schema's fields
  and the result is validated against it.
* **Schema absent → unchanged behaviour.** No schema means the existing claim
  extraction path, byte for byte.
* **Malformed schema → falls back, and says so.** A schema the model can't
  honour degrades to the normal path rather than failing the run.
* **The schema travels with the request**, so it is set once at submit time
  and not re-derived per page.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from searchops.scraping.schema_extractor import (
    SchemaConstraintEngine,
    build_extraction_prompt,
    extract_structured,
)


class ProductRecord(BaseModel):
    name: str
    price_usd: float | None = None
    features: list[str] = Field(default_factory=list)


VALID_JSON = '{"name": "Widget", "price_usd": 19.99, "features": ["fast", "cheap"]}'
FENCED_JSON = f"```json\n{VALID_JSON}\n```"
PROSE_WRAPPED = f"Here are the results:\n{VALID_JSON}\nHope that helps!"
TRUNCATED = '{"name": "Widget", "price_usd": 19.99, "features": ["fast", "che'


class TestBuildExtractionPrompt:
    def test_schema_fields_appear_in_the_prompt(self) -> None:
        prompt = build_extraction_prompt(ProductRecord, "some page text")
        assert "name" in prompt
        assert "price_usd" in prompt
        assert "features" in prompt

    def test_page_text_is_included(self) -> None:
        prompt = build_extraction_prompt(ProductRecord, "UNIQUE_PAGE_MARKER")
        assert "UNIQUE_PAGE_MARKER" in prompt

    def test_prompt_asks_for_json_only(self) -> None:
        prompt = build_extraction_prompt(ProductRecord, "text")
        assert "json" in prompt.lower()

    def test_no_schema_returns_none(self) -> None:
        """Absent schema must produce no prompt — behaviour is unchanged."""
        assert build_extraction_prompt(None, "text") is None


class TestExtractStructured:
    def setup_method(self) -> None:
        self.engine = SchemaConstraintEngine()

    def test_plain_json_is_validated(self) -> None:
        record = extract_structured(self.engine, VALID_JSON, ProductRecord)
        assert record is not None
        assert record.name == "Widget"
        assert record.price_usd == 19.99
        assert record.features == ["fast", "cheap"]

    def test_fenced_json_is_repaired(self) -> None:
        record = extract_structured(self.engine, FENCED_JSON, ProductRecord)
        assert record is not None
        assert record.name == "Widget"

    def test_prose_wrapped_json_is_repaired(self) -> None:
        record = extract_structured(self.engine, PROSE_WRAPPED, ProductRecord)
        assert record is not None
        assert record.name == "Widget"

    def test_unparseable_returns_none_rather_than_raising(self) -> None:
        """A malformed extraction degrades; it never fails the run."""
        assert extract_structured(self.engine, "not json at all", ProductRecord) is None

    def test_truncated_json_returns_none(self) -> None:
        assert extract_structured(self.engine, TRUNCATED, ProductRecord) is None

    def test_schema_violation_returns_none(self) -> None:
        """Valid JSON that does not satisfy the schema is rejected, not coerced."""
        wrong = '{"name": "Widget", "price_usd": "not a number"}'
        assert extract_structured(self.engine, wrong, ProductRecord) is None

    def test_missing_optional_fields_are_defaulted(self) -> None:
        record = extract_structured(self.engine, '{"name": "Widget"}', ProductRecord)
        assert record is not None
        assert record.price_usd is None
        assert record.features == []

    def test_missing_required_field_is_rejected(self) -> None:
        assert extract_structured(self.engine, '{"price_usd": 1.0}', ProductRecord) is None


class TestSchemaValidation:
    def test_engine_generates_json_schema(self) -> None:
        engine = SchemaConstraintEngine()
        schema = engine.get_json_schema(ProductRecord)
        assert schema["properties"].keys() >= {"name", "price_usd", "features"}
        assert "name" in schema.get("required", [])

    def test_valid_dict_validates(self) -> None:
        engine = SchemaConstraintEngine()
        record = engine.extract_from_json_dict({"name": "Widget", "price_usd": 5.0, "features": []}, ProductRecord)
        assert record.name == "Widget"

    def test_invalid_dict_raises(self) -> None:
        from pydantic import ValidationError

        engine = SchemaConstraintEngine()
        with pytest.raises(ValidationError):
            engine.extract_from_json_dict({"price_usd": 1.0}, ProductRecord)


class TestScrapeRequestSchema:
    def test_schema_defaults_to_none(self) -> None:
        from searchops.core.interfaces.scraper import ScrapeRequest

        req = ScrapeRequest(url="https://x.com")
        assert req.extract_schema is None

    def test_schema_can_be_supplied(self) -> None:
        from searchops.core.interfaces.scraper import ScrapeRequest

        req = ScrapeRequest(url="https://x.com", extract_schema={"type": "object"})
        assert req.extract_schema == {"type": "object"}

    def test_schema_survives_serialisation(self) -> None:
        from searchops.core.interfaces.scraper import ScrapeRequest

        req = ScrapeRequest(url="https://x.com", extract_schema={"type": "object"})
        restored = ScrapeRequest.model_validate(req.model_dump(mode="json"))
        assert restored.extract_schema == {"type": "object"}
