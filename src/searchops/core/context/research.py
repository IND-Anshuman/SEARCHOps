"""
ResearchContext — carries research-session-specific metadata and domain value objects.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any
from pydantic import BaseModel, Field

from searchops.core.context.execution import ExecutionContext
from searchops.typing.aliases import ResearchId


class ResearchDepth(enum.StrEnum):
    """Research depth determines how many search+scrape iterations are performed."""
    SHALLOW = "shallow"     # 1 search iteration, no deep scraping
    STANDARD = "standard"   # 3 iterations, moderate scraping
    DEEP = "deep"           # 5+ iterations, full scraping pipeline


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

