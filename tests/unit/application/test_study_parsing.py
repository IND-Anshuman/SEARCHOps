"""Tests for the study-guide parser and the answer pass.

The parser was JSON-only, and the model frequently ignores a "return ONLY
JSON" instruction and answers with a numbered prose list. Those questions
are real and drawn from the report, so discarding them left the study guide
empty on real runs while every fixture-driven test stayed green.
"""

from __future__ import annotations

import pytest

from searchops.application.exports.study import (
    StudyGuide,
    StudyQuestion,
    _parse_answers,
    _parse_questions,
    _questions_from_prose,
    build_study_guide,
)
from searchops.application.report_document import ReportDocument

MD = "# Quantum Error Correction\n\n## Findings\n\nSurface codes scale logarithmically with code distance.\n"

# The exact shape the model produced on a real run.
PROSE_OUTPUT = """**Questions to test understanding of the report excerpt**

1. What two-dimensional lattice geometry is used to define surface codes in the report?
2. Which two types of stabilizer generators are defined on the surface-code lattice?
3. How are logical X and Z operators represented in the surface-code lattice?
4. What determines the code distance in a surface code, and how does it scale?
5. What is the typical error-rate threshold for depolarizing noise?
6. How are logical operators constructed in a planar surface code?
7. What role do ancilla qubits play in syndrome extraction?
8. How many ancilla qubits are required per round of error detection?
"""

JSON_OUTPUT = (
    '{"questions":[{"question":"What is a surface code?","answer":"A 2D lattice code.","section":"Findings"}]}'
)


@pytest.mark.unit
def test_prose_questions_are_recovered():
    """Regression: the model answers with a numbered list, not JSON."""
    questions = _questions_from_prose(PROSE_OUTPUT)
    assert len(questions) == 8
    assert questions[0].question.startswith("What two-dimensional lattice geometry")
    assert all(q.answer == "" for q in questions), "prose carries no answers"


@pytest.mark.unit
def test_prose_parser_drops_headings_and_non_questions():
    raw = (
        "**Questions to test understanding**\n"
        "\n"
        "Here are some questions based on the report:\n"
        "1. What is the threshold for surface codes in this report?\n"
        "2. This is a statement, not a question.\n"
        "3. Why does the code distance matter for error rates?\n"
    )
    questions = _questions_from_prose(raw)
    assert len(questions) == 2
    assert "threshold" in questions[0].question
    assert "code distance" in questions[1].question


@pytest.mark.unit
def test_json_questions_still_parse():
    questions = _parse_questions(JSON_OUTPUT)
    assert len(questions) == 1
    assert questions[0].question == "What is a surface code?"
    assert questions[0].answer == "A 2D lattice code."


@pytest.mark.unit
def test_empty_output_yields_nothing():
    assert _parse_questions("") == []
    assert _parse_questions(None) == []
    assert _parse_questions("There is nothing useful here.") == []


@pytest.mark.unit
def test_parse_answers_reads_the_answer_payload():
    raw = '{"answers":[{"question":"Q1","answer":"A1"},{"question":"Q2","answer":"A2"}]}'
    answers = _parse_answers(raw)
    assert len(answers) == 2
    assert answers[0] == {"question": "Q1", "answer": "A1"}


@pytest.mark.unit
def test_parse_answers_drops_entries_with_no_answer():
    raw = '{"answers":[{"question":"Q1","answer":""},{"question":"Q2","answer":"A2"}]}'
    answers = _parse_answers(raw)
    assert len(answers) == 1
    assert answers[0]["question"] == "Q2"


class _FakeRouter:
    """Returns a canned response per prompt shape."""

    def __init__(self, first: str, second: str | None = None) -> None:
        self.first = first
        self.second = second
        self.calls: list[str] = []

    async def generate(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        if len(self.calls) == 1:
            return self.first
        return self.second or ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prose_questions_get_answers_from_a_second_pass():
    """A question with no answer is not a study aid; a second pass fills it."""
    doc = ReportDocument.from_markdown("j", "qec", MD)
    answer_payload = (
        '{"answers":['
        '{"question":"What two-dimensional lattice geometry is used to define surface codes in the report?",'
        '"answer":"A square lattice."},'
        '{"question":"Which two types of stabilizer generators are defined on the surface-code lattice?",'
        '"answer":"X and Z generators."},'
        '{"question":"How are logical X and Z operators represented in the surface-code lattice?",'
        '"answer":"As strings of physical qubits."},'
        '{"question":"What determines the code distance in a surface code, and how does it scale?",'
        '"answer":"The minimum weight of a logical operator."},'
        '{"question":"What is the typical error-rate threshold for depolarizing noise?",'
        '"answer":"About one percent."},'
        '{"question":"How are logical operators constructed in a planar surface code?",'
        '"answer":"By chaining boundary qubits."},'
        '{"question":"What role do ancilla qubits play in syndrome extraction?",'
        '"answer":"They measure stabilizers."},'
        '{"question":"How many ancilla qubits are required per round of error detection?",'
        '"answer":"One per stabilizer."}'
        "]}"
    )
    router = _FakeRouter(PROSE_OUTPUT, answer_payload)

    guide = await build_study_guide(doc, router=router)

    assert len(guide.questions) == 8
    assert all(q.answer for q in guide.questions), "every question must be answered"
    assert guide.questions[0].answer == "A square lattice."
    assert len(router.calls) == 2, "a second pass must have been made"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_questions_without_answers_are_dropped_when_the_pass_fails():
    """A failed answer pass must not produce unanswered questions."""
    doc = ReportDocument.from_markdown("j", "qec", MD)
    router = _FakeRouter(PROSE_OUTPUT, "")

    guide = await build_study_guide(doc, router=router)

    assert guide.questions == []
    assert guide.note


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_guide_with_no_usable_output_says_so():
    doc = ReportDocument.from_markdown("j", "qec", MD)
    router = _FakeRouter("I cannot help with that.")

    guide = await build_study_guide(doc, router=router)

    assert guide.is_empty
    assert guide.note


@pytest.mark.unit
def test_study_guide_dataclass_round_trip():
    guide = StudyGuide(questions=[StudyQuestion(question="Q", answer="A", section="S")], note="n")
    assert guide.to_dict() == {
        "questions": [{"question": "Q", "answer": "A", "section": "S"}],
        "note": "n",
    }
