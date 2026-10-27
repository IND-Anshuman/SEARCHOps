"""Enrichment pass: one LLM call producing all four study artifacts at depth.

The problem this solves
-----------------------
Every study aid in SEARCHOps was shallow for the same structural reason: each
was asked for a thin fragment, from a bounded slice of the report, in isolation.

* The glossary surfaced the extractor's ``"short description"`` field, so every
  definition was a one-liner *by instruction*.
* The study guide spent two calls over a 6,000-char excerpt and truncated every
  answer at 400 chars.
* The FAQ re-invoked the study guide, so it cost three calls to re-read the
  same excerpt.
* Next steps replayed the critic's gap strings verbatim, with no context.

One call over the accumulated corpus produces all four, at real depth, because
the model already holds the whole context — a richer definition costs nothing
extra in prompt tokens.

Two traps this module is written around
----------------------------------------
1. **The router truncates prompts from the tail** (``LLM_MAX_PROMPT_CHARS``).
   The tail is where instructions live, so a naive builder emits a wall of
   evidence and no direction, and the model answers with a short generic
   section. This module therefore puts the output contract *first* and caps
   the evidence explicitly, so the instructions can never be cut off.

2. **``repair_json`` salvages ONE array per call.** The envelope carries four.
   This module runs the salvage ladder once per array key and merges the
   results, so a malformed response still yields whatever fields survived.

Honesty contract
----------------
Nothing here may invent content. A field the corpus cannot support comes back
empty with a reason. A field the model filled with fragments is marked ``thin``
rather than shipped silently — a fragment presented as a definition is worse
than an honest gap.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from searchops.llm.json_repair import TIER_EXACT, repair_json
from searchops.llm.schemas.enrichment import (
    EnrichedFaq,
    EnrichedGap,
    EnrichedQuestion,
    EnrichedTerm,
    EnrichmentResult,
)

log = structlog.get_logger(__name__)

# ── Depth floors ─────────────────────────────────────────────────────────────
# A fragment is not a definition. These are the minimums the parser accepts
# before it will stop marking a field thin.
_MIN_DEFINITION_CHARS = 60
_MIN_ANSWER_CHARS = 40
_MIN_GAP_CONTEXT_CHARS = 30

# ── Quantity ceilings ────────────────────────────────────────────────────────
# More is not better: a glossary of 60 terms is a dump, and a study guide of 40
# questions is a chore. These bound the prompt's output budget too, so the call
# stays inside the router's token cap.
_MAX_TERMS = 20
_MAX_QUESTIONS = 8
_MAX_GAPS = 8
_MAX_FAQ = 8

# The prompt cap is applied by the router from the tail. We target well below
# it so the instructions always survive, and so a single call cannot spend the
# run's whole budget.
_TARGET_PROMPT_CHARS = 26_000

_SYSTEM_PROMPT = """You are a study assistant. Using ONLY the report corpus provided below,
produce study aids that teach what the report actually says.

Return ONLY a JSON object of exactly this shape, with no preamble and no commentary:

{
  "glossary": [
    {"term": "...", "entity_type": "Concept|Technology|Organization|Person|Method|Benchmark|Metric",
     "definition": "2-4 sentences: what it is, why it matters for this report, how it relates to the rest",
     "related": ["other term", "another term"]}
  ],
  "study_guide": [
    {"question": "...", "answer": "3-6 sentences, drawn only from the corpus",
     "section": "the section it came from",
     "tests": "concept|comparison|limitation|mechanism|general"}
  ],
  "next_steps": [
    {"gap": "what the run could not establish",
     "why_it_matters": "why this gap limits a reader's understanding",
     "suggested_query": "a concrete search query that would close it",
     "prerequisites": ["term to understand first"],
     "priority": 1}
  ],
  "faq": [
    {"q": "a question a reader would actually ask", "a": "a direct answer from the corpus"}
  ]
}

Rules that matter:
- Depth is required. A definition must explain, not name. A one-line definition is a failure.
- Everything must be answerable from the corpus alone. Never use outside knowledge.
- Do not invent content. If the corpus cannot support a field, return it empty.
- Questions must end in a question mark and test real understanding, not trivia.
- Do not ask for the report, do not offer to help, do not reply with commentary.
"""

_USER_TEMPLATE = """REPORT TOPIC: {query}

REPORT CORPUS:
{corpus}

Produce the JSON object now. Target at most {max_terms} glossary terms, {max_questions} study questions, {max_gaps} next steps and {max_faq} FAQ entries. Prioritise depth over breadth."""


def build_corpus_text(corpus: str, target_chars: int = _TARGET_PROMPT_CHARS) -> str:
    """Bound the corpus so the output contract survives the router's truncation.

    The router cuts prompts from the tail. This module emits the *contract*
    first, so the evidence is what gets trimmed — and it trims the evidence
    explicitly here, with a marker, rather than letting the router do it
    blindly and silently.
    """
    if not corpus or not corpus.strip():
        return ""
    cleaned = corpus.strip()
    if len(cleaned) <= target_chars:
        return cleaned
    return (
        cleaned[:target_chars].rstrip() + "\n\n[... corpus truncated to fit the model's context window; "
        "base every statement on the text above ...]"
    )


async def enrich_corpus(
    corpus: str,
    query: str,
    *,
    router: Any,
    max_terms: int = _MAX_TERMS,
    max_questions: int = _MAX_QUESTIONS,
    max_gaps: int = _MAX_GAPS,
    max_faq: int = _MAX_FAQ,
    temperature: float = 0.2,
    max_tokens: int = 8_000,
) -> EnrichmentResult:
    """Run one enrichment call and parse it into the four study artifacts.

    Returns an :class:`EnrichmentResult` — possibly partially empty, with a
    ``note`` explaining why. Never raises for model-output problems: a broken
    response yields whatever fields could be recovered, plus a note.
    """
    bounded = build_corpus_text(corpus)
    if not bounded:
        return EnrichmentResult(
            note="The report corpus is empty, so no study aids could be produced.",
        )

    prompt = _USER_TEMPLATE.format(
        query=query or "this report",
        corpus=bounded,
        max_terms=max_terms,
        max_questions=max_questions,
        max_gaps=max_gaps,
        max_faq=max_faq,
    )

    try:
        raw = await router.generate(
            prompt=prompt,
            system_prompt=_SYSTEM_PROMPT,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # pragma: no cover - provider failure
        log.warning("enrichment.call_failed", error=str(exc))
        return EnrichmentResult(note=f"Could not reach the model to build study aids: {exc}")

    return parse_enrichment(raw, max_terms=max_terms, max_questions=max_questions, max_gaps=max_gaps, max_faq=max_faq)


async def enrich_and_fill(
    corpus: str,
    query: str,
    *,
    router: Any,
    max_terms: int = _MAX_TERMS,
    max_questions: int = _MAX_QUESTIONS,
    max_gaps: int = _MAX_GAPS,
    max_faq: int = _MAX_FAQ,
) -> EnrichmentResult:
    """Enrich, then fill in any answers the first pass left empty.

    Prose output carries questions with no answers. This runs the answer pass
    only when something is actually missing, so the common case stays one call.
    """
    result = await enrich_corpus(
        corpus,
        query,
        router=router,
        max_terms=max_terms,
        max_questions=max_questions,
        max_gaps=max_gaps,
        max_faq=max_faq,
    )
    if result.study_guide and any(not q.answer for q in result.study_guide):
        result.study_guide = await _fill_answers(result.study_guide, build_corpus_text(corpus), router=router)
    return result


# ── Parsing ──────────────────────────────────────────────────────────────────


def parse_enrichment(
    raw: str | None,
    *,
    max_terms: int = _MAX_TERMS,
    max_questions: int = _MAX_QUESTIONS,
    max_gaps: int = _MAX_GAPS,
    max_faq: int = _MAX_FAQ,
) -> EnrichmentResult:
    """Parse model output into the four artifacts, salvaging what survived.

    ``repair_json`` recovers a single array per call, so the four keys are
    salvaged independently and merged. A field the model omitted or returned
    empty stays empty; a field it filled with fragments is kept but marked
    ``thin`` so the caller can say so honestly.
    """
    if not raw or not raw.strip():
        return EnrichmentResult(note="The model returned no output for the study aids.")
    # Tier 0: the model honoured the instruction exactly.
    envelope = _try_parse_envelope(raw)
    # Tier 1+: salvage each array independently.
    for key in ("glossary", "study_guide", "next_steps", "faq"):
        if envelope is not None and isinstance(envelope.get(key), list) and envelope[key]:
            continue
        outcome = repair_json(raw, record_key=key, field_keys=("definition", "answer", "term", "question", "gap", "a"))
        salvaged = outcome.parsed
        if isinstance(salvaged, dict) and isinstance(salvaged.get(key), list):
            envelope = envelope or {}
            envelope[key] = salvaged[key]

    # Tier last: the model ignored "return ONLY JSON" and wrote prose. This is
    # the shape the study guide already had to handle, and it is the shape that
    # made eight real report-grounded questions get thrown away. A numbered or
    # bulleted list of questions is real content and is read verbatim.
    if envelope is None or not any(
        isinstance(envelope.get(k), list) and envelope[k] for k in ("glossary", "study_guide", "next_steps", "faq")
    ):
        prose_questions = _questions_from_prose(raw)
        if prose_questions:
            envelope = envelope or {}
            if not envelope.get("study_guide"):
                envelope["study_guide"] = prose_questions

    # An envelope that parsed but carried only empty arrays is a RECOVERED
    # response, not an unrecoverable one — the model answered, it just had
    # nothing to say. Reporting it as "could not parse" would misstate the
    # failure and hide the real reason from the reader.
    if envelope is None:
        log.info("enrichment.unrecoverable")
        return EnrichmentResult(
            note="Could not parse the study aids from the model's output.",
        )

    glossary = _parse_terms(envelope.get("glossary"), max_terms)
    study = _parse_questions(envelope.get("study_guide"), max_questions)
    gaps = _parse_gaps(envelope.get("next_steps"), max_gaps)
    faq = _parse_faq(envelope.get("faq"), max_faq)

    thin: list[str] = []
    if glossary and any(len(g.definition) < _MIN_DEFINITION_CHARS for g in glossary):
        thin.append("glossary")
    if study and any(len(q.answer) < _MIN_ANSWER_CHARS for q in study):
        thin.append("study_guide")
    if gaps and any(len(g.why_it_matters) < _MIN_GAP_CONTEXT_CHARS for g in gaps):
        thin.append("next_steps")
    if faq and any(len(f.a) < _MIN_ANSWER_CHARS for f in faq):
        thin.append("faq")

    note = ""
    empty = [
        name
        for name, items in (
            ("glossary", glossary),
            ("study guide", study),
            ("next steps", gaps),
            ("faq", faq),
        )
        if not items
    ]
    if empty:
        note = "The corpus did not support: " + ", ".join(empty) + "."
    if thin:
        note = (note + " " if note else "") + ("Returned shallow content for: " + ", ".join(thin) + ".")

    return EnrichmentResult(
        glossary=glossary,
        study_guide=study,
        next_steps=gaps,
        faq=faq,
        note=note.strip(),
        thin_fields=thin,
    )


async def _fill_answers(
    questions: list[EnrichedQuestion],
    corpus: str,
    *,
    router: Any,
) -> list[EnrichedQuestion]:
    """Fill in answers for questions that arrived without one.

    A prose question carries no answer, and an unanswered prompt teaches the
    reader nothing. This is a single short call over the same corpus the
    questions came from, so every answer is grounded in the same text. It only
    runs when something is actually missing, so the common case stays one call.
    """
    missing = [q for q in questions if not q.answer]
    if not missing:
        return questions

    listed = "\n".join(f"- {q.question}" for q in missing)
    prompt = (
        f"REPORT CORPUS:\n{corpus}\n\n"
        f"ANSWER THESE QUESTIONS (all of them, in this order):\n{listed}\n\n"
        'Return ONLY JSON: {"answers": [{"question": "...", "answer": "..."}]}\n'
        "Every answer must be drawn from the corpus alone. If the corpus does not "
        "contain an answer, use the text 'Not stated in the report.'"
    )
    system = (
        "You are a study assistant. Answer each question in 3-6 sentences using "
        "ONLY the report corpus provided. Never use outside knowledge."
    )

    try:
        raw = await router.generate(
            prompt=prompt,
            system_prompt=system,
            temperature=0.2,
            max_tokens=3_000,
        )
    except Exception as exc:  # pragma: no cover - provider failure
        log.warning("enrichment.answer_pass_failed", error=str(exc))
        return [q for q in questions if q.answer]

    parsed = _try_parse_envelope(raw)
    answers: list[dict[str, str]] = []
    if parsed and isinstance(parsed.get("answers"), list):
        for item in parsed["answers"]:
            if isinstance(item, dict):
                answers.append(
                    {
                        "question": _clean(item.get("question")),
                        "answer": _clean(item.get("answer")),
                    }
                )
    if not answers:
        return [q for q in questions if q.answer]

    # Attach by position, then by normalised text — the model may reorder.
    for index, question in enumerate(missing):
        candidate: dict[str, str] | None = None
        if index < len(answers) and answers[index]["answer"]:
            candidate = answers[index]
        if candidate is None:
            key = re.sub(r"\W+", "", question.question.lower())
            for entry in answers:
                if re.sub(r"\W+", "", entry["question"].lower()) == key and entry["answer"]:
                    candidate = entry
                    break
        if candidate and candidate["answer"]:
            question.answer = candidate["answer"][:2_000]

    return [q for q in questions if q.answer]


# A prose list item: an optional bullet/number, then the text.
_PROSE_ITEM_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*(?P<text>.+?)\s*$")
# A numbered heading the model likes to put above the list ("**Questions ...**").
_PROSE_NOISE_RE = re.compile(r"^\**\s*(questions?|study questions|faq|glossary)\b", re.IGNORECASE)


def _questions_from_prose(raw: str) -> list[dict[str, str]]:
    """Read a numbered or bulleted list of questions out of prose.

    The text is taken verbatim from the model's own output — nothing is
    rephrased or invented. An item must look like a question (it ends with a
    question mark) to be accepted, which keeps headings and commentary out.

    Deliberately reused verbatim from ``exports/study.py``: the study guide
    already needed exactly this reader, and a second divergent copy is how the
    two paths drift apart.
    """
    questions: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or _PROSE_NOISE_RE.match(line):
            continue
        match = _PROSE_ITEM_RE.match(line)
        text = match.group("text") if match else line
        text = text.strip().strip("*").strip()
        if not text.endswith("?"):
            continue
        if len(text) < 15:
            continue
        key = re.sub(r"\W+", "", text.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        questions.append({"question": text, "answer": "", "section": "", "tests": "general"})
        if len(questions) >= _MAX_QUESTIONS:
            break
    return questions


def _try_parse_envelope(raw: str) -> dict[str, Any] | None:
    """Parse the requested envelope shape exactly, returning None on failure."""
    import json

    text = raw.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_terms(raw: Any, max_terms: int) -> list[EnrichedTerm]:
    out: list[EnrichedTerm] = []
    seen: set[str] = set()
    for item in _as_list(raw)[:max_terms]:
        if not isinstance(item, dict):
            continue
        term = _clean(item.get("term") or item.get("name"))
        definition = _clean(item.get("definition") or item.get("description"))
        if not term or not definition:
            continue
        key = re.sub(r"\W+", "", term.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            EnrichedTerm(
                term=term[:120],
                entity_type=_clean(item.get("entity_type") or item.get("type"))[:60] or "Concept",
                definition=definition[:2_000],
                related=[_clean(r)[:120] for r in _as_list(item.get("related")) if _clean(r)][:10],
            )
        )
    return out


def _parse_questions(raw: Any, max_questions: int) -> list[EnrichedQuestion]:
    """Build questions, keeping ones that arrived without an answer.

    A prose question has no answer yet — that is not a reason to drop it. The
    caller fills answers in a second pass when anything is missing. Requiring
    an answer here is what silently threw away real report-grounded questions.
    """
    out: list[EnrichedQuestion] = []
    seen: set[str] = set()
    for item in _as_list(raw)[:max_questions]:
        if not isinstance(item, dict):
            continue
        question = _clean(item.get("question") or item.get("q"))
        if not question:
            continue
        if not question.endswith("?"):
            continue
        key = re.sub(r"\W+", "", question.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            EnrichedQuestion(
                question=question[:400],
                answer=_clean(item.get("answer") or item.get("a"))[:2_000],
                section=_clean(item.get("section"))[:120],
                tests=_normalise_tests(item.get("tests")),
            )
        )
    return out


def _parse_gaps(raw: Any, max_gaps: int) -> list[EnrichedGap]:
    out: list[EnrichedGap] = []
    seen: set[str] = set()
    for item in _as_list(raw)[:max_gaps]:
        if not isinstance(item, dict):
            continue
        gap = _clean(item.get("gap") or item.get("description"))
        if not gap:
            continue
        why = _clean(item.get("why_it_matters") or item.get("why_it_matters"))
        query = _clean(item.get("suggested_query") or item.get("target_query"))
        key = re.sub(r"\W+", "", (gap + query).lower())
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            priority = max(1, min(5, int(item.get("priority", 1))))
        except (TypeError, ValueError):
            priority = 1
        out.append(
            EnrichedGap(
                gap=gap[:400],
                why_it_matters=why[:600],
                suggested_query=query[:300],
                prerequisites=[_clean(p)[:120] for p in _as_list(item.get("prerequisites")) if _clean(p)][:8],
                priority=priority,
            )
        )
    out.sort(key=lambda g: g.priority)
    return out


def _parse_faq(raw: Any, max_faq: int) -> list[EnrichedFaq]:
    out: list[EnrichedFaq] = []
    seen: set[str] = set()
    for item in _as_list(raw)[:max_faq]:
        if not isinstance(item, dict):
            continue
        q = _clean(item.get("q") or item.get("question"))
        a = _clean(item.get("a") or item.get("answer"))
        if not q or not a:
            continue
        key = re.sub(r"\W+", "", q.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(EnrichedFaq(q=q[:400], a=a[:2_000]))
    return out


def _normalise_tests(value: Any) -> str:
    text = _clean(value).lower()
    if text in ("concept", "comparison", "limitation", "mechanism", "general"):
        return text
    return "general"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


__all__ = [
    "TIER_EXACT",
    "EnrichmentResult",
    "build_corpus_text",
    "enrich_and_fill",
    "enrich_corpus",
    "parse_enrichment",
]
