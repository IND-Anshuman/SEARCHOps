"""Derived study artifacts: FAQ and study guide.

Users do not want a report, they want to *understand* it. NotebookLM's
most-used outputs are its study guide and FAQ — people upload material they
are about to present or be examined on and use the generated questions as
their framework[1]. SEARCHOps already extracts the entities and claims those
artifacts need; nothing was surfacing them.

The contract that matters most here is honesty over completeness:

- If the model produces nothing usable, return an **empty list**. Do not
  synthesise plausible-looking questions. A fabricated study question teaches
  the user something untrue with the product's authority behind it, which is
  the exact failure users complain about in this category[2].
- Every answer cites the block it came from, so a learner can check it.
- Answers are drawn from the report's own blocks, never from model memory.

[1] https://reddit.com/r/notebooklm/comments/1lgarv3/notebook_lm_is_just_too_insane
[2] https://reddit.com/r/notebooklm/comments/1nyj8sm/warning
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

from searchops.application.report_document import BlockKind, ReportDocument
from searchops.llm.json_repair import repair_json
from searchops.llm.token_budget import truncate_to_tokens

log = structlog.get_logger(__name__)

# Bounds. A study guide with 40 questions is a chore, not an aid.
_MAX_QUESTIONS = 8
_MAX_SOURCE_CHARS = 6_000
_MAX_ANSWER_CHARS = 400

_SYSTEM_PROMPT = (
    "You are a study assistant. Using ONLY the report excerpt provided, write "
    "questions that test understanding of what the report actually says. "
    "Every answer must be answerable from the excerpt alone. "
    "Do not introduce outside facts, and do not invent claims the report does not make. "
    "If the excerpt contains no substantive content, return an empty list."
)

# The placeholder ReportDocument inserts for an empty report. Matched exactly so
# a prompt is never built from it.
_EMPTY_NOTE = "This report has no content. The research run did not produce any readable output."

_USER_TEMPLATE = (
    "REPORT TOPIC: {query}\n\n"
    "REPORT EXCERPT:\n{excerpt}\n\n"
    "Return ONLY JSON of the form:\n"
    '{{"questions": [{{"question": "...", "answer": "...", "section": "..."}}]}}\n'
    "At most {max_questions} questions."
)


@dataclass
class StudyQuestion:
    """One study question, traced to the section it came from."""

    question: str
    answer: str
    section: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"question": self.question, "answer": self.answer, "section": self.section}


@dataclass
class StudyGuide:
    """A generated study aid plus an honest note about how it was produced."""

    questions: list[StudyQuestion]
    note: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.questions

    def to_dict(self) -> dict[str, Any]:
        return {
            "questions": [q.to_dict() for q in self.questions],
            "note": self.note,
        }


async def build_study_guide(
    doc: ReportDocument,
    *,
    router: Any,
    max_questions: int = _MAX_QUESTIONS,
) -> StudyGuide:
    """Generate study questions from the report's own content.

    Returns an empty guide with a note when there is nothing to work with —
    never fabricated questions.
    """
    excerpt = _build_excerpt(doc)
    if not excerpt:
        return StudyGuide(
            questions=[],
            note="This report has no substantive content to build a study guide from.",
        )

    prompt = _USER_TEMPLATE.format(
        query=doc.query or "this report",
        excerpt=excerpt,
        max_questions=min(max_questions, _MAX_QUESTIONS),
    )

    try:
        raw = await router.generate(
            prompt=prompt,
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=1_200,
        )
    except Exception as exc:
        log.warning("study.generate_failed", error=str(exc))
        return StudyGuide(questions=[], note=f"Could not generate a study guide: {exc}")

    questions = _parse_questions(raw)
    if not questions:
        # Honest empty state. Synthesising plausible questions here would teach
        # the user something untrue with the product's authority behind it.
        log.info("study.no_usable_output", query=doc.query)
        return StudyGuide(
            questions=[],
            note="Could not generate a study guide for this report.",
        )
    return StudyGuide(questions=questions)


async def build_faq(doc: ReportDocument, *, router: Any) -> StudyGuide:
    """Generate a FAQ from the report's own content.

    Same honesty contract as the study guide.
    """
    guide = await build_study_guide(
        doc,
        router=router,
        max_questions=6,
    )
    if guide.is_empty and not guide.note:
        guide.note = "Could not generate a FAQ for this report."
    return guide


# ── Internals ────────────────────────────────────────────────────────────────


def _build_excerpt(doc: ReportDocument) -> str:
    """Flatten the report into a bounded excerpt, headings preserved.

    Returns an empty string when the report carries no real content, so the
    caller can skip the LLM call entirely rather than paying for a prompt built
    out of the "this report has no content" placeholder.

    SOURCE blocks are excluded: a citation line is not study material, and
    including it wastes the budget that questions need.
    """
    parts: list[str] = []
    for block in doc.visible_blocks():
        if block.kind is BlockKind.SOURCE:
            continue
        if block.text.strip() == _EMPTY_NOTE.strip():
            continue
        if block.kind is BlockKind.HEADING:
            parts.append(f"\n{block.text}")
        elif block.kind is BlockKind.LIST:
            parts.append(block.text)
        else:
            parts.append(block.text)
    excerpt = "\n".join(p for p in parts if p).strip()
    if not excerpt:
        return ""
    return truncate_to_tokens(excerpt, _MAX_SOURCE_CHARS)


def _parse_questions(raw: str | None) -> list[StudyQuestion]:
    """Parse model output into questions, rejecting anything unusable."""
    if not raw or not raw.strip():
        return []

    outcome = repair_json(raw, record_key="questions", field_keys=("question", "answer"))
    payload = outcome.parsed if outcome.parsed is not None else _fallback_parse(raw)
    if not isinstance(payload, dict):
        return []

    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        return []

    questions: list[StudyQuestion] = []
    seen: set[str] = set()
    for item in raw_questions[:_MAX_QUESTIONS]:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "") or "").strip()
        answer = str(item.get("answer", "") or "").strip()
        if not question or not answer:
            continue
        key = re.sub(r"\W+", "", question.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        questions.append(
            StudyQuestion(
                question=question[:_MAX_ANSWER_CHARS],
                answer=answer[:_MAX_ANSWER_CHARS],
                section=str(item.get("section", "") or "").strip()[:120],
            )
        )
    return questions


def _fallback_parse(raw: str) -> dict[str, Any] | None:
    """Last-resort extraction when repair_json cannot recover a dict."""
    try:
        parsed: object = json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
