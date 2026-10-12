"""Tests for study guide / FAQ generation.

The contract that matters is honesty over completeness:

- If the model produces nothing usable, return an **empty** guide with a note.
  Fabricating plausible-looking questions would teach the user something
  untrue with the product's authority behind it — the exact failure users
  complain about in AI research tools.
- An empty report produces an empty guide, not a guess.
- Questions are de-duplicated and bounded.
"""

from __future__ import annotations

import pytest

from searchops.application.exports.study import (
    _build_excerpt,
    _parse_questions,
    build_faq,
    build_study_guide,
)
from searchops.application.report_document import ReportDocument

MD_DOC = (
    "# Quantum Error Correction\n\n"
    "## Executive Summary\n\nSurface codes are the leading approach to fault tolerance.\n\n"
    "## Findings\n\nLogical error rates fall as code distance increases.\n\n"
    "## Sources\n\n- [IBM](https://ibm.com/a)\n"
)

GOOD_JSON = """{"questions": [
  {"question": "Why are surface codes preferred?", "answer": "They are the leading approach to fault tolerance.", "section": "Executive Summary"},
  {"question": "What happens as code distance increases?", "answer": "Logical error rates fall.", "section": "Findings"}
]}"""


class _FakeRouter:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def generate(self, prompt, *, system_prompt=None, temperature=0.0, max_tokens=None):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_parse_questions_extracts_real_questions():
    qs = _parse_questions(GOOD_JSON)
    assert len(qs) == 2
    assert qs[0].question.startswith("Why are surface codes")
    assert qs[0].section == "Executive Summary"


def test_parse_questions_rejects_empty_and_missing_fields():
    raw = '{"questions": [{"question": "", "answer": "x"}, {"question": "q", "answer": ""}, {"answer": "no question"}]}'
    assert _parse_questions(raw) == []


def test_parse_questions_deduplicates():
    raw = """{"questions": [
      {"question": "Same question?", "answer": "a"},
      {"question": "same question?", "answer": "b"}
    ]}"""
    assert len(_parse_questions(raw)) == 1


def test_parse_questions_bounds_the_count():
    raw = '{"questions": [' + ",".join(f'{{"question": "Q{i}?", "answer": "A{i}"}}' for i in range(30)) + "]}"
    assert len(_parse_questions(raw)) <= 8


def test_parse_questions_returns_empty_for_garbage():
    assert _parse_questions("not json at all") == []
    assert _parse_questions("") == []
    assert _parse_questions(None) == []


def test_parse_questions_survives_a_fenced_reply():
    fenced = "```json\n" + GOOD_JSON + "\n```"
    assert len(_parse_questions(fenced)) == 2


# ── Excerpt building ─────────────────────────────────────────────────────────


def test_excerpt_excludes_source_blocks():
    """A citation line is not study material; including it wastes the budget."""
    doc = ReportDocument.from_markdown("j", "q", MD_DOC)
    excerpt = _build_excerpt(doc)
    assert "Surface codes" in excerpt
    assert "https://ibm.com/a" not in excerpt


def test_excerpt_is_empty_for_an_empty_document():
    doc = ReportDocument.from_markdown("j", "q", "")
    assert _build_excerpt(doc) == ""


def test_excerpt_omits_hidden_blocks():
    doc = ReportDocument.from_markdown("j", "q", MD_DOC)
    target = next(b for b in doc.blocks if "Logical error rates" in b.text)
    hidden = doc.set_hidden(target.id, True)
    assert "Logical error rates" not in _build_excerpt(hidden)


# ── Generation contract ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_study_guide_returns_questions_when_the_model_complies():
    doc = ReportDocument.from_markdown("j", "qec", MD_DOC)
    guide = await build_study_guide(doc, router=_FakeRouter(GOOD_JSON))

    assert not guide.is_empty
    assert len(guide.questions) == 2
    assert guide.to_dict()["questions"][0]["section"] == "Executive Summary"


@pytest.mark.asyncio
async def test_study_guide_is_empty_when_the_model_returns_nothing():
    """Never fabricate questions to fill the panel."""
    doc = ReportDocument.from_markdown("j", "qec", MD_DOC)
    guide = await build_study_guide(doc, router=_FakeRouter(""))

    assert guide.is_empty
    assert guide.note, "an empty guide must explain itself"


@pytest.mark.asyncio
async def test_study_guide_is_empty_for_an_empty_report():
    doc = ReportDocument.from_markdown("j", "qec", "")
    router = _FakeRouter(GOOD_JSON)
    guide = await build_study_guide(doc, router=router)

    assert guide.is_empty
    assert not router.calls, "no LLM call should be made for an empty report"


@pytest.mark.asyncio
async def test_study_guide_survives_a_router_error():
    doc = ReportDocument.from_markdown("j", "qec", MD_DOC)
    guide = await build_study_guide(doc, router=_FakeRouter(RuntimeError("provider down")))

    assert guide.is_empty
    assert "provider down" in guide.note


@pytest.mark.asyncio
async def test_faq_shares_the_same_honesty_contract():
    doc = ReportDocument.from_markdown("j", "qec", MD_DOC)
    guide = await build_faq(doc, router=_FakeRouter(""))
    assert guide.is_empty
    assert guide.note


@pytest.mark.asyncio
async def test_prompt_contains_only_the_reports_own_content():
    doc = ReportDocument.from_markdown("j", "qec", MD_DOC)
    router = _FakeRouter(GOOD_JSON)
    await build_study_guide(doc, router=router)

    prompt = router.calls[0]["prompt"]
    assert "Surface codes" in prompt
    assert "https://ibm.com/a" not in prompt
    assert "do not invent" in router.calls[0]["system_prompt"].lower()
