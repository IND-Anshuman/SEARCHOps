"""
Tests for the deterministic JSON repair ladder.

Every tier is exercised with the exact malformed shape it exists to fix, and
the tier reports are asserted so the ladder cannot silently change behaviour.
The last block pins the no-fabrication contract: salvage must never invent a
record or a field that does not appear in the input.
"""

from __future__ import annotations

import orjson
import pytest

from searchops.llm.json_repair import (
    TIER_BALANCED_SLICE,
    TIER_EXACT,
    TIER_FENCE_STRIP,
    TIER_SPAN_SALVAGE,
    TIER_STRUCTURAL_REPAIR,
    repair_json,
)

# ── Tier 0: exact ────────────────────────────────────────────────────────────


class TestTierExact:
    def test_clean_object(self):
        outcome = repair_json('{"claims": [{"text": "a"}]}')
        assert outcome.repair_tier == TIER_EXACT
        assert outcome.salvaged is False
        assert outcome.parsed == {"claims": [{"text": "a"}]}

    def test_clean_array(self):
        outcome = repair_json("[1, 2, 3]")
        assert outcome.repair_tier == TIER_EXACT
        assert outcome.parsed == [1, 2, 3]

    def test_whitespace_is_tolerated(self):
        outcome = repair_json('  \n {"claims": []}  \n ')
        assert outcome.repair_tier == TIER_EXACT


# ── Tier 1: fence strip ──────────────────────────────────────────────────────


class TestTierFenceStrip:
    def test_json_fence(self):
        outcome = repair_json('```json\n{"claims": [{"text": "x"}]}\n```')
        assert outcome.repair_tier == TIER_FENCE_STRIP
        assert outcome.parsed == {"claims": [{"text": "x"}]}

    def test_bare_fence(self):
        outcome = repair_json('```\n{"claims": []}\n```')
        assert outcome.repair_tier == TIER_FENCE_STRIP

    def test_uppercase_json_marker(self):
        outcome = repair_json('```JSON\n{"claims": []}\n```')
        assert outcome.repair_tier == TIER_FENCE_STRIP

    def test_prose_before_fence(self):
        outcome = repair_json('Here is the output:\n```json\n{"claims": []}\n```')
        assert outcome.repair_tier == TIER_FENCE_STRIP

    def test_fence_plus_prose_escalates(self):
        """Fence content that is itself broken must fall through, not stop."""
        outcome = repair_json('```json\n{"claims": [{"text": "a",},]}\n```')
        # fence strips, then structural repair fixes the trailing commas
        assert outcome.repair_tier == TIER_STRUCTURAL_REPAIR
        assert outcome.parsed == {"claims": [{"text": "a"}]}


# ── Tier 2: balanced slice ───────────────────────────────────────────────────


class TestTierBalancedSlice:
    def test_prose_before_and_after(self):
        outcome = repair_json('Sure! Here you go:\n{"claims": [{"text": "a"}]}\nLet me know if you need more.')
        assert outcome.repair_tier == TIER_BALANCED_SLICE
        assert outcome.parsed == {"claims": [{"text": "a"}]}

    def test_nested_braces_not_split_early(self):
        outcome = repair_json('noise {"outer": {"inner": {"text": "a"}}} trailing')
        assert outcome.repair_tier == TIER_BALANCED_SLICE
        assert outcome.parsed == {"outer": {"inner": {"text": "a"}}}

    def test_braces_inside_strings_ignored(self):
        outcome = repair_json('prefix {"text": "a } b { c"} suffix')
        assert outcome.repair_tier == TIER_BALANCED_SLICE
        assert outcome.parsed == {"text": "a } b { c"}

    def test_escaped_quote_inside_string(self):
        outcome = repair_json('pre {"text": "say \\"hi\\""} post')
        assert outcome.repair_tier == TIER_BALANCED_SLICE
        assert outcome.parsed == {"text": 'say "hi"'}

    def test_array_slice(self):
        outcome = repair_json('Results: [{"text": "a"}, {"text": "b"}] done')
        assert outcome.repair_tier == TIER_BALANCED_SLICE
        assert outcome.parsed == [{"text": "a"}, {"text": "b"}]

    def test_no_brackets_at_all_salvages(self):
        outcome = repair_json("I could not find any claims in this document.")
        assert outcome.parsed is None
        assert outcome.repair_tier == TIER_SPAN_SALVAGE


# ── Tier 3: structural repair ────────────────────────────────────────────────


class TestTierStructuralRepair:
    def test_trailing_comma_in_object(self):
        outcome = repair_json('{"claims": [{"text": "a",}]}')
        assert outcome.repair_tier == TIER_STRUCTURAL_REPAIR
        assert outcome.parsed == {"claims": [{"text": "a"}]}

    def test_trailing_comma_in_array(self):
        outcome = repair_json('{"claims": [{"text": "a"},]}')
        assert outcome.repair_tier == TIER_STRUCTURAL_REPAIR
        assert outcome.parsed == {"claims": [{"text": "a"}]}

    def test_trailing_comma_with_newlines(self):
        outcome = repair_json('{\n  "claims": [\n    {"text": "a"},\n  ],\n}')
        assert outcome.repair_tier == TIER_STRUCTURAL_REPAIR
        assert outcome.parsed == {"claims": [{"text": "a"}]}

    def test_smart_double_quotes(self):
        outcome = repair_json("{\u201cclaims\u201d: [{\u201ctext\u201d: \u201ca\u201d}]}")
        assert outcome.repair_tier == TIER_STRUCTURAL_REPAIR
        assert outcome.parsed == {"claims": [{"text": "a"}]}

    def test_python_none(self):
        outcome = repair_json('{"claims": [{"text": "a", "confidence": None}]}')
        assert outcome.repair_tier == TIER_STRUCTURAL_REPAIR
        assert outcome.parsed == {"claims": [{"text": "a", "confidence": None}]}

    def test_python_true_false(self):
        outcome = repair_json('{"a": True, "b": False}')
        assert outcome.repair_tier == TIER_STRUCTURAL_REPAIR
        assert outcome.parsed == {"a": True, "b": False}

    def test_prose_plus_trailing_comma_combined(self):
        """The most common real shape: prose wrapper AND structural breakage."""
        outcome = repair_json('Here is the JSON:\n{"claims": [{"text": "a",},]}\nHope that helps!')
        assert outcome.repair_tier == TIER_STRUCTURAL_REPAIR
        assert outcome.parsed == {"claims": [{"text": "a"}]}

    def test_multiple_trailing_commas(self):
        outcome = repair_json('{"claims": [{"text": "a",},{"text": "b",},],}')
        assert outcome.repair_tier == TIER_STRUCTURAL_REPAIR
        assert outcome.parsed == {"claims": [{"text": "a"}, {"text": "b"}]}


# ── Tier 4: span salvage ─────────────────────────────────────────────────────


class TestTierSpanSalvage:
    def test_truncated_json_salvages_complete_records(self):
        """A truncated tail is the salvage case that matters most."""
        broken = (
            '{"claims": [{"text": "first claim", "confidence": 0.9}, '
            '{"text": "second claim", "confidence": 0.8, "citations": [{"url": "ht'
        )
        outcome = repair_json(broken)
        assert outcome.repair_tier == TIER_SPAN_SALVAGE
        assert outcome.salvaged is True
        assert outcome.recovered_count >= 1
        texts = [c.get("text") for c in outcome.parsed["claims"]]
        assert "first claim" in texts
        assert "second claim" in texts

    def test_salvage_parses_numeric_confidence(self):
        broken = '{"claims": [{"text": "a", "confidence": 0.75}, {"text": "b", "conf'
        outcome = repair_json(broken)
        assert outcome.salvaged is True
        for record in outcome.parsed["claims"]:
            if "confidence" in record:
                assert isinstance(record["confidence"], float)

    def test_salvage_ignores_unquoted_keys(self):
        broken = '{"claims": [{text: "a"}, {"text": "b"}]'
        outcome = repair_json(broken)
        # {text: ...} is not a valid object; only the quoted record survives.
        assert outcome.repair_tier == TIER_SPAN_SALVAGE
        texts = [c.get("text") for c in outcome.parsed["claims"]]
        assert "b" in texts

    def test_salvage_respects_custom_record_key(self):
        broken = '{"entities": [{"name": "Python", "type": "Technology"}, {"name": "N'
        outcome = repair_json(broken, record_key="entities", field_keys=("name", "type"))
        assert outcome.salvaged is True
        assert outcome.parsed["entities"][0]["name"] == "Python"

    def test_salvage_empty_when_no_matching_fields(self):
        broken = '{"claims": [{"other": "x"}, {"other": "y"}]'
        outcome = repair_json(broken, record_key="claims", field_keys=("text",))
        assert outcome.parsed is None
        assert outcome.recovered_count == 0

    def test_unescaped_quote_in_value(self):
        """Unescaped inner quotes are the classic unparseable shape."""
        broken = '{"claims": [{"text": "he said "hello" today", "confidence": 0.5}]}'
        outcome = repair_json(broken)
        # Whole object cannot parse; salvage should still recover something.
        assert outcome.repair_tier == TIER_SPAN_SALVAGE
        assert outcome.salvaged is True


# ── Unrecoverable / edge cases ───────────────────────────────────────────────


class TestUnrecoverable:
    def test_plain_prose(self):
        outcome = repair_json("I am unable to extract claims from this document.")
        assert outcome.parsed is None
        assert outcome.error == "unrecoverable"
        assert outcome.salvaged is False

    def test_empty_string(self):
        outcome = repair_json("")
        assert outcome.parsed is None
        assert outcome.error == "empty_input"

    def test_whitespace_only(self):
        outcome = repair_json("   \n  ")
        assert outcome.parsed is None

    def test_none_input(self):
        outcome = repair_json(None)  # type: ignore[arg-type]
        assert outcome.parsed is None
        assert outcome.error == "empty_input"

    def test_markdown_table_not_json(self):
        outcome = repair_json("| a | b |\n|---|---|\n| 1 | 2 |")
        assert outcome.parsed is None

    def test_scalar_json_is_returned(self):
        outcome = repair_json('"just a string"')
        assert outcome.repair_tier == TIER_EXACT
        assert outcome.parsed == "just a string"

    def test_outcome_is_frozen(self):
        import dataclasses

        outcome = repair_json('{"claims": []}')
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcome.repair_tier = 99  # type: ignore[misc]


# ── No-fabrication contract ──────────────────────────────────────────────────


class TestNoFabrication:
    def test_salvaged_text_is_verbatim_from_input(self):
        source_text = "The quick brown fox jumps"
        broken = f'{{"claims": [{{"text": "{source_text}", "confidence": 0.9}}, {{"text": "sec'
        outcome = repair_json(broken)
        texts = [c.get("text") for c in outcome.parsed["claims"]]
        assert source_text in texts

    def test_salvage_never_invents_a_field(self):
        broken = '{"claims": [{"text": "a"}]'
        outcome = repair_json(broken)
        # No citations/confidence appear in the source — none may be added.
        for record in outcome.parsed["claims"]:
            assert "citations" not in record
            assert "confidence" not in record

    def test_salvage_never_invents_a_record(self):
        broken = '{"claims": [{"text": "only one"}]'
        outcome = repair_json(broken)
        assert len(outcome.parsed["claims"]) == 1

    def test_confidence_bad_value_is_dropped_not_coerced(self):
        broken = '{"claims": [{"text": "a", "confidence": "high"}, {"text": "b", "conf'
        outcome = repair_json(broken)
        for record in outcome.parsed["claims"]:
            assert record.get("confidence") != "high"

    def test_valid_json_is_never_modified(self):
        original = '{"claims": [{"text": "a", "confidence": 0.5, "citations": []}]}'
        outcome = repair_json(original)
        assert outcome.repair_tier == TIER_EXACT
        assert outcome.parsed == orjson.loads(original)


# ── Realistic combined shapes ────────────────────────────────────────────────


class TestRealisticShapes:
    def test_full_claim_payload_clean(self):
        payload = orjson.dumps(
            {
                "claims": [
                    {
                        "text": "Python 3.12 shipped in 2023.",
                        "confidence": 0.95,
                        "citations": [{"url": "https://x", "title": "", "date": "", "author": "", "excerpt": ""}],
                    }
                ]
            }
        ).decode()
        outcome = repair_json(payload)
        assert outcome.repair_tier == TIER_EXACT

    def test_fence_with_prose_and_trailing_comma(self):
        payload = '```json\nBased on the document:\n{"claims": [{"text": "a", "confidence": 0.9,},]}\n```'
        outcome = repair_json(payload)
        assert outcome.repair_tier == TIER_STRUCTURAL_REPAIR
        assert outcome.parsed["claims"][0]["text"] == "a"

    def test_truncated_realistic_payload_recovers_partial(self):
        payload = (
            '{"claims": ['
            '{"text": "Claim one is supported.", "confidence": 0.9, "citations": [{"url": "https://a"}]}, '
            '{"text": "Claim two is supported.", "confidence": 0.8, "citations": [{"url": "https://b"}]}, '
            '{"text": "Claim three is cut o'
        )
        outcome = repair_json(payload)
        assert outcome.salvaged is True
        texts = {c.get("text") for c in outcome.parsed["claims"]}
        assert "Claim one is supported." in texts
        assert "Claim two is supported." in texts

    def test_max_tokens_truncation_midway_through_text(self):
        """The exact failure shape that used to silently destroy evidence."""
        payload = '{"claims": [{"text": "The company reported record revenue of $4.2 billion in Q3, driv'
        outcome = repair_json(payload)
        assert outcome.repair_tier == TIER_SPAN_SALVAGE
        assert outcome.salvaged is True
        assert outcome.recovered_count == 1
        assert "4.2 billion" in outcome.parsed["claims"][0]["text"]
