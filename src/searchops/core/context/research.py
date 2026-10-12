"""
ResearchContext — carries research-session-specific metadata and domain value objects.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from searchops.core.context.execution import ExecutionContext
    from searchops.typing.aliases import ResearchId


class ResearchDepth(enum.StrEnum):
    """Research depth determines how many search+scrape iterations are performed."""

    SHALLOW = "shallow"  # 1 search iteration, no deep scraping
    STANDARD = "standard"  # 3 iterations, moderate scraping
    DEEP = "deep"  # 5+ iterations, full scraping pipeline


class ResearchMode(StrEnum):
    """The shape a research run takes.

    The same graph answers two different kinds of question:

    * ``PRECISION`` — answer one question well. A handful of high-relevance
      pages, deep extraction, exhaustive verification.
    * ``CORPUS`` — build or refresh a dataset. Many pages, shallow extraction,
      the deterministic filters on, verification sampled not exhaustive.

    Mode is always explicit on the request and never inferred: a guessed mode
    that picks wrong is worse than an explicit one that is set.
    """

    PRECISION = "precision"
    CORPUS = "corpus"


class ScrapePlan(BaseModel):
    """Per-mode parameters consumed by the research graph.

    Fields map to funnel stages from the two-mode plan:

    * ``max_pages`` / ``max_depth`` → frontier breadth and depth (Step 4)
    * ``relevance_filter`` → deterministic volume cut (Step 5)
    * ``extract_schema`` → schema-driven extraction (Step 6)
    * ``claim_dedup_aggressive`` → claim-level dedup (Step 7)
    * ``verify_sampling_rate`` → sampled verification (Step 8)
    * ``report_shape`` → narrative answer vs structured dataset (Step 8)

    Only fields a node reads today are given non-default values; the rest are
    declared so later steps have a named home without building speculative
    machinery ahead of its consumer.
    """

    mode: ResearchMode = ResearchMode.PRECISION

    # Stage 1/4 — fetch breadth and depth.
    max_pages: int = Field(default=10, ge=1, le=200)
    max_depth: int = Field(default=1, ge=1, le=10)

    # Stage 4 — deterministic relevance filter.
    relevance_filter: bool = False

    # Stage 6 — schema-driven extraction.
    extract_schema: dict[str, object] | None = None

    # Stage 7 — claim dedup and verification.
    claim_dedup_aggressive: bool = False
    verify_sampling_rate: float = Field(default=1.0, ge=0.0, le=1.0)

    # Stage 8 — report shape.
    report_shape: str = "narrative"

    @classmethod
    def for_mode(
        cls,
        mode: ResearchMode | str,
        *,
        max_sources: int | None = None,
    ) -> ScrapePlan:
        """Build the plan for *mode*, optionally overriding page breadth.

        ``max_sources`` is the API's existing knob; when given it wins over the
        mode default so a caller can tune breadth without changing mode.
        """
        resolved = mode if isinstance(mode, ResearchMode) else ResearchMode(mode)

        if resolved is ResearchMode.CORPUS:
            return cls(
                mode=resolved,
                max_pages=max_sources if max_sources is not None else 50,
                max_depth=3,
                relevance_filter=True,
                claim_dedup_aggressive=True,
                verify_sampling_rate=0.25,
                report_shape="dataset",
            )
        return cls(
            mode=resolved,
            max_pages=max_sources if max_sources is not None else 10,
            max_depth=1,
            relevance_filter=False,
            claim_dedup_aggressive=False,
            verify_sampling_rate=1.0,
            report_shape="narrative",
        )


class ResearchConfig(BaseModel):
    """User-tunable research configuration, with hard bounds on every knob.

    Every field a user can turn is a field they can break, so each one carries
    an absolute ceiling enforced by the schema. A caller cannot widen a bound
    by asking for a bigger number — the request is rejected rather than
    silently truncated.

    The bounds exist for two reasons:

    * **Cost.** Pages fetched and claims verified both spend money. An
      unbounded ``max_pages`` is an invoice, not a feature.
    * **Honesty.** A run asking for 200 pages on a 10-scrape budget is a
      mistake. It is refused with a reason rather than quietly doing less than
      the user asked for and looking like it succeeded.

    ``validate_against_budget`` is the cross-field check: the schema guards
    each knob individually, this guards them against each other.
    """

    # ── Crawl breadth and depth ─────────────────────────────────────────────
    max_pages: int = Field(default=10, ge=1, le=200, description="Hard cap on pages fetched for this run.")
    max_depth: int = Field(default=1, ge=1, le=10, description="Link depth to follow. 1 = seeds only.")

    # ── Content budget ──────────────────────────────────────────────────────
    max_content_chars: int = Field(
        default=200_000,
        ge=1_000,
        le=2_000_000,
        description="Total characters of page content to retain across the run.",
    )

    # ── Verification ────────────────────────────────────────────────────────
    verify_sampling_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Fraction of claims sent to the verifier. Below 1.0 means partial verification.",
    )

    # ── Report shape ────────────────────────────────────────────────────────
    max_report_sections: int = Field(default=12, ge=1, le=60, description="Maximum sections in the generated report.")

    # ── Budget the run is allowed to spend ──────────────────────────────────
    # Derived from max_pages by default so a partial config stays coherent:
    # asking for 25 pages without touching this gets a 25-scrape budget, not a
    # 10-scrape one that would be rejected as inconsistent.
    scrape_budget: int = Field(
        default=0, ge=0, le=200, description="Scrapes this run may consume. 0 = follow max_pages."
    )

    # Rough per-unit costs, used only to produce a labelled ESTIMATE. These are
    # never presented to the user as measured figures.
    _COST_PER_PAGE_USD = 0.002
    _COST_PER_DEPTH_USD = 0.001

    @model_validator(mode="after")
    def _default_scrape_budget_follows_max_pages(self) -> ResearchConfig:
        if self.scrape_budget == 0:
            # Bypass validation on assignment; the value is already bounded by
            # max_pages' own ceiling.
            object.__setattr__(self, "scrape_budget", min(self.max_pages, 200))
        return self

    def is_partial_verification(self) -> bool:
        """True when not every claim will be verified.

        A partially verified report must say so — the trust header reads this
        to tell the user which claims were checked.
        """
        return self.verify_sampling_rate < 1.0

    def validate_against_budget(self) -> list[str]:
        """Cross-field checks. Returns problems, empty when the plan is sound.

        Each message is written for a user to read, not for a log.
        """
        problems: list[str] = []
        if self.max_pages > self.scrape_budget:
            problems.append(
                f"Asking for up to {self.max_pages} pages but the scrape budget only "
                f"allows {self.scrape_budget}. Lower max_pages or raise scrape_budget."
            )
        return problems

    def estimate_cost(self) -> float:
        """Rough cost estimate in USD.

        Derived from page count and crawl depth. It is an estimate and the UI
        labels it as one — see ``estimate_cost_note``.
        """
        per_page = self._COST_PER_PAGE_USD + (self.max_depth - 1) * self._COST_PER_DEPTH_USD
        return round(self.max_pages * per_page, 4)

    def estimate_cost_note(self) -> str:
        return f"Estimated cost ${self.estimate_cost():.4f} (estimate, not a measured figure)"


class ResearchPlan(BaseModel):
    """Domain model representing a structured research plan emitted by the Planner."""

    primary_query: str
    sub_queries: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    priority_order: list[int] = Field(default_factory=list)
    search_budget: int = 5
    confidence: float = 1.0


class ExecutionBudget(BaseModel):
    """Domain model tracking budget allocations per research run."""

    remaining_searches: int = 10
    remaining_tokens: int = 100_000
    remaining_scrapes: int = 10
    remaining_cost_usd: float = 1.00

    def consume_search(self) -> None:
        if self.remaining_searches > 0:
            self.remaining_searches -= 1

    def consume_scrape(self) -> None:
        if self.remaining_scrapes > 0:
            self.remaining_scrapes -= 1


class SearchExecution(BaseModel):
    """Domain metadata recorded per search query execution."""

    query: str
    provider: str
    latency_ms: float
    cost_usd: float
    result_count: int
    cache_hit: bool = False
    confidence: float = 1.0


@dataclass(slots=True)
class ResearchContext:
    """Context for a single research session.

    Wraps ExecutionContext with research-specific state:
    - research_id: Unique session identifier
    - query: The primary research query
    - depth: How deep to research
    - sources_visited: URLs already scraped (for deduplication)
    - domains_blocked: Domains to avoid
    """

    execution_context: ExecutionContext
    research_id: ResearchId
    query: str
    depth: ResearchDepth = ResearchDepth.STANDARD
    sources_visited: set[str] = field(default_factory=set)
    domains_blocked: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)

    def mark_visited(self, url: str) -> None:
        """Record that a URL has been scraped."""
        self.sources_visited.add(url)

    def is_visited(self, url: str) -> bool:
        """Return True if the URL has already been scraped."""
        return url in self.sources_visited

    def is_domain_blocked(self, domain: str) -> bool:
        """Return True if the domain is on the blocklist."""
        return domain in self.domains_blocked
