"""
Tests for the report shaper.

The core contract: the user NEVER sees an empty, refused, or unstructured
report, and any degradation is honestly labelled rather than hidden.

The last block is the no-fabrication contract — a synthesized report must trace
every line to real evidence, and must say so plainly when there is none.
"""

from __future__ import annotations

import pytest

from searchops.llm.report_shaper import (
    ReportQuality,
    build_evidence_report,
    ensure_sections,
    extract_sections,
    looks_like_refusal,
    shape_report,
)


class _Citation:
    def __init__(self, url: str) -> None:
        self.url = url


class _Claim:
    def __init__(self, text: str, url: str = "", confidence: float = 0.9) -> None:
        self.text = text
        self.confidence = confidence
        self.citations = [_Citation(url)] if url else []


class _Entity:
    def __init__(self, name: str, etype: str = "Technology", description: str = "") -> None:
        self.name = name
        self.entity_type = etype
        self.description = description


GOOD_REPORT = """# Research Report: Python

## Executive Summary
Python is a widely used programming language with strong ecosystem support.

## Key Findings
- Python 3.12 shipped in October 2023.
- Python dominates machine learning tooling.

## Conclusion
Python remains a dominant language in 2026.
"""

SOURCES = [
    {"url": "https://example.com/a", "title": "Source A"},
    {"url": "https://example.com/b", "title": "Source B"},
]


# ── Detection helpers ────────────────────────────────────────────────────────


class TestDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "I'm sorry, I cannot help with that.",
            "I am unable to assist with this request.",
            "I don't have access to that information.",
        ],
    )
    def test_refusal_markers_detected(self, text):
        assert looks_like_refusal(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            GOOD_REPORT,
            # A substantive report that merely opens apologetically. Must clear
            # the _SUBSTANTIVE_CHARS bar so it is never mistaken for a refusal.
            (
                "I'm sorry for the delay in producing this analysis. "
                "Python's ecosystem continues to expand across data engineering, "
                "machine learning, and backend services, with packaging tooling "
                "maturing considerably over the past several release cycles and "
                "adoption broadening into embedded and edge deployment contexts."
            ),
            # Opens with "As an AI language model" — which is NOT a refusal
            # marker, because models use it to open real reports too.
            (
                "As an AI language model I am able to summarise source material, "
                "and this report does exactly that: the retrieved documents agree "
                "that runtime performance improved materially in recent releases, "
                "while the standard library remained backward compatible throughout "
                "the observed period under review here."
            ),
        ],
    )
    def test_real_reports_not_flagged(self, text):
        assert looks_like_refusal(text) is False

    def test_substantive_threshold_separates_refusal_from_report(self):
        short_refusal = "I'm sorry, I cannot help with that."
        assert len(short_refusal) < 300
        assert looks_like_refusal(short_refusal) is True

    def test_borderline_body_just_under_threshold_still_refusal(self):
        # 299 chars, opens with a marker — still below the substantive bar.
        body = "I cannot assist. " + "x" * 280
        assert len(body) < 300
        assert looks_like_refusal(body) is True

    def test_empty_string_is_not_a_refusal(self):
        assert looks_like_refusal("") is False

    def test_extract_sections_in_order(self):
        assert extract_sections(GOOD_REPORT) == [
            "Research Report: Python",
            "Executive Summary",
            "Key Findings",
            "Conclusion",
        ]

    def test_extract_sections_empty_for_prose(self):
        assert extract_sections("Just some prose with no headings at all.") == []


# ── Case: usable report passes through ───────────────────────────────────────


class TestGoodReport:
    def test_good_report_is_untouched(self):
        shaped = shape_report(GOOD_REPORT, query="Python")
        assert shaped.quality == ReportQuality.OK
        assert shaped.is_synthesized is False
        assert shaped.user_message == ""
        # No sections added, no sources appended — content is the model's own
        # (a trailing newline is the only permitted difference).
        assert shaped.markdown.strip() == GOOD_REPORT.strip()

    def test_sections_are_reported(self):
        shaped = shape_report(GOOD_REPORT, query="Python")
        assert "Executive Summary" in shaped.sections
        assert "Key Findings" in shaped.sections

    def test_citation_count_from_sources(self):
        shaped = shape_report(GOOD_REPORT, query="Python", sources=SOURCES)
        assert shaped.citation_count == 2

    def test_duplicate_source_urls_counted_once(self):
        dupes = [*SOURCES, {"url": "https://example.com/a", "title": "again"}]
        shaped = shape_report(GOOD_REPORT, sources=dupes)
        assert shaped.citation_count == 2


# ── Case: empty model output ─────────────────────────────────────────────────


class TestEmptyOutput:
    def test_empty_string_yields_readable_report(self):
        shaped = shape_report("", query="Python", claims=[_Claim("A fact.", "https://a")], sources=SOURCES)
        assert shaped.markdown.strip() != ""
        assert shaped.quality == ReportQuality.RAW_EMPTY
        assert shaped.is_synthesized is True

    def test_whitespace_only_yields_readable_report(self):
        shaped = shape_report("   \n\n  ", query="Python", claims=[_Claim("A fact.", "https://a")])
        assert shaped.markdown.strip() != ""
        assert shaped.is_synthesized is True

    def test_none_input_does_not_raise(self):
        shaped = shape_report(None, query="Python", sources=SOURCES)
        assert shaped.markdown.strip() != ""

    def test_very_short_output_is_treated_as_empty(self):
        shaped = shape_report("N/A", query="Python", sources=SOURCES)
        assert shaped.is_synthesized is True

    def test_synthesized_report_contains_the_real_claim(self):
        shaped = shape_report(
            "", query="Python", claims=[_Claim("Python 3.12 shipped in 2023.", "https://a")], sources=SOURCES
        )
        assert "Python 3.12 shipped in 2023." in shaped.markdown

    def test_user_message_explains_plainly(self):
        shaped = shape_report("", query="Python", claims=[_Claim("A fact.")], sources=SOURCES)
        assert "sources" in shaped.user_message.lower()
        assert "model" in shaped.user_message.lower()


# ── Case: model refusal ──────────────────────────────────────────────────────


class TestRefusal:
    def test_refusal_is_replaced_by_evidence_report(self):
        shaped = shape_report(
            "I'm sorry, I cannot help with that.",
            query="Python",
            claims=[_Claim("Python is widely used.", "https://a")],
            sources=SOURCES,
        )
        assert shaped.quality == ReportQuality.REFUSAL
        assert "I'm sorry" not in shaped.markdown
        assert "Python is widely used." in shaped.markdown

    def test_refusal_has_no_sections_of_its_own(self):
        shaped = shape_report(
            "I am unable to assist.",
            query="Python",
            claims=[_Claim("A fact.", "https://a")],
            sources=SOURCES,
        )
        assert shaped.sections, "synthesized report must be structured"

    def test_refusal_with_sources_does_not_deny_those_sources(self):
        """Regression: sources exist but no claims were extracted.

        The old branch tested only ``claims or entities``, so a refusal with
        real sources produced "No sources could be read for this query" in the
        Executive Summary while listing those same sources under ## Sources.
        The report contradicted itself.
        """
        docs = [
            {"url": "https://a.com", "title": "A", "content": "x"},
            {"url": "https://b.com", "title": "B", "content": "y"},
        ]
        shaped = shape_report(
            "I'm sorry, I cannot help with that.",
            query="qec",
            sources=docs,
        )
        assert shaped.quality == ReportQuality.REFUSAL
        assert shaped.citation_count == 2
        assert "No sources could be read" not in shaped.markdown
        assert "No sources were retrieved" not in shaped.markdown
        # The sources must still be listed.
        assert "https://a.com" in shaped.markdown
        assert "https://b.com" in shaped.markdown

    def test_refusal_with_no_evidence_at_all_still_states_it_plainly(self):
        shaped = shape_report("I cannot help with that.", query="qec")
        assert shaped.quality == ReportQuality.NO_EVIDENCE
        assert "No sources could be read" in shaped.markdown

    def test_empty_output_with_sources_does_not_deny_those_sources(self):
        """Same regression via the RAW_EMPTY path, not just the refusal path."""
        docs = [{"url": "https://a.com", "title": "A", "content": "x"}]
        shaped = shape_report("", query="qec", sources=docs)
        assert "No sources could be read" not in shaped.markdown
        assert "https://a.com" in shaped.markdown


# ── Case: unstructured prose ─────────────────────────────────────────────────


class TestUnstructured:
    def test_prose_without_headings_gets_a_summary_section(self):
        prose = "Python is a programming language. " * 10
        shaped = shape_report(prose, query="Python")
        assert shaped.quality == ReportQuality.NO_SECTIONS
        assert "Executive Summary" in shaped.markdown

    def test_original_prose_is_preserved_verbatim(self):
        prose = "Python is a programming language. " * 10
        shaped = shape_report(prose, query="Python")
        assert prose.strip() in shaped.markdown

    def test_structured_report_is_not_modified(self):
        shaped = shape_report(GOOD_REPORT, query="Python")
        assert shaped.quality == ReportQuality.OK


# ── Case: no evidence at all ─────────────────────────────────────────────────


class TestNoEvidence:
    def test_no_evidence_states_it_plainly(self):
        shaped = shape_report("", query="Python", claims=[], entities=[], sources=[])
        assert shaped.quality == ReportQuality.NO_EVIDENCE
        assert "No sources could be read" in shaped.markdown

    def test_no_evidence_does_not_invent_findings(self):
        shaped = shape_report("", query="Python")
        assert "Key Findings" in shaped.markdown
        assert "No factual claims" in shaped.markdown

    def test_no_evidence_user_message(self):
        shaped = shape_report("", query="Python")
        assert "nothing to report" in shaped.user_message.lower()


# ── Sources handling ─────────────────────────────────────────────────────────


class TestSources:
    def test_sources_appended_when_model_cited_none(self):
        report = "## Executive Summary\n\nSome analysis of the topic in question here.\n\n## Conclusion\n\nDone."
        shaped = shape_report(report, query="Python", sources=SOURCES)
        assert "## Sources" in shaped.markdown
        assert "https://example.com/a" in shaped.markdown

    def test_sources_not_appended_when_already_present(self):
        report = "## Executive Summary\n\nAnalysis here.\n\n## Sources\n\n- [A](https://example.com/a)\n"
        shaped = shape_report(report, query="Python", sources=SOURCES)
        assert shaped.markdown.count("## Sources") == 1

    def test_non_http_sources_ignored(self):
        shaped = shape_report(GOOD_REPORT, sources=[{"url": "not-a-url", "title": "x"}])
        assert shaped.citation_count == 0

    def test_duplicate_sources_deduped_in_appended_list(self):
        report = "## Executive Summary\n\nSome analysis of the topic in question.\n"
        dupes = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://a.com", "title": "A again"},
        ]
        shaped = shape_report(report, sources=dupes)
        source_lines = [ln for ln in shaped.markdown.splitlines() if "https://a.com" in ln]
        assert len(source_lines) == 1, "one rendered line per unique URL"


# ── build_evidence_report directly ───────────────────────────────────────────


class TestEvidenceReport:
    def test_contains_all_canonical_sections(self):
        md = build_evidence_report(
            "Python",
            claims=[_Claim("Fact one.", "https://a"), _Claim("Fact two.", "https://b")],
            entities=[_Entity("Python", "Technology", "A language.")],
            sources=SOURCES,
        )
        for section in ("Executive Summary", "Key Findings", "Notable Entities", "Sources", "Conclusion"):
            assert f"## {section}" in md

    def test_entity_renders_with_type_and_description(self):
        md = build_evidence_report("Python", entities=[_Entity("Python", "Technology", "A language.")])
        assert "**Python** (Technology)" in md
        assert "A language." in md

    def test_high_confidence_claim_is_marked(self):
        md = build_evidence_report("Python", claims=[_Claim("A fact.", "https://a", confidence=0.95)])
        assert "high confidence" in md

    def test_low_confidence_claim_is_not_marked(self):
        md = build_evidence_report("Python", claims=[_Claim("A fact.", "https://a", confidence=0.3)])
        assert "high confidence" not in md

    def test_claim_links_to_its_source(self):
        md = build_evidence_report("Python", claims=[_Claim("A fact.", "https://a.com/x")])
        assert "[source](https://a.com/x)" in md

    def test_claims_without_text_are_skipped(self):
        md = build_evidence_report("Python", claims=[_Claim("   "), _Claim("Real fact.")])
        assert "Real fact." in md

    def test_empty_evidence_is_honest(self):
        md = build_evidence_report("Python")
        assert "No sources could be read" in md
        assert "No factual claims" in md

    def test_empty_query_still_produces_a_heading(self):
        md = build_evidence_report("")
        assert md.startswith("# Research Report")

    def test_sources_are_deduped(self):
        md = build_evidence_report("Python", sources=[*SOURCES, {"url": "https://example.com/a", "title": "dup"}])
        assert md.count("https://example.com/a") == 1


# ── ensure_sections ──────────────────────────────────────────────────────────


class TestEnsureSections:
    def test_adds_summary_to_prose(self):
        md, changed = ensure_sections("Just prose.", "Python")
        assert changed is True
        assert "## Executive Summary" in md

    def test_leaves_structured_alone(self):
        md, changed = ensure_sections(GOOD_REPORT, "Python")
        assert changed is False
        assert md == GOOD_REPORT

    def test_summary_mentions_the_query(self):
        md, _ = ensure_sections("Just prose.", "quantum computing")
        assert "quantum computing" in md


# ── No-fabrication contract ──────────────────────────────────────────────────


class TestNoFabrication:
    def test_synthesized_text_only_contains_real_claims(self):
        claims = [_Claim("Alpha fact.", "https://a"), _Claim("Beta fact.", "https://b")]
        shaped = shape_report("", query="Q", claims=claims, sources=SOURCES)
        assert "Alpha fact." in shaped.markdown
        assert "Beta fact." in shaped.markdown
        # No third finding may appear: count bullets inside Key Findings only,
        # since the Sources list is also bulleted.
        findings_block = shaped.markdown.split("## Key Findings")[1].split("##")[0]
        assert findings_block.count("\n- ") == 2

    def test_synthesized_sources_only_contain_real_urls(self):
        shaped = shape_report("", query="Q", claims=[_Claim("A fact.", "https://a")], sources=SOURCES)
        assert "https://example.com/a" in shaped.markdown
        assert "https://example.com/b" in shaped.markdown
        assert "https://invented" not in shaped.markdown

    def test_missing_claim_fields_are_not_filled_in(self):
        md = build_evidence_report("Q", claims=[_Claim("A fact.")])
        assert "A fact." in md
        assert "http" not in md.split("## Sources")[0].replace("https://", "http")

    def test_no_evidence_produces_no_findings(self):
        shaped = shape_report("", query="Q")
        assert "No factual claims" in shaped.markdown
        assert "No sources were retrieved" in shaped.markdown

    def test_entity_without_description_renders_without_placeholder(self):
        md = build_evidence_report("Q", entities=[_Entity("BareName", "Technology", "")])
        assert "**BareName** (Technology)" in md
        assert "— None" not in md
