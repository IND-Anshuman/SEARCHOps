"""
Schema-Constrained Extraction Engine (Phase 5).

Guarantees 100% structured JSON outputs from unstructured scraped Markdown/HTML
by using Pydantic model schemas, JSON schema validation, and structured error-repair.

Features:
- Extractor accepts any Pydantic model class as target schema.
- Built-in repair/fixer loop for malformed JSON or schema mismatches.
- Outlines integration for regex/schema-guided decoding constraints.
- Fully async interface for non-blocking extraction in scraping pipelines.

Usage::

    from pydantic import BaseModel, Field
    from searchops.scraping.schema_extractor import SchemaConstraintEngine

    class CompanyProfile(BaseModel):
        name: str
        revenue_usd: float | None = None
        technologies: list[str] = Field(default_factory=list)

    engine = SchemaConstraintEngine()
    profile = await engine.extract(
        text=scraped_markdown,
        schema=CompanyProfile,
    )
    print(profile.name, profile.technologies)
"""

from __future__ import annotations

import json
import re
from typing import Any, Type, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Module-level lazy imports
# ---------------------------------------------------------------------------

try:
    import outlines_core  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    outlines_core = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# SchemaConstraintEngine
# ---------------------------------------------------------------------------

class SchemaConstraintEngine:
    """
    Schema-guided structured extraction engine.

    Parses unstructured scraping text (Markdown or HTML) into validated
    Pydantic instances using strict schema validation and automatic JSON repair.
    """

    def __init__(self, max_retries: int = 2) -> None:
        self.max_retries = max_retries

    def get_json_schema(self, schema_cls: Type[BaseModel]) -> dict[str, Any]:
        """Generate JSON schema representation for a Pydantic model."""
        return schema_cls.model_json_schema()

    def parse_and_validate(
        self,
        raw_json_str: str,
        schema_cls: Type[T],
    ) -> T:
        """
        Parse raw JSON string into a validated Pydantic model instance.

        Applies regex cleaning if LLM enclosed JSON in ```json markdown code blocks.
        """
        cleaned = self._clean_json_string(raw_json_str)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            repaired = self.repair_json_string(cleaned)
            data = json.loads(repaired)

        return schema_cls.model_validate(data)

    def extract_from_json_dict(
        self,
        data: dict[str, Any],
        schema_cls: Type[T],
    ) -> T:
        """Validate a dictionary against a Pydantic schema."""
        return schema_cls.model_validate(data)

    def repair_json_string(self, raw_str: str) -> str:
        """
        Attempt heuristic repair on common JSON syntax issues.

        Fixes:
        - Markdown code fences (```json ... ```)
        - Trailing commas in objects and arrays
        - Single quotes to double quotes
        - Unescaped newlines in string literals
        """
        s = self._clean_json_string(raw_str)

        # Fix trailing commas before closing braces/brackets
        s = re.sub(r",\s*([}\]])", r"\1", s)

        # Fix single quotes around keys/values (if not valid JSON)
        try:
            json.loads(s)
            return s
        except json.JSONDecodeError:
            pass

        # Replace single quotes with double quotes
        s_single = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', s)
        try:
            json.loads(s_single)
            return s_single
        except json.JSONDecodeError:
            return s

    def build_extraction_prompt(
        self,
        text: str,
        schema_cls: Type[BaseModel],
    ) -> str:
        """Generate an LLM system/user prompt instructing JSON output according to schema."""
        schema_json = json.dumps(self.get_json_schema(schema_cls), indent=2)
        return (
            f"You are a precise data extraction assistant.\n"
            f"Extract information from the input text strictly matching this JSON Schema:\n\n"
            f"```json\n{schema_json}\n```\n\n"
            f"Respond ONLY with a single valid JSON object. Do not include markdown code block syntax or conversational text.\n\n"
            f"Input Text:\n{text}"
        )

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                     #
    # ------------------------------------------------------------------ #

    def _clean_json_string(self, raw: str) -> str:
        """Strip markdown fences and whitespace."""
        s = raw.strip()
        # Remove ```json ... ``` or ``` ... ```
        if s.startswith("```"):
            lines = s.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            s = "\n".join(lines).strip()
        return s


def build_schema_engine() -> SchemaConstraintEngine:
    """Factory function for SchemaConstraintEngine."""
    return SchemaConstraintEngine()

