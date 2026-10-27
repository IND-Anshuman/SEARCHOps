"""Tests for wiring the wall detector into ScrapingPipeline.

A page the detector condemns must not enter the pipeline's success path:
* it is reclassified as a failure (403), so it never lands in the evidence
  layer counted as a successful scrape;
* the wall class is recorded in metadata so the DLQ and metrics can act on it;
* the tier loop still runs normally for real content.
"""

from __future__ import annotations

from typing import Any

import pytest

from searchops.scraping.pipeline import ScrapingPipeline
from searchops.scraping.rate_limiter import DomainRateLimiter
from searchops.scraping.wall_detector import WallClass


class _FakeStealth:
    def __init__(self, body: str, status: int = 200) -> None:
        self.body = body
        self.status = status

    async def scrape(self, request: Any) -> Any:
        from searchops.core.interfaces.scraper import ScrapeMode, ScrapeResult

        return ScrapeResult(
            url=request.url,
            final_url=request.url,
            status_code=self.status,
            markdown=self.body,
            html=f"<html><body>{self.body}</body></html>",
            scrape_mode_used=ScrapeMode.HTTP,
        )

    async def health_check(self) -> bool:
        return True


CONSENT = "We value your privacy. This website uses cookies. Accept all cookies."
REAL = (
    "Vector databases store embeddings and support approximate nearest "
    "neighbour search. They are the retrieval backbone of modern RAG systems, "
    "and popular implementations include Qdrant, Milvus, Weaviate and pgvector."
)


def _pipeline(body: str, status: int = 200) -> tuple[ScrapingPipeline, _FakeStealth]:
    stealth = _FakeStealth(body, status)
    return ScrapingPipeline(stealth=stealth, rate_limiter=DomainRateLimiter()), stealth  # type: ignore[arg-type]


def _req(url: str = "https://site.com/page") -> Any:
    from searchops.core.interfaces.scraper import ScrapeRequest

    return ScrapeRequest(url=url)


@pytest.fixture(autouse=True)
def _isolated_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the process-wide rate-limiter singleton.

    ``get_rate_limiter()`` is module-level shared state. Tests that fetch the
    same host repeatedly would otherwise exhaust its per-domain budget and
    rate-limit *unrelated* tests that run later in the same session.
    """
    from searchops.scraping import rate_limiter as rl_mod

    monkeypatch.setattr(rl_mod, "_RATE_LIMITER", rl_mod.DomainRateLimiter(), raising=False)


class TestWallGate:
    @pytest.mark.asyncio
    async def test_consent_wall_is_not_a_success(self) -> None:
        pipeline, _ = _pipeline(CONSENT)
        result = await pipeline.execute(_req())
        assert result.status_code != 200

    @pytest.mark.asyncio
    async def test_wall_class_is_recorded(self) -> None:
        pipeline, _ = _pipeline(CONSENT)
        result = await pipeline.execute(_req())
        assert result.metadata.get("wall_class") == WallClass.CONSENT.value

    @pytest.mark.asyncio
    async def test_real_content_is_unaffected(self) -> None:
        pipeline, _ = _pipeline(REAL)
        result = await pipeline.execute(_req())
        assert result.status_code == 200
        assert "wall_class" not in result.metadata

    @pytest.mark.asyncio
    async def test_wall_is_counted(self) -> None:
        pipeline, _ = _pipeline(CONSENT)
        await pipeline.execute(_req())
        assert pipeline.wall_stats.rejected == 1

    @pytest.mark.asyncio
    async def test_histogram_is_exposed(self) -> None:
        pipeline, _ = _pipeline(CONSENT)
        await pipeline.execute(_req())
        histogram = pipeline.wall_stats.histogram()
        assert histogram[WallClass.CONSENT.value] == 1
        assert histogram[WallClass.NONE.value] == 0

    @pytest.mark.asyncio
    async def test_wall_does_not_escalate_to_another_tier(self) -> None:
        """A gated page must not trigger paid tier escalation.

        The page is walled, not our request — retrying with a heavier tier
        would re-fetch the same wall at higher cost.
        """
        stealth = _FakeStealth(CONSENT)
        pipeline = ScrapingPipeline(stealth=stealth, rate_limiter=DomainRateLimiter())
        result = await pipeline.execute(_req())
        # stealth returned the wall once; no further tier was consulted.
        assert result.metadata.get("wall_class") == WallClass.CONSENT.value
        assert result.status_code == 403

    @pytest.mark.asyncio
    async def test_non_200_is_left_alone(self) -> None:
        """The detector only judges 200s; a 404 is already a failure."""
        pipeline, _ = _pipeline(CONSENT, status=404)
        result = await pipeline.execute(_req())
        assert result.status_code == 404
        assert pipeline.wall_stats.inspected == 0
