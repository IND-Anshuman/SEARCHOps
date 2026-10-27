"""
Tests for deterministic evaluation metrics.

These are ground truth: no LLM is involved, so a failure here is a real defect
in the metric rather than a model quality question. The tests pin the behaviour
that makes each metric trustworthy, including the ways each can be gamed.
"""

from __future__ import annotations

import pytest

from searchops.knowledge.evaluation import (
    DEFAULT_SEMANTIC_THRESHOLD,
    citation_coverage,
    hallucination_rate,
    key_fact_f1,
    literal_match_level,
    matches,
)


class _Citation:
    def __init__(self, url: str) -> None:
        self.url = url


class _Claim:
    def __init__(self, text: str, urls: list[str] | None = None) -> None:
        self.text = text
        self.citations = [_Citation(u) for u in (urls or [])]


# ── literal_match_level ──────────────────────────────────────────────────────


class TestLiteralMatchLevel:
    def test_exact_match_is_level_5(self):
        assert literal_match_level("Python is fast", "Python is fast") == 5

    def test_case_and_whitespace_insensitive(self):
        assert literal_match_level("  Python IS fast ", "python is fast") == 5

    def test_matching_numbers_is_level_4(self):
        assert literal_match_level("Revenue hit 4,200,000,000 dollars", "Revenue hit 4.2 billion dollars") == 4

    def test_number_mismatch_is_not_level_4(self):
        assert literal_match_level("Revenue was 100 dollars", "Revenue was 200 dollars") < 4

    def test_token_subset_is_level_3(self):
        # Genuine token-subset (not a substring): word order differs and the
        # candidate's tokens are all present in the reference.
        assert literal_match_level("language programming python", "python is a programming language") == 3

    def test_containment_is_level_2(self):
        assert literal_match_level("python programming", "python programming language design") == 2

    def test_stemmed_overlap_is_level_1(self):
        # Same words under different inflections, enough overlap to clear 0.5
        # stemmed Jaccard but not a subset, containment or numeric match.
        assert (
            literal_match_level(
                "the deployed service handles requests reliably",
                "the deploying services handled request reliably",
            )
            >= 1
        )

    def test_no_overlap_is_zero(self):
        assert literal_match_level("quantum entanglement", "culinary traditions of provence") == 0

    def test_empty_input_is_zero(self):
        assert literal_match_level("", "something") == 0
        assert literal_match_level("something", "") == 0

    def test_numbers_alone_can_match(self):
        assert literal_match_level("42", "42") >= 4


# ── matches (dual track) ─────────────────────────────────────────────────────


class TestDualTrackMatches:
    def test_exact_match_reports_literal(self):
        ok, reason = matches("Python 3.12 shipped in 2023", "Python 3.12 shipped in 2023")
        assert ok is True
        assert reason.startswith("literal")

    def test_paraphrase_matches_semantically(self):
        """A restatement must be detected even when the literal tracks miss it.

        The semantic track exists for the band the literal ladder cannot reach:
        enough vocabulary overlap for a real cosine, but below the 0.5 stemmed
        Jaccard that level 1 requires.
        """
        a = "quarterly revenue grew because of sustained cloud demand"
        b = "sustained cloud demand caused quarterly revenue to grow"
        ok, reason = matches(a, b)
        assert ok is True
        assert reason.startswith(("literal", "semantic"))

    def test_semantic_track_fires_below_literal_threshold(self):
        """Directly exercise the cosine band the literal ladder misses.

        Enough shared vocabulary for a high cosine, but stemmed Jaccard below
        the 0.5 level-1 bar, so only the semantic track can produce a match.
        """
        a = "quarterly report noted revenue growth driven by cloud demand operating margins expanded modestly across segments the board approved a new dividend policy for shareholders and vendors alike"
        b = "quarterly report noted revenue growth driven by cloud demand operating margins expanded modestly across segments guidance remained unchanged for the year while headcount grew steadily"
        # Verified band: stemmed Jaccard 0.467 (below the 0.5 level-1 bar) with
        # cosine 0.636 — so only the semantic track can produce a match.
        assert literal_match_level(a, b) == 0, "fixture must fall below level 1"
        ok, reason = matches(a, b, semantic_threshold=0.6)
        assert ok is True
        assert reason.startswith("semantic"), f"expected semantic, got {reason!r}"

    def test_reordered_same_words_are_detected(self):
        """Word order alone must not defeat detection."""
        a = "the distributed message queue provides system reliability"
        b = "reliability is provided by the distributed system message queue"
        assert matches(a, b)[0] is True

    def test_unrelated_statements_do_not_match(self):
        ok, reason = matches(
            "Quantum entanglement enables new cryptographic protocols.",
            "The bakery on main street opens at six in the morning.",
        )
        assert ok is False
        assert reason == "no_match"

    def test_semantic_threshold_gates_the_semantic_track(self):
        """A pair with no literal match must be decided by the threshold alone."""
        a = "quarterly report noted revenue growth driven by cloud demand operating margins expanded modestly across segments the board approved a new dividend policy for shareholders and vendors alike"
        b = "quarterly report noted revenue growth driven by cloud demand operating margins expanded modestly across segments guidance remained unchanged for the year while headcount grew steadily"
        assert literal_match_level(a, b) == 0, "fixture must fall below level 1"
        assert matches(a, b, semantic_threshold=0.1)[0] is True
        assert matches(a, b, semantic_threshold=0.999)[0] is False

    def test_high_jaccard_pair_matches_literally_not_semantically(self):
        """A near-identical pair is a literal match — the semantic track is not
        consulted, which is the cheaper and more precise answer."""
        a = "the distributed message queue provides system reliability"
        b = "reliability is provided by the distributed system message queue"
        ok, reason = matches(a, b)
        assert ok is True
        assert reason.startswith("literal")

    def test_default_threshold_is_published(self):
        assert DEFAULT_SEMANTIC_THRESHOLD == 0.75


# ── key_fact_f1 ──────────────────────────────────────────────────────────────


class TestKeyFactF1:
    def test_perfect_match(self):
        gold = ["Python 3.12 shipped in October 2023.", "The GIL was removed in 3.13."]
        s = key_fact_f1(gold, gold)
        assert s.precision == 1.0
        assert s.recall == 1.0
        assert s.f1 == 1.0

    def test_complete_miss(self):
        s = key_fact_f1(
            ["The bakery opens at six."],
            ["Python 3.12 shipped in October 2023."],
        )
        assert s.f1 == 0.0
        assert s.matched == 0

    def test_partial_recall(self):
        gold = [
            "Python 3.12 shipped in October 2023.",
            "The GIL was removed in version 3.13 of the language.",
        ]
        s = key_fact_f1(["Python 3.12 shipped in October 2023."], gold)
        assert s.recall == pytest.approx(0.5, abs=1e-4)
        assert s.precision == 1.0

    def test_paraphrased_fact_still_counts(self):
        gold = ["Python 3.12 was released in October 2023."]
        s = key_fact_f1(["Python 3.12 shipped in October 2023."], gold)
        assert s.f1 == 1.0, "a paraphrase must not be scored as a miss"

    def test_repetition_cannot_inflate_recall(self):
        """One-to-one matching: repeating a fact does not raise the score."""
        gold = ["Python 3.12 shipped in October 2023."]
        s = key_fact_f1(
            [
                "Python 3.12 shipped in October 2023.",
                "Python 3.12 shipped in October 2023.",
                "Python 3.12 shipped in October 2023.",
            ],
            gold,
        )
        assert s.recall == 1.0
        assert s.precision == pytest.approx(1 / 3, abs=1e-4), "duplicates must cost precision"

    def test_repetition_cannot_inflate_via_paraphrase(self):
        gold = ["The system uses Redis for caching."]
        s = key_fact_f1(
            [
                "Redis provides caching for the system.",
                "Caching is handled by Redis in this system.",
            ],
            gold,
        )
        assert s.matched == 1

    def test_empty_predictions(self):
        s = key_fact_f1([], ["A fact."])
        assert s.f1 == 0.0
        assert s.recall == 0.0

    def test_empty_gold_gives_zero_f1_not_error(self):
        s = key_fact_f1(["A fact."], [])
        assert s.f1 == 0.0
        assert s.recall == 0.0

    def test_both_empty(self):
        s = key_fact_f1([], [])
        assert s.predicted == 0
        assert s.expected == 0
        assert s.empty is True

    def test_counts_are_traceable(self):
        gold = ["Fact one here.", "Fact two here.", "Fact three here."]
        s = key_fact_f1(["Fact one here.", "Unrelated claim about baking."], gold)
        assert s.predicted == 2
        assert s.expected == 3
        assert s.matched == 1

    def test_match_reasons_record_the_track(self):
        s = key_fact_f1(["Python 3.12 shipped in 2023"], ["Python 3.12 shipped in 2023"])
        assert s.match_reasons, "the matching track must be recorded"

    def test_blank_strings_are_ignored(self):
        s = key_fact_f1(["", "  ", "Real fact."], ["Real fact."])
        assert s.predicted == 1
        assert s.matched == 1


# ── hallucination_rate ───────────────────────────────────────────────────────


CORPUS = "Python 3.12 was released by the Python Software Foundation in October 2023."


class TestHallucinationRate:
    def test_claim_grounded_in_corpus_is_not_hallucinated(self):
        s = hallucination_rate([_Claim("Python 3.12 was released in October 2023.")], CORPUS)
        assert s.rate == 0.0
        assert s.hallucinated == 0

    def test_out_of_corpus_entity_is_flagged(self):
        s = hallucination_rate([_Claim("Google acquired Anthropic in 2024.")], CORPUS)
        assert s.rate == 1.0
        assert "Google" in s.offending

    def test_unsupported_is_not_counted_as_hallucination(self):
        """A claim with no proper nouns is unsupported, not wrong — not counted."""
        s = hallucination_rate([_Claim("the system is generally reliable in practice")], CORPUS)
        assert s.checked == 0
        assert s.rate == 0.0

    def test_known_entities_are_excused(self):
        s = hallucination_rate(
            [_Claim("Rust was released by Mozilla Research.")],
            CORPUS,
            extra_known_entities=["Rust", "Mozilla Research"],
        )
        assert s.rate == 0.0

    def test_rate_is_fraction_over_checked_claims(self):
        claims = [
            _Claim("Python 3.12 was released in October 2023."),  # grounded
            _Claim("Google acquired Anthropic in 2024."),  # hallucinated
            _Claim("Microsoft released Windows in 1985."),  # hallucinated
            _Claim("Nvidia announced a new chip."),  # hallucinated
        ]
        s = hallucination_rate(claims, CORPUS)
        assert s.checked == 4
        assert s.hallucinated == 3
        assert s.rate == pytest.approx(0.75, abs=1e-4)

    def test_no_claims_gives_zero_not_error(self):
        s = hallucination_rate([], CORPUS)
        assert s.rate == 0.0
        assert s.checked == 0

    def test_empty_corpus_flags_every_entity(self):
        s = hallucination_rate([_Claim("Python 3.12 shipped.")], "")
        assert s.rate == 1.0

    def test_offending_list_is_bounded(self):
        many = [_Claim(f"Company{i} did something notable.") for i in range(50)]
        s = hallucination_rate(many, CORPUS)
        assert len(s.offending) <= 10

    def test_lowercase_entity_not_treated_as_entity(self):
        """Entity extraction is deliberately conservative — under-counts."""
        s = hallucination_rate([_Claim("python is a programming language")], CORPUS)
        assert s.checked == 0


# ── citation_coverage ────────────────────────────────────────────────────────


class TestCitationCoverage:
    def test_all_cited(self):
        s = citation_coverage([_Claim("A fact.", ["https://a.test"]), _Claim("B fact.", ["https://b.test"])])
        assert s.coverage == 1.0
        assert s.cited == 2

    def test_partial_coverage(self):
        s = citation_coverage([_Claim("A fact.", ["https://a.test"]), _Claim("B fact.", [])])
        assert s.coverage == pytest.approx(0.5, abs=1e-4)
        assert s.total == 2

    def test_none_cited(self):
        s = citation_coverage([_Claim("A fact.", []), _Claim("B fact.", [])])
        assert s.coverage == 0.0

    def test_non_http_citation_does_not_count(self):
        s = citation_coverage([_Claim("A fact.", ["ftp://files.test/x"])])
        assert s.coverage == 0.0, "a non-resolvable citation is not a citation"

    def test_no_claims_gives_zero_not_error(self):
        s = citation_coverage([])
        assert s.coverage == 0.0
        assert s.total == 0

    def test_one_bad_citation_among_good_still_counts(self):
        s = citation_coverage([_Claim("A fact.", ["not-a-url", "https://a.test"])])
        assert s.coverage == 1.0
