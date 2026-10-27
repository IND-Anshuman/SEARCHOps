"""Tests for claim-level deduplication and sampled verification.

Steps 7 and 8 are the claim-level counterparts to the page-level funnel.

**Step 7 — claim dedup.** At corpus scale the same fact appears on dozens of
pages, so verification would pay to check identical text over and over.
Deduplication is *free* (no LLM), so collapsing near-identical claims before
verification is the cheapest possible saving.

Contract pinned:
* Near-identical claim text collapses to one survivor.
* Genuinely different claims survive.
* The survivor keeps its citations, so evidence is not lost by merging.
* Aggressiveness is a parameter: corpus mode collapses more than precision.

**Step 8 — sampled verification.** Verifying *every* claim with an agent is
the budget trap. Instead a deterministic sample is verified, plus every
high-stakes claim, and the rest stay ``unverified`` — and the report says how
many were verified versus sampled. A sampled subset is **never** presented as
fully verified.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from searchops.orchestration.evidence import Citation, Claim
from searchops.orchestration.nodes.claim_dedup import dedupe_semantically
from searchops.orchestration.nodes.verifier import (
    ClaimVerdict,
    SamplingPlan,
    select_claims_to_verify,
)

OFFSET = 0


def _claim(cid: str, text: str, **kwargs: object) -> Claim:
    kwargs.setdefault("citations", [Citation(url=f"https://a.com/{cid}")])
    return Claim(id=cid, text=text, **kwargs)  # type: ignore[arg-type]


# ── Step 7: claim-level dedup ───────────────────────────────────────────────


class TestClaimDedup:
    def test_identical_claims_collapse(self) -> None:
        claims = [
            _claim("c1", "HNSW builds a navigable small-world graph over embeddings."),
            _claim("c2", "HNSW builds a navigable small-world graph over embeddings."),
        ]
        result = dedupe_semantically(claims)
        assert len(result) == 1

    def test_near_identical_claims_collapse(self) -> None:
        """Same fact, trivially reworded — must not be verified twice."""
        claims = [
            _claim("c1", "HNSW builds a navigable small-world graph over the embeddings."),
            _claim("c2", "HNSW builds a navigable small-world graph over embeddings"),
        ]
        result = dedupe_semantically(claims)
        assert len(result) == 1

    def test_distinct_claims_survive(self) -> None:
        claims = [
            _claim("c1", "HNSW builds a navigable small-world graph over embeddings."),
            _claim("c2", "Qdrant supports filtered vector search with payload indexes."),
            _claim("c3", "Milvus is written in Go and targets large-scale deployments."),
        ]
        result = dedupe_semantically(claims)
        assert len(result) == 3

    def test_survivor_keeps_merged_citations(self) -> None:
        """Merging must not lose evidence from the duplicates."""
        claims = [
            Claim(
                id="c1",
                text="HNSW builds a navigable small-world graph over embeddings.",
                citations=[Citation(url="https://a.com/1")],
            ),
            Claim(
                id="c2",
                text="HNSW builds a navigable small-world graph over embeddings.",
                citations=[Citation(url="https://b.com/2")],
            ),
        ]
        result = dedupe_semantically(claims)
        assert len(result) == 1
        urls = {c.url for c in result[0].citations}
        assert urls == {"https://a.com/1", "https://b.com/2"}

    def test_citations_are_not_duplicated(self) -> None:
        shared = Citation(url="https://a.com/same")
        claims = [
            Claim(id="c1", text="HNSW builds a navigable small-world graph over embeddings.", citations=[shared]),
            Claim(id="c2", text="HNSW builds a navigable small-world graph over embeddings.", citations=[shared]),
        ]
        result = dedupe_semantically(claims)
        assert len(result[0].citations) == 1

    def test_empty_input(self) -> None:
        assert dedupe_semantically([]) == []

    def test_single_claim_is_unchanged(self) -> None:
        claims = [_claim("c1", "A single claim stands alone.")]
        result = dedupe_semantically(claims)
        assert result == claims

    def test_first_occurrence_is_the_survivor(self) -> None:
        claims = [
            _claim("c1", "HNSW builds a navigable small-world graph over embeddings."),
            _claim("c2", "HNSW builds a navigable small-world graph over embeddings."),
        ]
        assert dedupe_semantically(claims)[0].id == "c1"

    def test_aggressive_mode_collapses_more(self) -> None:
        """Corpus mode treats looser paraphrases as duplicates."""
        claims = [
            _claim("c1", "Vector databases store embeddings and support fast search."),
            _claim("c2", "Databases store embeddings and support fast search quickly."),
        ]
        light = dedupe_semantically(claims, aggressive=False)
        aggressive = dedupe_semantically(claims, aggressive=True)
        assert len(aggressive) <= len(light)

    def test_merges_are_reported(self) -> None:
        claims = [
            _claim("c1", "HNSW builds a navigable small-world graph over embeddings."),
            _claim("c2", "HNSW builds a navigable small-world graph over embeddings."),
            _claim("c3", "Qdrant supports filtered vector search with payload indexes."),
        ]
        kept, merged = dedupe_semantically(claims, return_merged=True)
        assert len(kept) == 2
        assert merged == ["c2"]

    def test_input_is_not_mutated(self) -> None:
        claims = [
            _claim("c1", "HNSW builds a navigable small-world graph over embeddings."),
            _claim("c2", "HNSW builds a navigable small-world graph over embeddings."),
        ]
        before = [c.id for c in claims]
        dedupe_semantically(claims)
        assert [c.id for c in claims] == before


# ── Step 8: sampled verification ────────────────────────────────────────────


class TestSelectClaimsToVerify:
    def _claims(self, n: int) -> list[Claim]:
        return [_claim(f"c{i}", f"Claim number {i} about vector databases and search.") for i in range(n)]

    def test_full_rate_verifies_everything(self) -> None:
        claims = self._claims(10)
        selected = select_claims_to_verify(claims, SamplingPlan(rate=1.0))
        assert len(selected) == 10

    def test_zero_rate_verifies_only_high_stakes(self) -> None:
        claims = self._claims(10)
        claims[3] = claims[3].model_copy(update={"status": "unverified", "confidence": 0.9})
        selected = select_claims_to_verify(claims, SamplingPlan(rate=0.0))
        # Only the high-confidence (high-stakes) claim survives at rate 0.
        assert [c.id for c in selected] == ["c3"]

    def test_sampling_is_deterministic(self) -> None:
        claims = self._claims(50)
        a = [c.id for c in select_claims_to_verify(claims, SamplingPlan(rate=0.25))]
        b = [c.id for c in select_claims_to_verify(claims, SamplingPlan(rate=0.25))]
        assert a == b

    def test_sampling_is_strided_not_clustered(self) -> None:
        """Every Nth claim, so the sample spans the list rather than its head."""
        claims = self._claims(20)
        selected = select_claims_to_verify(claims, SamplingPlan(rate=0.25))
        assert len(selected) == 5
        assert selected[0].id == "c0"
        assert selected[1].id == "c4"

    def test_high_stakes_always_included(self) -> None:
        claims = self._claims(20)
        claims[17] = claims[17].model_copy(update={"confidence": 0.99})
        selected = select_claims_to_verify(claims, SamplingPlan(rate=0.1))
        assert any(c.id == "c17" for c in selected)

    def test_low_confidence_is_high_stakes(self) -> None:
        """A very low confidence claim is high-stakes: it may be wrong."""
        claims = self._claims(20)
        claims[2] = claims[2].model_copy(update={"confidence": 0.05})
        selected = select_claims_to_verify(claims, SamplingPlan(rate=0.1))
        assert any(c.id == "c2" for c in selected)

    def test_no_duplicates_in_selection(self) -> None:
        claims = self._claims(20)
        for i in range(20):
            claims[i] = claims[i].model_copy(update={"confidence": 0.95})
        selected = select_claims_to_verify(claims, SamplingPlan(rate=0.5))
        ids = [c.id for c in selected]
        assert len(ids) == len(set(ids))

    def test_already_verified_are_skipped(self) -> None:
        claims = self._claims(10)
        claims[0] = claims[0].model_copy(update={"status": "supported"})
        claims[1] = claims[1].model_copy(update={"status": "refuted"})
        selected = select_claims_to_verify(claims, SamplingPlan(rate=1.0))
        assert all(c.status == "unverified" for c in selected)
        assert len(selected) == 8

    def test_empty_claims(self) -> None:
        assert select_claims_to_verify([], SamplingPlan(rate=0.5)) == []

    def test_selection_never_exceeds_input(self) -> None:
        claims = self._claims(7)
        selected = select_claims_to_verify(claims, SamplingPlan(rate=1.0))
        assert len(selected) <= len(claims)

    def test_out_of_range_rate_is_rejected(self) -> None:
        """A bad rate must fail loudly, not silently clamp.

        ``plan_from_scrape_plan`` catches this and falls back to exhaustive
        verification, so a bad stored value degrades safely.
        """
        claims = self._claims(10)
        with pytest.raises(ValueError):
            select_claims_to_verify(claims, SamplingPlan(rate=5.0))
        with pytest.raises(ValueError):
            select_claims_to_verify(claims, SamplingPlan(rate=-1.0))


class TestSamplingPlan:
    def test_precision_verifies_everything(self) -> None:
        from searchops.orchestration.nodes.verifier import plan_from_scrape_plan

        plan = plan_from_scrape_plan({"mode": "precision", "verify_sampling_rate": 1.0})
        assert plan.rate == 1.0

    def test_corpus_samples(self) -> None:
        from searchops.orchestration.nodes.verifier import plan_from_scrape_plan

        plan = plan_from_scrape_plan({"mode": "corpus", "verify_sampling_rate": 0.25})
        assert plan.rate == 0.25

    def test_missing_plan_defaults_to_full_verification(self) -> None:
        from searchops.orchestration.nodes.verifier import plan_from_scrape_plan

        assert plan_from_scrape_plan(None).rate == 1.0

    def test_describes_itself_honestly(self) -> None:
        plan = SamplingPlan(rate=0.25)
        assert "sampled" in plan.describe().lower()


# ── Wiring: dedup in extract_claims, sampling in verifier ───────────────────


class TestDedupWiring:
    """Claim dedup runs inside extract_evidence and respects the mode."""

    @pytest.mark.asyncio
    async def test_duplicate_claims_are_collapsed_before_verification(self):
        from unittest.mock import AsyncMock

        from searchops.orchestration.nodes.extract_evidence import extract_evidence_node

        router = AsyncMock()

        async def _fake_extract(_router: object, doc: dict[str, object], *_: object) -> tuple[list[Claim], list, list]:
            return (
                [
                    _claim(f"{doc['url']}-{i}", "HNSW builds a navigable small-world graph over embeddings.")
                    for i in range(2)
                ],
                [],
                [],
            )

        with patch(
            "searchops.orchestration.nodes.extract_evidence._extract_from_doc",
            side_effect=_fake_extract,
        ):
            state = {
                "round_documents": [
                    {"url": "https://a.com/1", "content": "text", "title": "t"},
                    {"url": "https://a.com/2", "content": "text", "title": "t"},
                ],
                "scrape_plan": {"mode": "corpus", "claim_dedup_aggressive": True},
            }
            result = await extract_evidence_node(state, llm_router=router)  # type: ignore[arg-type]

        # 4 extracted claims, all the same fact -> 1 survivor.
        assert len(result["evidence_claims"]) == 1
        # Evidence from every duplicate is retained.
        assert len(result["evidence_claims"][0].citations) == 4


class TestSamplingWiring:
    """The verifier samples in corpus mode and reports what it did."""

    @pytest.mark.asyncio
    async def test_corpus_mode_reports_sampling(self) -> None:
        from unittest.mock import AsyncMock

        from searchops.orchestration.nodes.verifier import verifier_node

        claims = [_claim(f"c{i}", f"Claim {i} about vector databases and search engines.") for i in range(20)]
        router = AsyncMock()

        with patch(
            "searchops.orchestration.nodes.verifier._verify_batch",
            new=AsyncMock(return_value={"c0": ClaimVerdict(status="supported")}),
        ):
            result = await verifier_node(
                {
                    "evidence_claims": claims,
                    "scrape_plan": {"mode": "corpus", "verify_sampling_rate": 0.25},
                },
                llm_router=router,
            )

        stats = result["verification_stats"]
        assert stats["sampling_rate"] == 0.25
        # A sample, never presented as complete.
        assert stats["unverified"] > 0
        assert "sampled" in stats["note"].lower()

    @pytest.mark.asyncio
    async def test_precision_mode_verifies_everything(self) -> None:
        from unittest.mock import AsyncMock

        from searchops.orchestration.nodes.verifier import verifier_node

        claims = [_claim(f"c{i}", f"Claim {i} about vector databases.") for i in range(5)]
        router = AsyncMock()

        with patch(
            "searchops.orchestration.nodes.verifier._verify_batch",
            new=AsyncMock(return_value={c.id: ClaimVerdict(status="supported") for c in claims}),
        ):
            result = await verifier_node(
                {"evidence_claims": claims, "scrape_plan": {"mode": "precision"}},
                llm_router=router,
            )

        stats = result["verification_stats"]
        assert stats["unverified"] == 0
        assert stats["note"] == "All claims verified."
