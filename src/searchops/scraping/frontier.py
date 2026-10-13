"""BFS crawl frontier — the queue that turns 3-URL scraping into a crawler.

Why this module exists
----------------------
The research graph scraped a fixed handful of URLs handed to it by search.
That is fine for *precision* mode (answer one question) but cannot build a
*corpus* (a dataset across many pages), which needs the crawler to follow
links itself. This module is that link-following queue.

Design decisions
----------------
* **Pure — no I/O.** The frontier decides *what to fetch next* and never
  fetches, so it is testable without a network and cannot be the reason a
  crawl stalls.
* **The visited set is the existing ``URLSeenSet``.** That set already does
  exact, atomic, restart-surviving de-duplication; a second visited set would
  be a second source of truth.
* **Precision mode is untouched.** ``max_depth=1`` means seeds only — no link
  is ever enqueued — so a precision run behaves exactly as it did before.
* **BFS order under any cap.** Pages are dequeued shallowest-first, so when
  ``max_pages`` truncates the crawl the pages kept are the closest to the
  seeds, which are the most likely to be on-topic.
* **Exclude beats include.** A caller listing what they want must still be
  able to carve out a subtree.
* **Seeds are never filtered.** A URL the caller chose deliberately is always
  crawled; patterns only govern *discovered* links.
"""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse, urlunparse

import structlog

if TYPE_CHECKING:
    from searchops.scraping.url_seen import URLSeenSet

log = structlog.get_logger(__name__)

_HREF_RE = re.compile(r"""<a\s[^>]*href\s*=\s*["']([^"']+)["']""", re.I)
_NON_HTTP_SCHEME = re.compile(r"^(?:mailto|javascript|tel|data|ftp):", re.I)


def normalise_link(href: str, base_url: str) -> str | None:
    """Resolve *href* against *base_url* into an absolute http(s) URL.

    Returns ``None`` for anything that is not a fetchable web link (mail,
    javascript:, empty), so callers can simply skip ``None``.
    """
    if not href or not href.strip():
        return None
    href = href.strip()
    if _NON_HTTP_SCHEME.match(href):
        return None

    absolute = urljoin(base_url, href)
    parts = urlparse(absolute)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None

    # Drop the fragment: /a#x and /a are the same document.
    return urlunparse(parts._replace(fragment=""))


def extract_links(html: str, base_url: str) -> list[str]:
    """Return the unique absolute http(s) links in *html*.

    Order of first appearance is preserved. This is deliberately a simple
    ``<a href>`` scan rather than a full DOM parse: it runs on every fetched
    page in corpus mode and must stay cheap, and it needs no BeautifulSoup
    dependency.
    """
    if not html:
        return []

    seen: set[str] = set()
    links: list[str] = []
    for match in _HREF_RE.finditer(html):
        resolved = normalise_link(match.group(1), base_url)
        if resolved and resolved not in seen:
            seen.add(resolved)
            links.append(resolved)
    return links


@dataclass(frozen=True)
class CrawlPage:
    """A URL ready to be fetched, with the depth it was discovered at."""

    url: str
    depth: int


@dataclass
class CrawlConfig:
    """Scope and budget for one crawl.

    Args:
        max_pages: Hard cap on fetches for this crawl.
        max_depth: Seeds are depth 0; ``max_depth=1`` means seeds only.
        include_patterns: Regexes a *discovered* URL must match (any of).
        exclude_patterns: Regexes that veto a discovered URL (any of).
        same_host: Restrict discovered links to the seed's host.
    """

    max_pages: int = 50
    max_depth: int = 1
    include_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    same_host: bool = True

    include: re.Pattern[str] | None = field(default=None, init=False)
    exclude: re.Pattern[str] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.include = re.compile("|".join(self.include_patterns)) if self.include_patterns else None
        self.exclude = re.compile("|".join(self.exclude_patterns)) if self.exclude_patterns else None

    @property
    def expands(self) -> bool:
        """True when discovered links will be enqueued (i.e. corpus mode)."""
        return self.max_depth > 1

    def in_scope(self, url: str, seed_host: str) -> bool:
        """Whether a *discovered* URL may be crawled."""
        if self.exclude is not None and self.exclude.search(url):
            return False
        if self.include is not None and not self.include.search(url):
            return False
        return not (self.same_host and urlparse(url).netloc != seed_host)

    @classmethod
    def from_scrape_plan(cls, plan: dict[str, Any] | None) -> CrawlConfig:
        """Build a config from a stored ``ScrapePlan`` dict.

        A missing plan (precision, or a caller that predates modes) yields
        ``max_depth=1`` — seeds only — which is exactly the previous
        behaviour.
        """
        if not plan:
            return cls(max_depth=1)
        return cls(
            max_pages=int(plan.get("max_pages", 50)),
            max_depth=int(plan.get("max_depth", 1)),
        )


@dataclass(order=True)
class _Queued:
    """Heap entry: shallowest first, then insertion order for stability."""

    depth: int
    sequence: int
    page: CrawlPage = field(compare=False)


@dataclass
class CrawlStats:
    """Free counters describing what the frontier actually did."""

    links_seen: int = 0
    links_enqueued: int = 0
    out_of_scope: int = 0
    already_seen: int = 0
    pages_fetched: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "links_seen": self.links_seen,
            "links_enqueued": self.links_enqueued,
            "out_of_scope": self.out_of_scope,
            "already_seen": self.already_seen,
            "pages_fetched": self.pages_fetched,
        }


@dataclass
class CrawlFrontier:
    """A breadth-first, depth-limited crawl queue.

    The frontier never performs I/O: ``next_url()`` returns what to fetch and
    ``record_links()`` supplies what was found on it. De-duplication is
    delegated to the injected ``URLSeenSet``.
    """

    seen: URLSeenSet
    config: CrawlConfig = field(default_factory=CrawlConfig)
    stats: CrawlStats = field(default_factory=CrawlStats)
    _queue: list[_Queued] = field(default_factory=list)
    _sequence: int = 0
    _seed_host: str = ""
    _enqueued: set[str] = field(default_factory=set)
    # Depth of each URL once popped, so record_links() can bound expansion
    # without the caller threading depth back through.
    _popped_depth: dict[str, int] = field(default_factory=dict)

    @property
    def size(self) -> int:
        """Number of URLs still waiting to be fetched."""
        return len(self._queue)

    async def add_seeds(self, urls: list[str]) -> None:
        """Queue the starting URLs at depth 0.

        Seeds bypass the include/exclude/host filters — a URL the caller chose
        deliberately is always crawled.
        """
        for url in urls:
            if not url:
                continue
            if url in self._enqueued:
                continue
            if await self.seen.seen(url):
                self.stats.already_seen += 1
                continue
            if not self._seed_host:
                self._seed_host = urlparse(url).netloc
            self._enqueue(url, depth=0)

    async def record_links(self, page_url: str, html: str) -> int:
        """Parse *html* fetched from *page_url* and queue the in-scope links.

        Returns the number of newly enqueued URLs. The depth of *page_url* is
        remembered when it was popped by :meth:`next_url`, so callers do not
        have to thread it back in. Links discovered on a page at ``max_depth``
        are not enqueued, which is what bounds the crawl.
        """
        page_depth = self._popped_depth.get(page_url, 0)
        if not self.config.expands or page_depth + 1 > self.config.max_depth:
            return 0

        added = 0
        for link in extract_links(html, page_url):
            self.stats.links_seen += 1

            if not self.config.in_scope(link, self._seed_host):
                self.stats.out_of_scope += 1
                continue
            if link in self._enqueued:
                self.stats.already_seen += 1
                continue
            if await self.seen.seen(link):
                self.stats.already_seen += 1
                continue

            self._enqueue(link, depth=page_depth + 1)
            added += 1
        return added

    async def next_url(self) -> CrawlPage | None:
        """Pop the shallowest queued URL, or ``None`` when exhausted/capped."""
        while self._queue:
            if self.stats.pages_fetched >= self.config.max_pages:
                return None
            entry = heapq.heappop(self._queue)
            self.stats.pages_fetched += 1
            self._popped_depth[entry.page.url] = entry.depth
            return entry.page
        return None

    async def drain(self) -> list[CrawlPage]:
        """Pop every remaining page (respecting ``max_pages``)."""
        pages: list[CrawlPage] = []
        while (page := await self.next_url()) is not None:
            pages.append(page)
        return pages

    def stats_dict(self) -> dict[str, int]:
        """Return the frontier's counters as a plain dict."""
        return self.stats.as_dict()

    def _enqueue(self, url: str, *, depth: int) -> None:
        self._sequence += 1
        self._enqueued.add(url)
        self.stats.links_enqueued += 1
        heapq.heappush(self._queue, _Queued(depth=depth, sequence=self._sequence, page=CrawlPage(url, depth)))
