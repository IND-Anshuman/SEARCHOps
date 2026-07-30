"""
ResearchContext — carries research-session-specific metadata.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from searchops.core.context.execution import ExecutionContext
from searchops.typing.aliases import ResearchId


class ResearchDepth(enum.StrEnum):
    """Research depth determines how many search+scrape iterations are performed."""
    SHALLOW = "shallow"     # 1 search iteration, no deep scraping
    STANDARD = "standard"   # 3 iterations, moderate scraping
    DEEP = "deep"           # 5+ iterations, full scraping pipeline


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
