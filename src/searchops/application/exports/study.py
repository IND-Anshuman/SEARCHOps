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
    "questions that test understanding of what the report actually says.\n\n"
    "Rules:\n"
    "- Produce the questions directly. Do not ask for the report, do not ask "
    "which question to answer, and do not reply with an offer to help — the "
    "excerpt and the topic are already given below.\n"
    "- Every question must be answerable from the excerpt alone.\n"
    "- Do not introduce outside facts, and do not invent claims the report does not make.\n"
    "- If the excerpt contains no substantive content, return an empty list.\n"
    "- Return ONLY JSON, no preamble and no commentary:\n"
    '{"questions": [{"question": "...", "answer": "...", "section": "..."}]}\n'
    "Include an answer for each question."
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

    # A question with no answer is not a study aid — the learner still has
    # nothing to check. When the model returned questions without answers
    # (the prose fallback shape), a second pass fills them in from the same
    # report excerpt. A question that still has no answer is dropped, so the
    # guide never shows an unanswered prompt.
    if any(not q.answer for q in questions):
        questions = await _answer_questions(doc, questions, router=router)

    if not questions:
        log.info("study.answers_unavailable", query=doc.query)
        return StudyGuide(
            questions=[],
            note="Could not generate a study guide for this report.",
        )

    return StudyGuide(questions=questions)


_ANSWER_SYSTEM_PROMPT = (
    "You are a study assistant. Using ONLY the report excerpt provided, answer "
    "every question below in a single response.\n\n"
    "Rules:\n"
    "- Answer ALL of the questions. Do not ask for the questions one at a time, "
    "do not ask which question to answer next, and do not reply with an offer to "
    "help — the questions are already given below.\n"
    "- Every answer must be answerable from the excerpt alone.\n"
    "- If the excerpt does not contain the answer for a question, answer "
    "'Not stated in the report.' and do not use outside knowledge.\n"
    "- Return ONLY JSON, no preamble and no commentary:\n"
    '{"answers": [{"question": "...", "answer": "..."}]}\n'
    "One entry per question, in the same order as the list below."
)

_ANSWER_TEMPLATE = (
    "REPORT EXCERPT:\n{excerpt}\n\n"
    "QUESTIONS TO ANSWER (answer all of them, in this order):\n{questions}\n\n"
    "Return ONLY JSON of the form:\n"
    '{{"answers": [{{"question": "...", "answer": "..."}}]}}\n'
    "One entry per question, in the same order."
)


async def _answer_questions(
    doc: ReportDocument,
    questions: list[StudyQuestion],
    *,
    router: Any,
) -> list[StudyQuestion]:
    """Fill in answers for questions that arrived without one.

    A second model call, using the same report excerpt, so every answer is
    grounded in the same text the questions came from. If the call fails, the
    questions that had no answer are dropped and the ones that already had
    answers are kept — a partially answerable guide is still useful, and an
    unanswered question is not.
    """
    missing = [q for q in questions if not q.answer]
    if not missing:
        return questions

    excerpt = _build_excerpt(doc)
    if not excerpt:
        return [q for q in questions if q.answer]

    listed = "\n".join(f"- {q.question}" for q in missing)
    try:
        raw = await router.generate(
            prompt=_ANSWER_TEMPLATE.format(excerpt=excerpt, questions=listed),
            system_prompt=_ANSWER_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=1_500,
        )
    except Exception as exc:
        log.warning("study.answer_pass_failed", error=str(exc))
        return [q for q in questions if q.answer]

    answers = _parse_answers(raw)
    if not answers:
        return [q for q in questions if q.answer]

    # Attach by position, then by normalised text — the model may reorder.
    by_position = list(answers)
    for index, question in enumerate(missing):
        candidate = None
        if index < len(by_position):
            candidate = by_position[index]
        if candidate is None or not candidate.get("answer"):
            key = re.sub(r"\W+", "", question.question.lower())
            for entry in answers:
                if re.sub(r"\W+", "", str(entry.get("question", "")).lower()) == key:
                    candidate = entry
                    break
        if candidate and candidate.get("answer"):
            question.answer = str(candidate["answer"])[:_MAX_ANSWER_CHARS]

    return [q for q in questions if q.answer]


def _parse_answers(raw: str | None) -> list[dict[str, str]]:
    """Parse the answer pass's output into {question, answer} pairs."""
    if not raw or not raw.strip():
        return []
    outcome = repair_json(raw, record_key="answers", field_keys=("question", "answer"))
    payload = outcome.parsed if outcome.parsed is not None else _fallback_parse(raw)
    if not isinstance(payload, dict):
        return []
    entries = payload.get("answers")
    if not isinstance(entries, list):
        return []
    out: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        answer = str(item.get("answer", "") or "").strip()
        if not answer:
            continue
        out.append(
            {
                "question": str(item.get("question", "") or "").strip(),
                "answer": answer,
            }
        )
    return out


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
    """Parse model output into questions, rejecting anything unusable.

    Two shapes are handled, because models do not reliably honour a "return
    ONLY JSON" instruction:

    1. JSON (the requested form) — parsed via the repair ladder.
    2. A numbered or bulleted prose list of questions — the form a model
       falls back to when it ignores the instruction. The questions in it are
       real and drawn from the report, so discarding them would throw away
       good output. They arrive without answers, and the caller fills those
       in with a second pass.

    Anything that is neither yields nothing: a fabricated question teaches the
    user something untrue with the product's authority behind it.
    """
    if not raw or not raw.strip():
        return []

    outcome = repair_json(raw, record_key="questions", field_keys=("question", "answer"))
    payload = outcome.parsed if outcome.parsed is not None else _fallback_parse(raw)
    if isinstance(payload, dict):
        raw_questions = payload.get("questions")
        if isinstance(raw_questions, list):
            questions = _questions_from_payload(raw_questions)
            if questions:
                return questions
            # The JSON parsed but carried no usable question — fall through to
            # the prose reader rather than giving up on the whole response.
    return _questions_from_prose(raw)


def _questions_from_payload(raw_questions: list[Any]) -> list[StudyQuestion]:
    """Build questions from the JSON shape, dropping anything unusable."""
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


# A prose list item: an optional bullet/number, then the text.
_PROSE_ITEM_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*(?P<text>.+?)\s*$")
# A numbered heading the model likes to put above the list ("**Questions ...**").
_PROSE_NOISE_RE = re.compile(r"^\**\s*(questions?|study questions)\b", re.IGNORECASE)


def _questions_from_prose(raw: str) -> list[StudyQuestion]:
    """Read a numbered or bulleted list of questions out of prose.

    The text is taken verbatim from the model's own output — nothing is
    rephrased or invented. An item must look like a question (it ends with a
    question mark) to be accepted, which keeps headings and commentary out.
    """
    questions: list[StudyQuestion] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or _PROSE_NOISE_RE.match(line):
            continue
        match = _PROSE_ITEM_RE.match(line)
        text = match.group("text") if match else line
        # Strip markdown emphasis the model wrapped the item in.
        text = text.strip().strip("*").strip()
        if not text.endswith("?"):
            continue
        if len(text) < 15:
            continue
        key = re.sub(r"\W+", "", text.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        questions.append(
            StudyQuestion(
                question=text[:_MAX_ANSWER_CHARS],
                answer="",  # filled in by the answer pass
                section="",
            )
        )
        if len(questions) >= _MAX_QUESTIONS:
            break
    return questions


def _fallback_parse(raw: str) -> dict[str, Any] | None:
    """Last-resort extraction when repair_json cannot recover a dict."""
    try:
        parsed: object = json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
