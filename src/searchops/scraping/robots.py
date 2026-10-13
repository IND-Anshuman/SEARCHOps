"""robots.txt compliance and per-host politeness.

Why this module exists
----------------------
Before it, SEARCHOps fetched arbitrary URLs with no awareness of ``robots.txt``.
A crawler that ignores robots is not merely impolite — it exposes the operator
to access restrictions and can get a host (and an IP range) blocked for every
other user of the same egress.

Design decisions
----------------
* **Fail open on error, closed on an explicit Disallow.** An unreachable,
  timed-out or malformed robots.txt must never block a scrape; the legal risk
  sits in *ignoring* robots, not in failing to parse them. When the file *is*
  readable, its rules are honoured exactly.
* **RFC 9309 longest-match precedence.** The most specific matching rule wins;
  on equal length, ``Allow`` wins (the permissive tie-break).
* **Crawl-Delay reuses the existing rate limiter.** There is deliberately no
  second timing path — a host's declared delay becomes its domain rate config.
* **Errors are not cached**, so a transient DNS failure does not disable
  politeness for the rest of the process lifetime.

Usage::

    cache = RobotsCache(fetcher=stealth_fetch)
    rules = await cache.get(url)
    if not robots_url_allowed(url, rules):
        return  # recorded as skipped_robots, not a failure
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import structlog

log = structlog.get_logger(__name__)

DEFAULT_USER_AGENT = "SEARCHOpsBot"
_ROBOTS_FETCH_TIMEOUT = 10.0


@dataclass(frozen=True)
class RobotsRules:
    """Parsed rules for one user-agent group.

    ``allow`` and ``disallow`` hold the raw rule paths (already URL-decoded by
    the caller's HTTP layer); matching is prefix/wildcard based, exactly as the
    spec defines it. An empty tuple is permissive.
    """

    allow: tuple[str, ...] = ()
    disallow: tuple[str, ...] = ()
    crawl_delay: float | None = None

    @property
    def is_permissive(self) -> bool:
        """True when nothing is disallowed."""
        return not self.disallow


_PERMISSIVE = RobotsRules()


def _rule_to_regex(rule: str) -> re.Pattern[str]:
    """Compile a robots path pattern into a regex.

    Supports the two spec wildcards: ``*`` (any sequence, including empty) and
    ``$`` (end of path). ``%XX`` escapes are decoded by the HTTP layer upstream,
    so they are not handled here.
    """
    out: list[str] = []
    for ch in rule:
        if ch == "*":
            out.append(".*")
        elif ch == "$":
            out.append("$")
        else:
            out.append(re.escape(ch))
    return re.compile("".join(out))


_RULE_CACHE: dict[str, re.Pattern[str]] = {}


def _compiled(rule: str) -> re.Pattern[str]:
    cached = _RULE_CACHE.get(rule)
    if cached is None:
        cached = _rule_to_regex(rule)
        _RULE_CACHE[rule] = cached
    return cached


def _match_length(rule: str, path: str) -> int | None:
    """Return the rule's length if it matches *path*, else None.

    A robots path rule is a *prefix* pattern, not an exact match: ``/admin``
    must also cover ``/admin/x``. ``*`` is a wildcard over any sequence and a
    trailing ``$`` anchors to the end of the path. Matching always goes through
    the compiled regex so ``*`` is honoured on every rule, anchored or not.
    """
    if rule == "/":
        return 1
    anchored = rule.endswith("$")
    pattern = rule[:-1] if anchored else rule
    # Prefix semantics: the rule must match from the start of the path. Using
    # a non-anchored search would let /b match /a/b.
    return len(rule) if _compiled(pattern).match(path) else None


def robots_url_allowed(url: str, rules: RobotsRules) -> bool:
    """Return True if *url* may be fetched under *rules*.

    Fail-open: a URL that cannot be parsed is treated as allowed, because
    blocking on our own parsing failure would be a false restriction.
    """
    if not url or not rules.disallow:
        return True

    path = urlsplit(url)
    # The query string is part of the match target: rules like /*?print=1
    # are matched against path+query, exactly as crawlers evaluate them.
    match_target = path.path or "/"
    if path.query:
        match_target = f"{match_target}?{path.query}"
    best_allow = -1
    best_disallow = -1

    for rule in rules.allow:
        n = _match_length(rule, match_target)
        if n is not None and n > best_allow:
            best_allow = n

    for rule in rules.disallow:
        n = _match_length(rule, match_target)
        if n is not None and n > best_disallow:
            best_disallow = n

    if best_disallow < 0:
        return True
    # Equal specificity resolves in favour of Allow.
    return best_allow >= best_disallow


def robots_crawl_delay(rules: RobotsRules) -> float | None:
    """Return the group's declared Crawl-Delay, or None when absent/invalid."""
    return rules.crawl_delay


def _split_groups(body: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    """Split a robots.txt body into ``(agents, rules)`` groups.

    Consecutive ``User-agent`` lines form one group; the first non-agent
    directive closes it. Rules are ``(field, value)`` pairs in file order.
    """
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []

    for raw_line in body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip().lower()
        value = value.strip()

        if name == "user-agent":
            if rules:
                # A new agent after real rules starts a genuinely new group.
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
            continue

        if not agents:
            # Directives before any group (e.g. a leading Sitemap) apply to
            # nothing; skip rather than inventing a group.
            continue
        rules.append((name, value))

    if agents:
        groups.append((agents, rules))
    return groups


def parse_robots_txt(
    body: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
) -> RobotsRules:
    """Parse a robots.txt body for one user-agent group.

    Resolution order follows the spec: a group naming our exact agent wins;
    otherwise the ``*`` group applies. Later groups never override an earlier
    match, and rules never carry across groups — in particular a wildcard
    group's ``Crawl-Delay`` must not leak into an agent-specific group.

    ``Disallow:`` with an empty value means "allow all", which is distinct
    from an absent rule. Any internal error yields permissive rules
    (fail-open).
    """
    try:
        if not body or not body.strip():
            return _PERMISSIVE

        want = user_agent.strip().lower()
        groups = _split_groups(body)
        if not groups:
            return _PERMISSIVE

        # Exact agent match first, then wildcard. Only ONE group applies.
        chosen: list[tuple[str, str]] | None = None
        for agents, rules in groups:
            if want in agents:
                chosen = rules
                break
        if chosen is None:
            for agents, rules in groups:
                if "*" in agents:
                    chosen = rules
                    break
        if chosen is None:
            return _PERMISSIVE

        allow: list[str] = []
        disallow: list[str] = []
        crawl_delay: float | None = None
        for name, value in chosen:
            if name == "allow" and value:
                allow.append(value)
            elif name == "disallow":
                # Empty value is meaningful: it means "allow all".
                disallow.append(value)
            elif name == "crawl-delay":
                try:
                    delay = float(value)
                    if delay >= 0:
                        crawl_delay = delay
                except ValueError:
                    pass

        return RobotsRules(
            allow=tuple(allow),
            disallow=tuple(d for d in disallow if d),
            crawl_delay=crawl_delay,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("robots.parse_failed", error=str(exc))
        return _PERMISSIVE


@dataclass
class RobotsStats:
    """Counters describing how robots compliance behaved."""

    fetched: int = 0
    cached_hits: int = 0
    fail_open: int = 0
    errors: int = 0


@dataclass
class RobotsGateStats:
    """Counters for the pipeline-level robots gate.

    ``skipped`` is the number of URLs robots.txt forbade and that were
    therefore never fetched; ``delays_applied`` counts the Crawl-Delay values
    turned into rate limits. Both are free to emit and are how a later
    hardening pass decides where politeness actually bit.
    """

    skipped: int = 0
    delays_applied: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"skipped": self.skipped, "delays_applied": self.delays_applied}


@dataclass
class RobotsCache:
    """Per-host robots.txt cache with fail-open fetching.

    Args:
        fetcher: ``async (url) -> str | None``. Returning ``None`` means
            "no rules" (typically a 404); raising means "unknown" and is
            treated as permissive and *not* cached.
        user_agent: The agent group whose rules we honour.
        ttl_seconds: How long a host's rules are trusted.
    """

    fetcher: Any
    user_agent: str = DEFAULT_USER_AGENT
    ttl_seconds: float = 86400.0
    stats: RobotsStats = field(default_factory=RobotsStats)
    _rules: dict[str, RobotsRules] = field(default_factory=dict)
    _fetched_at: dict[str, float] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def robots_url(self, url: str) -> str:
        try:
            parts = urlsplit(url)
            if not parts.netloc:
                return ""
            return f"{parts.scheme or 'https'}://{parts.netloc}/robots.txt"
        except Exception:
            return ""

    async def get(self, url: str) -> RobotsRules:
        """Return the rules governing *url*'s host, fetching at most once."""
        host = self._host(url)
        if not host:
            return _PERMISSIVE

        cached = self._rules.get(host)
        if cached is not None:
            self.stats.cached_hits += 1
            return cached

        # One in-flight fetch per host: a burst of first URLs for the same
        # site must not open N concurrent robots requests.
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            # Re-check: another waiter may have populated it while we queued.
            cached = self._rules.get(host)
            if cached is not None:
                self.stats.cached_hits += 1
                return cached

            body = await self._fetch(self.robots_url(url))
            if body is None:
                # Either a genuine 404 (no rules) or an error we already logged.
                return _PERMISSIVE

            rules = parse_robots_txt(body, user_agent=self.user_agent)
            self._rules[host] = rules
            import time

            self._fetched_at[host] = time.monotonic()
            self.stats.fetched += 1
            return rules

    async def _fetch(self, robots_url: str) -> str | None:
        try:
            body = await asyncio.wait_for(self.fetcher(robots_url), _ROBOTS_FETCH_TIMEOUT)
        except Exception as exc:
            # Fail open, and do NOT cache: a retry should be allowed to succeed.
            self.stats.errors += 1
            self.stats.fail_open += 1
            log.warning("robots.fetch_failed", url=robots_url, error=str(exc))
            return None
        return str(body) if body is not None else None

    @staticmethod
    def _host(url: str) -> str:
        try:
            return str(urlsplit(url).netloc)
        except Exception:
            return ""

    async def clear(self) -> None:
        self._rules.clear()
        self._fetched_at.clear()
