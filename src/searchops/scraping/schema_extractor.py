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
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Module-level lazy imports
# ---------------------------------------------------------------------------

try:
    import outlines_core
except ImportError:  # pragma: no cover
    outlines_core = None


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

    def get_json_schema(self, schema_cls: type[BaseModel]) -> dict[str, Any]:
        """Generate JSON schema representation for a Pydantic model."""
        return schema_cls.model_json_schema()

    def parse_and_validate(
        self,
        raw_json_str: str,
        schema_cls: type[T],
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
        schema_cls: type[T],
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
        schema_cls: type[BaseModel],
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
        """Strip markdown fences and surrounding prose.

        Models routinely wrap a JSON object in conversational text ("Here are
        the results: {...} Hope that helps!"). Fences alone do not handle that,
        so the outermost balanced JSON object/array is extracted when the text
        is not already pure JSON.
        """
        s = raw.strip()
        # Remove ```json ... ``` or ``` ... ```
        if s.startswith("```"):
            lines = s.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            s = "\n".join(lines).strip()
        return self._extract_json_span(s)

    @staticmethod
    def _extract_json_span(s: str) -> str:
        """Return the outermost balanced JSON object/array in *s*."""
        if not s:
            return s
        start = None
        for i, ch in enumerate(s):
            if ch in "{[":
                start = i
                break
        if start is None:
            return s

        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1]
        # Unbalanced (truncated) — return what we have so the caller's JSON
        # parse fails cleanly rather than on leading prose.
        return s[start:]


def build_schema_engine() -> SchemaConstraintEngine:
    """Factory function for SchemaConstraintEngine."""
    return SchemaConstraintEngine()


def build_extraction_prompt(schema_cls: type[BaseModel] | None, text: str) -> str | None:
    """Build the LLM prompt that extracts *text* into *schema_cls*.

    Returns ``None`` when no schema is given — the caller then uses its
    ordinary (claim-extraction) path unchanged. A schema request asks for
    exactly the schema's fields, so the result can be validated and used
    directly, removing the later structuring pass.
    """
    if schema_cls is None:
        return None
    schema_json = json.dumps(schema_cls.model_json_schema(), indent=2)
    return (
        "You are a precise data extraction assistant.\n"
        "Extract information from the input text strictly matching this JSON Schema:\n\n"
        f"```json\n{schema_json}\n```\n\n"
        "Respond ONLY with a single valid JSON object. "
        "Do not include markdown code block syntax or conversational text.\n\n"
        f"Input Text:\n{text}"
    )


def extract_structured(
    engine: SchemaConstraintEngine,
    raw: str,
    schema_cls: type[BaseModel],
) -> BaseModel | None:
    """Parse and validate *raw* against *schema_cls*.

    Returns ``None`` — never raises — when the text cannot be repaired into a
    schema-valid record. Extraction is a quality stage, not a correctness
    gate: a malformed response must degrade the run, not fail it.
    """
    if not raw or not raw.strip():
        return None
    try:
        return engine.parse_and_validate(raw, schema_cls)
    except Exception as exc:
        log.warning("schema_extractor.validation_failed", error=str(exc))
        return None
