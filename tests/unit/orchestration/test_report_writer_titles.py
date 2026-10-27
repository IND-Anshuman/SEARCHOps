"""The report writer must not emit a section title twice.

A section written by the model can open with its own ``## Title`` and then
restate the title as the first prose line. Both survive into the report, so a
reader sees the heading twice — once at heading size and once as body text.
The heading is the real one; the echoed line is removed.
"""

from __future__ import annotations

from searchops.orchestration.nodes.report_writer import (
    _strip_duplicated_section_title,
)


class TestStripDuplicatedSectionTitle:
    def test_echoed_title_beneath_heading_is_removed(self) -> None:
        text = (
            "## RESTful API Architectural Foundations\n\n"
            "RESTful API Architectural Foundations\n\n"
            "The conceptual bedrock of any robust RESTful service."
        )
        out = _strip_duplicated_section_title(text, "RESTful API Architectural Foundations")
        assert out.count("RESTful API Architectural Foundations") == 1
        assert "The conceptual bedrock" in out

    def test_bold_wrapped_echo_is_removed(self) -> None:
        text = "## Design Goals\n\n**Design Goals**\n\nReal prose follows."
        out = _strip_duplicated_section_title(text, "Design Goals")
        assert out.count("Design Goals") == 1

    def test_echo_is_not_removed_when_no_heading_present(self) -> None:
        """Without a heading there is nothing to duplicate."""
        text = "Design Goals\n\nReal prose follows."
        out = _strip_duplicated_section_title(text, "Design Goals")
        assert out == text

    def test_sentence_starting_with_the_title_is_kept(self) -> None:
        """Legitimate prose that opens with the title must not be dropped."""
        text = "## Design Goals\n\nDesign Goals are the first thing to settle.\n\nMore."
        out = _strip_duplicated_section_title(text, "Design Goals")
        assert out == text

    def test_unrelated_prose_is_untouched(self) -> None:
        text = "## Design Goals\n\nThe evidence is thin on this point.\n\nMore."
        out = _strip_duplicated_section_title(text, "Design Goals")
        assert out == text

    def test_whitespace_and_marker_tolerance(self) -> None:
        text = "##  Design Goals  \n\n   Design Goals   \n\nBody text."
        out = _strip_duplicated_section_title(text, "Design Goals")
        assert out.count("Design Goals") == 1
