"""Tests for the mode-aware scrape node.

The contract these tests pin — and the whole point of Step 4:

* **Precision mode is unchanged.** A run with no plan (or ``max_depth=1``)
  scrapes exactly the URLs it was given and never follows a link. The
  free-tier 3-URL cap still applies.
* **Corpus mode crawls.** With ``max_depth > 1`` the node follows in-scope
  links from each fetched page, up to the plan's ``max_pages``.
* **The visited set is shared**, so a URL the frontier already took is never
  fetched twice even across the loop.
* **Failures never abort the crawl** — a page that 404s is recorded and the
  frontier continues, so one dead link cannot truncate a corpus.
* **The plan is read from state**, so the node stays a pure function of its
  inputs and is testable without a graph.
"""

from __future__ import annotations

from typing import Any

import pytest

from searchops.orchestration.nodes.scrape import scrape_node

# A page body long enough to be real content, with two links.
_BODY = (
    "Vector databases store embeddings and support approximate nearest "
    "neighbour search, forming the retrieval backbone of modern RAG systems."
)


class _FakeResult:
    def __init__(
        self,
        url: str,
        status: int = 200,
        body: str = _BODY,
        links: list[str] | None = None,
    ) -> None:
        self.url = url
        self.status_code = status
        # A real 404 page still renders HTML with links, so the body is kept
        # for every status — only markdown is cleared for failures.
        self.markdown = body if status == 200 else None
        self.html = f"<html><body>{body}</body></html>"
        self.title = "Doc"
        self.links = links or []
        self.metadata: dict[str, Any] = {}


class _RecordingPipeline:
    """Pipeline double that records every URL it was asked to fetch."""

    def __init__(self, *, bodies: dict[str, str] | None = None, fail: set[str] | None = None) -> None:
        self.fetched: list[str] = []
        self._bodies = bodies or {}
        self._fail = fail or set()

    async def execute(self, request: Any) -> _FakeResult:
        self.fetched.append(request.url)
        if request.url in self._fail:
            # A real 404 still renders its page body (with links) — only the
            # status differs, so the frontier can still follow its children.
            return _FakeResult(request.url, status=404, body=self._bodies.get(request.url, _BODY))
        body = self._bodies.get(request.url, _BODY)
        return _FakeResult(request.url, status=200, body=body)


def _precision_state(urls: list[str]) -> dict[str, Any]:
    return {"urls_to_scrape": urls}


def _corpus_state(urls: list[str], **plan: Any) -> dict[str, Any]:
    base = {
        "urls_to_scrape": urls,
        "scrape_plan": {
            "mode": "corpus",
            "max_pages": plan.pop("max_pages", 20),
            "max_depth": plan.pop("max_depth", 2),
            **plan,
        },
    }
    return base


class TestPrecisionModeIsUnchanged:
    @pytest.mark.asyncio
    async def test_scrapes_exactly_the_given_urls(self) -> None:
        pipeline = _RecordingPipeline()
        state = _precision_state(["https://a.com/1", "https://a.com/2"])

        result = await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert pipeline.fetched == ["https://a.com/1", "https://a.com/2"]
        assert len(result["scraped_contents"]) == 2

    @pytest.mark.asyncio
    async def test_does_not_follow_links(self) -> None:
        """No plan means no crawl — precision behaviour is preserved exactly."""
        pipeline = _RecordingPipeline(bodies={"https://a.com/1": _BODY + '<a href="/2">next</a>'})
        state = _precision_state(["https://a.com/1"])

        await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert pipeline.fetched == ["https://a.com/1"]

    @pytest.mark.asyncio
    async def test_depth_one_does_not_crawl(self) -> None:
        pipeline = _RecordingPipeline(bodies={"https://a.com/1": _BODY + '<a href="/2">next</a>'})
        state = _corpus_state(["https://a.com/1"], max_depth=1)

        await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert pipeline.fetched == ["https://a.com/1"]

    @pytest.mark.asyncio
    async def test_free_tier_cap_still_applies(self) -> None:
        """The 3-URL free-tier cap is not relaxed by adding a plan."""
        pipeline = _RecordingPipeline()
        state = _precision_state([f"https://a.com/{i}" for i in range(10)])

        await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert len(pipeline.fetched) == 3


class TestCorpusModeCrawls:
    @pytest.mark.asyncio
    async def test_follows_links_from_fetched_pages(self) -> None:
        pipeline = _RecordingPipeline(
            bodies={
                "https://a.com/1": _BODY + '<a href="/2">next</a>',
                "https://a.com/2": _BODY + '<a href="/3">next</a>',
            }
        )
        state = _corpus_state(["https://a.com/1"], max_depth=2, max_pages=10)

        result = await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert "https://a.com/2" in pipeline.fetched
        assert len(result["scraped_contents"]) >= 2

    @pytest.mark.asyncio
    async def test_respects_max_pages(self) -> None:
        bodies = {f"https://a.com/{i}": _BODY + f'<a href="/{i + 1}">n</a>' for i in range(1, 30)}
        pipeline = _RecordingPipeline(bodies=bodies)
        state = _corpus_state(["https://a.com/1"], max_depth=5, max_pages=4)

        await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert len(pipeline.fetched) == 4

    @pytest.mark.asyncio
    async def test_does_not_fetch_the_same_url_twice(self) -> None:
        pipeline = _RecordingPipeline(
            bodies={
                "https://a.com/1": _BODY + '<a href="/2">b</a><a href="/1">self</a>',
                "https://a.com/2": _BODY + '<a href="/1">back</a>',
            }
        )
        state = _corpus_state(["https://a.com/1"], max_depth=3, max_pages=20)

        await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert len(pipeline.fetched) == len(set(pipeline.fetched))

    @pytest.mark.asyncio
    async def test_stays_on_the_seed_host(self) -> None:
        pipeline = _RecordingPipeline(
            bodies={"https://a.com/1": _BODY + '<a href="https://b.com/x">x</a><a href="/2">y</a>'}
        )
        state = _corpus_state(["https://a.com/1"], max_depth=2, max_pages=10)

        await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert all(u.startswith("https://a.com") for u in pipeline.fetched)

    @pytest.mark.asyncio
    async def test_crawl_continues_past_a_failed_page(self) -> None:
        """One dead link must not truncate the corpus."""
        pipeline = _RecordingPipeline(
            bodies={
                "https://a.com/1": _BODY + '<a href="/2">b</a>',
                "https://a.com/2": _BODY + '<a href="/3">c</a>',
            },
            fail={"https://a.com/2"},
        )
        state = _corpus_state(["https://a.com/1"], max_depth=3, max_pages=10)

        result = await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert "https://a.com/2" in result["failed_urls"]
        assert "https://a.com/3" in pipeline.fetched

    @pytest.mark.asyncio
    async def test_reports_frontier_stats(self) -> None:
        pipeline = _RecordingPipeline(bodies={"https://a.com/1": _BODY + '<a href="/2">b</a>'})
        state = _corpus_state(["https://a.com/1"], max_depth=2, max_pages=10)

        result = await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        stats = result["frontier_stats"]
        assert stats["pages_fetched"] == 2
        assert stats["links_seen"] >= 1


class TestNearDuplicateDropping:
    """Near-duplicate pages are dropped before extraction ever sees them."""

    # Long enough to clear the node's _MIN_DEDUP_CHARS fingerprint floor.
    _LONG = (
        "Retrieval augmented generation couples a vector store with a language model so answers "
        "are grounded in retrieved passages rather than parametric memory alone. The retriever "
        "embeds the query, finds nearest-neighbour chunks, and the model conditions on them. "
    ) * 2

    @pytest.mark.asyncio
    async def test_identical_pages_are_deduplicated(self) -> None:
        pipeline = _RecordingPipeline(
            bodies={"https://a.com/1": self._LONG, "https://mirror.com/1": self._LONG}
        )
        state = _precision_state(["https://a.com/1", "https://mirror.com/1"])

        result = await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        # Both were fetched, but the mirror carries identical text, so only one
        # survives into the documents the extractor will read.
        assert len(pipeline.fetched) == 2
        assert len(result["round_documents"]) == 1
        assert result["frontier_stats"]["duplicates_dropped"] == 1

    @pytest.mark.asyncio
    async def test_distinct_pages_are_all_kept(self) -> None:
        pipeline = _RecordingPipeline(
            bodies={
                "https://a.com/1": self._LONG + " Vector indexes use HNSW graphs.",
                "https://b.com/1": "An entirely unrelated essay about the history of cartography in Europe. " * 4,
            }
        )
        state = _precision_state(["https://a.com/1", "https://b.com/1"])

        result = await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert len(result["round_documents"]) == 2
        assert result["frontier_stats"]["duplicates_dropped"] == 0


class TestRelevanceFilterWiring:
    """Stage 4 runs in corpus mode only, and never in precision mode."""

    @pytest.mark.asyncio
    async def test_precision_mode_does_not_filter(self) -> None:
        """Precision fetches few pages that are already relevant — no filter."""
        pipeline = _RecordingPipeline()
        state = _precision_state(["https://a.com/1"])
        state["query"] = "vector databases"

        result = await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert result["relevance_stats"]["degraded"] == "filter_off"

    @pytest.mark.asyncio
    async def test_corpus_mode_filters_off_topic_pages(self) -> None:
        pipeline = _RecordingPipeline(
            bodies={
                "https://a.com/1": _BODY + '<a href="/2">b</a>',
                "https://a.com/2": "The history of the bicycle begins in Europe.",
            }
        )
        state = _corpus_state(["https://a.com/1"], max_depth=2, max_pages=10)
        state["query"] = "vector database approximate nearest neighbour search"

        result = await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        stats = result["relevance_stats"]
        assert stats["pages_dropped"] >= 1
        kept_urls = {p["url"] for p in result["scraped_contents"]}
        assert "https://a.com/2" not in kept_urls

    @pytest.mark.asyncio
    async def test_kept_pages_carry_a_score(self) -> None:
        pipeline = _RecordingPipeline()
        state = _corpus_state(["https://a.com/1"], max_depth=2)
        state["query"] = "vector database approximate nearest neighbour search"

        result = await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        for page in result["scraped_contents"]:
            assert page["relevance"] == "kept"
            assert isinstance(page["relevance_score"], float)

    @pytest.mark.asyncio
    async def test_no_query_means_no_filter(self) -> None:
        """Without a query there is nothing to score against; keep everything."""
        pipeline = _RecordingPipeline()
        state = _corpus_state(["https://a.com/1"], max_depth=2)

        result = await scrape_node(state, pipeline=pipeline)  # type: ignore[arg-type]

        assert result["relevance_stats"]["degraded"] == "filter_off"
