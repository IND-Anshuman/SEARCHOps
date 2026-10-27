"""
Tests for the self-healing extraction loop as wired into the nodes.

These pin the contract that replaced the silent ``except: return []``:

  - malformed output that used to destroy a document now yields claims
  - a truncated payload yields the claims that survived, and is *reported* as
    salvage (never presented as a clean parse)
  - genuinely unrecoverable output is a counted, logged failure — not a
    silent drop
  - repair outcomes feed the per-domain repair memory
"""

from __future__ import annotations

import orjson
import pytest

from searchops.knowledge.extractor import EntityExtractor
from searchops.llm.json_repair import (
    TIER_EXACT,
)
from searchops.llm.repair_memory import get_repair_memory
from searchops.orchestration.nodes.extract_evidence import _extract_from_doc as _extract_with_passages
from searchops.orchestration.passages import chunk_page

# Every statement the stub model returns appears verbatim in this page, so the
# claims ground; these tests are about the JSON repair ladder, not grounding.
PAGE_TEXT = (
    "Python 3.12 shipped in October 2023. A fact about the system. First fact in the guide. "
    "Second fact in the guide. A fact that survived. Revenue hit $4.2 billion in Q3, driven by cloud demand."
)


async def _extract_from_doc(router, doc):
    passages = chunk_page(doc["url"], doc.get("title", ""), doc.get("content", ""))
    return await _extract_with_passages(router, doc, passages, "question", "question")


class _RecordingRouter:
    """Stub LLM router that returns a fixed response and counts calls."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, str]] = []

    async def generate(self, *, prompt: str, system_prompt: str = "", temperature: float = 0.0) -> str:
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        return self.response


CLEAN = orjson.dumps(
    {
        "claims": [
            {
                "text": "Python 3.12 shipped in October 2023.",
                "confidence": 0.95,
                "citations": [{"url": "https://example.com/a", "title": "", "date": "", "author": "", "excerpt": ""}],
            }
        ]
    }
).decode()


@pytest.fixture(autouse=True)
def _clean_repair_memory():
    get_repair_memory().reset()
    yield
    get_repair_memory().reset()


@pytest.fixture
def doc():
    return {"url": "https://docs.example.com/guide", "content": PAGE_TEXT, "title": "Guide"}


# ── Clean path ───────────────────────────────────────────────────────────────


class TestCleanPath:
    @pytest.mark.asyncio
    async def test_clean_json_extracts_claims(self, doc):
        router = _RecordingRouter(CLEAN)
        claims, _, _ = await _extract_from_doc(router, doc)
        assert len(claims) == 1
        assert claims[0].text == "Python 3.12 shipped in October 2023."
        # The citation is the page itself, never a URL the model chose.
        assert claims[0].citations[0].url == doc["url"]
        assert claims[0].citations[0].quote_verified

    @pytest.mark.asyncio
    async def test_clean_path_records_exact_tier(self, doc):
        router = _RecordingRouter(CLEAN)
        await _extract_from_doc(router, doc)
        stats = get_repair_memory().domain_stats("docs.example.com")
        assert stats is not None
        assert stats["last_successful_tier"] == TIER_EXACT

    @pytest.mark.asyncio
    async def test_empty_content_short_circuits(self, doc):
        router = _RecordingRouter(CLEAN)
        doc["content"] = ""
        claims, _, _ = await _extract_from_doc(router, doc)
        assert claims == []
        assert router.calls == []


# ── The bug being fixed: silent evidence loss ────────────────────────────────


class TestRecoveryReplacesSilentLoss:
    @pytest.mark.asyncio
    async def test_markdown_fence_still_yields_claims(self, doc):
        """This used to return [] because the fence-strip regex missed a form."""
        router = _RecordingRouter(f"```json\n{CLEAN}\n```")
        claims, _, _ = await _extract_from_doc(router, doc)
        assert len(claims) == 1
        assert claims[0].text == "Python 3.12 shipped in October 2023."

    @pytest.mark.asyncio
    async def test_trailing_comma_still_yields_claims(self, doc):
        broken = '{"claims": [{"text": "A fact about the system.", "confidence": 0.9,},]}'
        router = _RecordingRouter(broken)
        claims, _, _ = await _extract_from_doc(router, doc)
        assert len(claims) == 1
        assert claims[0].text == "A fact about the system."

    @pytest.mark.asyncio
    async def test_prose_wrapper_still_yields_claims(self, doc):
        router = _RecordingRouter(f"Here is the extracted JSON:\n{CLEAN}\nLet me know if you need changes.")
        claims, _, _ = await _extract_from_doc(router, doc)
        assert len(claims) == 1

    @pytest.mark.asyncio
    async def test_python_literals_still_yield_claims(self, doc):
        broken = '{"claims": [{"text": "A fact about the system.", "confidence": 0.9, "citations": None}]}'
        router = _RecordingRouter(broken)
        claims, _, _ = await _extract_from_doc(router, doc)
        assert len(claims) == 1


class TestSalvageRecoversPartialEvidence:
    @pytest.mark.asyncio
    async def test_truncated_payload_recovers_complete_claims(self, doc):
        truncated = (
            '{"claims": ['
            '{"text": "First fact in the guide.", "confidence": 0.9, "citations": [{"url": "https://a"}]}, '
            '{"text": "Second fact in the guide.", "confidence": 0.8, "citations": [{"url": "https://b"}]}, '
            '{"text": "Third fact is cut o'
        )
        router = _RecordingRouter(truncated)
        claims, _, _ = await _extract_from_doc(router, doc)
        texts = {c.text for c in claims}
        assert "First fact in the guide." in texts
        assert "Second fact in the guide." in texts
        # The truncated tail is verbatim input, so recovering it verbatim is
        # correct. What must NOT happen is inventing the missing half.
        recovered_tail = [t for t in texts if t.startswith("Third fact")]
        for t in recovered_tail:
            assert t == "Third fact is cut o", f"tail was completed, not recovered: {t!r}"

    @pytest.mark.asyncio
    async def test_salvaged_claims_are_returned_not_dropped(self, doc):
        truncated = '{"claims": [{"text": "A fact that survived.", "confidence": 0.9}, {"text": "cut'
        router = _RecordingRouter(truncated)
        claims, _, _ = await _extract_from_doc(router, doc)
        assert len(claims) >= 1
        assert claims[0].text == "A fact that survived."

    @pytest.mark.asyncio
    async def test_max_tokens_truncation_recovers_something(self, doc):
        truncated = '{"claims": [{"text": "Revenue hit $4.2 billion in Q3, driven by'
        router = _RecordingRouter(truncated)
        claims, _, _ = await _extract_from_doc(router, doc)
        assert len(claims) == 1
        assert "4.2 billion" in claims[0].text


class TestUnrecoverableIsRecordedNotSilent:
    @pytest.mark.asyncio
    async def test_prose_only_returns_empty_but_records_failure(self, doc):
        router = _RecordingRouter("I could not find any claims in this document.")
        claims, _, _ = await _extract_from_doc(router, doc)
        assert claims == []
        snapshot = get_repair_memory().snapshot()
        assert snapshot["unrecovered"] == 1, "the failure must be counted"
        assert snapshot["attempts"] == 1

    @pytest.mark.asyncio
    async def test_unrecoverable_leaves_no_false_preference(self, doc):
        router = _RecordingRouter("no json here")
        await _extract_from_doc(router, doc)
        stats = get_repair_memory().domain_stats("docs.example.com")
        assert stats is not None
        assert stats["last_successful_tier"] == 0


# ── Per-domain repair memory learning ────────────────────────────────────────


class TestPerDomainLearning:
    @pytest.mark.asyncio
    async def test_domains_are_tracked_separately(self, doc):
        router = _RecordingRouter(CLEAN)
        await _extract_from_doc(router, {"url": "https://a.example.com/x", "content": PAGE_TEXT, "title": ""})
        await _extract_from_doc(router, {"url": "https://b.example.com/y", "content": PAGE_TEXT, "title": ""})

        mem = get_repair_memory()
        assert mem.domain_stats("a.example.com") is not None
        assert mem.domain_stats("b.example.com") is not None

    @pytest.mark.asyncio
    async def test_recovery_rate_improves_across_mixed_batch(self, doc):
        mem = get_repair_memory()
        good = _RecordingRouter(CLEAN)
        bad = _RecordingRouter("I found nothing.")

        for _ in range(4):
            await _extract_from_doc(good, doc)
        for _ in range(1):
            await _extract_from_doc(bad, doc)

        s = mem.snapshot()
        assert s["recovered"] == 4
        assert s["unrecovered"] == 1
        assert s["recovery_rate"] == pytest.approx(0.8)


# ── EntityExtractor gets the same treatment ──────────────────────────────────


class TestEntityExtractorRepair:
    ENTITIES = orjson.dumps(
        {
            "entities": [{"name": "Python", "type": "Technology", "description": "A language."}],
            "relations": [{"source": "Python", "target": "CPython", "type": "USES", "description": ""}],
        }
    ).decode()

    @pytest.mark.asyncio
    async def test_clean_entities(self):
        router = _RecordingRouter(self.ENTITIES)
        entities, relations = await EntityExtractor(router).extract("some text")
        names = {e.name for e in entities}
        assert "Python" in names
        # CPython is referenced by the relation but never declared; the
        # placeholder-entity fix means the relation is kept, not dropped.
        assert len(relations) == 1
        assert relations[0].relation_type == "USES"

    @pytest.mark.asyncio
    async def test_fenced_entities_recovered(self):
        router = _RecordingRouter(f"```json\n{self.ENTITIES}\n```")
        entities, relations = await EntityExtractor(router).extract("some text")
        assert "Python" in {e.name for e in entities}, "fenced entity output must not be dropped"
        assert len(relations) == 1

    @pytest.mark.asyncio
    async def test_truncated_entities_salvaged(self):
        truncated = (
            '{"entities": [{"name": "Python", "type": "Technology", "description": "A language."}, '
            '{"name": "Rust", "type": "Technology", "description": "A language.", '
            '"relations": [{"source": "Rust", "target": "Cargo", "type": "USES", "des'
        )
        router = _RecordingRouter(truncated)
        entities, _relations = await EntityExtractor(router).extract("some text")
        names = {e.name for e in entities}
        assert "Python" in names

    @pytest.mark.asyncio
    async def test_unrecoverable_entities_counted(self):
        router = _RecordingRouter("no entities found.")
        entities, relations = await EntityExtractor(router).extract("some text")
        assert entities == []
        assert relations == []
        s = get_repair_memory().snapshot()
        assert s["unrecovered"] >= 1

    @pytest.mark.asyncio
    async def test_empty_text_short_circuits(self):
        router = _RecordingRouter(self.ENTITIES)
        entities, relations = await EntityExtractor(router).extract("   ")
        assert entities == []
        assert relations == []
        assert router.calls == []


# ── Regression: relations with undeclared endpoints ──────────────────────────


class TestUndeclaredRelationEndpoints:
    """A relation naming an endpoint absent from ``entities`` used to be
    silently dropped, losing graph structure. It must now survive."""

    @pytest.mark.asyncio
    async def test_relation_with_undeclared_target_is_kept(self):
        payload = orjson.dumps(
            {
                "entities": [{"name": "Python", "type": "Technology", "description": "A language."}],
                "relations": [{"source": "Python", "target": "CPython", "type": "USES", "description": "runtime"}],
            }
        ).decode()
        router = _RecordingRouter(payload)
        _entities, relations = await EntityExtractor(router).extract("text")
        assert len(relations) == 1, "the relation must not be dropped"

    @pytest.mark.asyncio
    async def test_placeholder_entity_is_marked_as_undeclared(self):
        payload = orjson.dumps(
            {
                "entities": [{"name": "Python", "type": "Technology", "description": "A language."}],
                "relations": [{"source": "Python", "target": "CPython", "type": "USES", "description": ""}],
            }
        ).decode()
        router = _RecordingRouter(payload)
        entities, _ = await EntityExtractor(router).extract("text")
        by_name = {e.name: e for e in entities}
        assert "CPython" in by_name
        assert by_name["CPython"].description == ""
        assert by_name["CPython"].entity_type == "Concept"
        # The declared entity keeps its real metadata.
        assert by_name["Python"].entity_type == "Technology"

    @pytest.mark.asyncio
    async def test_placeholder_is_reused_not_duplicated(self):
        payload = orjson.dumps(
            {
                "entities": [{"name": "A", "type": "Technology", "description": ""}],
                "relations": [
                    {"source": "A", "target": "Shared", "type": "USES", "description": ""},
                    {"source": "A", "target": "Shared", "type": "DEPENDS_ON", "description": ""},
                ],
            }
        ).decode()
        router = _RecordingRouter(payload)
        entities, relations = await EntityExtractor(router).extract("text")
        assert len(relations) == 2
        assert len([e for e in entities if e.name == "Shared"]) == 1

    @pytest.mark.asyncio
    async def test_empty_endpoint_name_relation_is_dropped(self):
        payload = orjson.dumps(
            {
                "entities": [{"name": "A", "type": "Technology", "description": ""}],
                "relations": [{"source": "A", "target": "  ", "type": "USES", "description": ""}],
            }
        ).decode()
        router = _RecordingRouter(payload)
        _, relations = await EntityExtractor(router).extract("text")
        assert relations == [], "a relation with a blank endpoint is not evidence"
